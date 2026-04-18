#!/usr/bin/env python3
"""
Usage:
    uv run termapy [config.json]

Runs well in most terminals (Windows Terminal, iTerm2, etc).
VS Code's integrated terminal can be jerky due to its rendering pipeline.
"""

import json
import re
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from threading import Event

import serial
from termapy.config import (
    CURRENT_CONFIG_VERSION,
    cfg_data_dir,
    cfg_dir,
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
# Config-path resolution lives in termapy.config_resolve (Textual-free).
# Re-exported under the original underscored names so existing
# `from termapy.app import _find_config` callers keep working while
# entry.py / cli_flags.py can import from config_resolve directly.
from termapy.config_resolve import (
    find_config as _find_config,
    infer_config_from_run_file as _infer_config_from_run_file,
    resolve_config as _resolve_config,
)
from rich.text import Text
from textual import on, work

from termapy import port_control
from termapy.defaults import DEFAULT_CFG
from termapy.folders import FOLDER_PATTERNS
from termapy.dialogs import (
    CfgConfirm,
    ConfigEditor,
    ConfigPicker,
    ConfirmDialog,
    PortPicker,
    ProtoEditor,
    ProtoPicker,
    QuickSetup,
    ScriptEditor,
    ScriptPicker,
    UpdateAvailableDialog,
)
from termapy.plugins import (
    CapabilitySet,
    LoadResult,
    PluginContext,
    load_plugins_from_dir,
)
from termapy.proto_debug import ProtoDebugScreen
from termapy.protocol_viz import builtins_viz_dir, load_visualizers_from_dir
from termapy.capture import CaptureEngine, CaptureResult
from termapy.serial_engine import SerialEngine
from termapy.serial_port import eol_label
from termapy.repl import ReplEngine
from termapy.terminal_host import TerminalHost
from termapy.plugins import CmdResult
from termapy.scripting import parse_duration
from textual.app import App, ComposeResult
from textual.message import Message
from textual.timer import Timer
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widgets import Button, Input, Label, OptionList, RichLog, Static
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


from termapy.scripting import ANSI_RE  # noqa: E402 - used for log stripping


class SerialTerminal(TerminalHost, App):
    """Textual app: scrolling output + local input line."""

    class ScriptStarted(Message):
        """Posted when a script starts or nests deeper."""

        def __init__(self, stack: list[str]) -> None:
            super().__init__()
            self.stack = stack

    class ScriptProgress(Message):
        """Posted on each script line."""

        def __init__(self, stack: list[str], step: int, total: int) -> None:
            super().__init__()
            self.stack = stack
            self.step = step
            self.total = total

    class ScriptFinished(Message):
        """Posted when a script finishes or returns from a nested call."""

        def __init__(self, stack: list[str]) -> None:
            super().__init__()
            self.stack = stack

    TITLE = "termapy"

    CSS = """
    Tooltip {
        max-width: 80;
    }
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
    #status-bar {
        width: auto;
        max-width: 50%;
        height: 1;
        display: none;
        color: $text-muted;
        content-align: right middle;
        padding: 0 1;
    }
    #status-bar.visible {
        display: block;
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
        Binding("escape", "stop_script", "Stop Script", show=False),
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
        first_run: bool = False,
    ) -> None:
        super().__init__()
        self.switch_to: str | None = None
        self.config_path = config_path
        self.open_editor_on_start = open_editor
        self.show_picker_on_start = show_picker
        self._first_run = first_run
        self.log_fh = None
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
        self._show_line_numbers: bool = cfg.get("show_line_numbers", False)
        self._line_counter: int = 0
        self._xfer_cancel = threading.Event()

        # File capture engine
        self._capture = CaptureEngine(
            on_echo=lambda line: self._status(line, "dim"),
            on_complete=lambda result: self._on_capture_complete(result),
        )

        # Serial engine (owns port, reader, connection state)
        self._engine = SerialEngine(
            cfg=cfg,
            capture=self._capture,
            open_fn=open_serial,
            log=self._log_line,
        )
        self._cap_timer: "Timer | None" = None
        self._cap_progress_timer: "Timer | None" = None
        self._reconnecting = False
        # Set by _btn_exit / on_unmount before we start tearing the UI
        # down, so reader-thread callbacks that are still in flight
        # (already queued via call_from_thread) no-op instead of
        # touching widgets that are being unmounted.  Defense in depth;
        # the query_one sites still have try/except guards.
        self._shutting_down = False

    @property
    def cfg(self):
        """Read-only view of the config dict (single source of truth in ReplEngine)."""
        return self.repl.cfg

    @property
    def engine(self) -> SerialEngine:
        """SerialEngine instance (TerminalHost protocol)."""
        return self._engine

    @property
    def capture(self) -> CaptureEngine:
        """CaptureEngine instance (TerminalHost protocol)."""
        return self._capture

    @property
    def is_connected(self) -> bool:
        return self._engine.is_connected

    @property
    def ser(self):
        """The underlying serial port object (for DTR/RTS/break access)."""
        return self._engine.port_obj

    # -- TerminalHost abstract method implementations -------------------------
    # These satisfy the ABC contract by delegating to the TUI-specific methods
    # that already exist.  In later phases, the internal names may be unified.

    def write(self, text: str, color: str = "") -> None:
        """Write text to the terminal output (TerminalHost protocol)."""
        self._status(text, color or "dim")

    def write_markup(self, text: str) -> None:
        """Write Rich markup to the terminal output (TerminalHost protocol)."""
        self._write_output_markup(text)

    def status(self, text: str, color: str = "") -> None:
        """Write an indented status message (TerminalHost protocol)."""
        self._status(text, color or "dim")

    def _log(self, direction: str, text: str) -> None:
        """Log callback (TerminalHost protocol)."""
        self._log_line(direction, text)

    def _start_reader(self) -> None:
        """Start the background serial reader (TerminalHost protocol)."""
        self._run_reader()

    def _history_path(self) -> str:
        if self.config_path:
            p = Path(self.config_path)
            return str(p.parent / f"{p.stem}.history")
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

        Scans run/ (.run) and proto/ (.pro).
        Skips ss/, plugin/, viz/, and .py files.
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

        Handles prefixed paths (run/foo.run, proto/bar.pro).
        Falls back to extension-based lookup for bare filenames.

        Args:
            name: User input (e.g. "run/demo.run", "test.pro").

        Returns:
            Resolved Path, or None if not found.
        """
        # Prefixed path: "run/foo.run" or "proto/bar.pro"
        dir_map = {
            "run": self.repl.scripts_dir,
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
        for name in self.repl.ctx.ns("target_commands"):
            commands.append(name)
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

            from importlib.metadata import version as _get_version

            try:
                ver = _get_version("termapy")
            except Exception:
                ver = "?"
            help_btn = Button("?", id="btn-help")
            help_btn.tooltip = f"Termapy v{ver} -- Show help guide."
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
            # Hidden at startup; _check_for_updates() unhides this if
            # a newer termapy version is out on PyPI.
            update_btn = Button("Update", id="btn-update", variant="warning")
            update_btn.tooltip = "A newer version of termapy is available."
            update_btn.display = False
            yield update_btn
            yield Static("", id="title-spacer-l")
            center = Button(title, id="title-center")
            center.tooltip = (
                f"Config: {self.config_path or 'none'}\nClick to edit config"
            )
            yield center
            yield Static("", id="title-spacer-r")
            left = Button(port_info, id="title-left")
            left.tooltip = "Click to select serial port."
            yield left
            right = Button("Disconnected", id="title-right")
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
                yield Label("", id="status-bar")

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

    def _show_config_info(self, path: str) -> None:
        """Print config dir, file, and log file paths (verbose only)."""
        if not getattr(self.repl, "ctx", None):
            return
        if not self.repl.ctx.ns("flags")["verbose"]:
            return
        resolved = Path(path).resolve()
        self._status(f"Config dir:  {resolved.parent}", "green")
        self._status(f"Config file: {resolved}", "green")
        xfer_root = self.cfg.get("file_xfer_root", "")
        if xfer_root:
            self._status(f"Xfer root:   {Path(xfer_root).resolve()}", "green")
        else:
            self._status(f"Xfer root:   {resolved.parent / 'cap'}", "green")
        log_path = self._log_path()
        if log_path:
            self._status(f"Log file:    {Path(log_path).resolve()}", "green")

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

    def _apply_border_color(self) -> None:
        """Apply border_color from config to title bar and output border."""
        from termapy.defaults import resolve_color

        color = resolve_color(self.cfg.get("border_color", "") or "blue")
        bar = self.query_one("#title-bar")
        bar.styles.background = color
        self.query_one("#output", RichLog).styles.border = ("solid", color)

    def on_mount(self) -> None:
        self._setup_vars()
        self._apply_border_color()
        self._build_context()
        self._register_tui_hooks()
        self._load_plugins()
        self._run_startup()
        self.repl.fire_lifecycle("on_app_start")
        self._check_for_updates()

    @work(thread=True)
    def _check_for_updates(self) -> None:
        """Background thread: check PyPI for a newer termapy release.

        Silent on any failure, rate-limited to once per 7 days.  If a
        newer version is out, unhides the ``Update`` button in the
        title bar.  Clicking that button opens ``UpdateAvailableDialog``
        which shows the two version numbers and offers an Info button
        linking to the installation help page.

        The title-bar button is an ambient, non-intrusive signal: it
        survives screen-clears and auto-run scripts, never steals
        focus, and disappears once the user has upgraded (next launch
        sees the same version on PyPI and does not unhide).
        """
        try:
            from importlib.metadata import version as _get_version

            from termapy.update_check import check

            current = _get_version("termapy")
            latest = check(current_version=current)
        except Exception:
            return  # any failure -> silent, no user-visible artifact

        if not latest:
            return

        # Stash the version pair so the click handler can pass them
        # to the modal without re-running the check.
        self._pending_update = (current, latest)
        try:
            self.call_from_thread(self._reveal_update_button)
        except RuntimeError:
            pass  # app shutting down

    def _reveal_update_button(self) -> None:
        """Main-thread helper: unhide the title-bar Update button."""
        try:
            self.query_one("#btn-update", Button).display = True
        except Exception:
            pass  # button gone (shutdown) or not yet composed

    def _btn_update(self) -> None:
        """Show the UpdateAvailableDialog; open install docs on Info."""
        pending = getattr(self, "_pending_update", None)
        if not pending:
            return
        current, latest = pending

        def _on_result(action: str | None) -> None:
            if action == "info":
                self._hook_help_open(None, "installation")

        self.push_screen(
            UpdateAvailableDialog(current, latest),
            callback=_on_result,  # ty: ignore[no-matching-overload]
        )

    def _setup_vars(self) -> None:
        """Set launch/context variables for plugin use."""
        from termapy.builtins.plugins.var import (
            register_cfg_vars,
            set_context_var,
            set_launch_var,
        )

        set_launch_var("FRONT_END", "textual")
        set_context_var(
            "CFG", lambda: Path(self.config_path).stem if self.config_path else "none"
        )
        register_cfg_vars(
            get_config_path=lambda: self.config_path,
            get_cfg=lambda: self.cfg,
            get_log_path=lambda: self._log_path(),
        )

    def _build_context(self) -> None:
        """Build PluginContext and EngineAPI, wire to REPL."""
        engine = self._build_engine_api()
        # TUI-specific EngineAPI extensions
        engine.save_cfg = self._hook_cfg_confirm
        engine.open_proto_debug = lambda path, script: self.call_later(
            self._open_proto_debug, path, script
        )
        engine.start_capture = self._cap_start
        engine.stop_capture = self._cap_stop
        engine.directives = self.repl._directives

        ctx = self._build_plugin_context(engine)
        # TUI-specific PluginContext overrides
        ctx.write = self._status
        ctx.write_markup = self._write_output_markup
        ctx.log = self._log_line
        ctx.serial_wait_for_data = lambda timeout_ms=250: (
            self._engine.serial_port.wait_for_data(timeout_ms)
            if self._engine.serial_port
            else False
        )
        ctx.wait_for_match = self.repl.wait_for_match
        ctx.status_bar = self._set_status_bar
        ctx.dispatch = self._dispatch_single
        ctx.notify = lambda text, **kw: self._on_main(self.notify, text, **kw)
        ctx.clear_screen = lambda: self._on_main(self._clear_output)
        ctx.save_screenshot = lambda *a, **kw: self._on_main(
            self.save_screenshot, *a, **kw
        )
        ctx.get_screen_text = lambda: self._on_main(self._get_screen_text)
        ctx.exit_app = lambda: self._on_main(self.exit)
        # TUI environment capabilities.  See CapabilitySet for the full
        # vocabulary.  block_until is NOT set here -- it's provided
        # dynamically by the script runner (see _effective_capabilities).
        ctx.capabilities = CapabilitySet(
            confirm_dialog=True,
            ui_notify=True,
            status_bar=True,
            screen_capture=True,
            tui_mode=True,
        )

        self.repl.set_context(ctx)
        self.repl._after_cfg = self._refresh_after_cfg
        self.ctx = ctx
        self._init_flags(echo=True)

    def _register_tui_hooks(self) -> None:
        """Register TUI-specific commands as plugin hooks."""
        self.repl.register_hook(
            "ss.svg",
            "{name}",
            "Save SVG screenshot. Name defaults to 'screenshot'.",
            self._hook_ss_svg,
            source="app",
            needs=CapabilitySet(screen_capture=True),
        )
        self.repl.register_hook(
            "ss.svg.quiet",
            "{name}",
            "Save SVG screenshot silently (no status message).",
            self._hook_ss_svg_quiet,
            source="app",
            needs=CapabilitySet(screen_capture=True),
        )
        self.repl.register_hook(
            "ss.txt",
            "{name}",
            "Save text screenshot. Name defaults to 'screenshot'.",
            self._hook_ss_txt,
            source="app",
            needs=CapabilitySet(screen_capture=True),
        )
        self.repl.register_hook(
            "delay",
            "<duration>",
            "Wait for duration (e.g. 500ms, 1.5s).",
            self._hook_delay,
            source="app",
        )
        self.repl.register_hook(
            "delay.quiet",
            "<duration>",
            "Wait silently (no output).",
            self._hook_delay_quiet,
            source="app",
        )
        self.repl.register_hook(
            "run",
            "<filename>",
            "Run a script file. Checks scripts/ folder then cwd.",
            self._hook_run,
            source="app",
            flags={
                "--verbose": "Show each command and its result as the script runs.",
                "-v": "--verbose",
            },
        )
        self.repl.register_hook(
            "run.profile",
            "<filename>",
            "Run a script with per-line timing.",
            self._hook_run_profile,
            source="app",
            flags={
                "--verbose": "Show each command and its result as the script runs.",
                "-v": "--verbose",
            },
        )
        self.repl.register_hook(
            "run.profile.show",
            "",
            "Open the newest .prof file in system viewer.",
            self._hook_run_profile_show,
            source="app",
        )
        self.repl.register_hook(
            "run.profile.explore",
            "",
            "Open config directory in file explorer.",
            self._hook_run_profile_explore,
            source="app",
        )
        self.repl.register_hook(
            "run.profile.cmd",
            "<command>",
            "Profile a single command.",
            self._hook_run_profile_cmd,
            source="app",
        )
        self.repl.register_hook(
            "run.profile.dump",
            "{filename}",
            "Print newest (or named) profile to the terminal.",
            self._hook_run_profile_dump,
            source="app",
        )
        self.repl.register_hook(
            "run.profile.list",
            "",
            "List profile (.prof) files.",
            self._hook_run_profile_list,
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
            "cli",
            "",
            "Switch to CLI mode.",
            lambda ctx, args: self._switch_to_cli(),
            source="app",
        )
        self.repl.register_hook(
            "tui",
            "",
            "Already in TUI mode.",
            lambda ctx, args: CmdResult.ok(),
            source="app",
        )
        self.repl.register_hook(
            "line_no",
            "<on|off>",
            "Toggle line numbers on or off.",
            self._hook_line_no,
            source="app",
            needs=CapabilitySet(tui_mode=True),
        )
        # /edit - TUI overrides root (Textual modals for .run/.pro)
        # This wipes all edit.* children from the plugin, so we must
        # re-register every subcommand the TUI wants to expose.
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
            "log.clear",
            "",
            "Delete the session log file.",
            lambda ctx, args: self._tui_hook_log_clear(),
            source="app",
        )
        self.repl.register_hook(
            "edit.info",
            "",
            "Open the info report in the system viewer.",
            lambda ctx, args: self._hook_edit_info(),
            source="app",
        )
        # Re-register folder subcommands (wiped by /edit override)
        from termapy.builtins.plugins.edit import (
            _make_edit_handler,
            _make_list_handler,
            _make_explore_handler,
        )

        for folder, get_dir, ext, pat in (
            ("run", lambda ctx: ctx.scripts_dir, ".run", "*.run"),
            ("proto", lambda ctx: ctx.proto_dir, ".pro", "*.pro"),
            (
                "plugin",
                lambda ctx: Path(ctx.config_path).parent / "plugin"
                if ctx.config_path
                else Path("."),
                ".py",
                "*.py",
            ),
        ):
            # TUI uses Textual modals for run and proto edit
            if folder in ("run", "proto"):
                self.repl.register_hook(
                    f"edit.{folder}",
                    "{{filename}}",
                    f"Edit a {ext} file.",
                    (
                        lambda f=folder, e=ext: lambda ctx,
                        args: self._hook_edit_folder(ctx, args, f, e)
                    )(),
                    source="app",
                )
            else:
                self.repl.register_hook(
                    f"edit.{folder}",
                    "{{filename}}",
                    f"Open a {ext} file in the system editor.",
                    _make_edit_handler(get_dir, ext, pat),
                    source="app",
                )
            self.repl.register_hook(
                f"edit.{folder}.list",
                "",
                f"List {ext} files.",
                _make_list_handler(get_dir, pat),
                source="app",
            )
            self.repl.register_hook(
                f"edit.{folder}.explore",
                "",
                f"Open {folder}/ in file explorer.",
                _make_explore_handler(get_dir),
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
            lambda ctx, args: self._tui_hook_raw(args),
            source="app",
        )
        self.repl.register_hook(
            "help.open",
            "{topic}",
            "Open help file in system viewer.",
            self._hook_help_open,
            source="app",
        )

    def _load_plugins(self) -> None:
        """Load global and per-config external plugins."""
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

    def _run_startup(self) -> None:
        """Open log, sync buttons, and show startup screen."""
        self._open_log()
        if self.config_path:
            self._show_config_info(self.config_path)
        self._sync_all_buttons()
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
        elif self._first_run:
            self.push_screen(
                QuickSetup(title="Welcome to Termapy - Quick Setup"),
                callback=self._on_quick_setup,
            )
        elif self.cfg.get("auto_connect"):
            self._connect()
        else:
            self._status(f"{self._port_info_str()} - press Connect to start")
        self._update_title()
        self.query_one("#cmd", Input).focus()

    def on_unmount(self) -> None:
        self._shutting_down = True
        self.repl.fire_lifecycle("on_app_stop")
        self._save_history()
        self._disconnect()
        self._engine.reader_stopped.wait(timeout=0.2)
        if self.log_fh:
            self.log_fh.close()
            self.log_fh = None

    def _connect(self, port: str | None = None) -> bool:
        if self.is_connected:
            return False
        self._engine.reader_stopped.wait(timeout=0.3)
        return super()._connect(port)

    def _on_connected(self, message: str) -> None:
        """TUI post-connect: notify, update UI, run on_connect_cmd."""
        self.notify(message, timeout=0.75)
        self._set_conn_status("Connected")
        self._update_title()
        inp = self.query_one("#cmd", Input)
        inp.placeholder = "REPL:type command, Enter to send"
        inp.focus()
        self._sync_hw_buttons()
        connect_cmds: list[str] = []
        if self.cfg.get("device_json_cmd", ""):
            connect_cmds.append(self.repl.cmd("include"))
        auto_cmd = self.cfg.get("on_connect_cmd", "")
        if auto_cmd:
            connect_cmds.extend(auto_cmd.replace("\\n", "\n").split("\n"))
        if connect_cmds:
            self._run_lines(connect_cmds, delay=0.2)

    def _on_connect_failed(self) -> None:
        """TUI post-connect failure: update status, auto-reconnect."""
        self._engine.reader_stopped.set()
        self._set_conn_status("Disconnected")
        if self.cfg.get("auto_reconnect"):
            self._auto_reconnect()

    @work(thread=True)
    def _auto_reconnect(self) -> None:
        """Background thread: retry connecting until success or stop."""
        self._engine.stop_event.clear()
        self._reconnecting = True
        try:
            success = self._engine.reconnect_loop(
                on_status=lambda msg: self.call_from_thread(
                    self._set_conn_status, msg, "retry"
                ),
            )
            if success:
                self.call_from_thread(self._connect)
        except RuntimeError:
            pass  # call_from_thread fails during app shutdown
        finally:
            self._reconnecting = False

    def _open_proto_debug(self, path, script) -> None:
        """Open the interactive protocol debug screen.

        Discovers available packet visualizers from built-in, global,
        and per-config ``viz/`` directories.

        Args:
            path: Path to the .pro script file.
            script: Parsed ProtoScript instance.
        """
        # Discover visualizers: built-in -> per-config (later overrides)
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

        Called from a background thread (@work). _status and
        _dispatch_single are both thread-safe.
        """
        delay_s = self.cfg.get("cmd_delay_ms", 0) / 1000.0
        for cmd in lines:
            cmd = cmd.strip()
            if not cmd:
                continue
            if echo_prefix:
                self._status(f"{echo_prefix}{cmd}")
            self._dispatch_single(cmd)
            if delay_s > 0:
                time.sleep(delay_s)
            self._wait_for_idle(400)

    def _on_main(self, fn, *args, **kwargs):
        """Run *fn* on the main thread.  No-op if already there."""
        if self._thread_id == threading.get_ident():
            return fn(*args, **kwargs)
        try:
            return self.call_from_thread(fn, *args, **kwargs)
        except RuntimeError:
            return None  # app shutting down

    def _status(self, text: str, color: str = "dim") -> None:
        """Write a termapy status message with consistent formatting.

        Thread-safe: if called from a background thread (e.g. interactive
        command dispatch), posts the widget update to the main thread.
        """
        if self._thread_id != threading.get_ident():
            try:
                self.call_from_thread(self._status, text, color)
            except RuntimeError:
                pass
            return
        try:
            self.query_one("#output", RichLog).write(
                Text(text, style=f"bold italic {color}")
            )
        except Exception:
            pass  # widgets gone during shutdown
        self._log_line("#", text)

    _status_bar_timer: Timer | None = None

    def _set_status_bar(self, text: str, timeout: float = 5.0) -> None:
        """Show transient text in the bottom status bar.

        The text appears in the status area next to the REPL input,
        sharing 50% of the width.  It auto-clears after *timeout*
        seconds.  Pass empty string to clear immediately.

        Thread-safe: posts to the main thread if called from background.
        """
        if self._thread_id != threading.get_ident():
            try:
                self.call_from_thread(self._set_status_bar, text, timeout)
            except RuntimeError:
                pass
            return
        try:
            label = self.query_one("#status-bar", Label)
            if text:
                label.update(text)
                label.add_class("visible")
                if self._status_bar_timer is not None:
                    self._status_bar_timer.stop()
                self._status_bar_timer = self.set_timer(
                    timeout, self._clear_status_bar
                )
            else:
                self._clear_status_bar()
        except Exception:
            pass  # widgets gone during shutdown

    def _clear_status_bar(self) -> None:
        """Clear the status bar text and hide the widget."""
        try:
            label = self.query_one("#status-bar", Label)
            label.update("")
            label.remove_class("visible")
            self._status_bar_timer = None
        except Exception:
            pass

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
        """Write bytes to serial port and log TX (delegates to SerialPort)."""
        if self._engine.serial_port:
            self._engine.serial_port.write(data)
            self._engine.notify_tx(data)
        else:
            self._log_line(">", data.hex(" "))

    def _confirm(self, message: str) -> bool:
        """Show a Yes/Cancel dialog and block until the user responds.

        THREADING: Must be called from a background thread - NOT the main
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

            self.push_screen(ConfirmDialog(message), callback=_on_result)  # ty: ignore[no-matching-overload]

        try:
            self.call_from_thread(_show)
        except RuntimeError:
            self._status("/confirm can only be used in scripts.", "yellow")
            return True
        event.wait()
        return result[0]

    def _write_output_markup(self, text: str) -> None:
        if self._thread_id != threading.get_ident():
            try:
                self.call_from_thread(self._write_output_markup, text)
            except RuntimeError:
                pass
            return
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
        if self._capture.active:
            self._cap_stop()
        super()._disconnect()

    def _on_disconnected(self) -> None:
        """TUI post-disconnect: notify, update status, title, placeholder, buttons."""
        try:
            self.notify("Disconnected", severity="warning", timeout=0.75)
            self._set_conn_status("Disconnected")
            self._update_title()
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
        self._clear_output()
        self._save_history()
        migrated_from = cfg.pop("_migrated_from", None)
        was_connected = self.is_connected
        if was_connected:
            self._disconnect()
        if migrated_from is not None:
            self._status(
                f"Config migrated: v{migrated_from} -> v{CURRENT_CONFIG_VERSION}",
                "yellow",
            )
        for w in cfg.pop("_config_warnings", []):
            self._status(f"Config warning: {w}", "yellow")
        if not cfg.get("device_json_cmd", ""):
            self.repl.ctx.ns("target_commands").clear()
            self._rebuild_suggester_commands()
        self.repl.ctx.ns("flags")["hex_mode"] = cfg.get("hex_mode", False)
        self._show_line_numbers = cfg.get("show_line_numbers", False)
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
        self._sync_all_buttons()
        self._open_log()
        self.repl.fire_lifecycle("on_config_load")
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
        for hook in result.lifecycle_hooks:
            self.repl.register_lifecycle_hook(hook)
        if loaded:
            self.repl.ctx.status(
                f"Loaded {len(loaded)} plugin(s): " + ", ".join(loaded),
            )
        for name in result.skipped:
            self._status(
                f"Skipped {name} - no COMMAND or TRANSFORM (see plugin docs)",
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
            self.repl.ctx.status(
                f"Unloaded {len(to_remove)} plugin(s): " + ", ".join(to_remove),
            )
        self._load_and_report(
            load_plugins_from_dir(
                cfg_plugins_dir(config_path),
                Path(config_path).stem,
            ),
        )
        self._rebuild_suggester_commands()

    def _switch_to_cli(self) -> CmdResult:
        """Switch to CLI mode - sets flag and exits TUI."""
        self.switch_to = "cli"
        # Stash current config path so the mode loop can pass it to CLI
        self._switch_config_path = self.config_path
        self.exit()
        return CmdResult.ok()

    def _start_demo(self, args: str = "") -> CmdResult:
        """Set up and switch to the built-in demo device config.

        Args:
            args: Optional ``--force`` to overwrite existing demo config.
        """
        force = "--force" in args.lower()
        self._start_demo_async(force)
        return CmdResult.ok()

    @work(thread=True)
    def _start_demo_async(self, force: bool) -> None:
        """Background thread for demo setup so status messages render."""
        try:
            verbose_on = self.repl.ctx.ns("flags")["verbose"]
            if verbose_on:
                self.call_from_thread(self._status, "Setting up demo files...", "dim")
            config_path = setup_demo_config(cfg_dir(), force=force)

            if verbose_on:
                self.call_from_thread(self._status, "Loading demo config...", "dim")
            cfg = load_config(str(config_path))

            if verbose_on:
                self.call_from_thread(
                    self._status, "Switching to demo device...", "dim"
                )
            self.call_from_thread(self._switch_config, cfg, str(config_path))

            msg = "Switched to demo device"
            if force:
                msg += " (config reset)"
            self.call_from_thread(self._status, msg, "green")
        except RuntimeError:
            pass  # call_from_thread fails during app shutdown
        except Exception as e:
            self.call_from_thread(
                self._status,
                f"Failed to load demo config: {e}",
                "red",
            )

    def _confirm_delete(self, path: str, label: str, on_deleted=None) -> None:
        """Show a confirmation dialog and delete a file if confirmed."""
        name = Path(path).name

        def _on_confirm(confirmed: bool) -> None:
            if not confirmed:
                return
            try:
                Path(path).unlink()
                self._status(f"Deleted {label}: {name}", "green")
                if on_deleted:
                    on_deleted()
            except OSError as e:
                self._status(f"Delete failed: {e}", "red")

        self.push_screen(  # ty: ignore[no-matching-overload]
            ConfirmDialog(f"Delete {label} '{name}'?"),
            callback=_on_confirm,
        )

    def _check_port_and_switch(self, cfg: dict, path: str) -> None:
        """If the configured port doesn't exist, prompt with PortPicker."""
        port = cfg.get("port", "")
        try:
            from serial.tools.list_ports import comports

            available = {p.device for p in comports()}
        except Exception:
            available = set()

        if port and (port in available or port.upper() == "DEMO"):
            self._switch_config(cfg, path)
            self._show_config_info(path)
        elif available:
            self._pending_cfg = cfg
            self._pending_config_path = path
            if port:
                self._status(f"Port {port} not found", "yellow")
            self.push_screen(PortPicker(), callback=self._on_load_port_picked)
        else:
            self._switch_config(cfg, path)
            self._show_config_info(path)
            self._status("(no ports found)", "yellow")

    def _on_load_port_picked(self, port: str | None) -> None:
        cfg = self._pending_cfg
        path = self._pending_config_path
        if port is not None:
            cfg["port"] = port
        self._switch_config(cfg, path)
        self._show_config_info(path)

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
            self._check_port_and_switch(cfg, result[1])
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
        elif action == "delete":
            is_active = result[1] == self.config_path

            def _after_delete():
                if is_active:
                    self.config_path = ""
                    self.push_screen(
                        ConfigPicker(""),
                        callback=self._on_config_picked,
                    )

            self._confirm_delete(result[1], "config", on_deleted=_after_delete)

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
        self._show_config_info(new_path)

    def _port_info_str(self) -> str:
        """Format port info like 'COM4 115200 8N1' for title bar.

        Brackets were previously wrapped around the string but interacted
        badly with Rich markup parsing -- the closing ``]`` was consumed
        as a malformed markup tag terminator.  The bare label is cleaner
        and fits the title-bar button without escaping concerns.
        """
        from termapy.config import connection_string

        return connection_string(self.cfg, 'short')

    @staticmethod
    def _format_title_tooltip(
        title: str, kv_pairs: list[tuple[str, object]], action: str
    ) -> str:
        """Render a title-bar tooltip with the shared three-section layout.

        Layout::

            <title>

            key1            = value1
            key2            = value2
            key3            = value3

            Click to: <action>

        Keys are left-aligned and padded so the ``=`` signs line up
        across rows.  Values are formatted with ``_format_tooltip_value``
        so booleans appear as ``ON``/``OFF``, ``None`` becomes ``(none)``,
        and strings with control characters are repr'd.

        Args:
            title: Heading line shown at the top.
            kv_pairs: List of (key, value) tuples for the body.  Order
                preserved.  Empty keys are silently skipped.
            action: Verb fragment shown after ``Click to:`` at the bottom.

        Returns:
            Formatted tooltip string with embedded newlines.
        """
        body_pairs = [(k, v) for k, v in kv_pairs if k]
        key_width = max((len(k) for k, _ in body_pairs), default=0)
        lines: list[str] = [title, ""]
        for key, value in body_pairs:
            display = SerialTerminal._format_tooltip_value(value)
            lines.append(f"{key.ljust(key_width)}  = {display}")
        lines.append("")
        lines.append(f"Click to: {action}")
        return "\n".join(lines)

    @staticmethod
    def _format_tooltip_value(value: object) -> str:
        """Render a config value for display in a tooltip body line.

        Booleans become ``ON``/``OFF`` (matching how the user toggles
        them in config).  ``None`` becomes ``(none)``.  Strings with
        non-printable characters (like ``\\r``) are wrapped in repr()
        so they're visually distinct from regular text.  Numbers and
        plain strings pass through unchanged.
        """
        if value is None:
            return "(none)"
        if isinstance(value, bool):
            return "ON" if value else "OFF"
        if isinstance(value, str):
            if not value:
                return "(empty)"
            if any(not c.isprintable() for c in value):
                return repr(value)
            return value
        return str(value)

    def _update_title(self) -> None:
        # Called from lifecycle hooks (on_mount, _on_connected,
        # _on_config_result) and config switches.  During teardown or
        # before initial mount the title-bar widgets aren't available;
        # bail quietly instead of raising NoMatches into the caller.
        if self._shutting_down:
            return
        try:
            center = self.query_one("#title-center", Button)
        except NoMatches:
            return
        title = self.cfg.get("title", "") or self.config_path
        center.label = Text(title)

        # Cfg button (center) tooltip
        cfg_title = self.cfg.get("title", "") or (
            Path(self.config_path).stem if self.config_path else "Config"
        )
        sb = self.cfg.get("stop_bits", 1)
        sb_str = str(int(sb)) if sb == int(sb) else str(sb)
        frame = (
            f"{self.cfg.get('byte_size', 8)}"
            f"{self.cfg.get('parity', 'N')}{sb_str}"
        )
        cfg_pairs: list[tuple[str, object]] = [
            ("config_path", self.config_path or "(none)"),
            ("port", self.cfg.get("port", "?")),
            ("baud_rate", self.cfg.get("baud_rate", "?")),
            ("frame", frame),
            ("flow_control", self.cfg.get("flow_control", "none")),
            ("encoding", self.cfg.get("encoding", "utf-8")),
            ("line_ending", self.cfg.get("line_ending", "\r")),
            ("on_connect_cmd", self.cfg.get("on_connect_cmd") or None),
            ("auto_connect", bool(self.cfg.get("auto_connect"))),
            ("auto_reconnect", bool(self.cfg.get("auto_reconnect"))),
            ("echo_input", bool(self.cfg.get("echo_input"))),
            ("show_timestamps", bool(self.cfg.get("show_timestamps"))),
            ("os_cmd_enabled", bool(self.cfg.get("os_cmd_enabled"))),
        ]
        center.tooltip = self._format_title_tooltip(
            cfg_title, cfg_pairs, "edit config"
        )

        # Port button (left) tooltip + label
        try:
            port_btn = self.query_one("#title-left", Button)
            port_btn.label = self._port_info_str()
            port_btn.tooltip = self._port_button_tooltip()
        except NoMatches:
            pass

        # Connection status button (right) tooltip
        self._update_conn_tooltip()

    def _port_button_tooltip(self) -> str:
        """Build the title-bar port button tooltip with chip info.

        Uses the shared three-section layout (title / kv body / action).
        The title is the device name from cfg["port"].  The body shows
        chip identification fields from port_control.gather_chip_facts():
        description, manufacturer, model, USB speed, VID:PID, max baud,
        and the in_use status.  Linux-only fields (driver, latency_timer,
        negotiated link speed) appear automatically when available.

        Falls back to a brief "no USB chip info available" body when the
        port is not enumerable (DEMO, unplugged cable, non-USB device).
        """
        from termapy import port_control

        port_name = self.cfg.get("port", "") or "(none)"
        connected = port_name if self.is_connected else ""
        facts = (
            port_control.gather_chip_facts(port_name, connected)
            if port_name and port_name != "(none)"
            else None
        )
        pairs: list[tuple[str, object]] = []
        if facts is not None:
            for field_name in (
                "description",
                "manufacturer",
                "model",
                "usb_speed",
                "vid_pid",
                "serial",
                "negotiated",
                "driver",
                "latency_timer",
                "max_baud",
                "in_use",
            ):
                value = getattr(facts, field_name)
                if value is None:
                    continue
                pairs.append((field_name, value))
        else:
            pairs.append(("status", "no USB chip info available"))
        return self._format_title_tooltip(
            port_name, pairs, "select serial port"
        )

    def _set_conn_status(self, text: str, style: str = "") -> None:
        try:
            colors = {
                "": ("green" if text == "Connected" else "red"),
                "retry": "darkorange",
            }
            color = colors.get(style, colors[""])
            widget = self.query_one("#title-right", Button)
            widget.label = f"{text:^12}"
            widget.styles.background = color
            self.query_one("#title-left", Button).styles.background = color
            self.query_one("#title-center", Button).styles.background = color
            self._update_conn_tooltip(widget)
        except Exception:
            pass  # widgets gone during shutdown

    def _update_conn_tooltip(self, widget: Button | None = None) -> None:
        """Update the connection button tooltip with status and config.

        Uses the shared three-section layout (title / kv body / action).
        Title is the live connection state (Connected / Disconnected).
        Body shows port name and the auto-connect / auto-reconnect flags
        as ON/OFF so the user can discover them without opening config.
        Action verb tracks state: "disconnect" when connected,
        "connect" when not.
        """
        try:
            if widget is None:
                widget = self.query_one("#title-right", Button)
            connected = self.is_connected
            title = "Connected" if connected else "Disconnected"
            action = "disconnect" if connected else "connect"
            pairs: list[tuple[str, object]] = [
                ("port", self.cfg.get("port", "?")),
                ("auto_connect", bool(self.cfg.get("auto_connect"))),
                ("auto_reconnect", bool(self.cfg.get("auto_reconnect"))),
            ]
            widget.tooltip = self._format_title_tooltip(title, pairs, action)
        except Exception:
            pass

    def _sync_hw_buttons(self, reset: bool = False) -> None:
        """Update DTR/RTS button labels to reflect actual pin state."""
        if reset:
            self._btn_dtr.label = "DTR:0"
            self._btn_rts.label = "RTS:0"
        elif self.is_connected:
            try:
                dtr, rts = self._engine.get_hw_state()
                self._btn_dtr.label = f"DTR:{int(dtr)}"
                self._btn_rts.label = f"RTS:{int(rts)}"
            except (OSError, serial.SerialException) as e:
                self._report_exception(e)

    def _btn_title_right(self) -> None:
        if self._reconnecting:
            self._engine.stop_event.set()
            self._set_conn_status("Disconnected")
        elif self.is_connected:
            self._disconnect()
        else:
            self._connect()

    def _btn_title_center(self) -> None:
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

    def _on_btn_dtr(self) -> None:
        if self.is_connected:
            self._serial_op(
                "DTR",
                lambda: setattr(
                    self._btn_dtr, "label", f"DTR:{int(self._engine.toggle_dtr())}"
                ),
            )

    def _on_btn_rts(self) -> None:
        if self.is_connected:
            self._serial_op(
                "RTS",
                lambda: setattr(
                    self._btn_rts, "label", f"RTS:{int(self._engine.toggle_rts())}"
                ),
            )

    def _on_btn_break(self) -> None:
        if self.is_connected:

            def _send():
                self._engine.send_break()
                self.notify("Break sent", timeout=1.5)

            self._serial_op("Break", _send)

    def _btn_scripts(self) -> None:
        self.push_screen(
            ScriptPicker(
                self.repl.scripts_dir, read_only=self.cfg.get("config_read_only", False)
            ),
            callback=self._on_script_picked,
        )

    def _btn_proto(self) -> None:
        self.push_screen(
            ProtoPicker(
                self.repl.proto_dir, read_only=self.cfg.get("config_read_only", False)
            ),
            callback=self._on_proto_picked,
        )

    def _btn_cfg(self) -> None:
        self.push_screen(
            ConfigPicker(
                self.config_path, read_only=self.cfg.get("config_read_only", False)
            ),
            callback=self._on_config_picked,
        )

    def _btn_exit(self) -> None:
        self._shutting_down = True
        self._disconnect()
        self.exit()

    _BUTTON_DISPATCH: dict[str, str] = {
        # Title bar
        "title-right": "_btn_title_right",  # connect / disconnect / cancel reconnect
        "title-left": "_show_port_picker",  # port selection
        "title-center": "_btn_title_center",  # edit config or pick config
        # Hardware control
        "btn-dtr": "_on_btn_dtr",  # toggle DTR pin
        "btn-rts": "_on_btn_rts",  # toggle RTS pin
        "btn-break": "_on_btn_break",  # send serial break
        # Toolbar
        "btn-cmds": "_show_commands",  # command palette
        "btn-help": "_btn_help",  # open help
        "btn-log": "_btn_log",  # open session log
        "btn-ss-dir": "action_open_screenshot",  # open screenshot folder
        "btn-cap-dir": "_open_captures_dir",  # open captures folder
        # Pickers
        "btn-scripts": "_btn_scripts",  # script picker
        "btn-proto": "_btn_proto",  # protocol test picker
        "btn-cfg": "_btn_cfg",  # config picker
        "btn-update": "_btn_update",  # update-available dialog
        # Overlays
        "cap-stop": "_cap_stop",  # stop capture
        "script-stop": "_btn_script_stop",  # stop running script
        # Exit
        "btn-exit": "_btn_exit",
    }

    def _btn_help(self) -> None:
        self._hook_help_open(None, "")

    def _btn_log(self) -> None:
        open_with_system(self._log_path())

    def _btn_script_stop(self) -> None:
        self.repl._script_stop.set()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Route button clicks via _BUTTON_DISPATCH, fall through for custom buttons."""
        btn_id = event.button.id
        handler_name = self._BUTTON_DISPATCH.get(btn_id or "")
        if handler_name:
            getattr(self, handler_name)()
        elif btn_id and btn_id.startswith("btn-custom-"):
            self._run_custom_button(btn_id)

    def _run_custom_button(self, btn_id: str) -> None:
        """Execute the command associated with a custom button.

        Custom button commands support \\n as a multi-command separator.
        """
        idx = int(btn_id.split("-")[-1])
        buttons = self.cfg.get("custom_buttons", [])
        if idx >= len(buttons):
            return
        raw = buttons[idx].get("command", "").strip()
        if not raw:
            return
        parts = [c.strip() for c in raw.replace("\\n", "\n").split("\n") if c.strip()]
        if len(parts) > 1:
            self._dispatch_sequence_on_thread(parts)
        elif parts:
            self._dispatch_on_thread(parts[0])

    def _show_port_picker(self) -> None:
        from serial.tools.list_ports import comports

        ports = sorted(comports(), key=lambda p: p.device)
        if len(ports) == 1:
            self._on_port_picked(ports[0].device)
            return
        self.push_screen(PortPicker(), callback=self._on_port_picked)

    def _update_port(self, port: str) -> None:
        """Change serial port for this session and reconnect.

        Does not write to disk - the config editor is the only path
        that persists changes.  This keeps $(env.NAME) templates intact.
        """
        cfg = dict(self.cfg)
        cfg["port"] = port
        self._switch_config(cfg, self.config_path)
        if self.is_connected:
            self._status(f"Port changed to {port} (session)", "green")
        # Connection failure already reported by _try_open_port

    def _on_port_picked(self, port: str | None) -> None:
        if port is None:
            return
        self._update_port(port)

    # -- Palette action wrappers --

    def _toggle_connection(self) -> None:
        if self._reconnecting:
            self._engine.stop_event.set()
            self._set_conn_status("Disconnected")
        elif self.is_connected:
            self._disconnect()
        else:
            self._connect()

    def _new_config(self) -> None:
        self.push_screen(QuickSetup(), callback=self._on_quick_setup)

    def _on_quick_setup(self, result: tuple | None) -> None:
        if result is None:
            return
        action, name, port, baud, custom_baud = result
        config_path = str(cfg_path_for_name(name))
        cfg = dict(DEFAULT_CFG)
        cfg["title"] = name
        if port:
            cfg["port"] = port
        cfg["baud_rate"] = baud
        cfg["custom_baud"] = custom_baud
        if action == "advanced":
            # Open the full config editor with pre-filled values
            cfg_data_dir(config_path)
            self.push_screen(
                ConfigEditor(cfg, config_path),
                callback=self._on_config_result,
            )
            return
        # Create config dir structure (.gitignore, subdirs) and write config
        cfg_data_dir(config_path)
        with open(config_path, "w") as f:
            json.dump(cfg, f, indent=4)
        self._on_config_result((cfg, config_path))
        if port:
            self._connect()

    def _palette_edit_config(self) -> None:
        self.push_screen(
            ConfigEditor(dict(self.cfg), self.config_path),
            callback=self._on_config_result,
        )

    def _palette_load_config(self) -> None:
        self.push_screen(
            ConfigPicker(
                self.config_path, read_only=self.cfg.get("config_read_only", False)
            ),
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

        self.push_screen(  # ty: ignore[no-matching-overload]
            ConfirmDialog(f"Delete {Path(log_path).name}?"),
            callback=on_confirmed,
        )

    def _palette_clear(self) -> None:
        try:
            self.query_one("#output", RichLog).clear()
        except NoMatches:
            pass

    def _palette_ss_svg(self) -> None:
        self.repl.dispatch("ss.svg")

    def _palette_ss_txt(self) -> None:
        self.repl.dispatch("ss.txt")

    _HELP_TOPICS = [
        "getting-started",
        "toolbar",
        "commands",
        "config",
        "custom-buttons",
        "scripting",
        "protocol-testing",
        "data-capture",
        "writing-plugins",
        "using-git",
        "demo",
    ]

    def _hook_help_open(self, ctx: "PluginContext | None", args: str) -> CmdResult:
        """Open a help topic in the local docs server."""
        from importlib.resources import files as pkg_files

        html_dir = pkg_files("termapy").joinpath("html")
        topic = args.strip()
        if not topic:
            page = "index.html"
        else:
            topic = topic.replace(".md", "").replace(".html", "")
            page = f"{topic}.html"
        if not Path(str(html_dir.joinpath(page))).exists():
            msg = (
                f"Unknown topic: {topic!r}. "
                f"Available: {', '.join(self._HELP_TOPICS)}"
            )
            self._status(msg, "red")
            return CmdResult.fail(msg=msg)
        port = self._ensure_help_server()
        import webbrowser

        webbrowser.open(f"http://127.0.0.1:{port}/{page}")
        return CmdResult.ok()

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
            f for f in directory.iterdir() if f.is_file() and not f.name.startswith(".")
        ]
        if not files:
            return None
        return max(files, key=lambda f: f.stat().st_mtime)

    def _palette_exit(self) -> None:
        self._disconnect()
        self.exit()

    @work(thread=True)
    def _run_reader(self) -> None:
        """Background thread: delegates to SerialEngine.read_loop."""

        def on_error(detail: str) -> None:
            self.call_from_thread(self._status, f"Serial read error: {detail}", "red")

        def on_disconnect() -> None:
            self.call_from_thread(
                self.notify,
                "Serial disconnected",
                severity="warning",
                timeout=1.5,
            )
            self.call_from_thread(self._set_conn_status, "Disconnected")
            if self.cfg.get("auto_reconnect"):
                self.call_from_thread(self._auto_reconnect)

        try:
            self._engine.read_loop(
                on_lines=lambda lines: self.call_from_thread(self._write_batch, lines),
                on_clear=lambda: self.call_from_thread(self._clear_output),
                on_capture_done=lambda: self.call_from_thread(self._cap_stop),
                on_error=on_error,
                on_disconnect=on_disconnect,
            )
        except RuntimeError:
            pass  # call_from_thread fails during app shutdown

    def _clear_output(self) -> None:
        if self._shutting_down:
            return
        try:
            self.query_one("#output", RichLog).clear()
        except NoMatches:
            return
        self._line_counter = 0

    def _get_screen_text(self) -> str:
        if self._shutting_down:
            return ""
        try:
            log = self.query_one("#output", RichLog)
        except NoMatches:
            return ""
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
        hex_mode: bool = False,
        timeout: float = 0.0,
    ) -> bool:
        """Start a file capture session (delegates to CaptureEngine)."""
        if self._capture.active:
            self._status("Capture already active - use .stop first.", "yellow")
            return False

        started = self._capture.start(
            path=path,
            file_mode=file_mode,
            mode=mode,
            duration=duration,
            target_bytes=target_bytes,
            columns=columns,
            record_size=record_size,
            sep=sep,
            echo=echo,
            hex_mode=hex_mode,
            timeout=timeout,
        )
        if not started:
            self._status(f"Cannot open capture file: {path}", "red")
            return False

        if mode == "text":
            self._cap_timer = self.set_timer(duration, self._cap_stop)
        else:
            self._engine.proto_active = True
            if timeout > 0:
                self._cap_timer = self.set_timer(timeout, self._cap_stop)
            else:
                self._cap_timer = None

        self._cap_show_progress()
        raw = not columns
        mode_label = "raw" if raw else ("fmt" if columns else "text")
        self._log_line("#", f"capture start: {path} mode={mode_label}")
        return True

    def _cap_stop(self) -> None:
        """End file capture: delegate to engine, restore UI."""
        if not self._capture.active:
            return

        result = self._capture.stop()

        if not self.repl.in_script:
            self._engine.proto_active = False

        if self._cap_timer:
            self._cap_timer.stop()
            self._cap_timer = None

        self._cap_hide_progress()

        if result:
            self._status(
                f"Capture complete: {result.path} ({result.size_label})", "green"
            )
            self._log_line("#", f"capture end: {result.path} ({result.size_label})")
        self._sync_cap_button()

    def _on_capture_complete(self, result: CaptureResult) -> None:
        """Called by CaptureEngine when capture finishes (unused for now)."""
        pass

    def _cap_show_progress(self) -> None:
        """Mount a progress overlay in the bottom bar."""
        if self.repl.in_script:
            return  # script overlay owns the bar
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
            inp = self.query_one("#cmd", Input)
            inp.disabled = True
            inp.focus()
        except Exception:
            pass

    def _cap_update_progress(self) -> None:
        """Update the capture progress label."""
        prog = self._capture.get_progress()
        if not prog:
            return
        try:
            label = self.query_one("#cap-label", Static)
        except Exception:
            return
        if prog.mode == "text":
            label.update(
                f" Capturing -> {prog.path_name}  [{prog.pct}%]  "
                f"{prog.remaining_s:.1f}s left  {prog.bytes_captured} bytes"
            )
        else:
            label.update(
                f" Capturing -> {prog.path_name}  [{prog.pct}%]  "
                f"{prog.bytes_captured}/{prog.target_bytes} bytes"
            )

    def _cap_hide_progress(self) -> None:
        """Remove the capture overlay and restore normal buttons."""
        if self._cap_progress_timer:
            self._cap_progress_timer.stop()
            self._cap_progress_timer = None
        if self.repl.in_script:
            return  # script overlay owns the bar
        try:
            bar = self.query_one("#bottom-bar")
            for widget in bar.query("#cap-label, #cap-stop"):
                widget.remove()
            for child in bar.children:
                child.display = True
            inp = self.query_one("#cmd", Input)
            inp.disabled = False
            inp.focus()
        except Exception:
            pass

    def _write_batch(self, lines: list[str]) -> None:
        """Write a batch of lines to the output log and optional log file.

        Combines screen output and file logging in a single call to
        minimize ``call_from_thread`` round-trips from the serial reader.

        Short-circuits during shutdown: reader callbacks can still be
        queued on the event loop when Textual starts tearing down
        widgets.  Skip both the RichLog write and the log-file write
        once ``_shutting_down`` is set so we don't touch a vanished
        widget tree.

        Args:
            lines: Decoded text lines to display and log.
        """
        if self._shutting_down:
            return
        try:
            log = self.query_one("#output", RichLog)
        except NoMatches:
            return  # widgets torn down between flag check and query
        show_ts = self.cfg.get("show_timestamps", False)
        show_ln = self._show_line_numbers
        hex_mode = self.repl.ctx.ns("flags")["hex_mode"]
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
                    f"{b:02X}" for b in text.encode(enc, errors="replace")
                )
                log.write(Text.from_ansi(f"{prefix}{hex_str}"))
            else:
                log.write(Text.from_ansi(f"{prefix}{text}"))
        for text in lines:
            self._log_line("<", ANSI_RE.sub("", text))

        # Expect watcher tap - feed lines to script pattern matcher
        self.repl.feed_lines(lines)

        # Text capture tap - feed ANSI-stripped lines to capture engine
        if self._capture.active and self._capture.mode == "text":
            stripped = [ANSI_RE.sub("", t) for t in lines]
            self._capture.feed_text(stripped)

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
                self._dispatch_on_thread("")
            return

        # Add to history (remove earlier duplicate, keep most recent)
        if cmd in self.history:
            self.history.remove(cmd)
        self.history.append(cmd)
        if len(self.history) > self._HISTORY_LIMIT:
            self.history.pop(0)
        self._update_suggester()

        inp = self.query_one("#cmd", Input)
        inp.value = ""
        self._saved_placeholder = inp.placeholder
        inp.placeholder = "Running... escape to cancel"
        self._dispatch_on_thread_interactive(cmd)

    def _restore_input_placeholder(self) -> None:
        """Restore the command input placeholder after execution."""
        inp = self.query_one("#cmd", Input)
        inp.placeholder = self._saved_placeholder

    # -- Background dispatch gateway --------------------------------------------
    # One rule: every user command goes through a @work(thread=True) method
    # so blocking handlers (xmodem, ymodem, etc.) never freeze the TUI.

    @work(thread=True)
    def _dispatch_on_thread(self, cmd: str) -> None:
        """Run a command on a background thread."""
        try:
            self._dispatch_single(cmd)
        except RuntimeError:
            pass

    @work(thread=True)
    def _dispatch_on_thread_interactive(self, cmd: str) -> None:
        """Background dispatch for interactive input (restores placeholder)."""
        try:
            self._dispatch_single(cmd)
        except RuntimeError:
            pass
        try:
            self.call_from_thread(self._restore_input_placeholder)
        except RuntimeError:
            pass

    @work(thread=True)
    def _dispatch_sequence_on_thread(self, cmds: list[str]) -> None:
        """Run a sequence of commands on a background thread."""
        for cmd in cmds:
            try:
                self._dispatch_single(cmd)
            except RuntimeError:
                break
            time.sleep(0.05)

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
                echo_text += eol_label(le)
            self._write_output_markup(fmt.replace("{cmd}", echo_text))
        if not self.is_connected:
            self._status("Not connected.", "red")
            return
        line_ending = self.cfg.get("line_ending", "\r")
        self._serial_op(
            "Send",
            lambda: self._serial_write(
                (text + line_ending).encode(self.cfg.get("encoding", "utf-8"))
            ),
        )

    def _tui_hook_raw(self, text: str) -> CmdResult:
        """Hook wrapper for /raw - sends text with no transforms."""
        self._send_serial_raw(text)
        return CmdResult.ok()

    def _dispatch_single(self, cmd: str) -> CmdResult:
        """Dispatch a single command (delegates to repl.dispatch_full).

        Safe to call from any thread - output helpers (_status,
        _write_output_markup) detect their thread internally.
        """
        return self.repl.dispatch_full(
            cmd,
            log=self._log_line,
            echo_markup=self._write_output_markup,
            status=self._status,
            serial_write=self._serial_write,
            serial_write_raw=self._send_serial_raw,
            is_connected=lambda: self.is_connected,
            eol_label=eol_label,
        )

    # Column at which the "# help text" comment starts in the /
    # popup.  Short command+args lines are left-padded to this
    # column; longer lines break the comment onto a new continuation
    # line indented to the same column.  Tuned so the three or four
    # most common commands fit on one line without wrap.
    _CMDS_HELP_COL = 50

    # Regex matching help strings that flag a command as Linux-only
    # (e.g. "Linux only", "Linux + FTDI only", "only on linux").
    # Case-insensitive.  Used to hide those commands from the /
    # popup on non-Linux platforms.
    _LINUX_ONLY_RE = re.compile(r"linux[^.\n]*only|only[^.\n]*linux", re.IGNORECASE)

    def _show_commands(self) -> None:
        """Show the REPL command picker with smart arg handling.

        Hides Linux-only commands on non-Linux platforms (detected by
        scanning each plugin's help text for "linux ... only").
        Right-pads the command+args portion so the "# help text"
        comment starts at a consistent column; if the prefix is too
        long to fit, the comment wraps to a new continuation line
        indented to the same column.
        """
        popup = self.query_one("#history-popup", OptionList)
        popup.clear_options()
        prefix = self.cfg.get("cmd_prefix", "/")
        skip_linux_only = sys.platform != "linux"

        def _add_row(cmd_text: str, help_text: str, option_id: str) -> None:
            """Build a Text row with the help column aligned to _CMDS_HELP_COL.

            If ``cmd_text`` is shorter than the target column, pad
            with spaces.  If it's longer, break and indent the
            help text on the next line so the comment column
            stays aligned.
            """
            label = Text(cmd_text)
            pad = self._CMDS_HELP_COL - len(cmd_text)
            if pad > 0:
                label.append(" " * pad)
                label.append(f"# {help_text}", style="dim")
            else:
                label.append("\n")
                label.append(" " * self._CMDS_HELP_COL)
                label.append(f"# {help_text}", style="dim")
            popup.add_option(Option(label, id=option_id))

        groups: dict[str, list] = {}
        for name, plugin in self.repl._plugins.items():
            if skip_linux_only and self._LINUX_ONLY_RE.search(plugin.help or ""):
                continue
            groups.setdefault(plugin.source, []).append((name, plugin))
        for source, plugins in groups.items():
            popup.add_option(Option(f"── {source} ──", disabled=True))
            for name, plugin in sorted(plugins, key=lambda p: p[0]):
                has_required = "<" in plugin.args if plugin.args else False
                has_optional = "{" in plugin.args if plugin.args else False
                if not plugin.args or (has_optional and not has_required):
                    _add_row(f"{prefix}{name}", plugin.help, f"run:{name}")
                if plugin.args:
                    _add_row(
                        f"{prefix}{name} {plugin.args}",
                        plugin.help,
                        f"repl:{name}",
                    )
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
            prefix = self.cfg.get("cmd_prefix", "/")
            self._dispatch_on_thread(f"{prefix}{name}")
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
        # Don't intercept keys when a modal screen is active
        if len(self.screen_stack) > 1:
            return

        inp = self.query_one("#cmd", Input)
        popup = self.query_one("#history-popup", OptionList)
        popup_visible = popup.has_class("visible")

        if event.key == "escape":
            if self._engine.proto_active:
                self._xfer_cancel.set()
                event.prevent_default()
            elif popup_visible:
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

    def action_stop_script(self) -> None:
        """Stop a running script or repeat (Escape key)."""
        self.repl._script_stop.set()

    def action_clear_log(self) -> None:
        try:
            self.query_one("#output", RichLog).clear()
        except NoMatches:
            pass

    def action_screenshot(
        self, filename: str | None = None, path: str | None = None
    ) -> None:
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
            self.query_one(
                "#cmd", Input
            ).placeholder = f"{prefix} for REPL commands, Ctrl+P: palette"
        except Exception:
            pass
        self.repl.ctx.engine.prefix = prefix

    def _sync_all_buttons(self) -> None:
        """Refresh all file-count button tooltips and custom buttons."""
        self._sync_ss_button()
        self._sync_scripts_button()
        self._sync_proto_button()
        self._sync_cap_button()
        self.run_worker(self._sync_custom_buttons())

    @staticmethod
    def _count_files(directory: Path, pattern: str) -> int:
        """Count files matching a glob pattern in a directory."""
        if not directory.exists():
            return 0
        return len(list(directory.glob(pattern)))

    def _sync_ss_button(self) -> None:
        """Update the SS button tooltip with file counts."""
        btn = self.query_one("#btn-ss-dir", Button)
        ss_dir = self.repl.ss_dir
        svgs = self._count_files(ss_dir, "*.svg")
        txts = self._count_files(ss_dir, "*.txt")
        if svgs or txts:
            btn.tooltip = f"Open screenshot folder ({svgs} svg, {txts} txt)."
        else:
            btn.tooltip = "Open screenshot folder (empty)."

    def _sync_scripts_button(self) -> None:
        """Update the Scripts button tooltip with file counts."""
        btn = self.query_one("#btn-scripts", Button)
        count = self._count_files(self.repl.scripts_dir, FOLDER_PATTERNS["run"])
        btn.tooltip = (
            f"Run a script ({count} available)." if count else "Run a script (empty)."
        )

    def _sync_proto_button(self) -> None:
        """Update the Proto button tooltip with file counts."""
        btn = self.query_one("#btn-proto", Button)
        count = self._count_files(self.repl.proto_dir, FOLDER_PATTERNS["proto"])
        btn.tooltip = (
            f"Protocol test scripts ({count} available)."
            if count
            else "Protocol test scripts (empty)."
        )

    def _sync_cap_button(self) -> None:
        """Update the Captures button tooltip with file counts."""
        btn = self.query_one("#btn-cap-dir", Button)
        count = self._count_files(self.repl.cap_dir, FOLDER_PATTERNS["cap"])
        btn.tooltip = (
            f"Open captures folder ({count} files)."
            if count
            else "Open captures folder (empty)."
        )

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

    def _hook_ss_svg(self, ctx, args: str) -> CmdResult:
        base = args.strip() or "screenshot"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = str((self.repl.ss_dir / f"{base}_{ts}.svg").resolve())
        self._on_main(self.save_screenshot, path)
        self.last_screenshot = path
        self._status(f"SVG screenshot saved: {path}", "green")
        self._on_main(self._sync_ss_button)
        return CmdResult.ok()

    def _hook_ss_svg_quiet(self, ctx, args: str) -> CmdResult:
        name = args.strip() or "screenshot"
        if not name.endswith(".svg"):
            name += ".svg"
        path = str((self.repl.ss_dir / name).resolve())
        self._on_main(self.save_screenshot, path)
        self.last_screenshot = path
        self._on_main(self._sync_ss_button)
        return CmdResult.ok()

    def _hook_ss_txt(self, ctx, args: str) -> CmdResult:
        base = args.strip() or "screenshot"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = str((self.repl.ss_dir / f"{base}_{ts}.txt").resolve())
        text = self._on_main(self._get_screen_text)
        Path(path).write_text(text or "", encoding="utf-8")
        self.last_screenshot = path
        self._status(f"Text screenshot saved: {path}", "green")
        self._on_main(self._sync_ss_button)
        return CmdResult.ok()

    def _hook_delay(self, ctx, args: str) -> CmdResult:
        try:
            seconds = parse_duration(args)
        except ValueError as e:
            self._status(str(e), "red")
            return CmdResult.fail(msg=str(e))
        self._on_main(
            self.set_timer, seconds, lambda: self._status(f"Delay {args} done.")
        )
        return CmdResult.ok()

    def _hook_delay_quiet(self, ctx, args: str) -> CmdResult:
        """Wait silently - non-blocking timer, no output."""
        try:
            seconds = parse_duration(args)
        except ValueError as e:
            self._status(str(e), "red")
            return CmdResult.fail(msg=str(e))
        self._on_main(self.set_timer, seconds, lambda: None)
        return CmdResult.ok()

    def _hook_line_no(self, ctx, args: str) -> CmdResult:
        """Toggle line numbers on or off."""
        arg = args.strip().lower()
        if arg == "on":
            self._show_line_numbers = True
            self._status("Line numbers ON")
            return CmdResult.ok()
        elif arg == "off":
            self._show_line_numbers = False
            self._status("Line numbers OFF")
            return CmdResult.ok()
        else:
            self._status("Usage: line_no on|off", "yellow")
            return CmdResult.fail(msg="Usage: line_no on|off")

    def _apply_port_effects(self, effects: dict) -> None:
        """Apply side effects from a port_control function (used by port plugin)."""
        if effects.get("cfg_update"):
            for key, val in effects["cfg_update"].items():
                self.repl._cfg_data[key] = val
        if effects.get("update_title"):
            self._update_title()
        if effects.get("sync_hw"):
            self._sync_hw_visibility()
            self._sync_hw_buttons()

    def _refresh_after_cfg(self, key: str, new_val) -> None:
        was_connected = self.is_connected
        if key in port_control.SERIAL_KEYS and was_connected:
            self._disconnect()
        self._update_title()
        self._apply_border_color()
        self._sync_hw_visibility()
        if key == "cmd_prefix":
            self._sync_cmd_prefix()
        if key == "custom_buttons":
            self.run_worker(self._sync_custom_buttons())
        if key in port_control.SERIAL_KEYS and was_connected:
            self._connect()

    def _hook_cfg_confirm(self, key: str, new_val) -> None:
        old_val = self.cfg[key]

        def on_result(confirmed: bool) -> None:
            if confirmed:
                self.repl._apply_cfg(key, new_val)

        self._on_main(
            self.push_screen, CfgConfirm(key, old_val, new_val), callback=on_result
        )

    def _on_script_picked(self, result: tuple | None) -> None:
        if result is None:
            return
        action = result[0]
        if action == "run":
            from termapy.builtins.plugins.var import clear_vars, set_start_time_vars

            clear_vars()
            set_start_time_vars()
            path, _ = self.repl.start_script(result[1])
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
        elif action == "delete":
            self._confirm_delete(
                result[1],
                "script",
                on_deleted=self._sync_scripts_button,
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
            prefix = self.cfg.get("cmd_prefix", "/")
            self._dispatch_on_thread(f"{prefix}proto.run {filename}")
        elif action == "debug":
            filename = Path(result[1]).name
            prefix = self.cfg.get("cmd_prefix", "/")
            self._dispatch_on_thread(f"{prefix}proto.debug {filename}")
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
        elif action == "delete":
            self._confirm_delete(
                result[1],
                "proto script",
                on_deleted=self._sync_proto_button,
            )

    def _on_proto_saved(self, path: str | None) -> None:
        """Handle result from the ProtoEditor dialog.

        Args:
            path: Saved file path, or None if cancelled.
        """
        if path:
            self._status(f"Proto script saved: {Path(path).name}", "green")
            self._sync_proto_button()

    def _hook_edit_cfg(self) -> CmdResult:
        """Open the config editor modal."""
        self._on_main(
            self.push_screen,
            ConfigEditor(dict(self.cfg), self.config_path),
            callback=self._on_config_result,
        )
        return CmdResult.ok()

    def _hook_edit_log(self) -> CmdResult:
        """Open the session log in the system viewer."""
        open_with_system(self._log_path())
        return CmdResult.ok()

    def _tui_hook_log_clear(self) -> CmdResult:
        """Delete the session log file."""
        log_path = self._log_path()
        if not log_path or not Path(log_path).exists():
            self._status("No log file to delete.", "yellow")
            return CmdResult.fail(msg="No log file.")
        if self.log_fh:
            self.log_fh.close()
            self.log_fh = None
        try:
            Path(log_path).unlink()
            self._status(f"Deleted {Path(log_path).name}", "green")
            return CmdResult.ok()
        except OSError as e:
            self._status(f"Delete failed: {e}", "red")
            return CmdResult.fail(msg=str(e))

    def _hook_edit_info(self) -> CmdResult:
        """Open the info report in the system viewer."""
        if not self.config_path:
            self.repl.write("No config loaded.", "red")
            return CmdResult.fail(msg="No config loaded.")
        stem = Path(self.config_path).stem
        path = Path(self.config_path).parent / f"{stem}.md"
        if path.exists():
            open_with_system(str(path))
            return CmdResult.ok()
        else:
            self.repl.write("No info report yet. Run /cfg.info first.", "red")
            return CmdResult.fail(msg="No info report yet. Run /cfg.info first.")

    def _hook_edit(self, ctx, args: str) -> CmdResult:
        """Edit a project file using the same dialogs as the UI menus.

        Routes to ScriptEditor (.run) or ProtoEditor (.pro).

        Args:
            ctx: Plugin context (unused).
            args: Filename (scripts/proto path).
        """
        filename = args.strip()
        if not filename:
            self.repl.write("Usage: /edit <filename>", "red")
            return CmdResult.fail(msg="Usage: /edit <filename>")

        # Resolve prefixed or bare filename
        path = self._resolve_project_file(filename)
        if path is None:
            self.repl.write(f"File not found: {filename}", "red")
            return CmdResult.fail(msg=f"File not found: {filename}")

        ext = path.suffix.lower()
        if ext == ".run":
            self._on_main(
                self.push_screen,
                ScriptEditor(self.repl.scripts_dir, str(path)),
                callback=self._on_script_saved,
            )
        elif ext == ".pro":
            self._on_main(
                self.push_screen,
                ProtoEditor(self.repl.proto_dir, str(path)),
                callback=self._on_proto_saved,
            )
        return CmdResult.ok()

    def _hook_edit_folder(self, ctx, args: str, folder: str, ext: str) -> CmdResult:
        """Edit a file from a specific folder using Textual modal."""
        name = args.strip()
        if not name:
            dir_map = {"run": self.repl.scripts_dir, "proto": self.repl.proto_dir}
            base = dir_map.get(folder)
            if base and base.is_dir():
                files = sorted(base.glob(f"*{ext}"))
                if files:
                    self.repl.write("  Available file(s):")
                    for f in files:
                        self.repl.write(f"    {f.name}")
                else:
                    self.repl.write("  (empty)")
            else:
                self.repl.write("  (no directory)")
            return CmdResult.ok()
        if not name.endswith(ext):
            name += ext
        dir_map = {"run": self.repl.scripts_dir, "proto": self.repl.proto_dir}
        base = dir_map.get(folder)
        if not base:
            return CmdResult.fail(msg=f"Unknown folder: {folder}")
        path = base / name
        if not path.exists():
            self.repl.write(f"File not found: {name}", "red")
            return CmdResult.fail(msg=f"File not found: {name}")
        if ext == ".run":
            self._on_main(
                self.push_screen,
                ScriptEditor(base, str(path)),
                callback=self._on_script_saved,
            )
        elif ext == ".pro":
            self._on_main(
                self.push_screen,
                ProtoEditor(base, str(path)),
                callback=self._on_proto_saved,
            )
        return CmdResult.ok()

    def _hook_run(self, ctx, args: str) -> CmdResult:
        verbose = ctx.flag("--verbose")
        path, result = self.repl.start_script(args)
        if path:
            self._run_script(path, verbose=verbose)
        return result

    _PROFILE_TMP_PREFIX = "_profile_tmp_"

    def _hook_run_profile(self, ctx, args: str) -> CmdResult:
        path, result = self.repl.start_script(args)
        if path:
            self._run_script(path, profile=True)
        return result

    def _hook_run_profile_cmd(self, ctx, args: str) -> CmdResult:
        """Profile a single command by writing a temp script."""
        import time as _time

        line = args.strip()
        if not line:
            ctx.write("Usage: /run.profile.cmd <command>", "red")
            return CmdResult.fail(msg="Usage: /run.profile.cmd <command>")
        prefix = self.cfg.get("cmd_prefix", "/")
        if not line.startswith(prefix) and "." in line.split()[0]:
            line = prefix + line
        ts = str(int(_time.time() * 1000))
        tmp_name = f"{self._PROFILE_TMP_PREFIX}{ts}.run"
        tmp_path = self.repl.scripts_dir / tmp_name
        parts = line.replace("\\n", "\n").split("\n")
        tmp_path.write_text(
            "\n".join(p.strip() for p in parts) + "\n", encoding="utf-8"
        )
        path, result = self.repl.start_script(tmp_name)
        if path:
            self._run_script(path, profile=True)
        return result

    def _prof_dir(self) -> Path | None:
        """Return the prof/ directory, or None if no config loaded."""
        if not self.config_path:
            return None
        return Path(self.config_path).parent / "prof"

    def _hook_run_profile_show(self, ctx, args: str) -> CmdResult:
        """Open the newest .prof file in the system viewer."""
        prof_dir = self._prof_dir()
        if not prof_dir:
            ctx.write("No config loaded.", "red")
            return CmdResult.fail(msg="No config loaded.")
        profs = sorted(prof_dir.glob("*.csv"), key=lambda f: f.stat().st_mtime)
        if not profs:
            ctx.output("No profile files found.")
            return CmdResult.fail(msg="No profile files found.")
        newest = profs[-1]
        ctx.write(f"Opening {newest.name}")
        open_with_system(str(newest))
        return CmdResult.ok()

    def _hook_run_profile_dump(self, ctx, args: str) -> CmdResult:
        """Print newest (or named) profile to the terminal."""
        prof_dir = self._prof_dir()
        if not prof_dir:
            ctx.write("No config loaded.", "red")
            return CmdResult.fail(msg="No config loaded.")
        name = args.strip()
        if name:
            path = prof_dir / name
            if not path.exists():
                ctx.write(f"File not found: {name}", "red")
                return CmdResult.fail(msg=f"File not found: {name}")
        else:
            profs = sorted(prof_dir.glob("*.csv"), key=lambda f: f.stat().st_mtime)
            if not profs:
                ctx.output("No profile files found.")
                return CmdResult.fail(msg="No profile files found.")
            path = profs[-1]
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                ctx.output(line)
        except OSError as e:
            ctx.write(f"Read error: {e}", "red")
            return CmdResult.fail(msg=f"Read error: {e}")
        return CmdResult.ok()

    def _hook_run_profile_explore(self, ctx, args: str) -> CmdResult:
        """Open the prof/ directory in file explorer."""
        prof_dir = self._prof_dir()
        if not prof_dir:
            ctx.write("No config loaded.", "red")
            return CmdResult.fail(msg="No config loaded.")
        prof_dir.mkdir(exist_ok=True)
        open_with_system(str(prof_dir))
        return CmdResult.ok()

    def _hook_run_profile_list(self, ctx, args: str) -> CmdResult:
        """List .prof files."""
        prof_dir = self._prof_dir()
        if not prof_dir:
            ctx.write("No config loaded.", "red")
            return CmdResult.fail(msg="No config loaded.")
        if not prof_dir.exists():
            ctx.output("  (no profile files)")
            return CmdResult.ok()
        profs = sorted(prof_dir.glob("*.csv"))
        if not profs:
            ctx.output("  (no profile files)")
            return CmdResult.ok()
        for f in profs:
            ctx.write(f"  {f.name}")
        return CmdResult.ok()

    def _hook_cfg_load(self, ctx, args: str) -> CmdResult:
        """Switch to a different config by name or path."""
        name = args.strip()
        if not name:
            self.repl.write("Usage: /cfg.load <name>", "red")
            return CmdResult.fail(msg="Usage: /cfg.load <name>")
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
            return CmdResult.fail(msg=f"Config not found: {name}")
        try:
            from termapy.config import load_config

            cfg = load_config(str(path))
        except Exception as e:
            self.repl.write(f"Failed to load config: {e}", "red")
            return CmdResult.fail(msg=f"Failed to load config: {e}")
        self._on_main(self._switch_config, cfg, str(path))
        self._on_main(self._show_config_info, str(path))
        return CmdResult.ok()

    def _hook_proto_load(self, ctx, args: str) -> CmdResult:
        """Run a protocol test script (delegates to /proto.run)."""
        prefix = self.cfg.get("cmd_prefix", "/")
        self._dispatch_single(f"{prefix}proto.run {args}")
        return CmdResult.ok()

    def _hook_run_list(self, ctx, args: str) -> CmdResult:
        """List .run files in the run/ directory."""
        d = self.repl.scripts_dir
        if not d.exists():
            ctx.output("  (no run/ directory)")
            return CmdResult.ok()
        files = sorted(d.glob("*.run"))
        if not files:
            ctx.output("  (no .run files)")
            return CmdResult.ok()
        for f in files:
            self.repl.write(f"  {f.name}")
        return CmdResult.ok()

    _script_last_label: str = ""

    def on_serial_terminal_script_started(self, event: ScriptStarted) -> None:
        """Mount script overlay or update label when a script starts/nests."""
        try:
            bar = self.query_one("#bottom-bar")
            inp = self.query_one("#cmd", Input)
            has_overlay = bool(bar.query("#script-stop"))
            text = " " + " \u2192 ".join(event.stack)
            if not has_overlay:
                for child in bar.children:
                    child.display = False
                label = Static(text, id="script-label")
                label.styles.width = "1fr"
                stop_btn = Button("Stop", id="script-stop", variant="error")
                bar.mount(label)
                bar.mount(stop_btn)
                inp.disabled = True
            else:
                label_w = bar.query_one("#script-label", Static)
                label_w.display = True
                bar.query_one("#script-stop").display = True
                label_w.update(text)
            self._script_last_label = text
        except Exception:
            pass

    def on_serial_terminal_script_progress(self, event: ScriptProgress) -> None:
        """Update the script overlay label with step count."""
        try:
            chain = " \u2192 ".join(event.stack)
            text = f" {chain} [{event.step}/{event.total}]"
            if text != self._script_last_label:
                label_w = self.query_one("#script-label", Static)
                label_w.display = True
                self.query_one("#script-stop").display = True
                label_w.update(text)
                self._script_last_label = text
        except Exception:
            pass

    def on_serial_terminal_script_finished(self, event: ScriptFinished) -> None:
        """Update or teardown script overlay when a script finishes."""
        try:
            bar = self.query_one("#bottom-bar")
            inp = self.query_one("#cmd", Input)
            if event.stack:
                # Returned from nested script - update label to parent
                text = " " + " \u2192 ".join(event.stack)
                label_w = bar.query_one("#script-label", Static)
                label_w.display = True
                bar.query_one("#script-stop").display = True
                label_w.update(text)
                self._script_last_label = text
            else:
                # Top-level script done - teardown overlay
                for widget in bar.query("#script-label, #script-stop"):
                    widget.remove()
                for child in bar.children:
                    child.display = True
                self._script_last_label = ""
                inp.disabled = False
                inp.focus()
                self._engine.proto_active = False
        except Exception:
            pass

    @work(thread=True)
    def _run_script(
        self,
        path: Path,
        profile: bool = False,
        verbose: bool = False,
    ) -> None:
        """Threaded wrapper for repl.run_script (needs @work decorator).

        _status and _dispatch_single are thread-safe, so no
        call_from_thread wrapper needed for write/dispatch.
        """
        self.post_message(self.ScriptStarted(self.repl._script_stack[:]))

        def _progress(step: int, total: int) -> None:
            self.post_message(
                self.ScriptProgress(self.repl._script_stack[:], step, total)
            )

        def _on_nest() -> None:
            self.post_message(self.ScriptStarted(self.repl._script_stack[:]))

        try:
            self.repl.run_script(
                path,
                write=self._status,
                dispatch=self._dispatch_single,
                profile=profile,
                verbose=verbose,
                progress=_progress,
                on_nest=_on_nest,
            )
        except RuntimeError:
            pass
        finally:
            self.post_message(self.ScriptFinished(self.repl._script_stack[:]))


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


def _run_cli_mode(args) -> str | None:
    """Run in CLI mode - plain text terminal, no TUI.

    Returns:
        Mode to switch to ("tui") or None for normal exit.
    """
    from termapy.cli import CLITerminal

    run_script = getattr(args, "run", None)

    # If a positional arg is a .run file, treat it as --run
    if args.config and args.config.endswith(".run") and not run_script:
        run_script = args.config
        args.config = None

    if args.demo:
        from termapy.config import setup_demo_config

        config_path = str(setup_demo_config(cfg_dir(), force=True))
    elif run_script and not args.config:
        # Infer config from the .run file's location
        config_path = _infer_config_from_run_file(run_script)
        if not config_path:
            print(
                f"termapy: cannot infer config from {Path(run_script).resolve()}",
                file=sys.stderr,
            )
            sys.exit(1)
    elif args.config:
        config_path = _resolve_config(args.config)
        if config_path is None:
            print(
                f"termapy: config not found: {Path(args.config).resolve()}",
                file=sys.stderr,
            )
            print(
                "  Use --demo to create a demo config, or specify a .cfg file.",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        path, _ = _find_config()
        if not path:
            print(
                "termapy: no config found. Use --demo or specify a config.",
                file=sys.stderr,
            )
            sys.exit(1)
        config_path = path

    try:
        cfg = load_config(config_path)
    except Exception as e:
        print(f"termapy: failed to load config: {e}", file=sys.stderr)
        sys.exit(1)

    cli = CLITerminal(
        cfg,
        config_path,
        no_color=args.no_color,
        run_script=run_script,
        term_width=getattr(args, "term_width", None),
    )
    result = cli.run()
    if result:
        args.config = cli.config_path
    return result


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
        print(f"termapy: proto file not found: {Path(name).resolve()}", file=sys.stderr)
        if proto_dir.exists():
            print(f"  (checked {proto_dir.resolve()})", file=sys.stderr)
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
    print(
        f"{results['meta']['script_name']}: {passed}/{total} {status} ({elapsed:.0f}ms)"
    )

    sys.exit(0 if failed == 0 else 1)


def _run_web_mode(args) -> None:
    """Serve the TUI in a web browser via textual-serve."""
    try:
        from textual_serve.server import Server  # ty: ignore[unresolved-import]
    except ImportError:
        print("Error: --web requires textual-serve.")
        print("  pip install termapy[web]")
        print("  uv tool install termapy[web]")
        sys.exit(1)

    print("=" * 60)
    print("  EXPERIMENTAL: Web mode via textual-serve")
    print("  /tui and /cli mode switching are not available.")
    print("  /help.open may not work in the browser.")
    print("=" * 60)
    print()

    # Build the command to re-launch termapy in TUI mode
    cmd_parts = [sys.executable, "-m", "termapy"]
    if args.demo:
        cmd_parts.append("--demo")
    if args.config:
        cmd_parts.append(args.config)
    if args.cfg_dir:
        cmd_parts.extend(["--cfg-dir", args.cfg_dir])

    server = Server(
        " ".join(cmd_parts),
        host="localhost",
        port=args.web_port,
        title="Termapy",
    )
    server.serve()


def _run_tui_mode(args) -> str | None:
    """Run in TUI mode. Returns mode to switch to, or None for exit."""
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
        if app.switch_to:
            args.config = app.config_path
        return app.switch_to

    if args.config:
        config_path = _resolve_config(args.config)
        if config_path is None:
            print(
                f"termapy: config not found: {Path(args.config).resolve()}",
                file=sys.stderr,
            )
            print(
                "  Use --demo to create a demo config, or specify a .cfg file.",
                file=sys.stderr,
            )
            sys.exit(1)
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
        if app.switch_to:
            args.config = app.config_path
        return app.switch_to

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
        if app.switch_to:
            args.config = app.config_path
        return app.switch_to
    elif show_picker:
        cfg = dict(DEFAULT_CFG)
        app = SerialTerminal(cfg, config_path="", show_picker=True)
        app.run()
        _reset_terminal()
        if app.switch_to:
            args.config = app.config_path
        return app.switch_to
    else:
        from termapy.config import setup_demo_config

        config_path = str(setup_demo_config(cfg_dir(), force=True))
        try:
            cfg = load_config(config_path)
        except Exception as e:
            print(f"termapy: failed to load demo config: {e}", file=sys.stderr)
            sys.exit(1)
        app = SerialTerminal(cfg, config_path=config_path, first_run=True)
        app.run()
        _reset_terminal()
        if app.switch_to:
            args.config = app.config_path
        return app.switch_to


if __name__ == "__main__":
    from termapy.entry import main

    main()
