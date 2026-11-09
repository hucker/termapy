#!/usr/bin/env python3
"""Textual TUI frontend: the app, its panes and buttons, and the wiring around the engines.

Usage:
    uv run termapy [config.json]

Runs well in most terminals (Windows Terminal, iTerm2, etc).
VS Code's integrated terminal can be jerky due to its rendering pipeline.
"""

import argparse
import os
import re
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path
from threading import Event
from types import MappingProxyType
from typing import Any, ParamSpec, TypeVar

import serial
from rich.style import Style
from rich.text import Text
from textual import events, on, work
from textual.actions import SkipAction
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Button, Input, Label, OptionList, RichLog, Static
from textual.widgets.button import ButtonVariant

from termapy import diag_log, port_control
from termapy.capture import (
    CaptureEngine,
    CaptureResult,
    format_capture_result,
)
from termapy.config import (
    CONFIG_LOAD_ERRORS,
    CURRENT_CONFIG_VERSION,
    cfg_data_dir,
    cfg_dir,
    cfg_history_path,
    load_config,
    on_connect_commands,
    open_with_system,
    rename_config,
    session_log_file,
)

# Config-path resolution lives in termapy.config_resolve (Textual-free).
# Re-exported under the original underscored names so existing
# `from termapy.app import _find_config` callers keep working while
# entry.py / cli_flags.py can import from config_resolve directly.
from termapy.config_resolve import (
    find_config as _find_config,
    proto_dir_for,
    proto_script_name,
    resolve_config as _resolve_config,
    resolve_proto_path,
)
from termapy.defaults import DEFAULT_CMD_PREFIX, cfg_slot, cfg_value, cmd_prefix, default_cfg
from termapy.dialogs import (
    CfgConfirm,
    ConfigEditor,
    ConfigPicker,
    ConfirmDialog,
    FilenameDialog,
    PortPicker,
    ProtoEditor,
    ProtoPicker,
    QuickSetup,
    ScriptEditor,
    ScriptPicker,
    UpdateAvailableDialog,
)
from termapy.folder_ops import stamped_path
from termapy.folders import FOLDER_EXTS, FOLDER_PATTERNS, ensure_folder
from termapy.help_tooltip import build_help_tooltip
from termapy.history_nav import HistoryNavigator, merge_history, read_history
from termapy.markup import escape_markup, markup_to_plain, received_json_markup
from termapy.palette_provider import PaletteProvider
from termapy.plugins import (
    BoundaryException,
    CapabilitySet,
    CmdResult,
    cfg_users,
)
from termapy.plugins.capabilities import STATE_MESSAGES
from termapy.plugins.views import Link, Piece
from termapy.proto_debug import ProtoDebugScreen
from termapy.protocol import ProtoScript, builtins_viz_dir, load_visualizers_from_dir
from termapy.repl import ReplEngine
from termapy.serial_engine import SerialEngine
from termapy.serial_port import eol_label_markup
from termapy.session_log import SessionLog
from termapy.terminal_host import TerminalHost
from termapy.widgets import (
    LINK_META,
    CommandSuggester,
    LinkLog,
    PromptInput,
    SelectableRichLog,
    StatusBar,
    without_color,
)

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

# ``_on_main``'s signature: the arguments ``fn`` takes, and what it returns.
_P = ParamSpec("_P")
_R = TypeVar("_R")


# scripting import kept low (below the module-level defs above); placement, not a cycle.
from termapy.scripting import (  # noqa: E402
    ANSI_RE,
    format_age,
    format_duration,
    format_size,
    format_timestamp,
    render_progress_bar,
    split_cmd_lines,
    transcript_commands,
)

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
    "btn-help": ("f1,ctrl+shift+1", "btn_help", "Help"),
    "btn-cfg": ("f2,ctrl+shift+2", "btn_cfg", "Cfg"),
    "btn-scripts": ("f3,ctrl+shift+3", "btn_run", "Run"),
    "btn-proto": ("f4,ctrl+shift+4", "btn_proto", "Proto"),
    "title-center": ("f10,ctrl+shift+5", "btn_open_config", "Open Config"),
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


class SerialTerminal(TerminalHost, App):
    """Textual app: scrolling output + local input line."""

    class SerialRx(Message):
        """Posted when the reader has queued RX events to render.

        Carries no payload: the events live in ``_rx_events`` and the
        handler drains whatever has accumulated.  This conflation is the
        point -- at most one of these is ever in flight, so a slow main
        thread cannot make the message queue grow without bound.
        """

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
    front_end = "tui"

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
    #custom-bar {
        width: auto;
        height: 1;
        margin: 0 1 0 2;
        display: none;
    }
    #custom-side {
        dock: right;
        width: auto;
        max-width: 24;
        height: 1fr;
        padding-top: 1;
        display: none;
        scrollbar-size-vertical: 1;
    }
    #custom-side.left {
        dock: left;
    }
    #custom-side Button {
        width: auto;
        min-width: 100%;
        height: 1;
        min-height: 1;
        border: none;
        margin: 0 0 1 0;
        padding: 0 1;
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
        Binding("ctrl+p", "command_palette", "Command Palette", show=False, priority=True),
        Binding("ctrl+s", "screenshot", "Screenshot", show=False),
        Binding("ctrl+t", "text_screenshot", "Text Screenshot", show=False),
        # Escape stops what runs; with nothing to stop it clears the prompt,
        # or, empty, is the device's (control_keys).
        Binding("escape", "stop_script", "Stop, clear, or Esc to the device", show=False),
        # Ctrl+C, Ctrl+D and Ctrl+Z the focused widget did not take go to the device
        # (/term.ctrl) while control_keys is on: a selection anywhere copies
        # first (Input, then the screen, pass Ctrl+C on when nothing is
        # selected), and the prompt keeps Ctrl+D and Ctrl+Z while it holds
        # text.
        # Replaces Textual's quit hint.
        Binding("ctrl+c", "send_control('c')", "Ctrl-C to the device", show=False),
        Binding("ctrl+d", "send_control('d')", "Ctrl-D to the device", show=False),
        Binding("ctrl+z", "send_control('z')", "Ctrl-Z to the device", show=False),
        # Shift+Up/Down from the prompt start a keyboard selection in the
        # output pane; once the pane has focus its own bindings take them.
        Binding("shift+up,shift+down", "select_output", "Select output", show=False),
        # F-keys for top-row buttons -- defined in _HOTKEYS (module top)
        # so the binding and the tooltip annotation share one source.
        *[Binding(k, a, lbl, show=False) for (k, a, lbl) in _HOTKEYS.values()],
        # Alt-key fallbacks: VS Code's integrated terminal (and a few
        # other hosts) captures Ctrl+P / Ctrl+S / Ctrl+T before the
        # shell sees them.  Alt-key bindings are rarely intercepted,
        # so they give every user a path that survives hostile
        # terminals.  Harmless everywhere else.
        Binding("alt+q", "quit", "Quit (alt)", show=False),
        Binding("alt+p", "command_palette", "Command Palette (alt)", show=False, priority=True),
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
        ("Connect / Disconnect", "_btn_title_right"),
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
        self.session_log = SessionLog()
        self.last_screenshot: str | None = None
        self.repl = ReplEngine(
            cfg,
            config_path,
            write=self._status,
        )
        self.repl.screen_json = True
        # Set by _load_history; declared first so _sync_history is safe
        # however early it runs.
        self._history_mtime_seen: float = 0.0
        self.history: list[str] = self._load_history()
        self._history_nav = HistoryNavigator()
        # A typed command or paste is in flight (_begin_busy / _end_busy);
        # its grey-bar look comes only after _BUSY_LOOK_DELAY_S.
        self._busy = False
        self._busy_look_timer: Timer | None = None
        self._busy_look_shown = False
        # On-connect runs not finished yet (_run_on_connect).  A paste or a
        # button's sequence waits for them, so its next line cannot reach the
        # device ahead of the setup they do.
        self._on_connect_runs = 0
        self._on_connect_lock = threading.Lock()
        # Counts every begin and end, so a busy-look timer that fires late
        # (after _end_busy) knows its command is over and does nothing.
        self._busy_generation = 0
        self._saved_placeholder = ""
        # Clickable output (_write_link_segments): link id -> Link, the
        # newest _LINK_LIMIT kept, so a long session's table stays bounded.
        self._links: OrderedDict[int, Link] = OrderedDict()
        self._next_link_id = 0
        # The output pane's row width, held here for the dispatch workers
        # (LinkLog.Width on every resize): a table is fitted to it (registry
        # table-fits-the-frontend).  None until the first layout.
        self._output_width: int | None = None
        self._suggester = CommandSuggester(complete_file=self.repl.complete_file_arg)
        self._cached_commands: list[str] = []
        self._popup_mode: str = "commands"
        self._line_counter: int = 0
        self._xfer_cancel = threading.Event()
        # RX handoff, reader thread -> main thread.  The reader appends
        # ordered events here and posts at most ONE SerialRx message; the
        # handler drains the lot.  Deliberately NOT call_from_thread: that
        # blocks the reader until the event loop services it, which (a) stalls
        # the reader long enough for the driver to silently drop bytes (see
        # config.SERIAL_RX_BUFFER_BYTES) and (b) deadlocks teardown, because
        # disconnect() waits on the reader from the very thread the reader is
        # waiting for.  Fire-and-forget removes both.
        self._rx_lock = threading.Lock()
        self._rx_events: list[tuple] = []
        self._rx_posted = False
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
        # Our own record of the main (event-loop) thread.  Textual keeps one
        # in App._thread_id, but that is private API: it is set by
        # _thread_init and zeroed after shutdown, and four output helpers
        # depend on it to decide whether to marshal.  Capturing it costs two
        # lines and removes the dependency.  Stamped here because the app is
        # constructed on the main thread, and again in on_mount, which is
        # authoritative -- it runs on the loop thread itself.
        self._main_thread_id = threading.get_ident()
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
        # False while new live data dismisses find: the user's interest moved
        # to the fresh output, so #output does not scroll back to the match.
        self._find_restore_scroll_on_close: bool = True

        # File capture engine
        # ``on_echo`` fires from ``_flush_bin`` on the READER thread, once per
        # record; it rides the RX queue like every other reader callback.  A
        # ``_status`` here was a blocking ``call_from_thread`` per line, which
        # stalled the reader and could not be joined by the teardown waiting
        # on it (docs/review/2026-10-10-v0.76.0-fable-5.1.md, C2).
        self._capture = CaptureEngine(
            on_echo=lambda line: self._rx_enqueue(("echo", line)),
            on_complete=lambda result: self._on_capture_complete(result),
        )

        # Serial engine (owns port, reader, connection state)
        self._engine = SerialEngine(
            cfg=cfg,
            capture=self._capture,
            open_fn=self._open_serial,
            log=self._log_line,
        )
        self._cap_timer: Timer | None = None
        self._cap_progress_timer: Timer | None = None
        # The running auto-reconnect's cancel token; a fresh one per run, so
        # an old loop that is still winding down can never act or block the
        # next.  SET means no reconnect is running (see ``_reconnecting``).
        self._reconnect_stop = threading.Event()
        self._reconnect_stop.set()
        # Set by _btn_exit / on_unmount before we start tearing the UI
        # down, so reader-thread callbacks that are still in flight
        # (already queued on the event loop) no-op instead of
        # touching widgets that are being unmounted.  Defense in depth;
        # the query_one sites still have try/except guards.
        self._shutting_down = False

    @property
    def cfg(self) -> MappingProxyType:  # ty: ignore[invalid-property-type-override] -- read-only by design, see below
        """Read-only view of the config dict (single source of truth in ReplEngine).

        ``TerminalHost`` declares ``cfg`` writable because the CLI and MCP
        hosts hold the engine's dict itself and its ``_switch_to_cfg_path``
        rebinds it.  The TUI overrides ``_switch_to_cfg_path``, that one
        writer, so nothing assigns here; its writes go through ``_apply_cfg``.
        """
        return self.repl.cfg

    @property
    def is_connected(self) -> bool:
        return self._engine.is_connected

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
        """Delegate to the single owner in config.cfg_history_path."""
        return cfg_history_path(self.config_path)

    _HISTORY_LIMIT = 30

    def _history_mtime(self) -> float:
        """The history file's mtime, or 0.0 when it does not exist."""
        try:
            return Path(self._history_path()).stat().st_mtime
        except OSError:
            return 0.0

    def _load_history(self) -> list[str]:
        """Load command history from disk (last _HISTORY_LIMIT entries)."""
        self._history_mtime_seen = self._history_mtime()
        return read_history(self._history_path(), self._HISTORY_LIMIT)

    def _sync_history(self) -> None:
        """Pick up lines the file gained since this session last read it.

        The history file is shared state: another session on the same
        config writes it on exit, and a command can be appended from
        outside (a test plan dropped in for the user to arrow through).
        Checked when an Up browse BEGINS -- one stat, never per keystroke
        -- so recall shows what is on disk now.
        """
        mtime = self._history_mtime()
        if mtime == self._history_mtime_seen:
            return
        self._history_mtime_seen = mtime
        self.history = merge_history(
            read_history(self._history_path(), self._HISTORY_LIMIT),
            self.history,
            self._HISTORY_LIMIT,
        )
        self._update_suggester()

    def _save_history(self) -> None:
        """Persist command history, MERGING with whatever the file holds.

        A plain overwrite would discard everything written since this
        session started: another instance's commands, or an external
        append.  Re-reading first makes concurrent sessions additive
        rather than last-exit-wins.
        """
        merged = merge_history(
            read_history(self._history_path(), self._HISTORY_LIMIT),
            self.history,
            self._HISTORY_LIMIT,
        )
        path = Path(self._history_path())
        try:
            # With no config loaded the file sits in the cfg root, which
            # the first write creates.
            ensure_folder(path.parent)
            path.write_text("\n".join(merged) + "\n" if merged else "", encoding="utf-8")
        except OSError:
            pass

    def _project_files(self) -> list[str]:
        """Return suggestion names for all editable project files.

        Scans run/ (.run) and proto/ (.pro).
        Skips ss/, plugin/, viz/, and .py files.
        """
        names: list[str] = []
        for d, pattern in [
            (self.repl.scripts_dir, FOLDER_PATTERNS["run"]),
            (self.repl.proto_dir, FOLDER_PATTERNS["proto"]),
        ]:
            if d.exists():
                for f in sorted(d.glob(pattern)):
                    if f.is_file():
                        names.append(f"{d.name}/{f.name}")
        return names

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
        title = cfg_value(self.cfg, "title") or self.config_path
        port_info = self._port_info_str()
        with Horizontal(id="title-bar"):
            from importlib.metadata import (
                PackageNotFoundError,
                version as _get_version,
            )

            from textual.widgets import Static

            # PackageNotFoundError fires when termapy is running from
            # a git clone without `pip install .` (dev setup).  Other
            # exceptions here would be bugs worth seeing.
            try:
                ver = _get_version("termapy")
            except PackageNotFoundError:
                ver = "?"
            # Each platform's window-close convention: top-right on Windows.
            exit_on_right = sys.platform == "win32"

            def _make_exit_btn() -> Button:
                b = Button(" X ", id="btn-exit", variant="error")
                b.tooltip = "Close connection and exit (Ctrl+Q)."
                return b

            if not exit_on_right:
                yield _make_exit_btn()
            help_btn = Button("Help", id="btn-help")
            help_btn.tooltip = build_help_tooltip(
                ver, _hotkey_label("btn-help"), prefix=cmd_prefix(self.cfg)
            )
            yield help_btn
            if cfg_value(self.cfg, "cfg_enabled"):
                cfg_btn = Button("Cfg", id="btn-cfg")
                cfg_btn.tooltip = f"New / Edit / Load config ({_hotkey_label('btn-cfg')})."
                yield cfg_btn
            if cfg_value(self.cfg, "run_enabled"):
                run_btn = Button("Run", id="btn-scripts")
                run_btn.tooltip = f"Run a script ({_hotkey_label('btn-scripts')})."
                yield run_btn
            if cfg_value(self.cfg, "proto_enabled"):
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
            center = Button(Text(title), id="title-center")
            center.tooltip = Text(
                f"Config: {self.config_path or 'none'}\n"
                f"Click to edit config ({_hotkey_label('title-center')})"
            )
            yield center
            yield Static("", id="title-spacer-r")
            left = Button(Text(port_info), id="title-left")
            left.tooltip = "Click to select serial port."
            yield left
            right = Button("Disconnected", id="title-right")
            yield right
            if exit_on_right:
                yield _make_exit_btn()
        max_lines = cfg_value(self.cfg, "max_lines")
        # Docks inside this container stay between the title bar and the
        # bottom bar; a dock on the screen would run over both.
        with Container(id="main-area"):
            yield LinkLog(
                highlight=False,
                markup=True,
                wrap=True,
                id="output",
                max_lines=max_lines,
                describe=self._describe_link,
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
            find_log = SelectableRichLog(
                highlight=False,
                markup=True,
                wrap=True,
                id="output-find",
                max_lines=max_lines,
                auto_scroll=False,
            )
            find_log.display = False
            yield find_log
            # The custom-button column (custom_buttons_side left/right),
            # filled by _sync_custom_buttons.
            yield VerticalScroll(id="custom-side")
        yield OptionList(id="history-popup")
        with Vertical(id="bottom-section"):
            with Horizontal(id="bottom-bar"):
                prefix = cmd_prefix(self.cfg)
                cmd_btn = Button(Text(prefix), id="btn-cmds")
                cmd_btn.tooltip = Text(f"Show REPL {prefix} commands.")
                yield cmd_btn
                yield PromptInput(
                    placeholder=f"{prefix} for REPL, {PALETTE_HOTKEY} for palette",
                    id="cmd",
                    suggester=self._suggester,
                    # A prompt keeps what is typed when focus comes back
                    # (a filled link, a closed dialog): selecting it all
                    # would let the next key erase it.
                    select_on_focus=False,
                )
                # Record button toggles /run.record.  Sits at the
                # right edge of the REPL input.  Green when idle,
                # red ("Stop") while recording.  Hidden when
                # record_enabled is false.  All recording state
                # lives in the recorder builtin -- this button just
                # dispatches /run.record and refreshes its visual.
                if cfg_value(self.cfg, "record_enabled"):
                    record_btn = Button(
                        # Angle brackets mark this as a state indicator
                        # rather than a discovery button.  Square
                        # brackets would render as Rich markup tags
                        # and eat the label.
                        "<Rec>",
                        id="btn-record",
                        variant="success",
                    )
                    record_btn.tooltip = (
                        "Record commands to a .run script. Click to start; click again to stop."
                    )
                    yield record_btn
                # FindBar: ephemeral, hidden until /find runs.
                # display=False on the container AND each child so
                # the widgets vanish from layout entirely until
                # _update_find_bar reveals them.
                find_bar = Horizontal(id="find-bar")
                find_bar.display = False
                with find_bar:
                    find_status = Label("", id="find-status", markup=False)
                    find_status.display = False
                    yield find_status
                    prev_btn = Button(
                        "▲",
                        id="btn-find-prev",
                        variant="default",
                    )
                    prev_btn.tooltip = Text(
                        f"Previous find match ({cmd_prefix(self.cfg)}find.prev)"
                    )
                    prev_btn.display = False
                    yield prev_btn
                    next_btn = Button(
                        "▼",
                        id="btn-find-next",
                        variant="default",
                    )
                    next_btn.tooltip = Text(f"Next find match ({cmd_prefix(self.cfg)}find.next)")
                    next_btn.display = False
                    yield next_btn
                    close_btn = Button(
                        "×",
                        id="btn-find-close",
                        variant="default",
                    )
                    close_btn.tooltip = Text(f"Close find ({cmd_prefix(self.cfg)}find.clear)")
                    close_btn.display = False
                    yield close_btn
                # /term.traffic's live counts: a label of their own, so status
                # messages and progress text never write over them.
                yield StatusBar(id="traffic-bar")
                yield StatusBar(id="status-bar")

                def _btn(
                    label: str,
                    id: str,
                    tip: str,
                    variant: ButtonVariant = "default",
                    display: bool = True,
                ) -> Button:
                    b = Button(Text(label), id=id, variant=variant)
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
                # Filled by _sync_custom_buttons when custom_buttons_side is
                # bottom; its margins are the gaps in from Break and out to Log.
                yield Horizontal(id="custom-bar")
                yield _btn("Log", "btn-log", "View current log file.")
                yield _btn("SS", "btn-ss-dir", "Open screenshot folder.")
                yield _btn("Cap", "btn-cap-dir", "Open captures folder.")

    def _show_config_info(self, path: str) -> None:
        """Delegate to ``info_views.show_config_info``."""
        from termapy.info_views import show_config_info

        show_config_info(self, path)

    def session_log_path(self) -> Path | None:
        """The TUI's session log: ``log_file`` when set, else ``<cfg>.log`` (``config.session_log_file``)."""
        return session_log_file(self.cfg, self.config_path)

    def _on_app_prestart(self) -> None:
        """Open the session log, then show the VS Code tip, so startup is in the log.

        The tip comes first on screen, ahead of anything the config load
        prints, and pre-start fires once per run, so a config switch never
        repeats it.
        """
        self._open_log()
        self._maybe_show_vscode_tip()

    def _apply_border_color(self) -> None:
        """Apply border_color from config to title bar and output border."""
        from termapy.defaults import resolve_color

        color = resolve_color(cfg_value(self.cfg, "border_color") or "blue")
        bar = self.query_one("#title-bar")
        bar.styles.background = color
        self.query_one("#output", RichLog).styles.border = ("solid", color)

    def on_mount(self) -> None:
        # Authoritative: on_mount runs on the event-loop thread (see D4).
        self._main_thread_id = threading.get_ident()
        self._apply_border_color()
        self._build_context()
        self._register_tui_hooks()
        # Pre-start opens the session log and shows the VS Code tip, first
        # on screen; on_app_start then resolves the plugin folders and
        # auto-loads the symbols (engine core listeners), so the load
        # report lands in the log.  Before _run_startup: an
        # auto_connect config fires on_connect from there, and the CLI/MCP
        # order (on_app_start, then connect) means both are already done
        # when on_connect hooks run.
        self._fire_app_start()
        self._rebuild_suggester_commands()
        self._run_startup()
        self._check_for_updates()
        self.set_interval(0.5, self._refresh_traffic)

    def _maybe_show_vscode_tip(self) -> None:
        """Inform Windows/Linux VS Code users about the Alt-key fallbacks.

        VS Code's integrated terminal captures Ctrl+P / Ctrl+S / Ctrl+T
        on Windows and Linux for Quick Open, Save, New Tab.  Those never
        reach termapy.  macOS VS Code uses Cmd+* for the same actions,
        so Ctrl+* passes through to the terminal normally -- no tip
        needed there.

        The Alt+* aliases are bound unconditionally (they cost nothing
        on non-VS-Code terminals), so on macOS we stay silent even
        though the bindings still work.  Shown once per run whatever
        ``quiet_start`` says, and every line goes to the session log.
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
            self.query_one("#output", RichLog)
        except SHUTDOWN_RACE:
            return
        rule = "-" * 72
        for line in (
            rule,
            "VS Code detected: F-keys and Ctrl+P/S/T are captured by VS Code.",
            "Top buttons: Ctrl+Shift+1=Help, 2=Cfg, 3=Run, 4=Proto, 5=Open Config.",
            "Other:       Alt+P=palette, Alt+S=SVG, Alt+T=text screenshot.",
            rule,
        ):
            self._pane_write(Text(line, style="bold green"))
            self._log_line("#", line)

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
        # git clone without an install.  update_check.check() honors
        # its own "never raises to the user" contract internally, so
        # we trust it not to leak other exceptions here.
        from importlib.metadata import PackageNotFoundError, version as _get_version

        from termapy.update_check import check

        try:
            current = _get_version("termapy")
        except PackageNotFoundError:
            return  # no installed metadata -> nothing to compare against
        latest = check(current_version=current)

        # Whether or not an upgrade is available, the check may have
        # refreshed the cached ``latest_seen``; rebuild the Help tooltip
        # so its status line reflects it this session (not just next
        # launch).  Network-free, so it can't fail here.
        try:
            self.call_from_thread(self._refresh_help_tooltip)
        except RuntimeError:
            pass  # app shutting down

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

    def _refresh_help_tooltip(self) -> None:
        """Main-thread helper: rebuild the Help tooltip's version status.

        Called after a background update check so the tooltip's
        up-to-date / update-available line reflects the just-cached
        result.  Mirrors compose's ``"?"`` fallback for an un-installed
        (git-clone) run.
        """
        from importlib.metadata import PackageNotFoundError, version as _get_version

        try:
            ver = _get_version("termapy")
        except PackageNotFoundError:
            ver = "?"
        try:
            self.query_one("#btn-help", Button).tooltip = build_help_tooltip(
                ver, _hotkey_label("btn-help"), prefix=cmd_prefix(self.cfg)
            )
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
        internal.delay_progress = self._delay_progress
        internal.directives = self.repl._directives
        # /find plugin calls this with a state snapshot to refresh the
        # FindBar widget (or None to hide it).  It runs on a dispatch
        # worker and mutates widgets, so it marshals like every sibling
        # slot -- this was the one internal-handle callback wired bare,
        # racing _write_batch's auto-dismiss on the main thread.
        internal.update_find_bar = lambda state: self._on_main(self._update_find_bar, state)

        ctx = self._build_plugin_context(internal)
        # TUI-specific PluginContext overrides
        ctx.io._write = self._status
        ctx.io._write_markup = lambda markup: self._write_output_markup(markup, log=True)
        ctx.io._write_segments = self._write_link_segments
        ctx.io.width = lambda: self._output_width
        ctx.io.log = self._log_line
        ctx.serial.wait_for_data = self._wait_for_data
        ctx.io.status_bar = self._set_status_bar
        ctx.dispatch = self._dispatch_single
        # A plugin's notification is text unless it asks otherwise.
        ctx.io.notify = lambda text, **kw: self._on_main(
            partial(self.notify, markup=False), text, **kw
        )
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
            # Its own config and script editors work over SSH too.
            file_editor=True,
            session_log=True,
            # The operator is the caller on their own machine: no sandbox.
            filesystem_unconfined=True,
            network_egress=True,
        )
        ctx.environment = "TUI"  # ENVIRONMENTS column; /help marks it (current)
        ctx.sync_capabilities()  # push caps into fs/ui handle snapshots

        self.repl.set_context(ctx)
        # The refresh redraws the title, border and hardware buttons, and
        # /cfg, /cfg.auto and the /term.* setters apply from a dispatch
        # worker (registry commands-off-main), so it runs on main.
        self.repl._after_cfg = lambda key, new_val: self._on_main(
            self._refresh_after_cfg, key, new_val
        )
        self.ctx = ctx
        # TUI: REPL echo on (input clears on Enter, so echo keeps a
        # scrollback record).  Device echo follows cfg echo.
        self._init_flags(echo_repl=True, output_level=self._initial_output_level)

    def _register_tui_hooks(self) -> None:
        """Register TUI-specific commands as plugin hooks.

        Implementation lives in ``termapy.app_hooks``.  Lazy-import
        keeps the module out of the CLI/MCP import graphs (those
        frontends never call this).
        """
        from termapy.app_hooks import register_tui_hooks

        register_tui_hooks(self)

    def _run_startup(self) -> None:
        """Sync buttons and show the startup screen (the log opened at pre-start)."""
        if self.config_path:
            self._show_config_info(self.config_path)
        self._sync_all_buttons()
        for w in self.repl._cfg_data.pop("_config_warnings", []):
            self._status(f"Config warning: {w}", "yellow")
        if self.show_picker_on_start:
            self.push_screen(
                self._config_picker(self.config_path),
                callback=self._on_config_picked,
            )
        elif self.open_editor_on_start:
            self._new_config()
        elif self._first_run:
            self.push_screen(
                QuickSetup(title="Welcome to Termapy - Quick Setup"),
                callback=self._on_quick_setup,
            )
        elif cfg_value(self.cfg, "auto_connect"):
            self._connect()
        else:
            self._status(f"{self._port_info_str()} - press Connect to start")
        self._update_title()
        self.query_one("#cmd", Input).focus()

    def on_unmount(self) -> None:
        self._shutting_down = True
        # History saved BEFORE the exit event fires: a plugin's on_app_stop
        # (the cfg info report tails it and the log) must see THIS session's
        # lines.  The session log needs no flush: every write flushes.
        self._save_history()
        self.repl.fire_lifecycle("on_app_stop")
        # Stop background work unconditionally: _disconnect() early-returns
        # when not connected, so relying on it left the auto-reconnect worker
        # spinning and a parked script alive after the TUI was gone (T4/T5).
        self._reconnect_stop.set()
        self.repl._script_stop.set()
        self._disconnect()
        # No settle needed: _disconnect joins the reader, so by here it has
        # genuinely exited rather than probably-exited.  Last, so the end
        # marker follows the Disconnected line and the stop hooks' output.
        self._end_session_log()

    def _connect(self, port: str | None = None, *, report: bool = True) -> bool:
        if self.is_connected:
            return False
        # The engine joins any prior reader before it opens a new handle.
        connected = super()._connect(port, report=report)
        if connected:
            # Whoever opened the port, a reconnect still retrying is done; left
            # running it would keep trying to open the port we now hold.
            self._reconnect_stop.set()
        return connected

    # The three connection hooks below run wherever _connect / _disconnect
    # run, and /port.connect and /port.disconnect run on a dispatch worker
    # (registry commands-off-main).  Each forwards itself to main, as the
    # output helpers do, before it touches a widget (registry
    # output-helpers-marshal).

    def _on_connected(self, message: str) -> None:
        """TUI post-connect: notify, update UI, run on_connect_cmd (on main)."""
        self._on_main(self._show_connected, message)

    def _show_connected(self, message: str) -> None:
        """Main thread: the body of ``_on_connected``."""
        self.notify(message, timeout=0.75, markup=False)
        self._set_conn_status("Connected")
        self._update_title()
        inp = self.query_one("#cmd", Input)
        prefix = cmd_prefix(self.cfg)
        inp.placeholder = f"{prefix} for REPL, {PALETTE_HOTKEY} for palette"
        inp.focus()
        self._sync_hw_buttons()
        connect_cmds = [
            command for _, command in on_connect_commands(self.cfg, "tui_on_connect_cmd")
        ]
        if connect_cmds:
            with self._on_connect_lock:
                self._on_connect_runs += 1
            self._run_on_connect(connect_cmds)

    def _on_connect_failed(self) -> None:
        """TUI post-connect failure: update status, auto-reconnect (on main)."""
        self._on_main(self._show_connect_failed)

    def _show_connect_failed(self) -> None:
        """Main thread: the body of ``_on_connect_failed``."""
        self._set_conn_status("Disconnected")
        if cfg_value(self.cfg, "auto_reconnect"):
            self._auto_reconnect()

    @property
    def _reconnecting(self) -> bool:
        """True while an auto-reconnect run is live (its token is not set)."""
        return not self._reconnect_stop.is_set()

    def _auto_reconnect(self) -> None:
        """Start the auto-reconnect worker unless one is already running.

        Main thread only -- reached from ``_show_connect_failed`` and from
        ``_on_rx_disconnect``, both on main -- so the check and the new token
        cannot interleave with another start.  Each run gets a FRESH token:
        cancelling sets the old one, which the old loop sees, while a new
        run can start at once without waiting for the old worker to exit.
        A single flag cleared in the worker's ``finally`` could not do
        that; it stayed set while a cancelled loop wound down and swallowed
        the restart (registry ``reconnect-survives-connect-attempts``).
        """
        if self._reconnecting or self._shutting_down:
            return
        self._reconnect_stop = threading.Event()
        self._run_reconnect(self._reconnect_stop)

    @work(thread=True)
    def _run_reconnect(self, stop: threading.Event) -> None:
        """Background thread: retry opening the port until it opens or *stop* is set."""
        try:
            port = self._engine.reconnect_loop(
                stop,
                on_status=lambda msg: self.call_from_thread(self._show_reconnect_status, stop, msg),
            )
            if port is not None:
                self.call_from_thread(self._finish_reconnect, stop, port)
        except RuntimeError:
            pass  # call_from_thread fails during app shutdown, which closes the port
        finally:
            stop.set()  # this run is over; only ever its OWN token

    def _finish_reconnect(self, stop: threading.Event, port: object) -> None:
        """Main thread: finish the connection a reconnect run opened, or close it.

        The run opened the port itself and nothing may open it again
        (registry ``reconnect-opens-once``).  A cancel that landed while the
        open was in flight (the title-bar button, a config switch) leaves the
        port unwanted; it is closed only if it is still the one this run
        opened, since a config switch may have connected another since.
        """
        if not stop.is_set():
            self._finish_connect()
        elif self._engine.port_obj is port:
            self._engine.disconnect()

    def _show_reconnect_status(self, stop: threading.Event, text: str) -> None:
        """Main thread: show a reconnect spinner frame, unless that run has ended.

        A frame queued just before the run was cancelled, or before a connect
        landed, would otherwise paint "Connecting" over "Connected".
        """
        if stop.is_set() or self.is_connected:
            return
        self._set_conn_status(text, "retry")

    def _open_proto_debug(self, path: Path, script: ProtoScript) -> None:
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
            visualizers += load_visualizers_from_dir(viz_dir, Path(self.config_path).stem)

        # Deduplicate by name (later wins), sort by sort_order
        by_name = {visualizer.name: visualizer for visualizer in visualizers}
        final = sorted(by_name.values(), key=lambda v: v.sort_order)

        ctx = self.repl.ctx
        self.push_screen(ProtoDebugScreen(path, ctx, script, final))

    # How long a connect settles before its on-connect commands run.
    _ON_CONNECT_DELAY_S = 0.2

    @work(thread=True)
    def _run_on_connect(self, cmds: list[str]) -> None:
        """Run the on-connect commands on a worker of their own, after the connect settles.

        Counted from before the worker starts until it ends, whatever
        happens, so ``_wait_for_on_connect`` holds a paste's next line for
        the whole of it: the settling pause too, when no script is running yet.
        """
        try:
            time.sleep(self._ON_CONNECT_DELAY_S)
            self._send_lines(cmds)
        finally:
            with self._on_connect_lock:
                self._on_connect_runs -= 1

    def _wait_for_on_connect(self) -> None:
        """Hold this worker while on-connect commands or a script it did not start are running.

        A paste or a button's sequence takes the dispatch guard one line at a
        time, so without this its lines run between another worker's: a
        pasted ``/port.connect`` started the on-connect script 200 ms later,
        the next pasted line reached the device ahead of what the script set
        up, and it lost its echo to the script's echo-off.  Its own ``/run``
        has returned by the time this is asked, so a running script is someone
        else's.  Off the main thread only.  The shared stop event ends the
        wait: Escape, and the app stopping.
        """
        stop = self.repl._script_stop
        while (self._on_connect_runs > 0 or self.repl.in_script) and not stop.wait(0.05):
            pass

    def _send_lines(self, lines: list[str], echo_prefix: str = "") -> None:
        """Send multiple commands with cmd_delay_ms between each.

        Called from a background thread (@work). _status and
        _dispatch_single are both thread-safe.
        """
        delay_s = cfg_value(self.cfg, "cmd_delay_ms") / 1000.0
        for command in lines:
            command = command.strip()
            if not command:
                continue
            if echo_prefix:
                self._status(f"{echo_prefix}{command}")
            self._dispatch_single(command)
            if delay_s > 0:
                time.sleep(delay_s)
            self._wait_for_idle(400)

    def _on_main(self, fn: Callable[_P, _R], *args: _P.args, **kwargs: _P.kwargs) -> _R | None:
        """Run *fn* on the main thread.  No-op if already there."""
        if self._main_thread_id == threading.get_ident():
            return fn(*args, **kwargs)
        try:
            return self.call_from_thread(fn, *args, **kwargs)
        except RuntimeError:
            return None  # app shutting down

    def _pane_write(self, content: str | Text) -> None:
        """Write to the output pane as /term.color has it: with color off, every color is dropped.

        Bold, italic, underline and the links' click actions stay, so a
        help page reads the same and still clicks; a markup string is
        parsed as the pane parses it.  With color on, ``content`` goes to
        the widget as it is.  Main thread only, like the widget.
        """
        log = self.query_one("#output", RichLog)
        if self._color_on():
            log.write(content)
            return
        text = Text.from_markup(content) if isinstance(content, str) else content
        log.write(without_color(text))

    def _color_on(self) -> bool:
        """Whether /term.color is on: the session flag, on before the context exists."""
        ctx = getattr(getattr(self, "repl", None), "ctx", None)
        return True if ctx is None else bool(ctx.ns("flags").get("color", True))

    def _status(self, text: str, color: str = "dim") -> None:
        """Write a termapy status message with consistent formatting.

        Thread-safe: if called from a background thread (e.g. interactive
        command dispatch), posts the widget update to the main thread.
        Logged one ``#`` entry per line, so a multi-line message keeps the
        log's prefix on every line.
        """
        if self._main_thread_id != threading.get_ident():
            try:
                self.call_from_thread(self._status, text, color)
            except RuntimeError:
                pass
            return
        try:
            self._pane_write(Text(text, style=f"bold italic {color}"))
        except SHUTDOWN_RACE:
            pass  # widgets gone during shutdown
        self._log_lines("#", text.splitlines() or [""])

    def _set_status_bar(self, text: str, timeout: float = 5.0) -> None:
        """Show transient text in the status bar (thread-safe).

        Display + auto-clear behavior lives in the StatusBar widget; this
        only marshals to the main thread when a plugin calls it from a
        background thread (via ``ctx.io.status_bar``).
        """
        if self._main_thread_id != threading.get_ident():
            try:
                self.call_from_thread(self._set_status_bar, text, timeout)
            except RuntimeError:
                pass
            return
        try:
            self.query_one("#status-bar", StatusBar).show(text, timeout)
        except SHUTDOWN_RACE:
            pass  # widget gone during shutdown

    def _wait_for_data(self, timeout_ms: int = 250) -> bool:
        """Block until RX arrives or *timeout_ms* elapses (``ctx.serial``).

        Same bind-to-local reason as ``TerminalHost._serial_write``: the
        port can be disowned between the check and the call.
        """
        port = self._engine.serial_port
        return port.wait_for_data(timeout_ms) if port is not None else False

    def _ask(self, screen: ModalScreen[bool]) -> bool | None:
        """Push a yes/no modal from a worker and block until it is answered.

        The one wait behind ``/confirm`` and ``/cfg <key> <value>``: both
        answer only after the dialog does (review 2026-10-15, D7, where
        ``/cfg`` returned the new value with the dialog still open).

        THREADING: must be called from a background thread -- every command
        is (registry ``commands-off-main``).  ``call_from_thread`` posts the
        dialog to main and the calling thread waits on an event.

        Returns:
            The answer; False when the app shuts down, or a running script
            is stopped, while the dialog is up; None when called on the main
            thread, where ``call_from_thread`` refuses.
        """
        result: list[bool] = [False]
        event = Event()

        def _show() -> None:
            def _on_result(confirmed: bool | None) -> None:
                result[0] = bool(confirmed)
                event.set()

            self.push_screen(screen, callback=_on_result)

        try:
            self.call_from_thread(_show)
        except RuntimeError:
            return None
        # Bounded, stop-aware wait.  Both dialogs bind escape/ctrl+q to a
        # dismissing action, so the normal path always sets the event -- but
        # a /stop or an app shutdown while the modal is up would otherwise
        # park this worker forever, and teardown joins the threads a worker
        # like this one runs on.  A stop already set outside a script is
        # stale and ignored: the event is cleared only when a script starts,
        # so it can still hold an Escape pressed long ago, which would
        # cancel an interactive /cfg before the user saw the dialog.  The
        # dialog is deliberately left on screen: dismissing a modal from
        # here would race the user's own click, and answering it after the
        # abort simply sets an event nobody is waiting on any more.
        stop = self.repl._script_stop
        stale_stop = not self.repl.in_script and stop.is_set()
        while not event.wait(timeout=0.25):
            if self._shutting_down or (stop.is_set() and not stale_stop):
                return False
        return result[0]

    def _confirm(self, message: str) -> bool:
        """Show a Yes/Cancel dialog and block until the user responds (``ctx.ui.confirm``).

        Args:
            message: Text to display in the confirmation dialog.

        Returns:
            True if the user clicked Yes; False otherwise.  Called on the
            main thread it fails closed, returning False, so a /confirm gate
            never proceeds unconfirmed.
        """
        answer = self._ask(ConfirmDialog(message))
        if answer is None:
            self._status(f"{cmd_prefix(self.cfg)}confirm can only be used in scripts.", "yellow")
            return False
        return answer

    def _write_output_markup(self, text: str, log: bool = False) -> None:
        """Write Rich markup to the output pane; with ``log``, to the session log too.

        The log gets the rendered text under ``#``, one entry per line, as
        ``_status`` lines do, so a command's markup output (a multi-line
        tree included) is in the transcript.  Echo leaves ``log`` off: the
        command it echoes is already logged as its ``>`` line.  Logged on
        the main thread, after the marshal, so the log keeps the order the
        pane shows.
        """
        if self._main_thread_id != threading.get_ident():
            try:
                self.call_from_thread(self._write_output_markup, text, log)
            except RuntimeError:
                pass
            return
        try:
            self._pane_write(text)
        except SHUTDOWN_RACE:
            pass  # widgets gone during shutdown
        if log:
            self._log_lines("#", markup_to_plain(text).splitlines() or [""])

    def _report_exception(self, e: Exception) -> None:
        """Show an exception an event handler stopped: one red line, the traceback in the diagnostic log.

        Call it in the ``except`` block, so the log records the traceback
        being handled.

        Args:
            e: The caught exception to report.
        """
        diag_log.exception("TUI event handler raised")
        self._status(f"Exception: {type(e).__name__}: {e}{diag_log.traceback_note()}", "red")

    def _disconnect(self) -> None:
        if self._capture.active:
            self._cap_stop()
        super()._disconnect()

    def _on_disconnected(self) -> None:
        """TUI post-disconnect: notify, update status, title, placeholder, buttons (on main)."""
        self._on_main(self._show_disconnected)

    def _show_disconnected(self) -> None:
        """Main thread: the body of ``_on_disconnected``."""
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
        except BoundaryException as e:
            self._report_exception(e)

    def _sync_hw_visibility(self) -> None:
        """Show or hide DTR/RTS/Break buttons based on flow_control config."""
        show = self.cfg["serial"]["flow_control"] == "manual"
        self._btn_dtr.display = show
        self._btn_rts.display = show
        self._btn_break.display = show

    def _switch_to_cfg_path(self, config_path: str) -> CmdResult:
        """``/cfg.load``'s apply step in the TUI: the screen's own switch, done before the command returns.

        ``TerminalHost._load_config`` resolves the name and words the
        failure for every frontend; this replaces only how a found config
        is applied.  ``_switch_config`` rebuilds the screen, so it runs on
        main, and ``_on_main`` waits for it from the dispatch worker.
        """
        try:
            cfg = load_config(config_path)
        except CONFIG_LOAD_ERRORS as e:
            return CmdResult.fail(msg=f"Cannot load config: {e}")
        self._on_main(self._switch_config, cfg, config_path)
        self._on_main(self._show_config_info, config_path)
        return CmdResult.ok(value=config_path)

    def _switch_config(
        self,
        cfg: dict,
        path: str,
        *,
        keep_connection: bool = False,
        keep_session: bool = False,
    ) -> None:
        """Apply a config: disconnect, update state, refresh UI, connect if it says to.

        Plain, this is a load, and a load ends with what a fresh start on the
        config has (registry ``config-load-deterministic``).

        Args:
            cfg: The config to apply.
            path: Its file.
            keep_connection: A connected port reconnects whatever ``auto_connect``
                says.  The editor's Save asks for this: a saved edit is not a
                request to drop the link.
            keep_session: Not a load at all, the same config with a new port:
                nothing is reset and ``on_config_load`` does not fire.
        """
        # Read before the swap: the log the session is leaving.
        previous_log = self.session_log_path()
        self._clear_output()
        self._save_history()
        migrated_from = cfg.pop("_migrated_from", None)
        # A reconnect still retrying belongs to the config being left.
        self._reconnect_stop.set()
        was_connected = self.is_connected
        if was_connected:
            self._disconnect()
        # Per-step breakdown: one line per step of THIS load that ran an
        # actual migrator (no-op versions are skipped).  Popped whether or
        # not a migration ran.  The editor opens through load_config, so its
        # text is already current; only a file changed on disk while the
        # editor was open can hand an old saved list over, and that list
        # describes past migrations, so dropping it loses nothing.
        steps = cfg.pop("_migration_steps", [])
        if migrated_from is not None:
            self._status(
                f"Config migrated: v{migrated_from} -> v{CURRENT_CONFIG_VERSION}",
                "yellow",
            )
            for step in steps:
                self._status(f"  {step}", "yellow")
        for w in cfg.pop("_config_warnings", []):
            self._status(f"Config warning: {w}", "yellow")
        self.repl.replace_cfg(cfg, path)
        self.config_path = path
        if not keep_session:
            self._reset_session()
        self._leave_breadcrumb()
        self.history = self._load_history()
        self._history_nav.reset()
        self.repl.ctx.config_path = path
        # Every path the handle declares, so none is left on the old config.
        self.repl.ctx.fs.ss_dir = self.repl.ss_dir
        self.repl.ctx.fs.scripts_dir = self.repl.scripts_dir
        self.repl.ctx.fs.proto_dir = self.repl.proto_dir
        self.repl.ctx.fs.cap_dir = self.repl.cap_dir
        self.repl.ctx.fs.prof_dir = self.repl.prof_dir
        self._update_title()
        self._apply_border_color()
        self._sync_hw_visibility()
        self._sync_cmd_prefix()
        self._sync_all_buttons()
        self._follow_session_log(previous_log)
        # The engine resolves the new config's plugins and symbols here;
        # the cfg may also have swapped the active profile out from under
        # us, so rebuild the suggester after the pass, not before it.
        if not keep_session:
            self.repl.fire_lifecycle("on_config_load")
            # The load may have ended a recording.
            self._refresh_record_button()
        self._rebuild_suggester_commands()
        if (keep_connection and was_connected) or cfg_value(cfg, "auto_connect"):
            self._connect()

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

    def _check_port_and_switch(self, cfg: dict, path: str) -> None:
        """If connecting would not find the configured port, prompt with PortPicker.

        Present or not is ``port_control.port_present``, the connect's own
        resolution, so a serial number or a ``|`` chain loads like a COM name.
        """
        port = cfg["serial"]["port"]
        # comports() failures are environmental: OSError on udev / IOKit
        # / WMI hiccups, ImportError on platforms without list_ports.
        # Treat either as "no ports visible" and fall through.
        try:
            from serial.tools.list_ports import comports

            available = {p.device for p in comports()}
            present = port_control.port_present(port)
        except (OSError, ImportError):
            available, present = set(), False

        if present:
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
                ConfigEditor(
                    cfg, result[1], used_by=cfg_users(self.repl._plugins), ctx=self.repl.ctx
                ),
                callback=self._on_config_result,
            )
        elif action == "rename":
            self._rename_config(result[1])
        elif action == "delete":
            # The button is the command: /cfg.delete asks, deletes the
            # folder, retires the launcher, and refuses the active config.
            self._dispatch_on_thread(f"{cmd_prefix(self.cfg)}cfg.delete {result[1]}")

    def _rename_config(self, path: str) -> None:
        """Prompt for a new name, then rename the config's folder and file together.

        A config is ``termapy_cfg/<name>/<name>.cfg``; ``config.rename_config``
        moves both.  If the renamed config is the ACTIVE one, every derived
        path (run/, proto/, cap/, the history sidecar, the log) just moved,
        so it is reloaded through the same path Load uses -- a forced
        reload keeps the session consistent rather than pointing at a
        folder that no longer exists.
        """
        old = Path(path)

        def _on_name(new_name: str | None) -> None:
            if not new_name or new_name == old.stem:
                return
            was_active = path == self.config_path
            try:
                new_path = rename_config(path, new_name)
            except (ValueError, OSError) as e:
                self._status(f"Rename failed: {e}", "red")
                return
            self._status(f"Renamed config: {old.stem} -> {new_name}", "green")
            # A launcher embeds the OLD cfg path, so it is a dead link now,
            # the same as after a delete; retire it and say how to get a
            # fresh one (creating it needs the renamed cfg to be active).
            from termapy.builtins.commands._cfg_icon import retire_launcher

            retired = retire_launcher(Path(path))
            if retired is not None:
                message, color = retired
                self._status(
                    f"{message} -- run {cmd_prefix(self.cfg)}cfg.icon to recreate it for '{new_name}'",
                    color,
                )
            if not was_active:
                return
            try:
                cfg = load_config(new_path)
            except CONFIG_LOAD_ERRORS as e:
                self._status(f"Failed to reload renamed config: {e}", "red")
                return
            self._check_port_and_switch(cfg, new_path)

        self.push_screen(
            FilenameDialog(title=f"Rename config '{old.stem}' to:", value=old.stem),
            callback=_on_name,
        )

    def _on_config_result(self, result: tuple | None) -> None:
        """Delegate to ``pickers.on_config_result``."""
        from termapy.pickers import on_config_result

        on_config_result(self, result)

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

        actual = self.engine.open_device if self.engine.is_connected else ""
        return connection_string(self.cfg, "short", actual_port=actual)

    def _update_title(self) -> None:
        """Refresh title-bar widgets.  Implementation in ``title_bar``."""
        from termapy.title_bar import update_title

        update_title(self)

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

    def _update_conn_tooltip(
        self,
        widget: Button | None = None,
        display: tuple[str, port_control.ChipFacts | None] | None = None,
    ) -> None:
        """Refresh the port-button tooltip.  Implementation in ``title_bar``."""
        from termapy.title_bar import update_conn_tooltip

        update_conn_tooltip(self, widget, display)

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
            self._reconnect_stop.set()
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
                ConfigEditor(
                    cfg, self.config_path, used_by=cfg_users(self.repl._plugins), ctx=self.repl.ctx
                ),
                callback=self._on_config_result,
            )
        else:
            self.push_screen(
                self._config_picker(self.config_path),
                callback=self._on_config_picked,
            )

    def _on_btn_dtr(self) -> None:
        if self.is_connected:
            self._serial_op(
                "DTR",
                lambda: setattr(self._btn_dtr, "label", f"DTR:{int(self._engine.toggle_dtr())}"),
            )

    def _on_btn_rts(self) -> None:
        if self.is_connected:
            self._serial_op(
                "RTS",
                lambda: setattr(self._btn_rts, "label", f"RTS:{int(self._engine.toggle_rts())}"),
            )

    def _on_btn_break(self) -> None:
        if self.is_connected:

            def _send() -> None:
                self._engine.send_break()
                self.notify("Break sent", timeout=1.5)

            self._serial_op("Break", _send)

    def _require_config(self) -> bool:
        """True when a config is loaded; otherwise say so and return False.

        The per-config folders (run/, proto/) derive from ``config_path``
        and fall back to the current directory when there is none -- a
        picker opened then would list whatever the shell happens to be in.
        """
        if self.config_path:
            return True
        self._status(STATE_MESSAGES["config_loaded"], "yellow")
        return False

    def _btn_scripts(self) -> None:
        if not self._require_config():
            return
        from termapy.pickers import file_action

        self.push_screen(
            ScriptPicker(
                self.repl.scripts_dir,
                read_only=cfg_value(self.cfg, "config_read_only"),
                gui_apps=self.repl.ctx.capabilities.gui_apps,
                web=self.is_web,
                file_action=partial(file_action, self, "run"),
            ),
            callback=self._on_script_picked,
        )

    def _btn_proto(self) -> None:
        if not self._require_config():
            return
        from termapy.pickers import file_action

        self.push_screen(
            ProtoPicker(
                self.repl.proto_dir,
                read_only=cfg_value(self.cfg, "config_read_only"),
                gui_apps=self.repl.ctx.capabilities.gui_apps,
                web=self.is_web,
                file_action=partial(file_action, self, "proto"),
            ),
            callback=self._on_proto_picked,
        )

    def _config_picker(
        self,
        current_path: str,
        *,
        read_only: bool | None = None,
    ) -> ConfigPicker:
        """The picker over ``current_path`` with this frontend's facts filled in.

        One place for the five push sites.  ``read_only`` comes from the cfg
        unless the caller says otherwise (after deleting the active config
        the old cfg's flag has nothing left to protect); the GUI flag is
        the SSH-aware capability, and ``web`` is Textual's own signal, so
        the dialog stays pure in its inputs and testable.
        """
        if read_only is None:
            read_only = cfg_value(self.cfg, "config_read_only")
        return ConfigPicker(
            current_path,
            read_only=read_only,
            gui_apps=self.repl.ctx.capabilities.gui_apps,
            web=self.is_web,
            open_device=self.engine.open_device if self.engine.is_connected else "",
        )

    def _btn_cfg(self) -> None:
        self.push_screen(self._config_picker(self.config_path), callback=self._on_config_picked)

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
        self._dispatch_on_thread(f"{cmd_prefix(self.cfg)}log.show")

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
            # Recording: a bare /run.record stops it.
            self._dispatch_on_thread(self._repl_line("run.record"))
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
            return  # canceled
        self._dispatch_on_thread(self._repl_line(f"run.record {name}"))
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
        "#find-bar",
        "#find-status",
        "#btn-find-prev",
        "#btn-find-next",
        "#btn-find-close",
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
            if self._find_restore_scroll_on_close and restore_line is not None:
                # Defer so #output has its layout back before we
                # compute its viewport-center scroll target.
                self.call_after_refresh(
                    self._scroll_to_find_match,
                    restore_line,
                    output,
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
            matched_set = {match[0] for match in matches}
            output_find.clear()
            for i, line in enumerate(
                scrollback_text.splitlines(),
                start=1,
            ):
                # Text, never a str: the widget parses a str as markup,
                # and a scrollback line is text (registry markup-escape).
                if i in matched_set:
                    output_find.write(
                        Text(line, style="reverse"),
                        width=wrap_width,
                    )
                else:
                    output_find.write(Text(line), width=wrap_width)
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
            self._scroll_to_find_match,
            current_line,
            output_find,
        )

    def _scroll_to_find_match(
        self,
        line_no: int,
        target: RichLog | None = None,
    ) -> None:
        """Scroll target RichLog so ``line_no`` lands at viewport middle.

        With the v2 design (matches highlighted with ``[reverse]`` in
        the frozen view), middle is preferred over top: the user sees
        context above and below the current match.  All matches share
        the same styling, so centering -- not a distinct style -- is
        what identifies the current one.

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
        self._dispatch_quiet(self._repl_line("find.prev"))

    def _btn_find_next(self) -> None:
        self._dispatch_quiet(self._repl_line("find.next"))

    def _btn_find_close(self) -> None:
        self._dispatch_quiet(self._repl_line("find.clear"))

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
        buttons = cfg_value(self.cfg, "custom_buttons")
        if idx >= len(buttons):
            return
        # "{prefix}" in a button command is the config's REPL prefix, so the
        # shipped Info button follows cmd_prefix (help: custom-buttons).
        command = buttons[idx].get("command", "").replace("{prefix}", cmd_prefix(self.cfg))
        parts = split_cmd_lines(command)
        if len(parts) > 1:
            self._dispatch_sequence_on_thread(parts)
        elif parts:
            self._dispatch_on_thread(parts[0])

    def _show_port_picker(self) -> None:
        """Delegate to ``info_views.show_port_picker``."""
        from termapy.info_views import show_port_picker

        show_port_picker(self)

    def _update_port(self, port: str) -> None:
        """Refresh port-name display.  Implementation in ``title_bar``."""
        from termapy.title_bar import update_port

        update_port(self, port)
        # Connection failure already reported by _try_open_port

    def _on_port_picked(self, port: str | None) -> None:
        """Delegate to ``pickers.on_port_picked``."""
        from termapy.pickers import on_port_picked

        on_port_picked(self, port)

    # -- Palette action wrappers --

    def _new_config(self) -> None:
        self.push_screen(QuickSetup(), callback=self._on_quick_setup)

    def _on_quick_setup(self, result: tuple | None) -> None:
        """Delegate to ``pickers.on_quick_setup``."""
        from termapy.pickers import on_quick_setup

        on_quick_setup(self, result)

    def _edit_file(self, path: Path) -> None:
        """``ctx.internal.edit_file``: the TUI's own editor for the config, a .run or a .pro file.

        Anything else goes to the system editor through ``ctx.fs.open_file``,
        which needs a desktop.  /edit decides which file; this is the TUI's
        half, so the TUI registers no /edit commands of its own (review
        2026-10-15, D5).  Runs on a dispatch worker: the screens are pushed
        on main.
        """
        ext = path.suffix.lower()
        if self.config_path and path == Path(self.config_path):
            config_path = self.config_path

            def open_editor() -> None:
                self.push_screen(
                    ConfigEditor(
                        dict(self.cfg),
                        config_path,
                        used_by=cfg_users(self.repl._plugins),
                        ctx=self.repl.ctx,
                    ),
                    callback=self._on_config_result,
                )
        elif ext == FOLDER_EXTS["run"]:

            def open_editor() -> None:
                self.push_screen(
                    ScriptEditor(self.repl.scripts_dir, str(path), prefix=cmd_prefix(self.cfg)),
                    callback=self._on_script_saved,
                )
        elif ext == FOLDER_EXTS["proto"]:

            def open_editor() -> None:
                self.push_screen(
                    ProtoEditor(self.repl.proto_dir, str(path)),
                    callback=self._on_proto_saved,
                )
        else:
            self.repl.ctx.fs.open_file(path)
            return
        # One no-argument call on main: ``_on_main`` forwards to
        # ``call_from_thread``, whose own first parameter is ``callback``,
        # so push_screen's ``callback=`` cannot ride through it.  Every
        # typed /edit failed that way from its dispatch worker.
        self._on_main(open_editor)

    def _palette_edit_config(self) -> None:
        self.push_screen(
            ConfigEditor(
                dict(self.cfg),
                self.config_path,
                used_by=cfg_users(self.repl._plugins),
                ctx=self.repl.ctx,
            ),
            callback=self._on_config_result,
        )

    def _palette_load_config(self) -> None:
        self.push_screen(self._config_picker(self.config_path), callback=self._on_config_picked)

    def _palette_new_config(self) -> None:
        self._new_config()

    def _palette_view_log(self) -> None:
        self._dispatch_on_thread(f"{cmd_prefix(self.cfg)}log.show")

    def _palette_delete_log(self) -> None:
        """Delete the current session log file after confirmation."""
        log_path = self.session_log_path()
        if log_path is None or not log_path.exists():
            self._status("No log file to delete.", "yellow")
            return

        def on_confirmed(confirmed: bool) -> None:
            # Single owner of the delete (stop the log + unlink + status):
            # /log.delete.  The palette only adds the confirm gate.
            if confirmed:
                self._dispatch_on_thread(f"{cmd_prefix(self.cfg)}log.delete")

        self.push_screen(  # ty: ignore[no-matching-overload] -- no (screen, callback=) overload
            ConfirmDialog(f"Delete {log_path.name}?"),
            callback=on_confirmed,
        )

    def _palette_clear(self) -> None:
        try:
            self.query_one("#output", RichLog).clear()
        except SHUTDOWN_RACE:
            pass

    def _palette_list_ports(self) -> None:
        self._dispatch_quiet(self._repl_line("port.list"))

    def _palette_list_vars(self) -> None:
        self._dispatch_quiet(self._repl_line("var"))

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
            self._dispatch_quiet(self._repl_line(f"var.set {name} {value}"))

        self.push_screen(SetVarDialog(), callback=_on_submit)

    # Buttons flip on click, so they dispatch the explicit `toggle` verb --
    # bare invocation now QUERIES (never mutates), per the settings-grammar
    # rule in CLAUDE.md.
    def _palette_toggle_line_numbers(self) -> None:
        self._dispatch_quiet(self._repl_line("term.line_no toggle"))

    def _palette_toggle_echo(self) -> None:
        self._dispatch_quiet(self._repl_line("term.echo toggle"))

    def _palette_toggle_timestamps(self) -> None:
        self._dispatch_quiet(self._repl_line("term.timestamps toggle"))

    def _palette_toggle_hex(self) -> None:
        self._dispatch_quiet(self._repl_line("term.hex toggle"))

    def _palette_toggle_line_endings(self) -> None:
        self._dispatch_quiet(self._repl_line("term.line_endings toggle"))

    def _palette_term_info(self) -> None:
        self._dispatch_quiet(self._repl_line("term.info"))

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

    def _palette_help(self) -> None:
        self._hook_help_open(None, "")

    def _palette_find(self) -> None:
        """Prompt for a pattern, then dispatch ``/find <pattern>``."""
        self._prompt_then_dispatch("Find pattern:", self._repl_line("find "))

    def _palette_grep(self) -> None:
        """Prompt for a pattern, then dispatch ``/grep <pattern>``."""
        self._prompt_then_dispatch("Grep pattern:", self._repl_line("grep "))

    def _palette_search_help(self) -> None:
        """Prompt for a term, then dispatch ``/search <term>``."""
        self._prompt_then_dispatch("Search help for:", self._repl_line("search "))

    def _prompt_then_dispatch(
        self,
        title: str,
        command_prefix: str,
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
            file for file in directory.iterdir() if file.is_file() and not file.name.startswith(".")
        ]
        if not files:
            return None
        return max(files, key=lambda f: f.stat().st_mtime)

    def _palette_exit(self) -> None:
        self._disconnect()
        self.exit()

    def _rx_enqueue(self, event: tuple) -> None:
        """Queue an ordered RX event for the main thread.  Reader thread only.

        Never blocks: appends under a short lock and posts a wake-up only
        when one is not already pending.  Ordering across event kinds is
        preserved because they share this one list -- a clear-screen must
        not overtake the lines it was meant to follow.

        Nothing is dropped here.  Every line must reach the session log, the
        expect watcher and text capture (registry ``rx-every-line-delivered``),
        and those run in ``_write_batch`` on main; an earlier cap here threw
        the oldest events away before they got there.  A backlog now costs
        memory only while main is blocked, and ``_write_batch`` bounds the
        catch-up by rendering no more than the scrollback holds.

        Args:
            event: ``("lines", [...])``, ``("hex", rows, lines)``,
                ``("clear",)``, ``("capdone",)``, ``("echo", line)``,
                ``("error", detail)`` or ``("disconnect",)``.
        """
        post = False
        with self._rx_lock:
            self._rx_events.append(event)
            if not self._rx_posted:
                self._rx_posted = True
                post = True
        if not post:
            return
        try:
            # Returns False (rather than raising) once the pump is closing.
            delivered = self.post_message(self.SerialRx())
        except RuntimeError:
            # Pump gone during teardown -- same guard as the script lifecycle
            # posts use.
            delivered = False
        if not delivered:
            # Nothing will drain us, so clear the latch: leaving it set would
            # suppress every future wake-up if the app outlives this.
            with self._rx_lock:
                self._rx_posted = False

    @on(SerialRx)
    def _on_serial_rx(self) -> None:
        """Drain queued RX events on the main thread, in arrival order.

        Consecutive ``lines`` events are merged into one ``_write_batch``
        call, so falling behind produces FEWER, larger renders rather than a
        growing backlog of small ones.  Consecutive ``hex`` events merge the
        same way; a batch never mixes the two views.
        """
        with self._rx_lock:
            events, self._rx_events = self._rx_events, []
            self._rx_posted = False
        if self._shutting_down:
            return
        pending: list[str] = []
        # The hex rows to render in place of ``pending``; None in the text view.
        pending_rows: list[str] | None = None
        for event in events:
            if event[0] in ("lines", "hex"):
                lines, rows = (event[1], None) if event[0] == "lines" else (event[2], event[1])
                # A /term.hex toggle between chunks starts a new batch.
                if (rows is None) != (pending_rows is None):
                    if pending or pending_rows:
                        self._write_batch(pending, pending_rows)
                    pending, pending_rows = [], (None if rows is None else [])
                pending.extend(lines)
                if rows is not None and pending_rows is not None:
                    pending_rows.extend(rows)
                continue
            # A non-line event must observe everything queued before it.
            if pending or pending_rows:
                self._write_batch(pending, pending_rows)
                pending, pending_rows = [], None
            if event[0] == "clear":
                self._clear_output()
            elif event[0] == "capdone":
                self._cap_stop()
            elif event[0] == "echo":
                self._status(event[1], "dim")
            elif event[0] == "error":
                self._on_rx_error(event[1])
            elif event[0] == "disconnect":
                self._on_rx_disconnect()
        if pending or pending_rows:
            self._write_batch(pending, pending_rows)

    def _run_reader(self) -> None:
        """Start the engine-owned reader thread with the TUI's callbacks.

        Every callback goes through ``_rx_enqueue`` -- none may block on the
        main thread.  That is what lets ``disconnect`` JOIN the reader instead
        of waiting on an Event: a callback blocked in ``call_from_thread``
        would deadlock against a teardown running on the very thread it waits
        for.  Errors and disconnects ride the same ordered queue as lines so
        "Serial disconnected" still lands after the output that preceded it.
        """
        self._engine.start_reader(
            on_lines=lambda lines: self._rx_enqueue(("lines", lines)),
            on_clear=lambda: self._rx_enqueue(("clear",)),
            on_capture_done=lambda: self._rx_enqueue(("capdone",)),
            on_error=lambda detail: self._rx_enqueue(("error", detail)),
            on_disconnect=lambda: self._rx_enqueue(("disconnect",)),
            on_hex=lambda rows, lines: self._rx_enqueue(("hex", rows, lines)),
            hex_view=self._hex_view,
        )

    def _on_rx_error(self, detail: str) -> None:
        """Main thread: report a serial read error."""
        self._status(f"Serial read error: {detail}", "red")

    def _on_rx_disconnect(self) -> None:
        """Main thread: the reader lost the port -- tear down, then maybe reconnect.

        ``_port_lost`` stops an active capture (and its claim) and runs
        ``_on_disconnected`` for the toast, status, title, prompt and DTR/RTS
        labels; this used to set only the status, leaving the rest stale and
        a binary capture suppressing the display after an auto-reconnect.
        """
        if self._shutting_down:
            return
        self._port_lost()
        if cfg_value(self.cfg, "auto_reconnect"):
            self._auto_reconnect()

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

    def _cap_start(self, **kwargs: Any) -> bool | None:
        """Start a capture on the main thread; ``capture_view._cap_start`` is the implementation.

        The host wires this onto ``internal.start_capture``.  ``/cap`` runs on
        a worker, and the capture mounts Textual timers and an overlay.
        """
        from termapy.capture_view import _cap_start

        return self._on_main(_cap_start, self, **kwargs)

    def _cap_stop(self) -> None:
        """Stop the capture on the main thread, as ``_cap_start`` starts it.

        Wired onto ``internal.stop_capture``; the cap-stop button looks it up by name.
        """
        from termapy.capture_view import _cap_stop

        self._on_main(_cap_stop, self)

    def _stop_capture(self) -> None:
        """The host's capture stop, routed through ``_cap_stop``.

        Shared host code (``_feed_rx_consumers`` on a failed write) calls
        ``_stop_capture``; in the TUI a stop must also cancel the timer and
        tear down the progress overlay, which only ``_cap_stop`` does.
        """
        self._cap_stop()

    def _on_capture_complete(self, result: CaptureResult) -> None:
        """Report capture completion -- the single owner of the status line.

        Fired by ``CaptureEngine.on_complete`` inside ``stop()`` for every
        completion path (manual /cap.stop, timed, target-reached), so the
        message prints exactly once.  ``_cap_stop`` handles the log line and
        UI teardown.
        """
        text, color = format_capture_result(result)
        self._status(text, color)

    def _write_batch(self, lines: list[str], rows: list[str] | None = None) -> None:
        """Render a batch of received lines and hand every one to its consumers.

        Runs on main for each drain of ``_on_serial_rx``.  Every line goes
        to the session log, the expect watcher and text capture (registry
        ``rx-every-line-delivered``); only the render is capped, at the
        output pane's ``max_lines``, because a batch longer than that would
        be evicted by RichLog the moment it was written.  That cap is what
        keeps a main thread catching up on a flood from stalling on render.
        In the hex view the pane shows ``rows`` instead, which the reader
        built from the received bytes (registry ``hex-view-wire-bytes``).

        Short-circuits during shutdown: reader callbacks can still be
        queued on the event loop when Textual starts tearing down
        widgets.  Skip both the RichLog write and the log-file write
        once ``_shutting_down`` is set so we don't touch a vanished
        widget tree.

        Args:
            lines: Decoded text lines to display and log.
            rows: The hex view's rows to display in place of ``lines``, or
                None in the text view.
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
                from termapy.builtins.commands.find import dismiss

                dismiss(self.repl.ctx)
            except BoundaryException:
                # Defensive: a broken plugin must not block live data.
                diag_log.exception("find.dismiss raised", once="find.dismiss")
                self._find_overlay_active = False
            finally:
                self._find_restore_scroll_on_close = True
        try:
            log = self.query_one("#output", RichLog)
        except SHUTDOWN_RACE:
            return  # widgets torn down between flag check and query
        show_ts = cfg_value(self.cfg, "timestamps")
        flags = self.repl.ctx.ns("flags")
        show_ln = flags.get("line_no", False)
        color_on = flags.get("color", True)
        # One ANSI strip per line, reused by the plain-render and log paths
        # below; each used to run its own (E3).  A text capture strips its
        # own copy, without the eol_markers marker (_feed_rx_consumers).
        plain = [ANSI_RE.sub("", text) for text in lines]
        # from_ansi renders device SGR color; when color is off, render the
        # already-stripped copy so raw escapes don't leak through.  A hex
        # row has no escapes to render.
        from_ansi = rows is None and color_on
        if rows is None:
            shown = lines if color_on else plain
        else:
            shown = rows
        # A device's JSON line is laid out by json_fmt.indent and json_fmt.highlight
        # (display only); highlighting needs color.  The hex view shows bytes.
        json_text = rows is None
        json_indent, json_highlight = self.repl.json_layout()
        # Lines beyond what the pane keeps are counted (line numbers stay
        # true) but not rendered: RichLog would discard them on arrival.
        skip = max(0, len(shown) - log.max_lines) if log.max_lines else 0
        self._line_counter += skip
        renderables: list[Text] = []
        for index, text in enumerate(shown[skip:], skip):
            self._line_counter += 1
            prefix = ""
            if show_ln:
                prefix += f"{self._line_counter:>5} | "
            if show_ts:
                prefix += f"[{format_timestamp()}] "
            shown_markup = None
            if json_text:
                shown_markup = received_json_markup(plain[index], json_indent, json_highlight)
            if shown_markup is not None:
                renderables.append(Text.from_markup(escape_markup(prefix) + shown_markup))
            elif from_ansi:
                renderables.append(Text.from_ansi(f"{prefix}{text}"))
            else:
                renderables.append(Text(f"{prefix}{text}"))
        if renderables:
            # ONE RichLog.write for the whole batch (E1).  Measured 3.7x
            # faster for 50 lines (6.03 ms -> 1.63 ms); render time on main
            # is what bounds how fast a backlog drains.  Consequence to
            # know: a combined write measures one render width for the
            # block, so wrapping for mixed-width lines can differ slightly
            # from the per-line rendering.
            log.write(Text("\n").join(renderables))
        self._log_lines("<", plain)
        self._feed_rx_consumers(lines)

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
        self._history_nav.reset()
        cmd = event.value.strip()
        if not cmd:
            if cfg_value(self.cfg, "send_bare_enter"):
                self._dispatch_on_thread("")
            return

        self.query_one("#cmd", Input).value = ""
        self._submit_line(cmd)

    def _submit_line(self, cmd: str) -> None:
        """Run a command line the way Enter does: history, busy prompt, worker dispatch.

        Enter and a clicked link both come here, so a click is recorded,
        echoed and run exactly like the same command typed by hand.
        """
        # Add to history (remove earlier duplicate, keep most recent)
        if cmd in self.history:
            self.history.remove(cmd)
        self.history.append(cmd)
        if len(self.history) > self._HISTORY_LIMIT:
            self.history.pop(0)
        self._update_suggester()

        self._begin_busy("Running... escape to cancel")
        self._dispatch_on_thread_interactive(cmd)

    def on_prompt_input_multiline_paste(self, event: PromptInput.MultilinePaste) -> None:
        """A paste of several lines into the prompt: send them all, as TeraTerm does.

        With ``multiline_paste`` off, the first line lands in the prompt as
        Textual's own paste puts it, and a status line says the rest were
        left out, so none go missing silently.  A copied transcript (a line
        starting with the echo's prompt) runs only its commands, the prompt
        taken off; its output lines would otherwise go to the device.
        """
        lines = event.lines
        commands = transcript_commands(lines, self.repl.echo_prompt())
        if commands is not None:
            skipped = len(lines) - len(commands)
            self._status(
                f"Pasted a transcript: running its {len(commands)} commands, "
                f"skipping {skipped} lines of output.",
                "yellow",
            )
            lines = commands
        inp = self.query_one("#cmd", Input)
        if not cfg_value(self.cfg, "multiline_paste"):
            inp.insert_text_at_cursor(lines[0])
            self._status(f"Pasted 1 of {len(lines)} lines: multiline_paste is off.", "yellow")
            return
        if self._busy or self.repl.in_script:
            self._status(self._BUSY_MSG, "yellow")
            return
        self._begin_busy(f"Pasting {len(lines)} lines... escape to stop")
        self._paste_on_thread(lines)

    # -- Clickable output ------------------------------------------------------

    _LINK_LIMIT = 5000

    def _register_link(self, link: Link) -> int:
        """Keep ``link`` under a new id for a click to find; the oldest past the limit go."""
        link_id = self._next_link_id
        self._next_link_id += 1
        self._links[link_id] = link
        while len(self._links) > self._LINK_LIMIT:
            self._links.popitem(last=False)
        return link_id

    def _write_link_segments(self, segments: Sequence[Piece], color: str | None = "dim") -> None:
        """Write one line whose linked pieces can be clicked (``IOHandle.output_segments``).

        With a ``color`` the line reads as a ``_status`` line in it (a
        table row); with None, as a markup line, each piece in its own
        style (a help row, ``output_markup_segments``).  A linked piece is underlined and its
        click action names the link's id, never its command, so no text
        from a record (a file name, say) ever reaches an action string.
        Logged as the plain text.  Device output never comes here: only
        views and help build pieces (registry ``links-are-data``).
        """
        if self._main_thread_id != threading.get_ident():
            try:
                self.call_from_thread(self._write_link_segments, segments, color)
            except RuntimeError:
                pass
            return
        line = Text() if color is None else Text(style=f"bold italic {color}")
        for piece in segments:
            style = Style.parse(piece.style) if piece.style else Style()
            if piece.link is not None:
                link_id = self._register_link(piece.link)
                # The id twice: the click action, and the key LinkLog's
                # hover reads to say what the click does.
                meta = {"@click": f"app.click_link({link_id})", LINK_META: link_id}
                style += Style(underline=True, meta=meta)
            line.append(piece.text, style=style)
        try:
            self._pane_write(line)
        except SHUTDOWN_RACE:
            pass  # widgets gone during shutdown
        self._log_lines("#", [line.plain])

    _EXPIRED_LINK_MSG = "That link has expired - run its list again."

    def _describe_link(self, link_id: int) -> Text:
        """What link ``link_id`` names and what a click on it does, for its tooltip (``LinkLog``).

        The definition's first line in bold, what the link adds, then, dim,
        the command as it will be typed, with the live prefix: the one a
        click runs, or the one it puts in the input line for Enter.  With
        /term.color off the colors go, as they do in the pane.
        """
        link = self._links.get(link_id)
        if link is None:
            return Text(self._EXPIRED_LINK_MSG)
        command = f"{cmd_prefix(self.cfg)}{link.command}".rstrip()
        click = f"Click to run: {command}" if link.run else f"Click to fill the input: {command}"
        head, newline, rest = self.repl.link_summary(link.command).partition("\n")
        tip = (Piece(link.tip),) if isinstance(link.tip, str) else link.tip
        parts = (
            Text.assemble((head, "bold"), newline + rest),
            Text.assemble(*((piece.text, piece.style) for piece in tip)),
            Text(click, style="dim"),
        )
        shown = Text("\n\n").join(part for part in parts if part)
        return shown if self._color_on() else without_color(shown)

    def action_click_link(self, link_id: int) -> None:
        """Do what a clicked link says: run its command as typed, or put it in the input line."""
        link = self._links.get(link_id)
        if link is None:
            self._status(self._EXPIRED_LINK_MSG, "yellow")
            return
        command = f"{cmd_prefix(self.cfg)}{link.command}"
        inp = self.query_one("#cmd", Input)
        if not link.run:
            # Ready for what follows: the cursor after a space, nothing
            # selected (the prompt does not select on focus).
            inp.value = f"{command.rstrip()} "
            inp.cursor_position = len(inp.value)
            inp.focus()
            return
        if self._busy or self.query_one("#bottom-bar").disabled:
            self._status(self._BUSY_MSG, "yellow")
            return
        self._submit_line(command)

    # A typed command or paste that finishes sooner than this never shows the
    # busy look: greying the bar and putting it back for a quick command
    # made every button flash.
    _BUSY_LOOK_DELAY_S = 0.15

    def _begin_busy(self, placeholder: str) -> None:
        """A typed command or paste starts: busy now, and looks it if it is still running soon.

        ``_busy`` is the fact (Escape and the click and paste checks read it);
        the grey bar and the ``placeholder`` come only after
        ``_BUSY_LOOK_DELAY_S``, so a quick command repaints nothing.
        """
        self._busy = True
        self._busy_generation += 1
        if self._busy_look_timer is not None:
            self._busy_look_timer.stop()
        self._busy_look_timer = self.set_timer(
            self._BUSY_LOOK_DELAY_S,
            partial(self._show_busy_look, self._busy_generation, placeholder),
        )

    def _show_busy_look(self, generation: int, placeholder: str) -> None:
        """The busy-look timer fired: grey the bar and show ``placeholder``, unless its command already ended.

        ``generation`` is the command the timer was set for.  A timer's
        ``stop()`` does not always beat a timer whose wait has already run
        out (Textual's Windows sleep waits on an executor thread), so the
        callback can run after ``_end_busy``; unguarded, it greyed the bar
        for good and every click answered Busy (found 2026-11-01, on a
        help page that took as long as ``_BUSY_LOOK_DELAY_S`` to render).
        """
        if generation != self._busy_generation:
            return
        self._busy_look_timer = None
        inp = self.query_one("#cmd", Input)
        self._saved_placeholder = inp.placeholder
        inp.placeholder = placeholder
        self._set_input_busy(True)
        self._busy_look_shown = True

    def _end_busy(self) -> None:
        """The typed command or paste ended: not busy, and the busy look undone if it was shown."""
        self._busy = False
        self._busy_generation += 1
        if self._busy_look_timer is not None:
            self._busy_look_timer.stop()
            self._busy_look_timer = None
        if self._busy_look_shown:
            self._busy_look_shown = False
            self._restore_input_placeholder()
            self._set_input_busy(False)

    def _restore_input_placeholder(self) -> None:
        """Restore the command input placeholder after execution."""
        inp = self.query_one("#cmd", Input)
        inp.placeholder = self._saved_placeholder

    def _set_input_busy(self, busy: bool) -> None:
        """Gray out the prompt + bottom-bar buttons while a typed command runs.

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
    def _dispatch_on_thread(self, cmd: str, then: Callable[[], None] | None = None) -> None:
        """Run a command on a background thread, then ``then`` on the main thread once it has finished.

        ``then`` is how a dialog that stays open learns the command is done
        (a picker re-listing its folder after a delete that asked first).
        """
        try:
            self._dispatch_single(cmd)
        except RuntimeError:
            return
        if then is not None:
            try:
                self.call_from_thread(then)
            except RuntimeError:
                pass

    @work(thread=True)
    def _dispatch_on_thread_interactive(self, cmd: str) -> None:
        """Background dispatch for typed input: keeps the line for /run.last, then ends the busy state."""
        try:
            self._dispatch_typed(cmd)
        except RuntimeError:
            pass
        try:
            self.call_from_thread(self._end_busy)
        except RuntimeError:
            pass

    @work(thread=True)
    def _dispatch_sequence_on_thread(self, cmds: list[str]) -> None:
        """Run a sequence of commands on a background thread, waiting out on-connect commands between them."""
        for cmd in cmds:
            self._wait_for_on_connect()
            try:
                self._dispatch_single(cmd)
            except RuntimeError:
                break
            time.sleep(0.05)

    @work(thread=True)
    def _paste_on_thread(self, lines: list[str]) -> None:
        """Send a paste line by line, as Enter sends a line, ``multiline_paste_delay_ms`` apart.

        TeraTerm's paste: a device line goes as pasted, its indentation
        kept (Enter strips a typed line; pasted code needs its indent), a
        blank line sends a bare line ending, and a line starting with the
        prefix runs as a command.  Pasted lines stay out of the history.
        A line that fails stops the rest, as it stops a script, so a paste
        into a closed port answers once rather than once per line.  Escape
        stops it through the shared stop event, which the paste may clear
        because it is then the outermost cancellable operation (registry
        ``plugin-never-clears-stop``).  Each line waits its turn behind
        on-connect commands (registry ``paste-waits-for-on-connect``).
        """
        stop = self.repl._script_stop
        if not self.repl.in_script:
            stop.clear()
        delay_s = cfg_value(self.cfg, "multiline_paste_delay_ms") / 1000.0
        prefix = cmd_prefix(self.cfg)
        sent = 0
        for line in lines:
            self._wait_for_on_connect()
            if stop.is_set():
                break
            command = line.strip() if line.lstrip().startswith(prefix) else line
            try:
                result = self._dispatch_single(command)
            except RuntimeError:
                break
            if not result.success:
                break
            sent += 1
            if sent < len(lines) and delay_s > 0:
                stop.wait(delay_s)
        if sent < len(lines):
            self._status(f"Paste stopped after {sent} of {len(lines)} lines.", "yellow")
        try:
            self.call_from_thread(self._end_busy)
        except RuntimeError:
            pass

    _BUSY_MSG = "Busy - a command is still running."
    # How long an off-main dispatch waits for the guard before giving up.
    _DISPATCH_WAIT_S = 2.0

    def _acquire_dispatch_guard(self) -> bool:
        """Take the dispatch guard: wait briefly off-main, never on main.

        Non-blocking on the MAIN thread is a hard requirement, not a
        preference: a worker holding the guard may be parked inside
        ``call_from_thread`` waiting for main, so a main thread that waits
        for the guard closes the cycle and deadlocks both.

        Off main there is no such cycle -- the holder's marshalling still
        runs -- so a worker waits instead of dropping the line.  That
        matters here more than in most apps: a refused dispatch is not a
        delayed command, it is a command that never reached the device.

        Returns:
            True if the guard was acquired (the caller must release it).
        """
        if self._main_thread_id == threading.get_ident():
            return self._dispatch_guard.acquire(blocking=False)
        return self._dispatch_guard.acquire(timeout=self._DISPATCH_WAIT_S)

    def _dispatch_single(self, cmd: str, *, report: bool = True) -> CmdResult:
        """Dispatch a single command (delegates to repl.dispatch_full).

        Safe to call from any thread - output helpers (_status,
        _write_output_markup) detect their thread internally.  Serialized
        via _dispatch_guard so a second command on another thread is
        refused rather than corrupting the shared PluginContext; nested
        ctx.dispatch on the same thread re-enters freely.  This is the
        TUI's ``ctx.dispatch``, so it takes ``report`` like the others:
        False when the caller answers with the result and prints it.
        """
        if not self._acquire_dispatch_guard():
            if report:
                self._status(self._BUSY_MSG, "yellow")
            return CmdResult.fail(msg=self._BUSY_MSG)
        try:
            return self.repl.dispatch_full(
                cmd,
                log=self._log_line,
                echo_markup=self._write_output_markup,
                status=self._status,
                serial_write=self._serial_write,
                is_connected=lambda: self.is_connected,
                eol_label=eol_label_markup,
                report=report,
            )
        finally:
            self._dispatch_guard.release()

    def _repl_line(self, command: str) -> str:
        """``command`` as a REPL line under the config's prefix.

        Every line the TUI synthesizes (a button, a menu action, a palette
        entry) is built here, so the prefix is never spelled in code
        (registry ``prefix-never-hardcoded``).
        """
        return f"{cmd_prefix(self.cfg)}{command}"

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
        if not self._acquire_dispatch_guard():
            self._status(self._BUSY_MSG, "yellow")
            return CmdResult.fail(msg=self._BUSY_MSG)
        try:
            return self.repl.dispatch_full(
                cmd,
                log=self._log_line,
                echo_markup=lambda _markup: None,
                status=self._status,
                serial_write=self._serial_write,
                is_connected=lambda: self.is_connected,
                eol_label=eol_label_markup,
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

    def _show_commands(self) -> None:
        """Delegate to ``info_views.show_commands``."""
        from termapy.info_views import show_commands

        show_commands(self)

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

    def on_key(self, event: events.Key) -> None:
        """Handle Up/Down for history cycling, Escape to dismiss popup or clear.

        In the output pane a printable key goes back to the prompt and is
        typed there, and Escape clears a selection and returns to it.
        """
        # Don't intercept keys when a modal screen is active
        if len(self.screen_stack) > 1:
            return
        pane = self.focused if isinstance(self.focused, SelectableRichLog) else None
        if pane is not None and event.is_printable and event.character:
            if self._return_to_prompt(pane, event.character):
                event.prevent_default()
                event.stop()
            return
        if event.key not in ("up", "down", "escape"):
            return

        inp = self.query_one("#cmd", Input)
        popup = self.query_one("#history-popup", OptionList)
        popup_visible = popup.has_class("visible")

        if event.key == "escape":
            if self._engine.serial_claimed:
                self._xfer_cancel.set()
                event.prevent_default()
            elif pane is not None:
                # Nothing is selected here (the pane keeps an Escape that
                # clears a selection, SelectionDismissed): back to the
                # prompt, and Escape keeps its other jobs.  The binding
                # still sees the pane focused (the focus moves after this
                # key), so it stops what runs and sends the device nothing.
                self._return_to_prompt(pane)
            elif popup_visible:
                self._hide_history()
                event.prevent_default()
            elif self._history_nav.browsing:
                self._history_nav.reset()
                inp.value = ""
                event.prevent_default()
            return

        if event.key == "up":
            if not inp.has_focus or popup_visible:
                return
            if not self._history_nav.browsing:
                # Starting a browse: refresh from the file first, so a
                # line appended since startup is recallable.
                self._sync_history()
            value = self._history_nav.up(self.history, inp.value)
            if value is None:
                return
            inp.value = value
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
            value = self._history_nav.down(self.history)
            if value is None:
                return
            inp.value = value
            inp.action_end()
            event.prevent_default()

    def on_link_log_width(self, message: LinkLog.Width) -> None:
        """Hold the output pane's row width for the dispatch workers (registry table-fits-the-frontend)."""
        self._output_width = message.columns

    def on_selectable_rich_log_selection_dismissed(
        self, message: SelectableRichLog.SelectionDismissed
    ) -> None:
        """Escape cleared a selection in the pane: the keyboard goes back to the prompt."""
        self._return_to_prompt(message.log)

    def action_select_output(self) -> None:
        """Shift+Up/Down from the prompt: select the output's newest row, the keyboard now in the pane."""
        if len(self.screen_stack) > 1:
            raise SkipAction()
        pane = next(
            (pane for pane in self.query(SelectableRichLog) if pane.display and pane.lines),
            None,
        )
        if pane is None:
            raise SkipAction()
        pane.focus()
        pane.select_newest_row()

    def _return_to_prompt(self, pane: SelectableRichLog, typed: str | None = None) -> bool:
        """Give the keyboard back to the prompt, typing ``typed`` there; False while a script owns it."""
        inp = self.query_one("#cmd", Input)
        if inp.disabled:
            return False
        if typed is not None:
            pane.deselect()
        inp.focus()
        if typed is not None:
            inp.insert_text_at_cursor(typed)
        return True

    def action_stop_script(self) -> None:
        """Escape: stop what is running; with nothing to stop, clear the prompt, or send Esc when it is empty.

        ``on_key`` runs first and keeps Escape for its own jobs (cancel a
        transfer, close the history popup, leave history browsing), and then
        this binding never fires.  Here Escape stops a script, a paste, a
        replay, a /delay or a /repeat.  With nothing running and the prompt
        focused, it clears the prompt, or, empty, is the device's
        (``/term.ctrl esc``) unless ``control_keys`` is off: the same shape
        as Ctrl+C (copy, else send) and Ctrl+D (delete, else send).
        """
        inp = self.query_one("#cmd", Input)
        if self.focused is inp and self._nothing_running():
            if inp.value:
                inp.value = ""
            elif cfg_value(self.cfg, "control_keys"):
                self._dispatch_on_thread(self._repl_line("term.ctrl esc"))
            return
        self.repl._script_stop.set()

    def _nothing_running(self) -> bool:
        """Whether no script and no command runs, so a key has nothing of termapy's to stop.

        A script is asked for (it keeps the bar live for its Stop button); a
        typed command, paste or replay is ``_busy`` from submit to finish,
        including the pause between pasted lines and before its grey look
        shows; a command from a button or the palette holds the dispatch
        guard (tested non-blocking, so main never waits on it).
        """
        if self.repl.in_script or self._busy:
            return False
        if not self._dispatch_guard.acquire(blocking=False):
            return False
        self._dispatch_guard.release()
        return True

    def action_send_control(self, key: str) -> None:
        """Send Ctrl+<key> to the device, as ``/term.ctrl <key>``, when nothing on screen took it.

        Reached only after the focused widget passed the key on: Ctrl+C
        when no text is selected, Ctrl+D or Ctrl+Z on an empty prompt.  Never over a
        dialog: Textual's binding chain stops at a modal screen, so app
        bindings do not fire there.  With ``control_keys`` off the key does
        nothing.
        """
        if cfg_value(self.cfg, "control_keys"):
            self._dispatch_on_thread(self._repl_line(f"term.ctrl {key}"))

    def action_btn_help(self) -> None:
        self._btn_help()

    def action_btn_cfg(self) -> None:
        if cfg_value(self.cfg, "cfg_enabled"):
            self._btn_cfg()

    def action_btn_run(self) -> None:
        if cfg_value(self.cfg, "run_enabled"):
            self._btn_scripts()

    def action_btn_proto(self) -> None:
        if cfg_value(self.cfg, "proto_enabled"):
            self._btn_proto()

    def action_btn_open_config(self) -> None:
        self._btn_title_center()

    def action_help_quit(self) -> None:
        """Stay silent on a Ctrl+C that had nothing to copy.

        Ctrl+C copies the output pane's selection (the screen's binding);
        with nothing selected Textual would toast "Press Ctrl+Q to quit",
        and Ctrl+Q is already in the footer.
        """
        return

    def action_clear_log(self) -> None:
        try:
            self.query_one("#output", RichLog).clear()
        except SHUTDOWN_RACE:
            pass

    def action_screenshot(self, filename: str | None = None, path: str | None = None) -> None:
        svg_path = str(
            stamped_path(ensure_folder(self.repl.ss_dir), "screenshot", ".svg").resolve()
        )
        self.save_screenshot(svg_path)
        self.last_screenshot = svg_path
        self.notify(f"Screenshot saved: {svg_path}", timeout=1.5, markup=False)
        self._sync_ss_button()

    def action_text_screenshot(self) -> None:
        txt_path = str(
            stamped_path(ensure_folder(self.repl.ss_dir), "screenshot", ".txt").resolve()
        )
        text = self._get_screen_text()
        Path(txt_path).write_text(text, encoding="utf-8")
        self.last_screenshot = txt_path
        self.notify(f"Text screenshot saved: {txt_path}", timeout=1.5, markup=False)
        self._sync_ss_button()

    def action_open_screenshot(self) -> None:
        if not self._require_config():
            return
        self._open_data_folder(self.repl.ss_dir)

    def _open_captures_dir(self) -> None:
        if not self._require_config():
            return
        self._open_data_folder(self.repl.cap_dir)

    def _open_data_folder(self, folder: Path) -> None:
        """Open a data folder in the file manager, creating it first.

        The SS and Captures buttons.  Goes through ``ctx.fs.open_file`` (the
        seam the ``.explore`` commands use and tests record on) rather than
        ``open_with_system`` directly, so a headless or served session gets
        a notice instead of a window on the wrong machine -- the gate the
        config picker's Explorer button honors too.
        """
        if not self.repl.ctx.capabilities.gui_apps:
            self.notify("No file manager in this environment", severity="warning")
            return
        self.repl.ctx.fs.open_file(ensure_folder(folder).resolve())

    def _sync_cmd_prefix(self) -> None:
        """Update the command prefix button and input placeholder."""
        prefix = cmd_prefix(self.cfg)
        try:
            self.query_one("#btn-cmds", Button).label = Text(prefix)
            self.query_one(
                "#cmd", Input
            ).placeholder = f"{prefix} for REPL, {PALETTE_HOTKEY} for palette"
        except SHUTDOWN_RACE:
            pass  # prefix changed before mount or during teardown

    def _sync_all_buttons(self) -> None:
        """Refresh all file-count button tooltips and custom buttons."""
        self._sync_file_buttons()
        self._sync_custom_buttons()

    def _sync_file_buttons(self) -> None:
        """Refresh the tooltips that count a folder's files (SS, Scripts, Proto, Cap)."""
        self._sync_ss_button()
        self._sync_scripts_button()
        self._sync_proto_button()
        self._sync_cap_button()

    def _folder_changed(self, folder: str) -> None:
        """``ctx.internal.folder_changed``: a delete left the folder buttons' counts stale."""
        self._on_main(self._sync_file_buttons)

    @staticmethod
    def _count_files(directory: Path, pattern: str) -> int:
        """Count files matching a glob pattern in a directory."""
        if not directory.exists():
            return 0
        return len(list(directory.glob(pattern)))

    def _newest_age(self, directory: Path) -> str:
        """``", newest 10 min ago"`` for a tooltip, or ``""`` when the folder is empty."""
        newest = self._newest_file(directory)
        if newest is None:
            return ""
        try:
            return f", newest {format_age(newest.stat().st_mtime)}"
        except OSError:
            return ""

    def _sync_ss_button(self) -> None:
        """Update the SS button tooltip with file counts and the newest file's age."""
        btn = self.query_one("#btn-ss-dir", Button)
        ss_dir = self.repl.ss_dir
        svgs = self._count_files(ss_dir, "*.svg")
        txts = self._count_files(ss_dir, "*.txt")
        if svgs or txts:
            btn.tooltip = (
                f"Open screenshot folder ({svgs} svg, {txts} txt{self._newest_age(ss_dir)})."
            )
        else:
            btn.tooltip = "Open screenshot folder (empty)."

    def _sync_scripts_button(self) -> None:
        """Update the Scripts button tooltip with file counts and the newest file's age."""
        btn = self.query_one("#btn-scripts", Button)
        count = self._count_files(self.repl.scripts_dir, FOLDER_PATTERNS["run"])
        hk = _hotkey_label("btn-scripts")
        suffix = f"{count} available{self._newest_age(self.repl.scripts_dir)}" if count else "empty"
        btn.tooltip = f"Run a script ({hk}, {suffix})."

    def _sync_proto_button(self) -> None:
        """Update the Proto button tooltip with file counts and the newest file's age."""
        btn = self.query_one("#btn-proto", Button)
        count = self._count_files(self.repl.proto_dir, FOLDER_PATTERNS["proto"])
        hk = _hotkey_label("btn-proto")
        suffix = f"{count} available{self._newest_age(self.repl.proto_dir)}" if count else "empty"
        btn.tooltip = f"Protocol test scripts ({hk}, {suffix})."

    def _sync_cap_button(self) -> None:
        """Update the Captures button tooltip with file counts and the newest file's age."""
        btn = self.query_one("#btn-cap-dir", Button)
        count = self._count_files(self.repl.cap_dir, FOLDER_PATTERNS["cap"])
        btn.tooltip = (
            f"Open captures folder ({count} files{self._newest_age(self.repl.cap_dir)})."
            if count
            else "Open captures folder (empty)."
        )

    @staticmethod
    def _custom_button(i: int, cb: dict) -> Button:
        """A custom button from its cfg entry; the name and tooltip are cfg text, so Text."""
        button = Button(Text(cb.get("name", f"C{i}")), id=f"btn-custom-{i}")
        button.tooltip = Text(cb.get("tooltip", cb.get("name", "")))
        button.add_class("custom-btn")
        return button

    @work(exclusive=True, group="custom-buttons")
    async def _sync_custom_buttons(self) -> None:
        """Mount the enabled custom buttons where ``custom_buttons_side`` puts them.

        The one builder of custom buttons: at startup, on a config switch and
        when either key changes.  ``left`` / ``right`` fill the
        ``#custom-side`` column docked to that edge of the output pane;
        ``bottom``, and a value ``validate_config`` warned about, fill the
        bottom bar's ``#custom-bar``.  Only a region holding buttons shows.
        Exclusive, so a second change cancels a sync still mounting.
        """
        for old_button in list(self.query(".custom-btn")):
            await old_button.remove()
        side = cfg_value(self.cfg, "custom_buttons_side")
        bar = self.query_one("#custom-bar", Horizontal)
        column = self.query_one("#custom-side", VerticalScroll)
        target = column if side in ("left", "right") else bar
        column.set_class(side == "left", "left")
        buttons = [
            self._custom_button(i, entry)
            for i, entry in enumerate(cfg_value(self.cfg, "custom_buttons"))
            if entry.get("enabled", False)
        ]
        if buttons:
            await target.mount_all(buttons)
        bar.display = target is bar and bool(buttons)
        column.display = target is column and bool(buttons)

    # -- REPL hook implementations (app-coupled commands) ----------------------

    def _delay_progress(self, elapsed: float, total: float, done: bool) -> None:
        """Draw /delay's progress: on the script overlay while a script runs, else the bottom bar.

        Called off main by /delay (``ctx.internal.delay_progress``); the
        overlay message and ``_on_main`` both reach the widgets safely.
        """
        bar = "" if done else render_progress_bar(elapsed, total)
        if self.repl.in_script:
            self.post_message(self.DelayProgress(bar, done))
        else:
            self._on_main(self._set_progress_label, bar)

    def _set_progress_label(self, text: str) -> None:
        """Write progress text to the bottom-bar status label (main thread).

        Empty text hides the label.  Same mechanism used by the capture
        progress overlay -- Input.placeholder doesn't reliably re-render
        from background threads, the status label does.
        """
        try:
            self.query_one("#status-bar", StatusBar).set_progress(text)
        except SHUTDOWN_RACE:
            pass

    def _refresh_traffic(self) -> None:
        """Show the engine's TX/RX counts while ``/term.traffic`` is on, hide them when off (main thread).

        Runs twice a second from ``on_mount``.  It reads the flag each tick,
        so a config load, which turns the flag off, hides the counts with no
        hook.
        """
        ctx = getattr(getattr(self, "repl", None), "ctx", None)
        on = ctx is not None and bool(ctx.ns("flags").get("traffic"))
        if on:
            tx, rx = ctx.serial.byte_counts()
            text = f"TX {format_size(tx)}  RX {format_size(rx)}"
        else:
            text = ""
        try:
            self.query_one("#traffic-bar", StatusBar).set_progress(text)
        except SHUTDOWN_RACE:
            pass

    def _apply_port_effects(self, effects: dict) -> None:
        """Apply side effects from a port_control function (used by port plugin).

        The base handles the flat ``cfg_update`` routing (v22 serial
        nesting); this override adds the TUI-only title/hardware refresh.
        """
        super()._apply_port_effects(effects)
        if effects.get("update_title"):
            self._update_title()
        if effects.get("sync_hw"):
            self._sync_hw_visibility()
            self._sync_hw_buttons()

    def _refresh_after_cfg(self, key: str, new_val: object) -> None:
        was_connected = self.is_connected
        if key in port_control.SERIAL_KEYS and was_connected:
            self._disconnect()
        self._update_title()
        self._apply_border_color()
        self._sync_hw_visibility()
        if key == "cmd_prefix":
            self._sync_cmd_prefix()
        if key in ("custom_buttons", "custom_buttons_side"):
            self._sync_custom_buttons()
        if key in port_control.SERIAL_KEYS and was_connected:
            self._connect()

    def _hook_cfg_confirm(self, key: str, new_val: object) -> bool:
        """``/cfg <key> <value>`` in the TUI: ask, wait for the answer, and apply on Yes.

        Returns:
            True when the change was applied; False on Cancel, and on a
            call from the main thread, which cannot wait.
        """
        # The current value from wherever the key sits: a section (serial,
        # json_fmt) or the top level, as /cfg found it.
        slot = cfg_slot(self.cfg, key)
        old_val = slot[0][slot[1]] if slot is not None else None
        if not self._ask(CfgConfirm(key, old_val, new_val)):
            return False
        self.repl._apply_cfg(key, new_val)
        return True

    def _on_script_picked(self, result: tuple | None) -> None:
        """Delegate to ``pickers.on_script_picked``."""
        from termapy.pickers import on_script_picked

        on_script_picked(self, result)

    def _on_script_saved(self, path: str | None) -> None:
        if path:
            self._status(f"Script saved: {Path(path).name}", "green")
            self._sync_scripts_button()
        self._sync_proto_button()

    def _on_proto_picked(self, result: tuple | None) -> None:
        """Delegate to ``pickers.on_proto_picked``."""
        from termapy.pickers import on_proto_picked

        on_proto_picked(self, result)

    def _on_proto_saved(self, path: str | None) -> None:
        """Handle result from the ProtoEditor dialog.

        Args:
            path: Saved file path, or None if canceled.
        """
        if path:
            self._status(f"Proto script saved: {Path(path).name}", "green")
            self._sync_proto_button()

    def _open_picker(self, name: str) -> CmdResult:
        """Delegate to ``pickers.open_picker``."""
        from termapy.pickers import open_picker

        return open_picker(self, name)

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
                # A TYPED /run reached here through _set_input_busy(True),
                # which disabled the bar before this overlay existed; a
                # Stop button mounted into a disabled bar is inert (only
                # Escape stopped the script).  The script owns the bar
                # from here, so enable it: its own buttons are hidden
                # above and Stop must be clickable.
                bar.disabled = False
                label = Static(text, id="script-label", markup=False)
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
                # Top-level script done - teardown overlay.
                self._script_last_label = ""
                # Re-enable and focus the input BEFORE removing the Stop
                # button.  While the input was disabled during the script,
                # focus moved to the Stop button; removing a focused widget
                # makes Textual reset focus asynchronously, which clobbers a
                # focus() issued after the remove (leaving the REPL input
                # unfocused -- keyboard dead though buttons still work).
                # Focus first so Stop is unfocused when removed, then
                # re-assert once the refresh settles.
                inp.disabled = False
                # A TYPED /run also went through _set_input_busy(True),
                # which sets bar.disabled=True; its paired (False) bails out
                # once the script overlay exists, so the bar would stay
                # disabled after the script.  The script lifecycle owns the
                # bar now -- re-enable it here so both the button path and
                # the typed path end usable.
                bar.disabled = False
                inp.focus()
                for widget in bar.query("#script-label, #script-stop"):
                    widget.remove()
                for child in bar.children:
                    child.display = True
                self.call_after_refresh(inp.focus)
        except SHUTDOWN_RACE:
            pass  # event posted after widget tree teardown

    @work(thread=True)
    def _run_script(
        self,
        path: Path,
        profile: bool = False,
        verbose: bool = False,
        echo: bool = False,
    ) -> None:
        """Threaded wrapper for repl.run_script (needs @work decorator).

        _status and _dispatch_single are thread-safe, so no
        call_from_thread wrapper needed for write/dispatch.
        """
        self.post_message(self.ScriptStarted(self.repl._script_stack[:]))

        def _progress(step: int, total: int) -> None:
            self.post_message(self.ScriptProgress(self.repl._script_stack[:], step, total))

        def _on_nest() -> None:
            self.post_message(self.ScriptStarted(self.repl._script_stack[:]))

        try:
            self.repl.run_script(
                path,
                write=self._status,
                dispatch=self._dispatch_single,
                profile=profile,
                verbose=verbose,
                echo=echo,
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


def _run_proto_headless(args: argparse.Namespace) -> None:
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
    pro_path = resolve_proto_path(args.proto, config_path)
    if pro_path is None:
        proto_dir = proto_dir_for(config_path)
        print(
            f"termapy: proto file not found: {Path(proto_script_name(args.proto)).resolve()}",
            file=sys.stderr,
        )
        if proto_dir.exists():
            print(f"  (checked {proto_dir.resolve()})", file=sys.stderr)
        sys.exit(2)

    # Run tests
    template = cfg_value(cfg, "proto_results_template")
    # run_proto_tests executes user-supplied .pro content, which drives
    # arbitrary command handlers and serial I/O; ValueError comes from
    # the parser specifically, everything else from the test session
    # is a trust-boundary catch.
    diag_log.start("proto", str(config_path))
    try:
        results = run_proto_tests(pro_path, cfg, template=template)
    except ValueError as e:
        print(f"termapy: {e}", file=sys.stderr)
        sys.exit(2)
    except BoundaryException as e:
        diag_log.exception("headless proto run raised: %s", pro_path)
        print(
            f"termapy: test error: {type(e).__name__}: {e}{diag_log.traceback_note()}",
            file=sys.stderr,
        )
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


def _run_web_mode(args: argparse.Namespace) -> None:
    """Serve the TUI in a web browser via textual-serve."""
    try:
        # No ty: ignore here -- the gate runs with all extras synced (see
        # CLAUDE.md), so textual_serve resolves and a suppression would be
        # flagged as unused.  The ImportError path below is what handles a
        # real install without the [web] extra.
        from textual_serve.server import Server
    except ImportError:
        print("Error: --web requires textual-serve.")
        print("  pip install termapy[web]")
        print("  uv tool install termapy[web]")
        sys.exit(1)

    print("=" * 60)
    print("  EXPERIMENTAL: Web mode via textual-serve")
    print(
        f"  {DEFAULT_CMD_PREFIX}tui and {DEFAULT_CMD_PREFIX}cli mode switching are not available."
    )
    print(f"  {DEFAULT_CMD_PREFIX}help.open may not work in the browser.")
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

    # The served TUI runs on THIS machine while the viewer sits in a browser
    # somewhere else, so the child's GUI detection must not see this
    # machine's display: anything it "opened" would open here, unseen.
    # The child inherits the environment; setdefault keeps an explicit
    # TERMAPY_GUI=1 from an operator who knows better.
    import os

    os.environ.setdefault("TERMAPY_GUI", "0")
    # The server holds no serial port (each browser session is a TUI
    # process with its own breadcrumb); this one lets --kill find it.
    from termapy import breadcrumbs

    breadcrumbs.leave("web", config=args.config or "")
    server = Server(
        " ".join(cmd_parts),
        host="localhost",
        port=args.web_port,
        title="Termapy",
    )
    server.serve()


def _run_tui_mode(args: argparse.Namespace) -> str | None:
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
            print(f"termapy: failed to load config '{config_path}': {e}", file=sys.stderr)
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
            print(f"termapy: failed to load config '{config_path}': {e}", file=sys.stderr)
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
