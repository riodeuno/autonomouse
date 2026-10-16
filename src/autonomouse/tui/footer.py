"""The footer every screen of the viewer ends with (`AppFooter`): the key hints in a
thick frame, the command palette key after them, and at the far right the project's
slug and the autonomouse version.

`AppFooter` is Textual's stock `Footer` with a frame and one label added, so it lists
the same keys the stock footer does, the same way: a key whose action the screen's
`check_action` refuses (returns `False`) is not shown, one it greys out (returns
`None`) is shown dim, a click on a key runs it, and the legend redraws whenever the
screen's bindings change. The stock footer docks the palette key at the far right;
here the palette key follows the other keys, and the right end holds the label.

The key hints keep their room on a narrow terminal. The keys are as wide as their
text, and the label takes only the width left after them, so it is the label that is
cut: the version first, then the slug, with an ellipsis where the cut falls. With no
room left, the label is gone and the keys scroll as the stock footer's do.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Footer, Static

import autonomouse
from autonomouse.tui.look import FRAME


def footer_label(slug: str | None) -> str:
    """`<slug>   autonomouse v<version>`, or the version alone with no slug."""
    version = f"autonomouse v{autonomouse.__version__}"
    return f"{slug}   {version}" if slug else version


class AppFooter(Footer):
    """The stock footer's key hints in sys-w110's frame (`look.FRAME`), then the
    command palette key, then `slug` (when given) and the autonomouse version at the
    far right."""

    DEFAULT_CSS = f"""
    AppFooter {{
        height: 3;
        border: {FRAME.rest};
        background: $background;
        FooterKey.-command-palette {{
            dock: none;
            border-left: none;
            margin-left: 1;
        }}
        .footer-about {{
            width: 1fr;
            min-width: 0;
            height: 1;
            padding: 0 1;
            color: $foreground-muted;
            text-align: right;
            text-wrap: nowrap;
            text-overflow: ellipsis;
        }}
    }}
    """

    def __init__(self, slug: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.slug = slug

    def compose(self) -> ComposeResult:
        yield from super().compose()
        # a class, not an id: while the app shuts down after an uncaught error, the
        # stock footer's recompose mounts a new label before the old one is removed,
        # and a second widget with one id raises a second error (sys-w116)
        yield Static(footer_label(self.slug), classes="footer-about")
