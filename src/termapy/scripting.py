"""Template expansion, script parsing, and shared utilities for termapy.

Pure functions and dataclasses with no Textual or serial dependencies.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


# Shared ANSI escape regex - matches all CSI sequences (color, cursor, clear, etc.).
# Use strip_ansi() to remove them from text.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return ANSI_RE.sub("", text)


def expand_template(
    text: str, counters: dict[int, int], start_time: str = ""
) -> tuple[str, dict[int, int]]:
    """Expand {seqN}, {seqN+}, {datetime}, {starttime} placeholders in text.

    Counters start at 0. {seqN+} pre-increments counter N and substitutes
    the new value. Incrementing level N resets all levels < N to 0.
    {seqN} without + substitutes the current value.

    Args:
        text: Template string containing placeholders.
        counters: Current sequence counter values keyed by level.
        start_time: Timestamp string set once at script start.

    Returns:
        Tuple of (expanded_text, updated_counters). Input dict is not mutated.
    """
    new_counters = dict(counters)

    def replace_seq(m: re.Match) -> str:
        level = int(m.group(1))
        if m.group(2) == "+":
            new_counters[level] = new_counters.get(level, 0) + 1
            for k in list(new_counters):
                if k < level:
                    new_counters[k] = 0
        return str(new_counters.get(level, 0))

    result = re.sub(r"\{seq(\d+)(\+)?\}", replace_seq, text)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    result = result.replace("{datetime}", ts)
    result = result.replace("{starttime}", start_time)
    return result, new_counters


def parse_duration(text: str) -> float:
    """Parse a duration string to seconds.

    Args:
        text: Duration string like '500ms', '1s', '1.5s'.

    Returns:
        Duration in seconds as a float.

    Raises:
        ValueError: If the input doesn't match a valid duration format.
    """
    text = text.strip().lower()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(ms|s)$", text)
    if not m:
        raise ValueError(f"Invalid duration: {text!r}. Use e.g. 500ms, 1.5s")
    value = float(m.group(1))
    unit = m.group(2)
    return value / 1000.0 if unit == "ms" else value


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

    # Extract rest_keyword first — it consumes everything after it
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
from termapy.folders import SEQ_FILE as _SEQ_FILE  # noqa: E402
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
