from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from legacy_rewrite_adapter import preclean_text, split_into_word_chunks
from providers import (
    build_client,
    chat_completion_kwargs,
    provider_from_config,
    response_to_stream_chunks,
)
from workflow_events import CHUNK, ENHANCER_APPEND, ENHANCER_DONE, LOG, PREVIEW, STATUS

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


class ChunkedRewriter(LLMRunner):
    def paths(self) -> tuple[Path, Path, Path, Path]:
        return (
            resolve_path(self.config.rewrite_input_file, "novel.md"),
            resolve_path(self.config.rewrite_output_file, "novel_rewritten.md"),
            resolve_path(self.config.rewrite_cleaned_file, "novel_cleaned_input.md"),
            resolve_path(self.config.rewrite_chunks_dir, "rewrite_chunks"),
        )

    def build_manifest(self, input_file: Path, cleaned_file: Path, cleaned_text: str, chunk_count: int) -> dict[str, Any]:
        return {
            "input_file": str(input_file.resolve()),
            "cleaned_file": str(cleaned_file.resolve()),
            "cleaned_sha256": hashlib.sha256(cleaned_text.encode("utf-8")).hexdigest(),
            "chunk_size": self.config.rewrite_chunk_words,
            "chunk_count": chunk_count,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def validate_retry_manifest(self, chunks_dir: Path, manifest: dict[str, Any]) -> None:
        manifest_file = chunks_dir / "manifest.json"
        if not manifest_file.exists():
            raise RuntimeError(
                "Cannot safely retry a chunk because rewrite_chunks/manifest.json is missing. "
                "Run a full rewrite first."
            )

        previous = json.loads(manifest_file.read_text(encoding="utf-8"))
        mismatches = []
        for key in ("input_file", "cleaned_file", "cleaned_sha256", "chunk_size", "chunk_count"):
            if previous.get(key) != manifest.get(key):
                mismatches.append(key)

        if mismatches:
            raise RuntimeError(
                "Cannot safely retry this chunk because rewrite settings/source changed "
                f"since the chunk files were created. Mismatched: {', '.join(mismatches)}. "
                "Run Start Rewrite to regenerate chunks with the new settings."
            )

    def prepare_chunks(self, clear_outputs: bool) -> list[str]:
        input_file, _, cleaned_file, chunks_dir = self.paths()
        if not input_file.exists():
            raise FileNotFoundError(f"Input file not found: {input_file}")

        raw_text = input_file.read_text(encoding="utf-8")
        cleaned_text = preclean_text(raw_text)

        chunks = split_into_word_chunks(cleaned_text, self.config.rewrite_chunk_words)
        chunks_dir.mkdir(parents=True, exist_ok=True)
        manifest = self.build_manifest(input_file, cleaned_file, cleaned_text, len(chunks))
        if clear_outputs:
            (chunks_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        else:
            self.validate_retry_manifest(chunks_dir, manifest)

        cleaned_file.parent.mkdir(parents=True, exist_ok=True)
        cleaned_file.write_text(cleaned_text, encoding="utf-8", newline="\n")

        if clear_outputs:
            for pattern in ("source_*.md", "rewritten_*.md"):
                for old_file in chunks_dir.glob(pattern):
                    old_file.unlink(missing_ok=True)

        for index, chunk_text in enumerate(chunks, start=1):
            (chunks_dir / f"source_{index:03}.md").write_text(chunk_text, encoding="utf-8", newline="\n")
            self.ui_queue.put((
                CHUNK,
                {
                    "index": index,
                    "input": len(chunk_text.split()),
                    "output": "",
                    "ratio": "",
                    "status": "Queued",
                },
            ))

        self.log(f"Loaded: {input_file}")
        self.log(f"Raw words: {len(raw_text.split()):,}")
        self.log(f"Cleaned words: {len(cleaned_text.split()):,}")
        self.log(f"Cleaned input saved to: {cleaned_file}")
        self.log(f"Chunks: {len(chunks)}")
        return chunks

    def rebuild_output(self, total_chunks: int) -> None:
        _, output_file, _, chunks_dir = self.paths()
        parts: list[str] = []
        for index in range(1, total_chunks + 1):
            chunk_file = chunks_dir / f"rewritten_{index:03}.md"
            if chunk_file.exists():
                parts.append(chunk_file.read_text(encoding="utf-8").strip())
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text("\n\n".join(part for part in parts if part) + "\n", encoding="utf-8", newline="\n")

    def rewrite_chunk(self, client: OpenAI, chunk_text: str, index: int, total_chunks: int) -> tuple[int, float, str | None]:
        input_words = len(chunk_text.split())
        min_words = int(input_words * self.config.rewrite_target_min_ratio)
        max_words = int(input_words * self.config.rewrite_target_max_ratio)
        _, _, _, chunks_dir = self.paths()
        rewritten_file = chunks_dir / f"rewritten_{index:03}.md"

        messages = [
            {"role": "system", "content": self.config.rewrite_system_prompt},
            {
                "role": "user",
                "content": (
                    f"You are rewriting chunk {index} of {total_chunks} from a longer story.\n"
                    f"This chunk has approximately {input_words} words.\n"
                    f"Target rewritten length: {min_words} to {max_words} words.\n"
                    "Output below the minimum target length is considered a failed rewrite.\n"
                    "Continue developing the prose until the rewritten chunk reaches the target length.\n"
                    "Do not choose brevity over preservation.\n"
                    "Rewrite only this chunk.\n"
                    "Do not summarize.\n"
                    "Do not compress.\n"
                    "Do not shorten.\n"
                    "Do not skip events.\n"
                    "Do not add a new ending.\n"
                    "Do not repeat previous chunks.\n"
                    "Preserve every meaningful action, observation, dialogue beat, technical detail, and scene beat.\n"
                    "If the source is repetitive, polish the repetition instead of deleting the underlying beat.\n"
                    "Return only the rewritten prose for this chunk.\n\n"
                    "SOURCE_TEXT_START\n"
                    f"{chunk_text}\n"
                    "SOURCE_TEXT_END"
                ),
            },
        ]

        finish_reason = None
        output_parts: list[str] = []
        rewritten_file.write_text("", encoding="utf-8")
        stream = self.create_stream_with_retries(
            client,
            messages,
            self.config.rewrite_temperature,
            self.config.rewrite_top_p,
            self.config.rewrite_max_tokens_per_call,
        )

        with rewritten_file.open("w", encoding="utf-8", newline="\n") as out:
            for api_chunk in stream:
                if self.stop_event.is_set():
                    raise KeyboardInterrupt
                if not api_chunk.choices:
                    continue

                choice = api_chunk.choices[0]
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                delta = choice.delta.content or ""
                if not delta:
                    continue

                out.write(delta)
                out.flush()
                output_parts.append(delta)
                self.preview(delta)

        output_words = len("".join(output_parts).split())
        ratio = output_words / max(input_words, 1)
        if ratio < self.config.rewrite_min_ratio:
            status = "Compressed"
        elif ratio > self.config.rewrite_max_ratio:
            status = "Expanded"
        else:
            status = "OK"
        self.ui_queue.put((
            CHUNK,
            {
                "index": index,
                "input": input_words,
                "output": output_words,
                "ratio": f"{ratio:.0%}",
                "finish": finish_reason or "",
                "status": status,
            },
        ))
        return output_words, ratio, finish_reason

    def run(self, retry_chunk: int | None = None) -> None:
        client = self.client()
        self.status("Rewriting chunks...")
        self.log(f"Model: {self.config.model}")
        self.log(f"Chunk target: {self.config.rewrite_chunk_words:,} words")
        chunks = self.prepare_chunks(clear_outputs=retry_chunk is None)
        total_start = time.time()

        if retry_chunk is not None:
            if retry_chunk < 1 or retry_chunk > len(chunks):
                raise ValueError(f"Chunk {retry_chunk} is outside 1..{len(chunks)}")
            chunk_numbers = [retry_chunk]
            self.log(f"Retrying chunk {retry_chunk}/{len(chunks)}")
        else:
            chunk_numbers = list(range(1, len(chunks) + 1))

        last_finish = None
        for index in chunk_numbers:
            if self.stop_event.is_set():
                raise KeyboardInterrupt
            chunk_text = chunks[index - 1]
            input_words = len(chunk_text.split())
            self.log(f"Chunk {index}/{len(chunks)} - input words: {input_words:,}")
            self.ui_queue.put((CHUNK, {"index": index, "status": "Running"}))

            output_words, ratio, last_finish = self.rewrite_chunk(client, chunk_text, index, len(chunks))
            self.rebuild_output(len(chunks))
            self.log(
                f"Chunk {index} done - output words: {output_words:,}, "
                f"ratio: {ratio:.0%}, finish: {last_finish}"
            )
            if ratio < self.config.rewrite_min_ratio:
                self.log(f"WARNING: Chunk {index} may be too compressed.")
            if ratio > self.config.rewrite_max_ratio:
                self.log(f"WARNING: Chunk {index} may be too expanded.")

            if index != chunk_numbers[-1] and self.config.rewrite_pause_seconds > 0:
                for _ in range(self.config.rewrite_pause_seconds):
                    if self.stop_event.is_set():
                        raise KeyboardInterrupt
                    time.sleep(1)

        _, output_file, _, chunks_dir = self.paths()
        final_text = output_file.read_text(encoding="utf-8") if output_file.exists() else ""
        elapsed = time.time() - total_start
        self.log("Done.")
        self.log(f"Saved to: {output_file}")
        self.log(f"Per-chunk files: {chunks_dir}")
        self.log(f"Output words: {len(final_text.split()):,}")
        self.log(f"Output chars: {len(final_text):,}")
        self.log(f"Total time: {elapsed / 60:.1f} minutes")
        self.log(f"Last finish reason: {last_finish}")
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
