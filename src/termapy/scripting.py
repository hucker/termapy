"""Template expansion and script parsing for termapy REPL commands.

Pure functions with no Textual or serial dependencies.
"""

import re
from datetime import datetime


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


def parse_script_lines(
    lines: list[str], prefix: str = "/"
) -> list[tuple[str, str]]:
    """Classify script lines for the /run command.

    Args:
        lines: Raw lines from a script file.
        prefix: REPL command prefix to detect local commands.

    Returns:
        List of (kind, content) tuples where kind is one of:
            'skip'   — blank line or comment (starts with #)
            'repl'   — REPL command (prefix stripped)
            'serial' — plain text to send to the device
    """
    result = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            result.append(("skip", stripped))
        elif stripped.startswith(prefix):
            result.append(("repl", stripped[len(prefix) :].strip()))
        else:
            result.append(("serial", stripped))
    return result
