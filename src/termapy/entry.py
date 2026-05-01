"""Termapy CLI entry point.

This module builds the argparse parser and dispatches to the right
runner.  Print-and-exit flags (``--info``, ``--ports``, ``--watch``,
``--chips``, ``--check``, ``--version``) dispatch to
``termapy.cli_flags`` and never touch the TUI stack.

**Architectural constraint:** importing ``termapy.entry`` must not
trigger import of Textual, Rich, prompt-toolkit, or any other UI
framework.  Users who run ``termapy --ports`` shouldn't pay the ~300ms
+ ~40MB load cost of Textual for a command that only calls pyserial.

The TUI-mode runner (``_run_tui_mode`` in ``app.py``) and the CLI-mode
runner (``_run_cli_mode`` in ``app.py``) are imported **lazily**,
inside the dispatch branch that actually needs them.  ``app.py``'s
module-level ``from textual import ...`` only runs when the user has
actually asked for interactive mode.
"""

from __future__ import annotations

import argparse
import sys
from importlib.metadata import version as _get_version
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser.  No side effects; pure construction."""
    from importlib.metadata import PackageNotFoundError
    try:
        _version = _get_version("termapy")
    except PackageNotFoundError:
        # Running from a git clone without `pip install .` -- version
        # metadata isn't available.  Anything else is a real bug.
        _version = "unknown"

    parser = argparse.ArgumentParser(
        description="TUI serial terminal with ANSI color support"
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"termapy {_version}",
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
        help="Config directory (default: ./termapy_cfg if present, else OS config dir)",
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
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Run in CLI mode (plain text terminal, no TUI)",
    )
    parser.add_argument(
        "--run",
        default=None,
        metavar="SCRIPT",
        help="Run a .run script and exit (CLI mode, implies --cli)",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="Serve the TUI in a web browser via textual-serve",
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=8000,
        help="Port for web server (default: 8000)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Strip ANSI color codes from output (CLI mode)",
    )
    output_level = parser.add_mutually_exclusive_group()
    output_level.add_argument(
        "--silent",
        dest="output_level",
        action="store_const",
        const="silent",
        help="Output level: nothing visible (use exit code).",
    )
    output_level.add_argument(
        "--quiet",
        dest="output_level",
        action="store_const",
        const="quiet",
        help="Output level: command results only.",
    )
    output_level.add_argument(
        "--verbose",
        dest="output_level",
        action="store_const",
        const="verbose",
        help="Output level: results, data, and progress chatter.",
    )
    parser.add_argument(
        "--term-width",
        type=int,
        default=None,
        help="Override terminal width for CLI mode (default: auto-detect)",
    )
    parser.add_argument(
        "--info",
        nargs="?",
        const="*",
        default=None,
        metavar="PORT",
        help="Show serial port chip info and exit (verbose per-port dump). "
             "Optional PORT name (e.g. COM3, /dev/ttyUSB0); "
             "omit for all connected ports.",
    )
    parser.add_argument(
        "--ports",
        nargs="?",
        const="*",
        default=None,
        metavar="PORT",
        help="List serial ports one line per port and exit.  "
             "Optional PORT name filters to a single device.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Monitor serial ports and print changes "
             "(presence, in-use, serial number) as events.  Ctrl+C to exit.",
    )
    parser.add_argument(
        "--chips",
        nargs="?",
        const="*",
        default=None,
        metavar="FILTER",
        help="Dump the USB-serial chip lookup table and exit.  "
             "Optional FILTER substring narrows to matching chip models.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a column-aligned table.  "
             "Applies to --ports and --chips.",
    )
    parser.add_argument(
        "--vid",
        default=None,
        help="Filter --ports to USB devices matching this VID "
             "(hex, with or without 0x).",
    )
    parser.add_argument(
        "--pid",
        default=None,
        help="Filter --ports to USB devices matching this PID "
             "(hex, with or without 0x).",
    )
    parser.add_argument(
        "--mfg",
        default=None,
        help="Filter --ports to devices whose manufacturer string contains "
             "this substring (case-insensitive).",
    )
    parser.add_argument(
        "--sn",
        default=None,
        help="Filter --ports to the device with this exact serial number "
             "(case-insensitive).",
    )
    return parser


def main() -> None:
    """Argparse + dispatch.  Print-and-exit flags run Textual-free."""
    parser = _build_parser()
    args = parser.parse_args()

    # CLI-only flags: dispatch before anything touches Textual.  Each
    # of these calls sys.exit() internally, so control never returns.
    if args.info is not None:
        from termapy.cli_flags import run_info
        run_info(args)
    if args.ports is not None:
        from termapy.cli_flags import run_ports
        run_ports(args)
    if args.watch:
        from termapy.cli_flags import run_watch
        run_watch(args)
    if args.chips is not None:
        from termapy.cli_flags import run_chips
        run_chips(args)

    # --cfg-dir writes a module-global; do this before anything else
    # that might resolve configs.
    if args.cfg_dir:
        import termapy.config as _cfg_mod
        _cfg_mod.CFG_DIR = args.cfg_dir

    # Normalize positional arg by extension - infer intent.  This
    # matters for --run and --proto; done here so both TUI and CLI
    # see the same shape.
    if args.config:
        ext = Path(args.config).suffix.lower()
        if ext == ".run" and not args.run:
            args.run = args.config
            args.config = None
            if not args.cli:
                # TUI mode: infer config, don't auto-run.  Lives in the
                # Textual-free config_resolve module.
                from termapy.config_resolve import infer_config_from_run_file
                inferred = infer_config_from_run_file(args.run)
                if inferred:
                    args.config = inferred
                    args.run = None
                else:
                    print(
                        f"termapy: cannot infer config from {Path(args.run).resolve()}",
                        file=sys.stderr,
                    )
                    sys.exit(1)
        elif ext == ".pro" and not args.proto:
            from termapy.config_resolve import infer_config_from_run_file
            inferred = infer_config_from_run_file(args.config)
            if inferred:
                args.proto = args.config
                args.config = inferred
            else:
                print(
                    f"termapy: cannot infer config from {Path(args.config).resolve()}",
                    file=sys.stderr,
                )
                sys.exit(1)

    # --check is TUI-free: dispatch to cli_flags.
    if args.check:
        from termapy.cli_flags import run_check
        run_check(args)
        return

    # --proto runs the headless protocol runner (no TUI, but needs
    # config + plugin loading).  Stays in app.py for now.
    if args.proto is not None:
        from termapy.app import _run_proto_headless
        _run_proto_headless(args)
        return

    # --web serves the TUI in a browser; spawns a subprocess so the
    # server module loads, not Textual in this process.
    if args.web:
        from termapy.app import _run_web_mode
        _run_web_mode(args)
        return

    if args.run:
        args.cli = True  # --run implies --cli

    # Mode switching loop -- CLI or TUI.  Both paths pull from
    # termapy.app which imports Textual; this is where Textual
    # finally loads in the import graph.
    from termapy.app import _run_cli_mode, _run_tui_mode
    from termapy.config import load_config
    from termapy.config_resolve import find_config, resolve_config

    # CLI flag from command line overrides config.  Otherwise check
    # default_ui.
    if args.cli:
        mode = "cli"
    else:
        _peek_cfg = None
        # Best-effort peek at the config to choose initial UI mode.
        # Any failure falls through to the default TUI -- the user will
        # see the real error (and full traceback) when the actual TUI
        # load tries the same config right after this.
        try:
            if args.demo:
                pass  # demo defaults to tui
            elif args.config:
                _peek_path = resolve_config(args.config)
                if _peek_path:
                    _peek_cfg = load_config(_peek_path)
            else:
                _peek_path, _ = find_config()
                if _peek_path:
                    _peek_cfg = load_config(_peek_path)
        except (OSError, ValueError):
            # OSError: config file missing / permission denied.
            # ValueError: json.JSONDecodeError is a ValueError subclass.
            pass
        mode = (_peek_cfg or {}).get("default_ui", "tui")
        if mode not in ("cli", "tui"):
            mode = "tui"

    while mode:
        if mode == "cli":
            result = _run_cli_mode(args)
        elif mode == "tui":
            result = _run_tui_mode(args)
        else:
            break
        if result is None:
            break
        mode = result
        args.cli = mode == "cli"
        args.run = None  # don't re-run a script on switch
        args.demo = False  # don't re-setup demo on switch
