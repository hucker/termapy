"""Built-in plugin: user-defined variables with $(NAME) syntax."""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING

from termapy.plugins import Command, Transform

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

# Module-level variable storage — cleared on script start.
_VARS: dict[str, str] = {}

# Match $(NAME) — letters, digits, underscore; must start with letter or _
_VAR_REF_RE = re.compile(r"\$\(([A-Za-z_][A-Za-z0-9_]*)\)")

# Match $(NAME) = value assignment (with or without spaces around =)
_VAR_ASSIGN_RE = re.compile(r"^\$\(([A-Za-z_][A-Za-z0-9_]*)\)\s*=\s*(.+)$")

# Match bare $NAME = value (old syntax) for helpful warning
_BARE_ASSIGN_RE = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)\s*=\s*.+$")

# Match optional $(NAME) or bare NAME wrapper for user input stripping
_STRIP_WRAPPER_RE = re.compile(r"^\$\((.+)\)$")


def clear_vars() -> None:
    """Clear all user variables."""
    _VARS.clear()


def set_start_time_vars() -> None:
    """Set $(SESSION_DATE), $(SESSION_TIME), and $(SESSION_DATETIME).

    Called once when a top-level script starts (Scripts button / Run menu).
    These are NOT reset by interactive ``/run`` calls so they reflect
    the original start time of the session.
    """
    now = datetime.now()
    _VARS["SESSION_DATE"] = now.strftime("%Y-%m-%d")
    _VARS["SESSION_TIME"] = now.strftime("%H:%M:%S")
    _VARS["SESSION_DATETIME"] = now.strftime("%Y-%m-%d %H:%M:%S")


def rewrite_assignment(line: str) -> str | None:
    """Rewrite ``$(VAR) = value`` into ``var.set VAR value``.

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


def check_bare_dollar(line: str) -> str | None:
    """Check for bare ``$NAME = value`` and return a warning message.

    Args:
        line: Raw input line.

    Returns:
        Warning string if bare syntax detected, or None.
    """
    m = _BARE_ASSIGN_RE.match(line)
    if m:
        name = m.group(1)
        return f"Did you mean $({name}) = ...?  Variables use $(NAME) syntax."
    return None


# Launch-time variables — frozen when the module loads (app start).
_LAUNCH_TIME = datetime.now()
_LAUNCH_VARS: dict[str, str] = {
    "LAUNCH_DATE": _LAUNCH_TIME.strftime("%Y-%m-%d"),
    "LAUNCH_TIME": _LAUNCH_TIME.strftime("%H:%M:%S"),
    "LAUNCH_DATETIME": _LAUNCH_TIME.strftime("%Y-%m-%d %H:%M:%S"),
}

# Dynamic built-in variables — resolved at expansion time.
_DYNAMIC_VARS: dict[str, str] = {
    "DATE": "%Y-%m-%d",
    "TIME": "%H:%M:%S",
    "DATETIME": "%Y-%m-%d %H:%M:%S",
}


_ESCAPE_SENTINEL = "\x00"


def expand_vars(text: str) -> str:
    """Expand $(NAME) references in a string.

    Resolution order:
    1. User-defined variables in ``_VARS``
    2. Dynamic built-ins (``$(DATE)``, ``$(TIME)``, ``$(DATETIME)``) — current clock
    3. Unknown names are left as literal ``$(NAME)``

    Use ``\\$`` to escape a literal ``$`` (e.g. ``\\$(PORT)`` → ``$(PORT)``).

    Args:
        text: String potentially containing $(NAME) references.

    Returns:
        String with known variables expanded.
    """
    # Swap \$ → sentinel so the regex doesn't see it as a var reference
    text = text.replace("\\$", _ESCAPE_SENTINEL)

    def _replace(m: re.Match) -> str:
        name = m.group(1)
        val = _VARS.get(name)
        if val is not None:
            return val
        val = _LAUNCH_VARS.get(name)
        if val is not None:
            return val
        fmt = _DYNAMIC_VARS.get(name)
        if fmt is not None:
            return datetime.now().strftime(fmt)
        return m.group(0)

    text = _VAR_REF_RE.sub(_replace, text)
    # Restore sentinel → literal $
    return text.replace(_ESCAPE_SENTINEL, "$")


# -- Handlers ----------------------------------------------------------------


def _handler_list(ctx: PluginContext, args: str) -> None:
    """List all defined variables, or show one by name.

    Args:
        ctx: Plugin context for output.
        args: Optional variable name to show.
    """
    raw = args.strip()
    m = _STRIP_WRAPPER_RE.match(raw)
    name = m.group(1) if m else raw
    if name:
        val = _VARS.get(name)
        if val is None:
            val = _LAUNCH_VARS.get(name)
        if val is None:
            fmt = _DYNAMIC_VARS.get(name)
            if fmt is not None:
                val = datetime.now().strftime(fmt)
        if val is not None:
            ctx.write(f"  $({name}) = {val}")
        else:
            ctx.write(f"  $({name}) — not defined", "red")
        return
    if not _VARS and not _LAUNCH_VARS and not _DYNAMIC_VARS:
        ctx.write("  (no variables defined)")
        return
    for k in sorted(_VARS):
        ctx.write(f"  $({k}) = {_VARS[k]}")
    for k in sorted(_LAUNCH_VARS):
        ctx.write(f"  $({k}) = {_LAUNCH_VARS[k]}  (launch)")
    now = datetime.now()
    for k, fmt in sorted(_DYNAMIC_VARS.items()):
        ctx.write(f"  $({k}) = {now.strftime(fmt)}  (dynamic)")


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
    m = _STRIP_WRAPPER_RE.match(parts[0])
    name = m.group(1) if m else parts[0]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        ctx.write("Variable names must be letters, digits, or underscore", "red")
        return
    value = parts[1]
    _VARS[name] = value
    ctx.write(f"  $({name}) = {value}", "green")


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
User-defined variables use $(NAME) syntax (case-sensitive).

Setting variables (no / prefix needed):
  $(PORT) = COM7
  $(ADDR) = 01

Using variables in commands:
  /print $(PORT)
  AT+PORT=$(PORT)

Commands:
  /var               — list all variables
  /var PORT          — show one variable (bare name or $(PORT))
  /var.set PORT val  — set a variable (bare name or $(PORT))
  /var.clear         — clear all variables

Dynamic variables (current clock at point of use):
  $(DATE)              — current date (YYYY-MM-DD)
  $(TIME)              — current time (HH:MM:SS)
  $(DATETIME)          — current date and time

Launch variables (frozen when the app starts):
  $(LAUNCH_DATE)       — app start date
  $(LAUNCH_TIME)       — app start time
  $(LAUNCH_DATETIME)   — app start date and time

Session variables (set once per script launch, frozen):
  $(SESSION_DATE)      — script start date
  $(SESSION_TIME)      — script start time
  $(SESSION_DATETIME)  — script start date and time

Escaping (when your data contains literal $):
  \\$(PORT)           — literal $(PORT) (not expanded)
  /raw $(GPS),NMEA,0 — send entire line verbatim (no expansion)

Scope: variables persist for the session. They are cleared
automatically when a script is launched from the Scripts button
or the Run menu, but NOT when /run is typed interactively.
Use /var.clear to reset manually.""",
    handler=_handler_list,
    raw_args=True,
    sub_commands={
        "set": Command(
            args="<NAME> <value>",
            help="Set a user variable.",
            handler=_handler_set,
            raw_args=True,
        ),
        "clear": Command(
            help="Clear all user variables.",
            handler=_handler_clear,
            raw_args=True,
        ),
    },
)

TRANSFORM = Transform(
    name="var",
    help="Expand $(NAME) placeholders from user-defined variables.",
    repl=expand_vars,
    serial=expand_vars,
)
