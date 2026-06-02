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

    Without an explicit ``_le`` / ``_be`` suffix the result's
    ``big_endian`` flag is derived from the algorithm's natural wire
    order (``refout=True`` -> ``False``, i.e. low byte first;
    ``refout=False`` -> ``True``, i.e. high byte first).  Explicit
    suffixes still win when present.

    Args:
        name: First word from the command (e.g. ``"crc16-modbus_be_ascii"``).
        registry: CRC algorithm registry to match against.

    Returns:
        Tuple of (algo_name, big_endian, ascii_crc). algo_name is None
        if the name doesn't match any algorithm.
    """
    low = name.lower()
    # Exact match first (some algo names contain underscores).  No
    # suffix -> use the algorithm's natural wire order: refout=True
    # means low byte first (LE wire), refout=False means high first (BE).
    if low in registry:
        return low, not registry[low].refout, False

    ascii_crc = False

    # Strip _ascii suffix
    if low.endswith("_ascii"):
        ascii_crc = True
        low = low[:-6]

    # Strip _le or _be suffix.  ``None`` here means the user gave no
    # explicit byte-order suffix; we derive from the algorithm below.
    explicit_be: bool | None = None
    if low.endswith("_be"):
        explicit_be = True
        low = low[:-3]
    elif low.endswith("_le"):
        explicit_be = False
        low = low[:-3]

    if low in registry:
        big_endian = (
            explicit_be
            if explicit_be is not None
            else not registry[low].refout
        )
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
    CRC to the data before sending.  Default byte order follows the
    algorithm's natural wire order (``refout=True`` -> LE, ``False``
    -> BE); explicit ``_le`` / ``_be`` override it.

    With ``--dry-run`` the handler does all the parsing and CRC
    assembly, prints the bytes that would have been sent, and returns
    without touching the serial port -- so it works without a connected
    device and is the way to verify CRC byte order against a real
    frame layout from the REPL.

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

    dry_run = ctx.flag("--dry-run")

    if algo is not None and (dry_run or ctx.output_level == "verbose"):
        endian_label = "BE" if big_endian else "LE"
        mode_label = "ascii" if ascii_crc else "bin"
        ctx.io.output(
            f"  CRC: {algo.name} = 0x{crc_value:0{algo.width * 2}X}"
            f" ({endian_label}, {mode_label})"
        )

    # Build display string with delay markers
    all_data = b"".join(s for s in segments if isinstance(s, bytes))
    has_delays = any(isinstance(s, float) for s in segments)

    if dry_run:
        # Skip the actual write -- show the bytes that would have been
        # sent and return.  Useful for verifying CRC byte order, frame
        # layout, or scripted sends without a connected device.
        if has_delays:
            parts: list[str] = []
            for s in segments:
                if isinstance(s, bytes):
                    parts.append(format_hex(s))
                elif s >= 1.0:
                    parts.append(f"[~{s:.1f}s]")
                elif s >= 0.001:
                    parts.append(f"[~{s * 1000:.0f}ms]")
                else:
                    parts.append(f"[~{s * 1_000_000:.0f}us]")
            ctx.io.output(f"  TX (dry-run): {' '.join(parts)}")
        else:
            _display_bytes(ctx, "TX (dry-run)", all_data, binary=True)
        return CmdResult.ok(value=all_data.hex())

    if not ctx.serial.is_connected():
        return CmdResult.fail(msg="Not connected.")

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
    # Return the response bytes as a hex string so scripts can
    # capture and parse them (empty string when no response).
    return CmdResult.ok(value=response.hex() if response else "")


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
    # Return the script path so scripts can confirm what was run.
    return CmdResult.ok(value=path)


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

    ctx.internal.open_proto_debug(path, script)
    return CmdResult.ok(value=path)


def _cmd_hex(ctx: PluginContext, args: str) -> CmdResult:
    """Toggle hex display mode for all serial I/O.

    When enabled, received serial data is shown as hex bytes instead of
    decoded text. Accepts ``on``, ``off``, or no argument to toggle.

    Args:
        ctx: Plugin context for internal-handle access.
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
    # Mirror the echo/verbose convention: return the new state.
    return CmdResult.ok(value="on" if flags["hex_mode"] else "off")


def _cmd_status(ctx: PluginContext, args: str) -> CmdResult:
    """Show current protocol mode state.

    Displays hex display mode and connection status.

    Args:
        ctx: Plugin context for state and output.
        args: Ignored.
    """
    hex_mode = ctx.ns("flags")["hex_mode"]
    connected = ctx.serial.is_connected()
    ctx.io.output(f"Hex mode: {'on' if hex_mode else 'off'}")
    ctx.io.output(f"Connected: {'yes' if connected else 'no'}")
    return CmdResult.ok(
        value=f"hex={'on' if hex_mode else 'off'}, "
              f"connected={'yes' if connected else 'no'}"
    )


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
    # Return the matched algorithm names so scripts can iterate or count.
    return CmdResult.ok(value="\n".join(names))


def _crc_info(ctx: PluginContext, args: str) -> CmdResult:
    """Show detailed parameters for a named CRC algorithm.

    Args:
        ctx: Plugin context for output.
        args: Algorithm name (e.g. ``"crc16-modbus"``).
    """
    from termapy.plugins import format_kv_lines

    p = ctx.prefix
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
            return CmdResult.ok(value=name)
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
    # Return the algorithm name so scripts can confirm the lookup.
    return CmdResult.ok(value=name)


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
        p = ctx.prefix
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




def _write_crc_codegen_files(
    result: str | tuple[str, str],
    lang: str,
    file_stem: str,
    cwd,
) -> list:
    """Write codegen output to disk; return the list of written ``Path``s.

    The C generator returns a ``(header, source)`` tuple; everything
    else returns a single source string.  This helper handles both
    shapes and picks the per-language extension from
    ``crcglot.LANGUAGES[lang].extensions``.  Extracted out of ``_crc_codegen`` so
    the file-writing logic is testable without spinning up a full
    PluginContext.

    Args:
        result: Generator output (string for python/rust/vhdl;
            ``(header, source)`` tuple for c).
        lang: Target language code (``c``, ``python``, ``rust``,
            ``vhdl``).
        file_stem: User-supplied stem; the per-language extension is
            appended.
        cwd: Base directory the file(s) are written under (typically
            ``Path.cwd()``; tests pass ``tmp_path``).

    Returns:
        List of ``Path`` objects, one per file written.  Length 2 for
        C (.h + .c), length 1 for the other languages.
    """
    from crcglot import LANGUAGES
    extensions = LANGUAGES[lang].extensions if lang in LANGUAGES else (".txt",)
    written: list = []
    # ``str(content)`` casts -- ``result`` comes from a Callable in
    # GENERATORS so ty widens its elements to ``object``; runtime
    # always returns strings.
    if isinstance(result, tuple):
        for content, ext in zip(result, extensions):
            path = cwd / f"{file_stem}{ext}"
            path.write_text(str(content), encoding="utf-8")
            written.append(path)
    else:
        path = cwd / f"{file_stem}{extensions[0]}"
        path.write_text(str(result), encoding="utf-8")
        written.append(path)
    return written


_CRC_CODEGEN_KV_KEYS = {
    "file", "width", "poly", "init", "refin", "refout",
    "xorout", "name", "desc", "symbol",
}


def _parse_int_value(value: str, key: str) -> int:
    """Parse a hex (``0x...``) or decimal integer; raise ValueError on garbage."""
    s = value.strip()
    if s.lower().startswith("0x"):
        return int(s, 16)
    return int(s)


def _parse_bool_value(value: str, key: str) -> bool:
    """Parse a permissive boolean: true / false / 1 / 0 / yes / no."""
    v = value.strip().lower()
    if v in ("true", "1", "yes", "on"):
        return True
    if v in ("false", "0", "no", "off"):
        return False
    raise ValueError(
        f"{key} must be true/false (got {value!r})"
    )


def _symbol_from_stem(file_stem: str) -> str:
    """Derive a valid C/Rust/Python identifier from a file path / stem.

    Strips the directory part and replaces ``-`` and ``.`` with ``_``
    so a stem like ``out/my-crc.v1`` becomes ``my_crc_v1``.
    """
    from pathlib import Path
    base = Path(file_stem).name
    return base.replace("-", "_").replace(".", "_")


def _crc_codegen(ctx: PluginContext, args: str, lang: str) -> CmdResult:
    """Generate CRC source code in the specified language.

    Two invocation shapes:

    * **Catalogue lookup** (existing): ``<algorithm-name> [file=stem]
      [symbol=name]``.  Looks ``algorithm-name`` up in
      ``CRC_CATALOGUE``; ``symbol=`` overrides the default function
      name; ``file=`` writes to disk and (if no ``symbol=`` given)
      also sets the function name from the file basename.
    * **Custom CRC** (new): ``width=N poly=X [init=...] [refin=...]
      [refout=...] [xorout=...] [name=...] [desc=...] [file=stem]
      [symbol=name]``.  Builds a synthetic catalogue entry from raw
      Rocksoft/Williams parameters and computes the check value via
      the same generic engine that drives the bundled catalogue.

    Args:
        ctx: Plugin context for output.
        args: See above.
        lang: Target language (c, python, rust, vhdl).
    """
    from pathlib import Path

    from crcglot import AlgorithmInfo
    from termapy.protocol import GENERATORS, GENERATORS_FROM_ENTRY
    from termapy.protocol.crc import _generic_crc

    # Variant resolution: crcglot 0.10's generators take one ``variant=``
    # literal ("bitwise" / "table" / "slice8") instead of the separate
    # boolean kwargs we used to pass.
    if ctx.flag("--table") and ctx.flag("--slice8"):
        return CmdResult.fail(
            msg="--slice8 and --table are mutually exclusive (slice-by-8 "
            "already uses tables, just 8 of them)"
        )
    if ctx.flag("--slice8"):
        variant = "slice8"
    elif ctx.flag("--table"):
        variant = "table"
    else:
        variant = "bitwise"

    # --slice8 fallback, data-driven from crcglot's per-language variant
    # set.  A language gets --slice8 registered (see _build_one_crc_lang_
    # command) whenever it has a table-driven variant -- so if the user
    # passes --slice8 but the language doesn't emit slice-by-8 natively,
    # fall back to --table with a one-line note rather than erroring.
    # Python is the one current case (table but no slice8): its note
    # calls out the measured 0.79x CPython regression specifically.
    if variant == "slice8":
        from crcglot import LANGUAGES
        variants = LANGUAGES[lang].variants if lang in LANGUAGES else frozenset()
        if "slice8" not in variants:
            if lang == "python":
                note = (
                    "Note: --slice8 is slower than --table in CPython "
                    "(measured 0.79x); using --table instead."
                )
            else:
                note = (
                    f"Note: --slice8 not available for {lang}; "
                    "using --table instead."
                )
            ctx.io.output(note, "yellow")
            variant = "table"

    # Parse all ``key=value`` tokens manually -- termapy's
    # registered-flag system is bool-only.  Anything that isn't a
    # recognized key=value pair drops into ``name_tokens`` and
    # (for catalogue mode) is treated as the algorithm name.
    kv: dict[str, str] = {}
    name_tokens: list[str] = []
    for tok in args.strip().split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            if k in _CRC_CODEGEN_KV_KEYS:
                kv[k] = v
                continue
        name_tokens.append(tok)

    # Reject flags the language doesn't support.  Languages that DO
    # register a flag (--table on c/python/rust/..., --slice8 on
    # c/rust/...) have it stripped by the dispatcher before we get here.
    # But bitwise-only languages (vhdl, verilog) register NO variant
    # flags, so the dispatcher passes --table / --slice8 through as bare
    # tokens -- which would otherwise be silently ignored.  Anything
    # left in name_tokens that starts with "--" is an unsupported flag;
    # tell the user instead of dropping it.  (Algorithm names like
    # "crc16-modbus" have internal dashes but never start with "--".)
    stray = next((t for t in name_tokens if t.startswith("--")), None)
    if stray is not None:
        from crcglot import LANGUAGES
        supported = (
            sorted(LANGUAGES[lang].variants) if lang in LANGUAGES else []
        )
        disp = LANGUAGES[lang].display_name if lang in LANGUAGES else lang
        return CmdResult.fail(
            msg=f"/proto.crc.{lang} doesn't accept {stray} "
            f"({disp} emits {', '.join(supported) or 'bitwise'} only)"
        )

    file_stem = kv.get("file")
    if file_stem == "":
        return CmdResult.fail(msg="file= requires a value (e.g. file=crc_my)")
    symbol_override = kv.get("symbol")
    if symbol_override == "":
        return CmdResult.fail(msg="symbol= requires a value")

    is_custom = "width" in kv

    if is_custom:
        # ----- Custom CRC: build synthetic entry from raw params -----
        if "poly" not in kv:
            return CmdResult.fail(msg="Custom CRC requires poly=...")
        try:
            width = _parse_int_value(kv["width"], "width")
            poly = _parse_int_value(kv["poly"], "poly")
            init = _parse_int_value(kv.get("init", "0"), "init")
            xorout = _parse_int_value(kv.get("xorout", "0"), "xorout")
            refin = _parse_bool_value(kv.get("refin", "false"), "refin")
            refout = _parse_bool_value(kv.get("refout", "false"), "refout")
        except ValueError as e:
            return CmdResult.fail(msg=f"Custom CRC param: {e}")
        if width not in (8, 16, 32, 64):
            return CmdResult.fail(
                msg="Custom CRC width must be 8, 16, 32, or 64"
            )
        if variant == "slice8" and width not in (32, 64):
            return CmdResult.fail(
                msg=f"--slice8 requires width=32 or 64 (got width={width}). "
                "Slice-by-8 only makes sense at those widths."
            )

        # Compute the check value (CRC of "123456789") via the same
        # engine that powers the bundled catalogue.  Embedded in the
        # generated _self_test so downstream users can verify too.
        check = _generic_crc(
            b"123456789", width, poly, init, refin, refout, xorout
        )

        custom_name = kv.get("name") or "crc_custom"
        # Unify with the catalogue-lookup branch's ``name`` so the
        # downstream stdout banner (which references ``name``) works for
        # custom params too -- previously custom + C + stdout crashed
        # with an unbound ``name``.
        name = custom_name
        desc = kv.get("desc") or (
            f"Custom CRC-{width} (poly=0x{poly:X}, init=0x{init:X}, "
            f"refin={refin}, refout={refout}, xorout=0x{xorout:X})"
        )
        # crcglot's *_from_entry generators take a typed AlgorithmInfo,
        # not a dict.  0.10 dropped the ``name`` field (the name is
        # always the dict key in the catalogue); we still pass it
        # separately to ``gen_entry(custom_name, entry, ...)`` below.
        entry = AlgorithmInfo(
            width=width, poly=poly, init=init,
            refin=refin, refout=refout, xorout=xorout,
            check=check, desc=desc,
        )
        # Symbol resolution: explicit > file basename > name=.
        symbol = (
            symbol_override
            or (_symbol_from_stem(file_stem) if file_stem else None)
            or _symbol_from_stem(custom_name)
        )

        gen_entry = GENERATORS_FROM_ENTRY.get(lang)
        if gen_entry is None:
            return CmdResult.fail(msg=f"Unknown language: {lang}")
        result = gen_entry(
            custom_name, entry, symbol=symbol, variant=variant,
        )
    else:
        # ----- Catalogue lookup (existing path) -----
        name = name_tokens[0].lower() if name_tokens else ""
        if not name:
            return CmdResult.fail(
                msg=(
                    f"Usage: /proto.crc.{lang} <algorithm> [--table] "
                    "[file=stem] [symbol=name]\n"
                    f"   or: /proto.crc.{lang} width=N poly=X "
                    "[init=...] [refin=...] [refout=...] [xorout=...] "
                    "[name=...] [file=...] [symbol=...]"
                )
            )

        # Symbol resolution: explicit > file basename > generator default.
        symbol = (
            symbol_override
            or (_symbol_from_stem(file_stem) if file_stem else None)
        )

        gen = GENERATORS.get(lang)
        if gen is None:
            return CmdResult.fail(msg=f"Unknown language: {lang}")
        if variant == "slice8":
            # Fail early with a clear message rather than letting
            # generate_c / generate_rust raise ValueError later.
            from termapy.protocol.crc import CRC_CATALOGUE
            entry = CRC_CATALOGUE.get(name)
            if entry is not None and entry["width"] not in (32, 64):
                return CmdResult.fail(
                    msg=f"--slice8 requires width=32 or 64; {name} is "
                    f"width={entry['width']}"
                )
        result = gen(name, symbol=symbol, variant=variant)
        if result is None:
            p = ctx.prefix
            ctx.io.output(
                f"Unknown algorithm: {name}. "
                f"Use {p}proto.crc.list to see available.",
                "red",
            )
            return CmdResult.fail(msg=f"Unknown algorithm: {name}")

    # ----- File output mode (file=STEM) -----
    if file_stem is not None:
        written = _write_crc_codegen_files(result, lang, file_stem, Path.cwd())
        ctx.io.output(
            f"Wrote {', '.join(str(p.name) for p in written)}", "green"
        )
        # Return the joined paths so scripts can capture them.
        return CmdResult.ok(value=" ".join(str(p) for p in written))

    # ----- Stdout mode (default) -----
    # generate_c returns a (header, source) tuple so the same algorithm
    # gives a complete C and C++-friendly pair.  generate_python /
    # generate_rust still return a single source string.  Render both
    # shapes with file-name banners so the user can split them easily.
    if isinstance(result, tuple):
        fname = name.replace("-", "_").replace(".", "_")
        header, source = result
        banner_h = f"/* ====== {fname}.h ====== */"
        banner_c = f"/* ====== {fname}.c ====== */"
        combined = "\n".join([banner_h, header, "", banner_c, source])
        for line in combined.split("\n"):
            ctx.io.output_markup(f"  [green]{line}[/]")
        return CmdResult.ok(value=combined)

    code = result
    for line in code.split("\n"):
        ctx.io.output_markup(f"  [green]{line}[/]")
    # Return the generated source so scripts can write it to disk.
    return CmdResult.ok(value=code)


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
    p = ctx.prefix
    usage = (
        f"Usage: {p}proto.crc.find [width=8|16|32|64] [endian=be|le] "
        "bin=<hex> | asc=<text>"
    )
    kw = _parse_find_args(args)
    if "mode" not in kw:
        return CmdResult.fail(msg=usage)

    try:
        width_filter = int(kw["width"]) if "width" in kw else None
    except ValueError:
        return CmdResult.fail(msg="Invalid width: must be 8, 16, 32, or 64")
    if width_filter is not None and width_filter not in (8, 16, 32, 64):
        return CmdResult.fail(msg="Invalid width: must be 8, 16, 32, or 64")

    endian_filter = kw.get("endian", "").lower()
    if endian_filter and endian_filter not in ("be", "le"):
        return CmdResult.fail(msg="Invalid endian: must be be or le")

    # CRC-64 widths searched by default alongside the smaller ones.
    byte_widths = (width_filter // 8,) if width_filter else (1, 2, 4, 8)
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
        # Zero matches -- empty value lets scripts distinguish "no match"
        # from "single hit" (the success branch returns the alg name).
        return CmdResult.ok(value="")
    collapsed = _dedupe_catalogue_aliases(matches)
    if len(collapsed) == 1:
        ctx.io.output("  1 match:", "green")
    else:
        ctx.io.output(f"  {len(collapsed)} matches:", "yellow")
    p = ctx.prefix
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
        prefix = ctx.prefix
        return CmdResult.fail(
            msg=f"Usage: {prefix}proto.<sub>  (try {prefix}proto.help)"
        )
    if ctx.internal.open_picker is not None:
        return ctx.internal.open_picker("proto")
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


def _build_one_crc_lang_command(code: str, info) -> Command:
    """Build one ``/proto.crc.<lang>`` Command from a crcglot LanguageInfo.

    Flags are derived from the language's ``variants`` set:
      * ``--table`` registered when table-driven output is available.
      * ``--slice8`` registered when *either* slice-by-8 is native OR a
        table variant exists to fall back to (so the flag is accepted
        and _crc_codegen's data-driven fallback handles the rest).
      * Bitwise-only languages (verilog, vhdl) get no variant flags.

    Help text, file extension, and display name all come from the
    LanguageInfo -- no per-language hardcoding here, so a new crcglot
    language surfaces as a working REPL command on the next bump.
    """
    variants = info.variants
    has_table = "table" in variants
    has_slice8 = "slice8" in variants
    exts = info.extensions
    ext_desc = " + ".join(f"STEM{e}" for e in exts)

    flags: dict[str, str] = {}
    if has_table:
        flags["--table"] = "Use 256-entry lookup table (4-8x faster)."
    if has_slice8:
        flags["--slice8"] = (
            "Use slice-by-8 (8 tables, 5-10x faster than --table for "
            "CRC-32/64 on large buffers). Width 32 or 64 only."
        )
    elif has_table:
        # Table exists but no native slice-by-8: accept --slice8 and
        # fall back to --table (the fallback note is emitted at dispatch).
        flags["--slice8"] = (
            "Accepted but falls back to --table (no native slice-by-8 "
            f"for {info.display_name})."
        )

    example_lines = [
        f"  {{prefix}}proto.crc.{code} crc16-modbus",
    ]
    if has_table:
        example_lines.append(f"  {{prefix}}proto.crc.{code} crc32 --table")
    if has_slice8:
        example_lines.append(f"  {{prefix}}proto.crc.{code} crc32 --slice8")
    example_lines.append(f"  {{prefix}}proto.crc.{code} crc16-modbus file=my_crc")

    long_help = (
        f"Prints a self-contained {info.display_name} implementation for the\n"
        f"named CRC. Use {{prefix}}proto.crc.list to see every available\n"
        f"algorithm name, or {{prefix}}proto.crc.info <name> for its parameters.\n"
        f"\n"
        f"With ``file=STEM``, writes {ext_desc} to the current directory\n"
        f"instead of stdout.\n"
        f"\n"
        f"Example:\n"
        + "\n".join(example_lines)
    )

    return Command(
        args="<name> {file=stem}",
        flags=flags,
        help=f"Generate {info.display_name} source code for a CRC algorithm.",
        long_help=long_help,
        # _code=code freezes the loop variable into the closure so each
        # handler dispatches to its own language (Python late-binding gotcha).
        handler=lambda ctx, args, _code=code: _crc_codegen(ctx, args, _code),
    )


def _build_crc_lang_commands() -> dict[str, Command]:
    """Build all ``/proto.crc.<lang>`` commands from ``crcglot.LANGUAGES``.

    One command per language crcglot exposes (c, csharp, go, python,
    rust, typescript, verilog, vhdl in crcglot 0.8.0).  Adding a target
    in a future crcglot release surfaces it here automatically on the
    next dependency bump -- no termapy code change.
    """
    from crcglot import LANGUAGES
    return {
        code: _build_one_crc_lang_command(code, info)
        for code, info in sorted(LANGUAGES.items())
    }


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="proto",
    help="Binary protocol tools: send, run, debug, hex, crc, info.",
    long_help=_proto_long_help,
    handler=_proto_root_handler,
    sub_commands={
        "help": Command(
            help="Show /proto help.",
            handler=_proto_help_handler,
        ),
        "send": Command(
            args='{algo[_le|_be][_ascii]} <hex|"text"> {--dry-run}',
            help="Send raw bytes (with optional CRC), show response.",
            handler=_cmd_send,
            flags={
                "--dry-run": (
                    "Print the bytes that would be sent without writing "
                    "to the port (works without a connected device)."
                ),
            },
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
                    help="Print algorithm parameters and description.",
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
                # /proto.crc.<lang> commands are built dynamically from
                # crcglot.LANGUAGES so every language crcglot ships gets a
                # REPL command automatically -- no hardcoded per-language
                # blocks to keep in sync.  See _build_crc_lang_commands.
                **_build_crc_lang_commands(),
            },
        ),
        "info": Command(
            help="Print current protocol state.",
            handler=_cmd_status,
        ),
        **build_folder_subcommands("proto"),
    },
)
