"""Template expansion and script parsing for termapy REPL commands.

Pure functions with no Textual or serial dependencies.
"""

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


# ── Sequence-numbered filenames ───────────────────────────────────────────────

_SEQ_RE = re.compile(r"\$\(n(0+)\)")
_SEQ_FILE = ".cap_seq"
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
