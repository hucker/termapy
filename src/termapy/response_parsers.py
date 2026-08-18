"""Pure response parsers for the profile response formats.

The MCP bridge applies one of these formats to every device response,
turning raw text into structured data the LLM consumes.  Formats:

- ``none``   -- fire-and-forget; returns ``None``.
- ``text``   -- whole response as one unstructured string.  The right
                choice for human-oriented output (help screens, dumps);
                needs no pattern.
- ``literal`` -- response must equal ``pattern`` exactly (after strip).
- ``lines``  -- splits on newlines; optional ``line_pattern`` parses each
                line into a typed dict.  Produces a list.
- ``regex``  -- ``re.search`` with named groups becoming a typed dict;
                unnamed groups become a list; no groups returns the
                matched substring.
- ``json``   -- parses the response as JSON.

Forward compatibility: an *unrecognized* format name degrades to
``text`` -- the caller still gets the raw response string instead of a
failure, so profiles written for a newer spec revision stay usable on
older hosts (see docs/profile-spec.md).  The same policy applies to
coercion names in ``types`` maps: unknown names leave the value as str
(see ``_coerce``).

This module is pure and stateless.  It intentionally ``never raises``
on bad text -- callers expect a graceful ``None`` (or empty result) so
device noise doesn't crash the bridge.

The same module is used by ``/cap.poll`` which needs the same regex
extraction pipeline.  Keeping the parsers in one place ensures both
paths behave identically.
"""

from __future__ import annotations

import json
import re
from typing import Any

from termapy.scripting import parse_bool

# ── Type coercion ────────────────────────────────────────────────────────────


def _coerce(value: str, type_name: str) -> Any:
    """Coerce a captured string to the declared profile type.

    Returns the raw string on coercion failure -- never raises.  This
    matches the "be permissive on parse" rule: if a regex matched, we'd
    rather hand the LLM the raw string than fail the whole response.
    """
    type_name = type_name.lower()
    if type_name == "int":
        try:
            return int(value, 10)
        except ValueError:
            return value
    if type_name == "float":
        try:
            return float(value)
        except ValueError:
            return value
    if type_name == "hex":
        try:
            return int(value, 16)
        except ValueError:
            return value
    if type_name == "bool":
        b = parse_bool(value)
        return b if b is not None else value
    # str (default)
    return value


def _coerce_dict(d: dict[str, str], types: dict[str, str] | None) -> dict[str, Any]:
    """Apply ``types`` to a captured-groupdict, leaving unknown keys as str."""
    if not types:
        return dict(d)
    out: dict[str, Any] = {}
    for k, v in d.items():
        if k in types:
            out[k] = _coerce(v, types[k])
        else:
            out[k] = v
    return out


# ── Format dispatch ──────────────────────────────────────────────────────────


def parse_response(
    text: str,
    fmt: str,
    pattern: str = "",
    types: dict[str, str] | None = None,
    *,
    line_pattern: str = "",
    line_types: dict[str, str] | None = None,
    terminator: str = "",
) -> Any:
    """Parse a device response per the declared format.

    Args:
        text: Raw response text from the device (after echo/prompt strip).
        fmt: One of ``"none" | "text" | "literal" | "lines" | "regex" |
            "json"``; unrecognized values degrade to ``"text"``.
        pattern: Regex (regex/lines line-filter) or literal (literal).
        types: Type coercion map for named regex groups in ``pattern``.
        line_pattern: For ``lines``: optional per-line regex with named
            groups; produces a list of dicts.
        line_types: Type coercion for ``line_pattern`` groups.
        terminator: For ``lines``: optional regex matching the line that
            ends collection.  Lines are kept up to but not including the
            terminator line.

    Returns:
        Parsed value, shape depending on format:
            none     -> None
            text     -> the response text unchanged (str)
            literal  -> stripped text if it equals pattern, else None
            lines    -> list[str] or list[dict] (if line_pattern provided)
            regex    -> str | list[str] | dict[str, Any] | None
            json     -> any JSON-parseable value, or None on parse failure

        Unknown format degrades to ``text`` (returns the raw string) so
        a profile from a newer spec revision stays usable on this host.
    """
    if fmt == "none":
        return None

    if fmt == "literal":
        return text.strip() if text.strip() == pattern else None

    if fmt == "json":
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None

    if fmt == "lines":
        return _parse_lines(text, pattern, line_pattern, line_types, terminator)

    if fmt == "regex":
        return _parse_regex(text, pattern, types)

    # "text" and any unrecognized future format: hand back the raw
    # response string -- degrade, never fail (compatibility policy).
    return text


# ── Per-format implementations ───────────────────────────────────────────────


def _parse_regex(
    text: str, pattern: str, types: dict[str, str] | None
) -> Any:
    """Apply ``re.search`` and return a typed shape based on group structure."""
    if not pattern:
        return None
    try:
        m = re.search(pattern, text)
    except re.error:
        return None
    if m is None:
        return None
    if m.groupdict():
        return _coerce_dict(m.groupdict(), types)
    if m.groups():
        # Anonymous groups; coerce by position only if types maps integer
        # keys, else return raw strings.  Profiles don't currently support
        # positional types, so just return the tuple of strings.
        return list(m.groups())
    return m.group(0)


def _parse_lines(
    text: str,
    line_filter: str,
    line_pattern: str,
    line_types: dict[str, str] | None,
    terminator: str,
) -> list:
    """Split into lines, optionally filter, optionally parse each line."""
    raw_lines = text.splitlines()
    # Apply terminator: keep lines up to but not including the terminator.
    if terminator:
        try:
            term_re = re.compile(terminator)
        except re.error:
            term_re = None
        if term_re is not None:
            cut = []
            for raw_line in raw_lines:
                if term_re.search(raw_line):
                    break
                cut.append(raw_line)
            raw_lines = cut
    # Apply line_filter (kept lines must match this regex).
    if line_filter:
        try:
            filt_re = re.compile(line_filter)
        except re.error:
            filt_re = None
        if filt_re is not None:
            raw_lines = [raw_line for raw_line in raw_lines if filt_re.search(raw_line)]
    # Per-line parsing.
    if not line_pattern:
        return raw_lines
    try:
        line_re = re.compile(line_pattern)
    except re.error:
        return raw_lines
    out: list = []
    for raw_line in raw_lines:
        m = line_re.search(raw_line)
        if m is None:
            continue
        if m.groupdict():
            out.append(_coerce_dict(m.groupdict(), line_types))
        elif m.groups():
            out.append(list(m.groups()))
        else:
            out.append(m.group(0))
    return out
