"""Built-in plugin: unified data capture — text, binary, struct, hex."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from termapy.plugins import Command
from termapy.protocol import parse_format_spec
from termapy.scripting import parse_duration, resolve_seq_filename

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

_KEYWORDS = {
    "mode=", "bytes=", "records=", "sep=", "echo=", "cmd=", "fmt=", "timeout=",
}


def _extract_keyword_sections(args: str) -> dict[str, str]:
    """Split args into keyword sections.

    Returns dict with keys like 'cmd', 'fmt', 'bytes', etc.
    Positional tokens before any keyword go under '_positional'.
    """
    result: dict[str, str] = {}
    # Extract cmd= first (everything after it is the command — must be last)
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
        for kw in ("mode=", "bytes=", "records=", "sep=", "timeout=", "echo="):
            if lower.startswith(kw):
                key = kw.rstrip("=")
                result[key] = tok.split("=", 1)[1]
                matched = True
                break
        if not matched:
            result.setdefault("_positional", "")
            result["_positional"] += " " + tok
    return result


def _parse_mode(sections: dict[str, str]) -> str | None:
    """Parse mode from sections. Returns 'w' or 'a', or None if invalid.

    Defaults to 'w' (new) if mode not specified.
    """
    mode_str = sections.get("mode", "new").lower()
    if mode_str in ("new", "n"):
        return "w"
    if mode_str in ("append", "a"):
        return "a"
    return None


def _resolve_path(filename: str, cap_dir: Path) -> Path | None:
    """Resolve sequence numbering and path for a capture filename."""
    path = Path(filename)
    if not path.is_absolute():
        path = cap_dir / filename
    return path.resolve()


# ── /cap.text handler ────────────────────────────────────────────────────────


def _handler_text(ctx: PluginContext, args: str) -> None:
    """Capture decoded serial text to a file for a timed duration.

    Syntax: /cap.text <file> timeout=<dur> {mode=new|append} {echo=on|off} {cmd=...}

    Args:
        ctx: Plugin context.
        args: Command arguments.
    """
    sections = _extract_keyword_sections(args)
    positional = sections.get("_positional", "").split()

    if len(positional) < 1 or "timeout" not in sections:
        ctx.write(
            "Usage: /cap.text <file> timeout=<dur> {mode=new|append} "
            "{echo=on|off} {cmd=... (must be last)}",
            "yellow",
        )
        return

    filename = positional[0]
    file_mode = _parse_mode(sections)
    if file_mode is None:
        ctx.write(
            f"Invalid mode: {sections['mode']!r}. Use new/n or append/a.", "red"
        )
        return

    try:
        seconds = parse_duration(sections["timeout"])
    except ValueError as e:
        ctx.write(str(e), "red")
        return

    cmd = sections.get("cmd", "")
    echo = sections.get("echo", "off").lower() == "on"

    try:
        filename = resolve_seq_filename(filename, ctx.cap_dir)
    except ValueError as e:
        ctx.write(str(e), "red")
        return

    path = _resolve_path(filename, ctx.cap_dir)

    started = ctx.engine.start_capture(
        path=path,
        file_mode=file_mode,
        mode="text",
        duration=seconds,
        echo=echo,
    )

    if started and cmd:
        ctx.dispatch(cmd)


# ── /cap.bin handler ─────────────────────────────────────────────────────────


def _handler_bin(ctx: PluginContext, args: str) -> None:
    """Capture raw binary bytes to a file.

    Syntax: /cap.bin <file> bytes=<N> {mode=new|append} {timeout=<dur>} {cmd=...}

    Args:
        ctx: Plugin context.
        args: Command arguments.
    """
    sections = _extract_keyword_sections(args)
    positional = sections.get("_positional", "").split()

    if len(positional) < 1 or "bytes" not in sections:
        ctx.write(
            "Usage: /cap.bin <file> bytes=<N> {mode=new|append} "
            "{timeout=<dur>} {cmd=... (must be last)}",
            "yellow",
        )
        return

    filename = positional[0]
    file_mode = _parse_mode(sections)
    if file_mode is None:
        ctx.write(
            f"Invalid mode: {sections['mode']!r}. Use new/n or append/a.", "red"
        )
        return

    try:
        cap_bytes = int(sections["bytes"])
    except ValueError:
        ctx.write(f"Invalid bytes: {sections['bytes']!r}", "red")
        return

    timeout_s = 0.0
    if "timeout" in sections:
        try:
            timeout_s = parse_duration(sections["timeout"])
        except ValueError as e:
            ctx.write(str(e), "red")
            return

    cmd = sections.get("cmd", "")

    try:
        filename = resolve_seq_filename(filename, ctx.cap_dir)
    except ValueError as e:
        ctx.write(str(e), "red")
        return

    path = _resolve_path(filename, ctx.cap_dir)

    started = ctx.engine.start_capture(
        path=path,
        file_mode=file_mode + "b",
        mode="bin",
        target_bytes=cap_bytes,
        timeout=timeout_s,
    )

    if started and cmd:
        ctx.serial_drain()
        ctx.dispatch(cmd)


# ── /cap.struct and /cap.hex shared handler ──────────────────────────────────


def _handler_structured(ctx: PluginContext, args: str, hex_mode: bool = False) -> None:
    """Capture binary data with format spec decoding to CSV.

    Used by both /cap.struct (raw bytes) and /cap.hex (hex text lines).

    Args:
        ctx: Plugin context.
        args: Command arguments.
        hex_mode: If True, parse hex text lines instead of raw bytes.
    """
    label = "cap.hex" if hex_mode else "cap.struct"
    sections = _extract_keyword_sections(args)
    positional = sections.get("_positional", "").split()

    if len(positional) < 1 or "fmt" not in sections:
        ctx.write(
            f"Usage: /{label} <file> fmt=<spec> records=<N> "
            "{mode=new|append} {sep=comma|tab|space} {echo=on|off} "
            "{timeout=<dur>} {cmd=... (must be last)}",
            "yellow",
        )
        return

    filename = positional[0]
    raw_file_mode = _parse_mode(sections)
    if raw_file_mode is None:
        ctx.write(
            f"Invalid mode: {sections['mode']!r}. Use new/n or append/a.", "red"
        )
        return

    fmt_spec = sections["fmt"]
    cmd = sections.get("cmd", "")
    echo = sections.get("echo", "off").lower() == "on"
    sep_name = sections.get("sep", "comma").lower()

    sep_map = {"comma": ",", "tab": "\t", "space": " "}
    sep = sep_map.get(sep_name)
    if sep is None:
        ctx.write(f"Invalid sep: {sep_name!r}. Use comma, tab, or space.", "red")
        return

    try:
        records = int(sections["records"]) if "records" in sections else 0
    except ValueError:
        ctx.write(f"Invalid records: {sections['records']!r}", "red")
        return

    try:
        cap_bytes = int(sections["bytes"]) if "bytes" in sections else 0
    except ValueError:
        ctx.write(f"Invalid bytes: {sections['bytes']!r}", "red")
        return

    if not records and not cap_bytes:
        ctx.write("Must specify records=N or bytes=N.", "red")
        return

    timeout_s = 0.0
    if "timeout" in sections:
        try:
            timeout_s = parse_duration(sections["timeout"])
        except ValueError as e:
            ctx.write(str(e), "red")
            return

    # Parse format spec
    try:
        columns = parse_format_spec(fmt_spec)
    except Exception as e:
        ctx.write(f"Invalid format spec: {e}", "red")
        return

    max_idx = 0
    for col in columns:
        if col.byte_indices:
            max_idx = max(max_idx, max(col.byte_indices))
    record_size = max_idx + 1
    if record_size == 0:
        ctx.write("Format spec has no byte references.", "red")
        return

    # Calculate target bytes
    if records:
        target_bytes = records * record_size
    else:
        target_bytes = cap_bytes
        if target_bytes % record_size != 0:
            ctx.write(
                f"bytes={cap_bytes} is not a multiple of "
                f"record size ({record_size} bytes).",
                "red",
            )
            return

    try:
        filename = resolve_seq_filename(filename, ctx.cap_dir)
    except ValueError as e:
        ctx.write(str(e), "red")
        return

    path = _resolve_path(filename, ctx.cap_dir)

    started = ctx.engine.start_capture(
        path=path,
        file_mode=raw_file_mode,
        mode="bin",
        target_bytes=target_bytes,
        columns=columns,
        record_size=record_size,
        sep=sep,
        echo=echo,
        hex_mode=hex_mode,
        timeout=timeout_s,
    )

    if started and cmd:
        ctx.serial_drain()
        ctx.dispatch(cmd)


def _handler_struct(ctx: PluginContext, args: str) -> None:
    """Capture raw bytes, decode with format spec to CSV."""
    _handler_structured(ctx, args, hex_mode=False)


def _handler_hex(ctx: PluginContext, args: str) -> None:
    """Capture hex text lines, decode with format spec to CSV."""
    _handler_structured(ctx, args, hex_mode=True)


# ── /cap.stop handler ────────────────────────────────────────────────────────


def _handler_stop(ctx: PluginContext, args: str) -> None:
    """Stop an active capture."""
    ctx.engine.stop_capture()


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="cap",
    help="Data capture tools.",
    handler=None,
    sub_commands={
        "text": Command(
            args="<file> timeout=<dur> {mode=new|append} {echo=on|off} {cmd=... (must be last)}",
            help="Capture serial text to a file for a timed duration.",
            handler=_handler_text,
        ),
        "bin": Command(
            args="<file> bytes=<N> {mode=new|append} {timeout=<dur>} {cmd=... (must be last)}",
            help="Capture raw binary bytes to a file.",
            handler=_handler_bin,
        ),
        "struct": Command(
            args="<file> fmt=<spec> records=<N> {mode=new|append} {sep=...} {echo=on|off} {timeout=<dur>} {cmd=... (must be last)}",
            help="Capture raw bytes, decode with format spec to CSV.",
            long_help=(
                "Decode binary data using C struct field mapping.\n"
                "fmt= uses the protocol format spec language.\n"
                "  e.g. fmt=Temp:U1-2 Pressure:F3-6 Status:H7\n"
                "records=N: number of records to capture.\n"
                "  Alternatively, bytes=N for total byte count.\n"
                "mode=new|append: file mode (default: new).\n"
                "sep=comma|tab|space: column separator (default comma).\n"
                "echo=on|off: print formatted values to terminal (default off).\n"
                "timeout=: optional safety timeout (e.g. 10s).\n"
                "cmd=...: command to send after capture starts (must be last)."
            ),
            handler=_handler_struct,
        ),
        "hex": Command(
            args="<file> fmt=<spec> records=<N> {mode=new|append} {sep=...} {echo=on|off} {timeout=<dur>} {cmd=... (must be last)}",
            help="Capture hex text lines, decode with format spec to CSV.",
            long_help=(
                "Like /cap.struct but reads hex-encoded text lines\n"
                "(e.g. '01 02 FF AB') instead of raw binary bytes.\n"
                "The hex bytes are converted to binary, then decoded\n"
                "with the same format spec pipeline.\n"
                "cmd=...: must be the last parameter."
            ),
            handler=_handler_hex,
        ),
        "stop": Command(
            help="Stop an active capture.",
            handler=_handler_stop,
        ),
    },
)
