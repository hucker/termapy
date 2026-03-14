"""Built-in plugin: user-defined variables with $VAR syntax."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from termapy.plugins import Command, Transform

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

# Module-level variable storage — cleared on script start.
_VARS: dict[str, str] = {}

# Match $VAR or $var (letters, digits, underscore; must start with letter or _)
_VAR_REF_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")

# Match $VAR = value assignment (with or without spaces around =)
_VAR_ASSIGN_RE = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$")


def clear_vars() -> None:
    """Clear all user variables."""
    _VARS.clear()


def rewrite_assignment(line: str) -> str | None:
    """Rewrite ``$VAR = value`` into ``var.set VAR value``.

    Called very early in dispatch, before the REPL/serial decision.
    Returns the rewritten REPL command string, or None if the line
    is not a variable assignment.

    Args:
        line: Raw input line.

    Returns:
        Rewritten command for REPL dispatch, or None.
    """
    m = _VAR_ASSIGN_RE.match(line)
    if m:
        return f"var.set {m.group(1)} {m.group(2)}"
    return None


def expand_vars(text: str) -> str:
    """Expand $VAR references in a string.

    Known variables are replaced with their values.
    Unknown variables are left as literal $VAR text.

    Args:
        text: String potentially containing $VAR references.

    Returns:
        String with known variables expanded.
    """
    def _replace(m: re.Match) -> str:
        name = m.group(1)
        return _VARS.get(name, m.group(0))

    return _VAR_REF_RE.sub(_replace, text)


# -- Handlers ----------------------------------------------------------------


def _handler_list(ctx: PluginContext, args: str) -> None:
    """List all defined variables, or show one by name.

    Args:
        ctx: Plugin context for output.
        args: Optional variable name to show.
    """
    name = args.strip().lstrip("$")
    if name:
        val = _VARS.get(name)
        if val is not None:
            ctx.write(f"  ${name} = {val}")
        else:
            ctx.write(f"  ${name} — not defined", "red")
        return
    if not _VARS:
        ctx.write("  (no variables defined)")
        return
    for k in sorted(_VARS):
        ctx.write(f"  ${k} = {_VARS[k]}")


def _handler_set(ctx: PluginContext, args: str) -> None:
    """Set a user variable.

    Args:
        ctx: Plugin context for output.
        args: ``"NAME value"`` string (both required).
    """
    parts = args.strip().split(None, 1)
    if len(parts) < 2:
        ctx.write("Usage: /var.set <NAME> <value>", "red")
        return
    name = parts[0].lstrip("$")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        ctx.write("Variable names must be letters, digits, or underscore", "red")
        return
    value = parts[1]
    _VARS[name] = value
    ctx.write(f"  ${name} = {value}", "green")


def _handler_clear(ctx: PluginContext, args: str) -> None:
    """Clear all user variables.

    Args:
        ctx: Plugin context for output.
        args: Unused.
    """
    count = len(_VARS)
    _VARS.clear()
    ctx.write(f"Cleared {count} variable(s).", "green")


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="var",
    args="{name}",
    help="List user variables, or show one by name.",
    long_help="""\
User-defined variables use $NAME syntax (case-sensitive).

Assignment (no / prefix needed):
  $PORT = COM7
  $ADDR = 01

Use in commands:
  /print $PORT
  AT+PORT=$PORT

Commands:
  /var               — list all variables
  /var $NAME         — show one variable
  /var.set NAME val  — set a variable
  /var.clear         — clear all variables

Variables are automatically cleared when a script starts.""",
    handler=_handler_list,
    sub_commands={
        "set": Command(
            args="<NAME> <value>",
            help="Set a user variable.",
            handler=_handler_set,
        ),
        "clear": Command(
            help="Clear all user variables.",
            handler=_handler_clear,
        ),
    },
)

TRANSFORM = Transform(
    name="var",
    help="Expand $VAR placeholders from user-defined variables.",
    repl=expand_vars,
    serial=expand_vars,
)
