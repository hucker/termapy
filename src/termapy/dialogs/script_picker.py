"""Modal dialog: ScriptPicker.

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


class ScriptPicker(ModalScreen[tuple | None]):
    """Modal dialog to pick a script file to run, edit, or create new."""

    BINDINGS = _DISMISS_BINDINGS

    CSS = f"""
    ScriptPicker {{ align: center middle; }}
    ScriptPicker Button {{ {_MODAL_BTN_CSS} }}
    #script-dialog {{
        width: 50; height: 18;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #script-title {{ height: 1; text-style: bold; }}
    #script-list {{ height: 1fr; border: thick $primary; }}
    #script-buttons {{ height: 1; align: right middle; }}
    """

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q or Escape."""
        self.dismiss(None)

    def __init__(self, scripts_dir: Path, read_only: bool = False) -> None:
        super().__init__()
        self.scripts_dir = scripts_dir
        self.read_only = read_only

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        scripts = sorted(self.scripts_dir.glob("*"))
        scripts = [f for f in scripts if f.is_file() and not f.name.startswith(".")]
        with Vertical(id="script-dialog"):
            yield Static("Select Script", id="script-title")
            ol = OptionList(id="script-list")
            for f in scripts:
                ol.add_option(Option(f.name, id=str(f)))
            if scripts:
                ol.highlighted = 0
            yield ol
            has_scripts = bool(scripts)
            with Horizontal(id="script-buttons"):
                yield Button(
                    "Run", id="script-run", variant="success", disabled=not has_scripts
                )
                yield Button(
                    "Edit",
                    id="script-edit",
                    variant="primary",
                    disabled=not has_scripts or self.read_only,
                )
                new_btn = Button("New", id="script-new")
                new_btn.styles.background = "darkorchid"
                yield new_btn
                yield Button(
                    "Delete",
                    id="script-delete",
                    variant="warning",
                    disabled=not has_scripts or self.read_only,
                )
                yield Button("Cancel", id="script-cancel", variant="error")

    def _selected_path(self) -> str | None:
        ol = self.query_one("#script-list", OptionList)
        if ol.highlighted is not None:
            return str(ol.get_option_at_index(ol.highlighted).id)
        return None

    @on(Button.Pressed, "#script-delete")
    def delete_script(self) -> None:
        path = self._selected_path()
        if path:
            self.dismiss(("delete", path))

    @on(Button.Pressed, "#script-new")
    def new_script(self) -> None:
        self.dismiss(("new",))

    @on(Button.Pressed, "#script-edit")
    def edit_script(self) -> None:
        path = self._selected_path()
        if path:
            self.dismiss(("edit", path))

    @on(Button.Pressed, "#script-run")
    def run_script(self) -> None:
        path = self._selected_path()
        if path:
            self.dismiss(("run", path))

    def on_key(self, event: events.Key) -> None:
        """Run the highlighted script when Enter is pressed in the list."""
        if event.key != "enter":
            return
        if not isinstance(self.focused, OptionList):
            return
        event.prevent_default()
        event.stop()
        path = self._selected_path()
        if path:
            self.dismiss(("run", path))

    @on(Button.Pressed, "#script-cancel")
    def cancel_picker(self) -> None:
        self.dismiss(None)
