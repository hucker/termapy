"""Modal dialog: ConfigPicker.

Extracted from the original monolithic ``dialogs.py``.  See
``termapy.dialogs.__init__`` for the package-level public API and
the ``_common`` submodule for shared constants and helpers.
"""

from __future__ import annotations


from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, OptionList
from textual.widgets.option_list import Option

from termapy.config import (
    cfg_dir,
    migrate_json_to_cfg,
)
from termapy.dialogs._common import _DISMISS_BINDINGS, _MODAL_BTN_CSS


class ConfigPicker(ModalScreen[tuple | None]):
    """Modal dialog to select a config file: load, edit, or create new."""

    BINDINGS = _DISMISS_BINDINGS

    CSS = f"""
    ConfigPicker {{ align: center middle; }}
    ConfigPicker Button {{ {_MODAL_BTN_CSS} }}
    #picker-dialog {{
        width: 50; height: 18;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #picker-title {{ height: 1; text-style: bold; }}
    #picker-list {{ height: 1fr; border: thick $primary; }}
    #picker-buttons {{ height: 1; align: right middle; }}
    """

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q or Escape."""
        self.dismiss(None)

    def __init__(self, current_path: str = "", read_only: bool = False) -> None:
        super().__init__()
        self.current_path = current_path
        self.read_only = read_only

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        d = cfg_dir()
        migrate_json_to_cfg(d)
        json_files = sorted(f for f in d.glob("*/*.cfg") if not f.name.startswith("."))
        with Vertical(id="picker-dialog"):
            yield Static("Select Config", id="picker-title")
            ol = OptionList(id="picker-list")
            highlight_idx = None
            for i, f in enumerate(json_files):
                ol.add_option(Option(f.stem, id=str(f)))
                if str(f) == self.current_path:
                    highlight_idx = i
            ol.highlighted = highlight_idx if highlight_idx is not None else 0
            yield ol
            has_configs = bool(json_files)
            with Horizontal(id="picker-buttons"):
                yield Button(
                    "Load",
                    id="picker-load",
                    variant="success",
                    disabled=not has_configs,
                )
                yield Button(
                    "Edit",
                    id="picker-edit",
                    variant="primary",
                    disabled=not has_configs or self.read_only,
                )
                new_btn = Button("New", id="picker-new")
                new_btn.styles.background = "darkorchid"
                yield new_btn
                yield Button(
                    "Delete",
                    id="picker-delete",
                    variant="warning",
                    disabled=not has_configs or self.read_only,
                )
                yield Button("Cancel", id="picker-cancel", variant="error")

    def _selected_path(self) -> str | None:
        ol = self.query_one("#picker-list", OptionList)
        if ol.highlighted is not None:
            return str(ol.get_option_at_index(ol.highlighted).id)
        return None

    @on(Button.Pressed, "#picker-delete")
    def delete_config(self) -> None:
        path = self._selected_path()
        if path:
            self.dismiss(("delete", path))

    @on(Button.Pressed, "#picker-new")
    def new_config(self) -> None:
        self.dismiss(("new",))

    @on(Button.Pressed, "#picker-edit")
    def edit_config(self) -> None:
        path = self._selected_path()
        if path:
            self.dismiss(("edit", path))

    @on(Button.Pressed, "#picker-load")
    def load_config_btn(self) -> None:
        path = self._selected_path()
        if path:
            self.dismiss(("load", path))

    def on_key(self, event: events.Key) -> None:
        """Load the highlighted config when Enter is pressed in the list."""
        if event.key != "enter":
            return
        if not isinstance(self.focused, OptionList):
            return
        event.prevent_default()
        event.stop()
        path = self._selected_path()
        if path:
            self.dismiss(("load", path))

    @on(Button.Pressed, "#picker-cancel")
    def cancel_picker(self) -> None:
        self.dismiss(None)
