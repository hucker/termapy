"""Built-in plugin: inspect app-wide state and config.

Termapy has two scopes of configuration: per-project (lives in
``termapy_cfg/<name>/`` and is accessed via ``/cfg``) and **app-wide**
(lives in the OS's standard user data location and is accessed via
``/app``).

Command surface:

- ``/app.list``         list files in the app folder(s)
- ``/app.explore``      open the app folder in the OS file manager
- ``/app.state``        print ``state.json``'s path
- ``/app.state.dump``   print ``state.json`` contents to the terminal
- ``/app.state.edit``   open ``state.json`` in the system editor
- ``/app.config``       print ``config.json``'s path
- ``/app.config.dump``  print ``config.json`` contents to the terminal
- ``/app.config.edit``  open ``config.json`` in the system editor

There is no bare ``/app`` -- every action is a subcommand.  Both
``state.json`` and ``config.json`` expose ``.edit`` for symmetry,
but ``state.json`` is app-written (PyPI update timestamps, caches);
hand-editing it will usually be silently overwritten or confuse
the update checker.  Stick to ``config.json`` for anything you
want to persist.

Linux splits state and config into separate XDG directories; on
Windows and macOS they share one folder.  ``/app.explore`` and
``/app.list`` dedupe when the two resolve to the same path.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from termapy.app_dirs import (
    app_config_dir,
    app_config_file,
    app_state_dir,
    app_state_file,
    load_app_config,
    load_app_state,
)
from termapy.plugins import CapabilitySet, CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ── /app.explore ──────────────────────────────────────────────────────────────


def _handler_explore(ctx: PluginContext, args: str) -> CmdResult:
    """Open the app folder in the OS file manager.

    Dedupes when state and config resolve to the same path (Windows
    and macOS put both under one folder; Linux splits them via
    XDG_STATE_HOME vs XDG_CONFIG_HOME).
    """
    paths = {app_state_dir(), app_config_dir()}
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
        ctx.fs.open_file(path)
    # Return newline-joined paths so scripts can capture which folders
    # were opened (one on Windows/macOS, two on Linux).
    return CmdResult.ok(value="\n".join(sorted(str(p) for p in paths)))


# ── /app.list ─────────────────────────────────────────────────────────────────


def _handler_list(ctx: PluginContext, args: str) -> CmdResult:
    """List files in the app folder(s).

    On Windows and macOS, state and config share one folder; on
    Linux they split into XDG_STATE_HOME and XDG_CONFIG_HOME.  Walk
    each distinct folder and list every file in it, so the user gets
    a complete picture of what termapy has written to their user-level
    data directories.
    """
    paths = sorted({app_state_dir(), app_config_dir()})
    any_shown = False
    file_names: list[str] = []
    for path in paths:
        if not path.is_dir():
            continue
        files = sorted(f for f in path.iterdir() if f.is_file())
        if not files:
            ctx.io.output(f"  {path}/ (empty)")
            any_shown = True
            continue
        ctx.io.output(f"  {path}/")
        for f in files:
            ctx.io.output(f"    {f.name}")
            file_names.append(str(f))
        any_shown = True
    if not any_shown:
        ctx.io.output("  (no app folder yet)")
    # Newline-joined list of every file (full path) so scripts can
    # capture or iterate.  Empty when no folders existed.
    return CmdResult.ok(value="\n".join(file_names))


# ── /app.state ────────────────────────────────────────────────────────────────


def _handler_state(ctx: PluginContext, args: str) -> CmdResult:
    """Print the path to ``state.json``."""
    path = str(app_state_file())
    ctx.io.output(path)
    return CmdResult.ok(value=path)


def _handler_state_dump(ctx: PluginContext, args: str) -> CmdResult:
    """Print ``state.json`` contents as pretty JSON.

    Missing or empty file renders as ``{}``.
    """
    state = load_app_state()
    payload = json.dumps(state, indent=4)
    ctx.io.output(payload)
    return CmdResult.ok(value=payload)


def _handler_state_edit(ctx: PluginContext, args: str) -> CmdResult:
    """Open ``state.json`` in the system editor.

    Termapy writes state.json itself (PyPI update timestamps,
    caches) so hand-editing is usually a bad idea -- whatever you
    change may get silently overwritten on the next update cycle.
    Exposed for symmetry with ``/app.config.edit`` and for debugging
    purposes only.  Creates the file with an empty object if it
    doesn't exist, so the editor always opens something editable.
    """
    path = app_state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("{}\n", encoding="utf-8")
    ctx.fs.open_file(path)
    return CmdResult.ok(value=path)


# ── /app.config ───────────────────────────────────────────────────────────────


def _handler_config(ctx: PluginContext, args: str) -> CmdResult:
    """Print the path to ``config.json``."""
    path = str(app_config_file())
    ctx.io.output(path)
    return CmdResult.ok(value=path)


def _handler_config_dump(ctx: PluginContext, args: str) -> CmdResult:
    """Print ``config.json`` contents as pretty JSON.

    Missing or empty file renders as ``{}``.  No feature reads this
    file yet; it's reserved for future global preferences.
    """
    cfg = load_app_config()
    payload = json.dumps(cfg, indent=4)
    ctx.io.output(payload)
    return CmdResult.ok(value=payload)


def _handler_config_edit(ctx: PluginContext, args: str) -> CmdResult:
    """Open ``config.json`` in the system editor.

    Creates the file with an empty object if it doesn't exist, so
    the editor always opens something editable.
    """
    path = app_config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("{}\n", encoding="utf-8")
    ctx.fs.open_file(path)
    return CmdResult.ok(value=path)


_APP_LONG_HELP = """\
Inspect app-wide state and config.

  {prefix}app.list             list files in the app folder(s)
  {prefix}app.explore          open the app folder in the file manager
  {prefix}app.state            print state.json path
  {prefix}app.state.dump       print state.json contents
  {prefix}app.state.edit       open state.json in the system editor
  {prefix}app.config           print config.json path
  {prefix}app.config.dump      print config.json contents
  {prefix}app.config.edit      open config.json in the system editor

state.json is app-written (PyPI update-check timestamps, caches);
.edit is exposed for symmetry but hand-edits usually get silently
overwritten.  config.json is reserved for user-editable global
preferences; no feature reads it yet."""


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="app",
    help="App-wide state and config.",
    long_help=_APP_LONG_HELP,
    handler=None,
    sub_commands={
        "list": Command(
            help="List files in the app folder(s).",
            handler=_handler_list,
        ),
        "explore": Command(
            help="Open the app folder in the file manager.",
            handler=_handler_explore,
            needs=CapabilitySet(gui_apps=True),
        ),
        "state": Command(
            help="Print state.json path.",
            long_help=(
                "Bare: print the path.\n"
                "  {prefix}app.state.dump      print state.json contents\n"
                "  {prefix}app.state.edit      open state.json in the system editor"
            ),
            handler=_handler_state,
            sub_commands={
                "dump": Command(
                    help="Print state.json contents to the terminal.",
                    handler=_handler_state_dump,
                ),
                "edit": Command(
                    help="Open state.json in the system editor.",
                    handler=_handler_state_edit,
                    needs=CapabilitySet(gui_apps=True),
                ),
            },
        ),
        "config": Command(
            help="Print config.json path.",
            long_help=(
                "Bare: print the path.\n"
                "  {prefix}app.config.dump     print config.json contents\n"
                "  {prefix}app.config.edit     open config.json in the system editor"
            ),
            handler=_handler_config,
            sub_commands={
                "dump": Command(
                    help="Print config.json contents to the terminal.",
                    handler=_handler_config_dump,
                ),
                "edit": Command(
                    help="Open config.json in the system editor.",
                    handler=_handler_config_edit,
                    needs=CapabilitySet(gui_apps=True),
                ),
            },
        ),
    },
)
