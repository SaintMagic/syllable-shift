from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Any

from providers import (
    build_client,
    chat_completion_kwargs,
    provider_from_config,
    response_to_stream_chunks,
)
from workflow_events import ENHANCER_APPEND, ENHANCER_DONE, LOG, PREVIEW, STATUS

try:
    from openai import OpenAI
except Exception:
    OpenAI = None  # type: ignore[assignment]


APP_DIR = Path(__file__).resolve().parent


def resolve_path(value: str, default_name: str) -> Path:
    path = Path(str(value).strip() or default_name)
    return path if path.is_absolute() else APP_DIR / path


class LLMRunner:
    def __init__(self, config: Any, ui_queue: queue.Queue[tuple[str, Any]], stop_event: threading.Event):
        self.config = config
        self.ui_queue = ui_queue
        self.stop_event = stop_event
        self.provider = provider_from_config(config)

    def log(self, text: str) -> None:
        self.ui_queue.put((LOG, text))

    def preview(self, text: str) -> None:
        self.ui_queue.put((PREVIEW, text))

    def status(self, text: str) -> None:
        self.ui_queue.put((STATUS, text))

    def client(self) -> OpenAI:
        return build_client(OpenAI, self.provider, self.config.timeout_seconds)

    def create_stream_with_retries(
        self,
        client: OpenAI,
        messages: list[dict[str, str]],
        temperature: float,
        top_p: float,
        max_tokens: int,
        model: str | None = None,
    ) -> Any:
        for attempt in range(1, self.config.max_retries + 1):
            if self.stop_event.is_set():
                raise KeyboardInterrupt
            try:
                kwargs = chat_completion_kwargs(
                    self.config,
                    self.provider,
                    messages,
                    temperature,
                    top_p,
                    max_tokens,
                    model=model or self.config.model,
                    stream=True,
                )
                response = client.chat.completions.create(**kwargs)
                if kwargs.get("stream", True):
                    return response
                return response_to_stream_chunks(response)
            except Exception as exc:
                msg = str(exc)
                rate_limited = "429" in msg or "rate" in msg.lower() or "rate-limited" in msg.lower()
                if not rate_limited:
                    raise

                wait = min(30 * attempt, 180)
                self.log(f"Rate limited on attempt {attempt}/{self.config.max_retries}. Waiting {wait}s...")
                for _ in range(wait):
                    if self.stop_event.is_set():
                        raise KeyboardInterrupt
                    time.sleep(1)
        raise RuntimeError("Too many rate-limit retries.")


class StoryGenerator(LLMRunner):
    def stream_call(self, client: OpenAI, messages: list[dict[str, str]], output_file: Path, append: bool) -> tuple[str, str | None]:
        mode = "a" if append else "w"
        text_parts: list[str] = []
        output_chars = 0
        last_progress = 0
        start_time = time.time()
        finish_reason = None

        output_file.parent.mkdir(parents=True, exist_ok=True)
        with output_file.open(mode, encoding="utf-8", newline="\n") as out:
            stream = self.create_stream_with_retries(
                client,
                messages,
                self.config.temperature,
                self.config.top_p,
                self.config.max_tokens_per_call,
            )
            for chunk in stream:
                if self.stop_event.is_set():
                    raise KeyboardInterrupt
                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                delta = choice.delta.content or ""
                if not delta:
                    continue

                text_parts.append(delta)
                out.write(delta)
                out.flush()
                self.preview(delta)

                output_chars += len(delta)
                if output_chars - last_progress >= 5000:
                    last_progress = output_chars
                    elapsed = time.time() - start_time
                    self.log(f"Written this call: {output_chars:,} chars ({elapsed / 60:.1f} min)")

        return "".join(text_parts), finish_reason

    def run(self) -> None:
        output_file = resolve_path(self.config.output_file, "deepseek_original_novella.md")
        client = self.client()

        self.status("Generating story...")
        self.log(f"Model: {self.config.model}")
        self.log(f"Output file: {output_file}")
        self.log(f"Safe routing: {self.config.safe_routing}")

        story_prompt = (
            self.config.story_prompt
            + "\n\nLENGTH OVERRIDE\n"
            + (
                f"Target final story length: {self.config.story_target_min_words:,} "
                f"to {self.config.story_target_max_words:,} words.\n"
            )
            + "Do not stop early unless you hit a clean continuation boundary."
        )

        messages = [
            {"role": "system", "content": self.config.system_prompt},
            {"role": "user", "content": story_prompt},
        ]

        total_start = time.time()
        append = False
        for call_number in range(1, self.config.max_continuations + 2):
            if self.stop_event.is_set():
                raise KeyboardInterrupt

            self.log(f"Call {call_number}/{self.config.max_continuations + 1}")
            generated, finish_reason = self.stream_call(client, messages, output_file, append=append)
            self.log(f"Call {call_number} finish reason: {finish_reason}")

            full_output = output_file.read_text(encoding="utf-8") if output_file.exists() else ""
            self.log(f"Current output words: {len(full_output.split()):,}")

            needs_continue = self.config.continue_marker in generated or finish_reason == "length"
            if not needs_continue:
                break

            output_file.write_text(
                full_output.replace(self.config.continue_marker, "").rstrip() + "\n",
                encoding="utf-8",
                newline="\n",
            )
            full_output = output_file.read_text(encoding="utf-8")
            if call_number > self.config.max_continuations:
                self.log("Reached maximum continuations. Partial story saved.")
                break

            messages = [
                {"role": "system", "content": self.config.continuation_system_prompt},
                {
                    "role": "user",
                    "content": (
                        "Continue the story from the exact point where it stopped.\n"
                        "Do not restart. Do not summarize. Do not explain.\n"
                        "Preserve the same second-person present-tense style, tone, continuity, headings, and pacing.\n"
                        "Continue from the last sentence of the story below.\n"
                        "If you still cannot finish, stop at a clean section boundary and write exactly:\n"
                        f"{self.config.continue_marker}\n\n"
                        "STORY_SO_FAR_START\n"
                        f"{full_output}\n"
                        "STORY_SO_FAR_END"
                    ),
                },
            ]
            append = True
            with output_file.open("a", encoding="utf-8", newline="\n") as out:
                out.write("\n\n")

        final_text = output_file.read_text(encoding="utf-8")
        elapsed = time.time() - total_start
        self.log("Done.")
        self.log(f"Saved to: {output_file}")
        self.log(f"Output words: {len(final_text.split()):,}")
        self.log(f"Output chars: {len(final_text):,}")
        self.log(f"Total time: {elapsed / 60:.1f} minutes")
        self.status("Done")


class PromptEnhancer(LLMRunner):
    MODE_INSTRUCTIONS = {
        "Enhance Story Prompt": (
            "You are improving a prompt for an AI long-form story generation tool.\n\n"
            "Do not write the story.\n"
            "Do not remove important constraints.\n"
            "Do not add new story content unless clearly requested by the prompt.\n"
            "Make the prompt clearer, more consistent, and easier for an LLM to follow.\n"
            "Remove contradictions, repetition, and vague wording.\n"
            "Preserve the user's intent.\n\n"
            "Return only the improved prompt."
        ),
        "Enhance Rewrite Prompt": (
            "You are improving a prompt for chunked prose rewriting.\n\n"
            "Preserve these priorities:\n"
            "1. Do not summarize.\n"
            "2. Preserve plot events, dialogue beats, continuity, and technical details.\n"
            "3. Keep output near the target word ratio.\n"
            "4. Improve prose without deleting story beats.\n"
            "5. Remove only non-story artifacts.\n\n"
            "Return only the improved rewrite prompt."
        ),
        "Make Prompt Shorter": (
            "You are making a prompt shorter while preserving its operational constraints.\n\n"
            "Remove repetition, filler, and redundant wording.\n"
            "Do not remove safety, length, format, continuity, or style requirements.\n"
            "Do not write the story or rewrite the source text.\n\n"
            "Return only the shorter prompt."
        ),
        "Make Prompt Stricter": (
            "You are making a writing-tool prompt stricter and less ambiguous.\n\n"
            "Add clear hard rules where the prompt is vague.\n"
            "Clarify length, preservation, formatting, and failure conditions.\n"
            "Do not add story content.\n"
            "Do not contradict the original intent.\n\n"
            "Return only the stricter prompt."
        ),
        "Extract Variables": (
            "You are extracting editable variables from a writing-tool prompt.\n\n"
            "Identify values like genre, POV, tense, tone, target words, section count, style rules, "
            "forbidden elements, model behavior flags, ratio targets, and output format.\n"
            "Do not rewrite the prompt.\n\n"
            "Return only a concise Markdown list of variables and current values."
        ),
    }

    def run(self, source_prompt: str, mode: str) -> None:
        if not source_prompt.strip():
            raise ValueError("Prompt enhancer source is empty.")

        client = self.client()
        instruction = self.MODE_INSTRUCTIONS.get(mode, self.MODE_INSTRUCTIONS["Enhance Story Prompt"])
        self.status("Enhancing prompt...")
        self.log(f"Prompt enhancer mode: {mode}")
        self.log(f"Enhancer model: {self.config.enhancer_model}")
        self.log(f"Input prompt words: {len(source_prompt.split()):,}")

        messages = [
            {"role": "system", "content": instruction},
            {
                "role": "user",
                "content": (
                    "PROMPT_TO_IMPROVE_START\n"
                    f"{source_prompt}\n"
                    "PROMPT_TO_IMPROVE_END"
                ),
            },
        ]
        stream = self.create_stream_with_retries(
            client,
            messages,
            self.config.enhancer_temperature,
            0.9,
            12000,
            model=self.config.enhancer_model or self.config.model,
        )

        parts: list[str] = []
        for chunk in stream:
            if self.stop_event.is_set():
                raise KeyboardInterrupt
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content or ""
            if not delta:
                continue
            parts.append(delta)
            self.ui_queue.put((ENHANCER_APPEND, delta))

        enhanced = "".join(parts).strip()
        self.ui_queue.put((ENHANCER_DONE, enhanced))
        self.log(f"Enhanced prompt words: {len(enhanced.split()):,}")
        self.status("Prompt enhanced")
