"""Built-in plugin: ``/run`` -- execute .run scripts from any host.

Until this commit ``/run`` was registered as a host hook in three
places: ``app_hooks.register_tui_hooks``, ``CLITerminal._register_hooks``,
and ``MCPHost._register_hooks``.  Each one reached into adapter
internals (TUI's ``app._run_script`` Worker, CLI's ``self._hook_run``,
MCP's inline lambda) because the script-runner machinery lives on
the host.

Now the host exposes the runner via the internal handle
(``ctx.internal.start_script`` + ``ctx.internal.run_script``, wired in
``TerminalHost._build_context``), and this single built-in handler
covers all three frontends.  ``TerminalHost._run_script`` is the
default synchronous implementation; ``SerialTerminal`` (TUI)
overrides it with the ``@work(thread=True)`` Worker version that
posts ``ScriptStarted`` / ``ScriptProgress`` / ``ScriptFinished``
messages for the overlay -- same polymorphism the rest of the
plugin layer uses.

Bare ``/run`` opens the Run picker when a host installs one
(``ctx.internal.open_picker`` is set in TUI, unset elsewhere); CLI
and MCP fall through to ``/help run`` with the available-files
section appended.  Folder subcommands (.list/.dump/.show/.explore)
come from the shared ``build_folder_subcommands`` helper.
``/run.legacy`` rides along by re-exporting the existing
``termapy.run_legacy`` handler.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from termapy import run_legacy
from termapy.builtins.commands._run_record import (
    _LONG_HELP as _RECORD_LONG_HELP,
    _handler as _record_handler,
)
from termapy.builtins.commands.help import (
    _show_command_help,
    append_files_section,
)
from termapy.folder_ops import (
    build_folder_subcommands,
    file_record,
    format_file_lines,
    list_entries,
)
from termapy.plugins import CmdResult, Command
from termapy.run_docstring import extract_docstring

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _resolve_script(ctx: PluginContext, name: str) -> Path | None:
    """Resolve a script name to a .run path in the config's run/ dir.

    Accepts the bare stem (``welcome``) or the full filename
    (``welcome.run``).  Returns ``None`` if the file doesn't exist
    or no config is loaded.
    """
    scripts_dir = ctx.fs.scripts_dir
    if not scripts_dir.is_dir():
        return None
    if not name.endswith(".run"):
        name = name + ".run"
    path = scripts_dir / name
    return path if path.is_file() else None


def _handler_help(ctx: PluginContext, args: str) -> CmdResult:
    """``/run.help`` (bare) or ``/run.help <script>``.

    Bare: same as ``/help run`` with the AVAILABLE RUN FILES list.
    With an argument: print the script's leading ``#`` docstring
    block (the convention is described in
    ``termapy.run_docstring``).  Missing or undocumented scripts
    fail with a clear message rather than silently printing nothing.
    """
    name = args.strip()
    if name:
        path = _resolve_script(ctx, name)
        if path is None:
            return CmdResult.fail(msg=f"Script not found: {name}")
        _summary, full = extract_docstring(path)
        if not full:
            return CmdResult.fail(
                msg=f"{path.name}: no docstring (add # comments at the top)."
            )
        ctx.io.output(full)
        return CmdResult.ok(value=full)

    # Bare /run.help -- the original behavior.
    result = _show_command_help(ctx, "run")
    scripts_dir = ctx.fs.scripts_dir
    files = (
        sorted(f.name for f in scripts_dir.glob("*.run"))
        if scripts_dir.is_dir()
        else []
    )
    append_files_section(ctx, "AVAILABLE RUN FILES", files)
    return result


def _handler_list(ctx: PluginContext, args: str) -> CmdResult:
    """``/run.list`` -- list .run scripts with their docstring summaries.

    Overrides the generic folder-list handler so the user sees
    "filename  --  one-line summary" instead of just the filenames.
    Scripts without a docstring still appear (with no summary), so
    the listing is never silently filtered.
    """
    scripts_dir = ctx.fs.scripts_dir
    if not scripts_dir.is_dir():
        return CmdResult.fail(msg="No config loaded.")
    files = list_entries(scripts_dir, "*.run")
    summaries = [extract_docstring(file)[0] for file in files]
    value = "\n".join(
        f"{file.name}\t{summary}" if summary else file.name
        for file, summary in zip(files, summaries, strict=True)
    )
    if ctx.wants_data:
        return CmdResult.ok(
            value=value,
            data=[
                {**file_record(file), "summary": summary}
                for file, summary in zip(files, summaries, strict=True)
            ],
        )
    if not files:
        ctx.io.output("  run/ (empty)")
        return CmdResult.ok(value="")

    ctx.io.output("  run/")
    for line, summary in zip(format_file_lines(files), summaries, strict=True):
        ctx.io.output(f"    {line}  --  {summary}" if summary else f"    {line}")
    return CmdResult.ok(value=value)


def _handler_root(ctx: PluginContext, args: str) -> CmdResult:
    """``/run`` -- bare opens picker (TUI) / shows help (CLI, MCP);
    with a filename, dispatches to the host's script runner.

    The host wires ``start_script`` and ``run_script`` onto
    ``ctx.internal`` in ``TerminalHost._build_context``; this handler
    is host-agnostic and works in every frontend.
    """
    arg = args.strip()
    if not arg:
        if ctx.internal.open_picker is not None:
            return ctx.internal.open_picker("run")
        # No picker in this host -- show help + the list of
        # available .run files (the closest CLI / MCP equivalent
        # to "open the picker").
        return _handler_help(ctx, args)

    if ctx.internal.start_script is None or ctx.internal.run_script is None:
        # Defensive: a host that doesn't wire the script runner can't
        # execute scripts.  Should not happen in practice -- TerminalHost
        # wires both -- but guard so a misconfigured embed doesn't crash.
        return CmdResult.fail(msg="This host does not support script execution.")

    verbose = ctx.output_level == "verbose"
    path, result = ctx.internal.start_script(arg)
    if path:
        ctx.internal.run_script(path, verbose=verbose)
    return result


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
_FOLDER_SUBS = build_folder_subcommands("run")
# Override .list with the docstring-aware version.  The other folder
# subs (.dump / .show / .explore) keep the generic behavior -- they
# operate on file content, not metadata.
_FOLDER_SUBS["list"] = Command(
    args="",
    help="List .run scripts, newest first, with size, age, and docstring summary.",
    handler=_handler_list,
)


COMMAND = Command(
    name="run",
    args="{filename}",
    help="Run a script file, or list available scripts.",
    handler=_handler_root,
    sub_commands={
        "help": Command(
            args="{script}",
            help="Show /run help, or print a script's docstring.",
            long_help=(
                "Bare /run.help shows the /help run landscape with "
                "the list of available .run scripts.\n"
                "\n"
                "/run.help <script> prints the script's leading "
                "# docstring block -- the convention is one or more "
                "# comments at the very top of the file, ending at the "
                "first blank or non-comment line.  Mirrors Python's "
                "module-docstring shape.\n"
                "\n"
                "If the script has no docstring, /run.help reports "
                "that rather than printing silence -- the LLM / human "
                "knows whether to look inside the file."
            ),
            handler=_handler_help,
        ),
        "legacy": Command(
            args=run_legacy.ARGS,
            help=run_legacy.HELP,
            long_help=run_legacy.LONG_HELP,
            handler=run_legacy.HANDLER,
            flags=run_legacy.FLAGS,
        ),
        "record": Command(
            args="{filename}",
            help="Record commands to a .run script (bare /run.record stops).",
            long_help=_RECORD_LONG_HELP,
            handler=_record_handler,
        ),
        **_FOLDER_SUBS,
    },
)
