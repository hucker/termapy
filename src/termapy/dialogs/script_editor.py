"""Modal dialog: ScriptEditor.

Extracted from the original monolithic ``dialogs.py``.  See
``termapy.dialogs.__init__`` for the package-level public API and
the ``_common`` submodule for shared constants and helpers.
"""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, TextArea

from termapy.defaults import SCRIPT_TEMPLATE
from termapy.dialogs._common import _DISMISS_BINDINGS, _MODAL_BTN_CSS


class ScriptEditor(ModalScreen[str | None]):
    """Modal editor for ``.run`` script files (termapy's scripting language).

    The TextArea uses ``language="bash"`` purely for cosmetic syntax
    highlighting (Textual ships no ``run`` lexer; bash is the closest
    fit for ``#`` comments and ``/cmd`` lines).  The files themselves
    are interpreted by termapy's REPL engine, not by bash.
    """

    BINDINGS = _DISMISS_BINDINGS

    CSS = f"""
    ScriptEditor {{ align: center middle; }}
    ScriptEditor Button {{ {_MODAL_BTN_CSS} }}
    #sed-dialog {{
        width: 90%; height: 90%;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #sed-title {{ height: 1; text-style: bold; }}
    #sed-editor {{ height: 1fr; border: thick $primary; }}
    #sed-name-row {{ height: 1; }}
    #sed-name {{ width: 1fr; height: 1; border: none; }}
    #sed-save-as-row {{ height: 1; display: none; }}
    #sed-save-as-row.visible {{ display: block; }}
    #sed-save-as-input {{ width: 1fr; height: 1; border: none; }}
    #sed-error {{ height: 1; color: $error; display: none; }}
    #sed-error.visible {{ display: block; }}
    #sed-buttons {{ height: 1; align: right middle; }}
    """

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q or Escape."""
        self.dismiss(None)

    def on_mount(self) -> None:
        self.query_one("#sed-editor", TextArea).tooltip = (
            "Edit the .run script body."
        )
        self.query_one("#sed-name", Input).tooltip = (
            "Script name (the .run extension is added automatically)."
        )
        self.query_one("#sed-save-as-input", Input).tooltip = (
            "New filename for Save As (without .run)."
        )
        self.query_one("#sed-save", Button).tooltip = (
            "Save the script to disk and close."
        )
        self.query_one("#sed-save-as", Button).tooltip = (
            "Save the script to a new filename."
        )
        self.query_one("#sed-cancel", Button).tooltip = (
            "Discard changes and close."
        )

    def __init__(self, scripts_dir: Path, path: str | None = None) -> None:
        super().__init__()
        self.scripts_dir = scripts_dir
        self.edit_path = path
        self._save_as_mode = False
        self._overwrite_ok = False

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        if self.edit_path:
            name = Path(self.edit_path).stem
            try:
                content = Path(self.edit_path).read_text(encoding="utf-8")
            except OSError:
                content = f"# Error: could not read {self.edit_path}\n"
            title = f"Edit: {Path(self.edit_path).name}"
        else:
            name = ""
            content = SCRIPT_TEMPLATE.format(name="untitled")
            title = "New Script"

        with Vertical(id="sed-dialog"):
            yield Static(title, id="sed-title")
            yield TextArea(
                content,
                language="bash",
                show_line_numbers=True,
                id="sed-editor",
            )
            with Horizontal(id="sed-name-row"):
                yield Input(
                    placeholder="script name (without .run)",
                    value=name,
                    id="sed-name",
                )
            with Horizontal(id="sed-save-as-row"):
                yield Input(
                    placeholder="new filename (without .run)",
                    id="sed-save-as-input",
                )
            yield Static("", id="sed-error")
            with Horizontal(id="sed-buttons"):
                yield Button("Save", id="sed-save", variant="success")
                yield Button("Save As", id="sed-save-as", variant="primary")
                yield Button("Cancel", id="sed-cancel", variant="error")

    def _show_error(self, msg: str) -> None:
        from textual.widgets import Static

        err = self.query_one("#sed-error", Static)
        err.update(msg)
        err.add_class("visible")

    @on(Button.Pressed, "#sed-save")
    def save_script(self) -> None:
        if self._save_as_mode:
            self._do_save_as()
            return
        name = self.query_one("#sed-name", Input).value.strip()
        if not name:
            self._show_error("Enter a script name")
            return
        if not name.endswith(".run"):
            name += ".run"
        content = self.query_one("#sed-editor", TextArea).text
        path = self.scripts_dir / name
        path.write_text(content, encoding="utf-8")
        self.dismiss(str(path))

    @on(Button.Pressed, "#sed-save-as")
    def save_as_script(self) -> None:
        self._save_as_mode = True
        self._overwrite_ok = False
        self.query_one("#sed-save-as-row").add_class("visible")
        self.query_one("#sed-save-as").display = False
        self.query_one("#sed-save-as-input", Input).focus()

    @on(Input.Submitted, "#sed-save-as-input")
    def save_as_on_enter(self) -> None:
        self._do_save_as()

    def _do_save_as(self) -> None:
        name = self.query_one("#sed-save-as-input", Input).value.strip()
        if not name:
            self._show_error("Enter a filename")
            return
        if not name.endswith(".run"):
            name += ".run"
        path = self.scripts_dir / name
        if path.exists() and not self._overwrite_ok:
            self._show_error(f"{name} exists - click Save again to overwrite")
            self._overwrite_ok = True
            return
        content = self.query_one("#sed-editor", TextArea).text
        path.write_text(content, encoding="utf-8")
        self.dismiss(str(path))

    @on(Input.Submitted, "#sed-name")
    def save_on_enter(self) -> None:
        self.save_script()

    @on(Button.Pressed, "#sed-cancel")
    def cancel_editor(self) -> None:
        self.dismiss(None)
