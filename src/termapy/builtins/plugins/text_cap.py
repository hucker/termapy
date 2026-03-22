"""Built-in plugin: timed text capture to file."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from termapy.plugins import Command
from termapy.scripting import parse_duration, resolve_seq_filename

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler(ctx: PluginContext, args: str) -> None:
    """Capture decoded serial text to a file for a timed duration.

    Syntax: /text_cap <mode> <file> <duration> {cmd=command...}

    Args:
        ctx: Plugin context.
        args: Command arguments.
    """
    # Split off cmd= portion first
    cmd = ""
    if "cmd=" in args:
        before, cmd = args.split("cmd=", 1)
        args = before.strip()
        cmd = cmd.strip()

    parts = args.split()
    if len(parts) < 3:
        ctx.write(
            "Usage: /text_cap <append|a|new|n> <file> <duration> {cmd=...}",
            "yellow",
        )
        return

    mode_str, filename, dur_str = parts[0], parts[1], parts[2]

    # Validate mode
    if mode_str.lower() in ("append", "a"):
        file_mode = "a"
    elif mode_str.lower() in ("new", "n"):
        file_mode = "w"
    else:
        ctx.write(f"Invalid mode: {mode_str!r}. Use append/a or new/n.", "red")
        return

    # Parse duration
    try:
        seconds = parse_duration(dur_str)
    except ValueError as e:
        ctx.write(str(e), "red")
        return

    # Resolve $(n000) sequence numbering
    try:
        filename = resolve_seq_filename(filename, ctx.cap_dir)
    except ValueError as e:
        ctx.write(str(e), "red")
        return

    # Resolve path: bare filename → cap/ dir, absolute → as-is
    path = Path(filename)
    if not path.is_absolute():
        path = ctx.cap_dir / filename
    path = path.resolve()

    # Start capture
    started = ctx.engine.start_capture(
        path=path,
        file_mode=file_mode,
        mode="text",
        duration=seconds,
    )

    if started and cmd:
        ctx.dispatch(cmd)


def _stop(ctx: PluginContext, args: str) -> None:
    """Stop an active text capture."""
    ctx.engine.stop_capture()


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="text_cap",
    args="<mode> <file> <duration> {cmd=command...}",
    help="Capture serial text to a file for a timed duration.",
    long_help=(
        "Modes: append/a (add to file), new/n (overwrite).\n"
        "Duration: e.g. 2s, 500ms.\n"
        "cmd=... sends a command to the device after capture starts.\n"
        "Data continues to display on screen normally.\n\n"
        "Examples:\n"
        "  /text_cap n log.txt 3s cmd=AT+INFO\n"
        "  /text_cap a session.txt 10s"
    ),
    handler=_handler,
    sub_commands={
        "stop": Command(
            args="",
            help="Stop an active text capture.",
            handler=_stop,
        ),
    },
)
