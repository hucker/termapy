"""Built-in plugin: sized binary capture to file."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from termapy.plugins import Command
from termapy.protocol import parse_format_spec
from termapy.scripting import resolve_seq_filename

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

_KEYWORDS = {"cap_vals=", "cap_bytes=", "sep=", "echo", "cmd=", "fmt="}


def _extract_keyword_sections(args: str) -> dict[str, str]:
    """Split args into keyword sections.

    Returns dict with keys like 'cmd', 'fmt', 'cap_vals', etc.
    Positional tokens before any keyword go under '_positional'.
    """
    result: dict[str, str] = {}
    # Extract cmd= first (everything after it is the command)
    cmd = ""
    if "cmd=" in args:
        before, cmd = args.split("cmd=", 1)
        args = before.strip()
        cmd = cmd.strip()
    if cmd:
        result["cmd"] = cmd

    # Extract fmt= (everything between fmt= and next keyword)
    if "fmt=" in args:
        idx = args.index("fmt=")
        before_fmt = args[:idx]
        after_fmt = args[idx + 4:]
        # Find next keyword in after_fmt
        fmt_end = len(after_fmt)
        for kw in _KEYWORDS:
            if kw == "fmt=":
                continue
            pos = after_fmt.find(kw)
            if pos != -1 and pos < fmt_end:
                fmt_end = pos
        result["fmt"] = after_fmt[:fmt_end].strip()
        args = before_fmt + after_fmt[fmt_end:]

    # Parse remaining tokens
    for tok in args.split():
        lower = tok.lower()
        matched = False
        for kw in ("cap_vals=", "cap_bytes=", "sep="):
            if lower.startswith(kw):
                key = kw.rstrip("=")
                result[key] = tok.split("=", 1)[1]
                matched = True
                break
        if not matched and lower == "echo":
            result["echo"] = "true"
        elif not matched:
            result.setdefault("_positional", "")
            result["_positional"] += " " + tok
    return result


def _handler(ctx: PluginContext, args: str) -> None:
    """Capture binary serial data to a file by byte/element count.

    Syntax: /bin_cap <mode> <file> {fmt=spec} <cap_vals=N|cap_bytes=N>
            {sep=comma|tab|space} {echo} {cmd=command...}

    Args:
        ctx: Plugin context.
        args: Command arguments.
    """
    sections = _extract_keyword_sections(args)
    positional = sections.get("_positional", "").split()

    if len(positional) < 2:
        ctx.write(
            "Usage: /bin_cap <append|a|new|n> <file> "
            "{fmt=spec} <cap_vals=N|cap_bytes=N> {sep=comma|tab|space} "
            "{echo} {cmd=...}",
            "yellow",
        )
        return

    mode_str = positional[0]
    filename = positional[1]

    # Validate mode
    if mode_str.lower() in ("append", "a"):
        raw_file_mode = "a"
    elif mode_str.lower() in ("new", "n"):
        raw_file_mode = "w"
    else:
        ctx.write(f"Invalid mode: {mode_str!r}. Use append/a or new/n.", "red")
        return

    # Parse keyword values
    fmt_spec = sections.get("fmt", "")
    cmd = sections.get("cmd", "")
    echo = "echo" in sections
    sep_name = sections.get("sep", "comma").lower()

    sep_map = {"comma": ",", "tab": "\t", "space": " "}
    sep = sep_map.get(sep_name)
    if sep is None:
        ctx.write(f"Invalid sep: {sep_name!r}. Use comma, tab, or space.", "red")
        return

    try:
        cap_vals = int(sections["cap_vals"]) if "cap_vals" in sections else 0
    except ValueError:
        ctx.write(f"Invalid cap_vals: {sections['cap_vals']!r}", "red")
        return

    try:
        cap_bytes = int(sections["cap_bytes"]) if "cap_bytes" in sections else 0
    except ValueError:
        ctx.write(f"Invalid cap_bytes: {sections['cap_bytes']!r}", "red")
        return

    if not cap_vals and not cap_bytes:
        ctx.write("Must specify cap_vals=N or cap_bytes=N.", "red")
        return

    # Parse format spec
    columns = []
    record_size = 0
    if fmt_spec:
        try:
            columns = parse_format_spec(fmt_spec)
        except Exception as e:
            ctx.write(f"Invalid format spec: {e}", "red")
            return
        # Record size = highest byte index + 1
        max_idx = 0
        for col in columns:
            if col.byte_indices:
                max_idx = max(max_idx, max(col.byte_indices))
        record_size = max_idx + 1
        if record_size == 0:
            ctx.write("Format spec has no byte references.", "red")
            return

    # Calculate target bytes
    if cap_vals:
        if not record_size:
            ctx.write("cap_vals requires fmt= format spec.", "red")
            return
        target_bytes = cap_vals * record_size
    else:
        target_bytes = cap_bytes
        if record_size and (target_bytes % record_size != 0):
            ctx.write(
                f"cap_bytes={cap_bytes} is not a multiple of "
                f"record size ({record_size} bytes).",
                "red",
            )
            return

    # File mode: binary when raw (no spec), text when formatted
    if columns:
        file_mode = raw_file_mode
    else:
        file_mode = raw_file_mode + "b"

    # Resolve $(n000) sequence numbering
    try:
        filename = resolve_seq_filename(filename, ctx.cap_dir)
    except ValueError as e:
        ctx.write(str(e), "red")
        return

    # Resolve path
    path = Path(filename)
    if not path.is_absolute():
        path = ctx.cap_dir / filename
    path = path.resolve()

    # Start capture
    started = ctx.engine.start_capture(
        path=path,
        file_mode=file_mode,
        mode="bin",
        target_bytes=target_bytes,
        columns=columns,
        record_size=record_size,
        sep=sep,
        echo=echo,
    )

    if started and cmd:
        ctx.serial_drain()
        ctx.dispatch(cmd)


def _stop(ctx: PluginContext, args: str) -> None:
    """Stop an active binary capture."""
    ctx.engine.stop_capture()


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="bin_cap",
    args="<mode> <file> {fmt=spec} <size> {sep=comma|tab|space} {echo} {cmd=...}",
    help="Capture binary serial data to a file by byte/element count.",
    long_help=(
        "Modes: append/a (add to file), new/n (overwrite).\n"
        "fmt=: format spec using protocol spec language.\n"
        "  e.g. fmt=Temp:U1-2 Pressure:F3-6 Status:H7\n"
        "  Omit fmt= for raw binary capture.\n"
        "Size: cap_vals=N (record count) or cap_bytes=N (total bytes).\n"
        "sep=comma|tab|space: column separator (default comma).\n"
        "echo: print formatted values to terminal.\n"
        "cmd=...: command to send after capture starts.\n\n"
        "Examples:\n"
        "  /bin_cap n data.csv fmt=Val:U1-2 cap_vals=50 cmd=AT+BINDUMP u16 50\n"
        "  /bin_cap n raw.bin cap_bytes=256 cmd=read_all\n"
        "  /bin_cap n log.csv fmt=T:U1-2 V:F3-6 cap_vals=100 sep=tab echo"
    ),
    handler=_handler,
    sub_commands={
        "stop": Command(
            args="",
            help="Stop an active binary capture.",
            handler=_stop,
        ),
    },
)
