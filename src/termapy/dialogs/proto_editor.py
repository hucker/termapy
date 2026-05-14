"""Modal dialog: ProtoEditor.

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

from termapy.defaults import PROTO_TEMPLATE
from termapy.dialogs._common import _DISMISS_BINDINGS, _MODAL_BTN_CSS


class ProtoEditor(ModalScreen[str | None]):
    """Modal editor for .pro protocol script files with TOML highlighting."""

    BINDINGS = _DISMISS_BINDINGS

    CSS = f"""
    ProtoEditor {{ align: center middle; }}
    ProtoEditor Button {{ {_MODAL_BTN_CSS} }}
    #ped-dialog {{
        width: 90%; height: 90%;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #ped-title {{ height: 1; text-style: bold; }}
    #ped-editor {{ height: 1fr; border: thick $primary; }}
    #ped-name-row {{ height: 1; }}
    #ped-name {{ width: 1fr; height: 1; border: none; }}
    #ped-save-as-row {{ height: 1; display: none; }}
    #ped-save-as-row.visible {{ display: block; }}
    #ped-save-as-input {{ width: 1fr; height: 1; border: none; }}
    #ped-error {{ height: 1; color: $error; display: none; }}
    #ped-error.visible {{ display: block; }}
    #ped-buttons {{ height: 1; align: right middle; }}
    """

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q or Escape."""
        self.dismiss(None)

    def __init__(self, proto_dir: Path, path: str | None = None) -> None:
        super().__init__()
        self.proto_dir = proto_dir
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
            content = PROTO_TEMPLATE
            title = "New Protocol Script"

        with Vertical(id="ped-dialog"):
            yield Static(title, id="ped-title")
            yield TextArea(
                content,
                language="toml",
                show_line_numbers=True,
                id="ped-editor",
            )
            with Horizontal(id="ped-name-row"):
                yield Input(
                    placeholder="script name (without .pro)",
                    value=name,
                    id="ped-name",
                )
            with Horizontal(id="ped-save-as-row"):
                yield Input(
                    placeholder="new filename (without .pro)",
                    id="ped-save-as-input",
                )
            yield Static("", id="ped-error")
            with Horizontal(id="ped-buttons"):
                yield Button("Save", id="ped-save", variant="success")
                yield Button("Save As", id="ped-save-as", variant="primary")
                yield Button("Cancel", id="ped-cancel", variant="error")

    def _show_error(self, msg: str) -> None:
        """Display an error message in the editor.

        Args:
            msg: Error text to show.
        """
        from textual.widgets import Static

        err = self.query_one("#ped-error", Static)
        err.update(msg)
        err.add_class("visible")

    @on(Button.Pressed, "#ped-save")
    def save_proto(self) -> None:
        if self._save_as_mode:
            self._do_save_as()
            return
        name = self.query_one("#ped-name", Input).value.strip()
        if not name:
            self._show_error("Enter a script name")
            return
        if not name.endswith(".pro"):
            name += ".pro"
        content = self.query_one("#ped-editor", TextArea).text
        path = self.proto_dir / name
        path.write_text(content, encoding="utf-8")
        self.dismiss(str(path))

    @on(Button.Pressed, "#ped-save-as")
    def save_as_proto(self) -> None:
        self._save_as_mode = True
        self._overwrite_ok = False
        self.query_one("#ped-save-as-row").add_class("visible")
        self.query_one("#ped-save-as").display = False
        self.query_one("#ped-save-as-input", Input).focus()

    @on(Input.Submitted, "#ped-save-as-input")
    def save_as_on_enter(self) -> None:
        self._do_save_as()

    def _do_save_as(self) -> None:
        name = self.query_one("#ped-save-as-input", Input).value.strip()
        if not name:
            self._show_error("Enter a filename")
            return
        if not name.endswith(".pro"):
            name += ".pro"
        path = self.proto_dir / name
        if path.exists() and not self._overwrite_ok:
            self._show_error(f"{name} exists - click Save again to overwrite")
            self._overwrite_ok = True
            return
        content = self.query_one("#ped-editor", TextArea).text
        path.write_text(content, encoding="utf-8")
        self.dismiss(str(path))

    @on(Input.Submitted, "#ped-name")
    def save_on_enter(self) -> None:
        self.save_proto()

    @on(Button.Pressed, "#ped-cancel")
    def cancel_editor(self) -> None:
        self.dismiss(None)
