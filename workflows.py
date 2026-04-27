from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from legacy_rewrite_adapter import preclean_text, split_into_word_chunks
from providers import (
    build_client,
    chat_completion_kwargs,
    provider_from_config,
    response_to_stream_chunks,
)
from segmentation import Segment, SegmentParser
from translation_profiles import (
    TranslationProfile,
    load_glossary,
    load_line_list,
    load_translation_profile,
)
from translation_validator import ValidationReport, TranslationValidator
from workflow_events import (
    CHUNK,
    ENHANCER_APPEND,
    ENHANCER_DONE,
    LOG,
    PREVIEW,
    SEGMENT,
    STATUS,
    TRANSLATION_OUTPUT,
    TRANSLATION_PREVIEW,
    TRANSLATION_SOURCE,
    VALIDATION_REPORT,
)

try:
    from openai import OpenAI
except Exception:
    OpenAI = None  # type: ignore[assignment]


APP_DIR = Path(__file__).resolve().parent


def resolve_path(value: str, default_name: str) -> Path:
    path = Path(str(value).strip() or default_name)
    return path if path.is_absolute() else APP_DIR / path


def resolve_optional_path(value: str) -> Path | None:
    clean = str(value).strip()
    if not clean:
        return None
    path = Path(clean)
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


class TranslationRunner(LLMRunner):
    def __init__(self, config: Any, ui_queue: queue.Queue[tuple[str, Any]], stop_event: threading.Event):
        super().__init__(config, ui_queue, stop_event)
        self.translation = self.to_translation_config(config)

    def to_translation_config(self, config: Any) -> Any:
        return SimpleNamespace(
            input_file=resolve_path(config.translation_input_file, "translation_source.txt"),
            output_file=resolve_path(config.translation_output_file, "translation_output.md"),
            segments_dir=resolve_path(config.translation_segments_dir, "translation_segments"),
            source_language=config.translation_source_language,
            target_language=config.translation_target_language,
            register_mode=config.translation_register_mode,
            instruction_file=resolve_optional_path(config.translation_instruction_file),
            glossary_file=resolve_optional_path(config.translation_glossary_file),
            dnt_file=resolve_optional_path(config.translation_dnt_file),
            protected_regex_file=resolve_optional_path(config.translation_protected_regex_file),
            segment_delimiter_style=config.translation_segment_delimiter_style,
            custom_delimiter_regex=config.translation_custom_delimiter_regex,
            chunk_segments=config.translation_chunk_segments,
            max_tokens_per_call=config.translation_max_tokens_per_call,
            temperature=config.translation_temperature,
            top_p=config.translation_top_p,
            pause_seconds=config.translation_pause_seconds,
            validate_after_run=config.translation_validate_after_run,
            validator_profile=config.translation_validator_profile,
            validation_report_file=resolve_path(config.translation_validation_report_file, "translation_validation_report.md"),
            grouped_report=config.translation_grouped_report,
            save_json_report=config.translation_save_json_report,
        )

    def load_profile(self) -> tuple[TranslationProfile, list[str], list[str]]:
        translation = self.translation
        profile = load_translation_profile(None, translation.validator_profile)
        if self.config.translation_instruction_text.strip():
            profile.task_instruction = self.config.translation_instruction_text.strip()
        elif translation.instruction_file and translation.instruction_file.exists():
            if translation.instruction_file.suffix.lower() == ".json":
                profile = load_translation_profile(translation.instruction_file, "")
            else:
                profile.task_instruction = translation.instruction_file.read_text(encoding="utf-8", errors="replace")

        profile.source_language = translation.source_language or profile.source_language
        profile.target_language = translation.target_language or profile.target_language
        profile.default_register_mode = translation.register_mode or profile.default_register_mode
        profile.delimiter_style = translation.segment_delimiter_style or profile.delimiter_style
        profile.delimiter_regex = translation.custom_delimiter_regex or profile.delimiter_regex

        dnt_terms = list(profile.dnt_terms)
        protected_regexes = list(profile.protected_token_regexes)
        if translation.dnt_file:
            dnt_terms.extend(load_line_list(translation.dnt_file))
        if translation.protected_regex_file:
            protected_regexes.extend(load_line_list(translation.protected_regex_file))
        if translation.glossary_file:
            profile.glossary_terms.extend(load_glossary(translation.glossary_file))

        for term in profile.glossary_terms:
            if not term.target_term or term.target_term.strip().upper() == "[TARGET TERM]":
                dnt_terms.append(term.source_term)

        return profile, list(dict.fromkeys(dnt_terms)), list(dict.fromkeys(protected_regexes))

    def parser_for_profile(self, profile: TranslationProfile) -> SegmentParser:
        return SegmentParser(profile.delimiter_style, profile.delimiter_regex)

    def read_segments(self, require_target: bool = True) -> tuple[list[Segment], TranslationProfile, list[str], list[str], SegmentParser]:
        translation = self.translation
        if not translation.input_file.exists():
            raise FileNotFoundError(f"Translation input file not found: {translation.input_file}")
        if require_target and not translation.target_language.strip():
            raise ValueError("Target language is required for translation.")

        profile, dnt_terms, protected_regexes = self.load_profile()
        parser = self.parser_for_profile(profile)
        source_text = translation.input_file.read_text(encoding="utf-8", errors="replace")
        segments = parser.parse(source_text)
        if not segments:
            raise ValueError(
                "No source segments were parsed. Check the segment delimiter style/profile settings."
            )
        return segments, profile, dnt_terms, protected_regexes, parser

    def write_manifest(self, segments: list[Segment], profile: TranslationProfile) -> None:
        translation = self.translation
        manifest = {
            "input_file": str(translation.input_file.resolve()),
            "input_sha256": hashlib.sha256(translation.input_file.read_bytes()).hexdigest(),
            "output_file": str(translation.output_file.resolve()),
            "segment_delimiter_style": translation.segment_delimiter_style,
            "custom_delimiter_regex": translation.custom_delimiter_regex,
            "chunk_segments": translation.chunk_segments,
            "segment_count": len(segments),
            "source_language": translation.source_language,
            "target_language": translation.target_language,
            "register_mode": translation.register_mode,
            "profile": profile.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        translation.segments_dir.mkdir(parents=True, exist_ok=True)
        (translation.segments_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def prepare_segments(self, clear_outputs: bool = True) -> tuple[list[Segment], TranslationProfile, list[str], list[str], SegmentParser]:
        segments, profile, dnt_terms, protected_regexes, parser = self.read_segments(require_target=clear_outputs)
        translation = self.translation
        translation.segments_dir.mkdir(parents=True, exist_ok=True)
        if clear_outputs:
            for pattern in ("source_segment_*.txt", "translated_chunk_*.txt"):
                for old_file in translation.segments_dir.glob(pattern):
                    old_file.unlink(missing_ok=True)
            translation.output_file.unlink(missing_ok=True)
        self.write_manifest(segments, profile)
        for segment in segments:
            (translation.segments_dir / f"source_segment_{segment.id}.txt").write_text(
                parser.render_segment(segment, segment.body),
                encoding="utf-8",
                newline="\n",
            )
            self.ui_queue.put((
                SEGMENT,
                {
                    "id": segment.id,
                    "input": segment.word_count,
                    "output": "",
                    "ratio": "",
                    "finish": "",
                    "status": "Queued",
                    "validation": "",
                    "issues": "",
                },
            ))
        self.log(f"Translation input: {translation.input_file}")
        self.log(f"Segments parsed: {len(segments)}")
        self.log(f"Target language: {translation.target_language}")
        self.log(f"Register mode: {translation.register_mode}")
        self.log(f"Profile: {profile.name}")
        return segments, profile, dnt_terms, protected_regexes, parser

    def build_translation_messages(
        self,
        profile: TranslationProfile,
        parser: SegmentParser,
        segment_chunk: list[Segment],
        chunk_index: int,
        total_chunks: int,
        dnt_terms: list[str],
    ) -> list[dict[str, str]]:
        translation = self.translation
        instruction = profile.instruction_text(
            translation.source_language,
            translation.target_language,
            translation.register_mode,
        )
        glossary_lines = []
        for term in profile.glossary_terms:
            target = term.target_term or "[preserve source term]"
            context = f" | context: {term.context}" if term.context else ""
            note = f" | note: {term.note}" if term.note else ""
            glossary_lines.append(f"- {term.source_term} => {target}{context}{note}")
        dnt_lines = [f"- {term}" for term in dnt_terms]
        source_payload = "\n".join(parser.render_segment(segment, segment.body).rstrip() for segment in segment_chunk)

        user_prompt = (
            f"Translate segment chunk {chunk_index} of {total_chunks}.\n"
            f"Source language: {translation.source_language}\n"
            f"Target language: {translation.target_language}\n"
            f"Register mode: {translation.register_mode}\n\n"
            "Do not translate delimiter lines. Preserve segment IDs and order exactly.\n"
            "Return only the translated segment block(s), with the same delimiters.\n\n"
            "DO_NOT_TRANSLATE_TERMS\n"
            + ("\n".join(dnt_lines) if dnt_lines else "- None")
            + "\n\nGLOSSARY\n"
            + ("\n".join(glossary_lines) if glossary_lines else "- None")
            + "\n\nSOURCE_SEGMENTS_START\n"
            + source_payload
            + "\nSOURCE_SEGMENTS_END"
        )
        return [
            {"role": "system", "content": instruction},
            {"role": "user", "content": user_prompt},
        ]

    def translate_chunk(
        self,
        client: OpenAI,
        profile: TranslationProfile,
        parser: SegmentParser,
        segment_chunk: list[Segment],
        chunk_index: int,
        total_chunks: int,
        dnt_terms: list[str],
    ) -> tuple[str, str | None]:
        translation = self.translation
        chunk_file = translation.segments_dir / f"translated_chunk_{chunk_index:03}.txt"
        messages = self.build_translation_messages(profile, parser, segment_chunk, chunk_index, total_chunks, dnt_terms)
        stream = self.create_stream_with_retries(
            client,
            messages,
            translation.temperature,
            translation.top_p,
            translation.max_tokens_per_call,
        )

        finish_reason = None
        parts: list[str] = []
        chunk_file.write_text("", encoding="utf-8")
        with chunk_file.open("w", encoding="utf-8", newline="\n") as out:
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
                parts.append(delta)
                out.write(delta)
                out.flush()
                self.preview(delta)
                self.ui_queue.put((TRANSLATION_PREVIEW, delta))

        translated = "".join(parts).strip()
        chunk_file.write_text(translated + "\n", encoding="utf-8", newline="\n")
        for segment in segment_chunk:
            self.ui_queue.put((
                SEGMENT,
                {
                    "id": segment.id,
                    "output": len(translated.split()),
                    "finish": finish_reason or "",
                    "status": "Done",
                },
            ))
        return translated, finish_reason

    def rebuild_output(self, total_chunks: int) -> None:
        translation = self.translation
        parts: list[str] = []
        for index in range(1, total_chunks + 1):
            chunk_file = translation.segments_dir / f"translated_chunk_{index:03}.txt"
            if chunk_file.exists():
                parts.append(chunk_file.read_text(encoding="utf-8", errors="replace").strip())
        translation.output_file.parent.mkdir(parents=True, exist_ok=True)
        translation.output_file.write_text("\n\n".join(part for part in parts if part) + "\n", encoding="utf-8", newline="\n")

    def validate_current_output(self) -> ValidationReport:
        translation = self.translation
        profile, dnt_terms, protected_regexes = self.load_profile()
        parser = self.parser_for_profile(profile)
        validator = TranslationValidator(
            profile,
            parser,
            dnt_terms=dnt_terms,
            protected_regexes=protected_regexes,
            grouped_report=translation.grouped_report,
        )
        report = validator.validate_files(translation.input_file, translation.output_file)
        validator.save_report(
            report,
            translation.validation_report_file,
            grouped=translation.grouped_report,
            save_json=translation.save_json_report,
        )
        self.ui_queue.put((VALIDATION_REPORT, report.format(grouped=translation.grouped_report)))
        self.log(f"Validation report saved to: {translation.validation_report_file}")
        self.log(f"Validation status: {'PASS' if report.passed else 'FAIL'}")
        self.log(f"Errors: {report.error_count}; warnings: {report.warning_count}")
        by_segment: dict[str, dict[str, int]] = {}
        for issue in report.issues:
            sid = issue.segment_id or "GLOBAL"
            bucket = by_segment.setdefault(sid, {"error": 0, "warning": 0})
            if issue.severity in {"critical", "error"}:
                bucket["error"] += 1
            elif issue.severity == "warning":
                bucket["warning"] += 1
        for sid, counts in by_segment.items():
            self.ui_queue.put((
                SEGMENT,
                {
                    "id": sid,
                    "validation": "FAIL" if counts["error"] else "WARN",
                    "issues": f"E{counts['error']} W{counts['warning']}",
                },
            ))
        return report

    def preview_segments(self) -> None:
        segments, profile, _, _, parser = self.prepare_segments(clear_outputs=False)
        sample = "\n".join(parser.render_segment(segment, segment.body).rstrip() for segment in segments[:5])
        self.ui_queue.put((TRANSLATION_SOURCE, sample))
        self.log(f"Previewed {len(segments)} translation segments using profile: {profile.name}")
        self.status("Translation segments previewed")

    def run(self) -> None:
        client = self.client()
        segments, profile, dnt_terms, _, parser = self.prepare_segments(clear_outputs=True)
        chunks = parser.chunk_segments(segments, self.translation.chunk_segments)
        self.status("Translating segments...")
        self.log(f"Model: {self.config.model}")
        self.log(f"Segment chunks: {len(chunks)}")
        total_start = time.time()
        last_finish = None

        for index, segment_chunk in enumerate(chunks, start=1):
            if self.stop_event.is_set():
                raise KeyboardInterrupt
            ids = ", ".join(segment.id for segment in segment_chunk)
            input_words = sum(segment.word_count for segment in segment_chunk)
            self.log(f"Translation chunk {index}/{len(chunks)} - segments: {ids}; input words: {input_words:,}")
            for segment in segment_chunk:
                self.ui_queue.put((SEGMENT, {"id": segment.id, "status": "Running"}))
            translated, last_finish = self.translate_chunk(client, profile, parser, segment_chunk, index, len(chunks), dnt_terms)
            self.rebuild_output(len(chunks))
            self.log(f"Chunk {index} done - output words: {len(translated.split()):,}, finish: {last_finish}")

            if index != len(chunks) and self.translation.pause_seconds > 0:
                for _ in range(self.translation.pause_seconds):
                    if self.stop_event.is_set():
                        raise KeyboardInterrupt
                    time.sleep(1)

        output_text = self.translation.output_file.read_text(encoding="utf-8", errors="replace")
        self.ui_queue.put((TRANSLATION_OUTPUT, output_text))
        self.log(f"Translation saved to: {self.translation.output_file}")
        self.log(f"Per-segment files: {self.translation.segments_dir}")
        self.log(f"Output words: {len(output_text.split()):,}")
        self.log(f"Total time: {(time.time() - total_start) / 60:.1f} minutes")
        self.log(f"Last finish reason: {last_finish}")

        if self.translation.validate_after_run:
            self.validate_current_output()
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
