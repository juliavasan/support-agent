"""HTML -> plain-text sections, and an allow-list sanitizer for KB article HTML.

Standard library only. KB article bodies come from ServiceNow (and, at seed time,
from public docs pages), so both functions treat the input as untrusted markup.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser

HEADINGS = {"h1", "h2", "h3", "h4"}
BLOCKS = HEADINGS | {"p", "li", "tr", "pre", "div", "br", "table", "ul", "ol", "blockquote"}
DROPPED = {"script", "style", "nav", "svg", "noscript", "button", "form", "iframe"}

# Tags kept by sanitize_html, each with the attributes it may keep.
ALLOWED_TAGS: dict[str, set[str]] = {
    **{tag: set() for tag in ("p", "h2", "h3", "h4", "ul", "ol", "li", "strong", "em", "b", "i")},
    **{tag: set() for tag in ("code", "pre", "blockquote", "br", "table", "thead", "tbody")},
    **{tag: set() for tag in ("tr", "th", "td")},
    "a": {"href"},
}
VOID_TAGS = {"br"}


@dataclass
class Section:
    heading: str
    text: str


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[Section] = []
        self._heading = ""
        self._parts: list[str] = []
        self._in_heading = False
        self._heading_parts: list[str] = []
        self._skip_depth = 0

    def _flush(self) -> None:
        text = _collapse("".join(self._parts))
        if text:
            self.sections.append(Section(self._heading, text))
        self._parts = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in DROPPED:
            self._skip_depth += 1
        elif tag in HEADINGS:
            self._flush()
            self._in_heading = True
            self._heading_parts = []
        elif tag in BLOCKS:
            self._parts.append("\n")
        elif tag in {"td", "th"}:
            self._parts.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if tag in DROPPED:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in HEADINGS and self._in_heading:
            self._heading = _collapse("".join(self._heading_parts))
            self._in_heading = False
        elif tag in BLOCKS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        (self._heading_parts if self._in_heading else self._parts).append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def _collapse(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line and line != "|")


def extract_sections(markup: str) -> list[Section]:
    """Split HTML into sections at h1-h4 headings. Text before the first heading has
    an empty heading."""
    parser = _Extractor()
    parser.feed(markup)
    parser.close()
    return parser.sections


def html_to_text(markup: str) -> str:
    return "\n".join(
        f"{s.heading}\n{s.text}" if s.heading else s.text for s in extract_sections(markup)
    )


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in DROPPED:
            self._skip_depth += 1
            return
        if self._skip_depth or tag not in ALLOWED_TAGS:
            return
        kept = ""
        for name, value in attrs:
            # Only absolute http(s) links survive: no javascript:, data:, or relative URLs.
            if name in ALLOWED_TAGS[tag] and value and re.match(r"^https?://", value):
                kept += f' {name}="{html.escape(value, quote=True)}"'
        self.out.append(f"<{tag}{kept}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in DROPPED:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if not self._skip_depth and tag in ALLOWED_TAGS and tag not in VOID_TAGS:
            self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.out.append(html.escape(data, quote=False))


def sanitize_html(markup: str) -> str:
    """Keep structural tags only (headings, paragraphs, lists, tables, code, links)."""
    sanitizer = _Sanitizer()
    sanitizer.feed(markup)
    sanitizer.close()
    return re.sub(r"\n{3,}", "\n\n", "".join(sanitizer.out)).strip()
