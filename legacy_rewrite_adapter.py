from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any


APP_DIR = Path(__file__).resolve().parent
REWRITE_SCRIPT = APP_DIR / "rewrite.py"


def load_rewrite_backend() -> Any | None:
    if not REWRITE_SCRIPT.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("rewrite_backend", REWRITE_SCRIPT)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


REWRITE_BACKEND = load_rewrite_backend()
DEFAULT_REWRITE_PROMPT = getattr(
    REWRITE_BACKEND,
    "SYSTEM_PROMPT",
    "Rewrite the provided story chunk into polished prose while preserving plot, tone, and continuity.",
)


def preclean_text(text: str) -> str:
    if REWRITE_BACKEND and hasattr(REWRITE_BACKEND, "preclean_text"):
        return REWRITE_BACKEND.preclean_text(text)

    lines = text.splitlines()
    garbage_patterns = [
        r"^\s*DEBUG INFO\s*$",
        r"^\s*Thought for .* seconds\s*$",
        r"^\s*continue\s*$",
        r"^\s*continuing in next response\s*$",
        r"^\s*\[CONTINUE FROM HERE\]\s*$",
    ]
    cleaned = [
        line for line in lines
        if not any(re.match(pattern, line, flags=re.IGNORECASE) for pattern in garbage_patterns)
    ]
    return re.sub(r"\n{4,}", "\n\n\n", "\n".join(cleaned)).strip() + "\n"


def split_into_word_chunks(text: str, max_words: int) -> list[str]:
    if REWRITE_BACKEND and hasattr(REWRITE_BACKEND, "split_into_word_chunks"):
        return REWRITE_BACKEND.split_into_word_chunks(text, max_words=max_words)

    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_words = 0
    for paragraph in paragraphs:
        words = len(paragraph.split())
        if current and current_words + words > max_words:
            chunks.append("\n\n".join(current))
            current = [paragraph]
            current_words = words
        else:
            current.append(paragraph)
            current_words += words
    if current:
        chunks.append("\n\n".join(current))
    return chunks

