"""Modal dialog: ConfigPicker -- pick a config to load or edit, or create a new one.

Extracted from the original monolithic ``dialogs.py``.  See
``termapy.dialogs.__init__`` for the package-level public API and
the ``_common`` submodule for shared constants and helpers.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, OptionList, Static

from termapy.config import (
    CONFIG_GLOB,
    cfg_dir,
    config_summary,
    file_manager_name,
    is_config_file,
)
from termapy.dialogs._common import (
    _DISMISS_BINDINGS,
    _FILE_PICKER_WIDTH,
    _MODAL_BTN_CSS,
    _NO_DISPLAY_TIP,
    FileOptionList,
    PathLink,
    _highlighted_file,
    _path_link_tooltip,
    _title_row_width,
)
from termapy.folder_ops import list_entries

if TYPE_CHECKING:
    from termapy.port_control import PortSource


def _config_info(path: Path) -> tuple[str, str, str]:
    """``("A1B2C3D4", "115200", "Bench board")`` for a picker row: the file's facts.

    The facts are ``config.config_summary``'s, the ones ``/cfg.list``
    shows, the port as the file spells it; all empty if the file won't
    load.  ``_config_details`` turns the port into the device it names.
    """
    summary = config_summary(path)
    baud = summary["baud_rate"]
    return summary["port"], "" if baud is None else str(baud), summary["title"]


def _config_details(
    paths: list[Path],
    *,
    active: Path | None = None,
    open_device: str = "",
    port_source: PortSource | None = None,
) -> tuple[dict[Path, str], str]:
    """Per-config detail text plus its header, one ``load_config`` per file.

    Three columns -- ``PORT`` (left-aligned, padded to the widest port so a
    macOS device name and ``COM4`` share a column), ``BAUD`` (right-aligned,
    it is a number), ``TITLE`` -- with the header padded the same way so
    it sits over them.  ``PORT`` is the device each config's port names,
    never its serial number or fallback chain as the file spells it
    (``port_control.listing_devices``, one enumeration for the whole list;
    registry ``port-display``); ``active`` shows ``open_device`` when the
    session holds one.
    """
    from termapy import port_control

    infos = {path: _config_info(path) for path in paths}
    devices = port_control.listing_devices(
        {str(path): port for path, (port, _, _) in infos.items()},
        active=None if active is None else str(active),
        open_device=open_device,
        source=port_source,
    )
    infos = {path: (devices[str(path)], baud, title) for path, (_, baud, title) in infos.items()}
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


_TITLE = "Select Config"


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
    #picker-header {{ height: 1; }}
    #picker-title {{ width: auto; text-style: bold; }}
    #picker-list {{ height: 1fr; border: thick $primary; }}
    #picker-buttons {{ height: 1; align: right middle; }}
    """

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q or Escape."""
        self.dismiss(None)

    def on_mount(self) -> None:
        self.query_one(
            "#picker-list", OptionList
        ).tooltip = "Configs found under termapy_cfg/.  Press Enter to load."
        # No path here: the header row already shows it in full, and
        # repeating it in a tooltip is the clutter this replaced.
        self.query_one("#picker-cfgdir", PathLink).tooltip = _path_link_tooltip(
            gui_apps=self.gui_apps,
            action=f"Open this folder in {self._manager}.",
        )
        self.query_one("#picker-load", Button).tooltip = "Load and activate the selected config."
        self.query_one("#picker-edit", Button).tooltip = "Open the selected config in the editor."
        # Disabled when this session has no desktop the user sees, with the
        # override named so the user can fix a wrong guess (mosh, tmux
        # inside SSH, WSLg) or turn it on at the serving machine.
        self.query_one("#picker-explore", Button).tooltip = (
            f"Open the selected config's folder in {self._manager}."
            if self.gui_apps
            else _NO_DISPLAY_TIP
        )
        self.query_one("#picker-new", Button).tooltip = "Create a new config."
        self.query_one(
            "#picker-rename", Button
        ).tooltip = "Rename the selected config (its folder and .cfg move together)."
        self.query_one(
            "#picker-delete", Button
        ).tooltip = "Delete the selected config (asks for confirmation)."
        self.query_one("#picker-cancel", Button).tooltip = "Close without loading or editing."

    def __init__(
        self,
        current_path: str = "",
        read_only: bool = False,
        *,
        gui_apps: bool = True,
        opener: Callable[[str], None] | None = None,
        open_device: str = "",
        port_source: PortSource | None = None,
    ) -> None:
        """Build the picker.

        Args:
            current_path: The active config, highlighted on open.
            read_only: Disable Edit / Rename / Delete.
            open_device: The device this session has open, ``""`` when none;
                the active config's PORT cell shows it (registry
                ``port-display``).
            port_source: A substitute port list for the PORT column's
                resolution, as ``port_control.resolve_port`` takes it;
                injected so a test can show a serial number's device
                without owning the machine's ports.
            gui_apps: Whether this session can show a desktop app the user
                will see (``ctx.capabilities.gui_apps``: SSH- and
                browser-aware).  False disables Explore and the folder link
                with a tooltip naming the ``TERMAPY_GUI`` override.
            opener: What Explore and the folder link call with a folder: the
                host's gated ``ctx.fs.open_file``; ``None`` leaves both inert.
                Injected so a test can prove a press reaches it without a
                file manager appearing.
        """
        super().__init__()
        self.current_path = current_path
        self.read_only = read_only
        self.gui_apps = gui_apps
        self._opener = opener
        self._open_device = open_device
        self._port_source = port_source
        # "Explorer", "Finder", "Files"...: the button is the app's own name,
        # because a verb like Explore reads as Windows to everyone else.
        self._manager = file_manager_name()

    def compose(self) -> ComposeResult:
        d = cfg_dir()
        json_files = [  # newest first
            path for path in list_entries(d, CONFIG_GLOB) if is_config_file(path)
        ]
        active = next((path for path in json_files if str(path) == self.current_path), None)
        details, detail_header = _config_details(
            json_files,
            active=active,
            open_device=self._open_device,
            port_source=self._port_source,
        )
        with Vertical(id="picker-dialog") as dialog:
            with Horizontal(id="picker-header"):
                yield Static(_TITLE, id="picker-title")
                # Display and open the ABSOLUTE folder; the listing above
                # keeps the path shape cfg_dir() gave it, so what gets
                # stored as a config path is unchanged.
                # Live only where a file manager reaches the user, the
                # Explore button's rule.
                yield PathLink(
                    d.resolve(),
                    len(str(d.resolve())),
                    link_id="picker-cfgdir",
                    opener=self._opener if self.gui_apps else None,
                )
            ol = FileOptionList(
                json_files,
                detail=details.__getitem__,
                detail_header=detail_header,
                # Show the stem (the config's name), padded like the filename
                # column so size/age still line up.
                label=lambda path, padded: f"{path.stem:<{len(padded)}}",
                id="picker-list",
            )
            current_idx = next(
                (i for i, path in enumerate(json_files) if str(path) == self.current_path),
                0,
            )
            ol.highlighted = ol.first_file + current_idx
            dialog.styles.width = max(ol.dialog_width(), _title_row_width(_TITLE, [d.resolve()]))
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
                # The header row cannot be highlighted, so whenever configs
                # exist one is selected: "a folder is selected" is the same
                # condition Load uses.
                explore_btn = Button(
                    Text(self._manager),
                    id="picker-explore",
                    disabled=not has_configs or not self.gui_apps,
                )
                explore_btn.styles.background = "slategray"
                yield explore_btn
                new_btn = Button("New", id="picker-new")
                new_btn.styles.background = "darkorchid"
                yield new_btn
                rename_btn = Button(
                    "Rename",
                    id="picker-rename",
                    disabled=not has_configs or self.read_only,
                )
                rename_btn.styles.background = "darkcyan"
                yield rename_btn
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

    @on(Button.Pressed, "#picker-rename")
    def rename_config_btn(self) -> None:
        path = self._selected_path()
        if path:
            self.dismiss(("rename", path))

    @on(Button.Pressed, "#picker-new")
    def new_config(self) -> None:
        self.dismiss(("new",))

    @on(Button.Pressed, "#picker-explore")
    def explore_config(self) -> None:
        """Open the selected config's folder; unlike its neighbours, this does not dismiss."""
        path = self._selected_path()
        if path and self._opener is not None:
            self._opener(str(Path(path).parent))

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
