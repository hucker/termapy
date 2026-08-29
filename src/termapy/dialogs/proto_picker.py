"""Modal dialog: ProtoPicker.

Extracted from the original monolithic ``dialogs.py``.  See
``termapy.dialogs.__init__`` for the package-level public API and
the ``_common`` submodule for shared constants and helpers.
"""

from __future__ import annotations

from pathlib import Path

from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, OptionList

from termapy.dialogs._common import (
    _DISMISS_BINDINGS,
    _FILE_PICKER_WIDTH,
    _MODAL_BTN_CSS,
    _highlighted_file,
    _populate_file_option_list,
)
from termapy.folder_ops import list_entries


class ProtoPicker(ModalScreen[tuple | None]):
    """Modal dialog to pick a .pro protocol script to run, edit, or create new."""

    BINDINGS = _DISMISS_BINDINGS

    CSS = f"""
    ProtoPicker {{ align: center middle; }}
    ProtoPicker Button {{ {_MODAL_BTN_CSS} }}
    #proto-dialog {{
        width: {_FILE_PICKER_WIDTH}; max-width: 100%; height: 18;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #proto-title {{ height: 1; text-style: bold; }}
    #proto-list {{ height: 1fr; border: thick $primary; }}
    #proto-buttons {{ height: 1; align: right middle; }}
    """

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q or Escape."""
        self.dismiss(None)

    def on_mount(self) -> None:
        self.query_one("#proto-list", OptionList).tooltip = (
            "Protocol scripts (.pro) in this config's proto/ folder.  "
            "Press Enter to run."
        )
        self.query_one("#proto-run", Button).tooltip = (
            "Run the selected protocol script."
        )
        self.query_one("#proto-debug", Button).tooltip = (
            "Run the selected script with debug tracing (TX/RX bytes shown)."
        )
        self.query_one("#proto-edit", Button).tooltip = (
            "Open the selected protocol script in the editor."
        )
        self.query_one("#proto-new", Button).tooltip = (
            "Create a new protocol script."
        )
        self.query_one("#proto-rename", Button).tooltip = (
            "Rename the selected protocol script."
        )
        self.query_one("#proto-delete", Button).tooltip = (
            "Delete the selected script (asks for confirmation)."
        )
        self.query_one("#proto-cancel", Button).tooltip = (
            "Close without running or editing."
        )

    def __init__(self, proto_dir: Path, read_only: bool = False) -> None:
        super().__init__()
        self.proto_dir = proto_dir
        self.read_only = read_only

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        protos = list_entries(self.proto_dir, "*.pro")  # newest first
        with Vertical(id="proto-dialog"):
            yield Static("Select Protocol Script", id="proto-title")
            ol = OptionList(id="proto-list")
            first = _populate_file_option_list(ol, protos)
            if protos:
                ol.highlighted = first
            yield ol
            has_protos = bool(protos)
            with Horizontal(id="proto-buttons"):
                yield Button(
                    "Run", id="proto-run", variant="success", disabled=not has_protos
                )
                yield Button(
                    "Debug",
                    id="proto-debug",
                    variant="warning",
                    disabled=not has_protos,
                )
                yield Button(
                    "Edit",
                    id="proto-edit",
                    variant="primary",
                    disabled=not has_protos or self.read_only,
                )
                new_btn = Button("New", id="proto-new")
                new_btn.styles.background = "darkorchid"
                yield new_btn
                rename_btn = Button(
                    "Rename",
                    id="proto-rename",
                    disabled=not has_protos or self.read_only,
                )
                rename_btn.styles.background = "darkcyan"
                yield rename_btn
                yield Button(
                    "Delete",
                    id="proto-delete",
                    variant="warning",
                    disabled=not has_protos or self.read_only,
                )
                yield Button("Cancel", id="proto-cancel", variant="error")

    def _selected_path(self) -> str | None:
        """Return the path of the currently highlighted option.

        Returns:
            Absolute path string, or None if nothing is highlighted.
        """
        return _highlighted_file(self.query_one("#proto-list", OptionList))

    @on(Button.Pressed, "#proto-delete")
    def delete_proto(self) -> None:
        path = self._selected_path()
        if path:
            self.dismiss(("delete", path))

    @on(Button.Pressed, "#proto-rename")
    def rename_proto(self) -> None:
        path = self._selected_path()
        if path:
            self.dismiss(("rename", path))

    @on(Button.Pressed, "#proto-new")
    def new_proto(self) -> None:
        self.dismiss(("new",))

    @on(Button.Pressed, "#proto-edit")
    def edit_proto(self) -> None:
        path = self._selected_path()
        if path:
            self.dismiss(("edit", path))

    @on(Button.Pressed, "#proto-run")
    def run_proto(self) -> None:
        path = self._selected_path()
        if path:
            self.dismiss(("run", path))

    @on(Button.Pressed, "#proto-debug")
    def debug_proto(self) -> None:
        path = self._selected_path()
        if path:
            self.dismiss(("debug", path))

    def on_key(self, event: events.Key) -> None:
        """Run the highlighted proto script when Enter is pressed in the list."""
        if event.key != "enter":
            return
        if not isinstance(self.focused, OptionList):
            return
        event.prevent_default()
        event.stop()
        path = self._selected_path()
        if path:
            self.dismiss(("run", path))

    @on(Button.Pressed, "#proto-cancel")
    def cancel_picker(self) -> None:
        self.dismiss(None)
