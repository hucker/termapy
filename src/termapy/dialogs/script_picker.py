"""Modal dialog: ScriptPicker.

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
from termapy.folders import FOLDER_PATTERNS, RUN
from termapy.run_docstring import extract_docstring


class ScriptPicker(ModalScreen[tuple | None]):
    """Modal dialog to pick a script file to run, edit, or create new."""

    BINDINGS = _DISMISS_BINDINGS

    CSS = f"""
    ScriptPicker {{ align: center middle; }}
    ScriptPicker Button {{ {_MODAL_BTN_CSS} }}
    #script-dialog {{
        width: {_FILE_PICKER_WIDTH}; max-width: 100%; height: 18;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #script-title {{ height: 1; text-style: bold; }}
    #script-list {{ height: 1fr; border: thick $primary; }}
    #script-buttons {{ height: 1; align: right middle; }}
    """

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q or Escape."""
        self.dismiss(None)

    def on_mount(self) -> None:
        self.query_one("#script-list", OptionList).tooltip = (
            "Run scripts (.run) in this config's run/ folder.  "
            "Press Enter to run."
        )
        self.query_one("#script-run", Button).tooltip = (
            "Run the selected script."
        )
        self.query_one("#script-edit", Button).tooltip = (
            "Open the selected script in the editor."
        )
        self.query_one("#script-new", Button).tooltip = (
            "Create a new .run script."
        )
        self.query_one("#script-delete", Button).tooltip = (
            "Delete the selected script (asks for confirmation)."
        )
        self.query_one("#script-cancel", Button).tooltip = (
            "Close without running or editing."
        )

    def __init__(self, scripts_dir: Path, read_only: bool = False) -> None:
        """Build the picker for scripts living in ``scripts_dir``.

        Args:
            scripts_dir: Folder to enumerate (usually the cfg's
                ``run/`` directory).
            read_only: When ``True``, hides Edit and Delete so the
                user can still pick a script to Run but cannot
                mutate the on-disk set.
        """
        super().__init__()
        self.scripts_dir = scripts_dir
        self.read_only = read_only

    def compose(self) -> ComposeResult:
        """Build the modal layout: title, file list, action buttons.

        Files are listed newest first with size, age, and docstring
        summary; dotfiles (e.g. ``.cmd_history.txt``) are filtered out
        so they don't appear as runnable scripts.  Run / Edit / Delete
        are disabled when the directory is empty; Edit / Delete are also
        disabled in read-only mode.
        """
        from textual.widgets import Static

        # Only .run files: the folder can hold editor backups, notes, or --
        # with no config loaded -- be the current directory.
        scripts = list_entries(self.scripts_dir, FOLDER_PATTERNS[RUN])
        with Vertical(id="script-dialog"):
            yield Static("Select Run File", id="script-title")
            ol = OptionList(id="script-list")
            first = _populate_file_option_list(
                ol,
                scripts,
                detail=lambda script: extract_docstring(script)[0],
                detail_header="SUMMARY",
            )
            if scripts:
                ol.highlighted = first
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
        """Return the absolute path of the highlighted entry, or ``None``.

        ``None`` covers both "list is empty" and "nothing highlighted"
        — callers should treat it as "no-op, don't dismiss."
        """
        return _highlighted_file(self.query_one("#script-list", OptionList))

    @on(Button.Pressed, "#script-delete")
    def delete_script(self) -> None:
        """Dismiss with ``("delete", path)`` so the app can prompt + unlink."""
        path = self._selected_path()
        if path:
            self.dismiss(("delete", path))

    @on(Button.Pressed, "#script-new")
    def new_script(self) -> None:
        """Dismiss with ``("new",)`` so the app opens a name-entry dialog."""
        self.dismiss(("new",))

    @on(Button.Pressed, "#script-edit")
    def edit_script(self) -> None:
        """Dismiss with ``("edit", path)`` so the app opens the script in $EDITOR."""
        path = self._selected_path()
        if path:
            self.dismiss(("edit", path))

    @on(Button.Pressed, "#script-run")
    def run_script(self) -> None:
        """Dismiss with ``("run", path)`` so the app executes the script."""
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
        """Dismiss with ``None`` — the app treats this as "no action taken"."""
        self.dismiss(None)
