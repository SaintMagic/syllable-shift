from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Pattern


@dataclass
class Segment:
    id: str
    body: str
    start_line: int = 1
    end_line: int = 1
    start_delimiter: str = ""
    end_delimiter: str = ""

    @property
    def word_count(self) -> int:
        return len(self.body.split())


class SegmentParser:
    """Configurable source segment parser for translation/rewrite-like workflows."""

    PERCENT_START = re.compile(r"^%%% SEGMENT (?P<id>\d+) START %%%$")
    PERCENT_END = re.compile(r"^%%% SEGMENT (?P<id>\d+) END %%%$")
    MARKDOWN_HEADING = re.compile(r"^#{1,6}\s+(?P<id>.+?)\s*$")

    STYLES = (
        "Percent Segment Blocks",
        "Markdown Headings",
        "Blank Line Blocks",
        "Whole File",
    )

    def __init__(self, style: str = "Percent Segment Blocks", delimiter_regex: str = "") -> None:
        if style not in self.STYLES:
            raise ValueError(f"Unknown segment delimiter style: {style}")
        self.style = style
        self.custom_delimiter_re: Pattern[str] | None = re.compile(delimiter_regex) if delimiter_regex else None

    def parse(self, text: str) -> list[Segment]:
        if self.custom_delimiter_re is not None:
            return self._parse_custom_line_delimiters(text)
        if self.style == "Percent Segment Blocks":
            return self._parse_percent_blocks(text)
        if self.style == "Markdown Headings":
            return self._parse_markdown_headings(text)
        if self.style == "Blank Line Blocks":
            return self._parse_blank_line_blocks(text)
        return [Segment(id="0001", body=text.strip(), start_line=1, end_line=max(1, len(text.splitlines())))]

    def render_segment(self, segment: Segment, body: str) -> str:
        body = body.strip("\n")
        parts: list[str] = []
        if segment.start_delimiter:
            parts.append(segment.start_delimiter)
        parts.append(body)
        if segment.end_delimiter:
            parts.append(segment.end_delimiter)
        return "\n".join(parts).strip("\n") + "\n"

    def render_segments(self, segments: list[Segment], bodies: dict[str, str]) -> str:
        rendered = [self.render_segment(segment, bodies.get(segment.id, segment.body)) for segment in segments]
        return "\n".join(part.rstrip("\n") for part in rendered).rstrip() + "\n"

    def chunk_segments(self, segments: list[Segment], chunk_size: int) -> list[list[Segment]]:
        if chunk_size < 1:
            raise ValueError("translation_chunk_segments must be at least 1.")
        return [segments[index:index + chunk_size] for index in range(0, len(segments), chunk_size)]

    def _parse_percent_blocks(self, text: str) -> list[Segment]:
        segments: list[Segment] = []
        lines = text.splitlines()
        index = 0
        while index < len(lines):
            start = self.PERCENT_START.match(lines[index])
            if not start:
                index += 1
                continue

            sid = start.group("id")
            start_line = index + 1
            start_delimiter = lines[index]
            body_lines: list[str] = []
            index += 1
            end_delimiter = ""
            end_line = start_line
            while index < len(lines):
                end = self.PERCENT_END.match(lines[index])
                if end and end.group("id") == sid:
                    end_delimiter = lines[index]
                    end_line = index + 1
                    break
                body_lines.append(lines[index])
                index += 1

            if not end_delimiter:
                raise ValueError(f"Segment {sid} has no matching END delimiter.")

            segments.append(
                Segment(
                    id=sid,
                    body="\n".join(body_lines).strip("\n"),
                    start_line=start_line,
                    end_line=end_line,
                    start_delimiter=start_delimiter,
                    end_delimiter=end_delimiter,
                )
            )
            index += 1
        return segments

    def _parse_custom_line_delimiters(self, text: str) -> list[Segment]:
        """Parse paired line delimiters with named groups: id and kind/start/end."""
        segments: list[Segment] = []
        lines = text.splitlines()
        index = 0
        while index < len(lines):
            match = self.custom_delimiter_re.match(lines[index])
            if not match:
                index += 1
                continue
            groups = match.groupdict()
            sid = groups.get("id") or f"{len(segments) + 1:04d}"
            kind = (groups.get("kind") or groups.get("marker") or "START").upper()
            if kind != "START":
                index += 1
                continue
            start_delimiter = lines[index]
            start_line = index + 1
            body_lines: list[str] = []
            index += 1
            end_delimiter = ""
            end_line = start_line
            while index < len(lines):
                end = self.custom_delimiter_re.match(lines[index])
                if end:
                    end_groups = end.groupdict()
                    end_id = end_groups.get("id") or sid
                    end_kind = (end_groups.get("kind") or end_groups.get("marker") or "").upper()
                    if end_id == sid and end_kind == "END":
                        end_delimiter = lines[index]
                        end_line = index + 1
                        break
                body_lines.append(lines[index])
                index += 1
            if not end_delimiter:
                raise ValueError(f"Segment {sid} has no matching custom END delimiter.")
            segments.append(
                Segment(
                    id=sid,
                    body="\n".join(body_lines).strip("\n"),
                    start_line=start_line,
                    end_line=end_line,
                    start_delimiter=start_delimiter,
                    end_delimiter=end_delimiter,
                )
            )
            index += 1
        return segments

    def _parse_markdown_headings(self, text: str) -> list[Segment]:
        segments: list[Segment] = []
        lines = text.splitlines()
        current_id = ""
        current_start = 1
        current_heading = ""
        current_body: list[str] = []

        def flush(end_line: int) -> None:
            if current_id:
                segments.append(
                    Segment(
                        id=current_id,
                        body="\n".join(current_body).strip("\n"),
                        start_line=current_start,
                        end_line=end_line,
                        start_delimiter=current_heading,
                    )
                )

        for idx, line in enumerate(lines, start=1):
            heading = self.MARKDOWN_HEADING.match(line)
            if heading:
                flush(idx - 1)
                current_id = re.sub(r"\s+", "_", heading.group("id").strip())[:80] or f"{len(segments) + 1:04d}"
                current_start = idx
                current_heading = line
                current_body = []
            else:
                current_body.append(line)
        flush(len(lines))
        return segments

    def _parse_blank_line_blocks(self, text: str) -> list[Segment]:
        blocks = [block.strip("\n") for block in re.split(r"\n\s*\n", text.strip()) if block.strip()]
        segments: list[Segment] = []
        line_cursor = 1
        for index, block in enumerate(blocks, start=1):
            line_count = max(1, len(block.splitlines()))
            segments.append(
                Segment(
                    id=f"{index:04d}",
                    body=block,
                    start_line=line_cursor,
                    end_line=line_cursor + line_count - 1,
                )
            )
            line_cursor += line_count + 1
        return segments
