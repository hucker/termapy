"""Built-in plugin: inspect app-wide state and config.

Termapy has two scopes of configuration: per-project (lives in
``termapy_cfg/<name>/`` and is accessed via ``/cfg``) and **app-wide**
(lives in the OS's standard user data location and is accessed via
``/app``).

Command surface:

- ``/app.explore``      open the app folder in the OS file manager
- ``/app.state``        print ``state.json``'s path
- ``/app.state.dump``   print ``state.json`` contents to the terminal
- ``/app.config``       print ``config.json``'s path
- ``/app.config.dump``  print ``config.json`` contents to the terminal
- ``/app.config.edit``  open ``config.json`` in the system editor

There is no bare ``/app`` -- every action is a subcommand.  Only
``config.json`` exposes ``.edit`` because it's designed for user
editing; ``state.json`` is app-written and editing it by hand
would usually cause more problems than it solves.

Linux splits state and config into separate XDG directories; on
Windows and macOS they share one folder.  ``/app.explore`` opens
both when they differ, deduped when they don't.
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
from termapy.plugins import CmdResult, Command

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
        ctx.open_file(path)
    return CmdResult.ok()


# ── /app.state ────────────────────────────────────────────────────────────────


def _handler_state(ctx: PluginContext, args: str) -> CmdResult:
    """Print the path to ``state.json``."""
    path = str(app_state_file())
    ctx.write(path)
    return CmdResult.ok(value=path)


def _handler_state_dump(ctx: PluginContext, args: str) -> CmdResult:
    """Print ``state.json`` contents as pretty JSON.

    Missing or empty file renders as ``{}``.
    """
    state = load_app_state()
    payload = json.dumps(state, indent=4)
    ctx.write(payload)
    return CmdResult.ok(value=payload)


# ── /app.config ───────────────────────────────────────────────────────────────


def _handler_config(ctx: PluginContext, args: str) -> CmdResult:
    """Print the path to ``config.json``."""
    path = str(app_config_file())
    ctx.write(path)
    return CmdResult.ok(value=path)


def _handler_config_dump(ctx: PluginContext, args: str) -> CmdResult:
    """Print ``config.json`` contents as pretty JSON.

    Missing or empty file renders as ``{}``.  No feature reads this
    file yet; it's reserved for future global preferences.
    """
    cfg = load_app_config()
    payload = json.dumps(cfg, indent=4)
    ctx.write(payload)
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
    ctx.open_file(path)
    return CmdResult.ok()


_APP_LONG_HELP = """\
Inspect app-wide state and config.

  {prefix}app.explore          open the app folder in the file manager
  {prefix}app.state            print state.json path
  {prefix}app.state.dump       print state.json contents
  {prefix}app.config           print config.json path
  {prefix}app.config.dump      print config.json contents
  {prefix}app.config.edit      open config.json in the system editor

state.json is app-written (PyPI update-check timestamps, caches).
config.json is reserved for user-editable global preferences; no
feature reads it yet."""


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="app",
    help="App-wide state and config.",
    long_help=_APP_LONG_HELP,
    handler=None,
    sub_commands={
        "explore": Command(
            help="Open the app folder in the file manager.",
            handler=_handler_explore,
        ),
        "state": Command(
            help="Print state.json path.",
            long_help=(
                "Bare: print the path.\n"
                "  {prefix}app.state.dump      print state.json contents"
            ),
            handler=_handler_state,
            sub_commands={
                "dump": Command(
                    help="Print state.json contents to the terminal.",
                    handler=_handler_state_dump,
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
                ),
            },
        ),
    },
)
