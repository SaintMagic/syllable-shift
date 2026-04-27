from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from segmentation import Segment, SegmentParser
from translation_profiles import TranslationProfile, compile_regexes


URL_RE = re.compile(r"https?://[^\s<>\"]+")
WINDOWS_PATH_RE = re.compile(
    r"(?:[A-Za-z]:\\(?:[^\\/:*?\"<>|\r\n]+\\)*[^\\/:*?\"<>|\r\n]*|\\\\[^\\\s]+\\[^\\\s]+(?:\\[^\\\s]+)*)"
)
ID_CODE_RE = re.compile(r"\b(?:[A-Z]{2,}[A-Z0-9_-]*|\d{4}-\d{2}-\d{2}|UTC[+-]\d{2}:\d{2})\b")
NOTE_RE = re.compile(
    r"<!--.*?-->|^\s*(translator'?s?\s+note|note|warning|commentary)\s*:",
    re.IGNORECASE | re.DOTALL | re.MULTILINE,
)
LEAK_RE = re.compile(r"\[(?:TARGET TERM|TARGET LANGUAGE|REGISTER MODE|SOURCE LANGUAGE)\]")


@dataclass
class ValidationIssue:
    severity: str
    category: str
    message: str
    segment_id: str | None = None
    token: str | None = None
    source_count: int | None = None
    output_count: int | None = None


@dataclass
class ValidationReport:
    source_segment_count: int = 0
    output_segment_count: int = 0
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity in {"critical", "error"})

    @property
    def warning_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "warning")

    @property
    def passed(self) -> bool:
        return self.error_count == 0

    def to_dict(self) -> dict[str, object]:
        return {
            "source_segment_count": self.source_segment_count,
            "output_segment_count": self.output_segment_count,
            "errors": self.error_count,
            "warnings": self.warning_count,
            "passed": self.passed,
            "issues": [asdict(issue) for issue in self.issues],
        }

    def format(self, grouped: bool = True) -> str:
        status = "PASS" if self.passed else "FAIL"
        lines = [
            f"Translation validation: {status}",
            f"Source segments: {self.source_segment_count}",
            f"Output segments: {self.output_segment_count}",
            f"Errors: {self.error_count}",
            f"Warnings: {self.warning_count}",
            "",
        ]
        if not self.issues:
            lines.append("No validation issues found.")
            return "\n".join(lines).rstrip() + "\n"

        if not grouped:
            lines.append("Raw issue list")
            lines.append("-" * 40)
            for issue in self.issues:
                sid = issue.segment_id or "GLOBAL"
                token = f" | token={issue.token}" if issue.token else ""
                lines.append(f"[{issue.severity.upper()}] {sid} {issue.category}: {issue.message}{token}")
            return "\n".join(lines).rstrip() + "\n"

        by_segment: dict[str, list[ValidationIssue]] = defaultdict(list)
        for issue in self.issues:
            by_segment[issue.segment_id or "GLOBAL"].append(issue)

        lines.append("Grouped issues")
        lines.append("-" * 40)
        for sid in sorted(by_segment, key=lambda value: (value != "GLOBAL", value)):
            segment_issues = by_segment[sid]
            lines.append(f"\nSegment {sid}")
            root = segment_issues[0]
            lines.append(f"- [{root.severity.upper()}] {root.category}: {root.message}")
            for issue in segment_issues[1:]:
                token = f" `{issue.token}`" if issue.token else ""
                lines.append(f"  - [{issue.severity.upper()}] {issue.category}:{token} {issue.message}")
        return "\n".join(lines).rstrip() + "\n"


class TranslationValidator:
    def __init__(
        self,
        profile: TranslationProfile,
        parser: SegmentParser,
        dnt_terms: list[str] | None = None,
        protected_regexes: list[str] | None = None,
        grouped_report: bool = True,
    ) -> None:
        self.profile = profile
        self.parser = parser
        self.dnt_terms = list(dict.fromkeys((dnt_terms or []) + profile.dnt_terms))
        self.protected_regexes = compile_regexes((protected_regexes or []) + profile.protected_token_regexes)
        self.placeholder_regexes = compile_regexes(profile.placeholder_regexes)
        self.grouped_report = grouped_report

    def validate_files(self, source_file: Path, output_file: Path) -> ValidationReport:
        if not source_file.exists():
            raise FileNotFoundError(f"Validation source file not found: {source_file}")
        if not output_file.exists():
            raise FileNotFoundError(f"Validation output file not found: {output_file}")
        return self.validate_texts(
            source_file.read_text(encoding="utf-8", errors="replace"),
            output_file.read_text(encoding="utf-8", errors="replace"),
        )

    def validate_texts(self, source_text: str, output_text: str) -> ValidationReport:
        report = ValidationReport()
        source_segments = self.parser.parse(source_text)
        output_segments = self.parser.parse(output_text)
        report.source_segment_count = len(source_segments)
        report.output_segment_count = len(output_segments)

        source_map = {segment.id: segment for segment in source_segments}
        output_map = {segment.id: segment for segment in output_segments}
        source_ids = [segment.id for segment in source_segments]
        output_ids = [segment.id for segment in output_segments]

        self._check_segment_structure(source_ids, output_ids, report)

        for sid in source_ids:
            source = source_map[sid]
            output = output_map.get(sid)
            if output is None:
                continue
            self._check_segment(source, output, report)

        return report

    def save_report(self, report: ValidationReport, path: Path, grouped: bool = True, save_json: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report.format(grouped=grouped), encoding="utf-8", newline="\n")
        if save_json:
            json_path = path.with_suffix(path.suffix + ".json")
            json_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")

    def _add(
        self,
        report: ValidationReport,
        severity: str,
        category: str,
        message: str,
        segment_id: str | None = None,
        token: str | None = None,
        source_count: int | None = None,
        output_count: int | None = None,
    ) -> None:
        report.issues.append(
            ValidationIssue(
                severity=severity,
                category=category,
                message=message,
                segment_id=segment_id,
                token=token,
                source_count=source_count,
                output_count=output_count,
            )
        )

    def _check_segment_structure(self, source_ids: list[str], output_ids: list[str], report: ValidationReport) -> None:
        if len(source_ids) != len(output_ids):
            self._add(
                report,
                "error",
                "segment_count",
                f"Source has {len(source_ids)} segments but output has {len(output_ids)}.",
            )

        missing = [sid for sid in source_ids if sid not in output_ids]
        extra = [sid for sid in output_ids if sid not in source_ids]
        for sid in missing:
            self._add(report, "error", "missing_segment", f"Output is missing segment {sid}.", sid)
        for sid in extra:
            self._add(report, "error", "extra_segment", f"Output contains unexpected segment {sid}.", sid)

        common_source_order = [sid for sid in source_ids if sid in output_ids]
        common_output_order = [sid for sid in output_ids if sid in source_ids]
        if common_source_order != common_output_order:
            self._add(report, "error", "segment_order", "Output segment order does not match source order.")

    def _check_segment(self, source: Segment, output: Segment, report: ValidationReport) -> None:
        sid = source.id
        if source.start_delimiter and source.start_delimiter != output.start_delimiter:
            self._add(report, "error", "start_delimiter", "START delimiter changed.", sid)
        if source.end_delimiter and source.end_delimiter != output.end_delimiter:
            self._add(report, "error", "end_delimiter", "END delimiter changed.", sid)

        if not source.body.strip() and output.body.strip():
            self._add(report, "error", "empty_segment", "Source segment was empty but output contains text.", sid)

        self._check_exact_terms(source.body, output.body, self.dnt_terms, "dnt_term", sid, report)
        self._check_regex_tokens(source.body, output.body, self.placeholder_regexes, "placeholder", sid, report)
        self._check_regex_tokens(source.body, output.body, [URL_RE], "url", sid, report)
        self._check_regex_tokens(source.body, output.body, [WINDOWS_PATH_RE], "path", sid, report)
        self._check_regex_tokens(source.body, output.body, [ID_CODE_RE], "id_code", sid, report)
        self._check_regex_tokens(source.body, output.body, self.protected_regexes, "protected_token", sid, report)

        for leak in LEAK_RE.findall(output.body):
            self._add(report, "error", "leaked_placeholder", "Output contains an unresolved profile placeholder.", sid, leak)

        if NOTE_RE.search(output.body):
            self._add(report, "warning", "translator_note", "Output appears to contain a translator note/comment.", sid)

    def _check_exact_terms(
        self,
        source_text: str,
        output_text: str,
        terms: list[str],
        category: str,
        sid: str,
        report: ValidationReport,
    ) -> None:
        for term in terms:
            source_count = source_text.count(term)
            if source_count <= 0:
                continue
            output_count = output_text.count(term)
            if source_count != output_count:
                self._add(
                    report,
                    "error",
                    category,
                    f"Expected {source_count} occurrence(s), found {output_count}.",
                    sid,
                    term,
                    source_count,
                    output_count,
                )

    def _check_regex_tokens(
        self,
        source_text: str,
        output_text: str,
        regexes: list[re.Pattern[str]],
        category: str,
        sid: str,
        report: ValidationReport,
    ) -> None:
        expected: dict[str, int] = defaultdict(int)
        actual: dict[str, int] = defaultdict(int)
        for regex in regexes:
            for token in regex.findall(source_text):
                expected[self._flatten_match(token)] += 1
            for token in regex.findall(output_text):
                actual[self._flatten_match(token)] += 1

        for token, source_count in sorted(expected.items()):
            output_count = actual.get(token, 0)
            if source_count != output_count:
                self._add(
                    report,
                    "error",
                    category,
                    f"Expected {source_count} occurrence(s), found {output_count}.",
                    sid,
                    token,
                    source_count,
                    output_count,
                )

    def _flatten_match(self, value: object) -> str:
        if isinstance(value, tuple):
            return "".join(str(part) for part in value if part)
        return str(value)
