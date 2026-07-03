"""--vt100 passthrough mode: a raw, Textual-free serial terminal.

This is a peer of CLI and TUI mode, not a separate runtime. When the
output surface is *already* a terminal, the right move is to hand it the
raw device stream and let it do the VT100/ANSI emulation -- so cursor-
addressed menus, vi, top, bootloader UIs, etc. render correctly. We do
*not* run an emulator here (that's Phase 2, for non-terminal surfaces
like MCP/snapshots).

The byte pump is the vendored pyserial miniterm, which already solves the
OS-specific raw-console hard parts (incl. enabling VT processing on
Windows 10+). We open the raw pyserial object via the shared ``open_serial``
path -- no termapy SerialReader; miniterm owns the read loop in passthrough.

No Textual import: dropping the TUI layer is the whole point (it avoids a
third key-interception layer; see docs/vt100-build-plan.md sections 3/6).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

def _miniterm_settings(cfg: dict) -> dict:
    """Map a config dict to the settings the passthrough needs.

    Pure (no I/O), so the cfg -> settings mapping is unit-testable without
    constructing a ``Miniterm`` (which would grab the real console).

    Args:
        cfg: Loaded config dict.

    Returns:
        ``{"echo": bool, "line_ending": str, "encoding": str}``.
    """
    return {
        "echo": cfg.get("echo_input", False),
        "line_ending": cfg.get("line_ending", "\r"),
        "encoding": cfg.get("encoding", "utf-8"),
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
    if not cfg.get("vt100_hint", True):
        return None
    if os.environ.get("TERM_PROGRAM") != "vscode":
        return None
    return (
        "note: in the VS Code terminal some keys can be captured by VS Code "
        "before the device.  If a key never arrives, set\n"
        '      "terminal.integrated.sendKeybindingsToShell": true  '
        "(or vt100_hint=false to hide this).\n"
    )


def _passthrough_transforms(line_ending: str):
    """Build miniterm ``(rx, tx)`` transform lists for raw VT100 passthrough.

    VT100 passthrough must be byte-transparent: the device's bytes have to
    reach the terminal *unchanged*, or cursor control and bare-``\\r`` redraws
    (progress bars, spinners) get corrupted. miniterm's default EOL transform
    rewrites ``\\r``/``\\n`` -- e.g. ``eol='cr'`` (what a cfg ``line_ending`` of
    ``"\\r"`` would select) turns every received ``\\r`` into ``\\n``, doubling
    ``\\r\\n`` lines and breaking in-place redraws. So we replace it:

    - **rx**: empty -- received bytes pass through verbatim.
    - **tx**: translate only Enter (``\\n``) to the cfg ``line_ending``; every
      other typed byte (letters, arrow-key escape sequences) is left as-is.

    Returns:
        ``(rx_transformations, tx_transformations)`` to assign onto a
        ``Miniterm`` instance (overriding its EOL-derived defaults).
    """
    from termapy.vendor.serial.tools.miniterm import Transform

    class _EnterEOL(Transform):
        def tx(self, text):
            return text.replace("\n", line_ending)

    return [], [_EnterEOL()]


def _resolve_cfg(args) -> dict:
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
        return load_config(config_path)
    except CONFIG_LOAD_ERRORS as e:
        print(f"termapy: failed to load config: {e}", file=sys.stderr)
        sys.exit(1)


def run_vt100_mode(args) -> str | None:
    """Run --vt100 passthrough: raw serial <-> host terminal via miniterm.

    Opens the raw pyserial object (no termapy SerialReader) and hands it to
    the vendored ``Miniterm`` pump, which copies device->console and
    console->device until the user hits the exit key.

    Args:
        args: Parsed argparse namespace.

    Returns:
        The mode to switch to on exit: ``"tui"`` when entered from the TUI
        (``/vt100`` / ``/demo.vt100``, signalled by ``args._vt100_return_to``)
        so Ctrl-] is a reversible toggle, else ``None`` to quit the process
        (the ``--vt100`` launch case). Matches ``_run_cli_mode`` /
        ``_run_tui_mode`` so entry.py's mode loop can switch on the value.
    """
    import serial as _serial

    from termapy.config import open_serial
    from termapy.vendor.serial.tools.miniterm import Miniterm

    return_to = getattr(args, "_vt100_return_to", None)
    cfg = _resolve_cfg(args)
    settings = _miniterm_settings(cfg)

    try:
        port = open_serial(cfg)
    except (_serial.SerialException, OSError, ValueError) as e:
        print(f"termapy: cannot open serial port: {e}", file=sys.stderr)
        sys.exit(1)

    # miniterm's Windows Console swaps sys.stdout/sys.stderr for UTF-8 wrappers
    # and never restores them (its own main() exits the process, so it never
    # had to). We reuse the process across mode switches, so a second /vt100
    # entry would call Console() with the leftover wrapper -- which has no
    # .buffer -- and crash. Save and restore them around the session. On POSIX
    # this is a harmless no-op (that Console doesn't touch sys.stdout).
    saved_stdout, saved_stderr = sys.stdout, sys.stderr
    term = None
    try:
        term = Miniterm(port, echo=settings["echo"])
        # Miniterm.__init__ leaves rx_decoder/tx_encoder UNSET; reader()/
        # writer() AttributeError on the first byte unless we set both here
        # (miniterm's own main() does the same before start()).
        term.set_rx_encoding(settings["encoding"])
        term.set_tx_encoding(settings["encoding"])
        # Raw byte-transparent passthrough: device bytes reach the terminal
        # verbatim (no CR/LF rewriting); only Enter is translated to the cfg
        # line ending. Replaces miniterm's EOL transform -- see
        # _passthrough_transforms.
        term.rx_transformations, term.tx_transformations = _passthrough_transforms(
            settings["line_ending"]
        )
        term.exit_character = chr(0x1d)   # Ctrl-]  -> exit / back to the TUI
        # Disable miniterm's Ctrl-T settings menu so nothing pyserial-flavoured
        # leaks: set the menu key to a value the user won't type, so Ctrl-T
        # (and every other key) passes straight through to the device. Keeps
        # the passthrough reading as a native termapy view.
        term.menu_character = chr(0x00)

        # Clean, termapy-branded banner on stderr (stdout is the device
        # stream); no mention of miniterm or its menu.
        exit_hint = (
            "Ctrl+] returns to termapy" if return_to == "tui" else "Ctrl+] to exit"
        )
        sys.stderr.write(
            f"termapy VT100  |  {port.name} {port.baudrate} "
            f"{port.bytesize}{port.parity}{port.stopbits}  |  {exit_hint}\n"
        )
        hint = _vscode_key_hint(cfg)
        if hint:
            sys.stderr.write(hint)

        term.start()
        try:
            term.join(True)        # wait on the transmitter until Ctrl-]
        except KeyboardInterrupt:
            pass
        term.join()                # then drain/stop the receiver
        sys.stderr.write(
            "\nreturning to termapy...\n" if return_to == "tui" else "\nexit\n"
        )
    finally:
        if term is not None:
            try:
                term.console.cleanup()  # restore termios on POSIX (no-op on Win)
            except Exception:
                # Teardown is best-effort; never crash on console restore.
                pass
            term.close()           # closes the serial port
        else:
            port.close()           # Miniterm never took ownership of the port
        sys.stdout, sys.stderr = saved_stdout, saved_stderr
    return return_to
