#!/usr/bin/env python3
"""
Usage:
    uv run termapy [config.json]

Runs well in most terminals (Windows Terminal, iTerm2, etc).
VS Code's integrated terminal can be jerky due to its rendering pipeline.
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from threading import Event

import serial
from rich.text import Text
from textual import on, work

from termapy.plugins import EngineAPI, PluginContext, load_plugins_from_dir
from termapy.repl import ReplEngine
from termapy.scripting import parse_duration
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, RichLog, TextArea
from textual.widgets.option_list import Option


CFG_DIR = "termapy_cfg"


def cfg_dir() -> Path:
    """Return the config directory, creating it if needed."""
    d = Path(CFG_DIR)
    d.mkdir(exist_ok=True)
    return d


def cfg_data_dir(config_path: str) -> Path:
    """Return the per-config data directory (for logs, screenshots, etc.).

    Config files live at termapy_cfg/<name>/<name>.json, so the data dir
    is just the parent directory of the config file.
    """
    d = Path(config_path).parent
    d.mkdir(parents=True, exist_ok=True)
    return d


def cfg_path_for_name(name: str) -> Path:
    """Return the config file path for a given name: termapy_cfg/<name>/<name>.json."""
    return cfg_dir() / name / f"{name}.json"


def cfg_log_path(config_path: str) -> str:
    """Return the default log file path for a config."""
    name = Path(config_path).stem + ".txt"
    return str((cfg_data_dir(config_path) / name).resolve())


def cfg_history_path(config_path: str) -> str:
    """Return the command history file path for a config."""
    return str(cfg_data_dir(config_path) / ".cmd_history.txt")


def cfg_plugins_dir(config_path: str) -> Path:
    """Return the plugins directory for a config, creating it if needed."""
    d = cfg_data_dir(config_path) / "plugins"
    d.mkdir(exist_ok=True)
    return d


def global_plugins_dir() -> Path:
    """Return the global plugins directory, creating it if needed."""
    d = cfg_dir() / "plugins"
    d.mkdir(exist_ok=True)
    return d


# Regex to strip ANSI escape sequences for plain-text logging
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
# ANSI clear screen sequence (with optional cursor-home prefix)
CLEAR_SCREEN_RE = re.compile(r"(\x1b\[H)?\x1b\[2J")
# Incomplete ANSI escape at end of buffer: ESC, or ESC[ with optional digits/semicolons
PARTIAL_ANSI_RE = re.compile(r"\x1b(\[[0-9;]*)?$")


DEFAULT_CFG = {
    # App
    "title": "",
    "app_border_color": "",
    "max_lines": 10000,
    "repl_prefix": "!!",
    "os_cmd_enabled": False,
    # Serial
    "port": "COM4",
    "baudrate": 115200,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    "flow_control": "none",
    "encoding": "utf-8",
    "inter_cmd_delay_ms": 0,
    # Connection
    "autoconnect": False,
    "autoreconnect": False,
    "autoconnect_cmd": "",
    "line_ending": "\r",
    # Input echo
    "echo_cmd": False,
    "echo_cmd_fmt": "[purple]> {cmd}[/]",
    # Logging
    "log_file": "",
    "add_date_to_cmd": False,
}


def load_config(path: str) -> dict:
    """Load and validate JSON config, applying defaults for missing fields."""
    p = Path(path)
    if not p.exists():
        print(f"Config file not found: {path}", file=sys.stderr)
        # Ensure it goes into termapy_cfg/<name>/<name>.json
        if not p.parent or p.parent == Path("."):
            name = p.stem
            p = cfg_path_for_name(name)
            path = str(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        print(f"Creating default config at {p}")
        with open(p, "w") as f:
            json.dump(DEFAULT_CFG, f, indent=4)
        return dict(DEFAULT_CFG)

    with open(path) as f:
        cfg = json.load(f)

    changed = False
    for key, val in DEFAULT_CFG.items():
        if key not in cfg:
            cfg[key] = val
            changed = True
    if changed:
        with open(path, "w") as f:
            json.dump(cfg, f, indent=4)
    return cfg


def open_serial(cfg: dict) -> serial.Serial:
    """Open serial port from config dict."""
    fc = cfg.get("flow_control", "none")
    return serial.Serial(
        port=cfg["port"],
        baudrate=cfg["baudrate"],
        bytesize=cfg["bytesize"],
        parity=cfg["parity"],
        stopbits=cfg["stopbits"],
        rtscts=(fc == "rtscts"),
        xonxoff=(fc == "xonxoff"),
        timeout=0.2,
    )


# Shared CSS for modal dialog buttons — used by all modal screens
_MODAL_BTN_CSS = """
    min-width: 0; width: auto; height: 1; min-height: 1;
    border: none; margin: 0 0 0 1;
"""


class ConfigEditor(ModalScreen[tuple | None]):
    """Modal dialog to edit JSON config. Returns (cfg_dict, path) or None."""

    CSS = f"""
    ConfigEditor {{ align: center middle; }}
    ConfigEditor Button {{ {_MODAL_BTN_CSS} }}
    #config-dialog {{
        width: 60; height: 24;
        border: thick $primary; background: $surface; padding: 1 2;
    }}
    #config-title {{ height: 1; text-style: bold; }}
    #config-editor {{ height: 1fr; }}
    #config-error {{ height: 1; color: $error; display: none; }}
    #config-error.visible {{ display: block; }}
    #save-as-row {{ height: 1; display: none; }}
    #save-as-row.visible {{ display: block; }}
    #save-as-input {{ width: 1fr; height: 1; border: none; }}
    #config-buttons {{ height: 1; align: right middle; }}
    """

    def __init__(self, cfg: dict, config_path: str) -> None:
        super().__init__()
        self.cfg = cfg
        self.config_path = config_path

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        with Vertical(id="config-dialog"):
            yield Static(f"JSON: {self.config_path}", id="config-title")
            yield TextArea(
                json.dumps(self.cfg, indent=4),
                language="json",
                show_line_numbers=True,
                id="config-editor",
            )
            yield Static("", id="config-error")
            with Horizontal(id="save-as-row"):
                yield Input(
                    placeholder="filename.json",
                    id="save-as-input",
                )
            with Horizontal(id="config-buttons"):
                yield Button("Save", id="cfg-save", variant="success")
                yield Button("Save As", id="cfg-save-as", variant="primary")
                yield Button("Cancel", id="cfg-cancel", variant="error")

    def _validate_json(self) -> dict | None:
        from textual.widgets import Static

        text = self.query_one("#config-editor", TextArea).text
        err = self.query_one("#config-error", Static)
        try:
            new_cfg = json.loads(text)
        except json.JSONDecodeError as e:
            err.update(f"Invalid JSON: {e}")
            err.add_class("visible")
            return None
        err.remove_class("visible")
        return new_cfg

    @on(Button.Pressed, "#cfg-save")
    def save_config(self) -> None:
        new_cfg = self._validate_json()
        if new_cfg is None:
            return
        Path(self.config_path).parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w") as f:
            json.dump(new_cfg, f, indent=4)
        self.dismiss((new_cfg, self.config_path))

    @on(Button.Pressed, "#cfg-save-as")
    def save_as_config(self) -> None:
        row = self.query_one("#save-as-row")
        if not row.has_class("visible"):
            row.add_class("visible")
            self.query_one("#save-as-input", Input).focus()
            return
        self._do_save_as()

    @on(Input.Submitted, "#save-as-input")
    def save_as_on_enter(self) -> None:
        self._do_save_as()

    def _do_save_as(self) -> None:
        new_cfg = self._validate_json()
        if new_cfg is None:
            return
        filename = self.query_one("#save-as-input", Input).value.strip()
        if not filename:
            from textual.widgets import Static

            err = self.query_one("#config-error", Static)
            err.update("Enter a filename")
            err.add_class("visible")
            return
        if not filename.endswith(".json"):
            filename += ".json"
        # Place in termapy_cfg/<name>/<name>.json
        p = Path(filename)
        if not p.parent or p.parent == Path("."):
            name = p.stem
            p = cfg_path_for_name(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        filename = str(p)
        # Update title to reflect new filename
        base = p.stem
        old_title = new_cfg.get("title", "")
        if old_title and base not in old_title:
            new_cfg["title"] = f"{old_title} — {base}"
        elif not old_title:
            new_cfg["title"] = base
        with open(filename, "w") as f:
            json.dump(new_cfg, f, indent=4)
        self.dismiss((new_cfg, filename))

    @on(Button.Pressed, "#cfg-cancel")
    def cancel_config(self) -> None:
        self.dismiss(None)


class HelpViewer(ModalScreen[None]):
    """Modal dialog to display the UI help guide."""

    CSS = f"""
    HelpViewer {{ align: center middle; }}
    HelpViewer Button {{ {_MODAL_BTN_CSS} }}
    #help-dialog {{
        width: 80%; height: 80%;
        border: thick $primary; background: $surface; padding: 1 2;
    }}
    #help-content {{ height: 1fr; overflow-y: auto; }}
    #help-buttons {{ height: 1; align: right middle; }}
    """

    def compose(self) -> ComposeResult:
        from importlib.resources import files as pkg_files
        from textual.widgets import Markdown

        help_text = pkg_files("termapy").joinpath("help.md").read_text(encoding="utf-8")
        with Vertical(id="help-dialog"):
            yield Markdown(help_text, id="help-content")
            with Horizontal(id="help-buttons"):
                yield Button("Close", id="help-close", variant="primary")

    @on(Button.Pressed, "#help-close")
    def close_help(self) -> None:
        self.dismiss(None)


class LogViewer(ModalScreen[None]):
    """Modal dialog to view the log file."""

    CSS = f"""
    LogViewer {{ align: center middle; }}
    LogViewer Button {{ {_MODAL_BTN_CSS} }}
    #log-dialog {{
        width: 80%; height: 80%;
        border: thick $primary; background: $surface; padding: 1 2;
    }}
    #log-title {{ height: 1; text-align: center; text-style: bold; }}
    #log-viewer {{ height: 1fr; }}
    #log-buttons {{ height: 1; align: right middle; }}
    """

    def __init__(self, log_path: str) -> None:
        super().__init__()
        self.log_path = log_path

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        try:
            content = Path(self.log_path).read_text(encoding="utf-8")
        except FileNotFoundError:
            content = "(no log file yet)"
        with Vertical(id="log-dialog"):
            yield Static(self.log_path, id="log-title")
            yield TextArea(content, read_only=True, id="log-viewer")
            with Horizontal(id="log-buttons"):
                yield Button("Close", id="log-close", variant="primary")

    @on(Button.Pressed, "#log-close")
    def close_log(self) -> None:
        self.dismiss(None)


class NamePicker(ModalScreen[str | None]):
    """Modal dialog to enter a name for a new config."""

    CSS = f"""
    NamePicker {{ align: center middle; }}
    NamePicker Button {{ {_MODAL_BTN_CSS} }}
    #name-dialog {{
        width: 40; height: auto;
        border: thick $primary; background: $surface; padding: 1 2;
    }}
    #name-label {{ height: 1; text-style: bold; }}
    #name-buttons {{ height: 1; margin-top: 1; }}
    """

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        with Vertical(id="name-dialog"):
            yield Static("New config name:", id="name-label")
            yield Input(placeholder="e.g. iot_dev", id="name-input")
            with Horizontal(id="name-buttons"):
                yield Button("Cancel", id="name-cancel")

    @on(Input.Submitted, "#name-input")
    def submit_name(self) -> None:
        name = self.query_one("#name-input", Input).value.strip()
        if name:
            # Strip .json if they typed it
            if name.endswith(".json"):
                name = name[:-5]
            self.dismiss(name)

    @on(Button.Pressed, "#name-cancel")
    def cancel(self) -> None:
        self.dismiss(None)


class ConfigPicker(ModalScreen[str | None]):
    """Modal dialog to select a JSON config file from the current directory."""

    CSS = f"""
    ConfigPicker {{ align: center middle; }}
    ConfigPicker Button {{ {_MODAL_BTN_CSS} }}
    #picker-dialog {{
        width: 50; height: 18;
        border: thick $primary; background: $surface; padding: 1 2;
    }}
    #picker-title {{ height: 1; text-style: bold; }}
    #picker-list {{ height: 1fr; }}
    #picker-buttons {{ height: 1; align: right middle; }}
    """

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        json_files = sorted(cfg_dir().glob("*/*.json"))
        with Vertical(id="picker-dialog"):
            yield Static("Select JSON Config File", id="picker-title")
            ol = OptionList(id="picker-list")
            for f in json_files:
                ol.add_option(Option(f.stem, id=str(f)))
            yield ol
            with Horizontal(id="picker-buttons"):
                yield Button("Cancel", id="picker-cancel", variant="error")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.id))

    @on(Button.Pressed, "#picker-cancel")
    def cancel_picker(self) -> None:
        self.dismiss(None)


class ScriptPicker(ModalScreen[str | None]):
    """Modal dialog to select a script file to run."""

    CSS = f"""
    ScriptPicker {{ align: center middle; }}
    ScriptPicker Button {{ {_MODAL_BTN_CSS} }}
    #script-dialog {{
        width: 50; height: 18;
        border: thick $primary; background: $surface; padding: 1 2;
    }}
    #script-title {{ height: 1; text-style: bold; }}
    #script-list {{ height: 1fr; }}
    #script-buttons {{ height: 1; align: right middle; }}
    """

    def __init__(self, scripts_dir: Path) -> None:
        super().__init__()
        self.scripts_dir = scripts_dir

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        scripts = sorted(self.scripts_dir.glob("*"))
        scripts = [f for f in scripts if f.is_file()]
        with Vertical(id="script-dialog"):
            yield Static("Select Script", id="script-title")
            ol = OptionList(id="script-list")
            for f in scripts:
                ol.add_option(Option(f.name, id=str(f)))
            yield ol
            with Horizontal(id="script-buttons"):
                yield Button("Cancel", id="script-cancel", variant="error")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.id))

    @on(Button.Pressed, "#script-cancel")
    def cancel_picker(self) -> None:
        self.dismiss(None)


class CfgConfirm(ModalScreen[bool]):
    """Modal dialog to confirm a config change."""

    CSS = f"""
    CfgConfirm {{ align: center middle; }}
    CfgConfirm Button {{ {_MODAL_BTN_CSS} }}
    #cfg-confirm-dialog {{
        width: 50; height: 7;
        border: thick $primary; background: $surface; padding: 1 2;
    }}
    #cfg-confirm-msg {{ height: 1; }}
    #cfg-confirm-buttons {{ height: 1; align: right middle; }}
    """

    def __init__(self, key: str, old_val, new_val) -> None:
        super().__init__()
        self.key = key
        self.old_val = old_val
        self.new_val = new_val

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        with Vertical(id="cfg-confirm-dialog"):
            yield Static(
                f"{self.key}: {self.old_val!r} → {self.new_val!r}",
                id="cfg-confirm-msg",
            )
            with Horizontal(id="cfg-confirm-buttons"):
                yield Button("Yes", id="cfg-yes", variant="success")
                yield Button("No", id="cfg-no", variant="error")

    @on(Button.Pressed, "#cfg-yes")
    def confirm(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#cfg-no")
    def cancel(self) -> None:
        self.dismiss(False)


class PortPicker(ModalScreen[str | None]):
    """Modal dialog to select an available serial port."""

    CSS = f"""
    PortPicker {{ align: center middle; }}
    PortPicker Button {{ {_MODAL_BTN_CSS} }}
    #port-dialog {{
        width: 60; height: 18;
        border: thick $primary; background: $surface; padding: 1 2;
    }}
    #port-title {{ height: 1; text-style: bold; }}
    #port-list {{ height: 1fr; }}
    #port-buttons {{ height: 1; align: right middle; }}
    """

    def compose(self) -> ComposeResult:
        from serial.tools.list_ports import comports
        from textual.widgets import Static

        ports = sorted(comports(), key=lambda p: p.device)
        with Vertical(id="port-dialog"):
            yield Static("Select Serial Port", id="port-title")
            ol = OptionList(id="port-list")
            if ports:
                for p in ports:
                    desc = p.description or ""
                    label = f"{p.device} - {desc}" if desc else p.device
                    ol.add_option(Option(label, id=p.device))
            else:
                ol.add_option(Option("(no ports found)", disabled=True))
            yield ol
            with Horizontal(id="port-buttons"):
                yield Button("Cancel", id="port-cancel", variant="error")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.id))

    @on(Button.Pressed, "#port-cancel")
    def cancel_port_picker(self) -> None:
        self.dismiss(None)


class SerialTerminal(App):
    """Textual app: scrolling output + local input line."""

    CSS = """
    #title-bar {
        dock: top;
        height: 1;
        color: white;
    }
    #title-bar Button {
        min-width: 0;
        width: auto;
        height: 1;
        min-height: 1;
        border: none;
        margin: 0;
        padding: 0 1;
    }
    #btn-help {
        width: 3;
        min-width: 3;
        text-align: center;
        padding: 0;
        background: $primary;
    }
    #btn-cmds {
        width: 4;
        min-width: 4;
        text-align: center;
        padding: 0;
    }
    #title-left {
        min-width: 20;
        background: red;
    }
    #title-spacer-l, #title-spacer-r {
        width: 1fr;
    }
    #title-bar #title-center {
        min-width: 6;
        max-width: 32;
        text-align: center;
        padding: 0;
    }
    #title-right {
        min-width: 14;
        text-align: center;
        background: red;
    }
    RichLog {
        height: 1fr;
        scrollbar-size: 1 1;
    }
    #bottom-section {
        dock: bottom;
        height: auto;
    }
    #bottom-bar {
        height: 1;
    }
    #cmd {
        width: 1fr;
        border: none;
        height: 1;
    }
    #bottom-bar Button {
        min-width: 0;
        width: auto;
        height: 1;
        min-height: 1;
        border: none;
        margin: 0 0 0 1;
    }
    #btn-dtr {
        background: #607d8b;
    }
    #btn-rts {
        background: #78909c;
    }
    #btn-break {
        background: #546e7a;
    }
    #btn-log {
        background: #3498db;
    }
    #btn-new-cfg {
        background: #2ecc71;
    }
    #btn-config {
        background: #1e8449;
    }
    #btn-exit {
        background: #e74c3c;
    }
    #history-popup {
        dock: bottom;
        height: auto;
        max-height: 22;
        display: none;
        border: solid $accent;
        background: $surface;
    }
    #history-popup.visible {
        display: block;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("ctrl+p", "show_palette", "Command Palette", show=False),
    ]

    PALETTE_CMDS = [
        ("Select Port...", "_show_port_picker"),
        ("Connect / Disconnect", "_toggle_connection"),
        ("Edit Config", "_palette_edit_config"),
        ("Load Config...", "_palette_load_config"),
        ("New Config", "_palette_new_config"),
        ("View Log", "_palette_view_log"),
        ("Clear Screen", "_palette_clear"),
        ("Save SVG Screenshot", "_palette_ss_svg"),
        ("Save Text Screenshot", "_palette_ss_txt"),
        ("Open Screenshot Folder", "action_open_screenshot"),
        ("Exit", "_palette_exit"),
    ]

    def __init__(
        self,
        cfg: dict,
        config_path: str,
        open_editor: bool = False,
        show_picker: bool = False,
    ) -> None:
        super().__init__()
        self.cfg = cfg
        self.config_path = config_path
        self.open_editor_on_start = open_editor
        self.show_picker_on_start = show_picker
        self.ser: serial.Serial | None = None
        self.log_fh = None
        self.stop_event = Event()
        self.reader_stopped = Event()
        self.reader_stopped.set()  # no reader running initially
        self.last_screenshot: str | None = None
        self.history: list[str] = self._load_history()
        self._popup_mode: str = "history"
        self.repl = ReplEngine(
            cfg,
            config_path,
            write=self._status,
            prefix=cfg.get("repl_prefix", "!!"),
        )

    @property
    def is_connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def _history_path(self) -> str:
        if self.config_path:
            return cfg_history_path(self.config_path)
        return str(cfg_dir() / ".cmd_history.txt")

    def _load_history(self) -> list[str]:
        try:
            lines = Path(self._history_path()).read_text(encoding="utf-8").splitlines()
            return lines[-15:]
        except FileNotFoundError:
            return []

    def _save_history(self) -> None:
        Path(self._history_path()).write_text(
            "\n".join(self.history[-15:]), encoding="utf-8"
        )

    def compose(self) -> ComposeResult:
        title = self.cfg.get("title", "") or self.config_path
        port_info = self._port_info_str()
        with Horizontal(id="title-bar"):
            from textual.widgets import Static

            help_btn = Button("?", id="btn-help")
            help_btn.tooltip = "Show help guide"
            yield help_btn
            left = Button(port_info, id="title-left")
            left.tooltip = "Click to select serial port"
            yield left
            yield Static("", id="title-spacer-l")
            center = Button(title, id="title-center")
            center.tooltip = "Click to load a config"
            yield center
            yield Static("", id="title-spacer-r")
            right = Button("Disconnected", id="title-right")
            right.tooltip = "Click to connect/disconnect"
            yield right
        max_lines = self.cfg.get("max_lines", 10000)
        yield RichLog(
            highlight=True, markup=True, wrap=True, id="output", max_lines=max_lines
        )
        yield OptionList(id="history-popup")
        with Vertical(id="bottom-section"):
            with Horizontal(id="bottom-bar"):
                cmd_btn = Button("!!", id="btn-cmds")
                cmd_btn.tooltip = "Show REPL commands"
                yield cmd_btn
                yield Input(
                    placeholder="!! for REPL commands, Ctrl+P: palette", id="cmd"
                )

                def _btn(label, id, tip, variant="default", display=True):
                    b = Button(label, id=id, variant=variant)
                    b.tooltip = tip
                    b.display = display
                    return b

                show_hw = self.cfg.get("flow_control") == "manual"
                yield _btn(
                    "DTR:0",
                    "btn-dtr",
                    "Toggle Data Terminal Ready line",
                    display=show_hw,
                )
                yield _btn(
                    "RTS:0", "btn-rts", "Toggle Request To Send line", display=show_hw
                )
                yield _btn(
                    "Break",
                    "btn-break",
                    "Send serial break signal (250ms)",
                    display=show_hw,
                )
                yield _btn("Log", "btn-log", "View current log file", "primary")
                yield _btn("SS", "btn-ss-dir", "Open screenshot folder", "primary")
                yield _btn(
                    "Scripts", "btn-scripts", "Run a script", "primary", display=False
                )
                yield _btn(
                    "New", "btn-new-cfg", "Create a new config from defaults", "success"
                )
                yield _btn("Edit", "btn-config", "Edit the current config", "warning")
                yield _btn(
                    "Exit", "btn-exit", "Close connection and exit (Ctrl+C)", "error"
                )

    def _log_path(self) -> str:
        """Return log file path in the per-config data directory."""
        configured = self.cfg.get("log_file", "")
        if configured:
            return str(Path(configured).resolve())
        if self.config_path:
            return cfg_log_path(self.config_path)
        return ""

    def _open_log(self) -> None:
        """Open the log file if a config is loaded and log isn't already open."""
        if self.log_fh or not self.config_path:
            return
        log_path = self._log_path()
        if not log_path:
            return
        self.log_fh = open(log_path, "a", encoding="utf-8")
        self.log_fh.write(f"\n--- Session {datetime.now().isoformat()} ---\n")
        self._status(f"Logging to {log_path}")

    def _apply_border_color(self) -> None:
        """Apply border_color from config to title bar and output border."""
        color = self.cfg.get("app_border_color", "") or "blue"
        bar = self.query_one("#title-bar")
        bar.styles.background = color
        self.query_one("#output", RichLog).styles.border = ("solid", color)

    def on_mount(self) -> None:
        self._apply_border_color()
        # Build plugin context — the stable API for all plugins
        engine = EngineAPI(
            prefix=self.cfg.get("repl_prefix", "!!"),
            plugins=self.repl._plugins,
            get_echo=lambda: self.repl._echo,
            set_echo=lambda val: setattr(self.repl, "_echo", val),
            get_seq_counters=lambda: self.repl._seq_counters,
            set_seq_counters=lambda val: setattr(self.repl, "_seq_counters", val),
            reset_seq=self.repl._reset_seq,
            in_script=lambda: self.repl._in_script,
            script_stop=lambda: self.repl._script_stop.set(),
            save_cfg=self._hook_cfg_confirm,
            apply_cfg=self.repl._apply_cfg,
            coerce_type=ReplEngine._coerce_type,
        )
        ctx = PluginContext(
            write=self._status,
            cfg=self.cfg,
            config_path=self.config_path,
            is_connected=lambda: self.is_connected,
            serial_write=lambda data: self.ser.write(data),
            serial_wait_idle=lambda: self._wait_for_idle(400),
            ss_dir=self.repl.ss_dir,
            scripts_dir=self.repl.scripts_dir,
            notify=lambda text, **kw: self.notify(text, **kw),
            clear_screen=self._clear_output,
            save_screenshot=self.save_screenshot,
            get_screen_text=self._get_screen_text,
            engine=engine,
        )
        self.repl.set_context(ctx)
        self.repl._after_cfg = self._refresh_after_cfg
        # Register app-coupled commands as plugins
        self.repl.register_hook(
            "ss_svg",
            "{name}",
            "Save SVG screenshot. Name defaults to 'screenshot'.",
            self._hook_ss_svg,
            source="app",
        )
        self.repl.register_hook(
            "ss_txt",
            "{name}",
            "Save text screenshot. Name defaults to 'screenshot'.",
            self._hook_ss_txt,
            source="app",
        )
        self.repl.register_hook(
            "ss_dir",
            "",
            "Show the screenshot folder path.",
            self._hook_ss_dir,
            source="app",
        )
        self.repl.register_hook(
            "clr",
            "",
            "Clear the terminal screen.",
            lambda ctx, args: self._clear_output(),
            source="app",
        )
        self.repl.register_hook(
            "delay",
            "<duration>",
            "Wait for duration (e.g. 500ms, 1.5s).",
            self._hook_delay,
            source="app",
        )
        self.repl.register_hook(
            "port",
            "{name | list}",
            "Open a port by name, or 'list' to show available ports.",
            self._hook_port,
            source="app",
        )
        self.repl.register_hook(
            "run",
            "<filename>",
            "Run a script file. Checks scripts/ folder then cwd.",
            self._hook_run,
            source="app",
        )
        self.repl.register_hook(
            "connect",
            "",
            "Connect to the serial port.",
            lambda ctx, args: self._connect(),
            source="app",
        )
        self.repl.register_hook(
            "disconnect",
            "",
            "Disconnect from the serial port.",
            lambda ctx, args: self._disconnect(),
            source="app",
        )
        # Load external plugins: global first, then per-config (can override)
        for info in load_plugins_from_dir(global_plugins_dir(), "global"):
            self.repl.register_plugin(info)
        if self.config_path:
            for info in load_plugins_from_dir(
                cfg_plugins_dir(self.config_path),
                Path(self.config_path).stem,
            ):
                self.repl.register_plugin(info)
        # Open log file (deferred if no config loaded yet)
        self._open_log()
        self._sync_ss_button()
        self._sync_scripts_button()
        if self.show_picker_on_start:
            self.push_screen(ConfigPicker(), callback=self._on_config_picked)
        elif self.open_editor_on_start:
            self._new_config()
        elif self.cfg.get("autoconnect"):
            self._connect()
        else:
            self._status(f"{self._port_info_str()} — press Connect to start")

    def on_unmount(self) -> None:
        self._disconnect()
        self.reader_stopped.wait(timeout=1.0)
        if self.log_fh:
            self.log_fh.close()
            self.log_fh = None

    def _connect(self) -> None:
        if self.is_connected:
            return
        # Wait for any previous reader thread to finish
        self.reader_stopped.wait(timeout=1.0)
        self.stop_event.clear()
        self._try_open_port()

    def _try_open_port(self) -> bool:
        """Attempt to open the serial port. Returns True on success."""
        try:
            self.reader_stopped.clear()
            self.ser = open_serial(self.cfg)
            self.notify(
                f"Connected: {self.cfg['port']} @ {self.cfg['baudrate']}", timeout=0.75
            )
            self._set_conn_status("Connected")
            inp = self.query_one("#cmd", Input)
            inp.placeholder = "Type command, Enter to send"
            inp.focus()
            self._sync_hw_buttons()
            self.read_serial()
            auto_cmd = self.cfg.get("autoconnect_cmd", "")
            if auto_cmd:
                self._run_autoconnect(auto_cmd)
            return True
        except serial.SerialException as e:
            self.reader_stopped.set()
            self._status(f"Serial error: {e}", "red")
            self._set_conn_status("Disconnected")
            if self.cfg.get("autoreconnect"):
                self._auto_reconnect()
            return False

    @work(thread=True)
    def _auto_reconnect(self) -> None:
        """Background thread: retry connecting every second until success or stop."""
        while not self.stop_event.is_set():
            time.sleep(1.0)
            if self.stop_event.is_set():
                break
            self.call_from_thread(self._set_conn_status, "Retrying...")
            try:
                ser = open_serial(self.cfg)
                # Success — hand off to the main thread to finish setup
                ser.close()
                self.call_from_thread(self._try_open_port)
                return
            except (serial.SerialException, OSError):
                self.call_from_thread(self._set_conn_status, "Disconnected")
                continue

    def _wait_for_idle(self, timeout_ms: int = 100, max_wait_s: float = 3.0) -> None:
        """Wait until no serial data arrives for timeout_ms, or max_wait_s elapses."""
        deadline = time.monotonic() + max_wait_s
        last_data = time.monotonic()
        while time.monotonic() < deadline:
            if not self.ser or not self.ser.is_open:
                return
            try:
                waiting = self.ser.in_waiting
            except (serial.SerialException, OSError):
                return
            if waiting > 0:
                last_data = time.monotonic()
            elif (time.monotonic() - last_data) >= timeout_ms / 1000.0:
                return
            time.sleep(0.01)

    @work(thread=True)
    def _run_autoconnect(self, auto_cmd: str) -> None:
        """Send autoconnect commands, waiting for idle between each."""
        time.sleep(0.2)
        self._send_lines(auto_cmd.split("\n"), echo_prefix="auto> ")

    @work(thread=True)
    def _run_multi_cmd(self, cmds: list[str]) -> None:
        """Send multiple commands from CLI input with inter-command delay."""
        self._send_lines(cmds)

    def _send_lines(self, lines: list[str], echo_prefix: str = "") -> None:
        """Send multiple commands with inter_cmd_delay_ms between each."""
        line_ending = self.cfg.get("line_ending", "\r")
        enc = self.cfg.get("encoding", "utf-8")
        delay_s = self.cfg.get("inter_cmd_delay_ms", 0) / 1000.0
        for cmd in lines:
            cmd = cmd.strip()
            if not cmd or not self.ser or not self.ser.is_open:
                continue
            if echo_prefix:
                self.call_from_thread(self._status, f"{echo_prefix}{cmd}")
            try:
                self.ser.write((cmd + line_ending).encode(enc))
            except (serial.SerialException, OSError):
                return
            if delay_s > 0:
                time.sleep(delay_s)
            self._wait_for_idle(400)

    def _status(self, text: str, color: str = "dim") -> None:
        """Write a termapy status message with consistent formatting."""
        self.query_one("#output", RichLog).write(f"[bold italic {color}]{text}[/]")

    def _write_output_markup(self, text: str) -> None:
        self.query_one("#output", RichLog).write(text)

    def _disconnect(self) -> None:
        self.stop_event.set()
        try:
            if self.is_connected:
                self.notify("Disconnected", severity="warning", timeout=0.75)
            self._set_conn_status("Disconnected")
            inp = self.query_one("#cmd", Input)
            inp.placeholder = "!! for REPL commands, Ctrl+P: palette"
            self._sync_hw_buttons(reset=True)
        except Exception:
            pass

    def _sync_hw_visibility(self) -> None:
        """Show or hide DTR/RTS/Break buttons based on flow_control config."""
        show = self.cfg.get("flow_control") == "manual"
        self.query_one("#btn-dtr", Button).display = show
        self.query_one("#btn-rts", Button).display = show
        self.query_one("#btn-break", Button).display = show

    def _switch_config(
        self, cfg: dict, path: str, reconnect_on_auto: bool = False
    ) -> None:
        """Apply a new config: disconnect, update state, refresh UI, reconnect."""
        was_connected = self.is_connected
        if was_connected:
            self._disconnect()
        self.cfg = cfg
        self.config_path = path
        self.repl.cfg = cfg
        self.repl.config_path = path
        self._update_title()
        self._apply_border_color()
        self._sync_hw_visibility()
        self._sync_ss_button()
        self._sync_scripts_button()
        self._open_log()
        if was_connected or (reconnect_on_auto and cfg.get("autoconnect")):
            self._connect()

    def _on_config_picked(self, filename: str | None) -> None:
        if filename is None:
            return
        cfg = load_config(filename)
        self._switch_config(cfg, filename, reconnect_on_auto=True)
        self._status(f"Loaded config: {filename}", "green")

    def _on_config_result(self, result: tuple | None) -> None:
        if result is None:
            return
        new_cfg, new_path = result
        self._switch_config(new_cfg, new_path)
        self._status(f"Config saved: {new_path}", "green")

    def _port_info_str(self) -> str:
        """Format port info like 'COM4 115200 8N1'."""
        sb = self.cfg.get("stopbits", 1)
        sb_str = str(int(sb)) if sb == int(sb) else str(sb)
        return (
            f"\\[{self.cfg['port']} {self.cfg['baudrate']}"
            f" {self.cfg.get('parity', 'N')}{self.cfg.get('bytesize', 8)}{sb_str}]"
        )

    def _update_title(self) -> None:
        title = self.cfg.get("title", "") or self.config_path
        self.query_one("#title-center", Button).label = title
        self.query_one("#title-left", Button).label = self._port_info_str()

    def _set_conn_status(self, text: str) -> None:
        color = "green" if text == "Connected" else "red"
        widget = self.query_one("#title-right", Button)
        widget.label = text
        widget.styles.background = color
        self.query_one("#title-left", Button).styles.background = color
        self.query_one("#title-center", Button).styles.background = color

    def _sync_hw_buttons(self, reset: bool = False) -> None:
        """Update DTR/RTS button labels to reflect actual pin state."""
        try:
            dtr_btn = self.query_one("#btn-dtr", Button)
            rts_btn = self.query_one("#btn-rts", Button)
            if reset:
                dtr_btn.label = "DTR:0"
                rts_btn.label = "RTS:0"
            elif self.is_connected:
                dtr_btn.label = f"DTR:{int(self.ser.dtr)}"
                rts_btn.label = f"RTS:{int(self.ser.rts)}"
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "title-right":
            if self.is_connected:
                self._disconnect()
            else:
                self._connect()
        elif event.button.id == "title-left":
            self._show_port_picker()
        elif event.button.id == "title-center":
            self.push_screen(ConfigPicker(), callback=self._on_config_picked)
        elif event.button.id == "btn-dtr":
            if self.is_connected:
                self.ser.dtr = not self.ser.dtr
                event.button.label = f"DTR:{int(self.ser.dtr)}"
        elif event.button.id == "btn-rts":
            if self.is_connected:
                self.ser.rts = not self.ser.rts
                event.button.label = f"RTS:{int(self.ser.rts)}"
        elif event.button.id == "btn-break":
            if self.is_connected:
                self.ser.send_break(duration=0.25)
                self.notify("Break sent", timeout=1.5)
        elif event.button.id == "btn-cmds":
            self._show_commands()
        elif event.button.id == "btn-help":
            self.push_screen(HelpViewer())
        elif event.button.id == "btn-log":
            self.push_screen(LogViewer(self._log_path()))
        elif event.button.id == "btn-ss-dir":
            self.action_open_screenshot()
        elif event.button.id == "btn-scripts":
            self.push_screen(
                ScriptPicker(self.repl.scripts_dir),
                callback=self._on_script_picked,
            )
        elif event.button.id == "btn-new-cfg":
            self._new_config()
        elif event.button.id == "btn-config":
            self.push_screen(
                ConfigEditor(self.cfg, self.config_path),
                callback=self._on_config_result,
            )
        elif event.button.id == "btn-exit":
            self._disconnect()
            self.exit()

    def _show_port_picker(self) -> None:
        self.push_screen(PortPicker(), callback=self._on_port_picked)

    def _update_port(self, port: str) -> None:
        """Change serial port, save config, and reconnect."""
        if self.is_connected:
            self._disconnect()
        self.cfg["port"] = port
        with open(self.config_path, "w") as f:
            json.dump(self.cfg, f, indent=4)
        self._update_title()
        self._status(f"Port changed to {port}", "green")
        self._connect()

    def _on_port_picked(self, port: str | None) -> None:
        if port is None:
            return
        self._update_port(port)

    # -- Palette action wrappers --

    def _toggle_connection(self) -> None:
        if self.is_connected:
            self._disconnect()
        else:
            self._connect()

    def _new_config(self) -> None:
        self.push_screen(NamePicker(), callback=self._on_name_picked)

    def _on_name_picked(self, name: str | None) -> None:
        if name is None:
            return
        config_path = str(cfg_path_for_name(name))
        cfg = dict(DEFAULT_CFG)
        cfg["title"] = name
        self.push_screen(
            ConfigEditor(cfg, config_path),
            callback=self._on_config_result,
        )

    def _palette_edit_config(self) -> None:
        self.push_screen(
            ConfigEditor(self.cfg, self.config_path),
            callback=self._on_config_result,
        )

    def _palette_load_config(self) -> None:
        self.push_screen(ConfigPicker(), callback=self._on_config_picked)

    def _palette_new_config(self) -> None:
        self._new_config()

    def _palette_view_log(self) -> None:
        self.push_screen(LogViewer(self._log_path()))

    def _palette_clear(self) -> None:
        self.query_one("#output", RichLog).clear()

    def _palette_ss_svg(self) -> None:
        self.repl.dispatch("ss_svg")

    def _palette_ss_txt(self) -> None:
        self.repl.dispatch("ss_txt")

    def _palette_exit(self) -> None:
        self._disconnect()
        self.exit()

    @work(thread=True)
    def read_serial(self) -> None:
        """Background thread: read serial port and post to output."""
        try:
            buf = ""
            while not self.stop_event.is_set():
                if not self.ser or not self.ser.is_open:
                    break
                try:
                    waiting = self.ser.in_waiting or 1
                    data = self.ser.read(min(waiting, 4096))
                except (serial.SerialException, OSError, AttributeError):
                    self.call_from_thread(
                        self.notify,
                        "Serial disconnected",
                        severity="warning",
                        timeout=1.5,
                    )
                    self.call_from_thread(self._set_conn_status, "Disconnected")
                    if self.cfg.get("autoreconnect"):
                        self.call_from_thread(self._auto_reconnect)
                    break

                if not data:
                    # Flush any partial line after idle, but not if it
                    # ends with an incomplete ANSI escape sequence
                    if buf and not PARTIAL_ANSI_RE.search(buf):
                        self.call_from_thread(self._write_output_batch, [buf])
                        self.call_from_thread(self._write_log_batch, [buf])
                        buf = ""
                    continue

                text = data.decode(self.cfg.get("encoding", "utf-8"), errors="replace")
                buf += text

                # Check for clear screen in raw buffer before line splitting
                # Discard everything before and including the clear sequence
                m = CLEAR_SCREEN_RE.search(buf)
                if m:
                    buf = buf[m.end() :]
                    self.call_from_thread(self._clear_output)

                # Collect all complete lines, post as a single batch
                lines = []
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip("\r")
                    if line:
                        lines.append(line)
                if lines:
                    self.call_from_thread(self._write_output_batch, lines)
                    self.call_from_thread(self._write_log_batch, lines)
        finally:
            if self.ser:
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.ser = None
            self.reader_stopped.set()

    def _clear_output(self) -> None:
        self.query_one("#output", RichLog).clear()

    def _get_screen_text(self) -> str:
        log = self.query_one("#output", RichLog)
        return "\n".join(strip.text for strip in log.lines)

    def _write_output_batch(self, lines: list[str]) -> None:
        log = self.query_one("#output", RichLog)
        for text in lines:
            log.write(Text.from_ansi(text))

    def _write_log_batch(self, lines: list[str]) -> None:
        if self.log_fh:
            for text in lines:
                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                clean = ANSI_RE.sub("", text)
                self.log_fh.write(f"[{ts}] {clean}\n")
            self.log_fh.flush()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Send command to serial port when Enter is pressed."""
        self._hide_history()
        cmd = event.value.strip()
        if not cmd:
            return

        # Add to history (keep last 15, persist to disk)
        if not self.history or self.history[-1] != cmd:
            self.history.append(cmd)
            if len(self.history) > 15:
                self.history.pop(0)
            self._save_history()

        # Log command with datetime if enabled
        if self.cfg.get("add_date_to_cmd") and self.log_fh:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            self.log_fh.write(f"[{ts}] CMD> {cmd}\n")
            self.log_fh.flush()

        # Check for REPL command — echo controlled by !!echo
        prefix = self.cfg.get("repl_prefix", "!!")
        if cmd.startswith(prefix):
            if self.repl.echo:
                fmt = self.cfg.get("echo_cmd_fmt", "> {cmd}")
                self._write_output_markup(fmt.replace("{cmd}", cmd))
            self.repl.dispatch(cmd[len(prefix) :].strip())
            self.query_one("#cmd", Input).value = ""
            return

        # Echo serial command locally if enabled
        if self.cfg.get("echo_cmd"):
            fmt = self.cfg.get("echo_cmd_fmt", "> {cmd}")
            self._write_output_markup(fmt.replace("{cmd}", cmd))

        # Send to device — split on literal \n for multi-command input
        if not self.is_connected:
            self._status("Not connected — command not sent", "red")
            self.query_one("#cmd", Input).value = ""
            return
        parts = cmd.replace("\\n", "\n").split("\n")
        if len(parts) > 1:
            self._run_multi_cmd(parts)
        else:
            line_ending = self.cfg.get("line_ending", "\r")
            self.ser.write(
                (cmd + line_ending).encode(self.cfg.get("encoding", "utf-8"))
            )

        # Clear input
        inp = self.query_one("#cmd", Input)
        inp.value = ""

    def _show_history(self) -> None:
        """Show the history popup with only past commands."""
        self.history = self._load_history()
        popup = self.query_one("#history-popup", OptionList)
        popup.clear_options()
        if not self.history:
            popup.add_option(Option("(no history yet)", disabled=True))
        else:
            for cmd in reversed(self.history):
                popup.add_option(Option(cmd))
        popup.add_class("visible")
        popup.focus()
        popup.highlighted = 0
        self._popup_mode = "history"

    def _show_commands(self) -> None:
        """Show the REPL command picker with smart arg handling."""
        popup = self.query_one("#history-popup", OptionList)
        popup.clear_options()
        prefix = self.cfg.get("repl_prefix", "!!")
        groups: dict[str, list] = {}
        for name, plugin in self.repl._plugins.items():
            groups.setdefault(plugin.source, []).append((name, plugin))
        for source, plugins in groups.items():
            popup.add_option(Option(f"── {source} ──", disabled=True))
            for name, plugin in plugins:
                has_required = "<" in plugin.args if plugin.args else False
                has_optional = "{" in plugin.args if plugin.args else False
                if not plugin.args or (has_optional and not has_required):
                    label = Text(f"{prefix}{name}")
                    label.append(f"  # {plugin.help}", style="dim")
                    popup.add_option(Option(label, id=f"run:{name}"))
                if plugin.args:
                    label = Text(f"{prefix}{name} {plugin.args}")
                    label.append(f"  # {plugin.help}", style="dim")
                    popup.add_option(Option(label, id=f"repl:{name}"))
        popup.add_class("visible")
        popup.focus()
        popup.highlighted = 1 if popup.option_count > 1 else 0
        self._popup_mode = "commands"

    def _show_palette(self) -> None:
        """Show the command palette popup."""
        popup = self.query_one("#history-popup", OptionList)
        popup.clear_options()
        for i, (label, _) in enumerate(self.PALETTE_CMDS):
            popup.add_option(Option(label, id=f"palette:{i}"))
        popup.add_class("visible")
        popup.focus()
        if popup.option_count > 0:
            popup.highlighted = 0
        self._popup_mode = "palette"

    def action_show_palette(self) -> None:
        self._show_palette()

    def _hide_history(self) -> None:
        popup = self.query_one("#history-popup", OptionList)
        popup.remove_class("visible")
        self.query_one("#cmd", Input).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Handle selection from history or palette popup."""
        if event.option_list.id != "history-popup":
            return
        self._hide_history()
        opt_id = str(event.option.id) if event.option.id is not None else ""
        if self._popup_mode == "palette" and opt_id.startswith("palette:"):
            idx = int(opt_id.split(":")[1])
            _, method_name = self.PALETTE_CMDS[idx]
            getattr(self, method_name)()
        elif opt_id.startswith("run:"):
            name = opt_id.split(":")[1]
            self.repl.dispatch(name)
        elif opt_id.startswith("repl:"):
            name = opt_id.split(":")[1]
            prefix = self.cfg.get("repl_prefix", "!!")
            inp = self.query_one("#cmd", Input)
            inp.value = f"{prefix}{name} "
            inp.action_end()
        else:
            inp = self.query_one("#cmd", Input)
            inp.value = str(event.option.prompt)
            inp.action_end()

    def on_key(self, event) -> None:
        """Handle Up arrow to show history, Escape to dismiss."""
        # Fast exit for normal typing — only handle special keys
        if event.key not in ("up", "down", "escape"):
            return

        if event.key == "escape":
            popup = self.query_one("#history-popup", OptionList)
            if popup.has_class("visible"):
                self._hide_history()
                event.prevent_default()
            return

        if event.key == "up":
            inp = self.query_one("#cmd", Input)
            if inp.has_focus:
                self._show_history()
                event.prevent_default()
            return

        if event.key == "down":
            inp = self.query_one("#cmd", Input)
            popup = self.query_one("#history-popup", OptionList)
            if inp.has_focus and popup.has_class("visible"):
                # Down from input while popup is showing — focus the popup
                popup.focus()
                event.prevent_default()

    def action_clear_log(self) -> None:
        self.query_one("#output", RichLog).clear()

    def action_screenshot(self) -> None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        svg_path = str((self.repl.ss_dir / f"screenshot_{ts}.svg").resolve())
        self.save_screenshot(svg_path)
        self.last_screenshot = svg_path
        self.notify(f"Screenshot saved: {svg_path}", timeout=1.5)
        self._sync_ss_button()

    def action_text_screenshot(self) -> None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        txt_path = str((self.repl.ss_dir / f"screenshot_{ts}.txt").resolve())
        text = self._get_screen_text()
        Path(txt_path).write_text(text, encoding="utf-8")
        self.last_screenshot = txt_path
        self.notify(f"Text screenshot saved: {txt_path}", timeout=1.5)
        self._sync_ss_button()

    def action_open_screenshot(self) -> None:
        import subprocess

        if not self.config_path:
            self.notify("No config loaded", severity="warning")
            return
        folder = str(self.repl.ss_dir.resolve())
        if sys.platform == "win32":
            import os

            os.startfile(folder)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])

    def _sync_ss_button(self) -> None:
        """Update the SS button tooltip with file counts."""
        btn = self.query_one("#btn-ss-dir", Button)
        ss_dir = self.repl.ss_dir
        if ss_dir.exists():
            svgs = len(list(ss_dir.glob("*.svg")))
            txts = len(list(ss_dir.glob("*.txt")))
            btn.tooltip = f"Open screenshot folder ({svgs} svg, {txts} txt)"
        else:
            btn.tooltip = "Open screenshot folder (empty)"

    def _sync_scripts_button(self) -> None:
        """Show the Scripts button if the scripts folder has any files."""
        scripts_dir = self.repl.scripts_dir
        has_files = scripts_dir.exists() and any(
            f.is_file() for f in scripts_dir.iterdir()
        )
        self.query_one("#btn-scripts", Button).display = has_files

    # -- REPL hook implementations (app-coupled commands) ----------------------

    def _hook_ss_svg(self, ctx, args: str) -> None:
        base = args.strip() or "screenshot"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = str((self.repl.ss_dir / f"{base}_{ts}.svg").resolve())
        self.save_screenshot(path)
        self.last_screenshot = path
        self._status(f"SVG screenshot saved: {path}", "green")
        self._sync_ss_button()

    def _hook_ss_txt(self, ctx, args: str) -> None:
        base = args.strip() or "screenshot"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = str((self.repl.ss_dir / f"{base}_{ts}.txt").resolve())
        text = self._get_screen_text()
        Path(path).write_text(text, encoding="utf-8")
        self.last_screenshot = path
        self._status(f"Text screenshot saved: {path}", "green")
        self._sync_ss_button()

    def _hook_ss_dir(self, ctx, args: str) -> None:
        self._status(f"Screenshot dir: {self.repl.ss_dir.resolve()}")

    def _hook_delay(self, ctx, args: str) -> None:
        try:
            seconds = parse_duration(args)
        except ValueError as e:
            self._status(str(e), "red")
            return
        self.set_timer(seconds, lambda: self._status(f"Delay {args} done."))

    def _hook_port(self, ctx, args: str) -> None:
        arg = args.strip().lower()
        if not arg or arg == "list":
            from serial.tools.list_ports import comports

            ports = sorted(comports(), key=lambda p: p.device)
            if not ports:
                self._status("No serial ports found", "yellow")
                return
            for p in ports:
                desc = p.description or ""
                self._status(f"  {p.device}  {desc}")
            return
        self._update_port(args.strip())

    _SERIAL_KEYS = {
        "port",
        "baudrate",
        "bytesize",
        "parity",
        "stopbits",
        "flow_control",
    }

    def _refresh_after_cfg(self, key: str, new_val) -> None:
        was_connected = self.is_connected
        if key in self._SERIAL_KEYS and was_connected:
            self._disconnect()
        self._update_title()
        self._apply_border_color()
        self._sync_hw_visibility()
        if key in self._SERIAL_KEYS and was_connected:
            self._connect()

    def _hook_cfg_confirm(self, key: str, new_val) -> None:
        old_val = self.cfg[key]

        def on_result(confirmed: bool) -> None:
            if confirmed:
                self.repl._apply_cfg(key, new_val)

        self.push_screen(CfgConfirm(key, old_val, new_val), callback=on_result)

    def _on_script_picked(self, filename: str | None) -> None:
        if filename:
            path = self.repl.start_script(filename)
            if path:
                self._run_script(path)

    def _hook_run(self, ctx, args: str) -> None:
        path = self.repl.start_script(args)
        if path:
            self._run_script(path)

    @work(thread=True)
    def _run_script(self, path: Path) -> None:
        """Threaded wrapper for repl.run_script (needs @work decorator)."""
        saved_write = self.repl.write
        self.repl.write = lambda text, color="dim": self.call_from_thread(
            self._status, text, color
        )
        try:
            self.repl.run_script(path)
        finally:
            self.repl.write = saved_write


def _find_config() -> tuple[str | None, bool]:
    """Find config in termapy_cfg/<name>/<name>.json. Returns (path, show_picker).

    - 1 json file: (path, False) — auto-load
    - 0 json files: (None, False) — show name picker for new config
    - 2+ json files: (None, True) — show file picker
    """
    d = cfg_dir()
    json_files = sorted(d.glob("*/*.json"))
    if len(json_files) == 1:
        return str(json_files[0]), False
    if len(json_files) > 1:
        return None, True
    return None, False


def main():
    global CFG_DIR
    parser = argparse.ArgumentParser(
        description="TUI serial terminal with ANSI color support"
    )
    parser.add_argument(
        "config",
        nargs="?",
        default=None,
        help="Path to JSON config file (auto-detects single .json in termapy_cfg/)",
    )
    parser.add_argument(
        "--cfg-dir",
        default=None,
        help=f"Config directory (default: {CFG_DIR})",
    )
    args = parser.parse_args()

    if args.cfg_dir:
        CFG_DIR = args.cfg_dir

    if args.config:
        cfg = load_config(args.config)
        app = SerialTerminal(cfg, config_path=args.config)
        app.run()
        return

    config_path, show_picker = _find_config()

    if config_path:
        cfg = load_config(config_path)
        app = SerialTerminal(cfg, config_path=config_path)
        app.run()
    elif show_picker:
        # Multiple json files — start with defaults, show picker on load
        cfg = dict(DEFAULT_CFG)
        app = SerialTerminal(cfg, config_path="", show_picker=True)
        app.run()
    else:
        # No json files — start with defaults and open editor
        cfg = dict(DEFAULT_CFG)
        app = SerialTerminal(cfg, config_path="", open_editor=True)
        app.run()


if __name__ == "__main__":
    main()
