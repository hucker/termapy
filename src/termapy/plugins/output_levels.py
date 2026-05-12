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


#: Canonical level names, ordered from quietest to loudest.
OUTPUT_LEVELS: tuple[str, ...] = ("silent", "quiet", "normal", "verbose")

#: Default level when nothing has been set explicitly.
DEFAULT_OUTPUT_LEVEL = "normal"

#: Per-level rank.  Higher rank = louder.
OUTPUT_LEVEL_RANK: dict[str, int] = {
    name: rank for rank, name in enumerate(OUTPUT_LEVELS)
}

#: Channel-to-minimum-rank mapping.  A channel writes only when the
#: active level's rank is at least this.
RESULT_MIN_RANK = OUTPUT_LEVEL_RANK["quiet"]    # quiet, normal, verbose
OUTPUT_MIN_RANK = OUTPUT_LEVEL_RANK["normal"]   # normal, verbose
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
# Every command that emits a "label: value" table -- /term.info, /term.usb_db,
# /port.info, /proto.crc.info, etc. -- routes through ``format_kv_lines()``
# below so they render with one consistent style:
#
#   - Two-space indent.
#   - Cyan label, padded to the widest in the set.
#   - Colon + single space between label and value.
#   - Optional per-row color baked into the value via Rich markup.
#
# Adding a new info command?  Build ``[(label, value), ...]``, call
# ``format_kv_lines()``, write each line via ``ctx.io._write_markup()``.  Don't
# roll your own padding -- consistency across info commands matters.


def format_kv_lines(
    rows: "list[tuple[str, str]]",
    indent: str = "  ",
    label_color: str = "cyan",
) -> "list[str]":
    """Render a list of ``(label, value)`` pairs as cyan-key markup lines.

    Pads labels to the widest in the set and adds a colon-space
    separator between label and value.  Returns a list of markup
    strings ready to pass to ``ctx.io._write_markup()``.

    Per-row coloring of the *value* is the caller's responsibility:
    embed Rich markup directly in the value string (e.g.
    ``"[yellow]warning[/]"``) and it'll render on top of the cyan
    label.  The label itself is always rendered in ``label_color``
    (default cyan) for consistency.

    Args:
        rows: Sequence of ``(label, value)`` tuples.
        indent: String prefix on each line (default two spaces).
        label_color: Rich color name for the label (default
            ``"cyan"``).

    Returns:
        A list of pre-formatted markup strings, one per row.  Empty
        list if ``rows`` is empty.
    """
    if not rows:
        return []
    width = max(len(label) for label, _ in rows)
    return [
        f"{indent}[{label_color}]{label:<{width}}[/]: {value}"
        for label, value in rows
    ]
