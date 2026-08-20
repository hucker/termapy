"""Variable engine: the ``$(NAME)`` namespace, storage, and expansion.

This is core infrastructure, not a plugin.  Every frontend (``app.py``,
``cli.py``, ``mcp/server.py``) registers context variables here at startup,
and ``repl.py`` expands ``$(NAME)`` on the dispatch path, so the engine has
to sit on the core side of the plugin boundary -- core depending on a file
under ``builtins/`` would invert the direction the plugin system is built on.

``builtins/commands/var.py`` is the *command surface* on top of this module:
it owns ``/var``, the ``$(NAME) = value`` directive, and the ``$()`` transform
registration, and it holds no state of its own.  Same split as
``port_control.py`` (engine) and ``builtins/commands/port.py`` (command).

Four namespaces resolve here, in this order:

  - **user vars** -- set by ``/var.set`` or ``$(NAME) = value``, cleared when
    a top-level script starts.
  - **launch vars** -- plain strings frozen at startup (e.g. ``FRONT_END``).
  - **datetime vars** -- the dynamic clock (``DATE`` / ``TIME`` / ``DATETIME``)
    plus frozen moments (``LAUNCH_*``, ``SESSION_*``), each accepting an
    optional ``:strftime`` suffix.
  - **context vars** -- resolved through a callable at expansion time, which
    is how ``CFG.*`` tracks a config that can change mid-session.

State is module level on purpose: one namespace per process, shared across
every ``PluginContext`` in the session.  Nothing here imports Textual,
pyserial, or anything under ``builtins/``.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Callable

# Match $(NAME) or $(NAME.SUB), with an optional :strftime format on
# datetime-valued vars -- e.g. $(DATETIME:%Y%m%d_%H%M%S).  The format runs
# to the closing paren and may itself contain colons (%H:%M:%S), so we split
# on the FIRST colon only; group(2) is None when no format is given.
_VAR_REF_RE = re.compile(r"\$\(([A-Za-z_][A-Za-z0-9_.]*)(?::([^)]*))?\)")

# Module-level variable storage - cleared on script start.
_VARS: dict[str, str] = {}

# Frozen datetime moments, keyed by concept.  LAUNCH is frozen when this
# module loads (app start); SESSION is frozen by ``set_start_time_vars`` at
# the top-level script boundary.  Each exposes $(<CONCEPT>_DATE/TIME/DATETIME)
# and honors an optional :fmt override -- format is applied on read.
_FROZEN_MOMENTS: dict[str, datetime] = {"LAUNCH": datetime.now()}

# Non-datetime launch vars (plain strings, no :fmt).
_LAUNCH_VARS: dict[str, str] = {
    "FRONT_END": "unknown",
}

# Context variables - resolved via callable at expansion time.
_CONTEXT_VARS: dict[str, Callable[[], str]] = {}

# Default strftime formats for the datetime vars.  A base name (DATE/TIME/
# DATETIME) resolves to the current clock; a CONCEPT_ prefix (LAUNCH_/SESSION_)
# resolves to that frozen moment.  All accept an optional :fmt override.
_DT_DEFAULTS: dict[str, str] = {
    "DATE": "%Y-%m-%d",
    "TIME": "%H:%M:%S",
    "DATETIME": "%Y-%m-%d %H:%M:%S",
}

_ESCAPE_SENTINEL = "\x00"

# Retired ambient wall-clock placeholders -> their $() equivalents.  These
# moved out of the {} scripting-template system when $() gained :fmt; the
# rewrite happens here, at the head of the $() transform, so the emitted
# $(TIME)/$(DATETIME:...) expands in the same pass and works transparently on
# both REPL and device lines.  {seqN}/{starttime}/{elapsed} are NOT here --
# they remain per-run stamps in the scripting layer.  Back-compat shim;
# retire in a future major once the v24 migration has aged out.
_RETIRED_PLACEHOLDERS = {
    "{clock}": "$(TIME)",
    "{datetime}": "$(DATETIME:%Y%m%d_%H%M%S)",
}


# ── User variables ────────────────────────────────────────────────────────────


def set_user_var(name: str, value: str) -> None:
    """Set a user variable to a literal value.

    Args:
        name: Variable name (no ``$()`` delimiters).
        value: Value to store.
    """
    _VARS[name] = value


def unset_user_var(name: str) -> None:
    """Remove one user variable, ignoring a name that is not set.

    Args:
        name: Variable name to drop.
    """
    _VARS.pop(name, None)


def user_vars() -> dict[str, str]:
    """Return the live user-variable store.

    The dict is the real one, not a copy -- callers that only need to read
    should treat it as read-only and go through :func:`set_user_var` /
    :func:`unset_user_var` to mutate.

    Returns:
        Name -> value for every user variable currently set.
    """
    return _VARS


def clear_vars() -> int:
    """Clear all user variables.

    Returns:
        How many variables were cleared.
    """
    count = len(_VARS)
    _VARS.clear()
    return count


# ── Launch variables ──────────────────────────────────────────────────────────


def set_launch_var(name: str, value: str) -> None:
    """Set a launch-time variable (called by each frontend at startup)."""
    _LAUNCH_VARS[name] = value


def launch_var(name: str) -> str | None:
    """Return one launch variable, or None when it is not set.

    Args:
        name: Launch variable name (e.g. ``FRONT_END``).

    Returns:
        The stored string, or None.
    """
    return _LAUNCH_VARS.get(name)


def launch_vars() -> dict[str, str]:
    """Return the live launch-variable store (read-only by convention)."""
    return _LAUNCH_VARS


# ── Datetime variables ────────────────────────────────────────────────────────


def set_start_time_vars() -> None:
    """Freeze the SESSION_* datetime moment.

    Called once when a top-level script starts (Scripts button / Run menu).
    Frozen here and NOT refreshed by interactive ``/run`` calls, so
    $(SESSION_DATE/TIME/DATETIME) reflect the original session start.  The
    moment is stored raw; format is applied on read (default or ``:fmt``).
    """
    _FROZEN_MOMENTS["SESSION"] = datetime.now()


def resolve_datetime_var(name: str, fmt: str | None) -> str | None:
    """Resolve a datetime var to a formatted string, or None if not one.

    Handles the dynamic clock (``DATE``/``TIME``/``DATETIME`` -> now) and the
    frozen moments (``LAUNCH_*`` / ``SESSION_*``).  ``fmt`` overrides the
    per-base default when given.

    Args:
        name: The variable name (e.g. ``DATETIME``, ``SESSION_DATE``).
        fmt: Optional strftime override; None uses the base default.

    Returns:
        The formatted timestamp, or None if ``name`` is not a datetime var.
    """
    base = _DT_DEFAULTS.get(name)
    if base is not None:
        return datetime.now().strftime(fmt or base)
    concept, _, tail = name.partition("_")
    default = _DT_DEFAULTS.get(tail)
    moment = _FROZEN_MOMENTS.get(concept)
    if default is not None and moment is not None:
        return moment.strftime(fmt or default)
    return None


def datetime_var_names() -> list[tuple[str, str]]:
    """(name, tag) for every datetime var: dynamic clock + frozen moments.

    Used by the ``/var`` listing.  Frozen concepts appear only once frozen
    (SESSION shows up after a top-level script starts).
    """
    names = [(base, "dynamic") for base in _DT_DEFAULTS]
    for concept in sorted(_FROZEN_MOMENTS):
        names.extend((f"{concept}_{base}", concept.lower()) for base in _DT_DEFAULTS)
    return names


# ── Context variables ─────────────────────────────────────────────────────────


def set_context_var(name: str, fn: Callable[[], str]) -> None:
    """Register a context variable resolved by callable at expansion time."""
    _CONTEXT_VARS[name] = fn


def context_vars() -> dict[str, Callable[[], str]]:
    """Return the live context-variable store (read-only by convention)."""
    return _CONTEXT_VARS


def register_cfg_vars(
    get_config_path: Callable[[], str],
    get_cfg: Callable[[], dict],
    get_log_path: Callable[[], str],
) -> None:
    """Register all CFG.* context variables.

    Called by each frontend after config is loaded.

    Args:
        get_config_path: Returns the current config file path.
        get_cfg: Returns the current config dict.
        get_log_path: Returns the current log file path.
    """
    from pathlib import Path

    from termapy.config import connection_string

    def _resolve_cfg() -> Path:
        return Path(get_config_path()).resolve() if get_config_path() else Path(".")

    set_context_var("CFG.DIR", lambda: str(_resolve_cfg().parent))
    set_context_var("CFG.FILE", lambda: str(_resolve_cfg()))
    set_context_var("CFG.RUN_DIR", lambda: str(_resolve_cfg().parent / "run"))
    set_context_var("CFG.PROTO_DIR", lambda: str(_resolve_cfg().parent / "proto"))
    set_context_var("CFG.PLUGIN_DIR", lambda: str(_resolve_cfg().parent / "plugin"))
    set_context_var("CFG.SS_DIR", lambda: str(_resolve_cfg().parent / "ss"))
    set_context_var("CFG.CAP_DIR", lambda: str(_resolve_cfg().parent / "cap"))
    set_context_var("CFG.PROF_DIR", lambda: str(_resolve_cfg().parent / "prof"))
    set_context_var("CFG.VIZ_DIR", lambda: str(_resolve_cfg().parent / "viz"))
    set_context_var("CFG.LOG_FILE", lambda: str(Path(get_log_path()).resolve()) if get_log_path() else "")
    # port / baud_rate live under cfg["serial"] post-v22.
    set_context_var("CFG.PORT", lambda: get_cfg().get("serial", {}).get("port", ""))
    set_context_var(
        "CFG.BAUD", lambda: str(get_cfg().get("serial", {}).get("baud_rate", ""))
    )
    set_context_var("CFG.PORT_CFG", lambda: connection_string(get_cfg(), "short"))
    set_context_var("CFG.PORT_FULL", lambda: connection_string(get_cfg(), "medium"))


# ── Resolution and expansion ──────────────────────────────────────────────────


def resolve_one(name: str, fmt: str | None = None) -> str | None:
    """Resolve a single variable name to its value, or return None if unknown.

    Resolution order (no :fmt): user vars -> launch strings -> datetime vars
    (dynamic clock + frozen LAUNCH_/SESSION_ moments) -> context vars -> None.
    A ``:fmt`` suffix (e.g. ``DATETIME:%Y%m%d_%H%M%S``) applies only to
    datetime vars; on any other name it returns None.

    Shared by both the blanket $(NAME) transform and per-token $(*NAME)
    dereference handlers that explicitly opt in.

    Args:
        name: Variable name (no $() delimiters, no :fmt suffix).
        fmt: Optional strftime format suffix (for datetime vars only).

    Returns:
        Resolved value as a string, or None if unknown.
    """
    if fmt is not None:
        # A :fmt suffix is only meaningful on datetime vars.
        return resolve_datetime_var(name, fmt)
    val = _VARS.get(name)
    if val is not None:
        return val
    val = _LAUNCH_VARS.get(name)
    if val is not None:
        return val
    dt = resolve_datetime_var(name, None)
    if dt is not None:
        return dt
    ctx_fn = _CONTEXT_VARS.get(name)
    if ctx_fn is not None:
        return ctx_fn()
    return None


def deref_ref(ref: str) -> str | None:
    """Resolve the inner text of one ``$(*NAME)`` / ``$(*NAME:fmt)`` reference.

    The arity-1 counterpart to ``expand_vars``' 0..N splice: same namespace and
    the same ``:fmt`` rule, but resolved per argument token by the param binder
    (``plugins/params.resolve_deref``) instead of spliced into the line.  The
    value it returns is data -- the binder never re-splits or re-scans it.

    Args:
        ref: The text between ``$(*`` and ``)`` -- ``NAME`` or ``NAME:fmt``.

    Returns:
        The resolved value, or None when the name is undefined.
    """
    name, sep, fmt = ref.partition(":")
    return resolve_one(name, fmt if sep else None)


def expand_vars(text: str) -> str:
    """Expand $(NAME) references in a string.

    Resolution order (no :fmt): user vars -> launch strings -> datetime vars
    (dynamic clock + frozen LAUNCH_/SESSION_ moments) -> context vars -> left
    literal.  A ``:fmt`` suffix (e.g. ``$(DATETIME:%Y%m%d_%H%M%S)``) applies
    only to datetime vars; on any other name it is left literal.

    Use ``\\$`` to escape a literal ``$`` (e.g. ``\\$(PORT)`` -> ``$(PORT)``).

    Args:
        text: String potentially containing $(NAME) references.

    Returns:
        String with known variables expanded.
    """
    # Back-compat: rewrite retired {clock}/{datetime} to their $() form first,
    # so the emitted references expand below in this same pass.
    if "{clock}" in text or "{datetime}" in text:
        for old, new in _RETIRED_PLACEHOLDERS.items():
            text = text.replace(old, new)
    # Swap \$ -> sentinel so the regex doesn't see it as a var reference
    text = text.replace("\\$", _ESCAPE_SENTINEL)

    def _replace(m: re.Match) -> str:
        name = m.group(1)
        fmt = m.group(2)
        val = resolve_one(name, fmt)
        return val if val is not None else m.group(0)

    text = _VAR_REF_RE.sub(_replace, text)
    # Restore sentinel -> literal $
    return text.replace(_ESCAPE_SENTINEL, "$")
