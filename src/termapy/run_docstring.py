"""Parse a `.run` script file's leading docstring block.

Convention (matches the Python docstring shape):

  A contiguous block of ``#`` comment lines at the **very top** of
  the file is the script's docstring.  The first line is the
  summary (shown by ``/run.list``); the full block is the long
  help (shown by ``/run.help <script>``).  The block ends at the
  first blank line, non-``#`` line, or end-of-file.

Examples accepted:

    # Smoke-test the device after power cycle.
    /port.connect
    /cap.text smoke.txt timeout=2s cmd=AT+VER

and

    # Smoke-test the device after power cycle.
    #
    # Connects, waits for VER, captures 2s of output to smoke.txt.
    # Used by the post-build CI gate.

    /port.connect
    ...

What is NOT a docstring (by design):

- Comment blocks anywhere except the first physical line.
- Comments preceded by blank lines at the top.  Strict "at the
  top" matches Python's docstring semantics; lenient skipping
  would let trailing blank lines in saved scripts hide the
  docstring accidentally.

This is intentionally a 30-line one-shot parser, not a schema.
Anything richer (typed args, capability gates, version fields)
would be the moment a ``.run`` file stops being "a recorded REPL
session" and becomes a worse scripting language -- write a plugin
instead.
"""

from __future__ import annotations

from pathlib import Path


def extract_docstring(path: Path) -> tuple[str, str]:
    """Return ``(summary, full)`` for the file's leading ``#`` block.

    Args:
        path: The .run file to read.

    Returns:
        Tuple of ``(summary, full)``.  ``summary`` is the first
        comment line (stripped); ``full`` is the whole block joined
        with newlines, ``#`` and one leading space stripped from each
        line.  Both are ``""`` when the file has no leading docstring
        (no comment on line 1, missing file, read error, etc.).
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", ""

    lines: list[str] = []
    for raw in text.splitlines():
        if not raw.startswith("#"):
            break
        # Strip the '#' and one optional space.
        content = raw[1:]
        if content.startswith(" "):
            content = content[1:]
        lines.append(content)

    if not lines:
        return "", ""
    full = "\n".join(lines)
    summary = lines[0].strip()
    return summary, full
