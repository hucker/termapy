"""--vt100 passthrough mode: a raw, Textual-free serial terminal on the engine.

This is a peer of CLI and TUI mode, not a separate runtime. When the
output surface is *already* a terminal, the right move is to hand it the
raw device stream and let it do the VT100/ANSI emulation -- so cursor-
addressed menus, vi, top, bootloader UIs, etc. render correctly. We do
*not* run an emulator here (that's Phase 2, for non-terminal surfaces
like MCP/snapshots).

The port is ``SerialEngine``'s, as in every frontend (registry
``vt100-on-engine``): its reader hands each raw chunk to the console
through an RX observer, byte for byte, and its line path feeds the session
log, the same ``<cfg>.log`` the TUI writes.  A key goes to the device
through ``SerialPort.write``.  The vendored pyserial miniterm is kept for
the console only: raw single-key input, VT processing on Windows 10+, and
the writer thread that pumps keys to the port; its own reader never starts
(``_KeyPump``).

What the log holds: received lines, ANSI stripped, under ``<``, and ``#``
for the session's own events; no ``>`` lines, because in passthrough
nothing on this side holds a line: each key goes as typed, and the
device's echo is the record of what it accepted (decision 2026-11-09).

No Textual import: dropping the TUI layer is the whole point (it avoids a
third key-interception layer; see docs/vt100-build-plan.md sections 3/6).
"""

from __future__ import annotations

import codecs
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from termapy import diag_log
from termapy.config import cap_folder_of, open_serial, session_log_file
from termapy.defaults import cfg_value
from termapy.scripting import strip_ansi
from termapy.session_log import STATUS_MARKER, SessionLog
from termapy.vendor.serial.tools.miniterm import Miniterm, Transform

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable


def _miniterm_settings(cfg: dict) -> dict:
    """Map a config dict to the settings the passthrough needs.

    Pure (no I/O), so the cfg -> settings mapping is unit-testable without
    constructing a ``Miniterm`` (which would grab the real console).

    Args:
        cfg: Loaded config dict.

    Returns:
        ``{"echo": bool, "eol": str, "encoding": str}``.
    """
    return {
        "echo": cfg_value(cfg, "echo"),
        "eol": cfg_value(cfg, "eol"),
        "encoding": cfg_value(cfg, "encoding"),
    }


def _vscode_key_hint(cfg: dict) -> str | None:
    """One-line tip when --vt100 runs in a VS Code terminal, else None.

    VS Code's integrated terminal can intercept some keys (function keys,
    certain Ctrl/Shift chords) before they reach the device. We surface the
    one-setting fix rather than read VS Code's own settings to auto-suppress
    -- that's layered JSONC across OS-specific paths (and remote/WSL), so the
    ``vt100_hint`` cfg opt-out is the robust lever instead.

    Args:
        cfg: Loaded config dict (reads ``vt100_hint``, default True).

    Returns:
        The tip string (newline-terminated), or None when disabled via
        ``vt100_hint`` or not running under VS Code.
    """
    if not cfg_value(cfg, "vt100_hint"):
        return None
    if os.environ.get("TERM_PROGRAM") != "vscode":
        return None
    return (
        "note: in the VS Code terminal some keys can be captured by VS Code "
        "before the device.  If a key never arrives, set\n"
        '      "terminal.integrated.sendKeybindingsToShell": true  '
        "(or vt100_hint=false to hide this).\n"
    )


def _tx_transforms(line_ending: str) -> list[Transform]:
    """Build the miniterm ``tx`` transform list for raw VT100 passthrough.

    Only Enter (``\\n``) is translated, to the cfg ``line_ending``; every
    other typed byte (letters, arrow-key escape sequences) is left as-is.
    miniterm's default EOL transform would rewrite every ``\\r``/``\\n``, so
    it is replaced.  Received bytes take no transform at all: the engine's
    RX observer hands them to the console as decoded (``PassthroughSession``).

    Returns:
        The ``tx_transformations`` to assign onto a ``Miniterm`` instance.
    """

    class _EnterEOL(Transform):
        def tx(self, text: str) -> str:
            return text.replace("\n", line_ending)

    return [_EnterEOL()]


class PassthroughSession:
    """The engine side of ``--vt100``: one port, raw bytes to the console, lines to the session log.

    Built on a ``SerialEngine`` so passthrough reads and writes as every
    frontend does.  ``start`` opens the log and the engine's reader;
    ``write`` is what the key pump calls for each key (the session is the
    pump's ``serial``); ``close`` joins the reader, closes the port and ends
    the log.  Writes to the log come from the reader thread, so the log's
    own lock orders them.

    Args:
        cfg: Loaded config dict.
        config_path: The config's path, or None for an in-memory config
            (``--demo``), which keeps no log.
        open_fn: Opens the port from the cfg; the default is
            ``config.open_serial`` with the config's ``cap/`` folder for a
            ``spy://`` port.  A test hands in its own.
    """

    def __init__(
        self,
        cfg: dict,
        config_path: str | None,
        open_fn: Callable[[dict], Any] | None = None,
    ) -> None:
        from termapy.capture import CaptureEngine
        from termapy.serial_engine import SerialEngine

        self.cfg = cfg
        self.config_path = config_path
        cap_folder = cap_folder_of(config_path or "")
        self.engine = SerialEngine(
            cfg=cfg,
            capture=CaptureEngine(),
            open_fn=open_fn or (lambda c: open_serial(c, cap_folder=cap_folder)),
        )
        self.session_log = SessionLog()
        self._decoder = codecs.getincrementaldecoder(cfg_value(cfg, "encoding"))("replace")
        self._console_write: Callable[[str], None] | None = None
        self._on_lost: Callable[[str], None] | None = None
        self._lost_reported = False

    def session_log_path(self) -> Path | None:
        """The file this frontend writes: the TUI's, ``log_file`` or ``<cfg>.log`` (registry ``session-log-one-owner``)."""
        return session_log_file(self.cfg, self.config_path)

    def connect(self) -> bool:
        """Open the port; False leaves the reason in ``engine.last_error``."""
        return self.engine.connect()

    def start(self, console_write: Callable[[str], None], on_lost: Callable[[str], None]) -> None:
        """Open the session log and start the engine's reader.

        Args:
            console_write: Gets every received chunk as text, on the reader
                thread; the console's ``write``.
            on_lost: Called once, on the reader thread, when the port is
                lost or a write fails, with the reason; the caller stops
                the key pump.
        """
        self._console_write = console_write
        self._on_lost = on_lost
        path = self.session_log_path()
        if path is not None:
            try:
                self.session_log.open(path)
            except OSError as e:
                # The transcript never stops the session; the person is told once.
                diag_log.warning("session log not opened: %s", e)
                sys.stderr.write(f"termapy: session log not opened: {e}\n")
        self.session_log.write(STATUS_MARKER, [f"VT100 passthrough: {self.engine.open_device}"])
        self.engine.add_rx_observer(self._on_rx)
        self.engine.start_reader(
            on_lines=self._on_lines,
            on_error=lambda detail: self._lost(f"Serial error: {detail}"),
            on_disconnect=lambda: self._lost("Serial disconnected"),
        )

    def write(self, data: bytes) -> None:
        """The key pump's ``serial.write``: each key to the device through the engine's port."""
        import serial as _serial

        port = self.engine.serial_port
        if port is None:
            return
        try:
            port.write(data)
        except (OSError, _serial.SerialException) as e:
            self._lost(f"Serial write failed: {e}")

    def close(self) -> None:
        """Stop the reader, close the port and end the log, in that order, so every line lands first."""
        self.engine.remove_rx_observer(self._on_rx)
        self.engine.disconnect()
        self.session_log.end()

    def _on_rx(self, data: bytes) -> None:
        """Reader thread: every raw chunk to the console as decoded text, nothing else.

        The incremental decoder carries a multi-byte character split across
        chunks, and ``replace`` keeps a bad byte from ever stopping the
        stream.
        """
        text = self._decoder.decode(data)
        if text and self._console_write is not None:
            self._console_write(text)

    def _on_lines(self, lines: list[str]) -> None:
        """Reader thread: the engine's lines into the log under ``<``, ANSI stripped as the TUI logs them."""
        self.session_log.write("<", [strip_ansi(line) for line in lines])

    def _lost(self, detail: str) -> None:
        """Record a lost port once and tell the caller; a read failure reports two ways and a write may race it."""
        if self._lost_reported:
            return
        self._lost_reported = True
        self.session_log.write(STATUS_MARKER, [detail])
        if self._on_lost is not None:
            self._on_lost(detail)


class _KeyPump(Miniterm):
    """miniterm as console and key pump only: the engine's reader reads the port, so its own never starts.

    Everything else is miniterm's: the OS console in raw single-key mode,
    the writer thread that pumps each key to ``serial.write`` (the
    ``PassthroughSession``), the exit key.  Only ``join(True)``, the
    writer's, is ever called; there is no receiver thread to join.
    """

    def _start_reader(self) -> None:
        """No reader of its own: ``SerialEngine`` owns the one reader (registry ``reader-owned-by-engine``)."""


def _resolve_cfg(args: argparse.Namespace) -> dict:
    """Resolve the config for vt100 mode (mirrors the cli.py resolution).

    demo -> in-memory cfg on the interactive ``DEMO_VT100`` device;
    positional -> resolve; else -> find in ``termapy_cfg/``. Unlike CLI
    there is no zero-config REPL fallback: passthrough needs a real port,
    so "no config" is a hard error.

    Args:
        args: Parsed argparse namespace (uses ``demo``/``config``).

    Returns:
        The loaded config dict.

    Exits the process with a message on any resolution/load failure.
    """
    from termapy.config import CONFIG_LOAD_ERRORS, load_config
    from termapy.config_resolve import find_config, resolve_config

    # --demo (launch flag) or /demo.vt100 (TUI switch) both select the VT100
    # widget-tour device: a cursor-addressed menu/dashboard rather than the
    # line-oriented AT device that --demo picks in tui/cli mode. In-memory
    # cfg, no disk write; the caller's args.config is left untouched so a
    # /demo.vt100 round-trip restores the original device in the TUI.
    if args.demo or getattr(args, "_vt100_demo", False):
        from termapy.defaults import default_cfg

        cfg = default_cfg()
        cfg["serial"]["port"] = "DEMO_VT100"
        cfg["title"] = "VT100 demo"
        return cfg

    if args.config:
        config_path = resolve_config(args.config)
        if config_path is None:
            print(
                f"termapy: config not found: {Path(args.config).resolve()}",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        path, _ = find_config()
        if not path:
            print(
                "termapy: no config found. Pass a .cfg path, use --demo, "
                "or run from a folder with termapy_cfg/.",
                file=sys.stderr,
            )
            sys.exit(1)
        config_path = path

    try:
        cfg = load_config(config_path)
    except CONFIG_LOAD_ERRORS as e:
        print(f"termapy: failed to load config: {e}", file=sys.stderr)
        sys.exit(1)
    # The path rides along, as the headless proto runner's does, so the
    # breadcrumb and the session log can name the config.
    cfg["_config_path"] = str(config_path)
    return cfg


def run_vt100_mode(args: argparse.Namespace) -> str | None:
    """Run --vt100 passthrough: raw serial <-> host terminal, the engine reading, miniterm typing.

    Opens the port through a ``PassthroughSession`` (``SerialEngine``) and
    hands the session to the ``_KeyPump``, which copies console->device
    until the user hits the exit key; the engine's reader copies
    device->console and logs the lines.

    Args:
        args: Parsed argparse namespace.

    Returns:
        The mode to switch to on exit: ``"tui"`` when entered from the TUI
        (``/vt100`` / ``/demo.vt100``, signaled by ``args._vt100_return_to``)
        so Ctrl-] is a reversible toggle, else ``None`` to quit the process
        (the ``--vt100`` launch case). Matches ``_run_cli_mode`` /
        ``_run_tui_mode`` so entry.py's mode loop can switch on the value.
    """
    return_to = getattr(args, "_vt100_return_to", None)
    cfg = _resolve_cfg(args)
    settings = _miniterm_settings(cfg)
    config_path = cfg.get("_config_path") or None

    # vt100 is not a TerminalHost, so its session is announced here.
    diag_log.start("vt100", config_path or "")
    session = PassthroughSession(cfg, config_path)
    if not session.connect():
        print(f"termapy: cannot open serial port: {session.engine.last_error}", file=sys.stderr)
        sys.exit(1)
    port = session.engine.port_obj
    from termapy import breadcrumbs
    from termapy.config import port_settings
    from termapy.port_control import serial_number

    breadcrumbs.leave(
        "vt100",
        config=config_path or "",
        port=str(cfg_value(cfg, "serial.port") or ""),
        open_port=session.engine.open_device,
        settings=port_settings(cfg),
        port_serial=serial_number(session.engine.open_device),
        log=str(session.session_log_path() or ""),
        detailed=True,
    )

    # miniterm's Windows Console swaps sys.stdout/sys.stderr for UTF-8 wrappers
    # and never restores them (its own main() exits the process, so it never
    # had to). We reuse the process across mode switches, so a second /vt100
    # entry would call Console() with the leftover wrapper -- which has no
    # .buffer -- and crash. Save and restore them around the session. On POSIX
    # this is a harmless no-op (that Console doesn't touch sys.stdout).
    saved_stdout, saved_stderr = sys.stdout, sys.stderr
    pump: _KeyPump | None = None
    try:
        pump = key_pump = _KeyPump(session, echo=settings["echo"])
        # Miniterm.__init__ leaves tx_encoder UNSET; writer() AttributeErrors
        # on the first key unless it is set here (miniterm's own main() does
        # the same before start()).  The rx side is the engine's.
        key_pump.set_tx_encoding(settings["encoding"])
        # Only Enter is translated, to the cfg line ending; every other key
        # goes out as typed.  Replaces miniterm's EOL transform.
        key_pump.tx_transformations = _tx_transforms(settings["eol"])
        key_pump.exit_character = chr(0x1D)  # Ctrl-]  -> exit / back to the TUI
        # Disable miniterm's Ctrl-T settings menu so nothing pyserial-flavored
        # leaks: set the menu key to a value the user won't type, so Ctrl-T
        # (and every other key) passes straight through to the device. Keeps
        # the passthrough reading as a native termapy view.
        key_pump.menu_character = chr(0x00)

        # Clean, termapy-branded banner on stderr (stdout is the device
        # stream); no mention of miniterm or its menu.
        exit_hint = "Ctrl+] returns to termapy" if return_to == "tui" else "Ctrl+] to exit"
        sys.stderr.write(
            f"termapy VT100  |  {getattr(port, 'name', '')} {getattr(port, 'baudrate', '')} "
            f"{getattr(port, 'bytesize', '')}{getattr(port, 'parity', '')}"
            f"{getattr(port, 'stopbits', '')}  |  {exit_hint}\n"
        )
        hint = _vscode_key_hint(cfg)
        if hint:
            sys.stderr.write(hint)

        def on_lost(detail: str) -> None:
            # Reader thread.  Stop the writer and wake its blocking getkey(),
            # as miniterm's own reader does when the port goes.
            sys.stderr.write(f"\n{detail}\n")
            key_pump.stop()
            key_pump.console.cancel()

        session.start(console_write=key_pump.console.write, on_lost=on_lost)
        key_pump.start()
        try:
            key_pump.join(True)  # wait on the transmitter until Ctrl-] or a lost port
        except KeyboardInterrupt:
            pass
        sys.stderr.write("\nreturning to termapy...\n" if return_to == "tui" else "\nexit\n")
    finally:
        # The reader first, so no chunk reaches a console being restored.
        session.close()
        if pump is not None:
            try:
                pump.console.cleanup()  # restore termios on POSIX (no-op on Win)
            except Exception:
                # Teardown is best-effort; never crash on console restore.
                diag_log.exception("vt100 console restore raised")
        sys.stdout, sys.stderr = saved_stdout, saved_stderr
    return return_to
