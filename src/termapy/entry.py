"""Termapy CLI entry point.

This module builds the argparse parser and dispatches to the right
runner.  Print-and-exit flags (``--info``, ``--ports``, ``--watch``,
``--chips``, ``--check``, ``--version``) dispatch to
``termapy.cli_flags`` and never touch the TUI stack.

**Architectural constraint:** importing ``termapy.entry`` must not
trigger import of Textual, Rich, prompt-toolkit, or any other UI
framework.  Users who run ``termapy --ports`` shouldn't pay the ~300ms
+ ~40MB load cost of Textual for a command that only calls pyserial.

The TUI-mode runner (``_run_tui_mode`` in ``app.py``) is imported
**lazily**, inside the TUI dispatch branch.  ``app.py``'s module-level
``from textual import ...`` only runs when the user has actually asked
for the TUI.  The CLI-mode runner (``_run_cli_mode`` in ``cli.py``)
is Textual-free and lives outside ``app.py`` so a CLI invocation
never pulls Textual into the import graph.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


class _Version(argparse.Action):
    """``--version``: looks the installed version up only when asked.

    ``importlib.metadata`` costs ~50 ms to import, and every invocation
    builds the parser, so an eager lookup charged ``--ports``, the CLI and
    the MCP server for a string only ``--version`` prints.
    """

    def __init__(
        self, option_strings: Sequence[str], dest: str = argparse.SUPPRESS, **kwargs: Any
    ) -> None:
        super().__init__(option_strings, dest, nargs=0, default=argparse.SUPPRESS, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        from importlib.metadata import PackageNotFoundError, version

        try:
            installed = version("termapy")
        except PackageNotFoundError:
            # Running from a git clone without `pip install .` -- version
            # metadata isn't available.  Anything else is a real bug.
            installed = "unknown"
        print(f"termapy {installed}")
        parser.exit()


class _OneCommand(argparse.Action):
    """``-e``: one command per invocation, never split.

    argparse's default keeps the last of a repeated option silently, so
    ``-e A -e B`` ran only B; a line break inside the value ran as one
    mangled command.  Both are refused: several commands go in a .run
    file, the one multi-command format.
    """

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        if getattr(namespace, self.dest) is not None:
            parser.error(
                f"{option_string} takes one command; put several in a .run file and use --run"
            )
        if len(str(values).splitlines()) > 1:
            parser.error(
                f"{option_string} takes one command, without line breaks; "
                f"put several in a .run file and use --run"
            )
        setattr(namespace, self.dest, values)


# Where --web serves when its address says nothing else: this machine only.
WEB_HOST_DEFAULT = "localhost"
WEB_PORT_DEFAULT = 8000


def _web_address(text: str) -> tuple[str, int]:
    """Read ``--web``'s ``[[HOST:]PORT]``: where the web server listens, as ``(host, port)``.

    Digits alone are a port on this machine (``9000``); anything else is a
    host, with ``:PORT`` after it or the default port (``192.168.1.50``,
    ``192.168.1.50:9000``).  An IPv6 host takes its port after brackets
    (``[fe80::1]:9000``).  Never a wildcard: textual-serve builds the
    page's links from the address it listens on, so ``0.0.0.0`` or ``::``
    would listen everywhere and hand the browser an address it cannot reach.
    """
    host, port_text = WEB_HOST_DEFAULT, ""
    if text.isdigit():
        port_text = text
    elif text.startswith("["):
        inside, _, after = text[1:].partition("]")
        host, port_text = inside, after.removeprefix(":")
    elif text.count(":") == 1:
        host, _, port_text = text.partition(":")
    else:
        host = text
    if not host:
        raise argparse.ArgumentTypeError(f"Invalid address: {text}")
    if host in ("0.0.0.0", "::"):
        raise argparse.ArgumentTypeError(
            f"{text} listens everywhere but gives the browser no address to come "
            "back to; give this machine's own address, e.g. 192.168.1.50"
        )
    if not port_text:
        return host, WEB_PORT_DEFAULT
    if not port_text.isdigit() or not 0 < int(port_text) < 65536:
        raise argparse.ArgumentTypeError(f"Invalid port: {port_text}")
    return host, int(port_text)


def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser.  No side effects; pure construction."""
    parser = argparse.ArgumentParser(
        # Without it argparse names the program after sys.argv[0], which
        # on Windows is the venv launcher: "python.exe ...\Scripts\termapy".
        prog="termapy",
        description="TUI serial terminal with ANSI color support",
        # No prefix abbreviation: without it, argparse silently expands
        # `--port COM3` to `--ports COM3` (a print-and-exit flag), so a
        # perfectly reasonable-looking invocation lists ports and exits 0
        # instead of doing anything with the command that followed.
        allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "exit codes (one-shot --exec / --run):\n"
            "  0    success -- the command or script ran and reported success\n"
            "  1    failure -- command failed, could not connect, or config error\n"
            "  2    usage error -- bad arguments (e.g. --run and --exec together)\n"
            "  130  interrupted with Ctrl+C"
        ),
    )
    parser.add_argument(
        "--version",
        action=_Version,
        help="show program's version number and exit",
    )
    parser.add_argument(
        "config",
        nargs="?",
        default=None,
        metavar="CONFIG|PORT",
        help="A config by name or path (auto-detects a single .cfg in termapy_cfg/), "
        "else a port: a device name (COM3, /dev/ttyUSB0), a USB serial number or "
        "a URL, opened with its own quick config in your user folder",
    )
    parser.add_argument(
        "settings",
        nargs="*",
        metavar="SETTING",
        help="After a port, /port.connect's settings: baud, frame (8N1), "
        "cr|lf|crlf, echo|noecho; saved with the port's quick config",
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
        "--new",
        nargs="+",
        default=None,
        # Two metavars render as "NAME PORT [SETTING ...]"; run_new
        # requires the port.
        metavar=("NAME PORT", "SETTING"),
        help="Create config NAME (title NAME) in ./termapy_cfg/NAME/ and "
        "start it connected.  The settings are /port.connect's: PORT, then "
        "baud, frame (8N1 or N81), cr|lf|crlf, echo|noecho; unset ones "
        "default to 115200 8N1.  An existing config asks before its .cfg "
        "is overwritten; its other files are kept.",
    )
    parser.add_argument(
        "--board",
        default=None,
        metavar="BOARD",
        help="Start the starter project for dev board BOARD (e.g. pico): its config "
        "in ./termapy_cfg/BOARD/, created on first use with the attached "
        "board's USB serial number as its port and reused after that.  "
        "/board.list names the boards.",
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
        help=(
            "Use plain-text mode instead of the TUI (interactive REPL by "
            "default; pair with --run or --exec for one-shot)"
        ),
    )
    parser.add_argument(
        "--vt100",
        action="store_true",
        help=(
            "Raw VT100/ANSI passthrough terminal (no TUI): pipe the device "
            "straight to your terminal so cursor-addressed menus, vi, top, "
            "etc. render. Quit with Ctrl+]."
        ),
    )
    parser.add_argument(
        "--run",
        default=None,
        metavar="SCRIPT",
        help="Run a .run script and exit (CLI mode, implies --cli)",
    )
    parser.add_argument(
        "-e",
        "--exec",
        action=_OneCommand,
        default=None,
        metavar="COMMAND",
        dest="exec_cmd",
        help=(
            "Run a single command and exit (CLI mode, implies --cli "
            "and --no-color).  One per invocation: several go in a "
            ".run file (--run)"
        ),
    )
    parser.add_argument(
        "--web",
        nargs="?",
        const=(WEB_HOST_DEFAULT, WEB_PORT_DEFAULT),
        default=None,
        type=_web_address,
        metavar="[HOST:]PORT",
        help=(
            "Serve the TUI in a web browser via textual-serve, at "
            f"http://{WEB_HOST_DEFAULT}:{WEB_PORT_DEFAULT}, this machine only, unless "
            "[HOST:]PORT says otherwise: --web 9000 for another port, --web "
            "192.168.1.50 (this machine's network address) to open it from another "
            "computer.  There is no login: anyone who can reach it can drive the port"
        ),
    )
    parser.add_argument(
        "--alive",
        action="store_true",
        help=(
            "List the termapys running on this machine: mode, config, port and its "
            "settings, version and what started each (/app.alive)"
        ),
    )
    parser.add_argument(
        "--kill",
        nargs="*",
        default=None,
        metavar="TARGET [timeout=DUR]",
        help=(
            "Stop the termapys a TARGET picks -- all, a PID, a port (COM3) or a "
            "config name -- after a yes (/app.kill), waiting timeout= (default 3s) "
            "for them to leave.  Bare, list them as --alive does"
        ),
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help=(
            "Answer yes to the command line's questions: --kill, --new's overwrite, "
            "and a CLI session's (--cli, -e, --run).  Not the TUI's dialogs, and "
            "never under --mcp"
        ),
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
        metavar="PATTERN",
        help="List serial ports one line per port and exit.  "
        "Optional PATTERN shows the ports any field of which contains it "
        "(FTDI, Microchip, a serial number, COM4); a wildcard makes a glob.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Monitor serial ports and print changes "
        "(presence, in-use, serial number) as events.  Ctrl+C to exit.",
    )
    parser.add_argument(
        "--usb",
        action="store_true",
        help="Show the whole USB tree -- every hub, device and "
        "interface -- with serial ports marked.  Windows and Linux.",
    )
    parser.add_argument(
        "--chips",
        nargs="?",
        const="*",
        default=None,
        metavar="PATTERN",
        help="Dump the USB-serial chip lookup table and exit.  "
        "Optional PATTERN shows the chips any column of which contains it "
        "(ftdi, 0403, high); a wildcard makes a glob.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a column-aligned table.  "
        "Applies to --ports, --chips, and --usb.",
    )
    parser.add_argument(
        "--validate-profile",
        default=None,
        metavar="PATH",
        help="Validate a device profile (.json or .toml) against the schema "
        "and exit.  Exit 0 if valid, 1 with errors otherwise.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="With --validate-profile: treat compatibility warnings "
        "(unknown fields, non-canonical values) as errors and exit 1. "
        "For CI and pre-commit hooks, where nobody reads warnings.",
    )
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="Run as a stdio MCP (Model Context Protocol) server, a preview "
        "that may change in any release.  Stdout "
        "is reserved for protocol frames; session log goes to "
        "<cfg_dir>/<name>.mcp.log.  Requires the 'mcp' extra: "
        "pip install termapy[mcp].",
    )
    parser.add_argument(
        "--mcp-verbose",
        action="store_true",
        help="Dev observability for --mcp: tee log events to stderr in real "
        "time.  Stderr is safe (only stdout is the MCP wire); production "
        "users omit this flag for clean stdio.",
    )
    return parser


def _run_validate_profile(path_str: str, strict: bool = False) -> None:
    """Validate a profile file against the schema and exit.

    Exits 0 on valid, 1 with line-numbered errors otherwise.  Lint
    warnings (unknown fields and non-canonical values, which degrade
    per the compatibility policy in docs/profile-spec.md) print to
    stderr; they affect the exit code only under ``--strict``, the
    CI/pre-commit mode where a misspelled field must stop the build
    instead of scrolling by.  Stays Textual-free -- the validator only
    needs ``profile.py`` and optionally ``jsonschema``.
    """
    from termapy.profile import load_profile, validate_profile

    p = Path(path_str)
    if not p.exists():
        print(f"termapy: profile not found: {p}", file=sys.stderr)
        sys.exit(1)
    try:
        profile = load_profile(p)
    except (OSError, ValueError) as e:
        print(f"termapy: parse error: {e}", file=sys.stderr)
        sys.exit(1)
    result = validate_profile(profile)
    for w in result.warnings:
        print(f"  warning: {w}", file=sys.stderr)
    if result.ok and result.warnings and strict:
        print(
            f"FAIL: {p} ({len(result.warnings)} warning(s) escalated by --strict)",
            file=sys.stderr,
        )
        sys.exit(1)
    if result.ok:
        n = len(profile.get("commands", {})) if isinstance(profile, dict) else 0
        suffix = f", {len(result.warnings)} warning(s)" if result.warnings else ""
        print(f"OK: {p} ({n} commands{suffix})")
        sys.exit(0)
    print(f"FAIL: {p}", file=sys.stderr)
    for error in result.errors:
        print(f"  {error}", file=sys.stderr)
    sys.exit(1)


def _no_terminal_to_confirm(question: str) -> bool:
    """The answer to ``--new``'s and ``--kill``'s question when stdin is not a terminal: always No."""
    print(f"termapy: {question} No terminal to answer on, so no.", file=sys.stderr)
    return False


def _flag_confirm(args: argparse.Namespace) -> Callable[[str], bool]:
    """How ``--kill`` and ``--new`` ask: yes under ``--yes``, else on stdin, else no."""
    from termapy.cli_flags import assume_yes, confirm_on_stdin

    if args.yes:
        return assume_yes
    return confirm_on_stdin if sys.stdin.isatty() else _no_terminal_to_confirm


def _resolve_port_positional(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Turn a positional that names a port into its quick config's path (``cli_flags.run_quick``).

    A ``.cfg``, ``.run`` or ``.pro`` name, or one ``resolve_config`` finds,
    is left as it is; a ``--demo`` launch ignores the positional, as it
    always has, and ``--mcp`` finds the config it names by its own rule, in
    the project's folder (``config_resolve.find_mcp_root``), and never opens
    a port for one.  Settings are refused after anything but a port.
    """
    from termapy.folders import FOLDER_EXTS, PROTO, RUN

    target = args.config
    file_types = (".cfg", FOLDER_EXTS[RUN], FOLDER_EXTS[PROTO])
    if target and not (args.demo or args.mcp) and Path(target).suffix.lower() not in file_types:
        from termapy.config_resolve import resolve_config

        if resolve_config(target) is None:
            from termapy.cli_flags import run_quick

            args.config = str(run_quick(target, args.settings, usage_error=parser.error))
            return
    if args.settings:
        parser.error(f"settings follow a port, not a config: {' '.join(args.settings)}")


def main() -> None:
    """Argparse + dispatch.  Print-and-exit flags run Textual-free."""
    parser = _build_parser()
    # Intermixed: a flag may sit between the positionals too
    # (`termapy COM3 9600 --quiet 8N1`), which plain parse_args refuses.
    args = parser.parse_intermixed_args()

    # The environment booleans are read once, at import (env_flags); a
    # value outside the vocabulary is refused here, by name, before any
    # flag or frontend runs -- a message, never a traceback.
    from termapy.env_flags import startup_error

    error = startup_error()
    if error:
        print(f"termapy: {error}", file=sys.stderr)
        sys.exit(1)

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
    if args.usb:
        from termapy.cli_flags import run_usb

        run_usb(args)
    if args.chips is not None:
        from termapy.cli_flags import run_chips

        run_chips(args)
    if args.alive:
        from termapy.cli_flags import run_alive

        run_alive()
    if args.kill is not None:
        from termapy.cli_flags import run_kill

        run_kill(args, confirm=_flag_confirm(args))
    if args.validate_profile is not None:
        _run_validate_profile(args.validate_profile, strict=args.strict)

    # --cfg-dir writes a module-global; do this before anything else
    # that might resolve configs.
    if args.cfg_dir:
        import termapy.config as _cfg_mod

        _cfg_mod.CFG_DIR = args.cfg_dir

    # --new writes a config, then hands it on as the positional config, so
    # every frontend below starts from it.  The overwrite question needs a
    # person: a piped stdin (or --mcp's protocol) answers No rather than
    # losing a line to the prompt.
    if args.new is not None:
        if args.config or args.demo:
            parser.error("--new cannot be combined with a config or --demo")
        from termapy.cli_flags import run_new

        args.config = str(run_new(args.new, confirm=_flag_confirm(args), usage_error=parser.error))

    # --board starts a dev board's starter config the same way: found or
    # created, then handed on as the positional config.
    if args.board is not None:
        if args.config or args.demo or args.new is not None:
            parser.error("--board cannot be combined with a config, --demo or --new")
        from termapy.cli_flags import run_board

        args.config = str(run_board(args.board, usage_error=parser.error))

    # A positional that names no config names a port: `termapy COM3 9600 8N1`
    # opens that port's quick config, made at first use and kept.  A config
    # wins the name, as an ssh_config alias does; settings follow a port only.
    _resolve_port_positional(args, parser)

    # Normalize positional arg by extension - infer intent.  This
    # matters for --run and --proto; done here so both TUI and CLI
    # see the same shape.
    if args.config:
        from termapy.folders import FOLDER_EXTS, PROTO, RUN

        ext = Path(args.config).suffix.lower()
        if ext == FOLDER_EXTS[RUN] and not args.run:
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
        elif ext == FOLDER_EXTS[PROTO] and not args.proto:
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

    # --mcp runs the MCP stdio server.  Lazy-import keeps the mcp SDK
    # (and pydantic) out of the import graph for normal termapy usage.
    if args.mcp:
        from termapy.mcp.server import run_mcp_stdio

        run_mcp_stdio(args)
        return

    if args.run and args.exec_cmd:
        print(
            "termapy: --run and --exec are mutually exclusive",
            file=sys.stderr,
        )
        sys.exit(2)

    if args.run or args.exec_cmd:
        args.cli = True  # --run / --exec imply --cli

    if args.exec_cmd:
        # --exec is for piping/scripting; ANSI escapes pollute captured
        # stdout and break grep/awk/jq.  Force --no-color so users
        # don't have to remember the pairing.  Anyone who genuinely
        # wants color in exec output is doing live-viewing -- use the
        # REPL form for that.
        args.no_color = True

    # Mode switching loop -- CLI or TUI.  ``_run_cli_mode`` lives in
    # ``cli.py`` (Textual-free); ``_run_tui_mode`` lives in ``app.py``
    # and is imported lazily below so CLI-only invocations never pay
    # the Textual import cost.
    from termapy.cli import _run_cli_mode
    from termapy.config import load_config
    from termapy.config_resolve import find_config, resolve_config

    # CLI flag from command line overrides config.  Otherwise check
    # default_ui.
    if args.cli:
        mode = "cli"
    elif args.vt100:
        mode = "vt100"
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
        from termapy.defaults import UI_MODES

        mode = (_peek_cfg or {}).get("default_ui", "tui")
        if mode not in UI_MODES:
            mode = "tui"

    prev_mode = None
    while mode:
        if mode == "cli":
            result = _run_cli_mode(args)
        elif mode == "vt100":
            from termapy.vt100 import run_vt100_mode

            # Entered from the TUI (/vt100, /demo.vt100) -> Ctrl-] returns to
            # the TUI (reversible toggle).  Launched via --vt100 -> quits.
            args._vt100_return_to = "tui" if prev_mode == "tui" else None
            result = run_vt100_mode(args)
        elif mode == "tui":
            from termapy.app import _run_tui_mode

            result = _run_tui_mode(args)
        else:
            break
        if result is None:
            break
        prev_mode = mode
        mode = result
        from termapy import diag_log

        diag_log.info("frontend switch: %s -> %s", prev_mode, mode)
        args.cli = mode == "cli"
        args.run = None  # don't re-run a script on switch
        args.exec_cmd = None  # don't re-run an exec command on switch
        args.demo = False  # don't re-setup demo on switch
