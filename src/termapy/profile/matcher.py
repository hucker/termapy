"""Match a typed command line against a profile's command catalog.

Pure module: no I/O, no transport, no engine deps.  Used by both the
MCP dispatch executor and the CLI dispatch path to decide "does this
text invoke a profile entry, and if so, what are the bound args?"

Lookup precedence:

1. **Exact name match** — the common case.  Both the LLM via MCP and a
   human typist usually invoke commands by their canonical name straight
   from /help or the catalog.
2. **send_template regex match** — for entries that take inline args
   (``AT+LED={state}``, ``bat {channel}``), this turns each ``{name}``
   placeholder into a named capture group and matches the input.  The
   first hit wins; profile authors are expected to avoid overlapping
   templates.

A successful match returns ``(canonical_name, command_spec, bound_args)``
where ``bound_args`` is a dict of placeholder name -> raw matched text.
The caller then validates ``bound_args`` against the profile's type
registry (see :mod:`termapy.profile.types`) before sending bytes.

Library use:

    from termapy.profile import match_profile_command, TypeRegistry

    match = match_profile_command(user_input, profile["commands"])
    if match is not None:
        name, spec, bound = match
        registry = TypeRegistry.from_profile(profile)
        for ta in spec.get("typed_args", []):
            outcome = registry.validate(ta["type"], bound[ta["name"]])
            ...
"""

from __future__ import annotations

import re


def template_to_regex(template: str) -> str:
    """Turn a ``send_template`` string into an anchored match regex.

    Each ``{name}`` placeholder becomes ``(?P<name>.+?)``; surrounding
    literal text is regex-escaped so dots, plus signs, parentheses etc.
    in the template behave literally.  Anchored at both ends so partial
    matches don't confuse lookup.

    Example::

        template_to_regex("AT+LED={state}")  # -> r"^AT\\+LED\\=(?P<state>.+?)$"
        template_to_regex("bat {channel}")    # -> r"^bat\\ (?P<channel>.+?)$"
    """
    pattern_parts: list[str] = []
    pos = 0
    for m in re.finditer(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", template):
        pattern_parts.append(re.escape(template[pos:m.start()]))
        pattern_parts.append(f"(?P<{m.group(1)}>.+?)")
        pos = m.end()
    pattern_parts.append(re.escape(template[pos:]))
    return "^" + "".join(pattern_parts) + "$"


def match_profile_command(
    text: str, commands: dict[str, dict],
) -> tuple[str, dict, dict[str, str]] | None:
    """Find the profile command that ``text`` invokes, if any.

    Args:
        text: The raw command line as the user / LLM typed it (no
            REPL prefix).
        commands: The profile's ``commands`` dict.

    Returns:
        ``(canonical_name, command_spec, bound_args)`` on a hit;
        ``None`` if no profile entry matches.  ``bound_args`` is empty
        for exact-name matches and populated from named capture groups
        for ``send_template`` matches.
    """
    if text in commands:
        return text, commands[text], {}
    for name, spec in commands.items():
        if not isinstance(spec, dict):
            continue
        tmpl = spec.get("send_template", "")
        if not tmpl:
            continue
        try:
            m = re.match(template_to_regex(tmpl), text)
        except re.error:
            continue
        if m:
            return name, spec, m.groupdict()
    return None
