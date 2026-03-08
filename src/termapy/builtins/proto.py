"""Built-in plugin: binary protocol send/expect testing."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.protocol import (
    ProtoScript,
    format_hex,
    format_hex_dump,
    format_smart,
    format_spaced,
    load_proto_script,
    match_response,
    parse_data,
    parse_proto_script,
    parse_toml_script,
    strip_ansi,
)

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

NAME = "proto"
ARGS = "<subcommand> [args]"
HELP = "Binary protocol tools: send, run, hex, status."

# Subcommand dispatch table (populated at module level)
_SUBCMDS: dict[str, str] = {
    "send": "send <hex|\"text\">  — send raw bytes, show response",
    "run": "run <file.pro>      — run a protocol test script",
    "debug": "debug <file.pro>    — interactive protocol debug screen",
    "hex": "hex {on|off}        — toggle hex display mode",
    "status": "status              — show current proto state",
}


def handler(ctx: PluginContext, args: str) -> None:
    """Dispatch proto subcommands for binary protocol testing."""
    parts = args.strip().split(None, 1)
    if not parts:
        _show_usage(ctx)
        return

    subcmd = parts[0].lower()
    sub_args = parts[1] if len(parts) > 1 else ""

    if subcmd == "send":
        _cmd_send(ctx, sub_args)
    elif subcmd == "run":
        _cmd_run(ctx, sub_args)
    elif subcmd == "debug":
        _cmd_debug(ctx, sub_args)
    elif subcmd == "hex":
        _cmd_hex(ctx, sub_args)
    elif subcmd == "status":
        _cmd_status(ctx)
    else:
        ctx.write(f"Unknown subcommand: {subcmd}", "red")
        _show_usage(ctx)


def _show_usage(ctx: PluginContext) -> None:
    """Display proto subcommand help."""
    prefix = ctx.engine.prefix
    ctx.write(f"Usage: {prefix}proto <subcommand>")
    for name, desc in _SUBCMDS.items():
        ctx.write(f"  {desc}")


def _display_bytes(ctx: PluginContext, direction: str, data: bytes,
                   binary: bool = False) -> None:
    """Display TX or RX data, choosing format by packet size.

    Short packets (<=16 bytes) are shown inline. Binary data is shown
    as hex, text data uses smart format (mixed text/hex). Longer packets
    get a multi-line hex dump with offset and ASCII sidebar.

    Args:
        ctx: Plugin context for output.
        direction: Label prefix — ``"TX"`` (cyan) or ``"RX"`` (yellow).
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


def _cmd_send(ctx: PluginContext, args: str) -> None:
    """Send raw bytes to the serial port and display the response.

    Parses hex and/or quoted text into bytes, transmits them (no line
    ending appended), then waits up to 1 second for a response frame.
    Displays both TX and RX as hex with byte count and round-trip time.

    Args:
        ctx: Plugin context for serial I/O and output.
        args: Hex bytes and/or quoted strings, e.g. ``'01 03 "OK\\r"'``.
    """
    if not args.strip():
        ctx.write("Usage: !!proto send <hex bytes or \"text\">", "red")
        return
    if not ctx.is_connected():
        ctx.write("Not connected.", "red")
        return

    try:
        data = parse_data(args)
    except ValueError as e:
        ctx.write(f"Parse error: {e}", "red")
        return

    ctx.engine.set_proto_active(True)
    ctx.serial_drain()
    _display_bytes(ctx, "TX", data)
    t0 = time.monotonic()
    ctx.serial_write(data)
    response = ctx.serial_read_raw(1000)
    elapsed_ms = (time.monotonic() - t0) * 1000
    ctx.engine.set_proto_active(False)

    if response:
        _display_bytes(ctx, "RX", response)
        ctx.write(f"  ({len(response)} bytes, {elapsed_ms:.0f}ms)")
    else:
        ctx.write("  RX: (no response)", "red")


def _resolve_proto_file(ctx: PluginContext, filename: str) -> Path | None:
    """Resolve a proto script filename to a full path.

    Args:
        ctx: Plugin context with proto_dir.
        filename: Filename or path to resolve.

    Returns:
        Resolved Path, or None if not found (error already written).
    """
    path = Path(filename)
    if not path.exists():
        alt = ctx.proto_dir / filename
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
    ctx.write(f"  {path.name} — {len(script.tests)} tests", "dim")
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
    ctx.write(f"  {path.name} — {len(steps)} steps", "dim")
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
        ctx.write("Usage: !!proto run <file.pro>", "red")
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
        ctx.write("Usage: !!proto debug <file.pro>", "red")
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


def _cmd_status(ctx: PluginContext) -> None:
    """Show current proto state."""
    hex_mode = ctx.engine.get_hex_mode()
    connected = ctx.is_connected()
    ctx.write(f"Hex mode: {'on' if hex_mode else 'off'}")
    ctx.write(f"Connected: {'yes' if connected else 'no'}")
