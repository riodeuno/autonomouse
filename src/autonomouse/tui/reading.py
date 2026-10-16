"""The reading view: text on the board shown as a document.

Every screen that shows text to read (the project page today; the card details pane and
the decision and planning screens next) shows it through `ReadingView`, so they all
look the same. The human asked for it on 2026-09-28: a project's page was "impossible to
read with all text of the same size with no clear boundaries".

A screen hands the view one Markdown text. The view renders it with Textual's `Markdown`
widget in a column at most `COLUMN_WIDTH` cells wide: centred when the space is wider,
the full width when it is narrower. A terminal cannot show larger letters, so headings
stand apart by colour, weight and a rule under the top two levels, and blocks
(paragraphs, lists, tables) have a blank line between them.

Board text written as Markdown (a project's brief) goes in as it is. Every other piece of
board text (a goal, a title, a summary) goes through `plain` first, so that a `*`, a
`|` or a line starting with `#` shows as written and never turns into formatting.

Raw HTML shows as written rather than rendered: agents write placeholders such as
`<id>` or `<your answer>` in their text, and a terminal cannot render HTML, so reading
them as tags would only drop those words from the screen. Code shows as written too.

Plain URLs stay plain text rather than links, and a link written in Markdown opens
nothing when clicked: over ssh, "open" would start a browser on the server, not on the
reader's machine. The reader copies the URL instead.
"""

from __future__ import annotations

import re

from markdown_it import MarkdownIt
from textual.app import ComposeResult
from textual.await_complete import AwaitComplete
from textual.containers import VerticalScroll
from textual.widgets import Markdown

COLUMN_WIDTH = 88  # cells, including the column's own padding of 2 on each side

# CommonMark lets a backslash escape any ASCII punctuation; these are the characters
# that can start formatting anywhere in a line
_INLINE = re.compile(r"([\\`*_\[\]<>|~#&])")
# and these only at the start of a line: a list item or a setext underline
_LINE_START = re.compile(r"^(\d+)([.)])|^([-+=])")


def plain(text: str) -> str:
    """`text` made safe to place in Markdown: it renders as written, never as
    formatting. A line's leading spaces are dropped (four would start a code block). A
    blank line still starts a new paragraph; a single line break shows as a space, as
    it does in any Markdown paragraph."""
    lines = []
    for line in text.splitlines():
        line = _INLINE.sub(r"\\\1", line.lstrip())
        lines.append(_LINE_START.sub(_escape_line_start, line))
    return "\n".join(lines)


def _escape_line_start(match: re.Match[str]) -> str:
    if match.group(3):
        return "\\" + match.group(3)
    return match.group(1) + "\\" + match.group(2)


def _parser() -> MarkdownIt:
    """Textual's own parser, with raw HTML off and without turning bare URLs into
    links (module docstring)."""
    return MarkdownIt("gfm-like", {"html": False}).disable("linkify")


class ReadingView(VerticalScroll):
    """A scrolling page that shows one Markdown text in the reading column.

    `update` replaces the text; `source` is the text shown. The rendered blocks are
    children of `document`, Textual's `Markdown` widget."""

    DEFAULT_CSS = f"""
    ReadingView {{
        align-horizontal: center;
        padding: 1 0;
        scrollbar-size-vertical: 1;
    }}
    ReadingView > Markdown {{
        width: 1fr;
        max-width: {COLUMN_WIDTH};
        padding: 0 2;
    }}
    ReadingView MarkdownH1 {{
        content-align: left top;
        color: $text-primary;
        background: transparent;
        text-style: bold;
        margin: 0 0 1 0;
        border-bottom: heavy $primary;
    }}
    ReadingView MarkdownH2 {{
        color: $text-primary;
        background: transparent;
        text-style: bold;
        margin: 2 0 1 0;
        border-bottom: solid $primary-muted;
    }}
    ReadingView MarkdownH3 {{
        width: 1fr;
        color: $foreground;
        background: transparent;
        text-style: bold;
        margin: 1 0 0 0;
    }}
    ReadingView MarkdownH4, ReadingView MarkdownH5, ReadingView MarkdownH6 {{
        color: $foreground-muted;
        background: transparent;
        text-style: bold;
        margin: 1 0 0 0;
    }}
    """

    def __init__(
        self,
        markdown: str = "",
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._initial = markdown

    def compose(self) -> ComposeResult:
        yield Markdown(self._initial, parser_factory=_parser, open_links=False)

    @property
    def document(self) -> Markdown:
        return self.query_one(Markdown)

    @property
    def source(self) -> str:
        """The Markdown text the view shows."""
        return self.document.source

    def update(self, markdown: str) -> AwaitComplete:
        """Show `markdown` in place of the current text. Await the result to wait until
        its blocks are on screen; the text is `source` at once."""
        return self.document.update(markdown)
