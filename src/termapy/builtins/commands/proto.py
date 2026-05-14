"""Built-in plugin: binary protocol send/expect testing."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

import fnmatch

from termapy.protocol import (
    ProtoScript,
    format_hex,
    format_hex_dump,
    format_smart,
    format_spaced,
    load_proto_script,
    match_response,
    parse_data_segments,
    parse_toml_script,
    strip_ansi,
)
from termapy.protocol import CRC_CATALOGUE, get_crc_registry

from termapy.folder_ops import build_folder_subcommands
from termapy.help_dynamic import compose, folder_line
from termapy.plugins import CapabilitySet, CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ---- Shared helpers --------------------------------------------------------


def _display_bytes(
    ctx: PluginContext, direction: str, data: bytes, binary: bool = False
) -> None:
    """Display TX or RX data as hex + smart text representation.

    Short packets (<=16 bytes) are shown on one line with hex and smart
    format. Longer packets get a multi-line hex dump with ASCII sidebar.

    Args:
        ctx: Plugin context for output.
        direction: Label prefix - ``"TX"`` (cyan) or ``"RX"`` (yellow).
        data: Raw bytes to display.
        binary: Unused (kept for API compatibility).
    """
    color = "cyan" if direction == "TX" else "yellow"
    if len(data) <= 16:
        hex_str = format_hex(data)
        smart_str = format_smart(data)
        if hex_str == smart_str:
            ctx.io.output(f"  {direction}: {hex_str}", color)
        else:
            ctx.io.output(f"  {direction}: {hex_str}  {smart_str}", color)
    else:
        ctx.io.output(f"  {direction} {len(data)} bytes:", color)
        for line in format_hex_dump(data):
            ctx.io.output(f"    {line}")


def _resolve_proto_file(ctx: PluginContext, filename: str) -> Path | None:
    """Resolve a proto script filename to a full path.

    Args:
        ctx: Plugin context with proto_dir.
        filename: Filename or path to resolve.

    Returns:
        Resolved Path, or None if not found (message written to ctx).
    """
    path = Path(filename)
    if not path.exists() and not path.suffix:
        path = Path(filename + ".pro")
    if not path.exists():
        alt = ctx.fs.proto_dir / path.name
        if alt.exists():
            path = alt
        else:
            ctx.io.output(f"File not found: {filename}", "red")
            if ctx.fs.proto_dir != Path("."):
                ctx.io.output(f"  (also checked {ctx.fs.proto_dir})")
            return None
    return path


def _run_cmd(ctx: PluginContext, cmd_text: str, frame_gap: int, quiet: bool) -> None:
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
        ctx.io.output(f"  CMD: {cmd_text}")
    ctx.serial.write((cmd_text + line_ending).encode(enc))
    response = ctx.serial.read_raw(1000, frame_gap)
    if not quiet:
        ctx.io.output(f"  CMD: flushed {len(response)} bytes")


def _run_toml_script(ctx: PluginContext, path: Path, script: ProtoScript) -> None:
    """Execute a TOML-format proto script.

    Args:
        ctx: Plugin context for serial I/O and output.
        path: Path to the script file (for display).
        script: Parsed ProtoScript.
    """
    with ctx.serial.io():
        ctx.serial.drain()

        script_name = script.name or path.name
        ctx.io.output(f"{'─' * 40}")
        ctx.io.output(f"  {script_name}", "bold underline bright_white")
        ctx.io.output(f"  {path.name} - {len(script.tests)} tests")
        ctx.io.output(f"{'─' * 40}")

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

            ctx.io.output(f"[PROTO] {tc.name}")
            ctx.io.output(f"  TX:       {format_spaced(tc.send_data, tc.binary)}", "cyan")
            ctx.serial.drain()
            ctx.serial.write(tc.send_data)

            t0 = time.monotonic()
            response = ctx.serial.read_raw(tc.timeout_ms, frame_gap)
            elapsed_ms = (time.monotonic() - t0) * 1000
            if script.strip_ansi:
                response = strip_ansi(response)

            ctx.io.output(f"  Expected: {format_spaced(tc.expect_data, tc.binary)}")
            if response:
                ctx.io.output(f"  Actual:   {format_spaced(response, tc.binary)}", "yellow")
                if match_response(tc.expect_data, response, tc.expect_mask):
                    ctx.io.output(
                        f"  PASS ({len(response)} bytes, {elapsed_ms:.0f}ms)",
                        "bright_green",
                    )
                    pass_count += 1
                else:
                    ctx.io.output("  FAIL", "red")
                    fail_count += 1
            else:
                ctx.io.output(f"  Actual:   (timeout after {tc.timeout_ms}ms)", "red")
                ctx.io.output("  FAIL", "red")
                fail_count += 1

        # Run teardown commands
        for cmd_text in script.teardown:
            _run_cmd(ctx, cmd_text, frame_gap, script.quiet)

    # Summary (after the with-block; serial port is released)
    elapsed_s = time.monotonic() - t_start
    total = pass_count + fail_count
    if total > 0:
        color = "bold bright_green" if fail_count == 0 else "bold red"
        ctx.io.output(f"{'─' * 40}")
        ctx.io.output(f"  Results: {pass_count}/{total} PASS ({elapsed_s:.3f}s)", color)
        ctx.io.output(f"{'─' * 40}")


def _run_flat_script(
    ctx: PluginContext, path: Path, settings: dict, steps: list
) -> None:
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

    with ctx.serial.io():
        ctx.serial.drain()

        script_name = settings.get("name") or path.name
        ctx.io.output(f"{'─' * 40}")
        ctx.io.output(f"  {script_name}", "bold underline bright_white")
        ctx.io.output(f"  {path.name} - {len(steps)} steps")
        ctx.io.output(f"{'─' * 40}")

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
                ctx.serial.drain()
                continue

            if step.action == "cmd":
                _run_cmd(ctx, step.data.decode("utf-8"), frame_gap, quiet)
                continue

            if step.action == "send":
                ctx.serial.drain()
                step_num += 1
                label = step.label or f"Step {step_num}"
                ctx.io.output(f"[PROTO] {label}")
                ctx.io.output(f"  TX:       {format_spaced(step.data, step.binary)}", "cyan")
                ctx.serial.write(step.data)

            elif step.action == "expect":
                if not step.label:
                    pass
                else:
                    step_num += 1
                    ctx.io.output(f"[PROTO] {step.label}")

                t0 = time.monotonic()
                response = ctx.serial.read_raw(step.timeout_ms, frame_gap)
                elapsed_ms = (time.monotonic() - t0) * 1000
                if do_strip_ansi:
                    response = strip_ansi(response)

                ctx.io.output(f"  Expected: {format_spaced(step.data, step.binary)}")
                if response:
                    ctx.io.output(
                        f"  Actual:   {format_spaced(response, step.binary)}", "yellow"
                    )
                    if match_response(step.data, response, step.mask):
                        ctx.io.output(
                            f"  PASS ({len(response)} bytes, {elapsed_ms:.0f}ms)",
                            "bright_green",
                        )
                        pass_count += 1
                    else:
                        ctx.io.output("  FAIL", "red")
                        fail_count += 1
                else:
                    ctx.io.output(f"  Actual:   (timeout after {step.timeout_ms}ms)", "red")
                    ctx.io.output("  FAIL", "red")
                    fail_count += 1

    # Summary (after the with-block; serial port is released)
    elapsed_s = time.monotonic() - t_start
    total = pass_count + fail_count
    if total > 0:
        color = "bold bright_green" if fail_count == 0 else "bold red"
        ctx.io.output(f"{'─' * 40}")
        ctx.io.output(f"  Results: {pass_count}/{total} PASS ({elapsed_s:.3f}s)", color)
        ctx.io.output(f"{'─' * 40}")


# ---- Leaf handlers ---------------------------------------------------------


def _parse_send_algo(
    name: str,
    registry: dict,
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


def _delay_at_least(seconds: float) -> None:
    """Delay for at least *seconds*. Spin-waits under 1ms for precision."""
    if seconds >= 0.001:
        time.sleep(seconds)
    else:
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            pass


def _cmd_send(ctx: PluginContext, args: str) -> CmdResult:
    """Send raw bytes to the serial port and display the response.

    Parses hex, quoted text, and inline delays (``~25ms``) into segments.
    Transmits data segments with delays between them (no line ending
    appended), then waits up to 1 second for a response frame.

    If the first word matches a known CRC algorithm (with optional
    ``_le``/``_be`` and ``_ascii`` suffixes), computes and appends the
    CRC to the data before sending. Default byte order is LE.

    Args:
        ctx: Plugin context for serial I/O and output.
        args: Hex bytes, quoted strings, and/or delays,
              e.g. ``'01 ~25ms 03 "OK\\r"'``.
    """
    if not args.strip():
        return CmdResult.fail(
            msg="Usage: /proto.send [algo[_le|_be][_ascii]] " '<hex/"text"/~delay ...>'
        )

    # Check if the first word is a CRC algorithm name (with optional suffixes)
    first, _, rest = args.strip().partition(" ")
    registry = get_crc_registry()
    algo_name, big_endian, ascii_crc = _parse_send_algo(first, registry)
    algo = registry.get(algo_name) if algo_name else None

    try:
        if algo is None:
            segments = parse_data_segments(args)
        else:
            if not rest.strip():
                return CmdResult.fail(msg=f"No data after CRC algorithm '{first}'")
            segments = parse_data_segments(rest.strip())

            # CRC is computed on all data bytes concatenated (delays excluded)
            all_data = b"".join(s for s in segments if isinstance(s, bytes))
            crc_value = algo.compute(all_data)
            if ascii_crc:
                hex_str = f"{crc_value:0{algo.width * 2}X}"
                if not big_endian:
                    pairs = [hex_str[i : i + 2] for i in range(0, len(hex_str), 2)]
                    hex_str = "".join(reversed(pairs))
                crc_data = hex_str.encode()
            else:
                crc_bytes = crc_value.to_bytes(algo.width, "big")
                if not big_endian:
                    crc_bytes = crc_bytes[::-1]
                crc_data = crc_bytes
            # Append CRC to the last data segment
            for i in range(len(segments) - 1, -1, -1):
                seg = segments[i]
                if isinstance(seg, bytes):
                    segments[i] = seg + crc_data
                    break
            else:
                segments.append(crc_data)
    except ValueError as e:
        return CmdResult.fail(msg=f"Parse error: {e}")

    if algo is not None and ctx.output_level == "verbose":
        endian_label = "BE" if big_endian else "LE"
        mode_label = "ascii" if ascii_crc else "bin"
        ctx.io.output(
            f"  CRC: {algo.name} = 0x{crc_value:0{algo.width * 2}X}"
            f" ({endian_label}, {mode_label})"
        )

    # Build display string with delay markers
    all_data = b"".join(s for s in segments if isinstance(s, bytes))
    has_delays = any(isinstance(s, float) for s in segments)

    with ctx.serial.io():
        ctx.serial.drain()
        if ctx.output_level == "verbose":
            if has_delays:
                parts = []
                for s in segments:
                    if isinstance(s, bytes):
                        hex_str = format_hex(s)
                        smart_str = format_smart(s)
                        if hex_str == smart_str:
                            parts.append(f"[cyan]{hex_str}[/]")
                        else:
                            parts.append(f"[cyan]{hex_str}[/]  [dim]{smart_str}[/]")
                    else:
                        if s >= 1.0:
                            parts.append(f"[dim][~{s:.1f}s][/]")
                        elif s >= 0.001:
                            parts.append(f"[dim][~{s * 1000:.0f}ms][/]")
                        else:
                            parts.append(f"[dim][~{s * 1_000_000:.0f}us][/]")
                ctx.io.output_markup(f"  [cyan]TX:[/] {' '.join(parts)}")
            else:
                _display_bytes(ctx, "TX", all_data, binary=True)

        t0 = time.monotonic()
        for segment in segments:
            if isinstance(segment, float):
                _delay_at_least(segment)
            else:
                ctx.serial.write(segment)
        response = ctx.serial.read_raw(1000)
        elapsed_ms = (time.monotonic() - t0) * 1000

    if response:
        _display_bytes(ctx, "RX", response, binary=True)
        if ctx.output_level == "verbose":
            ctx.io.output(f"  ({len(response)} bytes, {elapsed_ms:.0f}ms)")
    else:
        ctx.io.output("  RX: (no response)", "red")
    return CmdResult.ok()


def _cmd_run(ctx: PluginContext, args: str) -> CmdResult:
    """Execute a ``.pro`` test script (TOML or flat format).

    Auto-detects the format: TOML (structured with ``[[test]]`` sections)
    or flat (line-based with ``send:``/``expect:`` directives).

    Args:
        ctx: Plugin context for serial I/O, filesystem, and output.
        args: Filename of the ``.pro`` script to run.
    """
    filename = args.strip()
    if not filename:
        return CmdResult.fail(msg="Usage: /proto.run <file.pro>")

    path = _resolve_proto_file(ctx, filename)
    if path is None:
        return CmdResult.fail(msg=f"File not found: {filename}")

    try:
        text = path.read_text(encoding="utf-8")
        result = load_proto_script(text)
    except (ValueError, OSError) as e:
        return CmdResult.fail(msg=f"Script error: {e}")

    if result[0] == "toml":
        script = result[1]
        if not script.tests:
            return CmdResult.fail(msg="Script has no tests.")
        _run_toml_script(ctx, path, script)
    else:
        settings, steps = result[1]
        if not steps:
            return CmdResult.fail(msg="Script has no steps.")
        _run_flat_script(ctx, path, settings, steps)
    return CmdResult.ok()


def _cmd_debug(ctx: PluginContext, args: str) -> CmdResult:
    """Open the interactive protocol debug screen for a TOML .pro script.

    Args:
        ctx: Plugin context.
        args: Filename of the ``.pro`` script.
    """
    filename = args.strip()
    if not filename:
        return CmdResult.fail(msg="Usage: /proto.debug <file.pro>")

    path = _resolve_proto_file(ctx, filename)
    if path is None:
        return CmdResult.fail(msg=f"File not found: {filename}")

    try:
        text = path.read_text(encoding="utf-8")
        script = parse_toml_script(text)
    except (ValueError, OSError) as e:
        return CmdResult.fail(msg=f"Script error: {e}")

    if not script.tests:
        return CmdResult.fail(msg="Script has no tests.")

    ctx.engine.open_proto_debug(path, script)
    return CmdResult.ok()


def _cmd_hex(ctx: PluginContext, args: str) -> CmdResult:
    """Toggle hex display mode for all serial I/O.

    When enabled, received serial data is shown as hex bytes instead of
    decoded text. Accepts ``on``, ``off``, or no argument to toggle.

    Args:
        ctx: Plugin context for engine API access.
        args: ``"on"``, ``"off"``, or empty string to toggle.
    """
    flags = ctx.ns("flags")
    arg = args.strip().lower()
    if arg == "on":
        flags["hex_mode"] = True
        ctx.io.output("Hex display mode enabled.", "bright_green")
    elif arg == "off":
        flags["hex_mode"] = False
        ctx.io.output("Hex display mode disabled.", "bright_green")
    else:
        flags["hex_mode"] = not flags["hex_mode"]
        state = "enabled" if flags["hex_mode"] else "disabled"
        ctx.io.output(f"Hex display mode {state}.", "bright_green")
    return CmdResult.ok()


def _cmd_status(ctx: PluginContext, args: str) -> CmdResult:
    """Show current protocol mode state.

    Displays hex display mode and connection status.

    Args:
        ctx: Plugin context for engine state and output.
        args: Ignored.
    """
    hex_mode = ctx.ns("flags")["hex_mode"]
    connected = ctx.serial.is_connected()
    ctx.io.output(f"Hex mode: {'on' if hex_mode else 'off'}")
    ctx.io.output(f"Connected: {'yes' if connected else 'no'}")
    return CmdResult.ok()


# ---- CRC subcommand handlers ----------------------------------------------


def _crc_list(ctx: PluginContext, args: str) -> CmdResult:
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
        return CmdResult.fail(msg=f"No algorithms matching '{pattern}'")

    # Group by width
    groups: dict[int, list[str]] = {}
    for name in names:
        entry = CRC_CATALOGUE.get(name)
        width = entry["width"] if entry else registry[name].width * 8
        groups.setdefault(width, []).append(name)

    for width in sorted(groups):
        ctx.io.output(f"  CRC-{width} ({len(groups[width])} algorithms):", "bold")
        for name in groups[width]:
            entry = CRC_CATALOGUE.get(name)
            desc = entry.get("desc", "") if entry else "(plugin)"
            ctx.io.output(f"    {name:<30s} {desc}")

    total = sum(len(g) for g in groups.values())
    ctx.io.output(f"  {total} algorithms available")
    return CmdResult.ok()


def _crc_info(ctx: PluginContext, args: str) -> CmdResult:
    """Show detailed parameters for a named CRC algorithm.

    Args:
        ctx: Plugin context for output.
        args: Algorithm name (e.g. ``"crc16-modbus"``).
    """
    from termapy.plugins import format_kv_lines

    p = ctx.engine.prefix
    name = args.strip().lower()
    if not name:
        return CmdResult.fail(msg=f"Usage: {p}proto.crc.info <name>")

    entry = CRC_CATALOGUE.get(name)
    if entry is None:
        # Check if it's a plugin-only algorithm
        registry = get_crc_registry()
        if name in registry:
            alg = registry[name]
            ctx.io.output(f"  {name} (plugin, {alg.width * 8}-bit)")
            ctx.io.output("  No catalogue parameters - loaded from plugin file.")
            return CmdResult.ok()
        ctx.io.output(f"Use '{p}proto.crc.list' to see available algorithms.")
        return CmdResult.fail(msg=f"Unknown algorithm: {name}")

    w = entry["width"]
    hex_w = w // 4
    ctx.io.output(f"  {name}", "bold")
    desc = entry.get("desc", "")
    if desc:
        ctx.io.output(f"  {desc}")
    spec = (
        f"CRC:{name}" if w == 8
        else f"CRC:{name}_le  or  CRC:{name}_be"
    )
    rows = [
        ("Width",  f"{w} bits ({w // 8} bytes)"),
        ("Poly",   f"0x{entry['poly']:0{hex_w}X}"),
        ("Init",   f"0x{entry['init']:0{hex_w}X}"),
        ("RefIn",  str(entry['refin'])),
        ("RefOut", str(entry['refout'])),
        ("XorOut", f"0x{entry['xorout']:0{hex_w}X}"),
        ("Check",  f"0x{entry['check']:0{hex_w}X}  (CRC of '123456789')"),
        ("Spec",   spec),
    ]
    for line in format_kv_lines(rows):
        ctx.io.output_markup(line)
    return CmdResult.ok()


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
        len(t) == 2 and all(c in "0123456789abcdefABCDEF" for c in t) for t in tokens
    )
    if is_hex:
        return bytes(int(t, 16) for t in tokens), True
    return data_str.encode("utf-8"), False


def _crc_calc(ctx: PluginContext, args: str) -> CmdResult:
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
        return CmdResult.fail(msg="Usage: /proto.crc.calc <name> {hex bytes or text}")

    name = parts[0].lower()

    registry = get_crc_registry()
    alg = registry.get(name)
    if alg is None:
        p = ctx.engine.prefix
        ctx.io.output(f"Use '{p}proto.crc.list' to see available algorithms.")
        return CmdResult.fail(msg=f"Unknown algorithm: {name}")

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
                return CmdResult.fail(msg=f"Cannot read file: {e}")
        else:
            data, is_hex = _parse_crc_data(data_str)

    if not data:
        return CmdResult.fail(msg="No data to compute CRC over.")

    crc_val = alg.compute(data)
    hex_w = alg.width * 2
    crc_hex = f"0x{crc_val:0{hex_w}X}"

    # Show LE/BE byte representations
    crc_bytes = crc_val.to_bytes(alg.width, "big")
    crc_le = " ".join(f"{b:02X}" for b in reversed(crc_bytes))
    crc_be = " ".join(f"{b:02X}" for b in crc_bytes)

    ctx.io.output(f"  Algorithm: {name}")
    if file_path is not None:
        ctx.io.output(f"  Source:    file '{file_path}'")
        ctx.io.output(f"  Size:      {len(data)} bytes")
    elif is_hex:
        data_hex = " ".join(f"{b:02X}" for b in data)
        ctx.io.output(f"  Data:      {data_hex}  ({len(data)} bytes)")
    else:
        ctx.io.output(
            f"  Data:      {data_str!r}  ({len(data_str)} chars, " f"{len(data)} bytes)"
        )
    ctx.io.output(f"  CRC:       {crc_hex}")
    if alg.width > 1:
        ctx.io.output(f"  Bytes LE:  {crc_le}")
        ctx.io.output(f"  Bytes BE:  {crc_be}")
    else:
        ctx.io.output(f"  Byte:      {crc_be}")

    # In check mode, verify against the catalogue's expected value
    if check_mode:
        entry = CRC_CATALOGUE.get(name)
        if entry and "check" in entry:
            expected = entry["check"]
            if crc_val == expected:
                ctx.io.output(
                    f"  Check:     PASS - matches expected " f"0x{expected:0{hex_w}X}",
                    "green",
                )
            else:
                ctx.io.output(
                    f"  Check:     FAIL - expected " f"0x{expected:0{hex_w}X}",
                    "red",
                )
    return CmdResult.ok(value=crc_hex)


def _crc_codegen(ctx: PluginContext, args: str, lang: str) -> CmdResult:
    """Generate CRC source code in the specified language.

    Args:
        ctx: Plugin context for output.
        args: Algorithm name.
        lang: Target language (c, python, rust).
    """
    from termapy.protocol import GENERATORS

    use_table = ctx.flag("--table")
    tokens = args.strip().lower().split()
    name = tokens[0] if tokens else ""
    if not name:
        return CmdResult.fail(msg=f"Usage: /proto.crc.{lang} <algorithm> {{--table}}")

    gen = GENERATORS.get(lang)
    if gen is None:
        return CmdResult.fail(msg=f"Unknown language: {lang}")

    code = gen(name, table=use_table)
    if code is None:
        p = ctx.engine.prefix
        ctx.io.output(
            f"Unknown algorithm: {name}. Use {p}proto.crc.list to see available.", "red"
        )
        return CmdResult.fail(msg=f"Unknown algorithm: {name}")

    for line in code.split("\n"):
        ctx.io.output_markup(f"  [green]{line}[/]")
    return CmdResult.ok()


def _parse_find_args(text: str) -> dict[str, str]:
    """Parse /proto.crc.find arguments.

    ``bin=`` and ``asc=`` each consume everything after them to end of
    line (a captured packet may contain spaces and can't be split by
    whitespace).  ``width=`` and ``endian=`` are single-token filters
    and must come before the bin/asc argument.

    Returns a dict with keys ``mode`` ("bin" or "asc"), ``payload``
    (the captured packet string), and optionally ``width`` / ``endian``.
    Returns empty dict if neither bin= nor asc= is present.
    """
    result: dict[str, str] = {}
    stripped = text.strip()
    for key in ("bin", "asc"):
        marker = key + "="
        idx = stripped.find(marker)
        if idx != -1 and (idx == 0 or stripped[idx - 1].isspace()):
            result["mode"] = key
            result["payload"] = stripped[idx + len(marker):].strip()
            stripped = stripped[:idx].strip()
            break
    for tok in stripped.split():
        if tok.startswith("width="):
            result["width"] = tok[len("width="):]
        elif tok.startswith("endian="):
            result["endian"] = tok[len("endian="):]
    return result


def _crc_find(ctx: PluginContext, args: str) -> CmdResult:
    """Identify the CRC algorithm used in a captured packet.

    Given the full packet as ``bin=<hex>`` or ``asc=<text>``, slices
    trailing bytes (bin) or trailing hex-ASCII chars (asc) as the
    candidate CRC field and runs every catalogue algorithm against
    every plausible (width, endian) layout.  Reports the matches.
    """
    p = ctx.engine.prefix
    usage = (
        f"Usage: {p}proto.crc.find [width=8|16|32] [endian=be|le] "
        "bin=<hex> | asc=<text>"
    )
    kw = _parse_find_args(args)
    if "mode" not in kw:
        return CmdResult.fail(msg=usage)

    try:
        width_filter = int(kw["width"]) if "width" in kw else None
    except ValueError:
        return CmdResult.fail(msg="Invalid width: must be 8, 16, or 32")
    if width_filter is not None and width_filter not in (8, 16, 32):
        return CmdResult.fail(msg="Invalid width: must be 8, 16, or 32")

    endian_filter = kw.get("endian", "").lower()
    if endian_filter and endian_filter not in ("be", "le"):
        return CmdResult.fail(msg="Invalid endian: must be be or le")

    byte_widths = (width_filter // 8,) if width_filter else (1, 2, 4)
    endians = (endian_filter,) if endian_filter else ("be", "le")

    if kw["mode"] == "bin":
        tokens = kw["payload"].split()
        try:
            packet = bytes(int(t, 16) for t in tokens)
        except ValueError:
            return CmdResult.fail(msg="Invalid hex bytes in bin=")
        return _render_find_matches(
            ctx, _find_in_binary(packet, byte_widths, endians)
        )

    text = kw["payload"]
    if not text:
        return CmdResult.fail(msg="Empty asc= payload")
    return _render_find_matches(ctx, _find_in_ascii(text, byte_widths))


def _find_in_binary(
    packet: bytes, byte_widths: tuple[int, ...], endians: tuple[str, ...]
) -> list[tuple[str, int, str, int, int]]:
    """Search for matching CRC algorithms in a binary packet.

    Each match is ``(algo_name, width_bytes, endian, data_len, expected)``.
    """
    registry = get_crc_registry()
    matches: list[tuple[str, int, str, int, int]] = []
    for w in byte_widths:
        if len(packet) <= w:
            continue
        data = packet[:-w]
        crc_bytes = packet[-w:]
        candidates: list[tuple[str, int]] = []
        if w == 1:
            candidates.append(("-", crc_bytes[0]))
        else:
            for e in endians:
                order = "big" if e == "be" else "little"
                candidates.append((e, int.from_bytes(crc_bytes, order)))
        for name, algo in registry.items():
            if algo.width != w:
                continue
            computed = algo.compute(data)
            for endian, expected in candidates:
                if computed == expected:
                    matches.append((name, w, endian, len(data), expected))
    return matches


def _find_in_ascii(
    text: str, byte_widths: tuple[int, ...]
) -> list[tuple[str, int, str, int, int]]:
    """Search for matching CRC algorithms in an ASCII packet.

    Assumes the CRC field is the trailing N hex characters (where
    N = 2 * width_bytes).  No endian variation -- hex-ASCII encoding
    is unambiguous at the integer level.
    """
    registry = get_crc_registry()
    matches: list[tuple[str, int, str, int, int]] = []
    for w in byte_widths:
        hex_len = w * 2
        if len(text) <= hex_len:
            continue
        tail = text[-hex_len:]
        try:
            expected = int(tail, 16)
        except ValueError:
            continue  # trailing chars aren't hex; this width isn't a candidate
        data = text[:-hex_len].encode("utf-8")
        for name, algo in registry.items():
            if algo.width != w:
                continue
            if algo.compute(data) == expected:
                matches.append((name, w, "-", len(data), expected))
    return matches


def _dedupe_catalogue_aliases(
    matches: list[tuple[str, int, str, int, int]],
) -> list[tuple[str, int, str, int, int, list[str]]]:
    """Collapse matches that share identical catalogue parameters.

    crc16-modbus and crc16m (for example) are catalogue aliases for
    the same algorithm -- same poly/init/refin/refout/xorout/width.
    Reporting both as separate matches is noise.  Group them by the
    parameter tuple, keep the shortest name as canonical, list the
    rest as ``aliases``.

    Names not in ``CRC_CATALOGUE`` (e.g. plugins like ``sum8``)
    stand alone -- their ``compute`` is opaque so we can't compare
    parameters.
    """
    groups: dict[tuple, list[tuple[str, int, str, int, int]]] = {}
    for m in matches:
        name = m[0]
        entry = CRC_CATALOGUE.get(name)
        if entry is None:
            key = ("plugin", name, m[1], m[2])  # one group per plugin name
        else:
            key = (
                entry["width"],
                entry["poly"],
                entry["init"],
                entry["refin"],
                entry["refout"],
                entry["xorout"],
                m[2],  # endian -- different endians are legitimately different matches
                m[3],  # data_len
            )
        groups.setdefault(key, []).append(m)

    collapsed: list[tuple[str, int, str, int, int, list[str]]] = []
    for group in groups.values():
        # Prefer the longer, more descriptive name as canonical
        # (crc16-modbus > crc16m) -- matches what users expect to see.
        names = sorted({g[0] for g in group}, key=lambda n: (-len(n), n))
        canonical = names[0]
        aliases = names[1:]
        # pick one representative tuple for the other fields
        rep = next(g for g in group if g[0] == canonical)
        collapsed.append((canonical, rep[1], rep[2], rep[3], rep[4], aliases))
    return collapsed


def _render_find_matches(
    ctx: PluginContext,
    matches: list[tuple[str, int, str, int, int]],
) -> CmdResult:
    """Render the find results to the terminal."""
    if not matches:
        ctx.io.output("  No matches found.", "red")
        ctx.io.output("  Packet may use a non-standard algorithm, a CRC field")
        ctx.io.output("  that is not trailing, or be too short to identify.")
        return CmdResult.ok()
    collapsed = _dedupe_catalogue_aliases(matches)
    if len(collapsed) == 1:
        ctx.io.output("  1 match:", "green")
    else:
        ctx.io.output(f"  {len(collapsed)} matches:", "yellow")
    p = ctx.engine.prefix
    for name, w, endian, data_len, expected, aliases in collapsed:
        hex_w = w * 2
        width_bits = w * 8
        endian_str = "" if endian == "-" else f"  endian={endian}"
        alias_str = f"  (aka {', '.join(aliases)})" if aliases else ""
        ctx.io.output_markup(
            f"  [cyan]{name}[/]{alias_str}  "
            f"width={width_bits}  field=last{w}  "
            f"expected=0x{expected:0{hex_w}X}{endian_str}  "
            f"data={data_len} bytes"
        )
    if len(collapsed) == 1:
        name = collapsed[0][0]
        ctx.io.output("")
        ctx.io.output(
            f"  Generate source: {p}proto.crc.c {name}  "
            f"(or .python / .rust)",
            "dim",
        )
    if len(collapsed) > 1:
        ctx.io.output("")
        ctx.io.output(
            "  Multiple matches usually means the packet is too short to",
            "dim",
        )
        ctx.io.output(
            "  disambiguate.  Capture a second packet with a different CRC",
            "dim",
        )
        ctx.io.output(
            "  and intersect the match sets to narrow down.",
            "dim",
        )
    return CmdResult.ok(value=collapsed[0][0] if len(collapsed) == 1 else "")


# ── Dynamic long_help ─────────────────────────────────────────────────────────

_PROTO_PROSE = """\
Send examples:
  /proto.send 01 02 03         - send three hex bytes
  /proto.send "AT\\r"           - send text with carriage return
  /proto.send 0x01 "hello" 0D  - mix hex and text
  /proto.send 00 ~25ms "AT\\r"  - wake byte, 25ms pause, then command
  /proto.send ~500us 01 02     - 500us delay before data

Inline delays use ~duration syntax (us, ms, s). Delays under
1ms use spin-wait for precision. Delays >= 1ms use OS sleep.

Send with CRC (algorithm name with optional _le/_be/_ascii suffixes):
  /proto.send crc16-modbus 01 03 00 00 00 0A            - append LE CRC (default)
  /proto.send crc16-modbus_be 01 03 00 00 00 0A         - append BE CRC
  /proto.send crc16-modbus_ascii "READ 0000"             - append CRC as hex text
  /proto.send crc16-modbus_be_ascii 01 03 00 00 00 0A   - BE CRC as hex text

CRC tools:
  {prefix}proto.crc.list              - list all 62 algorithms
  {prefix}proto.crc.list *modbus*     - filter by glob pattern
  {prefix}proto.crc.info crc16-modbus - show parameters for Modbus CRC
  {prefix}proto.crc.calc crc16-modbus 01 03 00 00 00 01  - compute CRC

Script files (.pro) support TOML format with [[test]] sections
or flat format with send:/expect: directives. Scripts are found
in the proto/ subfolder of your config directory."""


def _proto_folder_line(ctx: PluginContext) -> str:
    """Green one-liner showing the count of .pro scripts."""
    return folder_line(ctx, "proto", noun="script")


def _proto_long_help(ctx: PluginContext) -> str:
    return compose(_proto_folder_line(ctx), _PROTO_PROSE)


def _proto_root_handler(ctx: PluginContext, args: str) -> CmdResult:
    """Bare /proto: TUI opens the Proto picker, CLI shows /help proto.

    With args, /proto is a namespace -- the dispatcher routes to a
    subcommand.  This handler runs only when no subcommand matched.
    """
    if args.strip():
        prefix = ctx.engine.prefix
        return CmdResult.fail(
            msg=f"Usage: {prefix}proto.<sub>  (try {prefix}proto.help)"
        )
    if ctx.engine.open_picker is not None:
        return ctx.engine.open_picker("proto")
    return _proto_help_handler(ctx, args)


def _proto_help_handler(ctx: PluginContext, args: str) -> CmdResult:
    """Same as /help proto, plus an AVAILABLE PROTO FILES list."""
    from termapy.builtins.commands.help import (
        _show_command_help,
        append_files_section,
    )

    result = _show_command_help(ctx, "proto")
    proto_dir = ctx.fs.proto_dir
    files = (
        sorted(f.name for f in proto_dir.glob("*.pro"))
        if proto_dir.is_dir() else []
    )
    append_files_section(ctx, "AVAILABLE PROTO FILES", files)
    return result


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="proto",
    help="Binary protocol tools: send, run, debug, hex, crc, status.",
    long_help=_proto_long_help,
    handler=_proto_root_handler,
    sub_commands={
        "help": Command(
            help="Show /proto help.",
            handler=_proto_help_handler,
        ),
        "send": Command(
            args='{algo[_le|_be][_ascii]} <hex|"text">',
            help="Send raw bytes (with optional CRC), show response.",
            handler=_cmd_send,
            needs=CapabilitySet(serial_connected=True),
        ),
        "run": Command(
            args="<file.pro>",
            help="Run a protocol test script.",
            long_help=_proto_folder_line,
            handler=_cmd_run,
            needs=CapabilitySet(serial_connected=True),
        ),
        "debug": Command(
            args="<file.pro>",
            help="Interactive protocol debug screen.",
            long_help=_proto_folder_line,
            handler=_cmd_debug,
            needs=CapabilitySet(tui_mode=True, serial_connected=True),
        ),
        "hex": Command(
            args="{on|off}",
            help="Toggle hex display mode.",
            handler=_cmd_hex,
        ),
        "crc": Command(
            help="Browse and compute CRC algorithms.",
            long_help=(
                "All 64 named CRC algorithms come from the reveng CRC\n"
                "catalogue maintained by Greg Cook since 1999:\n"
                "  https://reveng.sourceforge.io/crc-catalogue/all.htm\n"
                "\n"
                "Every algorithm is verified against its catalogue check\n"
                "value (the CRC of the ASCII string '123456789') on every\n"
                "test run.  See {prefix}credits for full attribution."
            ),
            sub_commands={
                "list": Command(
                    args="{pattern}",
                    help="List algorithms (optional glob filter).",
                    handler=_crc_list,
                ),
                "info": Command(
                    args="<name>",
                    help="Show algorithm parameters and description.",
                    long_help=(
                        "Shows the polynomial, init, reflection, xor-out, and check\n"
                        "value for one CRC algorithm. Use {prefix}proto.crc.list to see\n"
                        "every available name."
                    ),
                    handler=_crc_info,
                ),
                "find": Command(
                    args="[width=8|16|32] [endian=be|le] bin=<hex> | asc=<text>",
                    help="Identify the CRC algorithm used in a captured packet.",
                    long_help=(
                        "Given the full packet, tries every catalogue algorithm\n"
                        "against each plausible trailing-CRC layout and reports\n"
                        "the match(es).\n"
                        "\n"
                        "bin=<hex>  - hex bytes, last 1/2/4 bytes = CRC field\n"
                        "asc=<text> - ASCII, last 2/4/8 chars = hex-encoded CRC\n"
                        "\n"
                        "Optional filters:\n"
                        "  width=8|16|32  restrict CRC width\n"
                        "  endian=be|le   restrict byte order (bin= only)\n"
                        "\n"
                        "Examples:\n"
                        "  {prefix}proto.crc.find bin=01 03 00 00 00 0A C5 CD\n"
                        "  {prefix}proto.crc.find asc=123456789 width=16\n"
                    ),
                    handler=_crc_find,
                ),
                "calc": Command(
                    args="<name> {data}",
                    help="Compute CRC over hex bytes, text, or file.",
                    long_help=(
                        "Computes a CRC over the supplied data. Use {prefix}proto.crc.list\n"
                        "to see every available algorithm name.\n"
                        "\n"
                        "Example:\n"
                        "  {prefix}proto.crc.calc crc16-modbus 01 03 00 00 00 01"
                    ),
                    handler=_crc_calc,
                ),
                "c": Command(
                    args="<name>",
                    flags={"--table": "Use 256-entry lookup table (4-8x faster)."},
                    help="Generate C source code for a CRC algorithm.",
                    long_help=(
                        "Prints a self-contained C implementation for the named\n"
                        "CRC. Use {prefix}proto.crc.list to see every available algorithm\n"
                        "name, or {prefix}proto.crc.info <name> for its parameters.\n"
                        "\n"
                        "Example:\n"
                        "  {prefix}proto.crc.c crc16-modbus\n"
                        "  {prefix}proto.crc.c crc32 --table"
                    ),
                    handler=lambda ctx, args: _crc_codegen(ctx, args, "c"),
                ),
                "python": Command(
                    args="<name>",
                    flags={"--table": "Use 256-entry lookup table (4-8x faster)."},
                    help="Generate Python source code for a CRC algorithm.",
                    long_help=(
                        "Prints a self-contained Python implementation for the named\n"
                        "CRC. Use {prefix}proto.crc.list to see every available algorithm\n"
                        "name, or {prefix}proto.crc.info <name> for its parameters.\n"
                        "\n"
                        "Example:\n"
                        "  {prefix}proto.crc.python crc16-modbus\n"
                        "  {prefix}proto.crc.python crc32 --table"
                    ),
                    handler=lambda ctx, args: _crc_codegen(ctx, args, "python"),
                ),
                "rust": Command(
                    args="<name>",
                    flags={"--table": "Use 256-entry lookup table (4-8x faster)."},
                    help="Generate Rust source code for a CRC algorithm.",
                    long_help=(
                        "Prints a self-contained Rust implementation for the named\n"
                        "CRC. Use {prefix}proto.crc.list to see every available algorithm\n"
                        "name, or {prefix}proto.crc.info <name> for its parameters.\n"
                        "\n"
                        "Example:\n"
                        "  {prefix}proto.crc.rust crc16-modbus\n"
                        "  {prefix}proto.crc.rust crc32 --table"
                    ),
                    handler=lambda ctx, args: _crc_codegen(ctx, args, "rust"),
                ),
            },
        ),
        "status": Command(
            help="Show current protocol state.",
            handler=_cmd_status,
        ),
        **build_folder_subcommands("proto"),
    },
)
