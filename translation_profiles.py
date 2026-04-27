from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


APP_DIR = Path(__file__).resolve().parent
SAMPLE_TRANSLATION_DIR = APP_DIR / "01_test translation" / "translation_stress_test_v9_sanitized_bundle"


@dataclass
class GlossaryTerm:
    source_term: str
    target_term: str = ""
    context: str = ""
    note: str = ""


@dataclass
class TranslationProfile:
    name: str = "Generic Translation"
    task_instruction: str = (
        "Translate the source text from [SOURCE LANGUAGE] into [TARGET LANGUAGE].\n"
        "Use [REGISTER MODE] register.\n"
        "Preserve segment delimiters, placeholders, URLs, paths, IDs, codes, tags, numbers, and DNT terms exactly.\n"
        "Return only translated segments."
    )
    source_language: str = "English"
    target_language: str = ""
    register_modes: list[str] = field(default_factory=lambda: ["Professional/staff-facing", "Patient-facing plain language"])
    default_register_mode: str = "Professional/staff-facing"
    dnt_terms: list[str] = field(default_factory=list)
    glossary_terms: list[GlossaryTerm] = field(default_factory=list)
    placeholder_regexes: list[str] = field(default_factory=lambda: [
        r"\{\{[^{}]+\}\}",
        r"\{[^{}]+\}",
        r"%[A-Za-z0-9_]+%",
        r"\[[A-Za-z0-9_.:-]+\]",
    ])
    protected_token_regexes: list[str] = field(default_factory=lambda: [
        r"https?://[^\s<>\"]+",
        r"(?:[A-Za-z]:\\(?:[^\\/:*?\"<>|\r\n]+\\)*[^\\/:*?\"<>|\r\n]*|\\\\[^\\\s]+\\[^\\\s]+(?:\\[^\\\s]+)*)",
        r"\b[A-Z]{2,}[A-Z0-9_-]*\b",
        r"\b[A-Z]{2,}-\d{2,}-\d{2,}\b",
    ])
    delimiter_style: str = "Percent Segment Blocks"
    delimiter_regex: str = ""
    validation_rules: dict[str, bool] = field(default_factory=lambda: {
        "segments": True,
        "dnt_terms": True,
        "protected_regexes": True,
        "placeholders": True,
        "urls": True,
        "paths": True,
        "ids_codes": True,
        "empty_segments": True,
        "leaked_placeholders": True,
        "translator_notes": True,
    })
    output_format_rules: str = "Output only translated segment blocks. Preserve segment delimiters exactly."

    def instruction_text(self, source_language: str, target_language: str, register_mode: str) -> str:
        text = self.task_instruction
        replacements = {
            "[SOURCE LANGUAGE]": source_language or self.source_language,
            "[TARGET LANGUAGE]": target_language,
            "[REGISTER MODE]": register_mode or self.default_register_mode,
        }
        for placeholder, value in replacements.items():
            text = text.replace(placeholder, value)
        return text.strip()


def builtin_translation_profiles() -> dict[str, TranslationProfile]:
    generic = TranslationProfile()
    clinical_instruction = SAMPLE_TRANSLATION_DIR / "translation_test_instructions_v9_sanitized.md"
    clinical_text = generic.task_instruction
    if clinical_instruction.exists():
        clinical_text = clinical_instruction.read_text(encoding="utf-8", errors="replace")

    clinical = TranslationProfile(
        name="Clinical/Localization Protected Segment Test",
        task_instruction=clinical_text,
        source_language="English",
        default_register_mode="Professional/staff-facing",
        dnt_terms=[
            "FICTIVE_CLIENT_ALPHA",
            "FICTIVE_LSP",
            "SAMPLE_APP",
            "STUDY-000-0001",
            "WORKUNIT-ALPHA-001",
            "WORKORDER-ALPHA-001",
            "QR",
            "RFI",
            "eCOA",
            "ICF",
            "PRO",
            "SAE",
            "SUSAR",
            "placebo",
            "{patient_name}",
            "{visit_date}",
            "{{username}}",
            "%APPDATA%",
            r"C:\Users\TestUser\AppData\Local",
            "https://example.invalid/study/STUDY-000-0001",
            "ISO 8601",
            "UTC+02:00",
        ],
        protected_token_regexes=[
            r"https?://[^\s<>\"]+",
            r"(?:[A-Za-z]:\\(?:[^\\/:*?\"<>|\r\n]+\\)*[^\\/:*?\"<>|\r\n]*|\\\\[^\\\s]+\\[^\\\s]+(?:\\[^\\\s]+)*)",
            r"\b(?:STUDY|WORKUNIT|WORKORDER)-[A-Z0-9_-]+\b",
            r"\b(?:SAMPLE_APP|QR|RFI|eCOA|ICF|PRO|SAE|SUSAR)\b",
        ],
        delimiter_style="Percent Segment Blocks",
    )
    return {generic.name: generic, clinical.name: clinical}


def load_translation_profile(path: str | Path | None, profile_name: str = "") -> TranslationProfile:
    builtins = builtin_translation_profiles()
    if profile_name and profile_name in builtins:
        return builtins[profile_name]

    if not path:
        return builtins.get(profile_name) or builtins["Generic Translation"]

    profile_path = Path(path)
    if not profile_path.exists():
        raise FileNotFoundError(f"Translation profile/instruction file not found: {profile_path}")

    if profile_path.suffix.lower() == ".json":
        data = json.loads(profile_path.read_text(encoding="utf-8"))
        return profile_from_dict(data, default_name=profile_path.stem)

    text = profile_path.read_text(encoding="utf-8", errors="replace")
    name = profile_name or profile_path.stem
    return TranslationProfile(name=name, task_instruction=text)


def profile_from_dict(data: dict[str, Any], default_name: str = "Custom Translation Profile") -> TranslationProfile:
    glossary_terms = [
        GlossaryTerm(
            source_term=str(item.get("source_term", "")).strip(),
            target_term=str(item.get("target_term", "")).strip(),
            context=str(item.get("context", "")).strip(),
            note=str(item.get("note", "")).strip(),
        )
        for item in data.get("glossary_terms", [])
        if isinstance(item, dict) and str(item.get("source_term", "")).strip()
    ]
    return TranslationProfile(
        name=str(data.get("name") or default_name),
        task_instruction=str(data.get("task_instruction") or TranslationProfile().task_instruction),
        source_language=str(data.get("source_language") or "English"),
        target_language=str(data.get("target_language") or ""),
        register_modes=[str(value) for value in data.get("register_modes", TranslationProfile().register_modes)],
        default_register_mode=str(data.get("default_register_mode") or "Professional/staff-facing"),
        dnt_terms=[str(value) for value in data.get("dnt_terms", [])],
        glossary_terms=glossary_terms,
        placeholder_regexes=[str(value) for value in data.get("placeholder_regexes", TranslationProfile().placeholder_regexes)],
        protected_token_regexes=[str(value) for value in data.get("protected_token_regexes", TranslationProfile().protected_token_regexes)],
        delimiter_style=str(data.get("delimiter_style") or "Percent Segment Blocks"),
        delimiter_regex=str(data.get("delimiter_regex") or ""),
        validation_rules=dict(data.get("validation_rules", TranslationProfile().validation_rules)),
        output_format_rules=str(data.get("output_format_rules") or TranslationProfile().output_format_rules),
    )


def load_glossary(path: str | Path) -> list[GlossaryTerm]:
    if not str(path).strip():
        return []
    glossary_path = Path(path)
    if not glossary_path.exists():
        raise FileNotFoundError(f"Glossary file not found: {glossary_path}")

    with glossary_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"source_term"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Glossary CSV is missing column(s): {', '.join(sorted(missing))}")
        terms: list[GlossaryTerm] = []
        for row in reader:
            source = str(row.get("source_term", "")).strip()
            if not source:
                continue
            terms.append(
                GlossaryTerm(
                    source_term=source,
                    target_term=str(row.get("target_term", "")).strip(),
                    context=str(row.get("context", "")).strip(),
                    note=str(row.get("note", "")).strip(),
                )
            )
        return terms


def load_line_list(path: str | Path) -> list[str]:
    if not str(path).strip():
        return []
    list_path = Path(path)
    if not list_path.exists():
        raise FileNotFoundError(f"List file not found: {list_path}")
    terms: list[str] = []
    for line in list_path.read_text(encoding="utf-8", errors="replace").splitlines():
        clean = line.strip()
        if clean and not clean.startswith("#"):
            terms.append(clean)
    return terms


def compile_regexes(patterns: list[str]) -> list[re.Pattern[str]]:
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        if pattern.strip():
            compiled.append(re.compile(pattern))
    return compiled
