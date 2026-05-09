"""Helpers for building dynamic ``long_help`` callables.

A plugin's ``Command.long_help`` may be a ``(PluginContext) -> str`` callable
that is invoked every time ``/help <cmd>`` renders its DESCRIPTION section.
The functions in this module are small, pure utilities for the common
shapes those callables take:

    - one green state line ("Current = 8")
    - file counts in per-config folders ("42 files in ss/")
    - port status ("COM3 @ 115200" or "Not connected")
    - active cfg name + count across termapy_cfg/

Everything here is:
    - synchronous and fast (no I/O beyond a directory listing)
    - defensive (returns sensible fallbacks for missing state)
    - Textual-independent (strings only, uses Rich markup for color)

Plugins compose these however they want -- the typical pattern is a
``_long_help(ctx)`` that joins one state line with a block of static
prose via ``compose``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from termapy.folders import FOLDER_PATTERNS

if TYPE_CHECKING:
    from termapy.plugins import PluginContext  # noqa: F401 (referenced in docstrings)

# Helpers in this module are intentionally duck-typed: they work with
# real PluginContext instances, test fakes, or anything that exposes
# the same attribute names.  To stay permissive we reach for attributes
# via ``getattr(ctx, name, default)`` rather than ``try/except Exception``
# -- the getattr form is just as forgiving about missing attrs, and
# lets any real bug inside a callable propagate with a useful traceback
# instead of silently degrading.
_Ctx = Any


# Rich markup color used for every dynamic state line. Kept here so every
# caller agrees and a future palette change is a one-line edit.
STATE_COLOR = "green"


def green(text: str) -> str:
    """Wrap ``text`` in Rich green markup."""
    return f"[{STATE_COLOR}]{text}[/]"


def state_line(label: str, value: Any) -> str:
    """Format a single green status line: ``Current <label> = <value>``.

    This is the canonical shape for every "single value" dynamic help --
    baud rate, bytesize, active profile, etc. Using it consistently keeps
    the visual signal uniform across commands.
    """
    return green(f"Current {label} = {value}")


def compose(*parts: str | None) -> str:
    """Join non-empty parts with a blank line between each.

    Empty / ``None`` parts are dropped so callers can unconditionally
    include a state line that may not apply (e.g. "port closed" cases
    return an empty string instead of branching at the call site).
    """
    return "\n\n".join(p for p in parts if p)


# ─────────────────────────────────────────────────────────────────────────────
# Filesystem helpers -- per-config folder inspection.
# ─────────────────────────────────────────────────────────────────────────────

def folder_dir(ctx: _Ctx, kind: str) -> Path | None:
    """Return the Path for a per-config folder by ``folders.py`` name.

    Uses ``ctx`` accessors so the resolved dir matches whatever the
    engine is actually using. Returns ``None`` when the folder isn't
    exposed on ``ctx`` (older contexts, tests, etc.).

    ``kind`` is the name in ``folders.FOLDER_NAMES``:
        ``run`` → ``ctx.fs.scripts_dir``
        ``proto`` → ``ctx.fs.proto_dir``
        ``ss`` → ``ctx.fs.ss_dir``
        ``cap`` → ``ctx.fs.cap_dir``
        ``prof`` → ``ctx.fs.prof_dir``
        ``viz`` / ``plugin`` → derived from the active cfg data dir
    """
    direct = {
        "run": "scripts_dir",
        "proto": "proto_dir",
        "ss": "ss_dir",
        "cap": "cap_dir",
        "prof": "prof_dir",
    }
    attr = direct.get(kind)
    if attr:
        d = getattr(ctx, attr, None)
        return Path(d) if d else None
    # viz/ and plugin/ aren't exposed directly; derive from config_path.
    cfg_path = getattr(ctx, "config_path", "") or ""
    if not cfg_path:
        return None
    return Path(cfg_path).parent / kind


def file_count(ctx: _Ctx, kind: str, pattern: str | None = None) -> int:
    """Count files matching ``pattern`` in the per-config folder ``kind``.

    ``pattern`` defaults to the folder's canonical glob from ``folders.py``
    (``*.run`` for run, ``*.pro`` for proto, etc.). Returns 0 when the
    directory doesn't exist -- callers shouldn't have to distinguish
    "empty" from "missing" for display.
    """
    d = folder_dir(ctx, kind)
    if not d or not d.is_dir():
        return 0
    glob = pattern or FOLDER_PATTERNS.get(kind, "*")
    try:
        return sum(1 for p in d.glob(glob) if p.is_file())
    except OSError:
        return 0


def folder_line(ctx: _Ctx, kind: str, noun: str | None = None) -> str:
    """Green one-liner: ``N <noun> in <kind>/``.

    ``noun`` defaults to ``"file"`` / ``"files"``. The pluralization is
    naive (append 's') -- fine for the nouns this is used with.
    """
    n = file_count(ctx, kind)
    label = noun or "file"
    if n != 1:
        label += "s"
    return green(f"{n} {label} in {kind}/")


# ─────────────────────────────────────────────────────────────────────────────
# Port helpers -- read live pyserial state safely.
# ─────────────────────────────────────────────────────────────────────────────

def port_setting(ctx: _Ctx, attr: str) -> Any:
    """Return a live attribute from the pyserial port, or ``None`` if closed.

    ``attr`` is the pyserial attribute name (e.g. ``"baudrate"``,
    ``"bytesize"``, ``"parity"``, ``"stopbits"``).  Returns ``None``
    when ``ctx`` doesn't expose ``port`` or the port isn't open.
    """
    port_fn = getattr(ctx, "port", None)
    if port_fn is None:
        return None
    p = port_fn()
    if p is None:
        return None
    return getattr(p, attr, None)


def port_status(ctx: _Ctx) -> str:
    """Green status line summarizing the current port.

    "Connected: COM3 @ 115200 8N1" when open, "Not connected" otherwise.
    Intended as the top-of-DESCRIPTION line for ``/port`` and any
    subcommand whose value only has meaning while connected.
    """
    is_connected = getattr(ctx, "is_connected", None)
    if is_connected is None or not is_connected():
        return green("Not connected")
    port_fn = getattr(ctx, "port", None)
    p = port_fn() if port_fn else None
    if p is None:
        return green("Not connected")
    name = getattr(p, "port", "?") or "?"
    baud = getattr(p, "baudrate", "?")
    byte = getattr(p, "bytesize", "?")
    par = getattr(p, "parity", "?")
    stop = getattr(p, "stopbits", "?")
    return green(f"Connected: {name} @ {baud} {byte}{par}{stop}")


# ─────────────────────────────────────────────────────────────────────────────
# Config helpers -- active cfg + total count under termapy_cfg/.
# ─────────────────────────────────────────────────────────────────────────────

def cfg_name(ctx: _Ctx) -> str:
    """Return the active config's short name (the directory stem).

    Falls back to ``""`` if ``config_path`` isn't set. The name is the
    parent directory's stem, matching the ``termapy_cfg/<name>/<name>.cfg``
    layout.
    """
    path = getattr(ctx, "config_path", "") or ""
    if not path:
        return ""
    return Path(path).parent.name


def cfg_count(ctx: _Ctx) -> int:
    """Count discoverable configs under ``termapy_cfg/``.

    A "config" is a subdirectory of the cfg root that contains a
    ``<name>.cfg`` file at the expected path. Returns 0 when the root
    can't be determined (no active config).
    """
    path = getattr(ctx, "config_path", "") or ""
    if not path:
        return 0
    root = Path(path).parent.parent
    if not root.is_dir():
        return 0
    try:
        return sum(
            1 for sub in root.iterdir()
            if sub.is_dir() and (sub / f"{sub.name}.cfg").is_file()
        )
    except OSError:
        return 0


def cfg_status(ctx: _Ctx) -> str:
    """Green status line: ``Active cfg = <name> (N config(s) available)``.

    Empty string when there's no active config -- ``compose`` will drop
    it so help still renders cleanly.
    """
    name = cfg_name(ctx)
    if not name:
        return ""
    total = cfg_count(ctx)
    word = "config" if total == 1 else "configs"
    return green(f"Active cfg = {name} ({total} {word} available)")


# ─────────────────────────────────────────────────────────────────────────────
# Namespace helpers -- counts from ``ctx.ns(...)`` dicts.
# ─────────────────────────────────────────────────────────────────────────────

def ns_count(ctx: _Ctx, name: str) -> int:
    """Return ``len(ctx.ns(name))``, or 0 when ``ctx`` has no ``ns``."""
    ns_fn = getattr(ctx, "ns", None)
    return len(ns_fn(name)) if ns_fn else 0
