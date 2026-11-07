"""Base class for the TUI, CLI and MCP frontends: builds the PluginContext, owns the one TX path.

Owns the shared serial-I/O, dispatch, capture, help-server, and
hook-handler logic that both the Textual TUI (``app.py``) and the
headless CLI (``cli.py``) need.  Subclasses provide output, threading,
and UI-specific hooks.

No Textual or Rich imports - those stay in the subclasses.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from termapy import load_report
from termapy.config import (
    cap_folder_of,
    load_config,
    mcp_log_path,
    open_serial,
    open_with_system,
    session_log_file,
)
from termapy.config_resolve import resolve_config
from termapy.defaults import cfg_value, quiet_start
from termapy.plugins import CmdResult, InternalHandle, PluginContext
from termapy.plugins.capabilities import STATE_MESSAGES, state_message
from termapy.scripting import strip_ansi
from termapy.serial_port import eol_label_markup, strip_eol_markers
from termapy.session_log import (
    LOG_STARTED,
    LOG_STOPPED,
    SESSION_START,
    STATUS_MARKER,
    SessionLog,
)
from termapy.typed_lines import TypedLine

if TYPE_CHECKING:
    from collections.abc import Callable

    from termapy.capture import CaptureEngine
    from termapy.repl import ReplEngine
    from termapy.serial_engine import SerialEngine


def _format_resolved_line(spec: str, actual: str) -> str:
    """Build the 'Resolved X -> Y' status line shown on connect.

    Called only when ``spec != actual`` -- i.e. the config's port spec
    translated to a different device name.  Consults
    ``resolve_port_trace`` to say which candidate in a fallback chain
    won, so "Resolved A1B2C3D4|COM3 -> COM4" tells the user exactly
    which lookup path got them here.
    """
    from termapy.port_control import (
        MATCH_AMBIGUOUS,
        MATCH_LITERAL,
        MATCH_RESERVED,
        MATCH_SERIAL,
        MATCH_URL,
        resolve_port_trace,
    )

    reasons = {
        MATCH_LITERAL: "literal",
        MATCH_SERIAL: "serial number",
        MATCH_RESERVED: "reserved",
        MATCH_URL: "URL",
    }
    # Find which candidate in the chain was the first to resolve.  If
    # none reports a match reason, we still show the spec -> actual
    # translation but without a reason (edge case: would only happen
    # if an unusual pyserial URL resolved implicitly).
    trace = resolve_port_trace(spec)
    winning_candidate = None
    winning_reason = None
    for candidate, reason in trace:
        if reason is not None and reason != MATCH_AMBIGUOUS:
            winning_candidate = candidate
            winning_reason = reason
            break

    if winning_reason is not None and winning_reason in reasons:
        reason_label = reasons[winning_reason]
    elif winning_reason is not None:
        reason_label = winning_reason
    else:
        reason_label = "match"

    if "|" in spec and winning_candidate is not None:
        return f"Resolved {spec} -> {actual} ({reason_label} matched {winning_candidate})"
    if winning_reason is not None:
        return f"Resolved {spec} -> {actual} ({reason_label} match)"
    return f"Resolved {spec} -> {actual}"


def _is_recording() -> bool:
    """Forward to the /run.record builtin's module-level state.

    Imported here -- not at module import time -- so the terminal
    host has no static dependency on a specific built-in plugin.
    The function is called via ``ctx.internal.is_recording()`` from
    the TUI Record button to decide which form of /run.record to
    dispatch.
    """
    from termapy.builtins.commands import _run_record

    return _run_record.is_active()


class TerminalHost:
    """Base class shared by TUI and CLI terminal frontends.

    Not an ABC (Textual's ``App`` metaclass conflicts with ``ABCMeta``).
    Subclasses must override the methods marked *override required* below
    and set the following attributes before calling any base methods:

    * ``cfg``          - config dict
    * ``config_path``  - path to the config file
    * ``engine``       - ``SerialEngine`` instance
    * ``repl``         - ``ReplEngine`` instance
    * ``capture``      - ``CaptureEngine`` instance
    * ``ctx``          - ``PluginContext`` (set by subclass ``_setup_context``)
    * ``session_log``  - ``SessionLog``, which stays closed in a frontend that keeps none
    """

    _HISTORY_LIMIT: int = 30
    _help_server_port: int = 0
    # /log.stop's state: the log stays closed, through a config switch, until /log.start.
    _log_stopped: bool = False
    # Set when ctx.internal.typed_lines is read; see _dispatch_typed.
    _typed_lines_read: bool = False
    # The frontend's name, ``$(FRONT_END)``; each frontend sets its own.
    front_end: ClassVar[str] = "unknown"

    # -- Attributes set by subclass __init__ ----------------------------------

    cfg: dict
    config_path: str
    repl: ReplEngine
    ctx: PluginContext
    prefix: str
    # Set as ``_engine`` / ``_capture``; read through the properties below,
    # the same in every frontend.
    _engine: SerialEngine
    _capture: CaptureEngine
    session_log: SessionLog

    @property
    def engine(self) -> SerialEngine:
        """This frontend's ``SerialEngine``."""
        return self._engine

    @property
    def capture(self) -> CaptureEngine:
        """This frontend's ``CaptureEngine``."""
        return self._capture

    # -- Override required (subclass must implement) --------------------------

    def _setup_context(self) -> None:
        """Build and assign ``self.ctx`` for this frontend.  Subclass must override."""
        raise NotImplementedError

    def write(self, text: str, color: str = "") -> None:
        """Write text to the frontend output."""
        raise NotImplementedError

    def write_markup(self, text: str) -> None:
        """Write Rich markup text to the frontend output."""
        raise NotImplementedError

    def status(self, text: str, color: str = "") -> None:
        """Write an indented status message."""
        raise NotImplementedError

    def _log(self, direction: str, text: str) -> None:
        """Log callback for serial engine."""
        raise NotImplementedError

    def _start_reader(self) -> None:
        """Start the background serial reader thread."""
        raise NotImplementedError

    def _confirm(self, message: str) -> bool:
        """Prompt for user confirmation."""
        raise NotImplementedError

    def _run_script(
        self,
        path: Path,
        profile: bool = False,
        verbose: bool = False,
        echo: bool = False,
    ) -> None:
        """Run a .run script through the REPL engine (default: synchronous).

        Used by the ``/run`` built-in (via ``ctx.internal.run_script``) so
        one handler works in every host.  CLI and MCP inherit this
        synchronous default; ``SerialTerminal`` (TUI) overrides with a
        Textual ``@work(thread=True)`` variant that also posts the
        ``ScriptStarted`` / ``ScriptProgress`` / ``ScriptFinished``
        messages the overlay UI listens for.

        ``KeyboardInterrupt`` is caught so Ctrl-C stops a long-running
        script cleanly without dumping a traceback.
        """
        try:
            self.repl.run_script(
                path,
                write=self.status,
                dispatch=self.ctx.dispatch,
                profile=profile,
                verbose=verbose,
                echo=echo,
            )
        except KeyboardInterrupt:
            self.status("Script interrupted", "red")

    def _fire_app_start(self) -> None:
        """Start the session's lifecycle: pre-start, then start, in every frontend.

        The one place the app start is announced (registry
        ``prestart-before-start``).  Pre-start first: the shared work every
        frontend needs (:meth:`_register_vars`), the frontend's own
        (:meth:`_on_app_prestart`), then the ``on_app_prestart`` hooks.
        Then ``on_app_start``, whose config load prints the load report --
        so what pre-start sets up, the variables and the session log, is
        in place before anything else happens.  The diagnostic log opens
        first of all, so a failure anywhere after it has a file to go to.
        """
        from termapy import diag_log

        # No config here: on_app_start's load, below, records it as its header.
        diag_log.start(self.front_end)
        self._register_vars()
        self._leave_breadcrumb()
        self._on_app_prestart()
        self.repl.fire_lifecycle("on_app_prestart")
        self.repl.fire_lifecycle("on_app_start")

    def _leave_breadcrumb(self) -> None:
        """Tell ``termapy --kill`` what this session holds: frontend, config and open port.

        Called when the session starts, connects, disconnects, loses its
        port and switches config, so the breadcrumb always names the port
        this process has open (``breadcrumbs``).
        """
        from termapy import breadcrumbs

        serial_cfg = self.cfg.get("serial") or {}
        breadcrumbs.leave(
            self.front_end,
            config=self.config_path or "",
            port=str(serial_cfg.get("port") or ""),
            open_port=(self.engine.open_device or "") if self.engine.is_connected else "",
        )

    def _register_vars(self) -> None:
        """Register the session's variables: ``$(FRONT_END)``, ``$(CFG)``, ``$(CFG.*)``.

        The same set in every frontend, named by its :attr:`front_end`.
        The ``CFG`` values are read when expanded, so a config switch
        needs no second registration.
        """
        from termapy.variables import register_cfg_vars, set_context_var, set_launch_var

        set_launch_var("FRONT_END", self.front_end)
        set_context_var(
            "CFG",
            lambda: Path(self.config_path).stem if self.config_path else "termapy",
        )
        register_cfg_vars(
            get_config_path=lambda: self.config_path,
            get_cfg=lambda: self.cfg,
            get_log_path=lambda: str(self.session_log_path() or ""),
        )

    def _on_app_prestart(self) -> None:
        """The frontend's own pre-start work, after the shared variables.

        Override for something independent of the config's plugins that
        everything after it relies on (the TUI opens its session log here).
        The base has none: the MCP log needs no opening and the CLI keeps
        none.
        """

    def _connect(self, port: str | None = None, *, report: bool = True) -> bool:
        """Connect to a serial port.

        Handles the shared logic: set port, call engine.connect(), build
        connection string, fire lifecycle, start reader.  Returns True on
        success.  Subclasses override ``_on_connected()`` and
        ``_on_connect_failed()`` for UI updates.

        Args:
            port: Port to set before opening; None keeps the cfg's.
            report: Show why an open failed on the status line.  False
                when a command answers for the failure itself
                (``ctx.serial.connect``), so it is shown once.
        """
        if self.engine.is_connected:
            self.status("Already connected", "yellow")
            return False
        if port:
            self.repl._cfg_data["serial"]["port"] = port
        if not self.engine.connect():
            if report:
                self.status(self._open_error(), "red")
            self._on_connect_failed()
            return False
        self._finish_connect()
        return True

    def _open_error(self) -> str:
        """Why the last open of the cfg's port failed, as the user reads it."""
        port_name = self.cfg["serial"]["port"] or "?"
        detail = self.engine.last_error
        return f"Cannot open {port_name}: {detail}" if detail else f"Cannot open {port_name}"

    def _connect_for_command(self, port: str | None = None) -> None:
        """``ctx.serial.connect``: open the port or raise why it did not open.

        The command returns the reason as its failure, so the status line
        stays quiet (``report=False``); an already-open port is not a failure.

        Raises:
            SerialException: The port did not open; the message is the reason.
        """
        if self._connect(port, report=False) or self.engine.is_connected:
            return
        from serial import SerialException

        raise SerialException(self._open_error())

    def _finish_connect(self) -> None:
        """The half of a connect after the port is open: banner, lifecycle, reader, hook.

        Split from ``_connect`` for the auto-reconnect run, which opens the
        port itself (``SerialEngine.reconnect_loop``) and must not open it
        again (registry ``reconnect-opens-once``).
        """
        from termapy.config import connection_string, hardware_signals

        # If the config spec resolved to a different device (e.g. a USB
        # serial number lookup or a pipe fallback chain), tell the user
        # which candidate won so "why am I on COM4 when my config says
        # A1B2C3D4" never becomes a support question.
        spec = self.cfg["serial"]["port"]
        actual = self.engine.open_device or spec
        resolved = _format_resolved_line(spec, actual) if spec != actual else ""

        conn = connection_string(self.cfg, actual_port=actual)
        hw = hardware_signals(self.engine.port_obj)
        full = f"Connected: {conn}  {hw}" if hw else f"Connected: {conn}"
        # In --exec mode the output is for piping; the connect banner
        # would corrupt captured stdout.  Run mode keeps it -- the
        # gold test asserts on it, and human script output benefits
        # from the confirmation.
        banner = not getattr(self, "exec_cmd", None)
        if quiet_start(self.cfg):
            # The screen says where; the session log keeps how: the
            # device the spec resolved to, the settings, the signal lines.
            if resolved:
                self._log("#", resolved)
            if banner:
                self.write(f"Connected: {actual}", "green")
            self._log("#", f"  {conn}  {hw}".rstrip())
        else:
            if resolved:
                self.write(resolved, "green")
            if banner:
                self.write(full, "green")
        self.repl.fire_lifecycle("on_connect")
        self._start_reader()
        self._on_connected(full)
        self._leave_breadcrumb()

    def _on_connected(self, message: str) -> None:
        """Called after a successful connection.  Override for UI updates."""

    def _on_connect_failed(self) -> None:
        """Called after a failed connection.  Override for UI updates."""

    def _disconnect(self) -> None:
        """Disconnect from the serial port.

        Handles the shared logic: fire lifecycle, engine.disconnect(),
        clear device-specific state.  Subclasses override
        ``_on_disconnected()`` for UI updates.
        """
        if not self.engine.is_connected:
            self.status(STATE_MESSAGES["serial_connected"], "yellow")
            return
        # Capture the resolved port name BEFORE disconnect so the
        # banner mirrors "Connected: COM4 ...".  After disconnect()
        # the port_obj is gone, so reading it here is the only
        # place this works.
        actual = self.engine.open_device or self.cfg["serial"]["port"]
        self.repl.fire_lifecycle("on_disconnect")
        self.engine.disconnect()
        self._clear_device_state()
        msg = f"Disconnected: {actual}" if actual else "Disconnected."
        self.write(msg, "red")
        self._on_disconnected()
        self._leave_breadcrumb()

    def _port_lost(self) -> None:
        """Tear down after the port vanished mid-session (an unplug, a driver reset).

        The reader has already reported the loss and closed its port; this is
        the host's half.  An active capture stops, which releases its serial
        claim: left running, a binary capture kept suppressing the display
        across an auto-reconnect.  Then the frontend shows itself
        disconnected (registry ``port-lost-tears-down``).

        Deliberately NOT ``_disconnect``: the ``on_disconnect`` lifecycle
        fires for user-initiated disconnects only (``LifecycleHook`` docs).
        Device state is kept as well -- in MCP it holds the async events
        that explain the loss, such as a panic line printed just before a
        USB reset.
        """
        if self.capture.active:
            self._stop_capture()
        self._on_disconnected()
        self._leave_breadcrumb()

    def _clear_device_state(self) -> None:
        """Wipe namespaces tied to the device that just disconnected.

        ``active_profile`` is per-device: it describes the box on the
        wire, not the termapy session.  Carrying it across a disconnect
        produces a real bug -- the next ``/port.connect <other>`` lands
        you on a new device with the previous device's profile silently
        driving the MCP executor and stale ``/help`` entries cluttering
        completion.

        Subclasses with frontend-specific device state (e.g. MCPHost's
        banner-watch attrs) should extend this rather than override it.
        The load report's profile row goes with the profile.
        """
        self.ctx.ns("active_profile").clear()
        self.repl.load_report.put(load_report.PROFILE, [])

    def _on_disconnected(self) -> None:
        """Called after disconnection.  Override for UI updates."""

    # -- Context builders (subclass extends, not replaces) --------------------

    def _build_internal_handle(self) -> InternalHandle:
        """Build an InternalHandle with the callbacks shared by all frontends.

        Subclasses should call ``super()._build_internal_handle()`` then set
        any frontend-specific fields on the returned object before passing
        it to ``_build_plugin_context()``.
        """
        return InternalHandle(
            plugins=self.repl._plugins,
            # Aliases the engine's list (never a copy): resolve_plugins
            # rebuilds it in place on every config switch, and /sym.import
            # must see the converters the CURRENT config loaded.
            converters=self.repl.converters,
            reload_devices=self.repl.reload_devices,
            library_layers=self.repl.library_layers,
            load_report=lambda: self.repl.load_report.records,
            port=lambda: self.engine.port_obj if self.engine.is_connected else None,
            in_script=lambda: self.repl.in_script,
            # The dispatch gate's own "right now" answer (static env set
            # + dynamic serial_connected / block_until), forwarded so
            # /help's REQUIRED CAPABILITIES rows can render live status
            # without duplicating the single-point-of-truth logic.
            effective_capabilities=lambda: self.repl._effective_capabilities(),
            script_stop=lambda: self.repl._script_stop.set(),
            apply_cfg=self.repl._apply_cfg,
            # Every caller answers with the result (a forwarder, a
            # redirect), so the failure is printed once, at its exit.
            dispatch=lambda line: self.repl.dispatch(line, report=False),
            read_state=self.repl.read_state,
            # set_proto_active intentionally not exposed -- the bare flag
            # setter is private; ``ctx.serial.io()`` is the public path.
            # TUI override installs a real modal launcher.  In non-TUI
            # environments the capability gate (tui_mode) fails /proto.debug
            # dispatch before this is called, so the no-op default is fine.
            open_proto_debug=lambda path, script: None,
            start_capture=lambda **kw: self._start_capture(**kw),
            stop_capture=lambda: self._stop_capture(),
            capture_active=lambda: self.capture.active,
            load_config=lambda name: self._load_config(name),
            xfer_cancel=getattr(self, "_xfer_cancel", None),
            script_stop_event=self.repl._script_stop,
            # /run's built-in handler reaches script-running via these
            # two callbacks.  ``start_script`` is host-agnostic;
            # ``run_script`` differs by host -- CLI / MCP inherit the
            # synchronous TerminalHost._run_script, TUI overrides with
            # @work(thread=True).
            start_script=lambda args: self.repl.start_script(args),
            run_script=lambda path, profile=False, verbose=False, echo=False: self._run_script(
                path, profile=profile, verbose=verbose, echo=echo
            ),
            # Which file this frontend's session log is: one owner, the
            # host's session_log_path (registry session-log-one-owner).
            session_log_path=lambda: self.session_log_path(),
            session_log_paths=lambda: self.session_log_paths(),
            start_session_log=lambda: self._start_session_log(),
            stop_session_log=lambda: self._stop_session_log(),
            # How this frontend opens a file for editing (/edit decides which).
            edit_file=lambda path: self._edit_file(path),
            # What this frontend refreshes when a command deletes files.
            folder_changed=lambda folder: self._folder_changed(folder),
            # Post-dispatch observer pair, shared by all hosts.  /run.record
            # registers via add_post_dispatch_observer; future audit-log /
            # repeat-last / event-stream features will use the same hook.
            add_post_dispatch_observer=self.repl.add_post_dispatch_observer,
            remove_post_dispatch_observer=(self.repl.remove_post_dispatch_observer),
            # ``is_recording`` reaches into the recorder builtin's
            # module state.  Imported lazily inside the callable so the
            # host has no static dependency on a specific plugin (and
            # so the import is harmless if the builtin isn't loaded).
            is_recording=_is_recording,
            typed_lines=self._read_typed_lines,
            # ``update_find_bar`` is None on the base host; the TUI
            # subclass sets it to its own _update_find_bar method
            # after super().__init__() runs.  CLI/MCP hosts inherit
            # None, which the /find builtin treats as "no UI; no-op".
            update_find_bar=None,
        )

    def _build_plugin_context(self, internal_handle: InternalHandle) -> PluginContext:
        """Build a PluginContext with the callbacks shared by all frontends.

        Subclasses should call ``super()._build_plugin_context(api)`` then
        replace any frontend-specific fields on the appropriate handle
        (``ctx.io._write = self._status``, etc.) before calling
        ``repl.set_context()``.
        """
        from termapy.plugins.handles.fs import FilesystemHandle
        from termapy.plugins.handles.io import IOHandle
        from termapy.plugins.handles.serial import SerialHandle
        from termapy.plugins.handles.ui import UIHandle

        io = IOHandle(
            _write=self.status,
            _write_markup=self.write_markup,
            log=self._log,
        )

        serial = SerialHandle(
            is_connected=lambda: self.engine.is_connected,
            connect=self._connect_for_command,
            disconnect=lambda: self._disconnect(),
            update_port=lambda name: self._update_port(name),
            apply_port_effects=lambda effects: self._apply_port_effects(effects),
            write=self._serial_write,
            read_raw=self._serial_read_raw,
            drain=self._drain_rx_queue,
            wait_idle=lambda timeout_ms=100, max_wait_s=3.0: self._wait_for_idle(
                timeout_ms, max_wait_s
            ),
            byte_counts=self.engine.byte_counts,
            rx_queue=self.engine.rx_queue,
            claim=lambda: setattr(self.engine, "serial_claimed", True),
            release=lambda: setattr(self.engine, "serial_claimed", False),
            # Underscore-prefixed: ``ctx.serial.rx_observer(cb)`` and
            # ``ctx.serial.tx_observer(cb)`` context managers are the
            # public path; bare register/unregister stays private so
            # plugin code can't leak observers on exception.
            _add_rx_observer=self.engine.add_rx_observer,
            _remove_rx_observer=self.engine.remove_rx_observer,
            _add_tx_observer=self.engine.add_tx_observer,
            _remove_tx_observer=self.engine.remove_tx_observer,
        )

        fs = FilesystemHandle(
            ss_dir=self.repl.ss_dir,
            scripts_dir=self.repl.scripts_dir,
            proto_dir=self.repl.proto_dir,
            cap_dir=self.repl.cap_dir,
            prof_dir=self.repl.prof_dir,
            _open_file_impl=lambda path: open_with_system(str(path)),
        )

        ui = UIHandle(
            _confirm_impl=lambda msg: self._confirm(msg),
            # TUI subclass overrides _notify_impl, _status_bar_impl, etc.
        )

        ctx = PluginContext(
            cfg=self.cfg,
            config_path=self.config_path,
            internal=internal_handle,
            io=io,
            serial=serial,
            fs=fs,
            ui=ui,
            dispatch=lambda cmd, report=True: self._dispatch(cmd, report=report),
            # /expect's wait, in every host: until 2026-10-16 only the TUI and
            # MCP wired it, and the CLI's scripts reached the engine's wait
            # through the script runner's own /expect instead.
            wait_for_match=self.repl.wait_for_match,
            # is_oneshot: True when --run / --exec is in effect.  Plugins
            # use this to suppress chatter that would corrupt captured
            # stdout.  Defaults to False; CLITerminal overrides via a
            # property that consults run_script / exec_cmd.
            is_oneshot=lambda: getattr(self, "is_oneshot", False),
        )
        # PluginContext.__post_init__ wires output_level_fn and
        # capabilities into the handles automatically.
        return ctx

    def _init_flags(
        self,
        echo_repl: bool = True,
        device_echo_allowed: bool = True,
        color: bool | None = None,
        output_level: str | None = None,
    ) -> None:
        """Initialize the ``flags`` namespace from the config, and the host's own rules over it.

        Runs at startup and again at every load (``_reset_session``), with
        the arguments it was first given: what the user asked for at launch
        (``--no-color``, ``--quiet``) and what a host requires (MCP's JSON
        stays uncolored) outlast any config, and everything else is the
        config's.

        There are two independent local echoes, gated by two flags:

          - ``flags["echo"]`` -- echo of *device* commands (what's sent to
            the wire: bare commands + /term.send).  Seeded from
            ``cfg["echo"]`` -- the "cfg seeds the flag" pattern that
            ``hex`` uses below -- so it persists per config.  Toggled
            by ``/term.echo`` (alias ``/echo``).
          - ``flags["echo_repl"]`` -- echo of *slash / REPL* commands
            (``/cfg``, ``/help``, ...).  A session-only flag with a
            per-host default (below); not cfg-backed.  Toggled by
            ``/term.echo_repl``.

        Kept separate on purpose: at a device prompt you usually want to
        see the commands you send (device echo) but not the REPL meta
        commands.  Device echo (#1, the device parroting your bytes) is a
        device-side concern, not modeled here.

        REPL echo is session-only (no cfg key) because its right default is
        per-host, which a single cfg key can't express: the TUI clears its
        input on Enter, so echoing keeps a scrollback record; the CLI's OS
        terminal already shows the typed line, so app-echo would duplicate
        it.  Hence TUI passes ``echo_repl=True``, CLI/MCP ``False``.

        Args:
            echo_repl: Per-host default for REPL/slash-command echo (True
                for the TUI, False for the CLI and MCP).
            device_echo_allowed: Whether this host may echo device commands
                at all.  True (TUI/CLI) seeds ``flags["echo"]`` from
                ``cfg["echo"]``; False (MCP, headless) forces it off
                regardless of cfg.
            color: Color whatever the config says; ``None`` takes ``cfg["color"]``.
            output_level: Level whatever the config says; ``None`` takes
                ``cfg["output_level"]``.
        """
        self._flag_seeds: dict[str, Any] = {
            "echo_repl": echo_repl,
            "device_echo_allowed": device_echo_allowed,
            "color": color,
            "output_level": output_level,
        }
        flags = self.ctx.ns("flags")
        flags.setdefault(
            "echo",
            cfg_value(self.cfg, "echo") if device_echo_allowed else False,
        )
        flags.setdefault("echo_repl", echo_repl)
        flags.setdefault(
            "output_level",
            cfg_value(self.cfg, "output_level") if output_level is None else output_level,
        )
        flags.setdefault("hex", cfg_value(self.cfg, "hex"))
        # Line numbers: the same pattern.  Only the TUI renders them, but
        # the flag is what /term.line_no sets and /term.info reports.
        flags.setdefault("line_no", cfg_value(self.cfg, "line_no"))
        # The TUI's live TX/RX counts (/term.traffic): session-only, no cfg
        # key, so every load starts with them hidden.
        flags.setdefault("traffic", False)
        # Render device ANSI color (SGR).  Session flag toggled by
        # /term.color; the single source of truth for device color across
        # the TUI (strip before render when off) and CLI (strip in on_lines).
        flags.setdefault("color", cfg_value(self.cfg, "color") if color is None else color)

    def _reset_session(self) -> None:
        """Back to what a fresh start on the loaded config has.

        A load's one reset, in every frontend (registry
        ``config-load-deterministic``): whatever the session did goes, then
        the flags are seeded from the config just loaded, so what came before
        cannot change how this config behaves.  A plugin's session state is
        in ``ctx.ns()``, so this covers it; ``on_config_load`` fires after this
        for what a cleared dict cannot do.
        """
        self._clear_device_state()
        self.repl.reset_session()
        self._init_flags(**self._flag_seeds)
        if self.repl.in_script:
            # A script calling /cfg.load: its lines stay without echo, and its end
            # restores the new config's value rather than the old one's.
            self.repl.hold_script_echo()

    # -- Serial I/O -----------------------------------------------------------

    def _serial_read_raw(self, timeout_ms: int = 1000, frame_gap_ms: int = 0) -> bytes:
        """Collect raw bytes using timeout-based framing (delegates to SerialPort)."""
        frame_gap = frame_gap_ms or cfg_value(self.cfg, "proto_frame_gap_ms")
        if self.engine.serial_port:
            return self.engine.serial_port.read_raw(timeout_ms, frame_gap)
        return b""

    def _feed_rx_consumers(self, lines: list[str]) -> None:
        """Hand received lines to the consumers every frontend shares.

        The expect watcher and text capture.  Each frontend calls this for
        EVERY line it receives, beside its own display and session log
        (registry ``rx-every-line-delivered``).  MCP once skipped the capture
        half, so ``/cap.text`` over MCP recorded nothing, which is why the
        list lives here and not in three copies.

        Both get the line as the device sent it: the ``eol_markers`` display
        marker comes off first, so ``/expect.regex match=^OK$`` matches and a
        captured file holds no ``\\r\\n`` text, and the capture gets it with
        ANSI stripped.

        Args:
            lines: Received lines as the reader produced them.
        """
        received = [strip_eol_markers(line) for line in lines]
        self.repl.feed_lines(received)
        if self.capture.active and self.capture.mode == "text":
            plain = [strip_ansi(line) for line in received]
            if self.capture.feed_text(plain):
                # Write failed mid-capture -- stop so the abort is visible
                # instead of silently dropping the rest.
                self._stop_capture()

    def _hex_view(self) -> bool:
        """Whether ``/term.hex`` is on; every frontend hands this to ``read_loop``.

        Called on the reader thread once per chunk, so the reader, not the
        renderer, decides which view a chunk is shown in.
        """
        return bool(self.ctx.ns("flags").get("hex", False))

    def _drain_rx_queue(self) -> int:
        """Discard all pending bytes in the RX queue (delegates to SerialPort)."""
        if self.engine.serial_port:
            return self.engine.serial_port.drain()
        return 0

    def _wait_for_idle(self, timeout_ms: int = 100, max_wait_s: float = 3.0) -> None:
        """Wait until no serial data arrives for timeout_ms (delegates to SerialPort)."""
        if self.engine.serial_port:
            self.engine.serial_port.wait_for_idle(timeout_ms, max_wait_s)

    def _serial_op(self, label: str, fn: Callable[[], object]) -> None:
        """Run a serial operation, catching OSError/SerialException."""
        from serial import SerialException

        try:
            fn()
        except (OSError, SerialException) as e:
            self.status(f"{label} error: {e}", "red")

    def _serial_write(self, data: bytes) -> None:
        """Write bytes to the port; the one TX path in every frontend.

        A failed write raises to the caller, which reports it
        (``/term.send`` answers ``Send error: ...``), so a send never fails
        silently; with no open port that is ``SerialException("Not
        connected.")``.  TX observers hear only bytes that went out
        (registry ``tx-only-what-went-out``).

        THREADING: the reader thread's ``finally`` disowns
        ``engine.serial_port`` when it exits, so a check-then-use on the
        shared field can pass the check and then write to ``None``.  Bind
        it to a local.

        Raises:
            OSError: The write failed; pyserial's ``SerialException`` is one.
        """
        port = self.engine.serial_port
        if port is None:
            from serial import SerialException

            raise SerialException(STATE_MESSAGES["serial_connected"])
        port.write(data)
        self.engine.notify_tx(data)

    # -- Dispatch -------------------------------------------------------------

    def _dispatch(self, cmd: str, *, report: bool = True) -> CmdResult:
        """Route a command through the full dispatch pipeline (``ctx.dispatch``)."""
        return self.repl.dispatch_full(
            cmd,
            log=self._log,
            echo_markup=self.write_markup,
            status=self.status,
            serial_write=self._serial_write,
            is_connected=lambda: self.engine.is_connected,
            eol_label=eol_label_markup,
            report=report,
        )

    def _dispatch_typed(self, line: str) -> CmdResult:
        """Run a line the user typed at the prompt, and keep it for ``/run.last``.

        The one feed of ``repl.typed_lines``: the TUI's Enter (a clicked
        link too, which runs as typed) and the CLI prompt come here; a
        script line, a paste and a replayed line never do.  The line is
        kept under the config active when it was typed, so a ``/cfg.load``
        lands in the list it was typed in.  A line that read the typed
        lines is not kept: ``/run.last``, or ``/repeat`` around it, would
        replay its own replay.
        """
        config = self.repl.config_path
        self._typed_lines_read = False
        result = self.ctx.dispatch(line)
        if not self._typed_lines_read:
            self.repl.typed_lines.add(config, TypedLine(line, result.success, result.error))
        return result

    def _read_typed_lines(self) -> list[TypedLine]:
        """The active config's typed lines (``ctx.internal.typed_lines``); marks the read."""
        self._typed_lines_read = True
        return self.repl.typed_lines.lines(self.repl.config_path)

    # -- Port effects ---------------------------------------------------------

    def _apply_port_effects(self, effects: dict) -> None:
        """Apply port_control side effects (cfg_update).

        Subclasses may override to add UI updates (title, hw buttons).

        cfg_update keys arrive flat (e.g. ``"baud_rate"``) because the
        port_control producers don't need to know the cfg shape;
        ``ReplEngine.store_cfg`` routes each one.
        """
        for key, val in effects.get("cfg_update", {}).items():
            self.repl.store_cfg(key, val)

    # -- Port switching ---------------------------------------------------------

    def _update_port(self, port: str) -> None:
        """Change serial port for this session and reconnect.

        Does not write to disk -- keeps $(env.NAME) templates intact.
        """
        self.cfg["serial"]["port"] = port
        if self.engine.is_connected:
            self._disconnect()
        self._connect()
        if self.engine.is_connected:
            # Show the actual opened device, not the raw spec the user
            # typed.  A user who typed a USB serial number like
            # D20JSV68A needs to see "Port changed to COM4", not
            # "Port changed to D20JSV68A" -- that's the whole point
            # of surfacing the resolved device in the connect banner.
            actual = self.engine.open_device or port
            self.status(f"Port changed to {actual} (session)", "green")

    # -- Config switching -----------------------------------------------------

    def _load_config(self, name: str) -> CmdResult:
        """Resolve a config name and switch to it.

        Default implementation: resolve the user-supplied name (bare,
        relative, or absolute) via the same chain as the ``[config]``
        positional CLI arg, then call ``_switch_to_cfg_path``.  Works in
        any headless frontend (CLI, MCP) that has the standard engine /
        repl / capture trio.  Subclasses with dialog-based pickers
        (the TUI) override this to surface a frontend-appropriate
        message.
        """
        path = self._resolve_config_name(name)
        if path is None:
            from termapy.config import config_names
            from termapy.matching import closest, did_you_mean

            hint = did_you_mean(closest(name, config_names()))
            if hint:
                return CmdResult.fail(msg=f"No config matching {name!r}{hint}")
            return CmdResult.fail(
                msg=f"No config matching {name!r}. "
                f"Try {self.repl.prefix}cfg.list to list available."
            )
        result = self._switch_to_cfg_path(path)
        if result.success:
            self.status(f"Loaded config: {Path(path).stem}", "green")
        return result

    def _resolve_config_name(self, name: str) -> str | None:
        """The file ``/cfg.load`` takes ``name`` to mean, or None; a host may narrow it."""
        return resolve_config(name)

    def _open_serial(self, cfg: dict) -> Any:
        """The engine's ``open_fn``: ``open_serial``, with the active config's ``cap/`` for a ``spy://`` log."""
        return open_serial(cfg, cap_folder=cap_folder_of(self.config_path))

    def _switch_to_cfg_path(self, config_path: str) -> CmdResult:
        """Disconnect, replace the in-memory cfg, and reconnect.

        Shared machinery used by ``_load_config`` and ``--demo`` setup.
        Caller is responsible for resolving a user-facing name into a
        concrete path (and any cfg-creation side effects).

        Returns ``CmdResult.ok(value=path)`` on success.  OSError or JSON
        decode error becomes ``CmdResult.fail(msg=...)`` so the caller
        can surface the failure through its frontend without unwinding
        the exception.
        """
        try:
            cfg = load_config(config_path)
        except (OSError, ValueError) as e:
            return CmdResult.fail(msg=f"Cannot load config: {e}")

        if self.capture.active:
            self._stop_capture()
        if self.engine.is_connected:
            self.repl.fire_lifecycle("on_disconnect")
            self.engine.disconnect()
        self.repl.replace_cfg(cfg, config_path)
        self.config_path = config_path
        # One cfg object, three views: replace_cfg refreshed the engine's
        # dict in place, so rebind to THAT dict -- not the transient
        # ``cfg`` loaded from disk.  Binding ``cfg`` here forks identity:
        # session writes (``_apply_cfg``: /cfg.auto, /term.request, port
        # changes) land in the engine's dict while ctx.cfg reads a stale
        # snapshot forever after.
        self.cfg = self.repl._cfg_data
        self._leave_breadcrumb()
        self._setup_context()
        self._reset_session()
        self.repl.fire_lifecycle("on_config_load")
        # auto_connect lives in the newly-loaded cfg -- if the user's
        # saved config wants the port opened on start, honor it here.
        # Use self._connect() (not engine.connect() inline) so the full
        # connect lifecycle fires: on_connect lifecycle hook, reader
        # thread start, AND ``_on_connected()``.  The latter is where
        # MCPHost runs auto-load-profile, auto-include, on_connect_cmd,
        # mcp_on_connect_cmd, and the banner watcher.  Inlining
        # ``engine.connect()`` here used to silently skip all of those.
        if cfg_value(cfg, "auto_connect"):
            self._connect()
        return CmdResult.ok(value=config_path)

    # -- Capture helpers ------------------------------------------------------

    def _start_capture(self, **kwargs: Any) -> bool:
        """Start a capture session.

        Subclasses may override to add UI (timer, progress).
        """
        if self.capture.active:
            self.status(state_message("capture_idle", self.prefix))
            return False
        started = self.capture.start(**kwargs)
        if not started:
            self.status("Cannot open capture file")
            return False
        mode = kwargs.get("mode", "?")
        path = kwargs.get("path", "?")
        if mode != "text":
            self.engine.serial_claimed = True
        self.status(f"Capture started: {path} ({mode})")
        return True

    def _stop_capture(self) -> None:
        """Stop a capture session.

        The completion message is reported by the CaptureEngine's
        ``on_complete`` callback (fired inside ``stop()``) -- the single
        owner across all frontends -- so here we only release the serial
        claim.  Subclasses may override to add UI (timer cleanup, progress).
        """
        self.capture.stop()
        # Unconditional: the capture owns its claim (see capture_view._cap_stop).
        self.engine.serial_claimed = False

    # -- Help server ----------------------------------------------------------

    def _ensure_help_server(self) -> int:
        """Start a local HTTP server for help docs if not already running.

        Returns the port number.  The server runs as a daemon thread and
        stops automatically when the process exits.

        After spawning the thread, this method blocks briefly on a
        localhost GET so the accept loop is definitely running before
        the browser is handed the URL.  Without the warm-up, the
        browser's request could land while ``serve_forever`` hasn't
        yet started -- the connection would sit in the TCP backlog
        until a page reload (F5) kicked the request through.
        """
        if self._help_server_port:
            return self._help_server_port
        import urllib.error
        import urllib.request
        from http.server import HTTPServer, SimpleHTTPRequestHandler
        from importlib.resources import files as pkg_files

        html_dir = str(Path(str(pkg_files("termapy").joinpath("html"))).resolve())

        class _QuietHandler(SimpleHTTPRequestHandler):
            def __init__(self, *a: Any, **kw: Any) -> None:
                super().__init__(*a, directory=html_dir, **kw)

            def log_message(self, format: str, *args: object) -> None:
                pass

        server = HTTPServer(("127.0.0.1", 0), _QuietHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        # Warm-up GET: returns when the server has actually serviced one
        # request, confirming the accept loop is live.  URLError / OSError
        # ignored because the subsequent ``webbrowser.open`` will surface
        # any real failure to the user directly.
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2):
                pass
        except (urllib.error.URLError, OSError):
            pass
        self._help_server_port = port
        return port

    def _hook_help_open(self, ctx: PluginContext | None, args: str) -> CmdResult:
        """Open a help topic in the local docs server (the one /help.open, TUI and CLI).

        An unknown topic lists the pages that exist, read from the built
        help itself rather than a list kept beside it.
        """
        from importlib.resources import files as pkg_files

        html_dir = Path(str(pkg_files("termapy").joinpath("html")))
        topic = args.strip()
        if not topic:
            page = "index.html"
        else:
            topic = topic.replace(".md", "").replace(".html", "")
            page = f"{topic}.html"
        if not (html_dir / page).exists():
            topics = sorted(
                path.stem for path in html_dir.glob("*.html") if path.stem not in ("index", "404")
            )
            return CmdResult.fail(
                msg=f"Unknown help topic: {topic} (use one of: {', '.join(topics)})"
            )
        port = self._ensure_help_server()
        import webbrowser

        url = f"http://127.0.0.1:{port}/{page}"
        webbrowser.open(url)
        return CmdResult.ok(value=url)

    # -- Editing ----------------------------------------------------------------

    def _edit_file(self, path: Path) -> None:
        """``ctx.internal.edit_file``: open ``path`` in the system editor.

        The CLI's only editor, through ``ctx.fs.open_file`` (the one gated
        opener); ``file_editor`` (a desktop) gates the command.  The TUI
        overrides this with its own editors.
        """
        self.repl.ctx.fs.open_file(path)

    def _folder_changed(self, folder: str) -> None:
        """``ctx.internal.folder_changed``: the CLI and MCP show no file counts to refresh.

        The TUI overrides this to refresh its folder buttons' tooltips.
        """

    # -- Session log ------------------------------------------------------------

    def session_log_path(self) -> Path | None:
        """The file this frontend writes its session log to, or None when it keeps none.

        The one answer every ``/log.*`` command, ``$(CFG.LOG_FILE)`` and
        ``/cfg.info`` read (review 2026-10-15, D3).  The default is the
        CLI's: no session log.  The TUI and the MCP host override it.
        """
        return None

    def session_log_paths(self) -> list[Path]:
        """Every session log this config has, the frontend's own first: what ``/log.list`` lists.

        The TUI's and the MCP server's, whichever frontend asks, so either
        can see and open the other's transcript.  Paths, existing or not.
        """
        own = self.session_log_path()
        paths = [own] if own is not None else []
        if self.config_path:
            for path in (
                session_log_file(self.cfg, self.config_path),
                mcp_log_path(self.config_path),
            ):
                if path is not None and path not in paths:
                    paths.append(path)
        return paths

    def _log_line(self, prefix: str, text: str) -> None:
        """Write one line to the session log under ``prefix`` (see ``session_log`` for the markers)."""
        self.session_log.write(prefix, [text])

    def _log_lines(self, prefix: str, texts: list[str]) -> None:
        """Write a batch of lines under one timestamp, one write and one flush."""
        self.session_log.write(prefix, texts)

    def _open_log(self, marker: str = SESSION_START) -> None:
        """Open the session log from ``marker`` when a config is loaded and the log is neither open nor stopped."""
        if self.session_log.fh is not None or self._log_stopped or not self.config_path:
            return
        path = self.session_log_path()
        if path is not None:
            self.session_log.open(path, marker)

    def _end_session_log(self) -> None:
        """Write ``Session End`` into the open log, then close it.

        A session leaves a log two ways while it is still open: the
        frontend exits, or a switch moves it to another config's file
        (``_follow_session_log``).  So every ``Session Start`` has its
        ``Session End``, unless ``/log.stop`` closed the log first, and its
        last line says ``Log Stopped``, or the session did not end cleanly,
        which a start without an end then says.
        """
        self.session_log.end()

    def _follow_session_log(self, previous: Path | None) -> None:
        """Move the session log to the config just switched to.

        A config's log is that config's history, so the old file gets where
        the session went and its ``Session End``, and the new one its
        ``Session Start``.  Two configs naming one ``log_file`` share it, so a
        switch between them keeps the open file.
        """
        if self.session_log.fh is not None and self.session_log_path() != previous:
            self._log_line(STATUS_MARKER, f"Switched to config: {Path(self.config_path).stem}")
            self._end_session_log()
        self._open_log()

    def _start_session_log(self) -> bool:
        """``/log.start``: open the log again, appending, from a ``Log Started`` line.

        False when it was not stopped.  A file that cannot be opened raises
        ``OSError`` with the log still stopped, for the command to report.
        """
        if not self._log_stopped:
            return False
        self._log_stopped = False
        try:
            self._open_log(LOG_STARTED)
        except OSError:
            self._log_stopped = True
            raise
        return True

    def _stop_session_log(self) -> bool:
        """``/log.stop`` and ``/log.delete``: mark the gap, close the log, keep it closed until ``/log.start``.

        False when it was already stopped.  A delete stops it first, so it
        stays stopped after one too.
        """
        if self._log_stopped:
            return False
        self._log_stopped = True
        self.session_log.end(LOG_STOPPED)
        return True
