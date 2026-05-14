"""Modal dialog: MarkdownViewer.

Extracted from the original monolithic ``dialogs.py``.  See
``termapy.dialogs.__init__`` for the package-level public API and
the ``_common`` submodule for shared constants and helpers.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from rich.errors import MarkupError
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, TextArea
from textual.widgets.option_list import Option

from termapy.config import (
    cfg_dir,
    cfg_path_for_name,
    migrate_json_to_cfg,
    open_with_system,
)
from termapy.defaults import DEFAULT_CFG, PROTO_TEMPLATE, SCRIPT_TEMPLATE
from termapy.dialogs._common import _DISMISS_BINDINGS, _MODAL_BTN_CSS


class MarkdownViewer(ModalScreen[None]):
    """Modal dialog to display a markdown file with an option to open externally."""

    BINDINGS = _DISMISS_BINDINGS

    CSS = f"""
    MarkdownViewer {{ align: center middle; }}
    MarkdownViewer Button {{ {_MODAL_BTN_CSS} }}
    #mdv-dialog {{
        width: 90%; height: 90%;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #mdv-title {{ height: 1; text-style: bold; }}
    #mdv-content {{ height: 1fr; border: thick $primary; }}
    #mdv-buttons {{ height: 1; align: right middle; }}
    """

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q or Escape."""
        self.dismiss(None)

    def __init__(self, content: str, title: str = "", file_path: str = "") -> None:
        """Init markdown viewer.

        Args:
            content: Markdown text to display.
            title: Title shown at the top of the dialog.
            file_path: Path to the file on disk (for "Open" button).
        """
        super().__init__()
        self._md_content = content
        self._title = title
        self._file_path = file_path

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        with Vertical(id="mdv-dialog"):
            if self._title:
                yield Static(self._title, id="mdv-title")
            ta = TextArea(self._md_content, read_only=True, id="mdv-content")
            ta.soft_wrap = True
            yield ta
            with Horizontal(id="mdv-buttons"):
                if self._file_path:
                    yield Button("Open", id="mdv-open")
                yield Button("Close", id="mdv-close", variant="primary")

    @on(Button.Pressed, "#mdv-open")
    def open_external(self) -> None:
        """Open the file with the system default application."""
        if self._file_path:
            open_with_system(self._file_path)

    @on(Button.Pressed, "#mdv-close")
    def close_viewer(self) -> None:
        """Close the dialog."""
        self.dismiss(None)
