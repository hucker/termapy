"""Modal dialog: ConfigPicker.

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

from termapy.config import (
    cfg_dir,
    load_config,
    migrate_json_to_cfg,
)
from termapy.dialogs._common import (
    _DISMISS_BINDINGS,
    _FILE_PICKER_WIDTH,
    _MODAL_BTN_CSS,
    _highlighted_file,
    _populate_file_option_list,
)
from termapy.folder_ops import list_entries


def _config_info(path: Path) -> tuple[str, str, str]:
    """``("COM4", "115200", "Bench board")`` for a picker row.

    All empty if the file won't load -- a broken cfg still deserves a
    row (so it can be edited or deleted), just not a crash.
    """
    try:
        cfg = load_config(str(path))
    except Exception:  # noqa: BLE001 - see docstring
        return "", "", ""
    serial = cfg.get("serial", {})
    port = str(serial.get("port") or "")
    baud = str(serial.get("baud_rate") or "")
    title = str(cfg.get("title") or "")
    # New configs are written with the config's own name as the title;
    # that repeats the first column, so treat it as "no title given".
    if title.strip().lower() == path.stem.lower():
        title = ""
    return port, baud, title


def _config_details(paths: list[Path]) -> tuple[dict[Path, str], str]:
    """Per-config detail text plus its header, one ``load_config`` per file.

    Three columns -- ``PORT`` (left-aligned, padded to the widest port so a
    macOS device name and ``COM4`` share a column), ``BAUD`` (right-aligned,
    it is a number), ``TITLE`` -- with the header padded the same way so
    it sits over them.
    """
    infos = {path: _config_info(path) for path in paths}
    port_width = max((len(port) for port, _, _ in infos.values()), default=0)
    port_width = max(port_width, len("PORT"))
    baud_width = max((len(baud) for _, baud, _ in infos.values()), default=0)
    baud_width = max(baud_width, len("BAUD"))
    details = {
        path: f"{port:<{port_width}}  {baud:>{baud_width}}  {title}".rstrip()
        for path, (port, baud, title) in infos.items()
    }
    # Headers are all left-aligned (see _populate_file_option_list), even
    # over the right-aligned baud numbers.
    header = f"{'PORT':<{port_width}}  {'BAUD':<{baud_width}}  TITLE"
    return details, header


class ConfigPicker(ModalScreen[tuple | None]):
    """Modal dialog to select a config file: load, edit, or create new."""

    BINDINGS = _DISMISS_BINDINGS

    CSS = f"""
    ConfigPicker {{ align: center middle; }}
    ConfigPicker Button {{ {_MODAL_BTN_CSS} }}
    #picker-dialog {{
        width: {_FILE_PICKER_WIDTH}; max-width: 100%; height: 18;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #picker-title {{ height: 1; text-style: bold; }}
    #picker-list {{ height: 1fr; border: thick $primary; }}
    #picker-buttons {{ height: 1; align: right middle; }}
    """

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q or Escape."""
        self.dismiss(None)

    def on_mount(self) -> None:
        self.query_one("#picker-list", OptionList).tooltip = (
            "Configs found under termapy_cfg/.  Press Enter to load."
        )
        self.query_one("#picker-load", Button).tooltip = (
            "Load and activate the selected config."
        )
        self.query_one("#picker-edit", Button).tooltip = (
            "Open the selected config in the editor."
        )
        self.query_one("#picker-new", Button).tooltip = (
            "Create a new config."
        )
        self.query_one("#picker-delete", Button).tooltip = (
            "Delete the selected config (asks for confirmation)."
        )
        self.query_one("#picker-cancel", Button).tooltip = (
            "Close without loading or editing."
        )

    def __init__(self, current_path: str = "", read_only: bool = False) -> None:
        super().__init__()
        self.current_path = current_path
        self.read_only = read_only

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        d = cfg_dir()
        migrate_json_to_cfg(d)
        json_files = list_entries(d, "*/*.cfg")  # newest first
        details, detail_header = _config_details(json_files)
        with Vertical(id="picker-dialog"):
            yield Static("Select Config", id="picker-title")
            ol = OptionList(id="picker-list")
            first = _populate_file_option_list(
                ol,
                json_files,
                detail=details.__getitem__,
                detail_header=detail_header,
                # Show the stem (the config's name), padded like the filename
                # column so size/age still line up.
                label=lambda path, padded: f"{path.stem:<{len(padded)}}",
            )
            current_idx = next(
                (i for i, path in enumerate(json_files) if str(path) == self.current_path),
                0,
            )
            ol.highlighted = first + current_idx
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
        return _highlighted_file(self.query_one("#picker-list", OptionList))

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
