"""Base class for serial terminal frontends.

Owns the shared serial-I/O, dispatch, capture, help-server, and
hook-handler logic that both the Textual TUI (``app.py``) and the
headless CLI (``cli.py``) need.  Subclasses provide output, threading,
and UI-specific hooks.

No Textual or Rich imports — those stay in the subclasses.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.config import open_with_system
from termapy.plugins import CmdResult, EngineAPI, PluginContext
from termapy.serial_port import eol_label

if TYPE_CHECKING:
    from termapy.capture import CaptureEngine
    from termapy.repl import ReplEngine
    from termapy.serial_engine import SerialEngine


class TerminalHost:
    """Base class shared by TUI and CLI terminal frontends.

    Not an ABC (Textual's ``App`` metaclass conflicts with ``ABCMeta``).
    Subclasses must override the methods marked *override required* below
    and set the following attributes before calling any base methods:

    * ``cfg``          – config dict
    * ``config_path``  – path to the config file
    * ``engine``       – ``SerialEngine`` instance
    * ``repl``         – ``ReplEngine`` instance
    * ``capture``      – ``CaptureEngine`` instance
    * ``ctx``          – ``PluginContext`` (set by subclass ``_setup_context``)
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

    # -- Override required (subclass must implement) --------------------------

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

    def _connect(self, port: str | None = None) -> bool:
        """Connect to a serial port.

        Handles the shared logic: set port, call engine.connect(), build
        connection string, fire lifecycle, start reader.  Returns True on
        success.  Subclasses override ``_on_connected()`` and
        ``_on_connect_failed()`` for UI updates.
        """
        if self.engine.is_connected:
            self.status("Already connected", "yellow")
            return False
        if port:
            self.repl._cfg_data["port"] = port
        if not self.engine.connect():
            port_name = self.cfg.get("port", "?")
            detail = self.engine.last_error
            if detail:
                self.status(f"Cannot open {port_name}: {detail}", "red")
            else:
                self.status(f"Cannot open {port_name}", "red")
            self._on_connect_failed()
            return False
        from termapy.config import connection_string, hardware_signals

        conn = connection_string(self.cfg)
        hw = hardware_signals(self.engine.port_obj)
        full = f"Connected: {conn}  {hw}" if hw else f"Connected: {conn}"
        self.write(full, "green")
        self.repl.fire_lifecycle("on_connect")
        self._start_reader()
        self._on_connected(full)
        return True

    def _on_connected(self, message: str) -> None:
        """Called after a successful connection.  Override for UI updates."""

    def _on_connect_failed(self) -> None:
        """Called after a failed connection.  Override for UI updates."""

    def _disconnect(self) -> None:
        """Disconnect from the serial port.

        Handles the shared logic: fire lifecycle, engine.disconnect().
        Subclasses override ``_on_disconnected()`` for UI updates.
        """
        if not self.engine.is_connected:
            self.status("Not connected.", "yellow")
            return
        self.repl.fire_lifecycle("on_disconnect")
        self.engine.disconnect()
        self.write("Disconnected.", "red")
        self._on_disconnected()

    def _on_disconnected(self) -> None:
        """Called after disconnection.  Override for UI updates."""

    # -- Context builders (subclass extends, not replaces) --------------------

    def _build_engine_api(self) -> EngineAPI:
        """Build an EngineAPI with the callbacks shared by all frontends.

        Subclasses should call ``super()._build_engine_api()`` then set
        any frontend-specific fields on the returned object before passing
        it to ``_build_plugin_context()``.
        """
        from termapy.repl import ReplEngine

        return EngineAPI(
            prefix=self.cfg.get("cmd_prefix", "/"),
            plugins=self.repl._plugins,
            in_script=lambda: self.repl.in_script,
            script_stop=lambda: self.repl._script_stop.set(),
            apply_cfg=self.repl._apply_cfg,
            coerce_type=ReplEngine._coerce_type,
            set_proto_active=lambda active: setattr(
                self.engine, "proto_active", active
            ),
            open_proto_debug=lambda path, script: self.status(
                "Only available in /tui mode.", "yellow"
            ),
            start_capture=lambda **kw: self._start_capture(**kw),
            stop_capture=lambda: self._stop_capture(),
            connect=lambda port=None: self._connect(port),
            disconnect=lambda: self._disconnect(),
            update_port=lambda name: self._update_port(name),
            apply_port_effects=lambda effects: self._apply_port_effects(effects),
            rx_queue=self.engine.rx_queue,
            xfer_cancel=getattr(self, "_xfer_cancel", None),
            script_stop_event=self.repl._script_stop,
        )

    def _build_plugin_context(self, engine_api: EngineAPI) -> PluginContext:
        """Build a PluginContext with the callbacks shared by all frontends.

        Subclasses should call ``super()._build_plugin_context(api)`` then
        replace any frontend-specific fields before calling
        ``repl.set_context()``.
        """
        ctx = PluginContext(
            write=self.status,
            write_markup=self.write_markup,
            log=self._log,
            cfg=self.cfg,
            config_path=self.config_path,
            engine=engine_api,
            # Serial
            is_connected=lambda: self.engine.is_connected,
            serial_write=self._serial_write,
            serial_send=self._serial_send,
            serial_claim=lambda: setattr(self.engine, "proto_active", True),
            serial_release=lambda: setattr(self.engine, "proto_active", False),
            add_rx_observer=self.engine.add_rx_observer,
            remove_rx_observer=self.engine.remove_rx_observer,
            add_tx_observer=self.engine.add_tx_observer,
            remove_tx_observer=self.engine.remove_tx_observer,
            # Dispatch / confirmation
            dispatch=lambda cmd: self._dispatch(cmd),
            confirm=lambda msg: self._confirm(msg),
            # Filesystem directories
            ss_dir=self.repl.ss_dir,
            scripts_dir=self.repl.scripts_dir,
            proto_dir=self.repl.proto_dir,
            cap_dir=self.repl.cap_dir,
            prof_dir=self.repl.prof_dir,
            # Shared UI
            open_file=lambda path: open_with_system(str(path)),
        )
        # Serial port helpers — shared by TUI and CLI
        ctx.port = lambda: self.engine.port_obj if self.engine.is_connected else None
        ctx.serial_read_raw = self._serial_read_raw
        ctx.serial_drain = self._drain_rx_queue
        ctx.serial_wait_idle = lambda timeout_ms=100, max_wait_s=3.0: (
            self._wait_for_idle(timeout_ms, max_wait_s)
        )
        return ctx

    def _init_flags(self, echo: bool = True) -> None:
        """Initialise the ``flags`` namespace with shared engine defaults.

        Args:
            echo: Initial echo state (True for TUI, False for CLI).
        """
        flags = self.ctx.ns("flags")
        flags.setdefault("echo", echo)
        flags.setdefault("verbose", True)
        flags.setdefault("hex_mode", self.cfg.get("hex_mode", False))

    # -- Serial I/O -----------------------------------------------------------

    def _serial_read_raw(self, timeout_ms: int = 1000, frame_gap_ms: int = 0) -> bytes:
        """Collect raw bytes using timeout-based framing (delegates to SerialPort)."""
        frame_gap = frame_gap_ms or self.cfg.get("proto_frame_gap_ms", 50)
        if self.engine.serial_port:
            return self.engine.serial_port.read_raw(timeout_ms, frame_gap)
        return b""

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
        """Write raw bytes to the serial port and notify TX observers."""
        if self.engine.serial_port:
            self.engine.serial_port.write(data)
            self.engine.notify_tx(data)

    def _serial_send(self, text: str) -> None:
        """Send text with configured line ending and encoding."""
        ending = self.cfg.get("line_ending", "\r")
        encoding = self.cfg.get("encoding", "utf-8")
        self._serial_write((text + ending).encode(encoding))

    # -- Dispatch -------------------------------------------------------------

    def _dispatch(self, cmd: str) -> CmdResult:
        """Route a command through the full dispatch pipeline."""
        return self.repl.dispatch_full(
            cmd,
            log=self._log,
            echo_markup=self.write_markup,
            status=self.status,
            serial_write=self._serial_write,
            serial_write_raw=lambda text: self._serial_write_raw(text),
            is_connected=lambda: self.engine.is_connected,
            eol_label=eol_label,
        )

    def _serial_write_raw(self, text: str) -> None:
        """Send raw text to serial with line ending, no transforms.

        Subclasses may override to add echo handling (e.g. TUI).
        """
        if not self.engine.is_connected:
            self.status("Not connected.", "red")
            return
        line_ending = self.cfg.get("line_ending", "\r")
        encoding = self.cfg.get("encoding", "utf-8")
        if self.engine.serial_port:
            data = (text + line_ending).encode(encoding)
            self.engine.serial_port.write(data)
            self.engine.notify_tx(data)

    # -- Port effects ---------------------------------------------------------

    def _apply_port_effects(self, effects: dict) -> None:
        """Apply port_control side effects (cfg_update).

        Subclasses may override to add UI updates (title, hw buttons).
        """
        if effects.get("cfg_update"):
            for key, val in effects["cfg_update"].items():
                self.repl._cfg_data[key] = val

    # -- Port switching ---------------------------------------------------------

    def _update_port(self, port: str) -> None:
        """Change serial port for this session and reconnect.

        Does not write to disk -- keeps $(env.NAME) templates intact.
        """
        self.cfg["port"] = port
        if self.engine.is_connected:
            self._disconnect()
        self._connect()
        if self.engine.is_connected:
            self.status(f"Port changed to {port} (session)", "green")

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
            self.engine.proto_active = True
        self.status(f"Capture started: {path} ({mode})")
        return True

    def _stop_capture(self) -> None:
        """Stop a capture session.

        Subclasses may override to add UI (timer cleanup, progress).
        """
        result = self.capture.stop()
        if not self.repl.in_script:
            self.engine.proto_active = False
        if result:
            self.status(f"Capture complete: {result.path} ({result.size_label})")

    # -- Help server ----------------------------------------------------------

    def _ensure_help_server(self) -> int:
        """Start a local HTTP server for help docs if not already running.

        Returns the port number.  The server runs as a daemon thread and
        stops automatically when the process exits.
        """
        if self._help_server_port:
            return self._help_server_port
        from http.server import HTTPServer, SimpleHTTPRequestHandler
        from importlib.resources import files as pkg_files

        html_dir = str(Path(str(pkg_files("termapy").joinpath("html"))).resolve())

        class _QuietHandler(SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=html_dir, **kw)

            def log_message(self, format, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), _QuietHandler)
        self._help_server_port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        return self._help_server_port

    def _hook_help_open(self, ctx, args: str) -> CmdResult:
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
            return CmdResult.fail(msg=f"Unknown help topic: {topic}")
        port = self._ensure_help_server()
        import webbrowser

        webbrowser.open(f"http://127.0.0.1:{port}/{page}")
        return CmdResult.ok()

    # -- Shared hook handlers -------------------------------------------------

    def _hook_raw(self, ctx, args: str) -> CmdResult:
        """Send raw text to serial without transforms or line ending."""
        if not self.engine.is_connected:
            return CmdResult.fail(msg="Not connected.")
        if not args:
            return CmdResult.fail(msg="Usage: /raw <text>")
        if self.engine.serial_port:
            self.engine.serial_port.write(
                args.encode(self.cfg.get("encoding", "utf-8"))
            )
        return CmdResult.ok()

    def _hook_log_clear(self, ctx, args: str) -> CmdResult:
        """Delete the session log file."""
        from termapy.config import cfg_log_path

        log_path = cfg_log_path(self.config_path) if self.config_path else ""
        if not log_path or not Path(log_path).exists():
            self.status("No log file to delete.", "yellow")
            return CmdResult.fail(msg="No log file.")
        try:
            Path(log_path).unlink()
            self.status(f"Deleted {Path(log_path).name}", "green")
            return CmdResult.ok()
        except OSError as e:
            self.status(f"Delete failed: {e}", "red")
            return CmdResult.fail(msg=str(e))

    # -- Utilities ------------------------------------------------------------

    @staticmethod
    def _parse_run_flags(args: str) -> tuple[str, bool]:
        """Extract -v/--verbose from /run args.

        Returns:
            Tuple of (clean_args, verbose).
        """
        tokens = args.split()
        verbose = False
        clean: list[str] = []
        for tok in tokens:
            if tok in ("-v", "--verbose"):
                verbose = True
            else:
                clean.append(tok)
        return " ".join(clean), verbose
