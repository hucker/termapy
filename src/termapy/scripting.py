"""Template expansion, script parsing, and shared utilities for termapy.

Pure functions and dataclasses with no Textual or serial dependencies.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path


# Shared ANSI escape regex - matches all CSI sequences (color, cursor, clear, etc.).
# Use strip_ansi() to remove them from text.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return ANSI_RE.sub("", text)


def strip_leading_echo(text: str, command: str) -> str:
    """Drop a leading line that is the device echoing the command back.

    Half-duplex devices parrot the command before answering, so the reply
    reads as ``<command>\\n<answer>``.  When enabled (cfg
    ``strip_device_echo``), remove that first line if it exactly matches the
    sent command (whitespace-insensitive), leaving just the device's answer.
    A no-op when the first line does not match, so a device that does not
    echo is unaffected.
    """
    lines = text.splitlines()
    if lines and lines[0].strip() == command.strip():
        return "\n".join(lines[1:]).strip()
    return text


# -- Boolean parsing ---------------------------------------------------------

# Accepted truthy/falsy string tokens. Kept intentionally wide so users can
# type the form that feels natural without memorizing the allowed set.
# Hardware signal names like "high"/"low" are deliberately NOT here -- those
# live with port_control.parse_bool_value which is a separate domain.
_BOOL_TRUE = frozenset({"on", "1", "true", "yes", "y", "t"})
_BOOL_FALSE = frozenset({"off", "0", "false", "no", "n", "f"})


def parse_bool(val: str) -> bool | None:
    """Parse a boolean-like string from user input.

    Returns ``True`` for: on, 1, true, yes, y, t
    Returns ``False`` for: off, 0, false, no, n, f
    Returns ``None`` for anything else (unrecognized / empty).

    Case-insensitive. Leading and trailing whitespace is stripped.
    Use this anywhere a command accepts a user-facing boolean so the
    accepted tokens stay consistent across the app.

    Args:
        val: Raw string from user input or config.

    Returns:
        True, False, or None if the string is not a recognized boolean.
    """
    s = val.strip().lower()
    if s in _BOOL_TRUE:
        return True
    if s in _BOOL_FALSE:
        return False
    return None


def coerce_to_type(value_str: str, existing: object) -> object:
    """Coerce a string to match the type of an existing value.

    Used when a user supplies a config value as text and the target's
    current value tells us the expected type (bool/int/float/str).

    Args:
        value_str: Raw string from user input.
        existing: Current value whose type determines the conversion.

    Returns:
        ``value_str`` converted to the type of ``existing``.

    Raises:
        ValueError: If conversion fails (e.g. a non-boolean string for a
            bool field, or a non-numeric string for an int/float field).
    """
    if isinstance(existing, bool):
        result = parse_bool(value_str)
        if result is None:
            raise ValueError(f"Expected bool, got '{value_str}'")
        return result
    if isinstance(existing, int):
        return int(value_str)
    if isinstance(existing, float):
        return float(value_str)
    return value_str


def expand_template(
    text: str,
    counters: dict[int, int],
    start_time: str = "",
    *,
    elapsed_s: float | None = None,
) -> tuple[str, dict[int, int]]:
    """Expand the per-run placeholders {seqN}, {seqN+}, {starttime}, {elapsed}.

    Counters start at 0. seq1 is the top (most significant) level, seq2 the
    next, and so on. {seqN+} pre-increments counter N and substitutes the new
    value, resetting every deeper (higher-numbered) level > N to 0 -- so
    bumping an outer level restarts the inner ones. {seqN} without + substitutes
    the current value.

    {starttime} is the frozen per-run start stamp; {elapsed} is the time
    since start, rendered via ``format_duration``.  Ambient wall-clock
    stamps are NOT handled here -- they live in the $() variable system
    ($(TIME), $(DATETIME:%Y%m%d_%H%M%S)), which runs before this expander.

    Args:
        text: Template string containing placeholders.
        counters: Current sequence counter values keyed by level.
        start_time: Timestamp string set once at script start.
        elapsed_s: Seconds since start for {elapsed}; 0 when not in a run.

    Returns:
        Tuple of (expanded_text, updated_counters). Input dict is not mutated.
    """
    new_counters = dict(counters)

    def replace_seq(m: re.Match) -> str:
        level = int(m.group(1))
        if m.group(2) == "+":
            new_counters[level] = new_counters.get(level, 0) + 1
            # seq1 is the top level: bumping level N restarts every deeper
            # (higher-numbered) counter.
            for k in list(new_counters):
                if k > level:
                    new_counters[k] = 0
        return str(new_counters.get(level, 0))

    result = re.sub(r"\{seq(\d+)(\+)?\}", replace_seq, text)
    result = result.replace("{starttime}", start_time)
    if "{elapsed}" in result:
        result = result.replace("{elapsed}", format_duration(elapsed_s or 0.0))
    return result, new_counters


_DURATION_UNITS = {"us": 1e-6, "ms": 1e-3, "s": 1.0}


def parse_duration(text: str, *, default_unit: str | None = None) -> float:
    """Parse a duration string to seconds.

    This is the single duration parser for the whole app.  A bare ``0`` is
    zero regardless of unit.  Any other unitless value is rejected in the
    user-facing grammar (``default_unit=None``); protocol and config callers
    pass ``default_unit="ms"`` so a bare number -- as it arrives from ``.pro``
    directives and JSON profile fields -- is read as milliseconds.
    ``parse_duration_ms`` wraps this for callers that want whole milliseconds.

    Args:
        text: Duration like '500us', '25ms', '1.5s', or (with ``default_unit``
            set) a bare number.
        default_unit: 'us'/'ms'/'s' to apply to a unitless number, or None to
            require an explicit unit.

    Returns:
        Duration in seconds as a float.

    Raises:
        ValueError: If the input doesn't match a valid duration format.
    """
    text = text.strip().lower()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(us|ms|s)?$", text)
    if not m:
        raise ValueError(f"Invalid duration: {text!r}. Use e.g. 500us, 25ms, 1.5s")
    value = float(m.group(1))
    unit = m.group(2)
    if unit is None:
        if value == 0.0:
            return 0.0
        if default_unit is None:
            raise ValueError(
                f"Invalid duration: {text!r}. Use e.g. 500us, 25ms, 1.5s"
            )
        unit = default_unit
    return value * _DURATION_UNITS[unit]


def parse_duration_ms(text: str) -> int:
    """Parse a duration to whole milliseconds (protocol/config grammar).

    A bare number is read as milliseconds -- matching the values that come
    from ``.pro`` directives and JSON profile fields.  Rounds to the nearest
    millisecond.
    """
    return round(parse_duration(text, default_unit="ms") * 1000)


def format_duration(seconds: float) -> str:
    """Render a duration for humans: '480us', '25ms', or '1.50s'.

    Sub-millisecond values show whole microseconds, sub-second values show
    whole milliseconds, and anything >= 1s shows seconds to two decimals.
    This is the single display formatter for elapsed times, delays, and
    timeouts; raw data fields (``CmdResult.elapsed_s``, the ``.prof`` CSV,
    a results dict's ``elapsed_ms``) stay numeric.

    Args:
        seconds: A duration in seconds.

    Returns:
        A compact human-readable string with a unit suffix.
    """
    if seconds < 0.001:
        return f"{seconds * 1_000_000:.0f}us"
    if seconds < 1.0:
        return f"{seconds * 1000:.0f}ms"
    return f"{seconds:.2f}s"


def format_timestamp(dt: datetime | None = None) -> str:
    """Render a wall-clock timestamp as ``HH:MM:SS.mmm`` (millisecond precision).

    The single wall-clock formatter: used by the ``show_timestamps`` line
    prefix.  Defaults to the current local time when ``dt`` is omitted.

    Args:
        dt: The moment to render, or None for ``datetime.now()``.

    Returns:
        ``HH:MM:SS.mmm`` (microseconds truncated to milliseconds).
    """
    if dt is None:
        dt = datetime.now()
    return dt.strftime("%H:%M:%S.%f")[:-3]


def filename_timestamp() -> str:
    """Return a filename-safe timestamp: ``YYYYmmdd_HHMMSS`` (colon-free).

    One owner for the stamp embedded in screenshot / capture / recording
    filenames, so the format literal lives in exactly one place instead
    of being re-typed at every save site.
    """
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ── Line selection (shared count scheme) ──────────────────────────────────────

# A line-count token: an optional sign followed by digits.  Shared by every
# command that takes a "how many lines" argument so the parse stays uniform.
_COUNT_RE = re.compile(r"[+-]?\d+")


def select_lines(lines: list[str], n: int | None) -> list[str]:
    """Return the last n (n>0) or first n (n<0) lines; n is None -> all.

    The shared counting scheme behind ``/ss.txt``, ``/log.dump`` and
    ``/mcp.log.dump``: a positive N is the most-recent N (tail), a
    negative N is the oldest N (head).  Slicing clamps, so an ``abs(n)``
    larger than ``len(lines)`` yields every line.  Callers must reject
    ``n == 0`` before calling (``parse_count_arg`` does so).

    Args:
        lines: The lines to select from.
        n: Positive for the last N, negative for the first N, None for all.

    Returns:
        The selected lines (a slice of ``lines``).
    """
    if n is None:
        return lines
    return lines[-n:] if n > 0 else lines[:-n]


def parse_count_arg(args: str, default_name: str) -> tuple[str, int | None]:
    """Split a ``name``/``count`` argument string into ``(name, count)``.

    Tokenizes on whitespace.  A single token matching an optional-sign
    integer is the line count; the remaining tokens form the name
    (joined with spaces).  Order-independent: ``"cap 50"`` and
    ``"50 cap"`` both yield ``("cap", 50)``.  With no integer token the
    count is ``None`` and the name falls back to ``default_name``.

    Args:
        args: The raw argument string (e.g. from ``/ss.txt``).
        default_name: Name to use when no name token is present.

    Returns:
        ``(name, count)`` where count is None when no integer was given.

    Raises:
        ValueError: If more than one integer token is present, or the
            count is 0 (``-0 == 0`` is ambiguous; "all" is the no-arg
            form).  The message is ready for ``CmdResult.fail``.
    """
    tokens = args.split()
    int_tokens = [t for t in tokens if _COUNT_RE.fullmatch(t)]
    name_tokens = [t for t in tokens if not _COUNT_RE.fullmatch(t)]
    if len(int_tokens) > 1:
        raise ValueError(
            "Only one count allowed  (N>0 last N, N<0 first N)"
        )
    n = int(int_tokens[0]) if int_tokens else None
    if n == 0:
        raise ValueError("Invalid line count: 0")
    name = " ".join(name_tokens) or default_name
    return name, n


# ── Progress bar rendering ────────────────────────────────────────────────────

_PROGRESS_SUB_UNICODE = " \u2591\u2592\u2593\u2588"  # ░▒▓█
_PROGRESS_SUB_ASCII = " .-=#"


def render_progress_bar(
    elapsed: float,
    total: float,
    width: int = 30,
    ascii_only: bool = False,
) -> str:
    """Render a progress bar string with sub-character resolution.

    Returns ``[bar] Ns/Ms`` -- e.g. ``[███░░░░░] 3s/10s``.  Sub-step
    characters mean 1s-of-elapsed maps to a partial cell so short
    delays look like they're moving.  A full bar is reported exactly at
    ``elapsed >= total`` (so a caller rendering a final static line gets
    100%); it never reaches 100% before then.

    Both ``_hook_delay`` (interactive, TUI) and the script-path
    ``_script_delay`` (also TUI) share this helper so the two render
    sites stay byte-identical.
    """
    sub = _PROGRESS_SUB_ASCII if ascii_only else _PROGRESS_SUB_UNICODE
    sub_n = len(sub) - 1
    sub_steps = width * sub_n
    full_ch = sub[-1]
    if total <= 0:
        return f"[{full_ch * width}] 0s/0s"
    if elapsed >= total:
        # Finished state: a full bar.  Callers that render a final static
        # line (e.g. the CLI delay) get 100% here instead of re-deriving it.
        return f"[{full_ch * width}] {int(total)}s/{int(total)}s"
    frac = min(max(elapsed / total, 0.0), 1.0)
    pos = min(frac * sub_steps, sub_steps - 1)
    full = int(pos // sub_n)
    partial = int(pos % sub_n)
    bar = full_ch * full
    if full < width:
        bar += sub[partial] + " " * (width - full - 1)
    return f"[{bar}] {int(elapsed)}s/{int(total)}s"


# ── Keyword argument parsing ──────────────────────────────────────────────────

_KW_NORMALIZE_RE = re.compile(r"(\w+)\s*=\s*")


def parse_keywords(
    text: str,
    keywords: set[str],
    rest_keyword: str = "",
) -> dict[str, str]:
    """Parse key=value pairs from a command argument string.

    Handles spaces around ``=`` by normalizing ``key = value`` to
    ``key=value`` before parsing.  Unrecognized tokens accumulate
    under the ``_positional`` key.

    Args:
        text: Raw argument string
            (e.g. ``"timeout=2s quiet=on match=hello world"``).
        keywords: Set of recognized keyword names
            (e.g. ``{"timeout", "quiet", "match"}``).
        rest_keyword: If set, this keyword consumes everything to end
            of line.  Must appear last in the input (e.g. ``"match"``
            or ``"cmd"``).

    Returns:
        Dict mapping keyword name to value string.  Unrecognized tokens
        go under ``"_positional"``.  Missing keywords are absent.
    """
    # Normalize "key = value" and "key =value" to "key=value"
    text = _KW_NORMALIZE_RE.sub(r"\1=", text)

    result: dict[str, str] = {}

    # Extract rest_keyword first - it consumes everything after it
    if rest_keyword:
        rk_lower = rest_keyword.lower() + "="
        text_lower = text.lower()
        idx = text_lower.find(rk_lower)
        if idx != -1:
            result[rest_keyword.lower()] = text[idx + len(rk_lower):].strip()
            text = text[:idx]

    # Parse remaining tokens
    positional_parts: list[str] = []
    kw_lower = {k.lower() for k in keywords} - ({rest_keyword.lower()} if rest_keyword else set())
    for tok in text.split():
        matched = False
        for kw in kw_lower:
            if tok.lower().startswith(kw + "="):
                result[kw] = tok.split("=", 1)[1]
                matched = True
                break
        if not matched:
            positional_parts.append(tok)

    if positional_parts:
        result["_positional"] = " ".join(positional_parts)

    return result


# ── Sequence-numbered filenames ───────────────────────────────────────────────

_SEQ_RE = re.compile(r"\$\(n(0+)\)")
from termapy.folders import SEQ_FILE as _SEQ_FILE  # noqa: E402 -- with the seq-filename code below
_MAX_SEQ_WIDTH = 3


def resolve_seq_filename(filename: str, directory: Path) -> str:
    """Expand ``$(n000)``-style sequence placeholders in a filename.

    The number of zeros sets the digit width (max 3).  A counter file
    (``.cap_seq``) in *directory* tracks the last-used number per pattern
    so the sequence persists across sessions.

    Args:
        filename: Filename that may contain a ``$(n0+)`` placeholder.
        directory: Directory where the counter file lives (usually cap/).

    Returns:
        Filename with the placeholder replaced by the next sequence number.

    Raises:
        ValueError: If the digit width exceeds the maximum.
    """
    m = _SEQ_RE.search(filename)
    if not m:
        return filename

    zeros = m.group(1)
    width = len(zeros)
    if width > _MAX_SEQ_WIDTH:
        raise ValueError(
            f"$(n{zeros}) too wide - max {_MAX_SEQ_WIDTH} digits."
        )

    max_num = 10**width - 1
    pattern_key = filename  # use the un-resolved pattern as the dict key

    # Read counter file
    seq_path = directory / _SEQ_FILE
    counters: dict[str, int] = {}
    try:
        counters = json.loads(seq_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        pass

    last = counters.get(pattern_key, -1)
    next_num = (last + 1) % (max_num + 1)

    # Write counter back
    counters[pattern_key] = next_num
    try:
        directory.mkdir(parents=True, exist_ok=True)
        seq_path.write_text(
            json.dumps(counters, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        pass

    return _SEQ_RE.sub(f"{next_num:0{width}d}", filename)
