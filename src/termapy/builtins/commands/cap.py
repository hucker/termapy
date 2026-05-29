"""Built-in plugin: unified data capture - text, binary, struct, hex."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from termapy.folder_ops import build_folder_subcommands
from termapy.help_dynamic import compose, folder_line
from termapy.plugins import CapabilitySet, CmdResult, Command
from termapy.protocol import parse_format_spec
from termapy.scripting import parse_duration, parse_keywords, resolve_seq_filename

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
    # Extract cmd= first (everything after it is the command - must be last)
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


def _handler_text(ctx: PluginContext, args: str) -> CmdResult:
    """Capture decoded serial text to a file for a timed duration.

    Syntax: /cap.text <file> timeout=<dur> {mode=new|append} {echo=on|off} {cmd=...}

    Args:
        ctx: Plugin context.
        args: Command arguments.
    """
    sections = _extract_keyword_sections(args)
    positional = sections.get("_positional", "").split()

    if len(positional) < 1 or "timeout" not in sections:
        return CmdResult.fail(
            msg="Usage: /cap.text <file> timeout=<dur> {mode=new|append} "
            "{echo=on|off} {cmd=... (must be last)}"
        )

    filename = positional[0]
    file_mode = _parse_mode(sections)
    if file_mode is None:
        return CmdResult.fail(
            msg=f"Invalid mode: {sections['mode']!r}. Use new/n or append/a."
        )

    try:
        seconds = parse_duration(sections["timeout"])
    except ValueError as e:
        return CmdResult.fail(msg=str(e))

    cmd = sections.get("cmd", "")
    echo = sections.get("echo", "off").lower() == "on"

    try:
        filename = resolve_seq_filename(filename, ctx.fs.cap_dir)
    except ValueError as e:
        return CmdResult.fail(msg=str(e))

    path = _resolve_path(filename, ctx.fs.cap_dir)

    started = ctx.internal.start_capture(
        path=path,
        file_mode=file_mode,
        mode="text",
        duration=seconds,
        echo=echo,
    )

    if started and cmd:
        ctx.dispatch(cmd)
    return CmdResult.ok(value=path)


# ── /cap.bin handler ─────────────────────────────────────────────────────────


def _handler_bin(ctx: PluginContext, args: str) -> CmdResult:
    """Capture raw binary bytes to a file.

    Syntax: /cap.bin <file> bytes=<N> {mode=new|append} {timeout=<dur>} {cmd=...}

    Args:
        ctx: Plugin context.
        args: Command arguments.
    """
    sections = _extract_keyword_sections(args)
    positional = sections.get("_positional", "").split()

    if len(positional) < 1 or "bytes" not in sections:
        return CmdResult.fail(
            msg="Usage: /cap.bin <file> bytes=<N> {mode=new|append} "
            "{timeout=<dur>} {cmd=... (must be last)}"
        )

    filename = positional[0]
    file_mode = _parse_mode(sections)
    if file_mode is None:
        return CmdResult.fail(
            msg=f"Invalid mode: {sections['mode']!r}. Use new/n or append/a."
        )

    try:
        cap_bytes = int(sections["bytes"])
    except ValueError:
        return CmdResult.fail(msg=f"Invalid bytes: {sections['bytes']!r}")

    timeout_s = 0.0
    if "timeout" in sections:
        try:
            timeout_s = parse_duration(sections["timeout"])
        except ValueError as e:
            return CmdResult.fail(msg=str(e))

    cmd = sections.get("cmd", "")

    try:
        filename = resolve_seq_filename(filename, ctx.fs.cap_dir)
    except ValueError as e:
        return CmdResult.fail(msg=str(e))

    path = _resolve_path(filename, ctx.fs.cap_dir)

    started = ctx.internal.start_capture(
        path=path,
        file_mode=file_mode + "b",
        mode="bin",
        target_bytes=cap_bytes,
        timeout=timeout_s,
    )

    if started and cmd:
        ctx.serial.drain()
        ctx.dispatch(cmd)
    return CmdResult.ok(value=path)


# ── /cap.struct and /cap.hex shared handler ──────────────────────────────────


def _handler_structured(ctx: PluginContext, args: str, hex_mode: bool = False) -> CmdResult:
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
        return CmdResult.fail(
            msg=f"Usage: /{label} <file> fmt=<spec> records=<N> "
            "{mode=new|append} {sep=comma|tab|space} {echo=on|off} "
            "{timeout=<dur>} {cmd=... (must be last)}"
        )

    filename = positional[0]
    raw_file_mode = _parse_mode(sections)
    if raw_file_mode is None:
        return CmdResult.fail(
            msg=f"Invalid mode: {sections['mode']!r}. Use new/n or append/a."
        )

    fmt_spec = sections["fmt"]
    cmd = sections.get("cmd", "")
    echo = sections.get("echo", "off").lower() == "on"
    sep_name = sections.get("sep", "comma").lower()

    sep_map = {"comma": ",", "tab": "\t", "space": " "}
    sep = sep_map.get(sep_name)
    if sep is None:
        return CmdResult.fail(msg=f"Invalid sep: {sep_name!r}. Use comma, tab, or space.")

    try:
        records = int(sections["records"]) if "records" in sections else 0
    except ValueError:
        return CmdResult.fail(msg=f"Invalid records: {sections['records']!r}")

    try:
        cap_bytes = int(sections["bytes"]) if "bytes" in sections else 0
    except ValueError:
        return CmdResult.fail(msg=f"Invalid bytes: {sections['bytes']!r}")

    if not records and not cap_bytes:
        return CmdResult.fail(msg="Must specify records=N or bytes=N.")

    timeout_s = 0.0
    if "timeout" in sections:
        try:
            timeout_s = parse_duration(sections["timeout"])
        except ValueError as e:
            return CmdResult.fail(msg=str(e))

    # Parse format spec.  parse_format_spec raises ValueError on
    # malformed user input (missing parens, unknown column type,
    # unterminated string, etc.) -- that's the expected user error.
    try:
        columns = parse_format_spec(fmt_spec)
    except ValueError as e:
        return CmdResult.fail(msg=f"Invalid format spec: {e}")

    max_idx = 0
    for col in columns:
        if col.byte_indices:
            max_idx = max(max_idx, max(col.byte_indices))
    record_size = max_idx + 1
    if record_size == 0:
        return CmdResult.fail(msg="Format spec has no byte references.")

    # Calculate target bytes
    if records:
        target_bytes = records * record_size
    else:
        target_bytes = cap_bytes
        if target_bytes % record_size != 0:
            return CmdResult.fail(
                msg=f"bytes={cap_bytes} is not a multiple of "
                f"record size ({record_size} bytes)."
            )

    try:
        filename = resolve_seq_filename(filename, ctx.fs.cap_dir)
    except ValueError as e:
        return CmdResult.fail(msg=str(e))

    path = _resolve_path(filename, ctx.fs.cap_dir)

    started = ctx.internal.start_capture(
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
        ctx.serial.drain()
        ctx.dispatch(cmd)
    return CmdResult.ok(value=path)


def _handler_struct(ctx: PluginContext, args: str) -> CmdResult:
    """Capture raw bytes, decode with format spec to CSV."""
    return _handler_structured(ctx, args, hex_mode=False)


def _handler_hex(ctx: PluginContext, args: str) -> CmdResult:
    """Capture hex text lines, decode with format spec to CSV."""
    return _handler_structured(ctx, args, hex_mode=True)


# ── /cap.poll handler ────────────────────────────────────────────────────────


_POLL_KWS = ("count=", "delay=", "file=", "labels=", "regex=", "fmt=", "timeout=")


def _parse_poll_args(args: str) -> dict[str, str]:
    """Parse /cap.poll args.  cmd= must be last; everything after it is the command."""
    result: dict[str, str] = {}
    if "cmd=" not in args:
        return result
    before, cmd = args.split("cmd=", 1)
    result["cmd"] = cmd.strip()
    # Parse the remaining keywords (space-separated key=value).
    # --overwrite / --notime flags are handled as first-class flags and
    # stripped by the dispatcher before this parser ever runs.
    for tok in before.split():
        lower = tok.lower()
        for kw in _POLL_KWS:
            if lower.startswith(kw):
                result[kw.rstrip("=")] = tok[len(kw):]
                break
    return result


def _safe_filename(text: str) -> str:
    """Derive a safe filename stem from a command string."""
    import re as _re
    # Take first token, lowercase, replace non-alphanumeric with _
    first = text.strip().split()[0] if text.strip().split() else "poll"
    safe = _re.sub(r"[^A-Za-z0-9]+", "_", first).strip("_").lower()
    return safe or "poll"


def _handler_poll(ctx: PluginContext, args: str) -> CmdResult:
    """Poll one or more commands on a schedule, display (and optionally save) results.

    Syntax: /cap.poll {count=N} {delay=<dur>} {file=<name>}
            {labels=<names>} {regex=<pattern>} {fmt=csv|json}
            {timeout=<dur>} {--overwrite} {--notime}
            cmd=<commands> (must be last)

    Commands in cmd= are newline-separated.  Each tick sends all commands
    and records one row per tick.  Without file=, results go to the terminal
    only.  With file=, results also append to the named CSV or JSONL file.
    """
    import csv as _csv
    import json as _json
    import re as _re
    import time as _time
    from datetime import datetime

    sections = _parse_poll_args(args)
    if "cmd" not in sections:
        return CmdResult.fail(
            msg="Usage: /cap.poll {count=N} {delay=<dur>} {file=<name>} "
            "{labels=<names>} {parse=<regex>} {fmt=csv|json} "
            "{timeout=<dur>} {overwrite} cmd=<commands> (must be last)"
        )

    # Commands - newline-separated
    cmds = [c.strip() for c in sections["cmd"].replace("\\n", "\n").split("\n") if c.strip()]
    if not cmds:
        return CmdResult.fail(msg="cmd= must have at least one command")

    # Count (default 60)
    try:
        count = int(sections.get("count", "60"))
    except ValueError:
        return CmdResult.fail(msg=f"Invalid count: {sections['count']!r}")
    if count <= 0:
        return CmdResult.fail(msg="Count must be positive")

    # Delay (default 1s; 0 means "as fast as possible")
    delay_raw = sections.get("delay", "1s").strip()
    if delay_raw == "0":
        delay_s = 0.0
    else:
        try:
            delay_s = parse_duration(delay_raw)
        except ValueError as e:
            return CmdResult.fail(msg=f"Invalid delay: {e}")
    if delay_s < 0:
        return CmdResult.fail(msg="Delay must be non-negative")

    # Timeout (default 1s)
    try:
        timeout_ms = int(parse_duration(sections.get("timeout", "1s")) * 1000)
    except ValueError as e:
        return CmdResult.fail(msg=f"Invalid timeout: {e}")

    # Format
    fmt = sections.get("fmt", "csv").lower()
    if fmt not in ("csv", "json"):
        return CmdResult.fail(msg=f"Invalid fmt: {fmt!r}. Use csv or json.")

    # Labels (default: command strings)
    if "labels" in sections:
        labels = sections["labels"].split()
        if len(labels) != len(cmds):
            return CmdResult.fail(
                msg=f"labels count ({len(labels)}) must match cmd count ({len(cmds)})"
            )
        for lbl in labels:
            if not _re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", lbl):
                return CmdResult.fail(
                    msg=f"Invalid label: {lbl!r} (must be identifier chars)"
                )
    else:
        labels = cmds[:]

    # Regex for value extraction from command response
    parse_re = None
    if "regex" in sections:
        try:
            parse_re = _re.compile(sections["regex"])
        except _re.error as e:
            return CmdResult.fail(msg=f"Invalid regex: {e}")

    # Output file (optional)
    path: Path | None = None
    overwrite = ctx.flag("--overwrite")
    notime = ctx.flag("--notime")
    if "file" in sections:
        name = sections["file"]
        p = Path(name)
        if not p.is_absolute():
            p = ctx.fs.cap_dir / name
        ext = ".csv" if fmt == "csv" else ".jsonl"
        if p.suffix.lower() not in (".csv", ".json", ".jsonl"):
            p = p.with_suffix(ext)
        # Auto-number on collision unless overwrite
        if p.exists() and not overwrite:
            stem = p.stem
            suffix = p.suffix
            parent = p.parent
            for i in range(1, 1000):
                candidate = parent / f"{stem}_{i:03d}{suffix}"
                if not candidate.exists():
                    p = candidate
                    break
            else:
                return CmdResult.fail(msg=f"Cannot find free filename near {p}")
        path = p
        path.parent.mkdir(parents=True, exist_ok=True)

    encoding = ctx.cfg.get("encoding", "utf-8")
    cmd_prefix = ctx.prefix

    def extract(raw: str) -> str:
        if parse_re is None:
            return raw.strip()
        m = parse_re.search(raw)
        return m.group(0) if m else ""

    def run_one(cmd: str) -> str:
        if cmd.startswith(cmd_prefix):
            silent = cmd if cmd.endswith(".silent") else None
            if silent is None:
                parts = cmd.split(None, 1)
                silent = parts[0] + ".silent"
                if len(parts) > 1:
                    silent += " " + parts[1]
            result = ctx.dispatch(silent)
            raw = result.value if result.success else ""
        else:
            if not ctx.serial.is_connected():
                return ""
            with ctx.serial.io():
                ctx.serial.drain()
                ctx.serial.send(cmd)
                response = ctx.serial.read_raw(timeout_ms=timeout_ms)
            raw = response.decode(encoding, errors="replace") if response else ""
        return extract(raw)

    # Build header columns (timestamp optional)
    header_cols = []
    if not notime:
        header_cols.append(("timestamp", 23, "ljust"))
    header_cols.append(("counter", 7, "rjust"))
    for label in labels:
        header_cols.append((label, 10, "rjust"))

    # Open file if requested
    fh = None
    writer = None
    if path is not None:
        try:
            fh = open(path, "w", encoding="utf-8", newline="")
        except OSError as e:
            return CmdResult.fail(msg=f"Cannot open {path}: {e}")
        if fmt == "csv":
            writer = _csv.writer(fh)
            writer.writerow([c[0] for c in header_cols])
            fh.flush()

    # Header line to the terminal
    ctx.io.output("  " + "  ".join(
        getattr(name, align)(w) for name, w, align in header_cols
    ))

    stop_event = ctx.internal.script_stop_event
    samples_written = 0
    aborted_msg: str | None = None

    try:
        for i in range(count):
            if stop_event is not None and stop_event.is_set():
                aborted_msg = "Poll interrupted."
                break

            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            values = [run_one(c) for c in cmds]
            counter = i + 1

            # First-sample numeric check (skipped if regex= given)
            if i == 0 and parse_re is None:
                try:
                    float(values[0])
                except (ValueError, TypeError):
                    aborted_msg = (
                        f"Non-numeric output: {values[0]!r}.  "
                        f"Use regex=<pattern> to extract a number, or check the command output."
                    )
                    break

            # Build the row (timestamp optional)
            if notime:
                csv_row = [counter] + values
                line_parts = [str(counter).rjust(7)] + [v.rjust(10) for v in values]
            else:
                csv_row = [ts, counter] + values
                line_parts = [ts.ljust(23), str(counter).rjust(7)] + [v.rjust(10) for v in values]

            # Write to file
            if writer is not None and fh is not None:
                writer.writerow(csv_row)
                fh.flush()
            elif fh is not None:  # JSONL
                row: dict[str, object] = (
                    {} if notime else {"timestamp": ts}
                )
                row["counter"] = counter
                for k, v in zip(labels, values):
                    row[k] = v
                fh.write(_json.dumps(row) + "\n")
                fh.flush()

            # Echo to terminal (matching header alignment)
            ctx.io.output("  " + "  ".join(line_parts))

            samples_written += 1

            # Sleep, but stay responsive to stop
            if i + 1 < count and delay_s > 0:
                tick_end = _time.monotonic() + delay_s
                while _time.monotonic() < tick_end:
                    if stop_event is not None and stop_event.is_set():
                        break
                    _time.sleep(min(0.05, tick_end - _time.monotonic()))
    finally:
        if fh is not None:
            fh.close()

    if aborted_msg:
        if path is not None and samples_written == 0:
            try:
                path.unlink()
            except OSError:
                pass
        return CmdResult.fail(msg=aborted_msg)

    if path is not None:
        ctx.io.output(f"Poll complete: {path} ({samples_written} samples)", "green")
        return CmdResult.ok(value=path)
    ctx.io.output(f"Poll complete ({samples_written} samples)", "green")
    return CmdResult.ok(value=str(samples_written))


# ── /cap.stop handler ────────────────────────────────────────────────────────


def _handler_stop(ctx: PluginContext, args: str) -> CmdResult:
    """Stop an active capture.

    Side-effect only; ``ctx.internal.stop_capture`` returns no path, so
    ``CmdResult.value`` is ``""`` rather than something synthetic.
    """
    ctx.internal.stop_capture()
    return CmdResult.ok(value="")


# ── /cap.wire handler ────────────────────────────────────────────────────────


def _handler_wire(ctx: PluginContext, args: str) -> CmdResult:
    """Run a command and show TX/RX bytes as inline hex + repr.

    Wraps the command in ``ctx.serial.rx_observer()`` / ``ctx.serial.tx_observer()``
    so the bytes that flow during the dispatch are captured both ways.
    Emits two lines: one for TX, one for RX.  Each line shows the byte
    count, the hex-spaced bytes, and a Python ``repr()`` of the
    decoded text so non-printing characters appear as escape sequences
    (``\\r``, ``\\n``, ``\\x00``) instead of being rendered or eaten.

    Async response handling: ``ctx.dispatch()`` returns immediately
    after writing TX bytes; the RX response arrives later via the
    on_lines pipeline and the rx_observer.  After dispatch returns,
    we stay inside the with-block and poll a "last byte arrival" timer
    until the device's RX has been quiet for ``wait_gap`` (default
    50ms) -- the same idle-gap settling pattern used by
    ``request_response.py`` and the profile executor.  Capped by a
    5-second hard deadline.

    Args (parameters first, ``cmd=`` last as the rest-keyword that
    consumes the rest of the line so the wrapped command can contain
    spaces, equals signs, or quotes without escaping):

        /cap.wire cmd=AT+VER                       default 50ms wait_gap
        /cap.wire wait_gap=200ms cmd=AT+VER        slow device

    The canonical use case is line-ending and non-printing-character
    debugging: when a device "doesn't respond," it almost always
    actually does, but the response gets eaten by a terminator
    mismatch.  ``/cap.wire`` makes the mismatch immediately visible.
    """
    import time as _time

    kw = parse_keywords(args, {"cmd", "wait_gap"}, rest_keyword="cmd")
    cmd = kw.get("cmd", "").strip()
    if not cmd:
        return CmdResult.fail(msg="Usage: /cap.wire {wait_gap=<dur>} cmd=<command>")
    try:
        wait_gap_s = parse_duration(kw.get("wait_gap", "50ms"))
    except ValueError as e:
        return CmdResult.fail(msg=f"Invalid wait_gap: {e}")

    tx = bytearray()
    rx = bytearray()
    last_arrival = [_time.monotonic()]

    def cap_rx(data: bytes) -> None:
        rx.extend(data)
        last_arrival[0] = _time.monotonic()

    def cap_tx(data: bytes) -> None:
        tx.extend(data)

    with ctx.serial.rx_observer(cap_rx), ctx.serial.tx_observer(cap_tx):
        ctx.dispatch(cmd)
        # Settle on idle-gap: wait until rx has been quiet for wait_gap_s.
        # Hard-capped at 5s so a chatty device can't hang us forever.
        if wait_gap_s > 0:
            deadline = _time.monotonic() + 5.0
            while _time.monotonic() < deadline:
                if (_time.monotonic() - last_arrival[0]) >= wait_gap_s:
                    break
                _time.sleep(0.01)

    encoding = ctx.cfg.get("encoding", "utf-8")
    tx_decoded = bytes(tx).decode(encoding, errors="replace")
    rx_decoded = bytes(rx).decode(encoding, errors="replace")

    # Render the standard TX/RX envelope.  When zero bytes flowed (the
    # cmd was a slash command that ran locally, or the device just
    # didn't respond), we still render the lines with byte count 0 --
    # that empty envelope is itself the diagnostic, and a "Warning: no
    # wire traffic" header explains what to look at.
    if not tx and not rx:
        ctx.io.output("  Warning: no wire traffic", "yellow")
    # Hex-spaced bytes paired with a Python repr() of the decoded
    # text on the same line so escape sequences for non-printing
    # bytes (\\r, \\n, \\x00, \\x1b) are visible.
    ctx.io.output_markup(
        f"  [yellow]TX ({len(tx):3d}):[/] "
        f"{bytes(tx).hex(' ')}  [dim]{tx_decoded!r}[/]",
    )
    ctx.io.output_markup(
        f"  [cyan]RX ({len(rx):3d}):[/] "
        f"{bytes(rx).hex(' ')}  [dim]{rx_decoded!r}[/]",
    )

    return CmdResult.ok(value={
        "tx_bytes": len(tx),
        "rx_bytes": len(rx),
        "tx_hex": bytes(tx).hex(),
        "rx_hex": bytes(rx).hex(),
        "tx_text": tx_decoded,
        "rx_text": rx_decoded,
    })


# ── Dynamic long_help ─────────────────────────────────────────────────────────

def _cap_folder_line(ctx: PluginContext) -> str:
    """Green one-liner showing the count of files in cap/."""
    return folder_line(ctx, "cap")


def _cap_long_help_with_prose(prose: str):
    """Build a callable that prepends the cap/ file count to a fixed prose body."""

    def _long(ctx: PluginContext) -> str:
        return compose(_cap_folder_line(ctx), prose)

    return _long


_CAP_TEXT_PROSE = (
    "Passively captures all text arriving from the device for a\n"
    "fixed duration.  Use {prefix}cap.stop to end early.\n"
    "\n"
    "Parameters:\n"
    "  <file>            REQUIRED output filename (relative to cap/ dir)\n"
    "  timeout=<dur>     REQUIRED duration, e.g. 3s, 500ms, 1.5s\n"
    "  mode=new|append   file mode (default: new)\n"
    "  echo=on|off       also print captured text to terminal (default off)\n"
    "  cmd=...           command to send after capture starts (must be last)"
)

_CAP_BIN_PROSE = (
    "Captures a fixed number of raw bytes to a binary file.  Ends\n"
    "when the byte count is reached or the optional timeout expires.\n"
    "\n"
    "Parameters:\n"
    "  <file>            REQUIRED output filename (relative to cap/ dir)\n"
    "  bytes=<N>         REQUIRED target byte count\n"
    "  mode=new|append   file mode (default: new)\n"
    "  timeout=<dur>     safety timeout, e.g. 10s (default: no timeout)\n"
    "  cmd=...           command to send after capture starts (must be last)"
)

_CAP_STRUCT_PROSE = (
    "Decodes binary data using C struct field mapping into CSV rows.\n"
    "\n"
    "Parameters:\n"
    "  <file>            REQUIRED output filename (relative to cap/ dir)\n"
    "  fmt=<spec>        REQUIRED format spec, e.g.\n"
    "                    fmt=Temp:U1-2 Pressure:F3-6 Status:H7\n"
    "  records=<N>       REQUIRED record count (or bytes=N for total bytes)\n"
    "  mode=new|append   file mode (default: new)\n"
    "  sep=comma|tab|space  column separator (default: comma)\n"
    "  echo=on|off       print formatted values to terminal (default off)\n"
    "  timeout=<dur>     safety timeout, e.g. 10s (default: no timeout)\n"
    "  cmd=...           command to send after capture starts (must be last)\n"
    "\n"
    "See {prefix}help writing-plugins for the format spec language."
)

_CAP_HEX_PROSE = (
    "Like {prefix}cap.struct but reads hex-encoded text lines (e.g. '01 02 FF AB')\n"
    "instead of raw binary bytes.  Hex is converted to binary, then decoded\n"
    "with the same format spec pipeline.\n"
    "\n"
    "Parameters:\n"
    "  <file>            REQUIRED output filename (relative to cap/ dir)\n"
    "  fmt=<spec>        REQUIRED format spec (same as {prefix}cap.struct)\n"
    "  records=<N>       REQUIRED record count\n"
    "  mode=new|append   file mode (default: new)\n"
    "  sep=comma|tab|space  column separator (default: comma)\n"
    "  echo=on|off       print formatted values to terminal (default off)\n"
    "  timeout=<dur>     safety timeout (default: no timeout)\n"
    "  cmd=...           command to send after capture starts (must be last)"
)

_CAP_POLL_PROSE = (
    "Runs one or more commands every `delay=` seconds, printing each\n"
    "response as a row.  With `file=`, also writes to CSV or JSONL.\n"
    "\n"
    "cmd= is newline-separated for multiple columns:\n"
    "  {prefix}cap.poll cmd=AT+BAT\\nAT+TEMP\n"
    "\n"
    "Parameters:\n"
    "  cmd=<commands>    REQUIRED, must be last.  \\n-separated list.\n"
    "  count=<N>         number of samples (default: 60)\n"
    "  delay=<dur>       between samples, e.g. 500ms (default: 1s).\n"
    "                    Use delay=0 to go as fast as possible.\n"
    "  file=<name>       output file.  Without this, results only\n"
    "                    print to the terminal.\n"
    "  labels=<names>    space-separated column names (identifier\n"
    "                    chars only).  Defaults to the cmd strings.\n"
    "  regex=<pattern>   regex to extract value from each response\n"
    "                    (e.g. regex=[-\\d.]+ pulls 23.4 from '+TEMP: 23.4C')\n"
    "  fmt=csv|json      output format (default: csv).  json writes\n"
    "                    JSON Lines (.jsonl).\n"
    "  timeout=<dur>     per-command response timeout (default: 1s)\n"
    "\n"
    "Without regex=, the first sample must be numeric or the command\n"
    "aborts.  With regex=, non-matching responses become empty values."
)


_CAP_WIRE_PROSE = (
    "Run a single command, then display the TX and RX bytes that flowed\n"
    "during it as hex + Python repr() of the decoded text.  The repr()\n"
    "shows non-printing characters (\\r, \\n, \\x00, \\x1b) as visible\n"
    "escape sequences instead of letting them get eaten by line-splitting\n"
    "or rendered as control characters.\n"
    "\n"
    "Canonical use case: line-ending and non-printing-character debugging.\n"
    "When a device 'doesn't respond,' it almost always does -- but the\n"
    "response gets eaten by a terminator mismatch (\\r vs \\n vs \\r\\n).\n"
    "Running the same command through {prefix}cap.wire makes the bytes\n"
    "on the wire immediately visible.\n"
    "\n"
    "Parameters:\n"
    "  cmd=<command>     REQUIRED command to run (slash or bare).\n"
    "                    Must be last; consumes the rest of the line\n"
    "                    so the command can contain spaces, '=', or\n"
    "                    quotes without escaping.\n"
    "  wait_gap=<dur>    Idle gap that signals the device's response\n"
    "                    is complete (default: 50ms).  Capped at 5s.\n"
    "                    Increase for slow devices; lower for snappy\n"
    "                    ones where 50ms feels laggy.\n"
    "\n"
    "Examples:\n"
    "  {prefix}cap.wire cmd=AT+VER                - default 50ms wait_gap\n"
    "  {prefix}cap.wire wait_gap=200ms cmd=AT+VER - slow device\n"
    "\n"
    "Note: cmd= must be something that produces serial traffic.  Slash\n"
    "commands like {prefix}help are handled inside termapy and never\n"
    "reach the wire, so {prefix}cap.wire renders an empty envelope\n"
    "(TX/RX both showing 0 bytes) with a 'no wire traffic' warning\n"
    "header.\n"
    "\n"
    "Output format::\n"
    "\n"
    "  TX (  7): 41 54 2b 56 45 52 0d                       'AT+VER\\r'\n"
    "  RX ( 11): 56 45 52 3d 31 2e 32 2e 33 0d 0a           'VER=1.2.3\\r\\n'\n"
    "\n"
    "Output is inline only -- this is for interactive debugging, not\n"
    "logging.  For long-running wire captures to a file, use the\n"
    "{prefix}cap.text or {prefix}cap.bin commands."
)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="cap",
    help="Data capture tools.",
    long_help=_cap_folder_line,
    handler=None,
    sub_commands={
        "text": Command(
            args="<file> timeout=<dur> {mode=new|append} {echo=on|off} {cmd=... (must be last)}",
            help="Capture serial text to a file for a timed duration.",
            long_help=_cap_long_help_with_prose(_CAP_TEXT_PROSE),
            handler=_handler_text,
            needs=CapabilitySet(serial_connected=True),
        ),
        "bin": Command(
            args="<file> bytes=<N> {mode=new|append} {timeout=<dur>} {cmd=... (must be last)}",
            help="Capture raw binary bytes.",
            long_help=_cap_long_help_with_prose(_CAP_BIN_PROSE),
            handler=_handler_bin,
            needs=CapabilitySet(serial_connected=True),
        ),
        "struct": Command(
            args="<file> fmt=<spec> records=<N> {mode=new|append} {sep=...} {echo=on|off} {timeout=<dur>} {cmd=... (must be last)}",
            help="Capture raw bytes, decode with format spec to CSV.",
            long_help=_cap_long_help_with_prose(_CAP_STRUCT_PROSE),
            handler=_handler_struct,
            needs=CapabilitySet(serial_connected=True),
        ),
        "hex": Command(
            args="<file> fmt=<spec> records=<N> {mode=new|append} {sep=...} {echo=on|off} {timeout=<dur>} {cmd=... (must be last)}",
            help="Capture hex text lines, decode with format spec to CSV.",
            long_help=_cap_long_help_with_prose(_CAP_HEX_PROSE),
            handler=_handler_hex,
            needs=CapabilitySet(serial_connected=True),
        ),
        "poll": Command(
            args="{count=N} {delay=<dur>} {file=<name>} {labels=<names>} {regex=<pattern>} {fmt=csv|json} {timeout=<dur>} cmd=<commands> (must be last)",
            flags={
                "--overwrite": "Overwrite existing file instead of auto-numbering.",
                "--notime": "Omit the timestamp column (useful for tests).",
            },
            help="Poll commands on a schedule, display (and optionally save) results.",
            long_help=_cap_long_help_with_prose(_CAP_POLL_PROSE),
            handler=_handler_poll,
            raw_args=True,
        ),
        "stop": Command(
            help="Stop an active capture.",
            handler=_handler_stop,
        ),
        "wire": Command(
            args="{wait_gap=<dur>} cmd=<command>",
            help="Run a command and show TX/RX bytes as inline hex + repr.",
            # Plain string -- /cap.wire is inline-only, no file output, so
            # the "N files in cap/" prefix that other cap.* commands use
            # would be misleading.
            long_help=_CAP_WIRE_PROSE,
            handler=_handler_wire,
            raw_args=True,
        ),
        **build_folder_subcommands("cap"),
    },
)
