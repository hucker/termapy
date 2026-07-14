"""Modal-picker callbacks for the TUI.

When a Textual modal (config picker, port picker, script picker,
proto picker, quick-setup wizard) dismisses, the result lands in one
of these functions.  Plus ``open_picker`` -- the dispatcher that
opens the matching picker by name (used by ``/cfg``, ``/run``,
``/proto`` with no args, and by external engine callers).

Previously lived as six ``_on_*_picked`` / ``_open_picker`` methods
on ``SerialTerminal``.  Extracted here so ``ls src/termapy/`` shows
"pickers" as a named subsystem.

Each function takes the app as first argument.  ``SerialTerminal``
keeps thin stubs (``self._on_script_picked``, ``self._open_picker``,
...) so existing call sites -- including ``callback=self._on_X``
bound-method references and string-based ``"btn-X": "_show_X"`` dict
lookups -- keep working unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.config import (
    cfg_data_dir,
    cfg_path_for_name,
    expand_env_cfg,
    validate_config,
)
from termapy.defaults import cmd_prefix, default_cfg
from termapy.dialogs import (
    ConfigEditor,
    ProtoEditor,
    ScriptEditor,
)
from termapy.plugins import CmdResult

if TYPE_CHECKING:
    from termapy.app import SerialTerminal  # noqa: F401 -- type-hint surface


def on_config_result(app, result: tuple | None) -> None:
    """Apply the result of a config-editor dismissal.

    Validates the new cfg, swaps it into the live session via
    ``app._switch_config``, surfaces any warnings as a toast, then
    re-renders the config-info dialog.

    Args:
        app: The SerialTerminal instance.
        result: ``(new_cfg_dict, new_config_path)`` on save, or
            ``None`` if the user canceled.
    """
    if result is None:
        return
    new_cfg, new_path = result
    expand_env_cfg(new_cfg)
    config_warnings = validate_config(new_cfg)
    if config_warnings:
        new_cfg["_config_warnings"] = config_warnings
    app._switch_config(new_cfg, new_path)
    if config_warnings:
        detail = "\n".join(config_warnings)
        app.notify(detail, severity="warning", timeout=15)
    app._show_config_info(new_path)


def on_port_picked(app, port: str | None) -> None:
    """Update the title-bar port display when the port picker dismisses.

    Args:
        app: The SerialTerminal instance.
        port: The selected port name, or ``None`` if the user canceled.
    """
    if port is None:
        return
    app._update_port(port)


def on_quick_setup(app, result: tuple | None) -> None:
    """Apply the QuickSetup wizard result.

    On ``"advanced"`` action, opens the full config editor with the
    wizard's pre-filled values.  Otherwise writes the config to disk,
    switches to it, and optionally auto-connects if a port was set.
    If ``add_icon`` is true, dispatches ``/cfg.icon`` after the cfg
    is loaded.

    Args:
        app: The SerialTerminal instance.
        result: ``(action, name, port, baud, custom_baud, add_icon)``
            tuple where ``action`` is ``"connect"`` or ``"advanced"``.
            ``None`` if the user canceled.
    """
    if result is None:
        return
    action, name, port, baud, custom_baud, add_icon = result
    config_path = str(cfg_path_for_name(name))
    cfg = default_cfg()
    cfg["title"] = name
    if port:
        cfg["serial"]["port"] = port
    cfg["serial"]["baud_rate"] = baud
    cfg["serial"]["custom_baud"] = custom_baud
    if action == "advanced":
        # Open the full config editor with pre-filled values.
        # Advanced users skip the checkbox -- they can /cfg.icon
        # manually after editing.
        cfg_data_dir(config_path)
        app.push_screen(
            ConfigEditor(cfg, config_path),
            callback=app._on_config_result,
        )
        return
    # Create config dir structure (.gitignore, subdirs) and write config
    cfg_data_dir(config_path)
    with open(config_path, "w") as f:
        json.dump(cfg, f, indent=4)
    app._on_config_result((cfg, config_path))
    if add_icon:
        # Cfg is loaded; ctx.config_path now points at the new file
        # so /cfg.icon picks it up automatically.
        app.ctx.dispatch("/cfg.icon")
    if port:
        app._connect()


def on_script_picked(app, result: tuple | None) -> None:
    """Apply the script-picker result.

    Dispatches to one of: run the script (clears vars, calls
    ``app._run_script``), new (opens ScriptEditor), edit (opens
    ScriptEditor on the selected file), or delete (confirmation dialog).

    Args:
        app: The SerialTerminal instance.
        result: ``(action, ...path)`` where ``action`` is one of
            ``"run"``, ``"new"``, ``"edit"``, ``"delete"``.  ``None``
            if the user canceled.
    """
    if result is None:
        return
    action = result[0]
    if action == "run":
        from termapy.builtins.commands.var import clear_vars, set_start_time_vars

        clear_vars()
        set_start_time_vars()
        path, _ = app.repl.start_script(result[1])
        if path:
            app._run_script(path)
    elif action == "new":
        app.push_screen(
            ScriptEditor(app.repl.scripts_dir),
            callback=app._on_script_saved,
        )
    elif action == "edit":
        app.push_screen(
            ScriptEditor(app.repl.scripts_dir, result[1]),
            callback=app._on_script_saved,
        )
    elif action == "delete":
        app._confirm_delete(
            result[1],
            "script",
            on_deleted=app._sync_scripts_button,
        )


def on_proto_picked(app, result: tuple | None) -> None:
    """Handle result from the ProtoPicker dialog.

    Args:
        result: Tuple action from picker, or None if canceled.
    """
    if result is None:
        return
    action = result[0]
    if action == "run":
        filename = Path(result[1]).name
        prefix = cmd_prefix(app.cfg)
        app._dispatch_on_thread(f"{prefix}proto.run {filename}")
    elif action == "debug":
        filename = Path(result[1]).name
        prefix = cmd_prefix(app.cfg)
        app._dispatch_on_thread(f"{prefix}proto.debug {filename}")
    elif action == "new":
        app.push_screen(
            ProtoEditor(app.repl.proto_dir),
            callback=app._on_proto_saved,
        )
    elif action == "edit":
        app.push_screen(
            ProtoEditor(app.repl.proto_dir, result[1]),
            callback=app._on_proto_saved,
        )
    elif action == "delete":
        app._confirm_delete(
            result[1],
            "proto script",
            on_deleted=app._sync_proto_button,
        )


def open_picker(app, name: str) -> CmdResult:
    """Open the picker/dialog for a top-level command name.

    Wired into ``InternalHandle.open_picker`` from ``_register_tui_hooks``
    so that bare ``/cfg``, ``/run``, ``/proto`` invocations behave
    like clicking the matching title-bar button.  CLI never installs
    this callback; plugin handlers fall through to their CLI fallback.
    """
    dispatch = {
        "cfg": app._btn_cfg,
        "run": app._btn_scripts,
        "proto": app._btn_proto,
        "port": app._show_port_picker,
    }
    fn = dispatch.get(name)
    if fn is None:
        return CmdResult.fail(msg=f"Unknown picker: {name}")
    app._on_main(fn)
    # Return which picker was opened so scripts can log.
    return CmdResult.ok(value=name)


