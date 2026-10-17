"""Output-level vocabulary shared between :mod:`PluginContext` and :class:`IOHandle`.

A single dial controls how loud commands are.  Four monotonic levels
stratify the three output channels (result, output, status).  Set the
default for the session with ``/term.output <level>``; override for a
single call with ``cmd --<level>`` or ``cmd.<level>``.

This module also hosts :func:`format_kv_lines`, the shared helper for
``label: value`` table rendering used by every info-style command
(``/term.info``, ``/port.info``, ``/proto.crc.info``, ...).
"""

from __future__ import annotations

from collections.abc import Sequence

from termapy.markup import escape_markup

#: Canonical level names, ordered from quietest to loudest.
OUTPUT_LEVELS: tuple[str, ...] = ("silent", "quiet", "normal", "verbose")

#: Default level when nothing has been set explicitly.
DEFAULT_OUTPUT_LEVEL = "normal"

#: Per-level rank.  Higher rank = louder.
OUTPUT_LEVEL_RANK: dict[str, int] = {name: rank for rank, name in enumerate(OUTPUT_LEVELS)}

#: Channel-to-minimum-rank mapping.  A channel writes only when the
#: active level's rank is at least this.
RESULT_MIN_RANK = OUTPUT_LEVEL_RANK["quiet"]  # quiet, normal, verbose
OUTPUT_MIN_RANK = OUTPUT_LEVEL_RANK["normal"]  # normal, verbose
STATUS_MIN_RANK = OUTPUT_LEVEL_RANK["verbose"]  # verbose only

#: Per-call flag tokens that override the level for one dispatch.  Stripped
#: from args before per-command flag parsing in ``ReplEngine.dispatch``.
LEVEL_FLAGS: dict[str, str] = {f"--{name}": name for name in OUTPUT_LEVELS}


def parse_output_level(s: str) -> str | None:
    """Return the canonical level name for ``s``, or None if unknown."""
    s = s.strip().lower()
    return s if s in OUTPUT_LEVEL_RANK else None


# ─────────────────────────────────────────────────────────────────────────────
# Shared key/value rendering for info-style commands
# ─────────────────────────────────────────────────────────────────────────────
#
# Every status page -- /term.info, /mcp.info, /mem.info, /proto.crc.info,
# etc. -- renders through ``format_kv_lines()`` below so they read with one
# consistent style:
#
#   - Two-space indent.
#   - Cyan label, padded to the widest in the set.
#   - Colon + single space between label and value.
#   - Optional per-row value color, named as a third row element.
#
# Adding a new status page?  Don't call this: return
# ``CmdResult.record(data, rows, value=...)`` and dispatch renders the rows
# through ``views.RecordView``, the only caller (registry
# ``record-one-format``), or hands ``data`` alone to a caller that wants it.


def format_kv_lines(
    rows: Sequence[tuple[str, str] | tuple[str, str, str]],
    indent: str = "  ",
    label_color: str = "cyan",
) -> list[str]:
    """Render ``(label, value)`` rows as cyan-key markup lines.

    Pads labels to the widest in the set and adds a colon-space
    separator between label and value.  Labels and values are text:
    both are escaped, so a value holding ``[ok]`` or a path ending in a
    backslash renders as itself (registry ``markup-escape``).  A row
    that wants its value colored names the color as a third element,
    ``("Permissions", "denied", "red")``; the label is always
    ``label_color``.

    Args:
        rows: ``(label, value)`` or ``(label, value, color)`` tuples.
        indent: String prefix on each line (default two spaces).
        label_color: Rich color name for the label (default
            ``"cyan"``).

    Returns:
        A list of markup strings for ``ctx.io.output_markup()``, one per
        row.  Empty list if ``rows`` is empty.
    """
    if not rows:
        return []
    width = max(len(row[0]) for row in rows)
    lines: list[str] = []
    for label, value, *color in rows:
        label_markup = escape_markup(f"{label:<{width}}")
        value_markup = escape_markup(value)
        if color:
            value_markup = f"[{color[0]}]{value_markup}[/]"
        lines.append(indent + f"[{label_color}]{label_markup}[/]: {value_markup}")
    return lines
