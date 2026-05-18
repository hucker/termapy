"""Built-in plugin: ``/run`` -- execute .run scripts from any host.

Until this commit ``/run`` was registered as a host hook in three
places: ``app_hooks.register_tui_hooks``, ``CLITerminal._register_hooks``,
and ``MCPHost._register_hooks``.  Each one reached into adapter
internals (TUI's ``app._run_script`` Worker, CLI's ``self._hook_run``,
MCP's inline lambda) because the script-runner machinery lives on
the host.

Now the host exposes the runner via the engine handle
(``ctx.engine.start_script`` + ``ctx.engine.run_script``, wired in
``TerminalHost._build_context``), and this single built-in handler
covers all three frontends.  ``TerminalHost._run_script`` is the
default synchronous implementation; ``SerialTerminal`` (TUI)
overrides it with the ``@work(thread=True)`` Worker version that
posts ``ScriptStarted`` / ``ScriptProgress`` / ``ScriptFinished``
messages for the overlay -- same polymorphism the rest of the
plugin layer uses.

Bare ``/run`` opens the Run picker when a host installs one
(``ctx.engine.open_picker`` is set in TUI, unset elsewhere); CLI
and MCP fall through to ``/help run`` with the available-files
section appended.  Folder subcommands (.list/.dump/.show/.explore)
come from the shared ``build_folder_subcommands`` helper.
``/run.legacy`` rides along by re-exporting the existing
``termapy.run_legacy`` handler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy import run_legacy
from termapy.builtins.commands.help import (
    _show_command_help,
    append_files_section,
)
from termapy.folder_ops import build_folder_subcommands
from termapy.plugins import CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler_help(ctx: PluginContext, args: str) -> CmdResult:
    """``/run.help`` -- same as ``/help run`` with the script file list."""
    result = _show_command_help(ctx, "run")
    scripts_dir = ctx.fs.scripts_dir
    files = (
        sorted(f.name for f in scripts_dir.glob("*.run"))
        if scripts_dir.is_dir()
        else []
    )
    append_files_section(ctx, "AVAILABLE RUN FILES", files)
    return result


def _handler_root(ctx: PluginContext, args: str) -> CmdResult:
    """``/run`` -- bare opens picker (TUI) / shows help (CLI, MCP);
    with a filename, dispatches to the host's script runner.

    The host wires ``start_script`` and ``run_script`` onto
    ``ctx.engine`` in ``TerminalHost._build_context``; this handler
    is host-agnostic and works in every frontend.
    """
    arg = args.strip()
    if not arg:
        if ctx.engine.open_picker is not None:
            return ctx.engine.open_picker("run")
        # No picker in this host -- show help + the list of
        # available .run files (the closest CLI / MCP equivalent
        # to "open the picker").
        return _handler_help(ctx, args)

    if ctx.engine.start_script is None or ctx.engine.run_script is None:
        # Defensive: a host that doesn't wire the script runner can't
        # execute scripts.  Should not happen in practice -- TerminalHost
        # wires both -- but guard so a misconfigured embed doesn't crash.
        return CmdResult.fail(msg="This host does not support script execution.")

    verbose = ctx.output_level == "verbose"
    path, result = ctx.engine.start_script(arg)
    if path:
        ctx.engine.run_script(path, verbose=verbose)
    return result


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="run",
    args="{filename}",
    help="Run a script file, or list available scripts.",
    handler=_handler_root,
    sub_commands={
        "help": Command(
            help="Show /run help with the list of available .run scripts.",
            handler=_handler_help,
        ),
        "legacy": Command(
            args=run_legacy.ARGS,
            help=run_legacy.HELP,
            long_help=run_legacy.LONG_HELP,
            handler=run_legacy.HANDLER,
            flags=run_legacy.FLAGS,
        ),
        **build_folder_subcommands("run"),
    },
)
