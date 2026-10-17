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
from typing import TYPE_CHECKING

from termapy.config import load_config, open_with_system
from termapy.config_resolve import resolve_config
from termapy.plugins import CmdResult, InternalHandle, PluginContext
from termapy.plugins.capabilities import STATE_MESSAGES
from termapy.serial_port import eol_label

if TYPE_CHECKING:
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
        if reason is not None and reason != "ambiguous":
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
    """

    _HISTORY_LIMIT: int = 30
    _help_server_port: int = 0

    # -- Attributes set by subclass __init__ ----------------------------------

    cfg: dict
    config_path: str
    engine: SerialEngine
    repl: ReplEngine
    capture: CaptureEngine
    ctx: PluginContext
    prefix: str

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
            )
        except KeyboardInterrupt:
            self.status("Script interrupted", "red")

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
        actual = getattr(self.engine.port_obj, "port", spec) or spec
        if spec != actual:
            self.write(_format_resolved_line(spec, actual), "green")

        conn = connection_string(self.cfg, actual_port=actual)
        hw = hardware_signals(self.engine.port_obj)
        full = f"Connected: {conn}  {hw}" if hw else f"Connected: {conn}"
        # In --exec mode the output is for piping; the connect banner
        # would corrupt captured stdout.  Run mode keeps it -- the
        # gold test asserts on it, and human script output benefits
        # from the confirmation.
        if not getattr(self, "exec_cmd", None):
            self.write(full, "green")
        self.repl.fire_lifecycle("on_connect")
        self._start_reader()
        self._on_connected(full)

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
        actual = getattr(self.engine.port_obj, "port", "") or self.cfg["serial"]["port"]
        self.repl.fire_lifecycle("on_disconnect")
        self.engine.disconnect()
        self._clear_device_state()
        msg = f"Disconnected: {actual}" if actual else "Disconnected."
        self.write(msg, "red")
        self._on_disconnected()

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
        """
        self.ctx.ns("active_profile").clear()

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
            # set_proto_active intentionally not exposed -- the bare flag
            # setter is private; ``ctx.serial.io()`` is the public path.
            # TUI override installs a real modal launcher.  In non-TUI
            # environments the capability gate (tui_mode) fails /proto.debug
            # dispatch before this is called, so the no-op default is fine.
            open_proto_debug=lambda path, script: None,
            start_capture=lambda **kw: self._start_capture(**kw),
            stop_capture=lambda: self._stop_capture(),
            load_config=lambda name: self._load_config(name),
            xfer_cancel=getattr(self, "_xfer_cancel", None),
            script_stop_event=self.repl._script_stop,
            # /run's built-in handler reaches script-running via these
            # two callbacks.  ``start_script`` is host-agnostic;
            # ``run_script`` differs by host -- CLI / MCP inherit the
            # synchronous TerminalHost._run_script, TUI overrides with
            # @work(thread=True).
            start_script=lambda args: self.repl.start_script(args),
            run_script=lambda path, profile=False, verbose=False: self._run_script(
                path, profile=profile, verbose=verbose
            ),
            # Which file this frontend's session log is: one owner, the
            # host's session_log_path (registry session-log-one-owner).
            session_log_path=lambda: self.session_log_path(),
            close_session_log=lambda: self._close_session_log(),
            # How this frontend opens a file for editing (/edit decides which).
            edit_file=lambda path: self._edit_file(path),
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
            send=self._serial_send,
            read_raw=self._serial_read_raw,
            drain=self._drain_rx_queue,
            wait_idle=lambda timeout_ms=100, max_wait_s=3.0: self._wait_for_idle(
                timeout_ms, max_wait_s
            ),
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
        color: bool = True,
    ) -> None:
        """Initialize the ``flags`` namespace with shared engine defaults.

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
        """
        from termapy.plugins import DEFAULT_OUTPUT_LEVEL

        flags = self.ctx.ns("flags")
        flags.setdefault(
            "echo",
            self.cfg.get("echo", False) if device_echo_allowed else False,
        )
        flags.setdefault("echo_repl", echo_repl)
        flags.setdefault("output_level", DEFAULT_OUTPUT_LEVEL)
        flags.setdefault("hex", self.cfg.get("hex", False))
        # Line numbers: the same pattern.  Only the TUI renders them, but
        # the flag is what /term.line_no sets and /term.info reports.
        flags.setdefault("line_no", self.cfg.get("line_no", False))
        # Render device ANSI color (SGR).  Session flag toggled by
        # /term.color; the single source of truth for device color across
        # the TUI (strip before render when off) and CLI (strip in on_lines).
        flags.setdefault("color", color)

    # -- Serial I/O -----------------------------------------------------------

    def _serial_read_raw(self, timeout_ms: int = 1000, frame_gap_ms: int = 0) -> bytes:
        """Collect raw bytes using timeout-based framing (delegates to SerialPort)."""
        frame_gap = frame_gap_ms or self.cfg.get("proto_frame_gap_ms", 50)
        if self.engine.serial_port:
            return self.engine.serial_port.read_raw(timeout_ms, frame_gap)
        return b""

    def _feed_rx_consumers(self, lines: list[str], plain: list[str]) -> None:
        """Hand received lines to the consumers every frontend shares.

        The expect watcher and text capture.  Each frontend calls this for
        EVERY line it receives, beside its own display and session log
        (registry ``rx-every-line-delivered``).  MCP once skipped the capture
        half, so ``/cap.text`` over MCP recorded nothing, which is why the
        list lives here and not in three copies.

        Args:
            lines: Received lines as the reader produced them.
            plain: The same lines with ANSI stripped, for the capture file.
        """
        self.repl.feed_lines(lines)
        if self.capture.active and self.capture.mode == "text":
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

    def _serial_op(self, label: str, fn) -> None:
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

    def _serial_send(self, text: str) -> None:
        """Send text with configured line ending and encoding."""
        ending = self.cfg.get("eol", "\r")
        encoding = self.cfg.get("encoding", "utf-8")
        self._serial_write((text + ending).encode(encoding))

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
            eol_label=eol_label,
            report=report,
        )

    # -- Port effects ---------------------------------------------------------

    def _apply_port_effects(self, effects: dict) -> None:
        """Apply port_control side effects (cfg_update).

        Subclasses may override to add UI updates (title, hw buttons).

        cfg_update keys arrive flat (e.g. ``"baud_rate"``) because the
        port_control producers don't need to know the cfg shape.  Route
        each key to ``cfg["serial"]`` if it currently lives there;
        otherwise set it at the top level.  Keeps producers oblivious
        to schema changes like the v22 serial nesting.
        """
        if effects.get("cfg_update"):
            cfg = self.repl._cfg_data
            serial = cfg.get("serial", {})
            for key, val in effects["cfg_update"].items():
                if key in serial:
                    serial[key] = val
                else:
                    cfg[key] = val

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
            actual = getattr(self.engine.port_obj, "port", port) or port
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
        path = resolve_config(name)
        if path is None:
            return CmdResult.fail(
                msg=f"No config matching {name!r}. "
                f"Try {self.repl.prefix}cfg.list to list available."
            )
        result = self._switch_to_cfg_path(path)
        if result.success:
            self.status(f"Loaded config: {Path(path).stem}", "green")
        return result

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
        self._setup_context()
        self.repl.fire_lifecycle("on_config_load")
        # auto_connect lives in the newly-loaded cfg -- if the user's
        # saved config wants the port opened on start, honor it here.
        # Use self._connect() (not engine.connect() inline) so the full
        # connect lifecycle fires: on_connect lifecycle hook, reader
        # thread start, AND ``_on_connected()``.  The latter is where
        # MCPHost runs auto-load-profile, auto-include, on_connect_cmd,
        # mcp_on_connect_cmd, and the banner watcher.  Inlining
        # ``engine.connect()`` here used to silently skip all of those.
        if cfg.get("auto_connect"):
            self._connect()
        return CmdResult.ok(value=config_path)

    # -- Capture helpers ------------------------------------------------------

    def _start_capture(self, **kwargs) -> bool:
        """Start a capture session.

        Subclasses may override to add UI (timer, progress).
        """
        if self.capture.active:
            self.status("Capture already active - use /cap.stop")
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
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=html_dir, **kw)

            def log_message(self, format, *args):
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

    def _hook_help_open(self, ctx, args: str) -> CmdResult:
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

    # -- Session log ------------------------------------------------------------

    def session_log_path(self) -> Path | None:
        """The file this frontend writes its session log to, or None when it keeps none.

        The one answer every ``/log.*`` command, ``$(CFG.LOG_FILE)`` and
        ``/cfg.info`` read (review 2026-10-15, D3).  The default is the
        CLI's: no session log.  The TUI and the MCP host override it.
        """
        return None

    def _close_session_log(self) -> None:
        """Let go of the session log file before ``/log.delete`` removes it.

        A host that keeps the file open (the TUI) closes it here; the
        default holds nothing open.
        """
