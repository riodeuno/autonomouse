"""The project details page: `i` on the project list (for the focused project) or on a
project's board opens it, and escape goes back to the screen that opened it.

It shows what `autonomouse show <slug>` prints above its stacks, read through the same
function, `autonomouse.overview.project_details`, as one Markdown text in the shared
reading view (`autonomouse.tui.reading.ReadingView`), in the look the human chose on
2026-09-30:

- the header (`autonomouse.tui.logo.AppHeader`) has the project's name as its title, and
  under it the same status line and description the project's board shows
  (`autonomouse.tui.project_header`): the project's status pill, its slug, what its
  workers do, what waits on the human and when it last changed. The header reads them
  again whenever the board changes, and once a minute (its own clock);
- the reading column sits in the viewer's frame (`autonomouse.tui.look.FRAME`), bright
  while it holds focus, which it does for as long as the page is open;
- the footer is the framed one (`autonomouse.tui.footer.AppFooter`), with this
  project's slug and the version at its right.

The sections go from what may need the human now to what rarely changes, each under a
heading with its icon (`autonomouse.tui.icons`, in the form the app holds), under the
project's name and its project icon:

- Cards: a count per stack (a table), in the board's order (`autonomouse.tui.look.
  STACK_ORDER`), so the decisions waiting on the human come first. Each row's first
  cell reads `<icon> <stack word>` in the Markdown, which the page draws as that stack's
  pill (`autonomouse.tui.look.stack_pill`), the pill the board's details pane uses;
- Open pull requests: the repository's, each with the id of the card whose pull
  request it is (a table); none merges until the human merges it;
- Wakes: the wake that holds the project now, with whether it is alive, and the last
  wakes, each under its id and the wake icon, with when it started and ended and its
  summary;
- Goal, then Settings: the slug, repository, default branch and priority;
- last, after a rule, the brief (`projects/<slug>/brief.md`) as the Markdown it is,
  when the project has one. It is a whole document of its own, often longer than a
  screen, so above the rest it kept everything else off the first screen.

The icons also mark each stack, the wake that holds the claim, and a list that could
not be read.

The open pull requests come from `gh pr list`, a network call, so the page never waits
for them on the screen's own thread. It first draws everything else from the board,
with `PULLS_LOADING` where the list goes; a thread worker then reads the details again,
this time with the list, and the page redraws from that. When gh fails, the list's
place says why. Once that worker has loaded the list once, a later redraw (the live
poll below finding the board changed) keeps it rather than asking gh again.

The page writes nothing: it only reads the board and asks gh for a list.

The page also watches the board folder (`autonomouse.tui.live.LiveRefresh`), the same
mixin the other screens use: its first read is that watch's first poll, so a bad
project or card file already bad when `i` opens the page leaves it blank, with the
error in its status line, until a read succeeds (sys-m9). That poll's own read of the
board never asks gh (`_pulls_later`), so a bad file is never mistaken for a failed
network call. The claim's liveness (alive, stalled, crashed) is part of the page's
stamp (`_board_stamp`), so the page reads again when the claiming wake stops, though no
board file changed.
"""

from __future__ import annotations

from dataclasses import replace
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.content import Content
from textual.screen import Screen
from textual.widgets import Markdown
from textual.widgets.markdown import MarkdownBlock
from textual.worker import Worker, WorkerState

from autonomouse.board.commands import project_show
from autonomouse.board.paths import board_root
from autonomouse.board.runs import claim_liveness
from autonomouse.board.schema import Stack
from autonomouse.overview import (
    ProjectDetails,
    gh_open_prs,
    project_details,
)
from autonomouse.tui.footer import AppFooter
from autonomouse.tui.icons import NERD, PLAIN, Icons
from autonomouse.tui.live import LiveRefresh, ReadError
from autonomouse.tui.logo import AppHeader
from autonomouse.tui.look import FRAME, STACK_ORDER, stack_label, stack_pill
from autonomouse.tui.project_header import description, status_line
from autonomouse.tui.reading import ReadingView, plain

PULLS_LOADING = "reading the open pull requests from GitHub…"


def _stack_words(stack: Stack, *, icons: Icons) -> str:
    """A stack as the first cell of its count row says it: its icon and its word."""
    return f"{icons.stack[stack]} {stack_label(stack)}"


# The pill each such cell stands for, in both icon forms. The Markdown stays the plain
# words a reader can copy; the page draws the cell as the filled pill, the way the card
# details pane draws a question's `OPEN` pill (`autonomouse.tui.card_detail`).
_PILLS: dict[str, Content] = {
    _stack_words(stack, icons=icons): stack_pill(stack, icons=icons)
    for stack in STACK_ORDER
    for icons in (PLAIN, NERD)
}

# Textual's own table cell block, which it does not export by name
_Cell = Markdown.BLOCKS["td_open"]


class _PillCell(_Cell):
    """A table cell that draws the words of a stack's count row (`_stack_words`) as its
    pill, and every other cell as Markdown does."""

    def set_content(self, content: Content) -> None:
        super().set_content(_PILLS.get(content.plain, content))


_BLOCKS: dict[str, type[MarkdownBlock]] = {**Markdown.BLOCKS, "td_open": _PillCell}


class ProjectReading(ReadingView):
    """The page's reading column in the viewer's frame (`FRAME`), bright while it holds
    focus, and drawing each stack's count as its pill."""

    DEFAULT_CSS = f"""
    ProjectReading {{
        border: {FRAME.rest};
    }}
    ProjectReading:focus {{
        border: {FRAME.focused};
    }}
    """

    def compose(self) -> ComposeResult:
        for widget in super().compose():
            if isinstance(widget, Markdown):
                widget.BLOCKS = _BLOCKS  # draws a stack's count cell as its pill
            yield widget


def _pulls_later(_repo: str) -> list[dict[str, str]]:
    """The pull-request reader of the first, instant read: it skips gh, because the
    worker reads the list afterwards."""
    return []


def _when(at: str | None) -> str:
    """`2026-09-22T08:55:16Z` -> `2026-09-22 08:55`; no time -> `-`."""
    if not at:
        return "-"
    return at[:16].replace("T", " ") if len(at) >= 16 else at


def _cell(text: str) -> str:
    """Board text in one table cell: on one line, and shown as written."""
    return plain(" ".join(text.split()))


def _pulls_section(details: ProjectDetails, *, loading: bool, icons: Icons) -> list[str]:
    heading = f"## {icons.pr} Open pull requests"
    if loading:
        return [heading, f"*{plain(PULLS_LOADING)}*"]
    if details.pulls is None:
        why = plain(details.pulls_error or "")
        return [heading, f"{icons.failed} **Could not read them:** {why}"]
    if not details.pulls:
        return [heading, "None."]
    rows = []
    for pull in details.pulls:
        card = f"{icons.card} {_cell(pull.card)}" if pull.card else "no card"
        rows.append(f"| {card} | {_cell(pull.url)} | {_cell(pull.title)} |")
    table = "\n".join(["| Card | Pull request | Title |", "|---|---|---|", *rows])
    return [f"{heading} ({len(details.pulls)})", table]


def _wakes_section(details: ProjectDetails, *, icons: Icons) -> list[str]:
    """The wake that holds the project now, then the last wakes, newest first."""
    if details.project.claim.run_id is None:
        now = "**Now:** no wake holds the project."
    else:
        now = f"**Now:** {icons.wake} {plain(details.claim)}"
    blocks = [f"## {icons.wake} Wakes", now]
    if not details.wakes:
        return [*blocks, "No wake has run yet."]
    blocks.append("The last wakes, newest first:")
    for wake in details.wakes:
        blocks += [
            f"### {icons.wake} {plain(wake.run_id)}",
            f"Started {_when(wake.started_at)}, ended {_when(wake.ended_at)}.",
            plain(wake.summary) if wake.summary else "*No summary.*",
        ]
    return blocks


def format_project(
    details: ProjectDetails, *, pulls_loading: bool = False, icons: Icons = PLAIN
) -> str:
    """The page as one Markdown text: a pure function of the details, so a test can read
    it without a screen. The brief goes in as the Markdown it is; every other piece of
    board text goes through `plain`, so it shows as written. With `pulls_loading`, the
    pull-request list is not read yet and its place says so."""
    project = details.project
    brief = details.brief.text.strip() if details.brief else ""
    stacks = "\n".join(
        [
            "| Stack | Cards |",
            "|---|--:|",
            *(
                f"| {_stack_words(stack, icons=icons)} | {details.counts[stack]} |"
                for stack in STACK_ORDER
            ),
        ]
    )
    blocks = [
        f"# {icons.project} {plain(project.name)}",
        f"## {icons.card} Cards",
        stacks,
        *_pulls_section(details, loading=pulls_loading, icons=icons),
        *_wakes_section(details, icons=icons),
        f"## {icons.goal} Goal",
        plain(project.goal),
        f"## {icons.settings} Settings",
        "\n".join(
            [
                f"- **Slug:** {plain(project.slug)}",
                f"- **Repository:** {plain(project.repo)}",
                f"- **Default branch:** {plain(project.default_branch)}",
                f"- **Priority:** {project.priority}",
            ]
        ),
        *(["---", brief] if brief else []),
    ]
    return "\n\n".join(blocks) + "\n"


class ProjectScreen(LiveRefresh, Screen):
    """One project's details. Read-only; escape goes back."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "app.pop_screen", "Back", key_display="esc"),
    ]

    def __init__(self, slug: str) -> None:
        super().__init__()
        self.slug = slug
        # the gh-backed read, kept once loaded so a later poll's redraw (the board
        # changed) does not ask gh again; and whether that read has been started, so
        # a second board change while it is still running does not start a second one
        self._pulls_details: ProjectDetails | None = None
        self._pulls_requested = False

    def compose(self) -> ComposeResult:
        # no details yet: the first poll (`_start_live_refresh`) reads them and draws
        # them through `_board_changed`, so a bad file when the page opens is shown,
        # not raised
        yield AppHeader(
            status=lambda: status_line(board_root(), self.slug, icons=self.app.icons),
            description=lambda: description(board_root(), self.slug),
        )
        yield ReadError()
        yield ProjectReading(id="project-details")
        yield AppFooter(slug=self.slug)

    def on_mount(self) -> None:
        self.title = self.slug  # until the first read gives the project's name
        self._start_live_refresh()  # its first poll reads the project and draws it

    def _board_stamp(self) -> object:
        # the claim's liveness also turns on the claiming process and its transcript,
        # which live outside the board folder, so the page reads again when it changes
        root = board_root()
        report, _ = claim_liveness(root, project_show(root, self.slug))
        return super()._board_stamp(), report and report.status

    def _board_fingerprint(self) -> ProjectDetails:
        # never asks gh: a bad board file must show as a failed read, not sit behind
        # a network call that never runs
        return project_details(board_root(), self.slug, read_prs=_pulls_later)

    def _read_board(self) -> None:
        """The mixin's read and, when it took a new board (its stamp moved), the header's
        lines again. They read cards and planning threads that the page's own text does
        not show, so `_board_changed` does not run for a change in them alone."""
        before = self._live_stamp
        super()._read_board()
        if self._live_stamp != before:
            self.query_one(AppHeader).refresh_status()

    def _board_changed(self) -> None:
        details = self._live_fingerprint
        self.title = details.project.name
        if self._pulls_details is not None:
            details = replace(
                details,
                pulls=self._pulls_details.pulls,
                pulls_error=self._pulls_details.pulls_error,
            )
            self.query_one("#project-details", ReadingView).update(
                format_project(details, icons=self.app.icons)
            )
            return
        self.query_one("#project-details", ReadingView).update(
            format_project(details, pulls_loading=True, icons=self.app.icons)
        )
        if not self._pulls_requested:
            self._pulls_requested = True
            self.run_worker(self._read_details, thread=True)

    def _read_details(self) -> ProjectDetails:
        """Runs in the worker's thread. `gh_open_prs` is named here, not left to
        `project_details`' own default, so a test can swap in a fake reader by
        replacing this module's `gh_open_prs`."""
        return project_details(board_root(), self.slug, read_prs=gh_open_prs)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.state is WorkerState.SUCCESS:
            self._pulls_details = event.worker.result
            self.query_one("#project-details", ReadingView).update(
                format_project(self._pulls_details, icons=self.app.icons)
            )
