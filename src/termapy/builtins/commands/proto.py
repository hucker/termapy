"""Built-in plugin: binary protocol send/expect testing."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Literal

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

from crcglot import DetectResult, detect

from termapy.builtins.commands._crc_verbs import build_crc_verb_command
from termapy.folder_ops import build_folder_subcommands
from termapy.help_dynamic import compose, folder_line
from termapy.plugins import CapabilitySet, CmdResult, Command, UsageError
from termapy.scripting import format_duration

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ---- Shared helpers --------------------------------------------------------


_TEXT_ESCAPES = {
    0x00: r"\0", 0x07: r"\a", 0x08: r"\b", 0x09: r"\t",
    0x0A: r"\n", 0x0B: r"\v", 0x0C: r"\f", 0x0D: r"\r",
    0x22: r"\"", 0x5C: "\\\\",
}


def _format_as_quoted_text(data: bytes) -> str:
    """Render bytes as a quoted escape-string.

    Printable ASCII passes through literally; common control bytes
    use familiar escape sequences (``\\n``, ``\\r``, ``\\t``, ...);
    everything else becomes ``\\xNN``.  Used as the ``--ascii``
    rendering for byte-dump commands.
    """
    pieces: list[str] = []
    for b in data:
        if b in _TEXT_ESCAPES:
            pieces.append(_TEXT_ESCAPES[b])
        elif 0x20 <= b < 0x7F:
            pieces.append(chr(b))
        else:
            pieces.append(f"\\x{b:02X}")
    return '"' + "".join(pieces) + '"'


def _display_bytes(
    ctx: PluginContext,
    direction: str,
    data: bytes,
    *,
    as_text: bool = False,
) -> None:
    """Display TX or RX data.

    Default: hex bytes followed by an ASCII sidebar (``|HELLO...|``)
    showing printable chars literally and ``.`` for non-printable --
    the standard hex-dump row, just on one line for short payloads
    (<=16 bytes).  Longer payloads use the multi-line
    :func:`format_hex_dump`.

    ``as_text=True``: render the payload as a single quoted
    escape-string (``TX: "AT\\r\\n"``).  Useful when you sent ASCII
    text and want to verify the textual content; commands that wire a
    ``--ascii`` flag pass it through here.

    Args:
        ctx: Plugin context for output.
        direction: Label prefix -- ``"TX"`` (cyan) or ``"RX"`` (yellow).
            Variants like ``"TX (dry-run)"`` also get TX coloring.
        data: Raw bytes to display.
        as_text: Switch to escape-rendered text view.
    """
    color = "cyan" if direction.startswith("TX") else "yellow"
    if as_text:
        ctx.io.output(f"  {direction}: {_format_as_quoted_text(data)}", color)
        return
    if len(data) <= 16:
        hex_str = format_hex(data)
        sidebar = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in data)
        ctx.io.output(f"  {direction}: {hex_str}  |{sidebar}|", color)
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
    line_ending = ctx.cfg.get("eol", "\r")
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
            elapsed_s = time.monotonic() - t0
            if script.strip_ansi:
                response = strip_ansi(response)

            ctx.io.output(f"  Expected: {format_spaced(tc.expect_data, tc.binary)}")
            if response:
                ctx.io.output(f"  Actual:   {format_spaced(response, tc.binary)}", "yellow")
                if match_response(tc.expect_data, response, tc.expect_mask):
                    ctx.io.output(
                        f"  PASS ({len(response)} bytes, {format_duration(elapsed_s)})",
                        "bright_green",
                    )
                    pass_count += 1
                else:
                    ctx.io.output("  FAIL", "red")
                    fail_count += 1
            else:
                ctx.io.output(
                    f"  Actual:   (timeout after {format_duration(tc.timeout_ms / 1000)})",
                    "red",
                )
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
                elapsed_s = time.monotonic() - t0
                if do_strip_ansi:
                    response = strip_ansi(response)

                ctx.io.output(f"  Expected: {format_spaced(step.data, step.binary)}")
                if response:
                    ctx.io.output(
                        f"  Actual:   {format_spaced(response, step.binary)}", "yellow"
                    )
                    if match_response(step.data, response, step.mask):
                        ctx.io.output(
                            f"  PASS ({len(response)} bytes, {format_duration(elapsed_s)})",
                            "bright_green",
                        )
                        pass_count += 1
                    else:
                        ctx.io.output("  FAIL", "red")
                        fail_count += 1
                else:
                    ctx.io.output(
                        f"  Actual:   (timeout after {format_duration(step.timeout_ms / 1000)})",
                        "red",
                    )
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
        raise UsageError()

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
    ascii_view = ctx.flag("--ascii")

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
                    parts.append(
                        _format_as_quoted_text(s) if ascii_view
                        else format_hex(s)
                    )
                else:
                    parts.append(f"[~{format_duration(s)}]")
            ctx.io.output(f"  TX (dry-run): {' '.join(parts)}")
        else:
            _display_bytes(ctx, "TX (dry-run)", all_data, as_text=ascii_view)
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
                        parts.append(f"[dim][~{format_duration(s)}][/]")
                ctx.io.output_markup(f"  [cyan]TX:[/] {' '.join(parts)}")
            else:
                _display_bytes(ctx, "TX", all_data, as_text=ascii_view)

        t0 = time.monotonic()
        for segment in segments:
            if isinstance(segment, float):
                _delay_at_least(segment)
            else:
                ctx.serial.write(segment)
        response = ctx.serial.read_raw(1000)
        elapsed_s = time.monotonic() - t0

    if response:
        _display_bytes(ctx, "RX", response, as_text=ascii_view)
        if ctx.output_level == "verbose":
            ctx.io.output(f"  ({len(response)} bytes, {format_duration(elapsed_s)})")
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
        raise UsageError()

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
        raise UsageError()

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
        flags["hex"] = True
        ctx.io.output("Hex display mode enabled.", "bright_green")
    elif arg == "off":
        flags["hex"] = False
        ctx.io.output("Hex display mode disabled.", "bright_green")
    else:
        flags["hex"] = not flags["hex"]
        state = "enabled" if flags["hex"] else "disabled"
        ctx.io.output(f"Hex display mode {state}.", "bright_green")
    # Mirror the echo/verbose convention: return the new state.
    return CmdResult.ok(value="on" if flags["hex"] else "off")


def _cmd_status(ctx: PluginContext, args: str) -> CmdResult:
    """Show current protocol mode state.

    Displays hex display mode and connection status.

    Args:
        ctx: Plugin context for state and output.
        args: Ignored.
    """
    hex_mode = ctx.ns("flags")["hex"]
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
        raise UsageError()

    entry = CRC_CATALOGUE.get(name)
    if entry is None:
        # Check if it's a plugin-only algorithm
        registry = get_crc_registry()
        if name in registry:
            alg = registry[name]
            ctx.io.output(f"  {name} (plugin, {alg.width * 8}-bit)")
            ctx.io.output("  No catalog parameters - loaded from plugin file.")
            return CmdResult.ok(value=name)
        ctx.io.output(f"Use '{p}proto.crc.list' to see available algorithms.")
        return CmdResult.fail(msg=f"Unknown algorithm: {name}{_did_you_mean(name)}")

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
        raise UsageError()

    name = parts[0].lower()

    registry = get_crc_registry()
    alg = registry.get(name)
    if alg is None:
        p = ctx.prefix
        ctx.io.output(f"Use '{p}proto.crc.list' to see available algorithms.")
        return CmdResult.fail(msg=f"Unknown algorithm: {name}{_did_you_mean(name)}")

    # No data provided - use the standard check string "123456789"
    check_mode = len(parts) < 2
    file_path: Path | None = None
    if check_mode:
        data = b"123456789"
        data_str = "123456789"
        is_hex = False
    else:
        data_str = parts[1]
        # A file argument reads host bytes (an existence/size/CRC oracle);
        # contain it to the sandbox under MCP.  Literal hex/text data is
        # not a path, so this is a no-op for the normal case.
        ctx.fs.guard_external_path(data_str, "CRC data path")
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

    # crcglot 0.23 advisory: a handful of algorithms (the IEEE crc32
    # family today) have a stdlib / canonical-package fast path that
    # beats any generated code by ~30x on CPU CRC instructions.  Flag
    # it as a status hint so high-volume users see the recommendation.
    from crcglot import ALGORITHMS, has_faster_alternative
    entry_info = ALGORITHMS.get(name)
    if entry_info is not None and has_faster_alternative(entry_info):
        ctx.io.status(
            f"  Note: a stdlib fast path exists for {name} (e.g. Python "
            f"zlib.crc32, ~30x faster than generated code)."
        )

    # In check mode, verify against the catalog's expected value
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
    "xorout", "name", "desc", "symbol", "style", "naming",
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


def _did_you_mean(name: str) -> str:
    """Build a "; did you mean: X, Y, Z" suffix for an unknown algorithm.

    Calls ``crcglot.suggest_algorithms`` (0.25+), which runs a
    three-tier match -- exact prefix, ``crc<width>`` family, then
    fuzzy.  Returns ``""`` when crcglot has no suggestion to offer
    (the caller appends the suffix unconditionally; an empty string
    is a no-op).
    """
    from crcglot import suggest_algorithms
    suggestions = suggest_algorithms(name, limit=4)
    if not suggestions:
        return ""
    return f"; did you mean: {', '.join(suggestions)}"


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

    * **Catalog lookup** (existing): ``<algorithm-name> [file=stem]
      [symbol=name]``.  Looks ``algorithm-name`` up in
      ``CRC_CATALOGUE``; ``symbol=`` overrides the default function
      name; ``file=`` writes to disk and (if no ``symbol=`` given)
      also sets the function name from the file basename.
    * **Custom CRC** (new): ``width=N poly=X [init=...] [refin=...]
      [refout=...] [xorout=...] [name=...] [desc=...] [file=stem]
      [symbol=name]``.  Builds a synthetic catalog entry from raw
      Rocksoft/Williams parameters and computes the check value via
      the same generic engine that drives the bundled catalog.

    Args:
        ctx: Plugin context for output.
        args: See above.
        lang: Target language (c, python, rust, vhdl).
    """
    from pathlib import Path

    from crcglot import AlgorithmInfo, Crc
    from termapy.protocol import GENERATORS, GENERATORS_FROM_ENTRY
    from termapy.protocol.crc import _generic_crc

    # Variant resolution: crcglot 0.10 introduced one ``variant=``
    # literal ("bitwise" / "table" / "slice8") in place of separate
    # boolean kwargs; 0.23 added ``"auto"`` and made it the upstream
    # default (picks the fastest implementation per (language,
    # width) -- slice8 at width 32/64, table at width >= 8, bitwise
    # for sub-byte).  termapy mirrors the upstream default so bare
    # /proto.crc.<lang> matches ``crcglot <lang>`` and ``--fast``.
    #
    # Two flag tiers map to the same variant axis:
    #
    #   --fast / --small         -- crcglot 0.23's user-facing vocabulary.
    #                              --fast forces ``auto`` (fastest the
    #                              language+width supports); --small forces
    #                              ``bitwise`` (smallest code).  Mutually
    #                              exclusive with each other AND with the
    #                              explicit --table / --slice8 below.
    #   --table / --slice8       -- explicit variant overrides for users
    #                              who know exactly which implementation
    #                              shape they want.  Mutually exclusive
    #                              with each other.
    #
    # No flag at all => ``auto`` (the upstream default).
    explicit = [
        f for f in ("--small", "--fast", "--table", "--slice8") if ctx.flag(f)
    ]
    if len(explicit) > 1:
        return CmdResult.fail(
            msg=f"Variant flags are mutually exclusive; got {', '.join(explicit)}"
        )
    if ctx.flag("--small"):
        variant = "bitwise"
    elif ctx.flag("--fast"):
        variant = "auto"
    elif ctx.flag("--slice8"):
        variant = "slice8"
    elif ctx.flag("--table"):
        variant = "table"
    else:
        variant = "auto"

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
    # (for catalog mode) is treated as the algorithm name.
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

    # Doc-comment style: crcglot 0.13+ exposes a per-language curated
    # set (Python: plain/google/numpy/rest; C/Java: doxygen/...; Rust:
    # rustdoc; TypeScript: jsdoc; etc).  Default ``plain`` is applied
    # by crcglot when the kwarg is omitted, so we only forward when the
    # user explicitly passes ``style=``.
    style = kv.get("style")
    if style == "":
        return CmdResult.fail(msg="style= requires a value")
    if style is not None:
        from crcglot.comments.registry import (
            comment_style_for,
            styles_for_language,
        )
        try:
            comment_style_for(lang, style)
        except ValueError:
            allowed = styles_for_language(lang)
            return CmdResult.fail(
                msg=f"Unknown style: {style} "
                f"(allowed for {lang}: {', '.join(allowed)})"
            )

    # Naming convention: crcglot 0.13+ exposes a per-language curated
    # set via ``LanguageInfo.naming`` (snake / camel / pascal subset)
    # with ``default_naming`` picking the idiomatic one (snake for
    # C/Rust/Python/Verilog/VHDL, pascal for Go/C#, camel for
    # Java/TypeScript).  Only forward when the user explicitly passes
    # ``naming=`` so crcglot's per-language default applies otherwise.
    naming = kv.get("naming")
    if naming == "":
        return CmdResult.fail(msg="naming= requires a value")
    if naming is not None:
        from crcglot import LANGUAGES
        allowed_naming = LANGUAGES[lang].naming if lang in LANGUAGES else frozenset()
        if naming not in allowed_naming:
            return CmdResult.fail(
                msg=f"Unknown naming: {naming} "
                f"(allowed for {lang}: {', '.join(sorted(allowed_naming))})"
            )

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
        if variant == "slice8":
            # Defer the slice8/width-32-or-64 rule to crcglot via
            # ``LanguageInfo.variants_for_width`` -- the rule lives
            # next to the generators that enforce it.
            from crcglot import LANGUAGES
            if "slice8" not in LANGUAGES[lang].variants_for_width(width):
                return CmdResult.fail(
                    msg=f"--slice8 requires width=32 or 64 (got width={width})"
                )

        # Compute the check value (CRC of "123456789") via the same
        # engine that powers the bundled catalog.  Embedded in the
        # generated _self_test so downstream users can verify too.
        # crcglot 0.23 narrowed generic_crc to take one Crc value
        # object instead of seven positional ints.
        check = _generic_crc(
            b"123456789",
            Crc(
                width=width, poly=poly, init=init,
                refin=refin, refout=refout, xorout=xorout,
            ),
        )

        custom_name = kv.get("name") or "crc_custom"
        # Unify with the catalog-lookup branch's ``name`` so the
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
        # always the dict key in the catalog); we still pass it
        # separately to ``gen_entry(custom_name, entry, ...)`` below.
        # 0.11 added a required ``source`` provenance string -- "reveng"
        # for catalog entries, free-form citation otherwise.  --custom
        # params are exactly that: the user.
        entry = AlgorithmInfo(
            width=width, poly=poly, init=init,
            refin=refin, refout=refout, xorout=xorout,
            check=check, desc=desc,
            source="user",
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
        gen_kwargs: dict[str, object] = {"symbol": symbol, "variant": variant}
        if style is not None:
            gen_kwargs["comment_style"] = style
        if naming is not None:
            gen_kwargs["naming"] = naming
        result = gen_entry(custom_name, entry, **gen_kwargs)
    else:
        # ----- Catalog lookup (existing path) -----
        names = [t.lower() for t in name_tokens]
        if not names:
            # Two-form synopsis (catalog XOR custom width=/poly=) -- documented
            # hand-rolled boundary (CLAUDE.md "params vs hand-rolled"); raise
            # UsageError would show only the single-form args= declaration.
            p = ctx.prefix
            return CmdResult.fail(
                msg=(
                    f"Usage: {p}proto.crc.{lang} <algorithm> {{name ...}} "
                    "{--table} {file=stem} {symbol=name} {style=STYLE}\n"
                    f"   or: {p}proto.crc.{lang} width=N poly=X "
                    "{init=...} {refin=...} {refout=...} {xorout=...} "
                    "{name=...} {file=...} {symbol=...} {style=STYLE}"
                )
            )

        gen = GENERATORS.get(lang)
        if gen is None:
            return CmdResult.fail(msg=f"Unknown language: {lang}")

        # Multi-algorithm bundle: call the generator once per algorithm,
        # then merge via the language's combiner.  Mirrors crcglot's
        # library-level "loop + combine" pattern (combine_<lang> in
        # crcglot.lang.<lang>).  symbol= is rejected because there's no
        # single symbol to override -- each bundled algorithm gets its
        # own default symbol from its catalog name.
        if len(names) > 1:
            if symbol_override is not None:
                return CmdResult.fail(
                    msg="symbol= not allowed when bundling multiple "
                    "algorithms (each algorithm keeps its own symbol)"
                )
            from crcglot import LANGUAGES
            if variant == "slice8":
                # ``variants_for_widths`` returns the intersection across
                # every member's allowed-variant set, so the slice8 +
                # width-32/64 rule applies bundle-wide for free.
                from termapy.protocol.crc import CRC_CATALOGUE
                widths = [
                    CRC_CATALOGUE[n]["width"]
                    for n in names if n in CRC_CATALOGUE
                ]
                if (
                    widths
                    and "slice8"
                    not in LANGUAGES[lang].variants_for_widths(widths)
                ):
                    bad = [w for w in sorted(set(widths)) if w not in (32, 64)]
                    return CmdResult.fail(
                        msg=f"--slice8 requires every bundled CRC to be "
                        f"width=32 or 64; got widths {bad}"
                    )
            combiner = LANGUAGES[lang].combiner
            gen_kwargs_each: dict[str, object] = {"variant": variant}
            if style is not None:
                gen_kwargs_each["comment_style"] = style
            if naming is not None:
                gen_kwargs_each["naming"] = naming
            parts = []
            for n in names:
                part = gen(n, **gen_kwargs_each)
                if part is None:
                    p = ctx.prefix
                    ctx.io.output(
                        f"Unknown algorithm: {n}. "
                        f"Use {p}proto.crc.list to see available.",
                        "red",
                    )
                    return CmdResult.fail(
                        msg=f"Unknown algorithm: {n}{_did_you_mean(n)}"
                    )
                parts.append(part)
            # Combined file stem: file= wins; otherwise let crcglot
            # derive a canonical one from the algorithm names.
            from crcglot import default_stem
            bundle_stem = file_stem or default_stem(names)
            result = combiner(parts, bundle_stem)
            name = bundle_stem
        else:
            name = names[0]

            # Symbol resolution: explicit > file basename > generator default.
            symbol = (
                symbol_override
                or (_symbol_from_stem(file_stem) if file_stem else None)
            )

            if variant == "slice8":
                # Defer the slice8/width rule to crcglot via
                # ``LanguageInfo.variants_for_width`` so the rule lives
                # with the generators that enforce it.
                from crcglot import LANGUAGES
                from termapy.protocol.crc import CRC_CATALOGUE
                entry = CRC_CATALOGUE.get(name)
                if entry is not None and (
                    "slice8"
                    not in LANGUAGES[lang].variants_for_width(entry["width"])
                ):
                    return CmdResult.fail(
                        msg=f"--slice8 requires width=32 or 64; {name} is "
                        f"width={entry['width']}"
                    )
            gen_kwargs_single: dict[str, object] = {
                "symbol": symbol, "variant": variant,
            }
            if style is not None:
                gen_kwargs_single["comment_style"] = style
            if naming is not None:
                gen_kwargs_single["naming"] = naming
            result = gen(name, **gen_kwargs_single)
            if result is None:
                p = ctx.prefix
                ctx.io.output(
                    f"Unknown algorithm: {name}. "
                    f"Use {p}proto.crc.list to see available.",
                    "red",
                )
                return CmdResult.fail(
                    msg=f"Unknown algorithm: {name}{_did_you_mean(name)}"
                )

    # ----- File output mode (file=STEM) -----
    if file_stem is not None:
        # Writing generated source to the cwd is a host-filesystem write
        # outside the config sandbox; refuse under the MCP sandbox (the
        # inline/stdout mode below still works for a confined peer).
        if not ctx.capabilities.filesystem_unconfined:
            return CmdResult.fail(
                msg=(
                    "Writing generated files to disk is disabled under the "
                    "MCP sandbox. Omit file= to get the source inline, or set "
                    "TERMAPY_MCP_FS_UNCONFINED=1 in the server's shell."
                )
            )
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

    ``bin=`` and ``asc=`` each consume everything after them to end
    of line (a captured packet may contain spaces and can't be split
    by whitespace).  ``width=`` and ``endian=`` are single-token
    filters and must come before the bin/asc argument.

    Returns a dict with keys ``mode`` ("bin" or "asc"), ``payload``
    (the captured packet string), and optionally ``width`` /
    ``endian``.  Returns empty dict if neither bin= nor asc= is
    present.
    """
    result: dict[str, str] = {}
    stripped = text.strip()
    for key in ("bin", "asc", "cmd"):
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
        elif tok.startswith("form="):
            result["form"] = tok[len("form="):]
    return result


def _crc_find(ctx: PluginContext, args: str) -> CmdResult:
    """Identify the CRC algorithm used in a captured packet.

    Two input modes:

      - ``bin=<hex>``  -- a captured frame as hex bytes.
      - ``asc=<text>`` -- a packet whose trailing chars are the
        hex-encoded CRC.

    The actual identification work is ``crcglot.detect``; termapy
    just shuttles bytes in and formats the result.  Both modes
    require a real captured frame -- there's no live "send and
    detect" mode because constructing a valid query already requires
    knowing the CRC algorithm (chicken/egg), so capture-from-sniffer
    is the universally applicable path.
    """
    kw = _parse_find_args(args)
    if "mode" not in kw:
        raise UsageError()

    try:
        width_filter = int(kw["width"]) if "width" in kw else None
    except ValueError:
        return CmdResult.fail(msg="Invalid width: must be 8, 16, 32, or 64")
    if width_filter is not None and width_filter not in (8, 16, 32, 64):
        return CmdResult.fail(msg="Invalid width: must be 8, 16, 32, or 64")

    endian_filter = kw.get("endian", "").lower()
    if endian_filter and endian_filter not in ("be", "le"):
        return CmdResult.fail(msg="Invalid endian: must be be or le")
    # crcglot's detect uses "big" / "little" / "both" -- map from the
    # shorter "be" / "le" that termapy exposes to users.
    endian_arg: Literal["big", "little", "both"]
    if endian_filter == "be":
        endian_arg = "big"
    elif endian_filter == "le":
        endian_arg = "little"
    else:
        endian_arg = "both"

    # Payload form: crcglot 0.23+ recognizes named wrappers (e.g.
    # ``crclink`` JSON frames).  Pass ``form=`` straight through; on
    # an unknown form, validate up-front with a clear list of what
    # crcglot ships so the user can pick.
    form = kw.get("form")
    if form == "":
        return CmdResult.fail(msg="form= requires a value")
    if form is not None:
        from crcglot import FORMATS
        if form not in FORMATS:
            return CmdResult.fail(
                msg=f"Unknown form: {form} "
                f"(known: {', '.join(sorted(FORMATS)) or '(none)'})"
            )

    mode = kw["mode"]
    if mode == "bin":
        tokens = kw["payload"].split()
        try:
            packet: bytes = bytes(int(t, 16) for t in tokens)
        except ValueError:
            return CmdResult.fail(msg="Invalid hex bytes in bin=")
        if form is not None:
            # Form matching runs a text regex over the packet, so we
            # decode the bytes first and let detect's auto-mode pick
            # text.  Defaulting to UTF-8 matches crclink (and every
            # form that ships today); a future non-UTF-8 form would
            # need its own encoding plumbed through here.
            try:
                packet_text = packet.decode("utf-8")
            except UnicodeDecodeError:
                return CmdResult.fail(
                    msg=f"form={form} requires UTF-8 decodable bytes"
                )
            result = detect(
                packet_text, match="all",
                endian=endian_arg, form=form,
            )
        else:
            result = detect(
                packet, mode="binary", match="all", endian=endian_arg,
            )
        return _render_detect_result(
            ctx, result, len(packet), width_filter, is_text=False,
        )

    if mode == "cmd":
        # Send a trigger, capture the response, detect on it.  Useful
        # when the device responds to a plain trigger (the demo's
        # AT+RND, NMEA talkers, debug consoles, test equipment).  NOT
        # useful against a strict CRC-validating slave where the
        # trigger itself needs a valid CRC -- you'd already know the
        # algorithm in that case.  Revived from commit c06476a.
        if form is not None:
            return CmdResult.fail(
                msg="form= is not compatible with cmd= "
                "(form= matches a wrapped frame; cmd= captures raw bytes -- "
                "decode the captured response with form= via bin= afterwards)"
            )
        if not ctx.serial.is_connected():
            return CmdResult.fail(msg="Not connected.")
        send = kw["payload"]
        if not send:
            return CmdResult.fail(msg="Empty cmd= payload")
        captured = _send_and_capture(ctx, [_encode_device_cmd(ctx, send)])
        if captured is None:
            return CmdResult.fail(msg="No response within timeout")
        result = detect(
            captured, mode="binary", match="all", endian=endian_arg,
        )
        return _render_detect_result(
            ctx, result, len(captured), width_filter, is_text=False,
        )

    # mode == "asc"
    if form is not None:
        return CmdResult.fail(
            msg="form= is not compatible with asc= "
            "(asc= splits trailing hex; form= matches a full wrapper -- "
            "pass the wrapped frame via bin= instead)"
        )
    text = kw["payload"]
    if not text:
        return CmdResult.fail(msg="Empty asc= payload")
    result = _detect_ascii(text, endian_arg, width_filter)
    return _render_detect_result(
        ctx, result, len(text), width_filter, is_text=True,
    )


def _encode_device_cmd(ctx: PluginContext, text: str) -> bytes:
    """Encode a ``cmd=`` trigger for find/reverse, sent verbatim as a command.

    The trigger goes out exactly as if typed at the prompt: the configured line
    ending is appended when it isn't already present.  That is why ``cmd=`` takes
    a bare command (``cmd=AT+RND.CUSTOM``) with no quoting and no explicit ``\\r``.
    """
    line_ending = ctx.cfg.get("eol", "\r")
    if not text.endswith(line_ending):
        text += line_ending
    return text.encode(ctx.cfg.get("encoding", "utf-8"))


def _send_and_capture(
    ctx: PluginContext,
    segments: list,
    timeout_ms: int = 1000,
) -> bytes | None:
    """Issue a send-spec on the port and read back one framed response.

    Shared helper for the cmd= modes of /proto.crc.find and
    /proto.crc.reverse.  ``segments`` is the parse_data_segments
    output -- a mix of ``bytes`` (data) and ``float`` (inline delay
    in seconds), same shape /proto.send consumes.  Returns the
    captured bytes, or None when nothing arrives within ``timeout_ms``.

    A single trailing CRLF (or bare LF) is stripped before returning
    -- text-protocol responses (AT, NMEA, the demo's AT+RND) framed
    that way come back ready for direct CRC analysis.  Binary frames
    that happen to end with 0x0D 0x0A would lose two bytes, but the
    1-in-65536 coincidence is rare enough to be the right default.
    """
    import time
    with ctx.serial.io():
        for segment in segments:
            if isinstance(segment, float):
                time.sleep(segment)
            else:
                ctx.serial.write(segment)
        captured = ctx.serial.read_raw(timeout_ms)
    if not captured:
        return None
    if captured.endswith(b"\r\n"):
        captured = captured[:-2]
    elif captured.endswith(b"\n"):
        captured = captured[:-1]
    return captured if captured else None


def _crc_verify(ctx: PluginContext, args: str) -> CmdResult:
    """Verify that a packet's trailing CRC matches the named algorithm.

    Use when the algorithm is known and you just want a yes/no check on
    a captured frame.  Use ``/proto.crc.find`` when the algorithm is
    unknown.  Endianness defaults to big-endian (the convention crcglot
    ``encode`` and ``verify`` use); pass ``endian=le`` for low-byte-first
    trailers like Modbus.
    """
    p = ctx.prefix
    parts = args.strip().split(None, 1)
    if len(parts) < 2:
        raise UsageError()
    name = parts[0].lower()
    rest = parts[1]

    # Optional endian= prefix; default big to match crcglot's verify().
    endianness: Literal["big", "little"] = "big"
    endian_tokens: tuple[tuple[str, Literal["big", "little"]], ...] = (
        ("endian=be", "big"),
        ("endian=le", "little"),
    )
    for token, value in endian_tokens:
        if rest.startswith(token + " "):
            endianness = value
            rest = rest[len(token):].strip()
            break

    try:
        packet = bytes(int(t, 16) for t in rest.split())
    except ValueError:
        return CmdResult.fail(msg="Invalid hex bytes")
    if not packet:
        return CmdResult.fail(msg="No data")

    from crcglot import ALGORITHMS, verify
    algo = ALGORITHMS.get(name)
    if algo is None:
        ctx.io.output(f"Use '{p}proto.crc.list' to see available algorithms.")
        return CmdResult.fail(msg=f"Unknown algorithm: {name}{_did_you_mean(name)}")

    width_bytes = (algo.width + 7) // 8
    if len(packet) <= width_bytes:
        return CmdResult.fail(
            msg=f"Packet too short: {len(packet)} bytes <= "
            f"{width_bytes}-byte {name} CRC field"
        )

    result = verify(packet, algo, endianness=endianness)
    hex_w = width_bytes * 2
    if result.valid:
        ctx.io.result_markup(
            f"  [green]OK[/]  {name}  endian={endianness}  "
            f"crc=0x{result.actual:0{hex_w}X}  ({len(packet) - width_bytes} "
            f"data bytes)"
        )
        return CmdResult.ok(value="ok")
    ctx.io.result_markup(
        f"  [red]MISMATCH[/]  {name}  endian={endianness}  "
        f"expected=0x{result.expected:0{hex_w}X}  "
        f"actual=0x{result.actual:0{hex_w}X}"
    )
    ctx.io.output(
        f"  Try other endian (endian={'le' if endianness == 'big' else 'be'}) "
        f"or {p}proto.crc.find to identify the actual algorithm."
    )
    return CmdResult.fail(msg="CRC mismatch")


def _crc_reverse(ctx: PluginContext, args: str) -> CmdResult:
    """Recover the Rocksoft parameters of an unknown CRC algorithm.

    Wraps ``crcglot.reverse_packets`` with ``std_algo_only=False`` so
    algebraic recovery actually runs (the default catalog-only
    behavior is what /proto.crc.find does; reverse's whole point is
    handling the non-catalog case).

    Two invocation modes:

    * Explicit packets -- 2+ captured packets as hex bytes:
        /proto.crc.reverse [crc_bytes=N] [width=N] <p1-hex> <p2-hex> ...
    * Capture mode -- issue a trigger N times against a connected
      device and reverse on the responses:
        /proto.crc.reverse cmd=<trigger> count=<N> [crc_bytes=N] [width=N]

    On success, prints the recovered Rocksoft params AND returns them
    as a copy-pasteable ``width=N poly=0xP init=... refin=... refout=...
    xorout=... name=recovered`` kv string via ``CmdResult.ok(value=...)``
    so the ``$(rev) <- /proto.crc.reverse ...`` capture syntax pipes
    straight into ``/proto.crc.<lang> $(rev)`` codegen.
    """

    # Parse the args.  kv tokens (crc_bytes, width, count) are whitespace-
    # separated and can appear anywhere.  cmd= eats to end-of-line like
    # /proto.crc.find's cmd= -- it must therefore be the LAST token, but
    # any preceding kv tokens are parsed first so users can write either
    # ``cmd=...`` at the end OR ``count=N cmd=...`` etc.  Remaining
    # non-kv tokens are explicit packet hex.
    kv: dict[str, str] = {}
    rest_text = args.strip()
    cmd_payload: str | None = None
    cmd_idx = rest_text.find("cmd=")
    if cmd_idx != -1 and (cmd_idx == 0 or rest_text[cmd_idx - 1].isspace()):
        cmd_payload = rest_text[cmd_idx + len("cmd="):].strip()
        rest_text = rest_text[:cmd_idx].strip()
    rest_tokens: list[str] = []
    for tok in rest_text.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            if k in ("crc_bytes", "width", "count"):
                kv[k] = v
                continue
        rest_tokens.append(tok)
    # In cmd= mode, count= might also have appeared INSIDE the cmd payload
    # because cmd= ate the rest of the line.  Pull it back out if so:
    # the trigger usually doesn't contain "count=" as legitimate data.
    if cmd_payload is not None and " count=" in (" " + cmd_payload):
        # Split off the trailing "count=N [crc_bytes=N] [width=N]" tail.
        words = cmd_payload.split()
        body_words: list[str] = []
        for w in words:
            if "=" in w and w.split("=", 1)[0] in (
                "count", "crc_bytes", "width",
            ):
                k, v = w.split("=", 1)
                kv[k] = v
            else:
                body_words.append(w)
        cmd_payload = " ".join(body_words)

    # Validate the kv ints.
    try:
        crc_bytes = int(kv["crc_bytes"]) if "crc_bytes" in kv else None
        width = int(kv["width"]) if "width" in kv else None
        count = int(kv["count"]) if "count" in kv else None
    except ValueError:
        return CmdResult.fail(msg="Invalid integer in crc_bytes / width / count")

    # Build the packet list -- one path per mode.
    packets: list[bytes] = []
    if cmd_payload is not None:
        if not cmd_payload:
            return CmdResult.fail(msg="Empty cmd= payload")
        if count is None or count < 2:
            return CmdResult.fail(
                msg=(
                    "cmd= mode requires count=N (>=2). "
                    'Try: /proto.crc.reverse cmd="<trigger>\\r" '
                    "count=13 crc_bytes=<width-in-bytes>"
                )
            )
        if not ctx.serial.is_connected():
            return CmdResult.fail(msg="Not connected.")
        segments = [_encode_device_cmd(ctx, cmd_payload)]
        for _ in range(count):
            captured = _send_and_capture(ctx, segments)
            if captured is None:
                return CmdResult.fail(
                    msg=f"No response on capture #{len(packets) + 1} of {count}"
                )
            packets.append(captured)
    else:
        if len(rest_tokens) < 2:
            raise UsageError()
        try:
            for tok in rest_tokens:
                packets.append(bytes.fromhex(tok))
        except ValueError:
            return CmdResult.fail(msg="Invalid hex bytes in packet")
        if len(packets) < 2:
            return CmdResult.fail(msg="Need at least 2 packets to reverse")

    # Dispatch to crcglot.reverse_packets.  Explicit branches instead of
    # ``**kwargs`` so ty narrows the kwarg types correctly.
    from crcglot import reverse_packets
    try:
        result = reverse_packets(
            packets,
            crc_bytes=crc_bytes,
            width=width,
            std_algo_only=False,
        )
    except ValueError as e:
        return CmdResult.fail(msg=f"Reverse error: {e}")

    if result.status in ("none", "underdetermined") or not result.candidates:
        ctx.io.result(
            f"  No model recovered (status={result.status})", "red",
        )
        if result.note:
            ctx.io.output(f"  {result.note}")
        return CmdResult.fail(msg=f"Reverse {result.status}")

    # Canonical (first) candidate -- feeds the $(rev) pipeline value below.
    c = result.candidates[0]
    catalog_str = (
        f"  catalog: {result.catalogue_name}"
        if result.catalogue_name else ""
    )
    # Display EVERY candidate.  For an ``equivalent`` result these are the
    # (init, xorout) labelings that reproduce the captured codewords
    # identically (same poly/refin/refout); the note below explains the class.
    for cand in result.candidates:
        w = (cand.width + 3) // 4
        ctx.io.result_markup(
            f"  [green]{result.status}[/]  "
            f"width={cand.width}  poly=0x{cand.poly:0{w}X}  "
            f"init=0x{cand.init:0{w}X}  refin={cand.refin}  refout={cand.refout}  "
            f"xorout=0x{cand.xorout:0{w}X}{catalog_str}"
        )
    if result.note:
        ctx.io.output(f"  {result.note}")

    # Return a copy-pasteable kv string for the $(rev) <- ... pipeline.
    kv_value = (
        f"width={c.width} poly=0x{c.poly:X} init=0x{c.init:X} "
        f"refin={str(c.refin).lower()} refout={str(c.refout).lower()} "
        f"xorout=0x{c.xorout:X} name=recovered"
    )
    return CmdResult.ok(value=kv_value)


def _detect_ascii(
    text: str,
    endian_arg: Literal["big", "little", "both"],
    width_filter: int | None,
) -> DetectResult:
    """Identify the CRC in an ``asc=``-style ASCII payload.

    Termapy's asc= convention: the last ``2N`` chars are a hex-encoded
    N-byte CRC, the rest is data.  crcglot.detect's text mode wants an
    explicit ``data <sep> hex`` separator, which the convention skips,
    so this helper slices the trailing hex off for each plausible
    width, encodes the head as UTF-8, and shuttles the binary
    equivalent through ``detect``.  Pure data prep; detection itself
    still goes upstream.
    """
    widths_bytes = (width_filter // 8,) if width_filter else (1, 2, 4, 8)
    seen: set[str] = set()
    candidates: list = []
    for w in widths_bytes:
        hex_len = w * 2
        if len(text) <= hex_len:
            continue
        try:
            crc_int = int(text[-hex_len:], 16)
        except ValueError:
            continue
        packet = text[:-hex_len].encode("utf-8") + crc_int.to_bytes(w, "big")
        result = detect(packet, mode="binary", match="all", endian=endian_arg)
        for m in result.candidates:
            if m.algorithm not in seen:
                seen.add(m.algorithm)
                candidates.append(m)
    return DetectResult(matched=bool(candidates), candidates=tuple(candidates))


def _render_detect_result(
    ctx: PluginContext,
    result: DetectResult,
    packet_len: int,
    width_filter: int | None,
    is_text: bool,
) -> CmdResult:
    """Render a ``crcglot.detect`` result to the terminal.

    ``width_filter`` (None or 8/16/32/64) post-filters the candidate
    list -- crcglot's ``algorithms`` glob would work, but a tiny
    explicit filter sidesteps fnmatch corner cases.  ``is_text`` is
    True for ``asc=`` input (the trailing field is hex-encoded, so the
    "data" size is ``packet_len - 2 * width_bytes`` characters); False
    for binary input (``packet_len - width_bytes`` bytes).
    """
    candidates = result.candidates
    if width_filter is not None:
        candidates = tuple(m for m in candidates if m.info.width == width_filter)

    # Output-level model for /proto.crc.find:
    #   result()  -- the *answer*: the match line(s), or "No matches found."
    #   output()  -- supporting context (the explanation for the 0-case).
    #   status()  -- educational hints (codegen tip, multi-match advice),
    #                shown only under --verbose.
    # The old "1 match:" / "N matches:" header was noise; the count is
    # apparent from how many match lines follow.

    if not candidates:
        # crcglot 0.23 exposes ``DetectResult.trailer_hint`` -- a
        # TrailerResult that fires when no CRC matched but the trailing
        # bytes look like a non-CRC trailer (Adler-32, Fletcher, the
        # Internet checksum, MD5/SHA/BLAKE2 full or truncated).  Surface
        # it as an answer rather than the generic "no match" so the user
        # has a concrete next step.  Identification only -- crcglot does
        # not generate code for these.  ``trailer_hint`` is None when
        # the asc= helper synthesises a DetectResult directly.
        trailer_hint = getattr(result, "trailer_hint", None)
        if trailer_hint is not None and trailer_hint.matched:
            top = trailer_hint.candidates[0]
            trunc = (
                f"{top.truncated_to}-byte prefix of "
                if top.truncated_to else ""
            )
            ctx.io.result_markup(
                f"  [yellow]No CRC match. Looks like {trunc}{top.info.label}[/]"
                f"  ({top.info.kind}, width={top.info.width})"
            )
            ctx.io.output(f"  {top.info.description}")
            if len(trailer_hint.candidates) > 1:
                others = ", ".join(
                    m.info.label for m in trailer_hint.candidates[1:]
                )
                ctx.io.output(f"  Also consistent: {others}")
            ctx.io.status(
                "  Note: crcglot does not generate code for non-CRC trailers."
            )
            return CmdResult.ok(value="")

        ctx.io.result("  No matches found.", "red")
        ctx.io.output(
            "  Packet may use a non-standard algorithm, a CRC field that"
        )
        ctx.io.output(
            "  isn't trailing, or be too short to identify."
        )
        return CmdResult.ok(value="")

    p = ctx.prefix
    for m in candidates:
        width_bits = m.info.width
        width_bytes = (width_bits + 7) // 8
        endian_str = (
            f"  endian={m.endianness}" if m.endianness is not None else ""
        )
        data_len = packet_len - (width_bytes * 2 if is_text else width_bytes)
        unit = "chars" if is_text else "bytes"
        # crcglot 0.23+ tags the match's ``padding`` with the surface
        # formatting that wrapped the bytes -- ``FormatMatch`` for a
        # named form (e.g. a crclink JSON frame), ``TextFormat`` /
        # ``HexFormat`` for surface text/hex packets, ``None`` for a
        # plain binary packet.  Surface a ``form=`` tag when one of the
        # named forms matched so the user sees the wrapper without
        # parsing FormatMatch themselves.
        padding = getattr(m, "padding", None)
        form_str = (
            f"  form={padding.info.name}"
            if padding is not None and hasattr(padding, "info")
            and hasattr(padding.info, "name")
            else ""
        )
        ctx.io.result_markup(
            f"  [cyan]{m.algorithm}[/]  "
            f"width={width_bits}  field=last{width_bytes}"
            f"{endian_str}{form_str}  data={data_len} {unit}"
        )
        if form_str and padding is not None:
            ctx.io.output(
                f"  Wrapper: {padding.info.label}; "
                f"message={padding.message!r}"
            )
    if len(candidates) == 1:
        name = candidates[0].algorithm
        ctx.io.status("")
        ctx.io.status(
            f"  Generate source: {p}proto.crc.c {name}  "
            f"(or .python / .rust)"
        )
    else:
        ctx.io.status("")
        ctx.io.status(
            "  Multiple matches usually means the packet is too short to"
        )
        ctx.io.status(
            "  disambiguate.  Capture a second packet with a different CRC"
        )
        ctx.io.status(
            "  and intersect the match sets to narrow down."
        )
    return CmdResult.ok(
        value=candidates[0].algorithm if len(candidates) == 1 else ""
    )


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
  {prefix}proto.crc.list              - list all 100+ algorithms
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
    # The catalog size belongs to crcglot; the help text says "100+"
    # rather than an exact count so it never drifts from the upstream
    # catalog.  The exact, filter-aware tally lives in /proto.crc.list.
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
    # --fast / --small are crcglot 0.23's user-facing vocabulary:
    # always available because they map straight onto the auto / bitwise
    # variants every language supports, regardless of whether table or
    # slice8 are emitted.  --fast is the explicit form of the default.
    flags["--fast"] = (
        "Emit the fastest variant this language+width supports "
        "(default; same as no flag)."
    )
    flags["--small"] = "Emit the smallest implementation (bit-by-bit)."
    if has_table:
        flags["--table"] = "Pin to 256-entry lookup table (4-8x faster than bitwise)."
    if has_slice8:
        flags["--slice8"] = (
            "Pin to slice-by-8 (8 tables, 5-10x faster than --table for "
            "CRC-32/64 on large buffers). Width 32 or 64 only."
        )
    elif has_table:
        # Table exists but no native slice-by-8: accept --slice8 and
        # fall back to --table (the fallback note is emitted at dispatch).
        flags["--slice8"] = (
            "Accepted but falls back to --table (no native slice-by-8 "
            f"for {info.display_name})."
        )

    # Doc-comment styles per language (crcglot 0.13+).  ``plain`` is
    # always available; the rest are language-specific (e.g. doxygen
    # for C/Java, rustdoc for Rust, google/numpy/rest for Python).
    from crcglot.comments.registry import styles_for_language
    styles = tuple(styles_for_language(code))
    styles_line = (
        f"Doc-comment styles: {', '.join(styles)} (default: plain). "
        f"Pass via style=NAME.\n"
        if len(styles) > 1
        else ""
    )

    # Naming convention (crcglot 0.13+ via ``LanguageInfo.naming``):
    # snake / camel / pascal subset per language, with one idiomatic
    # default emitted when ``naming=`` is omitted.  Only advertise when
    # the language offers more than one option.
    naming_options = tuple(sorted(info.naming))
    naming_line = (
        f"Naming conventions: {', '.join(naming_options)} "
        f"(default: {info.default_naming}). Pass via naming=NAME.\n"
        if len(naming_options) > 1
        else ""
    )

    example_lines = [
        f"  {{prefix}}proto.crc.{code} crc16-modbus",
        f"  {{prefix}}proto.crc.{code} crc32 --small",
    ]
    if has_table:
        example_lines.append(f"  {{prefix}}proto.crc.{code} crc32 --table")
    if has_slice8:
        example_lines.append(f"  {{prefix}}proto.crc.{code} crc32 --slice8")
    example_lines.append(f"  {{prefix}}proto.crc.{code} crc16-modbus file=my_crc")
    # Doc-style + bundling examples only when the language supports
    # multiple styles (avoids noise on verilog/vhdl which are plain-only).
    if len(styles) > 1:
        non_plain = next((s for s in styles if s != "plain"), None)
        if non_plain is not None:
            example_lines.append(
                f"  {{prefix}}proto.crc.{code} crc32 style={non_plain}"
            )
    if len(naming_options) > 1:
        non_default = next(
            (n for n in naming_options if n != info.default_naming), None,
        )
        if non_default is not None:
            example_lines.append(
                f"  {{prefix}}proto.crc.{code} crc32 naming={non_default}"
            )
    example_lines.append(
        f"  {{prefix}}proto.crc.{code} crc16-modbus crc32 crc8 file=my_crcs"
    )

    long_help = (
        f"Prints a self-contained {info.display_name} implementation for the\n"
        f"named CRC. Use {{prefix}}proto.crc.list to see every available\n"
        f"algorithm name, or {{prefix}}proto.crc.info <name> for its parameters.\n"
        f"\n"
        f"With ``file=STEM``, writes {ext_desc} to the current directory\n"
        f"instead of stdout.\n"
        f"\n"
        f"Pass two or more algorithm names to bundle them into a single\n"
        f"output (each algorithm keeps its own symbol; ``symbol=`` is not\n"
        f"allowed in bundle mode).\n"
        f"\n"
        + styles_line
        + naming_line +
        "Example:\n"
        + "\n".join(example_lines)
    )

    return Command(
        args=(
            "<name> {name ...} {file=stem} {symbol=name} "
            "{style=name} {naming=name}"
        ),
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
            args=(
                '{algo{_le|_be}{_ascii}} <hex|"text"|~delay ...> '
                "{--dry-run} {--ascii}"
            ),
            help="Send raw bytes (with optional CRC), show response.",
            handler=_cmd_send,
            flags={
                "--dry-run": (
                    "Print the bytes that would be sent without writing "
                    "to the port (works without a connected device)."
                ),
                "--ascii": (
                    "Render TX/RX as a quoted escape-string (e.g. "
                    '"AT\\r\\n") instead of the default hex + ASCII '
                    "sidebar.  Use when you're sending text."
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
                "More than 100 named CRC algorithms come from the reveng CRC\n"
                "catalog maintained by Greg Cook since 1999:\n"
                "  https://reveng.sourceforge.io/crc-catalogue/all.htm\n"
                "\n"
                "Every algorithm is verified against its catalog check\n"
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
                    args=(
                        "{width=8|16|32|64} {endian=be|le} {form=NAME} "
                        "bin=<hex>|asc=<text>"
                    ),
                    help="Identify the CRC algorithm used in a captured packet.",
                    long_help=(
                        "Identifies which catalog algorithm produced the\n"
                        "trailing CRC bytes of a packet, via crcglot.detect.\n"
                        "Feed in a real captured frame (sniffer, scope, etc.):\n"
                        "constructing a valid query already requires knowing\n"
                        "the CRC algorithm, so the capture-from-the-wire path\n"
                        "is the universally applicable workflow.\n"
                        "\n"
                        "bin=<hex>     - hex bytes, last 1/2/4 bytes = CRC field\n"
                        "asc=<text>    - ASCII, last 2/4/8 chars = hex-encoded CRC\n"
                        "cmd=<trigger> - send a trigger to the device, capture the\n"
                        "                response, detect on it.  Useful with\n"
                        "                devices that respond to a plain trigger\n"
                        "                (the demo's AT+RND, NMEA talkers, debug\n"
                        "                consoles); NOT useful for strict CRC-\n"
                        "                validating slaves where the trigger\n"
                        "                itself needs a valid CRC.\n"
                        "\n"
                        "Optional filters:\n"
                        "  width=8|16|32|64  restrict CRC width\n"
                        "  endian=be|le      restrict byte order\n"
                        "  form=NAME         the packet is wrapped in a named\n"
                        "                    form (e.g. form=crclink for a\n"
                        "                    crclink JSON frame); bin= only.\n"
                        "\n"
                        "Examples:\n"
                        "  {prefix}proto.crc.find bin=01 03 00 00 00 0A C5 CD\n"
                        "  {prefix}proto.crc.find asc=123456789 width=16\n"
                        "  {prefix}proto.crc.find form=crclink bin=7B 22 74 22 3A 31 32 33 34 ...\n"
                        "  {prefix}proto.crc.find cmd=AT+RND\n"
                    ),
                    handler=_crc_find,
                ),
                # Generated entirely from crcglot.VERBS['detect'] -- params, help,
                # and execution (via crcglot.call_verb) all come from crcglot's
                # manifest, so there is nothing to keep in sync by hand.  See
                # _crc_verbs.build_crc_verb_command.
                "detect": build_crc_verb_command("detect"),
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
                "verify": Command(
                    args="<name> {endian=be|le} <hex bytes>",
                    help="Verify a packet's trailing CRC against a known algorithm.",
                    long_help=(
                        "Peels the trailing CRC field off a captured packet,\n"
                        "recomputes the CRC over the message, and reports OK or\n"
                        "MISMATCH.  Use when the algorithm is known and you just\n"
                        "want a yes/no check; use {prefix}proto.crc.find when the\n"
                        "algorithm is unknown.  Endianness defaults to big-endian;\n"
                        "pass endian=le for low-byte-first trailers like Modbus.\n"
                        "\n"
                        "Examples:\n"
                        "  {prefix}proto.crc.verify crc32 31 32 33 34 35 36 37 38 39 CB F4 39 26\n"
                        "  {prefix}proto.crc.verify crc16-modbus endian=le 01 03 00 00 00 0A C5 CD"
                    ),
                    handler=_crc_verify,
                ),
                "reverse": Command(
                    args=(
                        "{crc_bytes=N} {width=N} "
                        "<packet-hex> <packet-hex>...|cmd=<trigger> count=<N>"
                    ),
                    help="Recover the Rocksoft parameters of an unknown CRC.",
                    long_help=(
                        "Algebraic recovery via crcglot.reverse_packets.  Two\n"
                        "modes:\n"
                        "\n"
                        "  Explicit packets -- 2+ captured packets as hex bytes,\n"
                        "  pasted from a sniffer or scope log.  Needs at least\n"
                        "  two frames of the same length plus one of a different\n"
                        "  length so crcglot can pin the polynomial AND separate\n"
                        "  init from xorout.\n"
                        "\n"
                        "  Capture mode -- send a trigger N times against a\n"
                        "  connected device and reverse on the responses.  The\n"
                        "  demo's AT+RND.CUSTOM is designed for this: it emits\n"
                        "  packets at a deterministic length pattern so count=13\n"
                        "  reliably recovers the secret polynomial.\n"
                        "\n"
                        "On success, returns the recovered params as a kv string\n"
                        "via CmdResult.value, so it pipes straight into codegen:\n"
                        "\n"
                        "  $(rev) <- {prefix}proto.crc.reverse cmd=AT+RND.CUSTOM count=13 crc_bytes=2\n"
                        "  {prefix}proto.crc.c $(rev)\n"
                        "\n"
                        "Example (explicit):\n"
                        "  {prefix}proto.crc.reverse crc_bytes=2 010203AA55 040506BB66 0708CCAA"
                    ),
                    handler=_crc_reverse,
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
