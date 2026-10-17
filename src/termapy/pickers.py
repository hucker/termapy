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

from pathlib import Path
from typing import TYPE_CHECKING

from termapy.config import (
    ConfigExistsError,
    cfg_path_for_name,
    create_config,
    expand_env_cfg,
    new_config,
    validate_config,
    validate_file_stem,
)
from termapy.defaults import cmd_prefix
from termapy.dialogs import (
    ConfigEditor,
    ConfirmDialog,
    FilenameDialog,
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
    expand_warnings: list[str] = []
    try:
        expand_env_cfg(new_cfg, expand_warnings)
    except ValueError as e:
        # A templated boolean that reads as garbage: the saved file
        # stands, but the session is not switched onto a cfg that a load
        # would refuse.
        app.notify(f"Config not applied: {e}", severity="error", timeout=15)
        return
    config_warnings = expand_warnings + validate_config(new_cfg)
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
    cfg = new_config(name, port=port or "", baud=baud, custom_baud=custom_baud)
    if action == "advanced":
        # Open the full config editor with pre-filled values.
        # Advanced users skip the checkbox -- they can /cfg.icon
        # manually after editing.  The editor's Save creates the folder;
        # Cancel leaves nothing behind.
        app.push_screen(
            ConfigEditor(cfg, config_path, is_new=True),
            callback=app._on_config_result,
        )
        return

    def _write_and_activate(overwrite: bool) -> None:
        # The same writer as termapy --new: folder, .gitignore, the .cfg.
        try:
            create_config(config_path, cfg, overwrite=overwrite)
        except ConfigExistsError as e:
            # Ask before replacing an existing config's .cfg; its other
            # files stay.
            app.push_screen(
                ConfirmDialog(e.question),
                callback=lambda confirmed: (
                    _write_and_activate(overwrite=True) if confirmed else None
                ),
            )
            return
        except (ValueError, RuntimeError, OSError) as e:
            app._status(f"New config failed: {e}", "red")
            return
        app._on_config_result((cfg, config_path))
        if add_icon:
            # Cfg is loaded; ctx.config_path now points at the new file
            # so /cfg.icon picks it up automatically.
            app.ctx.dispatch("/cfg.icon")
        if port:
            app._connect()

    _write_and_activate(overwrite=False)


def prompt_rename(app, path_str: str, command: str) -> None:
    """Ask for a new name, then dispatch ``/<command>.rename <old> <new>``.

    The rename itself is the command's job (``/run.rename``,
    ``/proto.rename`` -- the shared ``folder_ops`` handler, so CLI and
    MCP callers get the same rules); the dialog only supplies the name.
    The name is checked here too so a bad one is refused before it
    reaches the dispatcher, which splits arguments on whitespace.

    Args:
        app: The SerialTerminal instance.
        path_str: The file to rename.
        command: The owning top-level command (``"run"`` / ``"proto"``).
    """
    old = Path(path_str)

    def _on_name(new_name: str | None) -> None:
        if not new_name or new_name == old.stem:
            return
        reason = validate_file_stem(new_name)
        if reason:
            app._status(reason, "red")
            return
        prefix = cmd_prefix(app.cfg)
        app._dispatch_on_thread(f"{prefix}{command}.rename {old.name} {new_name}")

    app.push_screen(
        FilenameDialog(title=f"Rename {old.name} to:", value=old.stem),
        callback=_on_name,
    )


def on_script_picked(app, result: tuple | None) -> None:
    """Apply the script-picker result.

    Dispatches to one of: run the script (clears vars, calls
    ``app._run_script``), new (opens ScriptEditor), edit (opens
    ScriptEditor on the selected file), rename (name prompt, then
    ``/run.rename``), or delete (confirmation dialog).

    Args:
        app: The SerialTerminal instance.
        result: ``(action, ...path)`` where ``action`` is one of
            ``"run"``, ``"new"``, ``"edit"``, ``"rename"``, ``"delete"``.
            ``None`` if the user canceled.
    """
    if result is None:
        return
    action = result[0]
    if action == "rename":
        prompt_rename(app, result[1], "run")
    elif action == "run":
        from termapy.variables import clear_vars, set_start_time_vars

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
    if action == "rename":
        prompt_rename(app, result[1], "proto")
    elif action == "run":
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

    Wired into ``InternalHandle.open_picker`` from ``_register_tui_hooks``;
    the dispatcher calls it for a bare invocation of any command that
    declares ``picker=`` (``/cfg``, ``/run``, ``/proto``, ``/port``, the
    edit/run twins, and ``/cfg.new`` for the New Config wizard), so it
    behaves like clicking the matching button.  CLI never installs this callback; those handlers
    then run their own bare fallback.  ``name`` is one of
    ``plugins.command.PICKERS``.
    """
    dispatch = {
        "cfg": app._btn_cfg,
        "run": app._btn_scripts,
        "proto": app._btn_proto,
        "port": app._show_port_picker,
        "setup": app._new_config,
    }
    fn = dispatch.get(name)
    if fn is None:
        return CmdResult.fail(msg=f"Unknown picker: {name}")
    app._on_main(fn)
    # Return which picker was opened so scripts can log.
    return CmdResult.ok(value=name)
