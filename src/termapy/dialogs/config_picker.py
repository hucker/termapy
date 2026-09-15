"""Modal dialog: ConfigPicker.

Extracted from the original monolithic ``dialogs.py``.  See
``termapy.dialogs.__init__`` for the package-level public API and
the ``_common`` submodule for shared constants and helpers.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, OptionList, Static

from termapy.config import (
    cfg_dir,
    load_config,
    migrate_json_to_cfg,
    open_with_system,
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


_TITLE = "Select Config"


def _fit_path(folder: Path, width: int) -> str:
    """``folder`` as text, shortened from the FRONT when it exceeds ``width``.

    CSS ellipsis truncates at the end, which would drop the tail that
    actually identifies the folder.  Leading segments are replaced with
    ``...`` instead, so ``.../termapy/termapy_cfg`` survives.  Only the
    DISPLAY is shortened; a click still opens the full path.

    Args:
        folder: The path to render.
        width: Columns available to the path.

    Returns:
        The full path, or a front-elided form that fits.
    """
    text = str(folder)
    if len(text) <= width:
        return text
    parts = list(folder.parts)
    while len(parts) > 1:
        parts.pop(0)
        candidate = "..." + os.sep + os.sep.join(parts)
        if len(candidate) <= width:
            return candidate
    return text[-width:]


class CfgDirLink(Static):
    """The cfg-dir path in the title row; clicking opens it in the file explorer.

    A widget rather than a markup ``@click`` action so the behaviour does
    not depend on Textual's action-resolution path, and a subclass rather
    than a handler on the screen so the click target is unambiguous.
    ``open_with_system`` is the same cross-platform launcher ``/ss.explore``
    and the log buttons use.
    """

    def __init__(
        self,
        folder: Path,
        width: int,
        *,
        opener: Callable[[str], None] = open_with_system,
    ) -> None:
        """Build the link.

        Args:
            folder: The folder to display and to open on click.
            width: Columns available for the path text.
            opener: What a click calls.  Injected so a test can prove the
                click actually reaches this handler without a file manager
                appearing -- the ``source=`` seam used elsewhere in the
                codebase, not a patched global.
        """
        super().__init__(_fit_path(folder, width), id="picker-cfgdir")
        self._folder = folder
        self._opener = opener

    def on_click(self) -> None:
        """Open the config folder with the system file manager."""
        self._opener(str(self._folder))


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
    /* Fills the rest of the header row.  Ellipsis truncates at the END,
       which would hide the identifying tail, so a path too long for the
       dialog is shortened in Python (_fit_path) before it gets here. */
    #picker-cfgdir {{
        width: 1fr; text-align: right; color: $text-muted;
        text-style: underline; text-overflow: ellipsis;
    }}
    #picker-cfgdir:hover {{ color: $accent; }}
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
        # No path here: the header row already shows it in full, and
        # repeating it in a tooltip is the clutter this replaced.
        self.query_one("#picker-cfgdir", Static).tooltip = (
            "Open this folder in the file explorer."
        )
        self.query_one("#picker-load", Button).tooltip = (
            "Load and activate the selected config."
        )
        self.query_one("#picker-edit", Button).tooltip = (
            "Open the selected config in the editor."
        )
        # Absent under --web (see __init__); disabled when this process has
        # no display to reach, with the override named so the user can fix
        # a wrong guess (mosh, tmux inside SSH, WSLg).
        explore = self.query("#picker-explore")
        if explore:
            explore.first(Button).tooltip = (
                "Open the selected config's folder in the system file explorer."
                if self.gui_apps
                else "Not available in this environment: no display this process "
                "can reach.  Set TERMAPY_GUI=1 to override the detection."
            )
        self.query_one("#picker-new", Button).tooltip = (
            "Create a new config."
        )
        self.query_one("#picker-rename", Button).tooltip = (
            "Rename the selected config (its folder and .cfg move together)."
        )
        self.query_one("#picker-delete", Button).tooltip = (
            "Delete the selected config (asks for confirmation)."
        )
        self.query_one("#picker-cancel", Button).tooltip = (
            "Close without loading or editing."
        )

    def __init__(
        self,
        current_path: str = "",
        read_only: bool = False,
        *,
        gui_apps: bool = True,
        web: bool = False,
        opener: Callable[[str], None] = open_with_system,
    ) -> None:
        """Build the picker.

        Args:
            current_path: The active config, highlighted on open.
            read_only: Disable Edit / Rename / Delete.
            gui_apps: Whether this process can show a desktop app the user
                will see (``ctx.capabilities.gui_apps``, SSH-aware).  False
                disables Explore with a tooltip naming the ``TERMAPY_GUI``
                override, since that case is fixable.
            web: Served in a browser (``App.is_web``).  A folder would open
                on the SERVER's desktop, never the viewer's, so Explore is
                not offered at all rather than disabled.
            opener: What Explore calls; injected so a test can prove the
                press reaches it without a file manager appearing.
        """
        super().__init__()
        self.current_path = current_path
        self.read_only = read_only
        self.gui_apps = gui_apps
        self.web = web
        self._opener = opener

    def compose(self) -> ComposeResult:
        d = cfg_dir()
        migrate_json_to_cfg(d)
        json_files = list_entries(d, "*/*.cfg")  # newest first
        details, detail_header = _config_details(json_files)
        with Vertical(id="picker-dialog"):
            with Horizontal(id="picker-header"):
                yield Static(_TITLE, id="picker-title")
                # Display and open the ABSOLUTE folder; the listing above
                # keeps the path shape cfg_dir() gave it, so what gets
                # stored as a config path is unchanged.
                yield CfgDirLink(d.resolve(), _FILE_PICKER_WIDTH - len(_TITLE) - 6)
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
                if not self.web:
                    # The header row cannot be highlighted, so whenever
                    # configs exist one is selected: "a folder is selected"
                    # is the same condition Load uses.
                    explore_btn = Button(
                        "Explore",
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
        if path:
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
