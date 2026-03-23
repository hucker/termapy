#!/usr/bin/env python3
"""
Usage:
    uv run termapy [config.json]

Runs well in most terminals (Windows Terminal, iTerm2, etc).
VS Code's integrated terminal can be jerky due to its rendering pipeline.
"""

import argparse
import io
import json
import queue
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from threading import Event

import serial
from termapy.config import (
    CFG_DIR,
    CURRENT_CONFIG_VERSION,
    cfg_data_dir,
    cfg_dir,
    migrate_json_to_cfg,
    cfg_history_path,
    cfg_log_path,
    cfg_path_for_name,
    cfg_plugins_dir,
    expand_env_cfg,
    global_plugins_dir,
    load_config,
    open_serial,
    open_with_system,
    setup_demo_config,
    validate_config,
)
from rich.text import Text
from textual import on, work

from termapy.builtins.plugins.var import (
    clear_vars,
    set_start_time_vars,
)
from termapy.defaults import (
    DEFAULT_CFG,
    VALID_BYTE_SIZES,
    VALID_FLOW_CONTROLS,
    VALID_PARITIES,
    VALID_STOP_BITS,
)
from termapy.dialogs import (
    CfgConfirm,
    ConfigEditor,
    ConfigPicker,
    ConfirmDialog,
    NamePicker,
    PortPicker,
    ProtoEditor,
    ProtoPicker,
    ScriptEditor,
    ScriptPicker,
)
from termapy.plugins import EngineAPI, LoadResult, PluginContext, load_plugins_from_dir
from termapy.proto_debug import ProtoDebugScreen
from termapy.protocol import builtins_viz_dir, load_visualizers_from_dir
from termapy.repl import ReplEngine
from termapy.protocol import apply_format
from termapy.scripting import parse_duration
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, OptionList, ProgressBar, RichLog, Static
from textual.widgets.option_list import Option
from textual.suggester import Suggester


class CommandSuggester(Suggester):
    """Type-ahead from REPL commands + device command history.

    Combines REPL command names (e.g. ``/help``, ``/cfg``) with non-REPL
    history entries (device commands like ``AT+CSQ``). Updated dynamically
    as new commands are entered.
    """

    def __init__(self) -> None:
        super().__init__(use_cache=False, case_sensitive=False)
        self._suggestions: list[str] = []

    def update(
        self, commands: list[str], history: list[str], prefix: str = "/"
    ) -> None:
        """Rebuild suggestions: REPL commands + non-REPL history (deduped)."""
        device_cmds = [h for h in history if not h.startswith(prefix)]
        self._suggestions = commands + device_cmds

    async def get_suggestion(self, value: str) -> str | None:
        """Return the first prefix match (case-insensitive)."""
        for s in self._suggestions:
            if s.casefold().startswith(value):
                return s
        return None


# Regex to strip ANSI escape sequences for plain-text logging
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
# ANSI clear screen sequence (with optional cursor-home prefix)
CLEAR_SCREEN_RE = re.compile(r"(\x1b\[H)?\x1b\[2J")
# Incomplete ANSI escape at end of buffer: ESC, or ESC[ with optional digits/semicolons
PARTIAL_ANSI_RE = re.compile(r"\x1b(\[[0-9;]*)?$")

# Dim ANSI markers for visible EOL display (show_line_endings mode)
_EOL_CR = "\x1b[2m\\r\x1b[0m"
_EOL_LF = "\x1b[2m\\n\x1b[0m"


def _eol_label(line_ending: str) -> str:
    """Return a dim ANSI label for a line ending string.

    Args:
        line_ending: The line ending characters (e.g. "\\r", "\\n", "\\r\\n").

    Returns:
        Dim ANSI string showing the ending, e.g. ``\\r\\n`` for CR+LF.
    """
    return line_ending.replace("\r", _EOL_CR).replace("\n", _EOL_LF)


class SerialTerminal(App):
    """Textual app: scrolling output + local input line."""

    TITLE = "termapy"

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
        margin: 0 0 0 1;
        padding: 0 1;
    }
    #btn-help {
        margin-left: 0;
        width: 3;
        min-width: 3;
        text-align: center;
        padding: 0;
        background: $primary;
    }
    #btn-cmds {
        width: auto;
        min-width: 3;
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
        width: 24;
        text-align: center;
    }
    #title-right {
        min-width: 14;
        text-align: center;
        background: red;
    }
    #btn-cfg {
        background: dodgerblue;
    }
    #btn-proto {
        background: dodgerblue;
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
    #cmd.repl-mode {
        color: red;
    }
    #cmd.var-mode {
        color: cyan;
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
        background: slategray;
    }
    #btn-rts {
        background: lightslategray;
    }
    #btn-break {
        background: darkslategray;
    }
    .custom-btn {
        background: mediumpurple;
    }
    #btn-log {
        background: mediumorchid;
    }
    #btn-ss-dir {
        background: dodgerblue;
    }
    #btn-cap-dir {
        background: mediumseagreen;
    }
    #btn-scripts {
        background: dodgerblue;
    }
    #btn-exit {
        background: crimson;
    }
    #cap-label {
        color: green;
        width: 1fr;
        height: 1;
    }
    #cap-stop {
        background: crimson;
        min-width: 0;
        width: auto;
        height: 1;
        min-height: 1;
        border: none;
        margin: 0 0 0 1;
    }
    Toast {
        min-width: 50;
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
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+p", "show_palette", "Command Palette", show=False, priority=True),
        Binding("ctrl+s", "screenshot", "Screenshot", show=False),
        Binding("ctrl+t", "text_screenshot", "Text Screenshot", show=False),
    ]

    PALETTE_CMDS = [
        ("Help", "_palette_help"),
        ("Select Port...", "_show_port_picker"),
        ("Connect / Disconnect", "_toggle_connection"),
        ("Edit Config", "_palette_edit_config"),
        ("Load Config...", "_palette_load_config"),
        ("New Config", "_palette_new_config"),
        ("View Log File", "_palette_view_log"),
        ("Delete Log File", "_palette_delete_log"),
        ("Clear Screen", "_palette_clear"),
        ("Save SVG Screenshot", "_palette_ss_svg"),
        ("Save Text Screenshot", "_palette_ss_txt"),
        ("Open Screenshot Folder", "action_open_screenshot"),
        ("Open Captures Folder", "_open_captures_dir"),
        ("Show Newest Screenshot", "_palette_show_newest_ss"),
        ("Show Newest Text Capture", "_palette_show_newest_cap"),
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
        self.config_path = config_path
        self.open_editor_on_start = open_editor
        self.show_picker_on_start = show_picker
        self.ser: serial.Serial | None = None
        self.log_fh = None
        self.stop_event = Event()
        self.reader_stopped = Event()
        self.reader_stopped.set()  # no reader running initially
        self.last_screenshot: str | None = None
        self.repl = ReplEngine(
            cfg,
            config_path,
            write=self._status,
            prefix=cfg.get("cmd_prefix", "/"),
        )
        self.history: list[str] = self._load_history()
        self._history_idx: int = -1  # -1 = not browsing history
        self._history_saved_input: str = ""  # input text before Up was pressed
        self._suggester = CommandSuggester()
        self._cached_commands: list[str] = []
        self._popup_mode: str = "commands"
        self._show_line_numbers: bool = False
        self._line_counter: int = 0
        self._proto_hex_mode: bool = False
        self._proto_active: bool = False
        self._raw_rx_queue: "queue.Queue[bytes]" = queue.Queue()

        # File capture state
        self._cap_suppress_display: bool = False  # True = hide serial output from screen
        self._cap_fh: "io.TextIOWrapper | io.BufferedWriter | None" = None
        self._cap_mode: str = ""          # "text" or "bin"
        self._cap_raw: bool = False       # True = raw binary (no type conversion)
        self._cap_path: Path | None = None
        self._cap_bytes: int = 0          # bytes captured so far
        self._cap_target: int = 0         # target bytes (bin mode)
        self._cap_end: float = 0.0        # monotonic deadline (text mode)
        self._cap_total: float = 0.0      # total duration/bytes for progress calc
        self._cap_timer: "Timer | None" = None
        self._cap_progress_timer: "Timer | None" = None
        self._cap_columns: list = []      # format spec columns (bin mode)
        self._cap_record_size: int = 0    # bytes per record
        self._cap_sep: str = ","          # column separator
        self._cap_echo: bool = False      # echo formatted values to terminal
        self._cap_header_written: bool = False  # CSV header written
        self._cap_buf: bytearray = bytearray()  # binary accumulator

    @property
    def cfg(self):
        """Read-only view of the config dict (single source of truth in ReplEngine)."""
        return self.repl.cfg

    @property
    def is_connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def _history_path(self) -> str:
        if self.config_path:
            return cfg_history_path(self.config_path)
        return str(cfg_dir() / ".cmd_history.txt")

    _HISTORY_LIMIT = 30

    def _load_history(self) -> list[str]:
        """Load command history from disk (last _HISTORY_LIMIT entries)."""
        try:
            lines = Path(self._history_path()).read_text(encoding="utf-8").splitlines()
            return lines[-self._HISTORY_LIMIT :]
        except FileNotFoundError:
            return []

    def _save_history(self) -> None:
        """Persist command history to disk."""
        data = "\n".join(self.history[-self._HISTORY_LIMIT :])
        try:
            Path(self._history_path()).write_text(data, encoding="utf-8")
        except OSError:
            pass

    def _project_files(self) -> list[str]:
        """Return suggestion names for all editable project files.

        Scans scripts/ (.run) and proto/ (.pro).
        Skips ss/, plugins/, viz/, and .py files.
        """
        names: list[str] = []
        for d, pattern in [
            (self.repl.scripts_dir, "*.run"),
            (self.repl.proto_dir, "*.pro"),
        ]:
            if d.exists():
                for f in sorted(d.glob(pattern)):
                    if f.is_file():
                        names.append(f"{d.name}/{f.name}")
        return names

    def _resolve_project_file(self, name: str) -> Path | None:
        """Resolve a user-supplied filename to an absolute path.

        Handles prefixed paths (scripts/foo.run, proto/bar.pro).
        Falls back to extension-based lookup for bare filenames.

        Args:
            name: User input (e.g. "scripts/demo.run", "test.pro").

        Returns:
            Resolved Path, or None if not found.
        """
        # Prefixed path: "scripts/foo.run" or "proto/bar.pro"
        dir_map = {
            "scripts": self.repl.scripts_dir,
            "proto": self.repl.proto_dir,
        }
        parts = Path(name).parts
        if len(parts) == 2:
            base = dir_map.get(parts[0].lower())
            if base:
                path = base / parts[1]
                return path if path.exists() else None

        # Bare filename fallback by extension
        ext = Path(name).suffix.lower()
        ext_map = {
            ".run": self.repl.scripts_dir,
            ".pro": self.repl.proto_dir,
        }
        base = ext_map.get(ext)
        if base:
            path = base / name
            return path if path.exists() else None
        return None

    def _rebuild_suggester_commands(self) -> None:
        """Rebuild the cached command list (call on plugin/config/file changes)."""
        prefix = self.cfg.get("cmd_prefix", "/")
        commands: list[str] = []
        for name, plugin in self.repl._plugins.items():
            commands.append(f"{prefix}{name}")
            if plugin.args:
                commands.append(f"{prefix}{name} {plugin.args}")
        for f in self._project_files():
            commands.append(f"{prefix}edit {f}")
        self._cached_commands = commands
        self._suggester.update(commands, self.history, prefix)

    def _update_suggester(self) -> None:
        """Update suggestions with current history (no filesystem scan)."""
        prefix = self.cfg.get("cmd_prefix", "/")
        self._suggester.update(self._cached_commands, self.history, prefix)

    def compose(self) -> ComposeResult:
        title = self.cfg.get("title", "") or self.config_path
        port_info = self._port_info_str()
        with Horizontal(id="title-bar"):
            from textual.widgets import Static

            help_btn = Button("?", id="btn-help")
            help_btn.tooltip = "Show help guide."
            yield help_btn
            cfg_btn = Button("Cfg", id="btn-cfg")
            cfg_btn.tooltip = "New / Edit / Load config."
            yield cfg_btn
            run_btn = Button("Run", id="btn-scripts")
            run_btn.tooltip = "Run a script."
            yield run_btn
            proto_btn = Button("Proto", id="btn-proto")
            proto_btn.tooltip = "Protocol test scripts."
            yield proto_btn
            yield Static("", id="title-spacer-l")
            center = Button(title, id="title-center")
            center.tooltip = "Click to load a config."
            yield center
            yield Static("", id="title-spacer-r")
            left = Button(port_info, id="title-left")
            left.tooltip = "Click to select serial port."
            yield left
            right = Button("Disconnected", id="title-right")
            right.tooltip = "Click to connect/disconnect."
            yield right
        max_lines = self.cfg.get("max_lines", 10000)
        yield RichLog(
            highlight=False, markup=True, wrap=True, id="output", max_lines=max_lines
        )
        yield OptionList(id="history-popup")
        with Vertical(id="bottom-section"):
            with Horizontal(id="bottom-bar"):
                prefix = self.cfg.get("cmd_prefix", "/")
                cmd_btn = Button(prefix, id="btn-cmds")
                cmd_btn.tooltip = f"Show REPL {prefix} commands."
                yield cmd_btn
                yield Input(
                    placeholder=f"{prefix} for REPL commands, Ctrl+P: palette",
                    id="cmd",
                    suggester=self._suggester,
                )

                def _btn(label, id, tip, variant="default", display=True):
                    b = Button(label, id=id, variant=variant)
                    b.tooltip = tip
                    b.display = display
                    return b

                show_hw = self.cfg.get("flow_control") == "manual"
                self._btn_dtr = _btn(
                    "DTR:0",
                    "btn-dtr",
                    "Toggle Data Terminal Ready line.",
                    display=show_hw,
                )
                yield self._btn_dtr
                self._btn_rts = _btn(
                    "RTS:0", "btn-rts", "Toggle Request To Send line.", display=show_hw
                )
                yield self._btn_rts
                self._btn_break = _btn(
                    "Break",
                    "btn-break",
                    "Send serial break signal (250ms).",
                    display=show_hw,
                )
                yield self._btn_break
                custom_buttons = self.cfg.get("custom_buttons", [])
                has_custom = False
                for i, cb in enumerate(custom_buttons):
                    if not cb.get("enabled", False):
                        continue
                    has_custom = True
                    btn_id = f"btn-custom-{i}"
                    b = Button(cb.get("name", f"C{i}"), id=btn_id)
                    b.tooltip = cb.get("tooltip", cb.get("name", ""))
                    b.add_class("custom-btn")
                    yield b
                log_btn = _btn("Log", "btn-log", "View current log file.")
                if has_custom:
                    log_btn.styles.margin = (0, 0, 0, 2)
                yield log_btn
                yield _btn("SS", "btn-ss-dir", "Open screenshot folder.")
                yield _btn("Cap", "btn-cap-dir", "Open captures folder.")
                yield _btn("Exit", "btn-exit", "Close connection and exit (Ctrl+C).")

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
        self._log_line("#", f"{' Session Start ':-^60s}")
        self._status(f"Logging to {log_path}")

    def _apply_border_color(self) -> None:
        """Apply border_color from config to title bar and output border."""
        color = self.cfg.get("border_color", "") or "blue"
        bar = self.query_one("#title-bar")
        bar.styles.background = color
        self.query_one("#output", RichLog).styles.border = ("solid", color)

    def on_mount(self) -> None:
        self._apply_border_color()
        # Build plugin context — the stable API for all plugins
        engine = EngineAPI(
            prefix=self.cfg.get("cmd_prefix", "/"),
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
            get_hex_mode=lambda: self._proto_hex_mode,
            set_hex_mode=self._set_hex_mode,
            set_proto_active=lambda active: setattr(self, "_proto_active", active),
            open_proto_debug=lambda path, script: self.call_later(
                self._open_proto_debug, path, script
            ),
            start_capture=self._cap_start,
            stop_capture=self._cap_stop,
            directives=self.repl._directives,
        )
        ctx = PluginContext(
            write=self._status,
            write_markup=self._write_output_markup,
            log=self._log_line,
            cfg=self.cfg,
            config_path=self.config_path,
            is_connected=lambda: self.is_connected,
            serial_write=self._serial_write,
            serial_wait_idle=lambda timeout_ms=400: self._wait_for_idle(timeout_ms),
            serial_read_raw=self._serial_read_raw,
            serial_drain=self._drain_rx_queue,
            serial_claim=lambda: setattr(self, "_proto_active", True),
            serial_release=lambda: setattr(self, "_proto_active", False),
            dispatch=self._dispatch_single,
            ss_dir=self.repl.ss_dir,
            scripts_dir=self.repl.scripts_dir,
            proto_dir=self.repl.proto_dir,
            cap_dir=self.repl.cap_dir,
            confirm=self._confirm,
            notify=lambda text, **kw: self.notify(text, **kw),
            clear_screen=self._clear_output,
            save_screenshot=self.save_screenshot,
            get_screen_text=self._get_screen_text,
            exit_app=self.exit,
            engine=engine,
        )
        self.repl.set_context(ctx)
        self.repl._after_cfg = self._refresh_after_cfg
        # Register app-coupled commands as plugins
        self.repl.register_hook(
            "ss.svg",
            "{name}",
            "Save SVG screenshot. Name defaults to 'screenshot'.",
            self._hook_ss_svg,
            source="app",
        )
        self.repl.register_hook(
            "ss.txt",
            "{name}",
            "Save text screenshot. Name defaults to 'screenshot'.",
            self._hook_ss_txt,
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
            "{name}",
            "Serial port tools: open, close, list.",
            self._hook_port,
            source="app",
        )
        self.repl.register_hook(
            "port.list",
            "",
            "List available serial ports.",
            self._hook_port_list,
            source="app",
        )
        self.repl.register_hook(
            "port.open",
            "{name}",
            "Connect to the serial port (optional port override).",
            lambda ctx, args: self._connect(args.strip() if args.strip() else None),
            source="app",
        )
        self.repl.register_hook(
            "port.close",
            "",
            "Disconnect from the serial port.",
            lambda ctx, args: self._disconnect(),
            source="app",
        )
        self.repl.register_hook(
            "port.info",
            "",
            "Show port status, serial parameters, and hardware lines.",
            self._hook_port_info,
            source="app",
        )
        for key, (_, _, desc, _) in self._PORT_PROPS.items():
            self.repl.register_hook(
                f"port.{key}",
                "{value}",
                f"Show or set {desc.lower()} (hardware only).",
                lambda ctx, args, k=key: self._hook_port_prop(ctx, args, k),
                source="app",
            )
        self.repl.register_hook(
            "port.flow_control",
            "{mode}",
            "Show or set flow control (none/rtscts/xonxoff/manual).",
            self._hook_port_flow,
            source="app",
        )
        self.repl.register_hook(
            "port.dtr",
            "{0|1}",
            "Show or set DTR line (hardware only).",
            lambda ctx, args: self._hook_port_hw_line(ctx, args, "dtr"),
            source="app",
        )
        self.repl.register_hook(
            "port.rts",
            "{0|1}",
            "Show or set RTS line (hardware only).",
            lambda ctx, args: self._hook_port_hw_line(ctx, args, "rts"),
            source="app",
        )
        for sig, desc in (
            ("cts", "Clear To Send"),
            ("dsr", "Data Set Ready"),
            ("ri", "Ring Indicator"),
            ("cd", "Carrier Detect"),
        ):
            self.repl.register_hook(
                f"port.{sig}",
                "",
                f"Show {desc} state (read-only).",
                lambda ctx, args, s=sig: self._hook_port_signal(ctx, args, s),
                source="app",
            )
        self.repl.register_hook(
            "port.break",
            "{duration_ms}",
            "Send a break signal (default 250ms).",
            self._hook_port_break,
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
            "run.list",
            "",
            "List .run files in the scripts/ directory.",
            self._hook_run_list,
            source="app",
        )
        self.repl.register_hook(
            "demo",
            "",
            "Switch to the built-in demo device.",
            lambda ctx, args: self._start_demo(args),
            source="app",
        )
        self.repl.register_hook(
            "demo.force",
            "",
            "Switch to demo device, overwriting existing config.",
            lambda ctx, args: self._start_demo("--force"),
            source="app",
        )
        self.repl.register_hook(
            "line_no",
            "<on|off>",
            "Toggle line numbers on or off.",
            self._hook_line_no,
            source="app",
        )
        self.repl.register_hook(
            "edit",
            "<filename>",
            "Edit a project file (scripts/proto path).",
            self._hook_edit,
            source="app",
        )
        self.repl.register_hook(
            "edit.cfg",
            "",
            "Edit the current config file.",
            lambda ctx, args: self._hook_edit_cfg(),
            source="app",
        )
        self.repl.register_hook(
            "edit.log",
            "",
            "Open the session log in the system viewer.",
            lambda ctx, args: self._hook_edit_log(),
            source="app",
        )
        self.repl.register_hook(
            "edit.info",
            "",
            "Open the info report in the system viewer.",
            lambda ctx, args: self._hook_edit_info(),
            source="app",
        )
        self.repl.register_hook(
            "cfg.load",
            "<name>",
            "Switch to a different config by name.",
            self._hook_cfg_load,
            source="app",
        )
        self.repl.register_hook(
            "run.load",
            "<filename>",
            "Run a script file (same as /run).",
            self._hook_run,
            source="app",
        )
        self.repl.register_hook(
            "proto.load",
            "<filename>",
            "Run a protocol test script (same as /proto.run).",
            self._hook_proto_load,
            source="app",
        )
        self.repl.register_hook(
            "raw",
            "<text>",
            "Send text to serial with no variable expansion or transforms.",
            lambda ctx, args: self._send_serial_raw(args),
            source="app",
        )
        self.repl.register_hook(
            "help.open",
            "{topic}",
            "Open help file in system viewer.",
            self._hook_help_open,
            source="app",
        )
        # Load external plugins: global first, then per-config (can override)
        self._load_and_report(
            load_plugins_from_dir(global_plugins_dir(), "global"),
        )
        if self.config_path:
            self._load_and_report(
                load_plugins_from_dir(
                    cfg_plugins_dir(self.config_path),
                    Path(self.config_path).stem,
                ),
            )
        self._rebuild_suggester_commands()
        # Open log file (deferred if no config loaded yet)
        self._open_log()
        self._sync_ss_button()
        self._sync_scripts_button()
        self._sync_proto_button()
        self._sync_cap_button()
        for w in self.repl._cfg_data.pop("_config_warnings", []):
            self._status(f"Config warning: {w}", "yellow")
        if self.show_picker_on_start:
            self.push_screen(
                ConfigPicker(
                    self.config_path, read_only=self.cfg.get("config_read_only", False)
                ),
                callback=self._on_config_picked,
            )
        elif self.open_editor_on_start:
            self._new_config()
        elif self.cfg.get("auto_connect"):
            self._connect()
        else:
            self._status(f"{self._port_info_str()} — press Connect to start")
    def on_unmount(self) -> None:
        self._save_history()
        self._disconnect()
        self.reader_stopped.wait(timeout=1.0)
        if self.log_fh:
            self.log_fh.close()
            self.log_fh = None

    def _connect(self, port: str | None = None) -> None:
        if self.is_connected:
            return
        if port:
            self.repl._cfg_data["port"] = port
        # Wait for any previous reader thread to finish
        self.reader_stopped.wait(timeout=0.3)
        self.stop_event.clear()
        self._try_open_port()

    def _try_open_port(self) -> bool:
        """Attempt to open the serial port. Returns True on success."""
        try:
            self.reader_stopped.clear()
            self.ser = open_serial(self.cfg)
            self.notify(
                f"Connected: {self.cfg['port']} @ {self.cfg['baud_rate']}", timeout=0.75
            )
            self._set_conn_status("Connected")
            inp = self.query_one("#cmd", Input)
            inp.placeholder = "REPL:type command, Enter to send"
            inp.focus()
            self._sync_hw_buttons()
            self.read_serial()
            auto_cmd = self.cfg.get("on_connect_cmd", "")
            if auto_cmd:
                self._run_lines(auto_cmd.split("\n"), delay=0.2)
            return True
        except serial.SerialException as e:
            self.reader_stopped.set()
            self._status(f"Serial error: {e}", "red")
            self._set_conn_status("Disconnected")
            if self.cfg.get("auto_reconnect"):
                self._auto_reconnect()
            return False

    @work(thread=True)
    def _auto_reconnect(self) -> None:
        """Background thread: retry connecting every second until success or stop."""
        try:
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
        except RuntimeError:
            pass  # call_from_thread fails during app shutdown

    def _set_hex_mode(self, enabled: bool) -> None:
        """Toggle hex display mode for serial I/O."""
        self._proto_hex_mode = enabled

    def _open_proto_debug(self, path, script) -> None:
        """Open the interactive protocol debug screen.

        Discovers available packet visualizers from built-in, global,
        and per-config ``viz/`` directories.

        Args:
            path: Path to the .pro script file.
            script: Parsed ProtoScript instance.
        """
        # Discover visualizers: built-in → per-config (later overrides)
        visualizers = load_visualizers_from_dir(builtins_viz_dir(), "built-in")
        if self.config_path:
            viz_dir = cfg_data_dir(self.config_path) / "viz"
            visualizers += load_visualizers_from_dir(
                viz_dir, Path(self.config_path).stem
            )

        # Deduplicate by name (later wins), sort by sort_order
        by_name = {v.name: v for v in visualizers}
        final = sorted(by_name.values(), key=lambda v: v.sort_order)

        ctx = self.repl.ctx
        self.push_screen(ProtoDebugScreen(path, ctx, script, final))

    def _serial_read_raw(self, timeout_ms: int = 1000, frame_gap_ms: int = 0) -> bytes:
        """Collect raw bytes from the serial port using timeout-based framing.

        Drains the raw RX queue, accumulating bytes until a silence gap
        indicates a complete frame, or the overall timeout expires.

        Args:
            timeout_ms: Maximum time to wait for a response in milliseconds.
            frame_gap_ms: Silence gap to detect frame end. 0 = use config default.

        Returns:
            Complete frame bytes, or empty bytes on timeout.
        """
        from termapy.protocol import FrameCollector

        frame_gap = frame_gap_ms or self.cfg.get("proto_frame_gap_ms", 50)
        collector = FrameCollector(timeout_ms=frame_gap)
        deadline = time.monotonic() + timeout_ms / 1000.0

        while time.monotonic() < deadline:
            try:
                chunk = self._raw_rx_queue.get(timeout=0.01)
                now = time.monotonic()
                frame = collector.feed(chunk, now)
                if frame is not None:
                    return frame
            except queue.Empty:
                now = time.monotonic()
                frame = collector.flush(now)
                if frame is not None:
                    return frame

        # Final flush in case data arrived right at deadline
        return collector.flush(time.monotonic()) or b""

    def _drain_rx_queue(self) -> int:
        """Discard all pending bytes in the raw RX queue.

        Returns:
            Number of bytes discarded.
        """
        count = 0
        while not self._raw_rx_queue.empty():
            try:
                count += len(self._raw_rx_queue.get_nowait())
            except queue.Empty:
                break
        return count

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
    def _run_lines(
        self,
        cmds: list[str],
        echo_prefix: str = "",
        delay: float = 0,
    ) -> None:
        """Send multiple commands in a background thread.

        Args:
            cmds: Command strings to send.
            echo_prefix: Optional prefix for echoed output.
            delay: Seconds to wait before sending the first command.
        """
        if delay:
            time.sleep(delay)
        self._send_lines(cmds, echo_prefix=echo_prefix)

    def _send_lines(self, lines: list[str], echo_prefix: str = "") -> None:
        """Send multiple commands with cmd_delay_ms between each.

        Routes each line through _dispatch_single, which handles REPL
        prefix detection and serial sending.
        """
        delay_s = self.cfg.get("cmd_delay_ms", 0) / 1000.0
        try:
            for cmd in lines:
                cmd = cmd.strip()
                if not cmd:
                    continue
                if echo_prefix:
                    self.call_from_thread(self._status, f"{echo_prefix}{cmd}")
                self.call_from_thread(self._dispatch_single, cmd)
                if delay_s > 0:
                    time.sleep(delay_s)
                self._wait_for_idle(400)
        except RuntimeError:
            pass  # call_from_thread fails during app shutdown

    def _status(self, text: str, color: str = "dim") -> None:
        """Write a termapy status message with consistent formatting."""
        try:
            self.query_one("#output", RichLog).write(
                Text(text, style=f"bold italic {color}")
            )
        except Exception:
            pass  # widgets gone during shutdown
        self._log_line("#", text)

    def _log_line(self, prefix: str, text: str) -> None:
        """Write a prefixed line to the log file.

        Args:
            prefix: Line prefix (``>`` TX, ``<`` RX, ``#`` status).
            text: Content to log.
        """
        if self.log_fh:
            try:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                self.log_fh.write(f"[{ts}] {prefix} {text}\n")
                self.log_fh.flush()
            except OSError:
                pass

    def _serial_write(self, data: bytes) -> None:
        """Write bytes to serial port and log the TX data.

        All serial output — interactive, scripted, and plugin — flows
        through this method so every TX is logged exactly once.
        """
        enc = self.cfg.get("encoding", "utf-8")
        try:
            text = data.decode(enc).rstrip("\r\n")
        except (UnicodeDecodeError, LookupError):
            text = data.hex(" ")
        self._log_line(">", text)
        if self.ser:
            self.ser.write(data)

    def _confirm(self, message: str) -> bool:
        """Show a Yes/Cancel dialog and block until the user responds.

        THREADING: Must be called from a background thread — NOT the main
        thread. Uses call_from_thread to post the dialog to the main thread,
        then event.wait() blocks the calling thread. If called from the main
        thread, call_from_thread raises RuntimeError. In scripts, this is
        handled as a special case in repl.py run_script() to ensure it runs
        on the background thread.

        Args:
            message: Text to display in the confirmation dialog.

        Returns:
            True if the user clicked Yes, False otherwise.
        """
        result: list[bool] = [False]
        event = Event()

        def _show() -> None:
            def _on_result(confirmed: bool) -> None:
                result[0] = confirmed
                event.set()

            self.push_screen(ConfirmDialog(message), callback=_on_result)

        try:
            self.call_from_thread(_show)
        except RuntimeError:
            self._status("/confirm can only be used in scripts.", "yellow")
            return True
        event.wait()
        return result[0]

    def _write_output_markup(self, text: str) -> None:
        self.query_one("#output", RichLog).write(text)

    def _report_exception(self, e: Exception) -> None:
        """Write exception details to the terminal output in red.

        Displays the exception type, message, filename, and line number
        so that silently swallowed errors become visible during development.

        Args:
            e: The caught exception to report.
        """
        tb = traceback.extract_tb(e.__traceback__)
        if tb:
            last = tb[-1]
            location = f"{last.filename}:{last.lineno}"
        else:
            location = "unknown"
        self._status(f"Exception: {type(e).__name__}: {e} ({location})", "red")

    def _disconnect(self) -> None:
        if self._cap_fh:
            self._cap_stop()
        was_open = self.is_connected
        self.stop_event.set()
        self.reader_stopped.wait(timeout=0.3)
        try:
            if was_open:
                self.notify("Disconnected", severity="warning", timeout=0.75)
            self._set_conn_status("Disconnected")
            try:
                inp = self.query_one("#cmd", Input)
                prefix = self.cfg.get("cmd_prefix", "/")
                inp.placeholder = f"{prefix} for REPL commands, Ctrl+P: palette"
            except Exception:
                pass  # widgets gone during shutdown
            self._sync_hw_buttons(reset=True)
        except Exception as e:
            self._report_exception(e)

    def _sync_hw_visibility(self) -> None:
        """Show or hide DTR/RTS/Break buttons based on flow_control config."""
        show = self.cfg.get("flow_control") == "manual"
        self._btn_dtr.display = show
        self._btn_rts.display = show
        self._btn_break.display = show

    def _switch_config(self, cfg: dict, path: str) -> None:
        """Apply a new config: disconnect, update state, refresh UI, reconnect."""
        self._save_history()
        migrated_from = cfg.pop("_migrated_from", None)
        was_connected = self.is_connected
        if was_connected:
            self._disconnect()
        if migrated_from is not None:
            self._status(
                f"Config migrated: v{migrated_from} → v{CURRENT_CONFIG_VERSION}",
                "yellow",
            )
        for w in cfg.pop("_config_warnings", []):
            self._status(f"Config warning: {w}", "yellow")
        self.repl.replace_cfg(cfg, path)
        self.config_path = path
        self.history = self._load_history()
        self._history_idx = -1
        self.repl.ctx.config_path = path
        self.repl.ctx.ss_dir = self.repl.ss_dir
        self.repl.ctx.scripts_dir = self.repl.scripts_dir
        self.repl.ctx.proto_dir = self.repl.proto_dir
        self.repl.ctx.cap_dir = self.repl.cap_dir
        self._reload_config_plugins(path)
        self._update_title()
        self._apply_border_color()
        self._sync_hw_visibility()
        self._sync_cmd_prefix()
        self._sync_ss_button()
        self._sync_scripts_button()
        self._sync_proto_button()
        self._sync_cap_button()
        self.run_worker(self._sync_custom_buttons())
        self._open_log()
        if was_connected or cfg.get("auto_connect"):
            self._connect()

    def _load_and_report(self, result: LoadResult) -> None:
        """Register loaded plugins/transforms and report status to the terminal.

        Shows loaded plugin names, warnings for skipped files (no COMMAND
        or TRANSFORM), and errors for files that raised exceptions.

        Args:
            result: LoadResult from load_plugins_from_dir.
        """
        loaded = []
        for info in result.plugins:
            self.repl.register_plugin(info)
            loaded.append(info.name)
        for xform in result.transforms:
            self.repl.register_transform(xform)
            loaded.append(f"~{xform.name}")
        for directive in result.directives:
            self.repl.register_directive(directive)
            loaded.append(f"@{directive.name}")
        if loaded:
            self._status(
                f"Loaded {len(loaded)} plugin(s): " + ", ".join(loaded),
                "dim",
            )
        for name in result.skipped:
            self._status(
                f"Skipped {name} — no COMMAND or TRANSFORM (see plugin docs)",
                "yellow",
            )
        for err in result.errors:
            self._status(f"Plugin error: {err}", "red")

    def _reload_config_plugins(self, config_path: str) -> None:
        """Remove old per-config plugins and load plugins for the new config.

        Built-in, global, and app-hook plugins are kept. Only plugins whose
        source is a config name (not "built-in", "global", or "app") are
        removed and replaced with those from the new config's plugins/ dir.

        Args:
            config_path: Path to the new config JSON file.
        """
        keep_sources = {"built-in", "global", "app"}
        to_remove = [
            name
            for name, p in self.repl._plugins.items()
            if p.source not in keep_sources
        ]
        for name in to_remove:
            del self.repl._plugins[name]
        if to_remove:
            self._status(
                f"Unloaded {len(to_remove)} plugin(s): " + ", ".join(to_remove),
                "dim",
            )
        self._load_and_report(
            load_plugins_from_dir(
                cfg_plugins_dir(config_path),
                Path(config_path).stem,
            ),
        )
        self._rebuild_suggester_commands()

    def _start_demo(self, args: str = "") -> None:
        """Set up and switch to the built-in demo device config.

        Args:
            args: Optional ``--force`` to overwrite existing demo config.
        """
        force = "--force" in args.lower()
        self._start_demo_async(force)

    @work(thread=True)
    def _start_demo_async(self, force: bool) -> None:
        """Background thread for demo setup so status messages render."""
        try:
            self.call_from_thread(self._status, "Setting up demo files...", "dim")
            config_path = setup_demo_config(cfg_dir(), force=force)

            self.call_from_thread(self._status, "Loading demo config...", "dim")
            cfg = load_config(str(config_path))

            self.call_from_thread(self._status, "Switching to demo device...", "dim")
            self.call_from_thread(self._switch_config, cfg, str(config_path))

            msg = "Switched to demo device"
            if force:
                msg += " (config reset)"
            self.call_from_thread(self._status, msg, "green")
        except RuntimeError:
            pass  # call_from_thread fails during app shutdown
        except Exception as e:
            self.call_from_thread(
                self._status, f"Failed to load demo config: {e}", "red",
            )

    def _on_config_picked(self, result: tuple | None) -> None:
        if result is None:
            return
        action = result[0]
        if action == "load":
            try:
                cfg = load_config(result[1])
            except Exception as e:
                self._status(f"Failed to load config: {e}", "red")
                return
            self._switch_config(cfg, result[1])
            self._status(f"Loaded config: {result[1]}", "green")
        elif action == "new":
            self._new_config()
        elif action == "edit":
            try:
                cfg = load_config(result[1])
            except Exception as e:
                self._status(f"Failed to load config: {e}", "red")
                return
            self.push_screen(
                ConfigEditor(cfg, result[1]),
                callback=self._on_config_result,
            )

    def _on_config_result(self, result: tuple | None) -> None:
        if result is None:
            return
        new_cfg, new_path = result
        expand_env_cfg(new_cfg)
        config_warnings = validate_config(new_cfg)
        if config_warnings:
            new_cfg["_config_warnings"] = config_warnings
        self._switch_config(new_cfg, new_path)
        if config_warnings:
            detail = "\n".join(config_warnings)
            self.notify(detail, severity="warning", timeout=15)
        self._status(f"Config saved: {new_path}", "green")

    def _port_info_str(self) -> str:
        """Format port info like 'COM4 115200 8N1'."""
        sb = self.cfg.get("stop_bits", 1)
        sb_str = str(int(sb)) if sb == int(sb) else str(sb)
        return (
            f"\\[{self.cfg['port']} {self.cfg['baud_rate']}"
            f" {self.cfg.get('parity', 'N')}{self.cfg.get('byte_size', 8)}{sb_str}]"
        )

    def _update_title(self) -> None:
        title = self.cfg.get("title", "") or self.config_path
        center = self.query_one("#title-center", Button)
        center.label = Text(title)
        # Build informative tooltip
        tip_lines = []
        if self.config_path:
            tip_lines.append(f"File: {self.config_path}")
        tip_lines.append(
            f"Port: {self.cfg.get('port', '?')} @ {self.cfg.get('baud_rate', '?')}"
        )
        fc = self.cfg.get("flow_control", "none")
        if fc != "none":
            tip_lines.append(f"Flow: {fc}")
        enc = self.cfg.get("encoding", "utf-8")
        if enc != "utf-8":
            tip_lines.append(f"Encoding: {enc}")
        if self.cfg.get("auto_connect"):
            tip_lines.append("Autoconnect: on")
        if self.cfg.get("auto_reconnect"):
            tip_lines.append("Auto_reconnect: on")
        if self.cfg.get("os_cmd_enabled"):
            tip_lines.append("OS commands: enabled")
        tip_lines.append("Click to edit config")
        center.tooltip = "\n".join(tip_lines)
        self.query_one("#title-left", Button).label = self._port_info_str()

    def _set_conn_status(self, text: str) -> None:
        try:
            color = "green" if text == "Connected" else "red"
            widget = self.query_one("#title-right", Button)
            widget.label = text
            widget.styles.background = color
            self.query_one("#title-left", Button).styles.background = color
            self.query_one("#title-center", Button).styles.background = color
        except Exception:
            pass  # widgets gone during shutdown

    def _sync_hw_buttons(self, reset: bool = False) -> None:
        """Update DTR/RTS button labels to reflect actual pin state."""
        if reset:
            self._btn_dtr.label = "DTR:0"
            self._btn_rts.label = "RTS:0"
        elif self.is_connected:
            try:
                self._btn_dtr.label = f"DTR:{int(self.ser.dtr)}"
                self._btn_rts.label = f"RTS:{int(self.ser.rts)}"
            except (OSError, serial.SerialException) as e:
                self._report_exception(e)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "title-right":
            if self.is_connected:
                self._disconnect()
            else:
                self._connect()
        elif event.button.id == "title-left":
            self._show_port_picker()
        elif event.button.id == "title-center":
            if self.config_path:
                try:
                    cfg = load_config(self.config_path)
                except Exception as e:
                    self._status(f"Failed to load config: {e}", "red")
                    return
                self.push_screen(
                    ConfigEditor(cfg, self.config_path),
                    callback=self._on_config_result,
                )
            else:
                self.push_screen(
                    ConfigPicker(
                        self.config_path, read_only=self.cfg.get("config_read_only", False)
                    ),
                    callback=self._on_config_picked,
                )
        elif event.button.id == "btn-dtr":
            if self.is_connected:
                try:
                    self.ser.dtr = not self.ser.dtr
                    event.button.label = f"DTR:{int(self.ser.dtr)}"
                except (OSError, serial.SerialException) as e:
                    self._status(f"DTR error: {e}", "red")
        elif event.button.id == "btn-rts":
            if self.is_connected:
                try:
                    self.ser.rts = not self.ser.rts
                    event.button.label = f"RTS:{int(self.ser.rts)}"
                except (OSError, serial.SerialException) as e:
                    self._status(f"RTS error: {e}", "red")
        elif event.button.id == "btn-break":
            if self.is_connected:
                try:
                    self.ser.send_break(duration=0.25)
                    self.notify("Break sent", timeout=1.5)
                except (OSError, serial.SerialException) as e:
                    self._status(f"Break error: {e}", "red")
        elif event.button.id == "btn-cmds":
            self._show_commands()
        elif event.button.id == "btn-help":
            self._hook_help_open(None, "")
        elif event.button.id == "btn-log":
            open_with_system(self._log_path())
        elif event.button.id == "btn-ss-dir":
            self.action_open_screenshot()
        elif event.button.id == "btn-cap-dir":
            self._open_captures_dir()
        elif event.button.id == "btn-scripts":
            self.push_screen(
                ScriptPicker(
                    self.repl.scripts_dir, read_only=self.cfg.get("config_read_only", False)
                ),
                callback=self._on_script_picked,
            )
        elif event.button.id == "btn-proto":
            self.push_screen(
                ProtoPicker(
                    self.repl.proto_dir, read_only=self.cfg.get("config_read_only", False)
                ),
                callback=self._on_proto_picked,
            )
        elif event.button.id == "btn-cfg":
            self.push_screen(
                ConfigPicker(
                    self.config_path, read_only=self.cfg.get("config_read_only", False)
                ),
                callback=self._on_config_picked,
            )
        elif event.button.id == "cap-stop":
            self._cap_stop()
        elif event.button.id == "btn-exit":
            self._disconnect()
            self.exit()
        elif event.button.id and event.button.id.startswith("btn-custom-"):
            self._run_custom_button(event.button.id)

    def _run_custom_button(self, btn_id: str) -> None:
        """Execute the command associated with a custom button."""
        idx = int(btn_id.split("-")[-1])
        buttons = self.cfg.get("custom_buttons", [])
        if idx >= len(buttons):
            return
        raw = buttons[idx].get("command", "").strip()
        if raw:
            self._execute_command(raw)

    def _show_port_picker(self) -> None:
        from serial.tools.list_ports import comports

        ports = sorted(comports(), key=lambda p: p.device)
        if len(ports) == 1:
            self._on_port_picked(ports[0].device)
            return
        self.push_screen(PortPicker(), callback=self._on_port_picked)

    def _update_port(self, port: str) -> None:
        """Change serial port for this session and reconnect.

        Does not write to disk — the config editor is the only path
        that persists changes.  This keeps $(env.NAME) templates intact.
        """
        cfg = dict(self.cfg)
        cfg["port"] = port
        self._switch_config(cfg, self.config_path)
        self._status(f"Port changed to {port} (session)", "green")

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
            ConfigEditor(dict(self.cfg), self.config_path),
            callback=self._on_config_result,
        )

    def _palette_load_config(self) -> None:
        self.push_screen(
            ConfigPicker(self.config_path, read_only=self.cfg.get("config_read_only", False)),
            callback=self._on_config_picked,
        )

    def _palette_new_config(self) -> None:
        self._new_config()

    def _palette_view_log(self) -> None:
        open_with_system(self._log_path())

    def _palette_delete_log(self) -> None:
        """Delete the current session log file after confirmation."""
        log_path = self._log_path()
        if not log_path or not Path(log_path).exists():
            self._status("No log file to delete.", "yellow")
            return

        def on_confirmed(confirmed: bool) -> None:
            if not confirmed:
                return
            # Close the open file handle first
            if self.log_fh:
                self.log_fh.close()
                self.log_fh = None
            try:
                Path(log_path).unlink()
                self._status(f"Deleted {log_path}", "green")
            except OSError as e:
                self._status(f"Delete failed: {e}", "red")

        self.push_screen(
            ConfirmDialog(f"Delete {Path(log_path).name}?"),
            callback=on_confirmed,
        )

    def _palette_clear(self) -> None:
        self.query_one("#output", RichLog).clear()

    def _palette_ss_svg(self) -> None:
        self.repl.dispatch("ss.svg")

    def _palette_ss_txt(self) -> None:
        self.repl.dispatch("ss.txt")

    _HELP_TOPICS = [
        "getting-started", "toolbar", "commands", "config",
        "custom-buttons", "scripting", "protocol-testing",
        "data-capture", "demo",
    ]

    def _hook_help_open(self, ctx: "PluginContext", args: str) -> None:
        """Open a help topic in the system browser."""
        from importlib.resources import files as pkg_files
        html_dir = pkg_files("termapy").joinpath("html")
        topic = args.strip()
        if not topic:
            path = html_dir.joinpath("index.html")
        else:
            # Topic can be "commands", "commands.md", or "commands.html"
            topic = topic.replace(".md", "").replace(".html", "")
            path = html_dir.joinpath(f"{topic}.html")
        if not Path(str(path)).exists():
            self._status(
                f"Unknown topic: {topic!r}. "
                f"Available: {', '.join(self._HELP_TOPICS)}",
                "red",
            )
            return
        open_with_system(str(path))

    def _palette_help(self) -> None:
        self._hook_help_open(None, "")

    def _palette_show_newest_ss(self) -> None:
        path = self._newest_file(self.repl.ss_dir)
        if path:
            open_with_system(str(path))
        else:
            self.notify("No screenshots found.", severity="warning")

    def _palette_show_newest_cap(self) -> None:
        path = self._newest_file(self.repl.cap_dir)
        if path:
            open_with_system(str(path))
        else:
            self.notify("No captures found.", severity="warning")

    @staticmethod
    def _newest_file(directory: Path) -> Path | None:
        """Return the most recently modified file in *directory*, or None."""
        if not directory.exists():
            return None
        files = [
            f for f in directory.iterdir()
            if f.is_file() and not f.name.startswith(".")
        ]
        if not files:
            return None
        return max(files, key=lambda f: f.stat().st_mtime)

    def _palette_exit(self) -> None:
        self._disconnect()
        self.exit()

    @work(thread=True)
    def read_serial(self) -> None:
        """Background thread: read serial port and post to output."""
        try:
            buf = ""
            last_rx = time.monotonic()
            while not self.stop_event.is_set():
                if not self.ser or not self.ser.is_open:
                    break
                try:
                    waiting = self.ser.in_waiting or 1
                    data = self.ser.read(min(waiting, 4096))
                except (serial.SerialException, OSError, AttributeError) as exc:
                    detail = f"{exc.__class__.__name__}: {exc}"
                    if self.cfg.get("show_traceback", False):
                        detail += f"\n{traceback.format_exc()}"
                    self.call_from_thread(
                        self._status,
                        f"Serial read error: {detail}",
                        "red",
                    )
                    self.call_from_thread(
                        self.notify,
                        "Serial disconnected",
                        severity="warning",
                        timeout=1.5,
                    )
                    self.call_from_thread(self._set_conn_status, "Disconnected")
                    if self.cfg.get("auto_reconnect"):
                        self.call_from_thread(self._auto_reconnect)
                    break

                if data:
                    # Feed raw bytes to protocol queue for /proto
                    self._raw_rx_queue.put(data)

                    # Binary capture tap — accumulate raw bytes
                    if self._cap_fh and self._cap_mode == "bin":
                        self._cap_buf.extend(data)
                        if self._cap_target and len(self._cap_buf) >= self._cap_target:
                            # Trim to target
                            self._cap_buf = self._cap_buf[: self._cap_target]
                            try:
                                self.call_from_thread(self._cap_flush_bin)
                                self.call_from_thread(self._cap_stop)
                            except RuntimeError:
                                pass
                        elif len(self._cap_buf) >= 4096:
                            # Periodic flush for large captures
                            try:
                                self.call_from_thread(self._cap_flush_bin)
                            except RuntimeError:
                                pass

                # Suppress normal display while proto script is running
                if self._proto_active:
                    if data:
                        last_rx = time.monotonic()
                        buf = ""
                    continue

                if not data:
                    # Flush partial line only after 200ms of silence
                    if buf and (time.monotonic() - last_rx) >= 0.2:
                        if not PARTIAL_ANSI_RE.search(buf):
                            self.call_from_thread(
                                self._write_batch, [buf])
                            buf = ""
                    continue

                last_rx = time.monotonic()
                text = data.decode(self.cfg.get("encoding", "utf-8"), errors="replace")

                # Insert visible EOL markers before line splitting consumes them
                if self.cfg.get("show_line_endings", False):
                    text = text.replace("\r", _EOL_CR + "\r")
                    text = text.replace("\n", _EOL_LF + "\n")

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
                    self.call_from_thread(self._write_batch, lines)
        except RuntimeError:
            pass  # call_from_thread fails during app shutdown
        finally:
            if self.ser:
                try:
                    self.ser.close()
                except Exception:
                    pass  # port already closed or inaccessible
                self.ser = None
            self.reader_stopped.set()

    def _clear_output(self) -> None:
        self.query_one("#output", RichLog).clear()
        self._line_counter = 0

    def _get_screen_text(self) -> str:
        log = self.query_one("#output", RichLog)
        return "\n".join(strip.text for strip in log.lines)

    # ── File capture engine ──────────────────────────────────────────────────

    def _cap_start(
        self,
        *,
        path: Path,
        file_mode: str,
        mode: str,
        duration: float = 0.0,
        target_bytes: int = 0,
        columns: list | None = None,
        record_size: int = 0,
        sep: str = ",",
        echo: bool = False,
    ) -> bool:
        """Start a file capture session.

        Args:
            path: Output file path (resolved).
            file_mode: File open mode ('a', 'w', 'ab', 'wb').
            mode: 'text' or 'bin'.
            duration: Capture duration in seconds (text mode).
            target_bytes: Target byte count (bin mode).
            columns: Parsed format spec columns (bin mode, None = raw).
            record_size: Bytes per record (bin mode with format spec).
            sep: Column separator for formatted output.
            echo: Print formatted values to terminal (bin mode).

        Returns:
            True if capture started, False on error.
        """
        if self._cap_fh:
            self._status("Capture already active — use .stop first.", "yellow")
            return False

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(path, file_mode, encoding=None if "b" in file_mode else "utf-8")
        except OSError as e:
            self._status(f"Cannot open capture file: {e}", "red")
            return False

        self._cap_fh = fh
        self._cap_path = path
        self._cap_mode = mode
        self._cap_raw = not columns
        self._cap_bytes = 0
        self._cap_columns = columns or []
        self._cap_record_size = record_size
        self._cap_sep = sep
        self._cap_echo = echo
        self._cap_header_written = False
        self._cap_buf = bytearray()

        if mode == "text":
            self._cap_end = time.monotonic() + duration
            self._cap_total = duration
            self._cap_target = 0
            self._cap_timer = self.set_timer(duration, self._cap_stop)
        else:
            self._cap_suppress_display = True
            self._proto_active = True
            self._cap_target = target_bytes
            self._cap_total = float(target_bytes)
            self._cap_end = 0.0
            self._cap_timer = None

        self._cap_show_progress()
        mode_label = "raw" if self._cap_raw else ("fmt" if self._cap_columns else "text")
        self._log_line("#", f"capture start: {path} mode={mode_label}")
        return True

    def _cap_stop(self) -> None:
        """End file capture: flush, close, report, restore UI."""
        if not self._cap_fh:
            return

        # Flush any remaining binary buffer (complete elements only)
        if self._cap_mode == "bin":
            self._proto_active = False
            if self._cap_buf:
                self._cap_flush_bin()

        path = self._cap_path
        byte_count = self._cap_bytes
        raw = self._cap_raw

        try:
            self._cap_fh.close()
        except OSError:
            pass

        # Clear state
        self._cap_fh = None
        self._cap_path = None
        self._cap_mode = ""
        self._cap_raw = False
        self._cap_bytes = 0
        self._cap_target = 0
        self._cap_end = 0.0
        self._cap_total = 0.0
        self._cap_columns = []
        self._cap_record_size = 0
        self._cap_sep = ","
        self._cap_echo = False
        self._cap_header_written = False
        self._cap_buf = bytearray()

        if self._cap_timer:
            self._cap_timer.stop()
            self._cap_timer = None

        self._cap_hide_progress()

        if byte_count > 1024:
            size = f"{byte_count / 1024:.1f} KB"
        else:
            size = f"{byte_count} bytes"
        self._status(f"Capture complete: {path} ({size})", "green")
        self._log_line("#", f"capture end: {path} ({size})")
        self._sync_cap_button()

        # Clear display suppression after a delay so any stale
        # _write_batch calls queued by the reader thread are discarded.
        if self._cap_suppress_display:
            self.set_timer(0.5, self._cap_clear_suppress)

    def _cap_clear_suppress(self) -> None:
        """Clear the binary capture display suppression flag."""
        self._cap_suppress_display = False

    def _cap_flush_bin(self) -> None:
        """Write accumulated binary buffer to file (complete records only)."""
        if not self._cap_fh or not self._cap_buf:
            return

        data = bytes(self._cap_buf)
        if self._cap_raw:
            # Raw binary — write as-is
            try:
                self._cap_fh.write(data)
                self._cap_bytes += len(data)
            except OSError:
                pass
        elif self._cap_record_size > 0:
            # Format spec — convert records to text
            usable = len(data) - (len(data) % self._cap_record_size)
            if usable > 0:
                lines: list[str] = []
                sep = self._cap_sep
                # Write header on first flush
                if not self._cap_header_written:
                    headers, _ = apply_format(
                        data[: self._cap_record_size], self._cap_columns
                    )
                    # Only write header if any column has a name that
                    # differs from its type code (i.e. user gave names)
                    has_names = any(
                        h != col.type_code
                        for h, col in zip(headers, self._cap_columns)
                        if col.type_code != "_"
                    )
                    if has_names:
                        lines.append(sep.join(headers))
                    self._cap_header_written = True

                for offset in range(0, usable, self._cap_record_size):
                    record = data[offset : offset + self._cap_record_size]
                    _, values = apply_format(record, self._cap_columns)
                    lines.append(sep.join(values))

                text = "\n".join(lines) + "\n"
                try:
                    self._cap_fh.write(text)
                    self._cap_bytes += usable
                except OSError:
                    pass
                if self._cap_echo:
                    for line in lines:
                        self._status(line, "dim")
        self._cap_buf.clear()

    def _cap_show_progress(self) -> None:
        """Mount a progress overlay in the bottom bar."""
        try:
            bar = self.query_one("#bottom-bar")
            for child in bar.children:
                child.display = False
            label = Static("", id="cap-label")
            label.styles.width = "1fr"
            stop_btn = Button("Stop", id="cap-stop", variant="error")
            bar.mount(label)
            bar.mount(stop_btn)
            self._cap_progress_timer = self.set_interval(0.5, self._cap_update_progress)
            self.query_one("#cmd", Input).focus()
        except Exception:
            pass

    def _cap_update_progress(self) -> None:
        """Update the capture progress label."""
        if not self._cap_fh:
            return
        try:
            label = self.query_one("#cap-label", Static)
        except Exception:
            return

        path_name = self._cap_path.name if self._cap_path else "?"
        if self._cap_mode == "text":
            remaining = max(0.0, self._cap_end - time.monotonic())
            elapsed = self._cap_total - remaining
            pct = min(100, int(elapsed / self._cap_total * 100)) if self._cap_total > 0 else 100
            label.update(f" Capturing → {path_name}  [{pct}%]  {remaining:.1f}s left  {self._cap_bytes} bytes")
        else:
            pct = min(100, int(self._cap_bytes / self._cap_total * 100)) if self._cap_total > 0 else 0
            label.update(f" Capturing → {path_name}  [{pct}%]  {self._cap_bytes}/{self._cap_target} bytes")

    def _cap_hide_progress(self) -> None:
        """Remove the progress overlay and restore normal buttons."""
        if self._cap_progress_timer:
            self._cap_progress_timer.stop()
            self._cap_progress_timer = None
        try:
            bar = self.query_one("#bottom-bar")
            for widget in bar.query("#cap-label, #cap-stop"):
                widget.remove()
            for child in bar.children:
                child.display = True
            self.query_one("#cmd", Input).focus()
        except Exception:
            pass

    def _write_batch(self, lines: list[str]) -> None:
        """Write a batch of lines to the output log and optional log file.

        Combines screen output and file logging in a single call to
        minimize ``call_from_thread`` round-trips from the serial reader.

        Args:
            lines: Decoded text lines to display and log.
        """
        # Suppress all output during binary capture — binary data decoded
        # as text produces garbled output, ANSI escapes, bells, etc.
        if self._cap_suppress_display:
            return

        log = self.query_one("#output", RichLog)
        show_ts = self.cfg.get("show_timestamps", False)
        show_ln = self._show_line_numbers
        hex_mode = self._proto_hex_mode
        enc = self.cfg.get("encoding", "utf-8")
        for text in lines:
            self._line_counter += 1
            prefix = ""
            if show_ln:
                prefix += f"{self._line_counter:>5} | "
            if show_ts:
                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                prefix += f"[{ts}] "
            if hex_mode:
                hex_str = " ".join(
                    f"{b:02X}"
                    for b in text.encode(enc, errors="replace")
                )
                log.write(Text.from_ansi(f"{prefix}{hex_str}"))
            else:
                log.write(Text.from_ansi(f"{prefix}{text}"))
        for text in lines:
            self._log_line("<", ANSI_RE.sub("", text))

        # Text capture tap — write ANSI-stripped lines to capture file
        if self._cap_fh and self._cap_mode == "text":
            try:
                for text in lines:
                    stripped = ANSI_RE.sub("", text)
                    self._cap_fh.write(stripped + "\n")
                    self._cap_bytes += len(stripped) + 1
                self._cap_fh.flush()
            except OSError:
                pass

    @on(Input.Changed, "#cmd")
    def _on_cmd_changed(self, event: Input.Changed) -> None:
        """Color input red when typing a REPL command."""
        prefix = self.cfg.get("cmd_prefix", "/")
        if event.value.startswith(prefix):
            event.input.add_class("repl-mode")
            event.input.remove_class("var-mode")
        elif event.value.startswith("$"):
            event.input.add_class("var-mode")
            event.input.remove_class("repl-mode")
        else:
            event.input.remove_class("repl-mode")
            event.input.remove_class("var-mode")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Send command to serial port when Enter is pressed."""
        self._hide_history()
        self._history_idx = -1
        cmd = event.value.strip()
        if not cmd:
            if self.cfg.get("send_bare_enter", False):
                self._dispatch_single("")
            return

        # Add to history (remove earlier duplicate, keep most recent)
        if cmd in self.history:
            self.history.remove(cmd)
        self.history.append(cmd)
        if len(self.history) > self._HISTORY_LIMIT:
            self.history.pop(0)
        self._update_suggester()

        self._execute_command(cmd)
        self.query_one("#cmd", Input).value = ""

    def _execute_command(self, cmd: str) -> None:
        """Dispatch a command string, which may contain multiple commands.

        Splits on literal ``\\n`` and real newlines. If multiple commands
        are found, they are executed one per refresh cycle via
        ``call_after_refresh`` so UI updates (like ``/cls``) complete
        before the next command runs.

        Args:
            cmd: Command string (REPL or serial). May contain ``\\n``
                 separators for multi-command sequences.
        """
        # Normalize literal \n and real newlines, then split
        parts = [c.strip() for c in cmd.replace("\\n", "\n").split("\n") if c.strip()]
        if not parts:
            return
        if len(parts) > 1:
            self._execute_sequence(parts)
            return
        self._dispatch_single(parts[0])

    def _execute_sequence(self, cmds: list[str], idx: int = 0) -> None:
        """Execute commands one per refresh cycle.

        Yields control between commands so UI updates are rendered
        and the reader thread's ``call_from_thread`` callbacks can
        be processed before the next command runs.

        Args:
            cmds: List of command strings to execute.
            idx: Current index into the list.
        """
        if idx >= len(cmds):
            return
        self._dispatch_single(cmds[idx])
        if idx + 1 < len(cmds):
            self.call_after_refresh(self._execute_sequence, cmds, idx + 1)

    def _send_serial_raw(self, text: str) -> None:
        """Send text to serial with no transforms or variable expansion.

        Reuses the standard echo / connection-check / line-ending / write
        path but bypasses all transforms.

        Args:
            text: Literal text to send.
        """
        if self.cfg.get("echo_input"):
            fmt = self.cfg.get("echo_input_fmt", "> {cmd}")
            echo_text = text
            if self.cfg.get("show_line_endings", False):
                le = self.cfg.get("line_ending", "\r")
                echo_text += _eol_label(le)
            self._write_output_markup(fmt.replace("{cmd}", echo_text))
        if not self.is_connected:
            self._status("Not connected — command not sent", "red")
            return
        line_ending = self.cfg.get("line_ending", "\r")
        try:
            self._serial_write(
                (text + line_ending).encode(self.cfg.get("encoding", "utf-8"))
            )
        except (OSError, serial.SerialException) as e:
            self._status(f"Send error: {e}", "red")

    def _dispatch_single(self, cmd: str) -> None:
        """Dispatch a single command: REPL prefix goes to REPL, otherwise serial.

        Args:
            cmd: A single command string (no ``\\n`` separators).
        """
        # ── Dispatch directives (meta-syntax, before plugins/transforms) ──
        prefix = self.cfg.get("cmd_prefix", "/")
        if cmd.startswith(prefix + "raw "):
            raw_text = cmd[len(prefix) + 4:]
            self._log_line(">", cmd)
            if self.repl.echo:
                self._write_output_markup(f"[cyan]> {cmd}[/]")
            self._send_serial_raw(raw_text)
            return

        # Pre-dispatch directives (e.g. $(VAR) = value → /var.set)
        result = self.repl.run_directives(cmd)
        if result.action == "rewrite":
            self._log_line(">", cmd)
            if self.repl.echo:
                self._write_output_markup(f"[cyan]> {cmd}[/]")
            self.repl.dispatch(result.payload)
            return
        elif result.action == "warn":
            self._log_line(">", cmd)
            if self.repl.echo:
                self._write_output_markup(f"[cyan]> {cmd}[/]")
            self._status(f"Warning: {result.payload}", "yellow")
            return
        elif result.action == "error":
            self._log_line(">", cmd)
            if self.repl.echo:
                self._write_output_markup(f"[cyan]> {cmd}[/]")
            self._status(f"Error: {result.payload}", "red")
            return

        if cmd.startswith(prefix):
            repl_cmd = cmd[len(prefix):].strip()
            self._log_line(">",f"{prefix}{repl_cmd}")
            if self.repl.echo:
                self._write_output_markup(f"[red]> {prefix}{repl_cmd}[/]")
            if self.repl.has_repl_transforms:
                if not self.repl.command_has_raw_args(repl_cmd):
                    try:
                        repl_cmd = self.repl.transform_repl(repl_cmd)
                    except ValueError as e:
                        self._status(str(e), "red")
                        return
            self.repl.dispatch(repl_cmd)
            return
        # Apply serial transforms
        if self.repl.has_serial_transforms:
            try:
                cmd = self.repl.transform_serial(cmd)
            except ValueError as e:
                self._status(str(e), "red")
                return
        # Echo serial command locally if enabled
        if self.cfg.get("echo_input"):
            fmt = self.cfg.get("echo_input_fmt", "> {cmd}")
            echo_text = cmd
            if self.cfg.get("show_line_endings", False):
                le = self.cfg.get("line_ending", "\r")
                echo_text += _eol_label(le)
            self._write_output_markup(fmt.replace("{cmd}", echo_text))
        if not self.is_connected:
            self._status("Not connected — command not sent", "red")
            return
        line_ending = self.cfg.get("line_ending", "\r")
        try:
            self._serial_write(
                (cmd + line_ending).encode(self.cfg.get("encoding", "utf-8"))
            )
        except (OSError, serial.SerialException) as e:
            self._status(f"Send error: {e}", "red")

        # Clear input
        inp = self.query_one("#cmd", Input)
        inp.value = ""

    def _show_commands(self) -> None:
        """Show the REPL command picker with smart arg handling."""
        popup = self.query_one("#history-popup", OptionList)
        popup.clear_options()
        prefix = self.cfg.get("cmd_prefix", "/")
        groups: dict[str, list] = {}
        for name, plugin in self.repl._plugins.items():
            groups.setdefault(plugin.source, []).append((name, plugin))
        for source, plugins in groups.items():
            popup.add_option(Option(f"── {source} ──", disabled=True))
            for name, plugin in sorted(plugins, key=lambda p: p[0]):
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
        """Handle selection from command picker or palette popup."""
        if event.option_list.id != "history-popup":
            return
        self._hide_history()
        opt_id = str(event.option.id) if event.option.id is not None else ""
        if self._popup_mode == "palette" and opt_id.startswith("palette:"):
            idx = int(opt_id.split(":")[1])
            _, method_name = self.PALETTE_CMDS[idx]
            self.set_timer(0.1, getattr(self, method_name))
        elif opt_id.startswith("run:"):
            name = opt_id.split(":")[1]
            self.call_after_refresh(self.repl.dispatch, name)
        elif opt_id.startswith("repl:"):
            name = opt_id.split(":")[1]
            prefix = self.cfg.get("cmd_prefix", "/")
            inp = self.query_one("#cmd", Input)
            inp.value = f"{prefix}{name} "
            inp.action_end()

    def on_key(self, event) -> None:
        """Handle Up/Down for history cycling, Escape to dismiss popup or clear."""
        if event.key not in ("up", "down", "escape"):
            return

        inp = self.query_one("#cmd", Input)
        popup = self.query_one("#history-popup", OptionList)
        popup_visible = popup.has_class("visible")

        if event.key == "escape":
            if popup_visible:
                self._hide_history()
                event.prevent_default()
            elif self._history_idx != -1:
                self._history_idx = -1
                inp.value = ""
                event.prevent_default()
            return

        if event.key == "up":
            if not inp.has_focus or popup_visible:
                return
            if not self.history:
                return
            if self._history_idx == -1:
                self._history_saved_input = inp.value
                self._history_idx = len(self.history) - 1
            elif self._history_idx > 0:
                self._history_idx -= 1
            inp.value = self.history[self._history_idx]
            inp.action_end()
            event.prevent_default()
            return

        if event.key == "down":
            if not inp.has_focus:
                return
            if popup_visible:
                popup.focus()
                event.prevent_default()
                return
            if self._history_idx == -1:
                return
            self._history_idx += 1
            if self._history_idx >= len(self.history):
                self._history_idx = -1
                inp.value = self._history_saved_input
            else:
                inp.value = self.history[self._history_idx]
            inp.action_end()
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
        if not self.config_path:
            self.notify("No config loaded", severity="warning")
            return
        open_with_system(str(self.repl.ss_dir.resolve()))

    def _open_captures_dir(self) -> None:
        if not self.config_path:
            self.notify("No config loaded", severity="warning")
            return
        open_with_system(str(self.repl.cap_dir.resolve()))

    def _sync_cmd_prefix(self) -> None:
        """Update the command prefix button and input placeholder."""
        prefix = self.cfg.get("cmd_prefix", "/")
        try:
            self.query_one("#btn-cmds", Button).label = prefix
            self.query_one("#cmd", Input).placeholder = (
                f"{prefix} for REPL commands, Ctrl+P: palette"
            )
        except Exception:
            pass
        self.repl.ctx.engine.prefix = prefix

    def _sync_ss_button(self) -> None:
        """Update the SS button tooltip with file counts."""
        btn = self.query_one("#btn-ss-dir", Button)
        ss_dir = self.repl.ss_dir
        if ss_dir.exists():
            svgs = len(list(ss_dir.glob("*.svg")))
            txts = len(list(ss_dir.glob("*.txt")))
            btn.tooltip = f"Open screenshot folder ({svgs} svg, {txts} txt)."
        else:
            btn.tooltip = "Open screenshot folder (empty)."

    def _sync_scripts_button(self) -> None:
        """Update the Scripts button tooltip with file counts."""
        btn = self.query_one("#btn-scripts", Button)
        scripts_dir = self.repl.scripts_dir
        if scripts_dir.exists():
            count = len([f for f in scripts_dir.iterdir() if f.is_file()])
            btn.tooltip = f"Run a script ({count} available)."
        else:
            btn.tooltip = "Run a script (empty)."

    def _sync_proto_button(self) -> None:
        """Update the Proto button tooltip with file counts."""
        btn = self.query_one("#btn-proto", Button)
        proto_dir = self.repl.proto_dir
        if proto_dir.exists():
            count = len(list(proto_dir.glob("*.pro")))
            btn.tooltip = f"Protocol test scripts ({count} available)."
        else:
            btn.tooltip = "Protocol test scripts (empty)."

    def _sync_cap_button(self) -> None:
        """Update the Captures button tooltip with file counts."""
        btn = self.query_one("#btn-cap-dir", Button)
        cap_dir = self.repl.cap_dir
        if cap_dir.exists():
            count = len([f for f in cap_dir.iterdir() if f.is_file()])
            btn.tooltip = f"Open captures folder ({count} files)."
        else:
            btn.tooltip = "Open captures folder (empty)."

    async def _sync_custom_buttons(self) -> None:
        """Remove old custom buttons and create new ones from config."""
        old_buttons = list(self.query(".custom-btn"))
        for old in old_buttons:
            await old.remove()
        log_btn = self.query_one("#btn-log", Button)
        log_btn.styles.margin = (0, 0, 0, 0)
        custom_buttons = self.cfg.get("custom_buttons", [])
        has_custom = False
        for i, cb in enumerate(custom_buttons):
            if not cb.get("enabled", False):
                continue
            has_custom = True
            b = Button(cb.get("name", f"C{i}"), id=f"btn-custom-{i}")
            b.tooltip = cb.get("tooltip", cb.get("name", ""))
            b.add_class("custom-btn")
            self.query_one("#bottom-bar").mount(b, before=log_btn)
        if has_custom:
            log_btn.styles.margin = (0, 0, 0, 2)

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

    def _hook_delay(self, ctx, args: str) -> None:
        try:
            seconds = parse_duration(args)
        except ValueError as e:
            self._status(str(e), "red")
            return
        self.set_timer(seconds, lambda: self._status(f"Delay {args} done."))

    def _hook_line_no(self, ctx, args: str) -> None:
        """Toggle line numbers on or off."""
        arg = args.strip().lower()
        if arg == "on":
            self._show_line_numbers = True
            self._status("Line numbers ON")
        elif arg == "off":
            self._show_line_numbers = False
            self._status("Line numbers OFF")
        else:
            self._status("Usage: line_no on|off", "yellow")

    def _hook_port(self, ctx, args: str) -> None:
        """Open a port by name, or show subcommands if no name given."""
        name = args.strip()
        if not name:
            ctx.write("Usage: /port <name> to switch, or use subcommands:")
            ctx.write("  /port.list          — list available ports")
            ctx.write("  /port.open          — connect (optional port override)")
            ctx.write("  /port.close         — disconnect")
            ctx.write("  /port.info          — show port status and parameters")
            ctx.write("  /port.baud_rate     — show or set baud rate")
            ctx.write("  /port.byte_size     — show or set data bits")
            ctx.write("  /port.parity        — show or set parity")
            ctx.write("  /port.stop_bits     — show or set stop bits")
            ctx.write("  /port.flow_control  — show or set flow control")
            ctx.write("  /port.dtr           — show or set DTR line")
            ctx.write("  /port.rts           — show or set RTS line")
            ctx.write("  /port.cts           — show CTS state (read-only)")
            ctx.write("  /port.dsr           — show DSR state (read-only)")
            ctx.write("  /port.ri            — show RI state (read-only)")
            ctx.write("  /port.cd            — show CD state (read-only)")
            ctx.write("  /port.break         — send break signal (default 250ms)")
            return
        self._update_port(name)

    def _hook_port_list(self, ctx, args: str) -> None:
        """List available serial ports."""
        from serial.tools.list_ports import comports

        ports = sorted(comports(), key=lambda p: p.device)
        if not ports:
            self._status("No serial ports found", "yellow")
            return
        for p in ports:
            desc = p.description or ""
            self._status(f"  {p.device}  {desc}")

    def _hook_port_info(self, ctx, args: str) -> None:
        """Print comprehensive port status."""
        c = self.cfg
        connected = self.is_connected
        state = "connected" if connected else "disconnected"
        ctx.write(f"  Port:         {c.get('port', '?')}  ({state})")
        ctx.write(f"  Baud rate:    {c.get('baud_rate', '?')}")
        sb = c.get("stop_bits", 1)
        sb_str = str(int(sb)) if sb == int(sb) else str(sb)
        ctx.write(
            f"  Frame:        {c.get('byte_size', 8)}"
            f"{c.get('parity', 'N')}{sb_str}"
        )
        ctx.write(f"  Flow control: {c.get('flow_control', 'none')}")
        ctx.write(f"  Encoding:     {c.get('encoding', 'utf-8')}")
        if connected:
            try:
                ctx.write(f"  DTR:          {int(self.ser.dtr)}")
                ctx.write(f"  RTS:          {int(self.ser.rts)}")
                ctx.write(f"  CTS:          {int(self.ser.cts)}")
                ctx.write(f"  DSR:          {int(self.ser.dsr)}")
                ctx.write(f"  RI:           {int(self.ser.ri)}")
                ctx.write(f"  CD:           {int(self.ser.cd)}")
            except (OSError, serial.SerialException):
                pass

    def _hook_port_prop(self, ctx, args: str, key: str) -> None:
        """Get or set a serial port property (hardware only, not saved to config)."""
        attr, coerce, desc, valid = self._PORT_PROPS[key]
        val = args.strip()
        if not val:
            if not self.is_connected:
                ctx.write(f"  {desc}: {self.cfg.get(key, '?')} (disconnected)")
                return
            try:
                ctx.write(f"  {desc}: {getattr(self.ser, attr)}")
            except (OSError, serial.SerialException) as e:
                self._status(f"{desc} read error: {e}", "red")
            return
        if not self.is_connected:
            self._status("Not connected", "yellow")
            return
        try:
            if key == "parity":
                val = val.upper()
            typed = coerce(val)
            if valid and typed not in valid:
                opts = ", ".join(sorted(str(v) for v in valid))
                self._status(f"Invalid {desc.lower()}: {val} (use {opts})", "red")
                return
            setattr(self.ser, attr, typed)
            self.repl._cfg_data[key] = typed
            self._update_title()
            self._status(f"{desc} → {typed}")
        except ValueError:
            self._status(f"Invalid {desc.lower()}: {val}", "red")
        except (OSError, serial.SerialException) as e:
            self._status(f"{desc} error: {e}", "red")

    _FLOW_MODES = VALID_FLOW_CONTROLS

    def _hook_port_flow(self, ctx, args: str) -> None:
        """Get or set flow control mode (hardware only, not saved to config)."""
        val = args.strip().lower()
        if not val:
            fc = self.cfg.get("flow_control", "none")
            suffix = " (disconnected)" if not self.is_connected else ""
            ctx.write(f"  Flow control: {fc}{suffix}")
            return
        if not self.is_connected:
            self._status("Not connected", "yellow")
            return
        if val not in self._FLOW_MODES:
            self._status(
                f"Invalid flow control: {val} (use none/rtscts/xonxoff/manual)", "red"
            )
            return
        try:
            self.ser.rtscts = (val == "rtscts")
            self.ser.xonxoff = (val == "xonxoff")
            self.repl._cfg_data["flow_control"] = val
            self._sync_hw_visibility()
            self._update_title()
            self._status(f"Flow control → {val}")
        except (OSError, serial.SerialException) as e:
            self._status(f"Flow control error: {e}", "red")

    def _hook_port_hw_line(self, ctx, args: str, line: str) -> None:
        """Get or set a hardware line (DTR or RTS)."""
        label = line.upper()
        val = args.strip().lower()
        if not val:
            if not self.is_connected:
                self._status("Not connected", "yellow")
                return
            try:
                ctx.write(f"  {label}: {int(getattr(self.ser, line))}")
            except (OSError, serial.SerialException) as e:
                self._status(f"{label} read error: {e}", "red")
            return
        if not self.is_connected:
            self._status("Not connected", "yellow")
            return
        if val in ("1", "on", "true", "high"):
            state = True
        elif val in ("0", "off", "false", "low"):
            state = False
        else:
            self._status(f"Invalid {label} value: {val} (use 0/1/on/off)", "red")
            return
        try:
            setattr(self.ser, line, state)
            self._sync_hw_buttons()
            self._status(f"{label} → {int(state)}")
        except (OSError, serial.SerialException) as e:
            self._status(f"{label} error: {e}", "red")

    def _hook_port_break(self, ctx, args: str) -> None:
        """Send a break signal on the serial line."""
        if not self.is_connected:
            self._status("Not connected", "yellow")
            return
        val = args.strip()
        duration = 0.25
        if val:
            try:
                duration = int(val) / 1000.0
                if duration <= 0:
                    raise ValueError
            except ValueError:
                self._status("Invalid duration (use milliseconds, e.g. 250)", "red")
                return
        try:
            self.ser.send_break(duration=duration)
            self._status(f"Break sent ({int(duration * 1000)}ms)")
        except (OSError, serial.SerialException) as e:
            self._status(f"Break error: {e}", "red")

    def _hook_port_signal(self, ctx, args: str, signal: str) -> None:
        """Show a read-only input signal (CTS, DSR, RI, CD)."""
        if args.strip():
            self._status(f"{signal.upper()} is read-only", "yellow")
            return
        if not self.is_connected:
            self._status("Not connected", "yellow")
            return
        try:
            ctx.write(f"  {signal.upper()}: {int(getattr(self.ser, signal))}")
        except (OSError, serial.SerialException) as e:
            self._status(f"{signal.upper()} read error: {e}", "red")

    _SERIAL_KEYS = {
        "port",
        "baud_rate",
        "byte_size",
        "parity",
        "stop_bits",
        "flow_control",
    }

    # Maps config key → (pyserial attribute, type coercion, description, valid values)
    _PORT_PROPS = {
        "baud_rate": ("baudrate", int, "Baud rate", None),
        "byte_size": ("bytesize", int, "Data bits", VALID_BYTE_SIZES),
        "parity": ("parity", str, "Parity", VALID_PARITIES),
        "stop_bits": ("stopbits", float, "Stop bits", VALID_STOP_BITS),
    }

    def _refresh_after_cfg(self, key: str, new_val) -> None:
        was_connected = self.is_connected
        if key in self._SERIAL_KEYS and was_connected:
            self._disconnect()
        self._update_title()
        self._apply_border_color()
        self._sync_hw_visibility()
        if key == "cmd_prefix":
            self._sync_cmd_prefix()
        if key == "custom_buttons":
            self.run_worker(self._sync_custom_buttons())
        if key in self._SERIAL_KEYS and was_connected:
            self._connect()

    def _hook_cfg_confirm(self, key: str, new_val) -> None:
        old_val = self.cfg[key]

        def on_result(confirmed: bool) -> None:
            if confirmed:
                self.repl._apply_cfg(key, new_val)

        self.push_screen(CfgConfirm(key, old_val, new_val), callback=on_result)

    def _on_script_picked(self, result: tuple | None) -> None:
        if result is None:
            return
        action = result[0]
        if action == "run":
            clear_vars()
            set_start_time_vars()
            path = self.repl.start_script(result[1])
            if path:
                self._run_script(path)
        elif action == "new":
            self.push_screen(
                ScriptEditor(self.repl.scripts_dir),
                callback=self._on_script_saved,
            )
        elif action == "edit":
            self.push_screen(
                ScriptEditor(self.repl.scripts_dir, result[1]),
                callback=self._on_script_saved,
            )

    def _on_script_saved(self, path: str | None) -> None:
        if path:
            self._status(f"Script saved: {Path(path).name}", "green")
            self._sync_scripts_button()
        self._sync_proto_button()

    def _on_proto_picked(self, result: tuple | None) -> None:
        """Handle result from the ProtoPicker dialog.

        Args:
            result: Tuple action from picker, or None if cancelled.
        """
        if result is None:
            return
        action = result[0]
        if action == "run":
            filename = Path(result[1]).name
            self.repl.dispatch(f"proto.run {filename}")
        elif action == "debug":
            filename = Path(result[1]).name
            self.repl.dispatch(f"proto.debug {filename}")
        elif action == "new":
            self.push_screen(
                ProtoEditor(self.repl.proto_dir),
                callback=self._on_proto_saved,
            )
        elif action == "edit":
            self.push_screen(
                ProtoEditor(self.repl.proto_dir, result[1]),
                callback=self._on_proto_saved,
            )

    def _on_proto_saved(self, path: str | None) -> None:
        """Handle result from the ProtoEditor dialog.

        Args:
            path: Saved file path, or None if cancelled.
        """
        if path:
            self._status(f"Proto script saved: {Path(path).name}", "green")
            self._sync_proto_button()

    def _hook_edit_cfg(self) -> None:
        """Open the config editor modal."""
        self.push_screen(
            ConfigEditor(dict(self.cfg), self.config_path),
            callback=self._on_config_result,
        )

    def _hook_edit_log(self) -> None:
        """Open the session log in the system viewer."""
        open_with_system(self._log_path())

    def _hook_edit_info(self) -> None:
        """Open the info report in the system viewer."""
        if not self.config_path:
            self.repl.write("No config loaded.", "red")
            return
        stem = Path(self.config_path).stem
        path = Path(self.config_path).parent / f"{stem}.md"
        if path.exists():
            open_with_system(str(path))
        else:
            self.repl.write("No info report yet. Run /cfg.info first.", "red")

    def _hook_edit(self, ctx, args: str) -> None:
        """Edit a project file using the same dialogs as the UI menus.

        Routes to ScriptEditor (.run) or ProtoEditor (.pro).

        Args:
            ctx: Plugin context (unused).
            args: Filename (scripts/proto path).
        """
        filename = args.strip()
        if not filename:
            self.repl.write("Usage: /edit <filename>", "red")
            return

        # Resolve prefixed or bare filename
        path = self._resolve_project_file(filename)
        if path is None:
            self.repl.write(f"File not found: {filename}", "red")
            return

        ext = path.suffix.lower()
        if ext == ".run":
            self.push_screen(
                ScriptEditor(self.repl.scripts_dir, str(path)),
                callback=self._on_script_saved,
            )
        elif ext == ".pro":
            self.push_screen(
                ProtoEditor(self.repl.proto_dir, str(path)),
                callback=self._on_proto_saved,
            )

    def _hook_run(self, ctx, args: str) -> None:
        path = self.repl.start_script(args)
        if path:
            self._run_script(path)

    def _hook_cfg_load(self, ctx, args: str) -> None:
        """Switch to a different config by name or path."""
        name = args.strip()
        if not name:
            self.repl.write("Usage: /cfg.load <name>", "red")
            return
        path = Path(name)
        # Try as a bare name: termapy_cfg/<name>/<name>.cfg
        if not path.exists():
            from termapy.config import cfg_path_for_name
            path = cfg_path_for_name(name)
        # Try appending .cfg
        if not path.exists() and not path.suffix:
            path = Path(str(path) + ".cfg")
        if not path.exists():
            self.repl.write(f"Config not found: {name}", "red")
            return
        try:
            from termapy.config import load_config
            cfg = load_config(str(path))
        except Exception as e:
            self.repl.write(f"Failed to load config: {e}", "red")
            return
        self._switch_config(cfg, str(path))
        self._status(f"Loaded config: {path.stem}", "green")

    def _hook_proto_load(self, ctx, args: str) -> None:
        """Run a protocol test script (delegates to /proto.run)."""
        self.repl.dispatch(f"proto.run {args}")

    def _hook_run_list(self, ctx, args: str) -> None:
        """List .run files in the scripts/ directory."""
        d = self.repl.scripts_dir
        if not d.exists():
            self.repl.write("  (no scripts/ directory)", "dim")
            return
        files = sorted(d.glob("*.run"))
        if not files:
            self.repl.write("  (no .run files)", "dim")
            return
        for f in files:
            self.repl.write(f"  {f.name}")

    @work(thread=True)
    def _run_script(self, path: Path) -> None:
        """Threaded wrapper for repl.run_script (needs @work decorator)."""

        def thread_safe_write(text: str, color: str = "dim") -> None:
            self.call_from_thread(self._status, text, color)

        def thread_safe_dispatch(cmd: str) -> None:
            self.call_from_thread(self._dispatch_single, cmd)

        try:
            self.repl.run_script(
                path, write=thread_safe_write, dispatch=thread_safe_dispatch,
            )
        except RuntimeError:
            pass  # call_from_thread fails during app shutdown


def _find_config() -> tuple[str | None, bool]:
    """Find config in termapy_cfg/<name>/<name>.cfg. Returns (path, show_picker).

    - 1 cfg file: (path, False) — auto-load
    - 0 cfg files: (None, False) — show name picker for new config
    - 2+ cfg files: (None, True) — show file picker
    """
    d = cfg_dir()
    migrate_json_to_cfg(d)
    json_files = sorted(d.glob("*/*.cfg"))
    if len(json_files) == 1:
        return str(json_files[0]), False
    if len(json_files) > 1:
        return None, True
    return None, False


def _reset_terminal() -> None:
    """Reset terminal to normal mode after TUI exit.

    Sends escape sequences to disable application cursor keys and other
    modes that Textual may have left on.  On Unix/MSYS, also restores
    cooked terminal mode via ``stty sane``.
    """
    sys.stdout.write(
        "\033[?1l"  # disable application cursor keys
        "\033>"  # disable application keypad
        "\033[?2004l"  # disable bracketed paste
        "\033[?1000l"  # disable mouse tracking
        "\033[!p"  # soft terminal reset (DECSTR)
    )
    sys.stdout.flush()
    try:
        import subprocess

        subprocess.run(["stty", "sane"], timeout=1, capture_output=True)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass


def _run_check(args) -> None:
    """Validate config and print JSON result to stdout (no TUI).

    Read-only — does not migrate or write to disk.
    """
    # Resolve config
    if args.config:
        config_path = args.config
    else:
        found, _ = _find_config()
        if not found:
            print("termapy: no config found. Use --cfg-dir or specify a config.", file=sys.stderr)
            sys.exit(1)
        config_path = found

    try:
        with open(config_path) as f:
            cfg = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        result = {"status": "error", "message": str(e)}
        print(json.dumps(result, indent=2))
        sys.exit(1)

    # Backfill defaults in memory only (no disk write, no migration)
    for key, val in DEFAULT_CFG.items():
        if key not in cfg:
            cfg[key] = val

    warnings = validate_config(cfg)
    if warnings:
        result = {"status": "warn", "warnings": warnings}
    else:
        result = {"status": "ok"}
    print(json.dumps(result, indent=2))


def _run_proto_headless(args) -> None:
    """Run a .pro test script headlessly (no TUI) and write JSON results."""
    from termapy.proto_runner import run_proto_tests

    # Resolve config
    if args.demo:
        from termapy.config import setup_demo_config
        config_path = str(setup_demo_config(cfg_dir()))
    elif args.config:
        config_path = args.config
    else:
        found, _ = _find_config()
        if not found:
            print("termapy: no config found. Use --demo or --cfg-dir.", file=sys.stderr)
            sys.exit(2)
        config_path = found

    try:
        cfg = load_config(config_path)
    except Exception as e:
        print(f"termapy: failed to load config: {e}", file=sys.stderr)
        sys.exit(2)

    # Stash config path for metadata
    cfg["_config_path"] = config_path

    # Resolve .pro file from config's proto/ dir
    proto_dir = Path(config_path).parent / "proto"
    name = args.proto
    if not name.endswith(".pro"):
        name += ".pro"
    pro_path = Path(name)
    if not pro_path.exists():
        pro_path = proto_dir / name
    if not pro_path.exists():
        print(f"termapy: proto file not found: {name}", file=sys.stderr)
        if proto_dir.exists():
            print(f"  (checked {proto_dir})", file=sys.stderr)
        sys.exit(2)

    # Run tests
    template = cfg.get("proto_results_template", "{name}_results.json")
    try:
        results = run_proto_tests(pro_path, cfg, template=template)
    except ValueError as e:
        print(f"termapy: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"termapy: test error: {e}", file=sys.stderr)
        sys.exit(2)

    # Print summary
    s = results["summary"]
    total, passed, failed = s["total"], s["passed"], s["failed"]
    elapsed = s["elapsed_ms"]
    status = "PASS" if failed == 0 else "FAIL"
    print(f"{results['meta']['script_name']}: {passed}/{total} {status} ({elapsed:.0f}ms)")

    sys.exit(0 if failed == 0 else 1)


def main():
    import termapy.config as _cfg_mod

    parser = argparse.ArgumentParser(
        description="TUI serial terminal with ANSI color support"
    )
    parser.add_argument(
        "config",
        nargs="?",
        default=None,
        help="Path to config file (auto-detects single .cfg in termapy_cfg/)",
    )
    parser.add_argument(
        "--cfg-dir",
        default=None,
        help=f"Config directory (default: {CFG_DIR})",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Start with simulated demo device (no hardware needed)",
    )
    parser.add_argument(
        "--proto",
        default=None,
        metavar="NAME",
        help="Run a .pro test script headlessly and write JSON results (no TUI)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate config and print JSON result to stdout (no TUI)",
    )
    args = parser.parse_args()

    if args.cfg_dir:
        _cfg_mod.CFG_DIR = args.cfg_dir

    if args.check:
        _run_check(args)
        return

    if args.proto is not None:
        _run_proto_headless(args)
        return

    if args.demo:
        from termapy.config import setup_demo_config

        config_path = setup_demo_config(cfg_dir(), force=True)
        try:
            cfg = load_config(str(config_path))
        except Exception as e:
            print(f"termapy: failed to load demo config: {e}", file=sys.stderr)
            sys.exit(1)
        app = SerialTerminal(cfg, config_path=str(config_path))
        app.run()
        _reset_terminal()
        return

    if args.config:
        try:
            cfg = load_config(args.config)
        except Exception as e:
            print(
                f"termapy: failed to load config '{args.config}': {e}", file=sys.stderr
            )
            sys.exit(1)
        app = SerialTerminal(cfg, config_path=args.config)
        app.run()
        _reset_terminal()
        return

    config_path, show_picker = _find_config()

    if config_path:
        try:
            cfg = load_config(config_path)
        except Exception as e:
            print(
                f"termapy: failed to load config '{config_path}': {e}", file=sys.stderr
            )
            sys.exit(1)
        app = SerialTerminal(cfg, config_path=config_path)
        app.run()
        _reset_terminal()
    elif show_picker:
        # Multiple json files — start with defaults, show picker on load
        cfg = dict(DEFAULT_CFG)
        app = SerialTerminal(cfg, config_path="", show_picker=True)
        app.run()
        _reset_terminal()
    else:
        # No json files — start with defaults and open editor
        cfg = dict(DEFAULT_CFG)
        app = SerialTerminal(cfg, config_path="", open_editor=True)
        app.run()
        _reset_terminal()


if __name__ == "__main__":
    main()
