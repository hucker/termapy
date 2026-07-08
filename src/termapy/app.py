#!/usr/bin/env python3
"""
Usage:
    uv run termapy [config.json]

Runs well in most terminals (Windows Terminal, iTerm2, etc).
VS Code's integrated terminal can be jerky due to its rendering pipeline.
"""

import re
import os
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from threading import Event

import serial
from termapy.config import (
    CONFIG_LOAD_ERRORS,
    CURRENT_CONFIG_VERSION,
    cfg_data_dir,
    cfg_dir,
    cfg_log_path,
    cfg_plugins_dir,
    global_plugins_dir,
    load_config,
    open_serial,
    open_with_system,
    setup_demo_config,
)

# Config-path resolution lives in termapy.config_resolve (Textual-free).
# Re-exported under the original underscored names so existing
# `from termapy.app import _find_config` callers keep working while
# entry.py / cli_flags.py can import from config_resolve directly.
from termapy.config_resolve import (
    find_config as _find_config,
    resolve_config as _resolve_config,
)
from rich.text import Text
from textual import on, work

from termapy import port_control
from termapy.defaults import DEFAULT_CMD_PREFIX, cmd_prefix, default_cfg
from termapy.folders import FOLDER_PATTERNS
from termapy.palette_provider import PaletteProvider
from termapy.dialogs import (
    CfgConfirm,
    ConfigEditor,
    ConfigPicker,
    ConfirmDialog,
    FilenameDialog,
    PortPicker,
    ProtoPicker,
    QuickSetup,
    ScriptPicker,
    UpdateAvailableDialog,
)
from termapy.plugins import (
    BoundaryException,
    CapabilitySet,
    LoadResult,
    load_plugins_from_dir,
)
from termapy.proto_debug import ProtoDebugScreen
from termapy.protocol import builtins_viz_dir, load_visualizers_from_dir
from termapy.capture import CaptureEngine, CaptureResult
from termapy.serial_engine import READER_STOP_WAIT_S, SerialEngine
from termapy.serial_port import eol_label
from termapy.repl import ReplEngine
from termapy.terminal_host import TerminalHost
from termapy.plugins import CmdResult
from textual.app import App, ComposeResult
from textual.message import Message
from textual.timer import Timer
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widgets import Button, Input, Label, OptionList, RichLog, Static
from textual.suggester import Suggester


# Exceptions we expect to see when an event-loop callback, timer tick,
# or reader-thread post-back fires during app teardown.  NoMatches
# comes from ``query_one`` when the widget tree is being unmounted;
# RuntimeError comes from ``call_from_thread`` when the event loop is
# shutting down.  Using this tuple as the ``except`` target narrows
# what we silence: real bugs (ValueError, TypeError, AttributeError,
# anything unexpected) still propagate and get reported.
#
# See the shutdown-widget-guards commit for why swallowing these
# specific types is necessary.  Anything else caught here would be
# hiding a real bug -- that's the whole point of keeping the tuple
# narrow.
SHUTDOWN_RACE: tuple[type[BaseException], ...] = (NoMatches, RuntimeError)


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
        self,
        commands: list[str],
        history: list[str],
        prefix: str = DEFAULT_CMD_PREFIX,
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


from termapy.scripting import ANSI_RE, format_duration, format_timestamp  # noqa: E402


# Single source of truth for top-row hotkeys.
# Maps button id -> (key spec, action name, footer label).
# The key spec may list multiple keys comma-separated (Textual's Binding
# splits on comma); the first entry is the canonical key shown in
# tooltips.  Ctrl+Shift+N aliases exist because VS Code's integrated
# terminal captures bare F-keys (F1=palette, F3=Find Next, etc.) and
# Alt+F-keys / Alt+digits are unreliable (window-manager menu mnemonics,
# editor-tab switching).  Ctrl+Shift+digit survives via xterm.js's
# modifyOtherKeys/csi-u handling.
# Consumed by SerialTerminal.BINDINGS (via comprehension) and by
# _hotkey_label() to annotate tooltips, so the keys are defined exactly
# once and the binding and the tooltip can never drift apart.
_HOTKEYS: dict[str, tuple[str, str, str]] = {
    "btn-help":     ("f1,ctrl+shift+1",   "btn_help",        "Help"),
    "btn-cfg":      ("f2,ctrl+shift+2",   "btn_cfg",         "Cfg"),
    "btn-scripts":  ("f3,ctrl+shift+3",   "btn_run",         "Run"),
    "btn-proto":    ("f4,ctrl+shift+4",   "btn_proto",       "Proto"),
    "title-center": ("f10,ctrl+shift+5",  "btn_open_config", "Open Config"),
}


def _hotkey_label(btn_id: str) -> str:
    """Return display label like 'F1' for a button, or '' if unbound.

    When the key spec lists multiple keys (e.g. ``"f1,shift+f1"``), the
    first one is shown -- the rest are silent fallbacks for hosts that
    capture the primary key.
    """
    entry = _HOTKEYS.get(btn_id)
    if not entry:
        return ""
    primary = entry[0].split(",", 1)[0]
    return primary.upper()


# VS Code's integrated terminal captures Ctrl+P for its own "Go to
# file" dialog before termapy can see the keystroke.  Detect that
# host and advertise the Alt+P fallback in the REPL placeholder so
# users in VS Code don't have to discover the alternative by guessing.
# ``TERM_PROGRAM=vscode`` is set by every VS Code integrated terminal
# session; other terminals leave it unset (or set to their own name
# like ``Apple_Terminal`` or ``iTerm.app``).
PALETTE_HOTKEY = "Alt+P" if os.environ.get("TERM_PROGRAM") == "vscode" else "Ctrl+P"


def _build_help_tooltip(ver: str):
    """Build the Help-button tooltip as a Rich renderable.

    Lays the attribution block out as a three-column ``Table.grid``
    (name / role / author) with a color per column so the tooltip
    stays scannable instead of running the author names into the
    role text.
    """
    from rich.console import Group
    from rich.table import Table
    from rich.text import Text

    hint = _hotkey_label("btn-help")
    hint_str = f" ({hint})" if hint else ""

    # reveng's project URL sits on a second line inside the "role"
    # column so it aligns under "CRC algorithms" rather than spawning
    # a free-floating line below the grid.  Rich Table renders ``\n``
    # inside a cell as multi-line; column widths still line up.
    reveng_role = Text("CRC algorithms\n", style="white")
    reveng_role.append("reveng.sourceforge.io", style="dim")

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="cyan")
    grid.add_column(style="white")
    grid.add_column(style="green")
    grid.add_row("pyserial",         "serial I/O",     "Chris Liechti")
    grid.add_row("Textual / Rich",   "TUI + output",   "Will McGugan")
    grid.add_row("prompt_toolkit",   "CLI",            "Jonathan Slenders")
    grid.add_row("reveng catalogue", reveng_role,      "Greg Cook")
    grid.add_row("xmodem",           "file transfer",
                 "Wijnand Modderman, Jeff Quast, Andrew Leech")
    grid.add_row("ymodem",           "file transfer",  "alexwoo")

    return Group(
        Text.from_markup(f"[bold]Termapy v{ver}[/]  [dim]Show help guide{hint_str}.[/]"),
        Text(""),
        Text.from_markup("[bold]Built on open source:[/]"),
        grid,
        Text(""),
        Text.from_markup("Type [bold cyan]/credits[/] for full attribution."),
    )


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

    class DelayProgress(Message):
        """Posted every 0.25s during a script-path /delay.

        ``bar`` is the pre-rendered "[bar] Ns/Ms" string; ``done=True``
        fires exactly once on the final tick so the handler can
        restore the script-label to its pre-delay text.
        """

        def __init__(self, bar: str, done: bool) -> None:
            super().__init__()
            self.bar = bar
            self.done = done

    TITLE = "termapy"

    CSS = """
    Tooltip {
        max-width: 80;
    }
    /* Tighten Textual's CommandPalette: the built-in CommandInput
       ships with `border: blank` which renders as one blank cell
       above and below the search row, on top of the #--input hkey
       border.  Drop the inner blank border so the search box is a
       single row instead of three. */
    CommandPalette CommandInput,
    CommandPalette CommandInput:focus {
        border: none;
        height: 1;
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
        background: $primary;
    }
    #title-bar #btn-exit {
        background: crimson;
        text-style: bold;
    }
    #title-bar > Button:first-of-type {
        margin-left: 0;
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
        background: dodgerblue;
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
    #find-bar {
        height: 1;
        width: auto;
        display: none;
    }
    #find-bar.visible {
        display: block;
    }
    #find-status {
        height: 1;
        width: auto;
        content-align: right middle;
        color: $accent;
        padding: 0 1;
    }
    #btn-find-prev, #btn-find-next, #btn-find-close {
        min-width: 3;
        background: $boost;
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
        # Ctrl+P opens Textual's built-in CommandPalette (top-center,
        # drop-down, fuzzy filter).  PALETTE_CMDS is exposed to it via
        # the PaletteProvider registered in App.COMMANDS below.
        Binding("ctrl+p", "command_palette", "Command Palette",
                show=False, priority=True),
        Binding("ctrl+s", "screenshot", "Screenshot", show=False),
        Binding("ctrl+t", "text_screenshot", "Text Screenshot", show=False),
        Binding("escape", "stop_script", "Stop Script", show=False),
        # F-keys for top-row buttons -- defined in _HOTKEYS (module top)
        # so the binding and the tooltip annotation share one source.
        *[Binding(k, a, lbl, show=False) for (k, a, lbl) in _HOTKEYS.values()],
        # Alt-key fallbacks: VS Code's integrated terminal (and a few
        # other hosts) captures Ctrl+P / Ctrl+S / Ctrl+T before the
        # shell sees them.  Alt-key bindings are rarely intercepted,
        # so they give every user a path that survives hostile
        # terminals.  Harmless everywhere else.
        Binding("alt+q", "quit", "Quit (alt)", show=False),
        Binding("alt+p", "command_palette", "Command Palette (alt)",
                show=False, priority=True),
        Binding("alt+s", "screenshot", "Screenshot (alt)", show=False),
        Binding("alt+t", "text_screenshot", "Text Screenshot (alt)", show=False),
    ]

    # COMMANDS replaces Textual's default providers (which would surface
    # every framework action).  We only want the curated PaletteProvider;
    # the user's "two-layer discovery" model says the palette is the
    # opinionated editorial layer, not a kitchen-sink action dump.
    COMMANDS = {PaletteProvider}

    PALETTE_CMDS = [
        ("Help", "_palette_help"),
        ("Find in scrollback...", "_palette_find"),
        ("Search scrollback (Grep)...", "_palette_grep"),
        ("Search command help...", "_palette_search_help"),
        ("Select Port...", "_show_port_picker"),
        ("List Ports", "_palette_list_ports"),
        ("List Variables", "_palette_list_vars"),
        ("Set Variable...", "_palette_set_var"),
        ("Toggle Line Numbers", "_palette_toggle_line_numbers"),
        ("Toggle Echo", "_palette_toggle_echo"),
        ("Toggle Timestamps", "_palette_toggle_timestamps"),
        ("Toggle Hex Display", "_palette_toggle_hex"),
        ("Toggle Line Endings", "_palette_toggle_line_endings"),
        ("Show Terminal Settings", "_palette_term_info"),
        ("Connect / Disconnect", "_toggle_connection"),
        ("Edit Config", "_palette_edit_config"),
        ("Load Config...", "_palette_load_config"),
        ("New Config", "_palette_new_config"),
        ("Load Run Script...", "_btn_scripts"),
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
        output_level: str | None = None,
    ) -> None:
        super().__init__()
        self.switch_to: str | None = None
        # When switching to vt100 mode, whether to force the DEMO_VT100
        # widget-tour device (/demo.vt100) instead of the current device.
        self._switch_vt100_demo: bool = False
        self.config_path = config_path
        self.open_editor_on_start = open_editor
        self.show_picker_on_start = show_picker
        self._first_run = first_run
        self._initial_output_level = output_level
        self.log_fh = None
        self.last_screenshot: str | None = None
        self.repl = ReplEngine(
            cfg,
            config_path,
            write=self._status,
            prefix=cmd_prefix(cfg),
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
        # Serializes command execution.  Every command path -- typed input,
        # UI buttons, palette, and each script line -- funnels through
        # _dispatch_single / _dispatch_quiet, which both mutate shared
        # PluginContext state (serial.write, _call_level, active_flags,
        # io._write) around the handler call.  Two workers running at once
        # would stomp that save/restore, so a non-blocking re-entrant lock
        # admits one dispatch at a time.  Non-blocking is required: the main
        # thread must never block here or it would deadlock a worker waiting
        # on call_from_thread.  Re-entrant so nested ctx.dispatch on the same
        # thread is allowed.
        self._dispatch_guard = threading.RLock()
        # True while /find is showing the frozen highlighted view.
        # New live data arrival (in _write_batch) flips this back
        # to False and restores #output.
        self._find_overlay_active: bool = False
        # Identity key for the currently-rendered snapshot.  If the
        # next state has the same key, we skip the expensive
        # clear+rewrite of #output-find and just re-scroll.  Key is
        # (id_of_scrollback_text, len_of_matches, first_match_lineno
        # if any, last_match_lineno if any) -- captures both "new
        # /find" and "matches changed" without per-element compare.
        self._find_render_key: tuple | None = None
        # Line number of the current find match at the moment of
        # close.  Used to scroll #output to the same line so the
        # user keeps their visual context when leaving find mode.
        # Cleared after restore or when find ends with no matches.
        self._find_last_current_line: int | None = None
        # When _write_batch auto-dismisses find (new live data),
        # we DON'T want to scroll #output back to the find
        # position -- the user's interest just shifted to fresh
        # output.  This flag is False during auto-dismiss and True
        # during user-initiated close.
        self._find_restore_scroll_on_close: bool = True

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
        prefix = cmd_prefix(self.cfg)
        commands: list[str] = []
        for name, plugin in self.repl._plugins.items():
            commands.append(f"{prefix}{name}")
            # Build a longer ghost-text variant when the plugin has args
            # or flags so the user sees the full signature inline.  Flags
            # render with {braces} -- matching termapy's args convention
            # for optional things -- so a Command(args="<name>",
            # flags={"--table": ...}) shows as "/cmd <name> {--table}".
            if plugin.args or plugin.flags:
                parts = [f"{prefix}{name}"]
                if plugin.args:
                    parts.append(plugin.args)
                parts.extend(f"{{{flag}}}" for flag in plugin.flags)
                commands.append(" ".join(parts))
        for f in self._project_files():
            commands.append(f"{prefix}edit {f}")
        # Device commands now live on the active profile; suggester
        # surfaces them so the TUI completer offers them too.
        active = self.repl.ctx.ns("active_profile")
        profile_cmds = active.get("commands") if isinstance(active, dict) else None
        if isinstance(profile_cmds, dict):
            for name in profile_cmds:
                commands.append(name)
        self._cached_commands = commands
        self._suggester.update(commands, self.history, prefix)

    def _update_suggester(self) -> None:
        """Update suggestions with current history (no filesystem scan)."""
        prefix = cmd_prefix(self.cfg)
        self._suggester.update(self._cached_commands, self.history, prefix)

    def compose(self) -> ComposeResult:
        title = self.cfg.get("title", "") or self.config_path
        port_info = self._port_info_str()
        with Horizontal(id="title-bar"):
            from textual.widgets import Static

            from importlib.metadata import (
                PackageNotFoundError,
                version as _get_version,
            )

            # PackageNotFoundError fires when termapy is running from
            # a git clone without `pip install .` (dev setup).  Other
            # exceptions here would be bugs worth seeing.
            try:
                ver = _get_version("termapy")
            except PackageNotFoundError:
                ver = "?"
            # Windows puts the close "X" top-right (Windows convention);
            # mac/linux put it top-left (macOS convention, standard on
            # Linux too).  Same button, same id/handler -- only position
            # differs.
            exit_on_right = sys.platform == "win32"

            def _make_exit_btn() -> Button:
                b = Button(" X ", id="btn-exit", variant="error")
                b.tooltip = "Close connection and exit (Ctrl+C)."
                return b

            if not exit_on_right:
                yield _make_exit_btn()
            help_btn = Button("Help", id="btn-help")
            help_btn.tooltip = _build_help_tooltip(ver)
            yield help_btn
            if self.cfg.get("cfg_enabled", True):
                cfg_btn = Button("Cfg", id="btn-cfg")
                cfg_btn.tooltip = f"New / Edit / Load config ({_hotkey_label('btn-cfg')})."
                yield cfg_btn
            if self.cfg.get("run_enabled", True):
                run_btn = Button("Run", id="btn-scripts")
                run_btn.tooltip = f"Run a script ({_hotkey_label('btn-scripts')})."
                yield run_btn
            if self.cfg.get("proto_enabled", True):
                proto_btn = Button("Proto", id="btn-proto")
                proto_btn.tooltip = f"Protocol test scripts ({_hotkey_label('btn-proto')})."
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
                f"Config: {self.config_path or 'none'}\n"
                f"Click to edit config ({_hotkey_label('title-center')})"
            )
            yield center
            yield Static("", id="title-spacer-r")
            left = Button(port_info, id="title-left")
            left.tooltip = "Click to select serial port."
            yield left
            right = Button("Disconnected", id="title-right")
            yield right
            if exit_on_right:
                yield _make_exit_btn()
        max_lines = self.cfg.get("max_lines", 10000)
        yield RichLog(
            highlight=False, markup=True, wrap=True, id="output", max_lines=max_lines
        )
        # Frozen-view companion for /find.  Hidden by default; when
        # find activates, _update_find_bar populates it with a
        # markup-styled copy of the current scrollback (matched
        # lines in [reverse]), hides #output, and shows this one.
        # Navigation just scrolls -- no rewrite -- so the current
        # match is identified by viewport position (centered), not
        # by per-line styling.  auto_scroll=False is critical:
        # otherwise the post-write snap-to-bottom races with our
        # explicit scroll_to(center).  Any new live data arriving
        # via _write_batch auto-dismisses the view.
        find_log = RichLog(
            highlight=False, markup=True, wrap=True,
            id="output-find", max_lines=max_lines,
            auto_scroll=False,
        )
        find_log.display = False
        yield find_log
        yield OptionList(id="history-popup")
        with Vertical(id="bottom-section"):
            with Horizontal(id="bottom-bar"):
                prefix = cmd_prefix(self.cfg)
                cmd_btn = Button(prefix, id="btn-cmds")
                cmd_btn.tooltip = f"Show REPL {prefix} commands."
                yield cmd_btn
                yield Input(
                    placeholder=f"{prefix} for REPL, {PALETTE_HOTKEY} for palette",
                    id="cmd",
                    suggester=self._suggester,
                )
                # Record button toggles /run.record.  Sits at the
                # right edge of the REPL input.  Green when idle,
                # red ("Stop") while recording.  Hidden when
                # record_enabled is false.  All recording state
                # lives in the recorder builtin -- this button just
                # dispatches /run.record and refreshes its visual.
                if self.cfg.get("record_enabled", True):
                    record_btn = Button(
                        # Angle brackets mark this as a state indicator
                        # rather than a discovery button.  Square
                        # brackets would render as Rich markup tags
                        # and eat the label.
                        "<Rec>", id="btn-record", variant="success",
                    )
                    record_btn.tooltip = (
                        "Record commands to a .run script. "
                        "Click to start; click again to stop."
                    )
                    yield record_btn
                # FindBar: ephemeral, hidden until /find runs.
                # display=False on the container AND each child so
                # the widgets vanish from layout entirely until
                # _update_find_bar reveals them.
                find_bar = Horizontal(id="find-bar")
                find_bar.display = False
                with find_bar:
                    find_status = Label("", id="find-status")
                    find_status.display = False
                    yield find_status
                    prev_btn = Button(
                        "▲", id="btn-find-prev", variant="default",
                    )
                    prev_btn.tooltip = "Previous find match (/find.prev)"
                    prev_btn.display = False
                    yield prev_btn
                    next_btn = Button(
                        "▼", id="btn-find-next", variant="default",
                    )
                    next_btn.tooltip = "Next find match (/find.next)"
                    next_btn.display = False
                    yield next_btn
                    close_btn = Button(
                        "×", id="btn-find-close", variant="default",
                    )
                    close_btn.tooltip = "Close find (/find.clear)"
                    close_btn.display = False
                    yield close_btn
                yield Label("", id="status-bar")

                def _btn(label, id, tip, variant="default", display=True):
                    b = Button(label, id=id, variant=variant)
                    b.tooltip = tip
                    b.display = display
                    return b

                show_hw = self.cfg["serial"]["flow_control"] == "manual"
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
                    btn_id = f"btn-custom-{i}"
                    b = Button(cb.get("name", f"C{i}"), id=btn_id)
                    b.tooltip = cb.get("tooltip", cb.get("name", ""))
                    b.add_class("custom-btn")
                    # First enabled custom button gets a wider left
                    # margin so the boundary into the custom-button
                    # region is unambiguously visible (intentionally
                    # a little wider than the matching boundary gap
                    # Log uses on the region's right edge -- erring
                    # on too-wide rather than ambiguously-close).
                    if not has_custom:
                        b.styles.margin = (0, 0, 0, 3)
                    has_custom = True
                    yield b
                log_btn = _btn("Log", "btn-log", "View current log file.")
                if has_custom:
                    log_btn.styles.margin = (0, 0, 0, 2)
                yield log_btn
                yield _btn("SS", "btn-ss-dir", "Open screenshot folder.")
                yield _btn("Cap", "btn-cap-dir", "Open captures folder.")

    def _show_config_info(self, *args, **kwargs):
        """Delegate to ``info_views.show_config_info``."""
        from termapy.info_views import show_config_info
        show_config_info(self, *args, **kwargs)
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
        # VS Code tip goes first so it's at the top of the startup
        # output -- before the plugin-load summary and anything else.
        # Easy to miss if it's buried mid-startup.
        self._maybe_show_vscode_tip()
        self._register_tui_hooks()
        self._load_plugins()
        self._run_startup()
        self.repl.fire_lifecycle("on_app_start")
        self._check_for_updates()

    def _maybe_show_vscode_tip(self) -> None:
        """Inform Windows/Linux VS Code users about the Alt-key fallbacks.

        VS Code's integrated terminal captures Ctrl+P / Ctrl+S / Ctrl+T
        on Windows and Linux for Quick Open, Save, New Tab.  Those never
        reach termapy.  macOS VS Code uses Cmd+* for the same actions,
        so Ctrl+* passes through to the terminal normally -- no tip
        needed there.

        The Alt+* aliases are bound unconditionally (they cost nothing
        on non-VS-Code terminals), so on macOS we stay silent even
        though the bindings still work.
        """
        if os.environ.get("TERM_PROGRAM") != "vscode":
            return
        if sys.platform == "darwin":
            return
        # Banner lines + bold-but-not-italic so the tip stands out at
        # the top of the output.  _status uses "bold italic <color>"
        # which renders as a faint dimmed tone; we want something that
        # actually draws the eye.
        try:
            output = self.query_one("#output", RichLog)
        except SHUTDOWN_RACE:
            return
        rule = Text("-" * 72, style="bold green")
        output.write(rule)
        output.write(Text(
            "VS Code detected: F-keys and Ctrl+P/S/T are captured by VS Code.",
            style="bold green",
        ))
        output.write(Text(
            "Top buttons: Ctrl+Shift+1=Help, 2=Cfg, 3=Run, 4=Proto, 5=Open Config.",
            style="bold green",
        ))
        output.write(Text(
            "Other:       Alt+P=palette, Alt+S=SVG, Alt+T=text screenshot.",
            style="bold green",
        ))
        output.write(rule)
        self._log_line("#", "VS Code terminal detected -- Ctrl+Shift+N and Alt-key fallbacks bound.")

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
        # Narrow catch: PackageNotFoundError covers running from a
        # git clone without an install.  update_check.check() honours
        # its own "never raises to the user" contract internally, so
        # we trust it not to leak other exceptions here.
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as _get_version

        from termapy.update_check import check

        try:
            current = _get_version("termapy")
        except PackageNotFoundError:
            return  # no installed metadata -> nothing to compare against
        latest = check(current_version=current)

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
        except SHUTDOWN_RACE:
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
            callback=_on_result,
        )

    def _setup_vars(self) -> None:
        """Set launch/context variables for plugin use."""
        from termapy.builtins.commands.var import (
            register_cfg_vars,
            set_context_var,
            set_launch_var,
        )

        set_launch_var("FRONT_END", "textual")
        set_context_var(
            "CFG",
            lambda: Path(self.config_path).stem if self.config_path else "termapy",
        )
        register_cfg_vars(
            get_config_path=lambda: self.config_path,
            get_cfg=lambda: self.cfg,
            get_log_path=lambda: self._log_path(),
        )

    def _build_context(self) -> None:
        """Build PluginContext and InternalHandle, wire to REPL."""
        internal = self._build_internal_handle()
        # TUI-specific InternalHandle extensions
        internal.confirm_save_cfg = self._hook_cfg_confirm
        internal.open_proto_debug = lambda path, script: self.call_later(
            self._open_proto_debug, path, script
        )
        internal.start_capture = self._cap_start
        internal.stop_capture = self._cap_stop
        internal.directives = self.repl._directives
        # /find plugin calls this with a state snapshot to refresh
        # the FindBar widget (or None to hide it).
        internal.update_find_bar = self._update_find_bar

        ctx = self._build_plugin_context(internal)
        # TUI-specific PluginContext overrides
        ctx.io._write = self._status
        ctx.io._write_markup = self._write_output_markup
        ctx.io.log = self._log_line
        ctx.serial.wait_for_data = lambda timeout_ms=250: (
            self._engine.serial_port.wait_for_data(timeout_ms)
            if self._engine.serial_port
            else False
        )
        ctx.wait_for_match = self.repl.wait_for_match
        ctx.io.status_bar = self._set_status_bar
        ctx.dispatch = self._dispatch_single
        ctx.io.notify = lambda text, **kw: self._on_main(self.notify, text, **kw)
        ctx.io.clear_screen = lambda: self._on_main(self._clear_output)
        ctx.ui._save_screenshot_impl = lambda *a, **kw: self._on_main(
            self.save_screenshot, *a, **kw
        )
        ctx.ui._get_screen_text_impl = lambda: self._on_main(self._get_screen_text)
        ctx.ui._exit_app_impl = lambda: self._on_main(self.exit)
        # TUI environment capabilities.  See CapabilitySet for the full
        # vocabulary.  block_until is NOT set here -- it's provided
        # dynamically by the script runner (see _effective_capabilities).
        from termapy.plugins import detect_gui_apps
        ctx.capabilities = CapabilitySet(
            confirm_dialog=True,
            ui_notify=True,
            status_bar=True,
            screen_capture=True,
            tui_mode=True,
            interactive=True,
            gui_apps=detect_gui_apps(),
        )

        self.repl.set_context(ctx)
        self.repl._after_cfg = self._refresh_after_cfg
        self.ctx = ctx
        self._init_flags(echo=True)
        if self._initial_output_level is not None:
            ctx.ns("flags")["output_level"] = self._initial_output_level

    def _register_tui_hooks(self) -> None:
        """Register TUI-specific commands as plugin hooks.

        Implementation lives in ``termapy.app_hooks``.  Lazy-import
        keeps the module out of the CLI/MCP import graphs (those
        frontends never call this).
        """
        from termapy.app_hooks import register_tui_hooks
        register_tui_hooks(self)

    def _load_plugins(self) -> None:
        """Load global and per-config external plugins.

        Skipped entirely when ``TERMAPY_TRUSTED_PLUGINS_ONLY`` is
        truthy in the environment -- the trust boundary collapses to
        "your Python site-packages," same as every other Python tool.
        Built-ins (loaded by ReplEngine from the bundled package) are
        unaffected.
        """
        from termapy.env_flags import TRUSTED_PLUGINS_ONLY
        if TRUSTED_PLUGINS_ONLY:
            self._status(
                "TERMAPY_TRUSTED_PLUGINS_ONLY=1: skipping filesystem plugin discovery.",
                "yellow",
            )
            self._rebuild_suggester_commands()
            return
        self._load_and_report(
            load_plugins_from_dir(global_plugins_dir(), "global"),
            source="global",
        )
        if self.config_path:
            cfg_name = Path(self.config_path).stem
            self._load_and_report(
                load_plugins_from_dir(cfg_plugins_dir(self.config_path), cfg_name),
                source=cfg_name,
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
        # Shutdown drain: _disconnect already waited on the reader; this is a
        # short final settle before we close the log fh, not the reconnect
        # correctness gate (that one is _connect below, keyed off the contract
        # constant).  The process is exiting either way.
        self._engine.reader_stopped.wait(timeout=0.2)
        if self.log_fh:
            self.log_fh.close()
            self.log_fh = None

    def _connect(self, port: str | None = None) -> bool:
        if self.is_connected:
            return False
        # Reconnect correctness gate: wait for any prior reader to fully stop
        # (and release the shared port handle) before opening a new one.  Keyed
        # off the same contract constant as engine teardown.
        self._engine.reader_stopped.wait(timeout=READER_STOP_WAIT_S)
        return super()._connect(port)

    def _on_connected(self, message: str) -> None:
        """TUI post-connect: notify, update UI, run on_connect_cmd."""
        self.notify(message, timeout=0.75)
        self._set_conn_status("Connected")
        self._update_title()
        inp = self.query_one("#cmd", Input)
        prefix = cmd_prefix(self.cfg)
        inp.placeholder = f"{prefix} for REPL, {PALETTE_HOTKEY} for palette"
        inp.focus()
        self._sync_hw_buttons()
        connect_cmds: list[str] = []
        auto_cmd = self.cfg.get("on_connect_cmd", "")
        if auto_cmd:
            connect_cmds.extend(auto_cmd.replace("\\n", "\n").split("\n"))
        tui_cmd = self.cfg.get("tui_on_connect_cmd", "")
        if tui_cmd:
            connect_cmds.extend(tui_cmd.replace("\\n", "\n").split("\n"))
        if connect_cmds:
            self._run_lines(connect_cmds, delay=0.2)

    def _on_connect_failed(self) -> None:
        """TUI post-connect failure: update status, auto-reconnect."""
        self._engine.reader_stopped.set()
        self._set_conn_status("Disconnected")
        if self.cfg.get("auto_reconnect"):
            self._auto_reconnect()

    def _auto_reconnect(self) -> None:
        """Start the auto-reconnect worker unless one is already running.

        Called only on the main thread -- directly from _on_connect_failed,
        or via call_from_thread from the reader's on_disconnect -- so the
        guard is race-free: _reconnecting is checked and set before the
        worker spawns.  Without it a second disconnect would launch a
        duplicate reconnect loop contending on the shared stop_event.
        """
        if self._reconnecting:
            return
        self._reconnecting = True
        self._run_reconnect()

    @work(thread=True)
    def _run_reconnect(self) -> None:
        """Background thread: retry connecting until success or stop."""
        self._engine.stop_event.clear()
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
        except SHUTDOWN_RACE:
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
                self._status_bar_timer = self.set_timer(timeout, self._clear_status_bar)
            else:
                self._clear_status_bar()
        except SHUTDOWN_RACE:
            pass  # widgets gone during shutdown

    def _clear_status_bar(self) -> None:
        """Clear the status bar text and hide the widget."""
        try:
            label = self.query_one("#status-bar", Label)
            label.update("")
            label.remove_class("visible")
            self._status_bar_timer = None
        except SHUTDOWN_RACE:
            pass  # timer fired after widgets unmounted

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

            self.push_screen(
                ConfirmDialog(message), callback=_on_result
            )  # ty: ignore[no-matching-overload]

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
        # Event-handler safety net: a bug in any of the UI-update
        # helpers below could otherwise propagate into Textual and
        # crash the app.  _report_exception formats type, message,
        # and source location in a user-visible red status line --
        # nothing is hidden, the failure is shown; bare catch is
        # appropriate at the top of an event handler.
        try:
            self.notify("Disconnected", severity="warning", timeout=0.75)
            self._set_conn_status("Disconnected")
            self._update_title()
            try:
                inp = self.query_one("#cmd", Input)
                prefix = cmd_prefix(self.cfg)
                inp.placeholder = f"{prefix} for REPL, {PALETTE_HOTKEY} for palette"
            except SHUTDOWN_RACE:
                pass  # widgets gone during shutdown
            self._sync_hw_buttons(reset=True)
        except Exception as e:  # noqa: BLE001 -- reported via _report_exception
            self._report_exception(e)

    def _sync_hw_visibility(self) -> None:
        """Show or hide DTR/RTS/Break buttons based on flow_control config."""
        show = self.cfg["serial"]["flow_control"] == "manual"
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
            # Per-step breakdown: cfg["_migration_steps"] holds one
            # line per step that ran an actual migrator (no-op
            # versions are skipped).  Multi-version jumps happen
            # rarely (once per cfg, on a major upgrade), so a few
            # extra status lines is more reassuring than a single
            # summary header.
            for step in cfg.pop("_migration_steps", []):
                self._status(f"  {step}", "yellow")
        else:
            # No migration ran, but a stale _migration_steps key
            # could still be present if the cfg was hand-edited;
            # drop it silently.
            cfg.pop("_migration_steps", None)
        for w in cfg.pop("_config_warnings", []):
            self._status(f"Config warning: {w}", "yellow")
        # Cfg reload may swap the active profile out from under us; rebuild
        # suggester commands so completion reflects the post-load state.
        self._rebuild_suggester_commands()
        self.repl.ctx.ns("flags")["hex_mode"] = cfg.get("hex_mode", False)
        self._show_line_numbers = cfg.get("show_line_numbers", False)
        self.repl.replace_cfg(cfg, path)
        self.config_path = path
        self.history = self._load_history()
        self._history_idx = -1
        self.repl.ctx.config_path = path
        self.repl.ctx.fs.ss_dir = self.repl.ss_dir
        self.repl.ctx.fs.scripts_dir = self.repl.scripts_dir
        self.repl.ctx.fs.proto_dir = self.repl.proto_dir
        self.repl.ctx.fs.cap_dir = self.repl.cap_dir
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

    def _load_and_report(self, result: LoadResult, source: str = "") -> None:
        """Register loaded plugins/transforms and report status to the terminal.

        Shows loaded plugin names, warnings for skipped files (no COMMAND
        or TRANSFORM), and errors for files that raised exceptions.

        The status message includes the source directory (e.g. "from
        global" or "from <config-name>") so it's clear when config-folder
        plugins are running -- a user opening an unfamiliar config
        folder should be able to notice "3 plugins loaded from <cfg>"
        at a glance and review them before trusting the session.

        Args:
            result: LoadResult from load_plugins_from_dir.
            source: Label for where these came from (``"global"`` or a
                config name).  Falls back to the source stored on the
                first plugin info if empty; if still empty, the
                ``"from ..."`` suffix is omitted.
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
            if not source and result.plugins:
                source = result.plugins[0].source
            where = f" from {source}" if source else ""
            self.repl.ctx.io.status(
                f"Loaded {len(loaded)} plugin(s){where}: " + ", ".join(loaded),
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
            self.repl.ctx.io.status(
                f"Unloaded {len(to_remove)} plugin(s): " + ", ".join(to_remove),
            )
        from termapy.env_flags import TRUSTED_PLUGINS_ONLY
        if TRUSTED_PLUGINS_ONLY:
            self._rebuild_suggester_commands()
            return
        cfg_name = Path(config_path).stem
        self._load_and_report(
            load_plugins_from_dir(cfg_plugins_dir(config_path), cfg_name),
            source=cfg_name,
        )
        self._rebuild_suggester_commands()

    def _switch_to_cli(self) -> CmdResult:
        """Switch to CLI mode - sets flag and exits TUI."""
        self.switch_to = "cli"
        # Stash current config path so the mode loop can pass it to CLI
        self._switch_config_path = self.config_path
        self.exit()
        # Mode switch is intercepted by the outer mode loop; no value
        # to capture reaches a script.
        return CmdResult.ok(value="cli")

    def _switch_to_vt100(self, demo: bool = False) -> CmdResult:
        """Switch to VT100 passthrough mode - sets flags and exits TUI.

        The outer mode loop sees the switch, runs ``run_vt100_mode``, and
        (because we came from the TUI) returns here on Ctrl-] -- a reversible
        toggle like ``/cli`` <-> ``/tui``.

        Args:
            demo: When True (``/demo.vt100``), force the ``DEMO_VT100``
                widget-tour device instead of the currently loaded device.
                The current config is still restored on return to the TUI.
        """
        self.switch_to = "vt100"
        self._switch_vt100_demo = demo
        self._switch_config_path = self.config_path
        self.exit()
        return CmdResult.ok(value="vt100")

    def _start_demo(self, args: str = "") -> CmdResult:
        """Set up and switch to the built-in demo device config.

        Args:
            args: Optional ``--force`` to overwrite existing demo config.
        """
        force = "--force" in args.lower()
        self._start_demo_async(force)
        # Demo setup runs in a background worker; this returns
        # immediately, so the value reports what was requested.
        return CmdResult.ok(value="demo")

    @work(thread=True)
    def _start_demo_async(self, force: bool) -> None:
        """Background thread for demo setup so status messages render."""
        try:
            verbose_on = self.repl.ctx.output_level == "verbose"
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
        except CONFIG_LOAD_ERRORS as e:
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
        port = cfg["serial"]["port"]
        # comports() failures are environmental: OSError on udev / IOKit
        # / WMI hiccups, ImportError on platforms without list_ports.
        # Treat either as "no ports visible" and fall through.
        try:
            from serial.tools.list_ports import comports

            available = {p.device for p in comports()}
        except (OSError, ImportError):
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
            cfg["serial"]["port"] = port
        self._switch_config(cfg, path)
        self._show_config_info(path)

    def _on_config_picked(self, result: tuple | None) -> None:
        if result is None:
            return
        action = result[0]
        if action == "load":
            try:
                cfg = load_config(result[1])
            except CONFIG_LOAD_ERRORS as e:
                self._status(f"Failed to load config: {e}", "red")
                return
            self._check_port_and_switch(cfg, result[1])
        elif action == "new":
            self._new_config()
        elif action == "edit":
            try:
                cfg = load_config(result[1])
            except CONFIG_LOAD_ERRORS as e:
                self._status(f"Failed to load config: {e}", "red")
                return
            self.push_screen(
                ConfigEditor(cfg, result[1]),
                callback=self._on_config_result,
            )
        elif action == "delete":
            is_active = result[1] == self.config_path
            cfg_path_str = result[1]

            def _after_delete():
                # Clean up any stale desktop launcher that pointed
                # at this cfg; the user just told us they don't
                # want the cfg anymore, so the icon would be a
                # dead link.  Silent best-effort -- a missing
                # launcher is the common case, and a permission
                # failure shouldn't block the cfg delete.
                from termapy.builtins.commands._cfg_icon import (
                    find_launcher_for_cfg,
                    remove_launcher_at,
                )
                launcher = find_launcher_for_cfg(Path(cfg_path_str))
                if launcher is not None:
                    try:
                        remove_launcher_at(launcher)
                        self._status(
                            f"Removed desktop launcher: {launcher.name}",
                            "green",
                        )
                    except OSError as e:
                        self._status(
                            f"Launcher cleanup failed: {e}", "yellow",
                        )
                if is_active:
                    self.config_path = ""
                    self.push_screen(
                        ConfigPicker(""),
                        callback=self._on_config_picked,
                    )

            self._confirm_delete(result[1], "config", on_deleted=_after_delete)

    def _on_config_result(self, *args, **kwargs):
        """Delegate to ``pickers.on_config_result``."""
        from termapy.pickers import on_config_result
        return on_config_result(self, *args, **kwargs)
    def _port_info_str(self) -> str:
        """Format port info like 'COM4 115200 8N1' for title bar.

        Brackets were previously wrapped around the string but interacted
        badly with Rich markup parsing -- the closing ``]`` was consumed
        as a malformed markup tag terminator.  The bare label is cleaner
        and fits the title-bar button without escaping concerns.

        When connected, shows the actual device name rather than the
        config spec, so a user running with ``"port": "A1B2C3D4|COM3"``
        sees ``COM4 115200 8N1`` in the title, not the raw spec.
        """
        from termapy.config import connection_string

        actual = ""
        if self.engine.is_connected and self.engine.port_obj is not None:
            actual = getattr(self.engine.port_obj, "port", "") or ""
        return connection_string(self.cfg, "short", actual_port=actual)

    def _format_title_tooltip(self, *args, **kwargs):
        """Format a title-bar tooltip.  Implementation in ``title_bar``."""
        from termapy.title_bar import format_title_tooltip
        return format_title_tooltip(self, *args, **kwargs)

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
        """Refresh title-bar widgets.  Implementation in ``title_bar``."""
        from termapy.title_bar import update_title
        update_title(self)
    def _port_button_tooltip(self) -> str:
        """Build the title-bar port button tooltip with chip info.

        Uses the shared three-section layout (title / kv body / action).
        The title is the device name from cfg["serial"]["port"].  The body shows
        chip identification fields from port_control.gather_chip_facts():
        description, manufacturer (descriptor / INF), vendor (silicon
        vendor by VID), model, USB speed, VID:PID, location (bus path),
        max baud, and the in_use status.  Linux-only fields (driver,
        latency_timer, negotiated link speed) appear automatically when
        available.

        Falls back to a brief "no USB chip info available" body when the
        port is not enumerable (DEMO, unplugged cable, non-USB device).
        """
        from termapy import port_control

        port_name = self.cfg["serial"]["port"] or "(none)"
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
                "vendor",
                "model",
                "usb_speed",
                "vid_pid",
                "location",
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
        return self._format_title_tooltip(port_name, pairs, "select serial port")

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
            self._update_conn_tooltip(widget)
        except SHUTDOWN_RACE:
            pass  # widgets gone during shutdown

    def _update_conn_tooltip(self, *args, **kwargs) -> None:
        """Refresh the port-button tooltip.  Implementation in ``title_bar``."""
        from termapy.title_bar import update_conn_tooltip
        update_conn_tooltip(self, *args, **kwargs)
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
            except CONFIG_LOAD_ERRORS as e:
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
        "btn-cmds": "_show_commands",  # REPL command picker (full plugin list)
        "btn-help": "_btn_help",  # open help
        "btn-log": "_btn_log",  # open session log
        "btn-ss-dir": "action_open_screenshot",  # open screenshot folder
        "btn-cap-dir": "_open_captures_dir",  # open captures folder
        # Pickers
        "btn-scripts": "_btn_scripts",  # script picker
        "btn-proto": "_btn_proto",  # protocol test picker
        "btn-cfg": "_btn_cfg",  # config picker
        "btn-update": "_btn_update",  # update-available dialog
        # Recorder (next to REPL prompt)
        "btn-record": "_btn_record",  # toggle /run.record
        # Find bar (ephemeral, next to REPL prompt; visible while /find active)
        "btn-find-prev": "_btn_find_prev",  # /find.prev
        "btn-find-next": "_btn_find_next",  # /find.next
        "btn-find-close": "_btn_find_close",  # /find.clear
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

    def _btn_record(self) -> None:
        """Toggle /run.record by dispatching the REPL command.

        The button stores no recording state of its own; it queries
        ``ctx.internal.is_recording()`` (the single source of truth,
        backed by the recorder builtin's module state) and dispatches
        the appropriate form of ``/run.record``.  All work -- file
        open, observer registration, state tracking, no-clobber
        guarantee -- lives in ``builtins/commands/_run_record.py``.
        """
        is_rec = self.ctx.internal.is_recording
        if is_rec is not None and is_rec():
            # Active: dispatch bare /run.record to stop.  The handler
            # closes the file, deregisters the observer, and prints
            # the "Recorded N commands" message.
            self._dispatch_on_thread("/run.record")
            self._refresh_record_button()
        else:
            # Idle: prompt for the filename, then dispatch
            # /run.record <name>.  The modal handles cancel as no-op.
            self.push_screen(
                FilenameDialog(
                    title="Record to .run file:",
                    placeholder="e.g. smoke_test",
                ),
                callback=self._on_record_filename,
            )

    def _on_record_filename(self, name: str | None) -> None:
        """Modal callback for the Record button's filename prompt."""
        if not name:
            return  # cancelled
        self._dispatch_on_thread(f"/run.record {name}")
        self._refresh_record_button()

    def _refresh_record_button(self) -> None:
        """Pure visual sync.  Reads recorder state, writes button props.

        Zero recording logic: if the button were removed entirely
        the recorder still works identically.  See known v1
        limitation in ``/run.record`` long-help: typing
        ``/run.record`` in the REPL doesn't refresh this label
        until the next click or visual sync.
        """
        try:
            btn = self.query_one("#btn-record", Button)
        except NoMatches:
            return  # Record button hidden via record_enabled=false
        is_rec = self.ctx.internal.is_recording
        if is_rec is not None and is_rec():
            btn.label = "<Stop>"
            btn.variant = "error"
        else:
            btn.label = "<Rec>"
            btn.variant = "success"

    # IDs of widgets that share the FindBar's show/hide lifecycle.
    _FIND_BAR_WIDGETS = (
        "#find-bar", "#find-status",
        "#btn-find-prev", "#btn-find-next", "#btn-find-close",
    )

    def _update_find_bar(self, state: dict | None) -> None:
        """Switch between live #output and the frozen #output-find view.

        ``state=None`` hides the frozen view + the FindBar, shows
        #output, restores the Record button.  ``state`` non-None
        builds a markup-styled copy of the captured scrollback
        (matched lines [reverse]; the current match is identified
        by viewport position, not separate styling), writes it into
        #output-find, hides #output, shows the frozen view, scrolls
        so the current match is centered.
        """
        try:
            output = self.query_one("#output", RichLog)
            output_find = self.query_one("#output-find", RichLog)
            status = self.query_one("#find-status", Label)
        except NoMatches:
            return  # widget tree not yet mounted (rare)
        active = state is not None
        # FindBar widgets and the Record button toggle in opposite
        # directions: find takes the row, Record steps aside.
        for sel in self._FIND_BAR_WIDGETS:
            self.query_one(sel).display = active
        try:
            self.query_one("#btn-record", Button).display = not active
        except NoMatches:
            pass  # Record button hidden via record_enabled=false

        if not active:
            # Clean exit: hide frozen view, restore live #output.
            # Optionally scroll #output to the same line the user
            # was sitting on in the find view, so the visual
            # context is preserved across the close.  Skipped
            # when auto-dismiss fires from _write_batch (user's
            # focus has moved on; fresh data is the new interest).
            restore_line = self._find_last_current_line
            output_find.display = False
            output_find.clear()
            output.display = True
            self._find_overlay_active = False
            self._find_render_key = None
            self._find_last_current_line = None
            if (
                self._find_restore_scroll_on_close
                and restore_line is not None
            ):
                # Defer so #output has its layout back before we
                # compute its viewport-center scroll target.
                self.call_after_refresh(
                    self._scroll_to_find_match, restore_line, output,
                )
            return

        total = state["total"]
        # Slim label: just "n/m"; the highlighted lines in the
        # frozen view ARE the preview, no snippet text needed.
        if total == 0:
            status.update(f" 0/0 '{state['pattern']}' ")
            # Zero matches -- nothing to freeze.  Leave the live
            # #output visible so the user can keep working.
            output_find.display = False
            output.display = True
            self._find_overlay_active = False
            self._find_render_key = None
            return
        status.update(f" {state['index'] + 1}/{total} ")

        # Render-cache key: same matches + same scrollback text
        # means we already painted #output-find; just re-scroll.
        # New /find or changed scrollback -> rebuild.
        matches = state["matches"]
        scrollback_text = state["scrollback_text"]
        render_key = (
            id(scrollback_text),  # cheap proxy for "same snapshot"
            len(matches),
            matches[0][0] if matches else None,
            matches[-1][0] if matches else None,
        )

        if render_key != self._find_render_key:
            # Build once.  Every matched line gets [reverse]; the
            # CURRENT match isn't separately styled -- viewport
            # position (centered) identifies it.  Rewriting per-
            # navigation would be wasteful AND races with
            # RichLog auto_scroll (which is why we set
            # auto_scroll=False on this widget).
            #
            # Per-line writes (not one big Text with embedded \n)
            # because the live #output also writes line by line --
            # batching into a single Text causes Rich to wrap the
            # whole block as one paragraph, which produces
            # different break points than #output uses for the
            # same source lines.
            #
            # Explicit width= is critical: #output-find has been
            # display=False since startup, so its own size.width
            # is 0 (never laid out).  Writing without an explicit
            # width makes Rich fall back to a default (~80 cols)
            # and wrap aggressively -- you can see the wrap
            # mismatch by comparing the same line in #output
            # (full width) and #output-find (wrapped narrower).
            # We borrow #output's current content width since
            # both widgets share the same CSS slot and will end
            # up with the same width once #output-find is shown.
            wrap_width = output.content_size.width or output.size.width
            matched_set = {m[0] for m in matches}
            output_find.clear()
            for i, line in enumerate(
                scrollback_text.splitlines(), start=1,
            ):
                if i in matched_set:
                    output_find.write(
                        Text(line, style="reverse"),
                        width=wrap_width,
                    )
                else:
                    output_find.write(line, width=wrap_width)
            self._find_render_key = render_key

        # Swap visibility (idempotent if already in find mode)
        # and scroll to the current match.  call_after_refresh
        # so layout has settled before we compute viewport math.
        output.display = False
        output_find.display = True
        self._find_overlay_active = True
        current_line = state["line_no"]
        # Remember for the close-restore path so #output can
        # scroll to this same line when find ends.
        self._find_last_current_line = current_line
        self.call_after_refresh(
            self._scroll_to_find_match, current_line, output_find,
        )

    def _scroll_to_find_match(
        self, line_no: int, target: RichLog | None = None,
    ) -> None:
        """Scroll target RichLog so ``line_no`` lands at viewport middle.

        With the v2 design (matches visibly highlighted in the
        frozen view), middle is preferred over top: the user can
        see context above and below the current match, and the
        [reverse bold] styling makes the current line easy to spot
        regardless of vertical position.

        Always centers, even if the line is already visible.  An
        earlier "keep visible if already in viewport" optimization
        made nearby matches NOT reposition -- which broke the
        promise that the current match sits in the middle.
        """
        if target is None:
            try:
                target = self.query_one("#output-find", RichLog)
            except NoMatches:
                return
        target_y = max(0, line_no - 1)  # 1-based -> 0-based
        centered = max(0, target_y - target.size.height // 2)
        target.scroll_to(y=centered, animate=False)

    def _btn_find_prev(self) -> None:
        # Quiet dispatch: clicking the button shouldn't fill the
        # scrollback with "/find.prev" echo lines.
        self._dispatch_quiet("/find.prev")

    def _btn_find_next(self) -> None:
        self._dispatch_quiet("/find.next")

    def _btn_find_close(self) -> None:
        self._dispatch_quiet("/find.clear")

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

    def _show_port_picker(self, *args, **kwargs):
        """Delegate to ``info_views.show_port_picker``."""
        from termapy.info_views import show_port_picker
        show_port_picker(self, *args, **kwargs)
    def _update_port(self, *args, **kwargs) -> None:
        """Refresh port-name display.  Implementation in ``title_bar``."""
        from termapy.title_bar import update_port
        update_port(self, *args, **kwargs)
        # Connection failure already reported by _try_open_port

    def _on_port_picked(self, *args, **kwargs):
        """Delegate to ``pickers.on_port_picked``."""
        from termapy.pickers import on_port_picked
        on_port_picked(self, *args, **kwargs)
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

    def _on_quick_setup(self, *args, **kwargs):
        """Delegate to ``pickers.on_quick_setup``."""
        from termapy.pickers import on_quick_setup
        on_quick_setup(self, *args, **kwargs)
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
        except SHUTDOWN_RACE:
            pass

    def _palette_list_ports(self) -> None:
        self._dispatch_quiet("/port.list")

    def _palette_list_vars(self) -> None:
        self._dispatch_quiet("/var")

    def _palette_set_var(self) -> None:
        """Push a SetVarDialog; on submit, dispatch /var.set NAME value."""
        from termapy.dialogs.set_var_dialog import SetVarDialog

        def _on_submit(result: tuple[str, str] | None) -> None:
            if result is None:
                return
            name, value = result
            # Accept either "TIMEOUT" or "$(TIMEOUT)" in the Name
            # field -- strip the $(...) wrapping so users can paste
            # a variable reference from elsewhere as-is.  Without
            # this, the REPL var transform would expand $(TIMEOUT)
            # before /var.set runs, producing the wrong dispatch.
            if name.startswith("$(") and name.endswith(")"):
                name = name[2:-1].strip()
            self._dispatch_quiet(f"/var.set {name} {value}")

        self.push_screen(SetVarDialog(), callback=_on_submit)

    def _palette_toggle_line_numbers(self) -> None:
        self._dispatch_quiet("/term.line_no")

    def _palette_toggle_echo(self) -> None:
        self._dispatch_quiet("/term.echo")

    def _palette_toggle_timestamps(self) -> None:
        self._dispatch_quiet("/term.timestamps")

    def _palette_toggle_hex(self) -> None:
        self._dispatch_quiet("/term.hex")

    def _palette_toggle_line_endings(self) -> None:
        self._dispatch_quiet("/term.line_endings")

    def _palette_term_info(self) -> None:
        self._dispatch_quiet("/term.info")

    def _palette_ss_svg(self) -> None:
        # call_after_refresh defers one tick so the CommandPalette
        # has finished dismissing before save_screenshot walks the
        # widget tree -- without this, the palette modal can end up
        # in the snapshot.
        self.call_after_refresh(self.repl.dispatch, "ss.svg")

    def _palette_ss_txt(self) -> None:
        # Same one-tick defer as ss.svg above; the text snapshot
        # path also reads the current screen, so the palette must
        # be gone first.
        self.call_after_refresh(self.repl.dispatch, "ss.txt")

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

    def _palette_help(self) -> None:
        self._hook_help_open(None, "")

    def _palette_find(self) -> None:
        """Prompt for a pattern, then dispatch ``/find <pattern>``."""
        self._prompt_then_dispatch("Find pattern:", "/find ")

    def _palette_grep(self) -> None:
        """Prompt for a pattern, then dispatch ``/grep <pattern>``."""
        self._prompt_then_dispatch("Grep pattern:", "/grep ")

    def _palette_search_help(self) -> None:
        """Prompt for a term, then dispatch ``/search <term>``."""
        self._prompt_then_dispatch("Search help for:", "/search ")

    def _prompt_then_dispatch(
        self, title: str, command_prefix: str,
    ) -> None:
        """Push a single-line input prompt; on submit, dispatch a REPL line.

        The dispatched line is ``command_prefix`` concatenated with
        the user input.  Uses the quiet dispatch path so the
        synthesized REPL line doesn't echo into the scrollback (same
        pattern as the FindBar buttons).  Cancel and empty input are
        no-ops -- no spurious blank-arg dispatches.
        """
        from termapy.dialogs.filename_dialog import FilenameDialog

        def _on_submit(value: str | None) -> None:
            if value:
                self._dispatch_quiet(command_prefix + value)

        self.push_screen(
            FilenameDialog(title=title, placeholder="regex"),
            callback=_on_submit,
        )

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
        except SHUTDOWN_RACE:
            return
        self._line_counter = 0

    def _get_screen_text(self) -> str:
        if self._shutting_down:
            return ""
        try:
            log = self.query_one("#output", RichLog)
        except SHUTDOWN_RACE:
            return ""
        return "\n".join(strip.text for strip in log.lines)

    # ── File capture engine ──────────────────────────────────────────────────

    def _cap_start(self, *args, **kwargs):
        """Start a capture.  Implementation in ``capture_view``.

        Stub kept on the App because the host wires this onto
        ``internal.start_capture`` (bound-method reference).
        Signature pass-through so this file stays unaware of the
        real arg list, which lives in ``capture_view._cap_start``.

        Marshalled to the main thread: the ``/cap`` command runs on the
        dispatch worker thread, but ``_cap_start`` schedules Textual
        timers and mounts the progress overlay, which require the event
        loop.  ``_on_main`` runs it inline when already on the main thread.
        """
        from termapy.capture_view import _cap_start
        return self._on_main(_cap_start, self, *args, **kwargs)

    def _cap_stop(self) -> None:
        """Stop the current capture.  Implementation in ``capture_view``.

        Stub kept on the App because the host wires this onto
        ``internal.stop_capture`` and the cap-stop button looks it up
        by attribute name.  Marshalled to the main thread for the same
        reason as ``_cap_start`` -- ``/cap.stop`` arrives on the worker
        thread but the overlay teardown is Textual work.
        """
        from termapy.capture_view import _cap_stop
        self._on_main(_cap_stop, self)
    def _on_capture_complete(self, result: CaptureResult) -> None:
        """Called by CaptureEngine when capture finishes (unused for now)."""
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
        # If /find's frozen view is up, new live data on the
        # underlying #output is the user's signal that they're
        # done with find; auto-dismiss so the fresh output isn't
        # hidden behind the snapshot.  Dispatched via the plugin's
        # clear handler so the dismiss state stays consistent.
        # Suppress the close-restore scroll: the user wasn't
        # asking to leave find at a particular line; new data is
        # arriving and they should see it at the bottom of
        # #output, not be teleported back to the find position.
        if self._find_overlay_active:
            self._find_restore_scroll_on_close = False
            try:
                from termapy.builtins.commands.find import _handler_clear
                _handler_clear(self.repl.ctx, "")
            except Exception:
                # Defensive: a broken plugin must not block live data.
                self._find_overlay_active = False
            finally:
                self._find_restore_scroll_on_close = True
        try:
            log = self.query_one("#output", RichLog)
        except SHUTDOWN_RACE:
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
                prefix += f"[{format_timestamp()}] "
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
            if self._capture.feed_text(stripped):
                # Write failed mid-capture -- stop so the user sees the abort
                # instead of silently losing the rest of the data.
                self._cap_stop()

    @on(Input.Changed, "#cmd")
    def _on_cmd_changed(self, event: Input.Changed) -> None:
        """Color input red when typing a REPL command."""
        prefix = cmd_prefix(self.cfg)
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
        self._set_input_busy(True)
        self._dispatch_on_thread_interactive(cmd)

    def _restore_input_placeholder(self) -> None:
        """Restore the command input placeholder after execution."""
        inp = self.query_one("#cmd", Input)
        inp.placeholder = self._saved_placeholder

    def _set_input_busy(self, busy: bool) -> None:
        """Grey out the prompt + bottom-bar buttons while a typed command runs.

        Visual companion to ``_dispatch_guard``: the guard guarantees one
        command at a time, and this makes that obvious on the common typed-
        command path so a second Enter or button press is plainly
        unavailable.  Skipped when a script overlay owns the bar -- scripts
        manage their own Stop UI and must keep that button clickable.
        """
        try:
            bar = self.query_one("#bottom-bar")
            if bool(bar.query("#script-stop")):
                return  # script overlay active; it owns the bar
            bar.disabled = busy
            if not busy:
                self.query_one("#cmd", Input).focus()
        except SHUTDOWN_RACE:
            pass  # widget tree gone during teardown

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
        try:
            self.call_from_thread(self._set_input_busy, False)
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
        # Return the sent text so scripts can confirm what was written.
        return CmdResult.ok(value=text)

    _BUSY_MSG = "Busy - a command is still running."

    def _dispatch_single(self, cmd: str) -> CmdResult:
        """Dispatch a single command (delegates to repl.dispatch_full).

        Safe to call from any thread - output helpers (_status,
        _write_output_markup) detect their thread internally.  Serialized
        via _dispatch_guard so a second command on another thread is
        refused rather than corrupting the shared PluginContext; nested
        ctx.dispatch on the same thread re-enters freely.
        """
        if not self._dispatch_guard.acquire(blocking=False):
            self._status(self._BUSY_MSG, "yellow")
            return CmdResult.fail(msg=self._BUSY_MSG)
        try:
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
        finally:
            self._dispatch_guard.release()

    def _dispatch_quiet(self, cmd: str) -> CmdResult:
        """Dispatch without echoing the command line to the output.

        Used by UI affordances (find-bar nav buttons, anything else
        that fires repeatedly from a click) where echoing every
        synthesized command would just clutter the scrollback.
        Status / serial / log paths are unchanged -- the only
        suppression is the input-echo line.  Shares _dispatch_guard with
        _dispatch_single so a button press during a running command is
        refused instead of racing it.
        """
        if not self._dispatch_guard.acquire(blocking=False):
            self._status(self._BUSY_MSG, "yellow")
            return CmdResult.fail(msg=self._BUSY_MSG)
        try:
            return self.repl.dispatch_full(
                cmd,
                log=self._log_line,
                echo_markup=lambda _markup: None,
                status=self._status,
                serial_write=self._serial_write,
                serial_write_raw=self._send_serial_raw,
                is_connected=lambda: self.is_connected,
                eol_label=eol_label,
            )
        finally:
            self._dispatch_guard.release()

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

    def _show_commands(self, *args, **kwargs):
        """Delegate to ``info_views.show_commands``."""
        from termapy.info_views import show_commands
        show_commands(self, *args, **kwargs)

    def _hide_history(self) -> None:
        popup = self.query_one("#history-popup", OptionList)
        popup.remove_class("visible")
        self.query_one("#cmd", Input).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Handle selection from the REPL command picker or run-script picker.

        The curated palette is no longer routed through this widget --
        it uses Textual's built-in CommandPalette (see PaletteProvider).
        This handler now covers only the ``#history-popup`` modes:
        REPL command picker (``repl:`` ids) and quick-run (``run:`` ids).
        """
        if event.option_list.id != "history-popup":
            return
        self._hide_history()
        opt_id = str(event.option.id) if event.option.id is not None else ""
        if opt_id.startswith("run:"):
            name = opt_id.split(":")[1]
            prefix = cmd_prefix(self.cfg)
            self._dispatch_on_thread(f"{prefix}{name}")
        elif opt_id.startswith("repl:"):
            name = opt_id.split(":")[1]
            prefix = cmd_prefix(self.cfg)
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
            if self._engine.serial_claimed:
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

    def action_btn_help(self) -> None:
        self._btn_help()

    def action_btn_cfg(self) -> None:
        if self.cfg.get("cfg_enabled", True):
            self._btn_cfg()

    def action_btn_run(self) -> None:
        if self.cfg.get("run_enabled", True):
            self._btn_scripts()

    def action_btn_proto(self) -> None:
        if self.cfg.get("proto_enabled", True):
            self._btn_proto()

    def action_btn_open_config(self) -> None:
        self._btn_title_center()

    def action_help_quit(self) -> None:
        """Suppress Textual's "Press Ctrl+Q to quit" toast on Ctrl+C.

        Ctrl+Q is shown in the footer; the redundant toast just adds noise.
        """
        return

    def action_clear_log(self) -> None:
        try:
            self.query_one("#output", RichLog).clear()
        except SHUTDOWN_RACE:
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
        prefix = cmd_prefix(self.cfg)
        try:
            self.query_one("#btn-cmds", Button).label = prefix
            self.query_one(
                "#cmd", Input
            ).placeholder = f"{prefix} for REPL, {PALETTE_HOTKEY} for palette"
        except SHUTDOWN_RACE:
            pass  # prefix changed before mount or during teardown

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
        hk = _hotkey_label("btn-scripts")
        suffix = f"{count} available" if count else "empty"
        btn.tooltip = f"Run a script ({hk}, {suffix})."

    def _sync_proto_button(self) -> None:
        """Update the Proto button tooltip with file counts."""
        btn = self.query_one("#btn-proto", Button)
        count = self._count_files(self.repl.proto_dir, FOLDER_PATTERNS["proto"])
        hk = _hotkey_label("btn-proto")
        suffix = f"{count} available" if count else "empty"
        btn.tooltip = f"Protocol test scripts ({hk}, {suffix})."

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

    def _set_progress_label(self, text: str) -> None:
        """Write progress text to the bottom-bar status label (main thread).

        Empty text hides the label.  Same mechanism used by the capture
        progress overlay -- Input.placeholder doesn't reliably re-render
        from background threads, the status label does.
        """
        try:
            label = self.query_one("#status-bar", Label)
            label.update(text)
            if text:
                label.add_class("visible")
            else:
                label.remove_class("visible")
        except SHUTDOWN_RACE:
            pass

    def _apply_port_effects(self, effects: dict) -> None:
        """Apply side effects from a port_control function (used by port plugin).

        cfg_update keys arrive flat (e.g. ``"baud_rate"``); route each
        to ``cfg["serial"]`` if it lives there, top level otherwise.
        Keeps port_control producers oblivious to the v22 nesting.
        """
        if effects.get("cfg_update"):
            cfg = self.repl._cfg_data
            serial = cfg.get("serial", {})
            for key, val in effects["cfg_update"].items():
                if key in serial:
                    serial[key] = val
                else:
                    cfg[key] = val
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
        # baud_rate / port / etc. live under cfg["serial"] post-v22, so read
        # the current value from wherever the key sits (mirrors the routing
        # in repl._apply_cfg).  A naive self.cfg[key] KeyErrors on serial keys.
        serial = self.cfg.get("serial", {})
        old_val = serial[key] if isinstance(serial, dict) and key in serial else self.cfg[key]

        def on_result(confirmed: bool) -> None:
            if confirmed:
                self.repl._apply_cfg(key, new_val)

        # Wrap in a no-arg lambda: _on_main forwards to call_from_thread,
        # whose own first param is named `callback`, so passing push_screen
        # positionally + callback=on_result collides.  The lambda hands
        # call_from_thread a single callable instead.
        self._on_main(
            lambda: self.push_screen(  # ty: ignore[no-matching-overload]
                CfgConfirm(key, old_val, new_val), callback=on_result
            )
        )

    def _on_script_picked(self, *args, **kwargs):
        """Delegate to ``pickers.on_script_picked``."""
        from termapy.pickers import on_script_picked
        on_script_picked(self, *args, **kwargs)
    def _on_script_saved(self, path: str | None) -> None:
        if path:
            self._status(f"Script saved: {Path(path).name}", "green")
            self._sync_scripts_button()
        self._sync_proto_button()

    def _on_proto_picked(self, *args, **kwargs):
        """Delegate to ``pickers.on_proto_picked``."""
        from termapy.pickers import on_proto_picked
        on_proto_picked(self, *args, **kwargs)
    def _on_proto_saved(self, path: str | None) -> None:
        """Handle result from the ProtoEditor dialog.

        Args:
            path: Saved file path, or None if cancelled.
        """
        if path:
            self._status(f"Proto script saved: {Path(path).name}", "green")
            self._sync_proto_button()

    def _tui_hook_log_delete(self) -> CmdResult:
        """Delete the session log file on disk.

        This is the canonical name; ``/log.clear`` is a hidden legacy
        alias that forwards here (with a one-time deprecation note).
        Vocabulary: "clear" should mean "empty visible/transient state"
        (``/cls`` for the screen; ``/var.clear`` for variables;
        ``/seq.reset`` for counters), and "delete" should mean
        "permanently remove from disk."  Pre-rename behaviour
        conflated those.
        """
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
            # Return the deleted path so scripts can log / confirm.
            return CmdResult.ok(value=Path(log_path))
        except OSError as e:
            self._status(f"Delete failed: {e}", "red")
            return CmdResult.fail(msg=str(e))

    def _open_picker(self, *args, **kwargs):
        """Delegate to ``pickers.open_picker``."""
        from termapy.pickers import open_picker
        return open_picker(self, *args, **kwargs)
    _PROFILE_TMP_PREFIX = "_profile_tmp_"

    def _prof_dir(self) -> Path | None:
        """Return the prof/ directory, or None if no config loaded."""
        if not self.config_path:
            return None
        return Path(self.config_path).parent / "prof"

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
        except SHUTDOWN_RACE:
            pass  # overlay / input gone during teardown

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
        except SHUTDOWN_RACE:
            pass  # event posted after widget tree teardown

    def on_serial_terminal_delay_progress(self, event: DelayProgress) -> None:
        """Append a delay-progress bar to the script-overlay label.

        Ticks arrive every 0.25s; ``event.done`` fires once on the last
        tick so the label can be restored to its pre-delay text.
        """
        try:
            label_w = self.query_one("#script-label", Static)
            if event.done:
                label_w.update(self._script_last_label)
            else:
                label_w.update(f"{self._script_last_label}  {event.bar}")
        except SHUTDOWN_RACE:
            pass  # event posted after overlay torn down

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
                self._engine.serial_claimed = False
        except SHUTDOWN_RACE:
            pass  # event posted after widget tree teardown

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

        def _delay_progress(bar: str, done: bool) -> None:
            self.post_message(self.DelayProgress(bar, done))

        try:
            self.repl.run_script(
                path,
                write=self._status,
                dispatch=self._dispatch_single,
                profile=profile,
                verbose=verbose,
                progress=_progress,
                on_nest=_on_nest,
                delay_progress=_delay_progress,
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


def _run_proto_headless(args) -> None:
    """Run a .pro test script headlessly (no TUI) and write JSON results."""
    from termapy.protocol import run_proto_tests

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
    except CONFIG_LOAD_ERRORS as e:
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
    # run_proto_tests executes user-supplied .pro content, which drives
    # arbitrary command handlers and serial I/O; ValueError comes from
    # the parser specifically, everything else from the test session
    # is a trust-boundary catch.
    try:
        results = run_proto_tests(pro_path, cfg, template=template)
    except ValueError as e:
        print(f"termapy: {e}", file=sys.stderr)
        sys.exit(2)
    except BoundaryException as e:
        print(f"termapy: test error: {e}", file=sys.stderr)
        sys.exit(2)

    # Print summary
    s = results["summary"]
    total, passed, failed = s["total"], s["passed"], s["failed"]
    elapsed_ms = s["elapsed_ms"]
    status = "PASS" if failed == 0 else "FAIL"
    print(
        f"{results['meta']['script_name']}: {passed}/{total} {status} "
        f"({format_duration(elapsed_ms / 1000)})"
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
        except CONFIG_LOAD_ERRORS as e:
            print(f"termapy: failed to load demo config: {e}", file=sys.stderr)
            sys.exit(1)
        app = SerialTerminal(
            cfg,
            config_path=str(config_path),
            output_level=getattr(args, "output_level", None),
        )
        app.run()
        _reset_terminal()
        if app.switch_to:
            args.config = app.config_path
            args._vt100_demo = app._switch_vt100_demo
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
        except CONFIG_LOAD_ERRORS as e:
            print(
                f"termapy: failed to load config '{config_path}': {e}", file=sys.stderr
            )
            sys.exit(1)
        app = SerialTerminal(
            cfg,
            config_path=config_path,
            output_level=getattr(args, "output_level", None),
        )
        app.run()
        _reset_terminal()
        if app.switch_to:
            args.config = app.config_path
            args._vt100_demo = app._switch_vt100_demo
        return app.switch_to

    config_path, show_picker = _find_config()

    if config_path:
        try:
            cfg = load_config(config_path)
        except CONFIG_LOAD_ERRORS as e:
            print(
                f"termapy: failed to load config '{config_path}': {e}", file=sys.stderr
            )
            sys.exit(1)
        app = SerialTerminal(
            cfg,
            config_path=config_path,
            output_level=getattr(args, "output_level", None),
        )
        app.run()
        _reset_terminal()
        if app.switch_to:
            args.config = app.config_path
            args._vt100_demo = app._switch_vt100_demo
        return app.switch_to
    elif show_picker:
        cfg = default_cfg()
        app = SerialTerminal(
            cfg,
            config_path="",
            show_picker=True,
            output_level=getattr(args, "output_level", None),
        )
        app.run()
        _reset_terminal()
        if app.switch_to:
            args.config = app.config_path
            args._vt100_demo = app._switch_vt100_demo
        return app.switch_to
    else:
        from termapy.config import setup_demo_config

        config_path = str(setup_demo_config(cfg_dir(), force=True))
        try:
            cfg = load_config(config_path)
        except CONFIG_LOAD_ERRORS as e:
            print(f"termapy: failed to load demo config: {e}", file=sys.stderr)
            sys.exit(1)
        app = SerialTerminal(
            cfg,
            config_path=config_path,
            first_run=True,
            output_level=getattr(args, "output_level", None),
        )
        app.run()
        _reset_terminal()
        if app.switch_to:
            args.config = app.config_path
            args._vt100_demo = app._switch_vt100_demo
        return app.switch_to


if __name__ == "__main__":
    from termapy.entry import main

    main()
