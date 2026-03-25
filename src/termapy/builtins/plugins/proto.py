"""Built-in plugin: binary protocol send/expect testing."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

import fnmatch

from termapy.protocol import (
    CRC_CATALOGUE,
    ProtoScript,
    format_hex,
    format_hex_dump,
    format_smart,
    format_spaced,
    get_crc_registry,
    load_proto_script,
    match_response,
    parse_data,
    parse_proto_script,
    parse_toml_script,
    strip_ansi,
)

from termapy.plugins import Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ---- Shared helpers --------------------------------------------------------

def _display_bytes(ctx: PluginContext, direction: str, data: bytes,
                   binary: bool = False) -> None:
    """Display TX or RX data, choosing format by packet size.

    Short packets (<=16 bytes) are shown inline. Binary data is shown
    as hex, text data uses smart format (mixed text/hex). Longer packets
    get a multi-line hex dump with offset and ASCII sidebar.

    Args:
        ctx: Plugin context for output.
        direction: Label prefix - ``"TX"`` (cyan) or ``"RX"`` (yellow).
        data: Raw bytes to display.
        binary: If True, display short packets as hex instead of smart format.
    """
    color = "cyan" if direction == "TX" else "yellow"
    if len(data) <= 16:
        fmt = format_hex(data) if binary else format_smart(data)
        ctx.write(f"  {direction}: {fmt}", color)
    else:
        ctx.write(f"  {direction} {len(data)} bytes:", color)
        for line in format_hex_dump(data):
            ctx.write(f"    {line}")


def _resolve_proto_file(ctx: PluginContext, filename: str) -> Path | None:
    """Resolve a proto script filename to a full path.

    Args:
        ctx: Plugin context with proto_dir.
        filename: Filename or path to resolve.

    Returns:
        Resolved Path, or None if not found (error already written).
    """
    path = Path(filename)
    if not path.exists() and not path.suffix:
        path = Path(filename + ".pro")
    if not path.exists():
        alt = ctx.proto_dir / path.name
        if alt.exists():
            path = alt
        else:
            ctx.write(f"File not found: {filename}", "red")
            if ctx.proto_dir != Path("."):
                ctx.write(f"  (also checked {ctx.proto_dir})", "dim")
            return None
    return path


def _run_cmd(ctx: PluginContext, cmd_text: str, frame_gap: int,
             quiet: bool) -> None:
    """Send a setup/teardown command and drain the response.

    Args:
        ctx: Plugin context for serial I/O.
        cmd_text: Command text to send.
        frame_gap: Frame gap for response collection.
        quiet: Suppress output if True.
    """
    line_ending = ctx.cfg.get("line_ending", "\r")
    enc = ctx.cfg.get("encoding", "utf-8")
    if not quiet:
        ctx.write(f"  CMD: {cmd_text}", "dim")
    ctx.serial_write((cmd_text + line_ending).encode(enc))
    response = ctx.serial_read_raw(1000, frame_gap)
    if not quiet:
        ctx.write(f"  CMD: flushed {len(response)} bytes", "dim")


def _run_toml_script(ctx: PluginContext, path: Path,
                     script: ProtoScript) -> None:
    """Execute a TOML-format proto script.

    Args:
        ctx: Plugin context for serial I/O and output.
        path: Path to the script file (for display).
        script: Parsed ProtoScript.
    """
    ctx.engine.set_proto_active(True)
    ctx.serial_drain()

    script_name = script.name or path.name
    ctx.write(f"{'─' * 40}")
    ctx.write(f"  {script_name}", "bold underline bright_white")
    ctx.write(f"  {path.name} - {len(script.tests)} tests", "dim")
    ctx.write(f"{'─' * 40}")

    frame_gap = script.frame_gap_ms

    # Run setup commands
    for cmd_text in script.setup:
        _run_cmd(ctx, cmd_text, frame_gap, script.quiet)

    pass_count = 0
    fail_count = 0
    t_start = time.monotonic()

    for tc in script.tests:
        # Run per-test setup commands
        for cmd_text in tc.setup:
            _run_cmd(ctx, cmd_text, frame_gap, script.quiet)

        ctx.write(f"[PROTO] {tc.name}")
        ctx.write(f"  TX:       {format_spaced(tc.send_data, tc.binary)}", "cyan")
        ctx.serial_drain()
        ctx.serial_write(tc.send_data)

        t0 = time.monotonic()
        response = ctx.serial_read_raw(tc.timeout_ms, frame_gap)
        elapsed_ms = (time.monotonic() - t0) * 1000
        if script.strip_ansi:
            response = strip_ansi(response)

        ctx.write(f"  Expected: {format_spaced(tc.expect_data, tc.binary)}", "dim")
        if response:
            ctx.write(f"  Actual:   {format_spaced(response, tc.binary)}", "yellow")
            if match_response(tc.expect_data, response, tc.expect_mask):
                ctx.write(f"  PASS ({len(response)} bytes, {elapsed_ms:.0f}ms)", "bright_green")
                pass_count += 1
            else:
                ctx.write("  FAIL", "red")
                fail_count += 1
        else:
            ctx.write(f"  Actual:   (timeout after {tc.timeout_ms}ms)", "red")
            ctx.write("  FAIL", "red")
            fail_count += 1

    # Run teardown commands
    for cmd_text in script.teardown:
        _run_cmd(ctx, cmd_text, frame_gap, script.quiet)

    # Summary
    ctx.engine.set_proto_active(False)
    elapsed_s = time.monotonic() - t_start
    total = pass_count + fail_count
    if total > 0:
        color = "bold bright_green" if fail_count == 0 else "bold red"
        ctx.write(f"{'─' * 40}")
        ctx.write(f"  Results: {pass_count}/{total} PASS ({elapsed_s:.3f}s)", color)
        ctx.write(f"{'─' * 40}")


def _run_flat_script(ctx: PluginContext, path: Path, settings: dict,
                     steps: list) -> None:
    """Execute a flat-format proto script.

    Args:
        ctx: Plugin context for serial I/O and output.
        path: Path to the script file (for display).
        settings: Parsed script settings.
        steps: Parsed step list.
    """
    do_strip_ansi = settings.get("strip_ansi", False)
    quiet = False
    frame_gap = settings.get("frame_gap_ms", 0)

    ctx.engine.set_proto_active(True)
    ctx.serial_drain()

    script_name = settings.get("name") or path.name
    ctx.write(f"{'─' * 40}")
    ctx.write(f"  {script_name}", "bold underline bright_white")
    ctx.write(f"  {path.name} - {len(steps)} steps", "dim")
    ctx.write(f"{'─' * 40}")

    pass_count = 0
    fail_count = 0
    step_num = 0
    t_start = time.monotonic()

    for step in steps:
        if step.action == "quiet":
            quiet = True
            continue

        if step.action == "loud":
            quiet = False
            continue

        if step.action == "delay":
            time.sleep(step.timeout_ms / 1000.0)
            continue

        if step.action == "flush":
            ctx.serial_drain()
            continue

        if step.action == "cmd":
            _run_cmd(ctx, step.data.decode("utf-8"), frame_gap, quiet)
            continue

        if step.action == "send":
            ctx.serial_drain()
            step_num += 1
            label = step.label or f"Step {step_num}"
            ctx.write(f"[PROTO] {label}")
            ctx.write(f"  TX:       {format_spaced(step.data, step.binary)}", "cyan")
            ctx.serial_write(step.data)

        elif step.action == "expect":
            if not step.label:
                pass
            else:
                step_num += 1
                ctx.write(f"[PROTO] {step.label}")

            t0 = time.monotonic()
            response = ctx.serial_read_raw(step.timeout_ms, frame_gap)
            elapsed_ms = (time.monotonic() - t0) * 1000
            if do_strip_ansi:
                response = strip_ansi(response)

            ctx.write(f"  Expected: {format_spaced(step.data, step.binary)}", "dim")
            if response:
                ctx.write(f"  Actual:   {format_spaced(response, step.binary)}", "yellow")
                if match_response(step.data, response, step.mask):
                    ctx.write(f"  PASS ({len(response)} bytes, {elapsed_ms:.0f}ms)", "bright_green")
                    pass_count += 1
                else:
                    ctx.write("  FAIL", "red")
                    fail_count += 1
            else:
                ctx.write(f"  Actual:   (timeout after {step.timeout_ms}ms)", "red")
                ctx.write("  FAIL", "red")
                fail_count += 1

    # Summary
    ctx.engine.set_proto_active(False)
    elapsed_s = time.monotonic() - t_start
    total = pass_count + fail_count
    if total > 0:
        color = "bold bright_green" if fail_count == 0 else "bold red"
        ctx.write(f"{'─' * 40}")
        ctx.write(f"  Results: {pass_count}/{total} PASS ({elapsed_s:.3f}s)", color)
        ctx.write(f"{'─' * 40}")


# ---- Leaf handlers ---------------------------------------------------------

def _parse_send_algo(
    name: str, registry: dict,
) -> tuple[str | None, bool, bool]:
    """Extract algorithm name and suffixes from a /proto.send first word.

    Strips ``_le``/``_be`` (byte order) and ``_ascii`` (output format)
    suffixes from the name and looks up the base algorithm in the registry.

    Args:
        name: First word from the command (e.g. ``"crc16-modbus_be_ascii"``).
        registry: CRC algorithm registry to match against.

    Returns:
        Tuple of (algo_name, big_endian, ascii_crc). algo_name is None
        if the name doesn't match any algorithm.
    """
    low = name.lower()
    # Exact match first (some algo names contain underscores)
    if low in registry:
        return low, False, False

    big_endian = False
    ascii_crc = False

    # Strip _ascii suffix
    if low.endswith("_ascii"):
        ascii_crc = True
        low = low[:-6]

    # Strip _le or _be suffix
    if low.endswith("_be"):
        big_endian = True
        low = low[:-3]
    elif low.endswith("_le"):
        low = low[:-3]

    if low in registry:
        return low, big_endian, ascii_crc
    return None, False, False


def _cmd_send(ctx: PluginContext, args: str) -> None:
    """Send raw bytes to the serial port and display the response.

    Parses hex and/or quoted text into bytes, transmits them (no line
    ending appended), then waits up to 1 second for a response frame.
    Displays both TX and RX as hex with byte count and round-trip time.

    If the first word matches a known CRC algorithm (with optional
    ``_le``/``_be`` and ``_ascii`` suffixes), computes and appends the
    CRC to the data before sending. Default byte order is LE.

    Args:
        ctx: Plugin context for serial I/O and output.
        args: Hex bytes and/or quoted strings, e.g. ``'01 03 "OK\\r"'``.
    """
    if not args.strip():
        ctx.write("Usage: /proto.send [algo[_le|_be][_ascii]] "
                  "<hex bytes or \"text\">", "red")
        return
    if not ctx.is_connected():
        ctx.write("Not connected.", "red")
        return

    # Check if the first word is a CRC algorithm name (with optional suffixes)
    first, _, rest = args.strip().partition(" ")
    registry = get_crc_registry()
    algo_name, big_endian, ascii_crc = _parse_send_algo(first, registry)
    algo = registry.get(algo_name) if algo_name else None

    try:
        if algo is None:
            data = parse_data(args)
        else:
            if not rest.strip():
                ctx.write(f"No data after CRC algorithm '{first}'", "red")
                return
            data = parse_data(rest.strip())
            crc_value = algo.compute(data)
            if ascii_crc:
                hex_str = f"{crc_value:0{algo.width * 2}X}"
                if not big_endian:
                    pairs = [hex_str[i:i+2]
                             for i in range(0, len(hex_str), 2)]
                    hex_str = "".join(reversed(pairs))
                data += hex_str.encode()
            else:
                crc_bytes = crc_value.to_bytes(algo.width, "big")
                if not big_endian:
                    crc_bytes = crc_bytes[::-1]
                data += crc_bytes
    except ValueError as e:
        ctx.write(f"Parse error: {e}", "red")
        return

    if algo is not None:
        endian_label = "BE" if big_endian else "LE"
        mode_label = "ascii" if ascii_crc else "bin"
        ctx.write(f"  CRC: {algo.name} = 0x{crc_value:0{algo.width * 2}X}"
                  f" ({endian_label}, {mode_label})")

    ctx.engine.set_proto_active(True)
    ctx.serial_drain()
    _display_bytes(ctx, "TX", data, binary=True)
    t0 = time.monotonic()
    ctx.serial_write(data)
    response = ctx.serial_read_raw(1000)
    elapsed_ms = (time.monotonic() - t0) * 1000
    ctx.engine.set_proto_active(False)

    if response:
        _display_bytes(ctx, "RX", response, binary=True)
        ctx.write(f"  ({len(response)} bytes, {elapsed_ms:.0f}ms)")
    else:
        ctx.write("  RX: (no response)", "red")


def _cmd_run(ctx: PluginContext, args: str) -> None:
    """Execute a ``.pro`` test script (TOML or flat format).

    Auto-detects the format: TOML (structured with ``[[test]]`` sections)
    or flat (line-based with ``send:``/``expect:`` directives).

    Args:
        ctx: Plugin context for serial I/O, filesystem, and output.
        args: Filename of the ``.pro`` script to run.
    """
    filename = args.strip()
    if not filename:
        ctx.write("Usage: /proto.run <file.pro>", "red")
        return

    path = _resolve_proto_file(ctx, filename)
    if path is None:
        return

    if not ctx.is_connected():
        ctx.write("Not connected.", "red")
        return

    try:
        text = path.read_text(encoding="utf-8")
        fmt, parsed = load_proto_script(text)
    except (ValueError, OSError) as e:
        ctx.write(f"Script error: {e}", "red")
        return

    if fmt == "toml":
        script = parsed
        if not script.tests:
            ctx.write("Script has no tests.", "red")
            return
        _run_toml_script(ctx, path, script)
    else:
        settings, steps = parsed
        if not steps:
            ctx.write("Script has no steps.", "red")
            return
        _run_flat_script(ctx, path, settings, steps)


def _cmd_debug(ctx: PluginContext, args: str) -> None:
    """Open the interactive protocol debug screen for a TOML .pro script.

    Args:
        ctx: Plugin context.
        args: Filename of the ``.pro`` script.
    """
    filename = args.strip()
    if not filename:
        ctx.write("Usage: /proto.debug <file.pro>", "red")
        return

    path = _resolve_proto_file(ctx, filename)
    if path is None:
        return

    if not ctx.is_connected():
        ctx.write("Not connected.", "red")
        return

    try:
        text = path.read_text(encoding="utf-8")
        script = parse_toml_script(text)
    except (ValueError, OSError) as e:
        ctx.write(f"Script error: {e}", "red")
        return

    if not script.tests:
        ctx.write("Script has no tests.", "red")
        return

    ctx.engine.open_proto_debug(path, script)


def _cmd_hex(ctx: PluginContext, args: str) -> None:
    """Toggle hex display mode for all serial I/O.

    When enabled, received serial data is shown as hex bytes instead of
    decoded text. Accepts ``on``, ``off``, or no argument to toggle.

    Args:
        ctx: Plugin context for engine API access.
        args: ``"on"``, ``"off"``, or empty string to toggle.
    """
    arg = args.strip().lower()
    if arg == "on":
        ctx.engine.set_hex_mode(True)
        ctx.write("Hex display mode enabled.", "bright_green")
    elif arg == "off":
        ctx.engine.set_hex_mode(False)
        ctx.write("Hex display mode disabled.", "bright_green")
    else:
        # Toggle
        current = ctx.engine.get_hex_mode()
        ctx.engine.set_hex_mode(not current)
        state = "enabled" if not current else "disabled"
        ctx.write(f"Hex display mode {state}.", "bright_green")


def _cmd_status(ctx: PluginContext, args: str) -> None:
    """Show current protocol mode state.

    Displays hex display mode and connection status.

    Args:
        ctx: Plugin context for engine state and output.
        args: Ignored.
    """
    hex_mode = ctx.engine.get_hex_mode()
    connected = ctx.is_connected()
    ctx.write(f"Hex mode: {'on' if hex_mode else 'off'}")
    ctx.write(f"Connected: {'yes' if connected else 'no'}")


# ---- CRC subcommand handlers ----------------------------------------------

def _crc_list(ctx: PluginContext, args: str) -> None:
    """List available CRC algorithms, optionally filtered by glob pattern.

    Args:
        ctx: Plugin context for output.
        args: Optional glob pattern (e.g. ``"*modbus*"``).
    """
    registry = get_crc_registry()
    pattern = args.strip().lower() if args.strip() else ""

    # Skip backward-compat aliases (crc16m, crc16x)
    aliases = {"crc16m", "crc16x"}
    names = sorted(n for n in registry if n not in aliases)
    if pattern:
        names = [n for n in names if fnmatch.fnmatch(n, pattern)]

    if not names:
        ctx.write(f"No algorithms matching '{pattern}'", "red")
        return

    # Group by width
    groups: dict[int, list[str]] = {}
    for name in names:
        entry = CRC_CATALOGUE.get(name)
        width = entry["width"] if entry else registry[name].width * 8
        groups.setdefault(width, []).append(name)

    for width in sorted(groups):
        ctx.write(f"  CRC-{width} ({len(groups[width])} algorithms):", "bold")
        for name in groups[width]:
            entry = CRC_CATALOGUE.get(name)
            desc = entry.get("desc", "") if entry else "(plugin)"
            ctx.write(f"    {name:<30s} {desc}", "dim")

    total = sum(len(g) for g in groups.values())
    ctx.write(f"  {total} algorithms available")


def _crc_help(ctx: PluginContext, args: str) -> None:
    """Show detailed parameters for a named CRC algorithm.

    Args:
        ctx: Plugin context for output.
        args: Algorithm name (e.g. ``"crc16-modbus"``).
    """
    name = args.strip().lower()
    if not name:
        ctx.write("Usage: /proto.crc.help <name>", "red")
        return

    entry = CRC_CATALOGUE.get(name)
    if entry is None:
        # Check if it's a plugin-only algorithm
        registry = get_crc_registry()
        if name in registry:
            alg = registry[name]
            ctx.write(f"  {name} (plugin, {alg.width * 8}-bit)")
            ctx.write("  No catalogue parameters - loaded from plugin file.")
            return
        ctx.write(f"Unknown algorithm: {name}", "red")
        ctx.write("Use '/proto.crc.list' to see available algorithms.")
        return

    w = entry["width"]
    hex_w = w // 4
    ctx.write(f"  {name}", "bold")
    desc = entry.get("desc", "")
    if desc:
        ctx.write(f"  {desc}")
    ctx.write(f"  Width:   {w} bits ({w // 8} bytes)")
    ctx.write(f"  Poly:    0x{entry['poly']:0{hex_w}X}")
    ctx.write(f"  Init:    0x{entry['init']:0{hex_w}X}")
    ctx.write(f"  RefIn:   {entry['refin']}")
    ctx.write(f"  RefOut:  {entry['refout']}")
    ctx.write(f"  XorOut:  0x{entry['xorout']:0{hex_w}X}")
    ctx.write(f"  Check:   0x{entry['check']:0{hex_w}X}  (CRC of '123456789')")
    # Show format spec usage
    if w == 8:
        ctx.write(f"  Spec:    CRC:{name}")
    else:
        ctx.write(f"  Spec:    CRC:{name}_le  or  CRC:{name}_be")


def _parse_crc_data(data_str: str) -> tuple[bytes, bool]:
    """Auto-detect hex bytes vs plain text.

    If every whitespace-separated token is a valid two-character hex pair,
    the input is treated as hex bytes. Otherwise the entire string is
    encoded as UTF-8 text.

    Args:
        data_str: Raw data string from the user.

    Returns:
        Tuple of (data bytes, True if parsed as hex).
    """
    tokens = data_str.split()
    is_hex = bool(tokens) and all(
        len(t) == 2 and all(c in "0123456789abcdefABCDEF" for c in t)
        for t in tokens
    )
    if is_hex:
        return bytes(int(t, 16) for t in tokens), True
    return data_str.encode("utf-8"), False


def _crc_calc(ctx: PluginContext, args: str) -> None:
    """Compute a CRC over the provided data.

    Auto-detects hex bytes vs plain text: if every token is a valid
    two-character hex pair the input is treated as hex bytes, otherwise
    the entire string is encoded as UTF-8 text.

    Args:
        ctx: Plugin context for output.
        args: Algorithm name followed by data (hex bytes or text).
    """
    parts = args.strip().split(None, 1)
    if not parts:
        ctx.write(
            "Usage: /proto.crc.calc <name> {hex bytes or text}", "red"
        )
        return

    name = parts[0].lower()

    registry = get_crc_registry()
    alg = registry.get(name)
    if alg is None:
        ctx.write(f"Unknown algorithm: {name}", "red")
        ctx.write("Use '/proto.crc.list' to see available algorithms.")
        return

    # No data provided - use the standard check string "123456789"
    check_mode = len(parts) < 2
    file_path: Path | None = None
    if check_mode:
        data = b"123456789"
        data_str = "123456789"
        is_hex = False
    else:
        data_str = parts[1]
        # Check if the data argument is a file path
        candidate = Path(data_str)
        if candidate.is_file():
            file_path = candidate
            try:
                data = file_path.read_bytes()
                is_hex = False
            except OSError as e:
                ctx.write(f"Cannot read file: {e}", "red")
                return
        else:
            data, is_hex = _parse_crc_data(data_str)

    if not data:
        ctx.write("No data to compute CRC over.", "red")
        return

    crc_val = alg.compute(data)
    hex_w = alg.width * 2
    crc_hex = f"0x{crc_val:0{hex_w}X}"

    # Show LE/BE byte representations
    crc_bytes = crc_val.to_bytes(alg.width, "big")
    crc_le = " ".join(f"{b:02X}" for b in reversed(crc_bytes))
    crc_be = " ".join(f"{b:02X}" for b in crc_bytes)

    ctx.write(f"  Algorithm: {name}")
    if file_path is not None:
        ctx.write(f"  Source:    file '{file_path}'")
        ctx.write(f"  Size:      {len(data)} bytes")
    elif is_hex:
        data_hex = " ".join(f"{b:02X}" for b in data)
        ctx.write(f"  Data:      {data_hex}  ({len(data)} bytes)")
    else:
        ctx.write(
            f"  Data:      {data_str!r}  ({len(data_str)} chars, "
            f"{len(data)} bytes)"
        )
    ctx.write(f"  CRC:       {crc_hex}")
    if alg.width > 1:
        ctx.write(f"  Bytes LE:  {crc_le}")
        ctx.write(f"  Bytes BE:  {crc_be}")
    else:
        ctx.write(f"  Byte:      {crc_be}")

    # In check mode, verify against the catalogue's expected value
    if check_mode:
        entry = CRC_CATALOGUE.get(name)
        if entry and "check" in entry:
            expected = entry["check"]
            if crc_val == expected:
                ctx.write(
                    f"  Check:     PASS - matches expected "
                    f"0x{expected:0{hex_w}X}",
                    "green",
                )
            else:
                ctx.write(
                    f"  Check:     FAIL - expected "
                    f"0x{expected:0{hex_w}X}",
                    "red",
                )


def _cmd_list(ctx: PluginContext, args: str) -> None:
    """List .pro files in the proto/ directory.

    Args:
        ctx: Plugin context for proto_dir and output.
        args: Unused.
    """
    d = ctx.proto_dir
    if not d.exists():
        ctx.write("  (no proto/ directory)", "dim")
        return
    files = sorted(d.glob("*.pro"))
    if not files:
        ctx.write("  (no .pro files)", "dim")
        return
    for f in files:
        ctx.write(f"  {f.name}")


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="proto",
    help="Binary protocol tools: send, run, debug, hex, crc, status.",
    long_help="""\
Send examples:
  /proto.send 01 02 03         - send three hex bytes
  /proto.send "AT\\r"           - send text with carriage return
  /proto.send 0x01 "hello" 0D  - mix hex and text

Send with CRC (algorithm name with optional _le/_be/_ascii suffixes):
  /proto.send crc16-modbus 01 03 00 00 00 0A            - append LE CRC (default)
  /proto.send crc16-modbus_be 01 03 00 00 00 0A         - append BE CRC
  /proto.send crc16-modbus_ascii "READ 0000"             - append CRC as hex text
  /proto.send crc16-modbus_be_ascii 01 03 00 00 00 0A   - BE CRC as hex text

CRC tools:
  /proto.crc.list              - list all 62 algorithms
  /proto.crc.list *modbus*     - filter by glob pattern
  /proto.crc.help crc16-modbus - show parameters for Modbus CRC
  /proto.crc.calc crc16-modbus 01 03 00 00 00 01  - compute CRC

Script files (.pro) support TOML format with [[test]] sections
or flat format with send:/expect: directives. Scripts are found
in the proto/ subfolder of your config directory.""",
    sub_commands={
        "send": Command(
            args='{algo[_le|_be][_ascii]} <hex|"text">',
            help="Send raw bytes (with optional CRC), show response.",
            handler=_cmd_send,
        ),
        "run": Command(
            args="<file.pro>",
            help="Run a protocol test script.",
            handler=_cmd_run,
        ),
        "list": Command(
            help="List .pro files in the proto/ directory.",
            handler=_cmd_list,
        ),
        "debug": Command(
            args="<file.pro>",
            help="Interactive protocol debug screen.",
            handler=_cmd_debug,
        ),
        "hex": Command(
            args="{on|off}",
            help="Toggle hex display mode.",
            handler=_cmd_hex,
        ),
        "crc": Command(
            help="Browse and compute CRC algorithms.",
            sub_commands={
                "list": Command(
                    args="{pattern}",
                    help="List algorithms (optional glob filter).",
                    handler=_crc_list,
                ),
                "help": Command(
                    args="<name>",
                    help="Show algorithm parameters and description.",
                    handler=_crc_help,
                ),
                "calc": Command(
                    args="<name> {data}",
                    help="Compute CRC over hex bytes, text, or file.",
                    handler=_crc_calc,
                ),
            },
        ),
        "status": Command(
            help="Show current protocol state.",
            handler=_cmd_status,
        ),
    },
)
