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

from pathlib import Path
from typing import TYPE_CHECKING

from termapy import run_legacy
from termapy.builtins.commands.help import (
    _show_command_help,
    append_files_section,
)
from termapy.folder_ops import build_folder_subcommands
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

    # Bare /run.help -- the original behaviour.
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
    files = sorted(scripts_dir.glob("*.run"))
    if not files:
        ctx.io.output("  run/ (empty)")
        return CmdResult.ok(value="")

    entries: list[tuple[str, str]] = [
        (f.name, extract_docstring(f)[0]) for f in files
    ]
    name_width = max(len(n) for n, _ in entries)

    ctx.io._write("  run/")
    out_lines: list[str] = []
    for name, summary in entries:
        if summary:
            line = f"    {name:<{name_width}}  --  {summary}"
        else:
            line = f"    {name}"
        ctx.io._write(line)
        out_lines.append(f"{name}\t{summary}" if summary else name)
    return CmdResult.ok(value="\n".join(out_lines))


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
_FOLDER_SUBS = build_folder_subcommands("run")
# Override .list with the docstring-aware version.  The other folder
# subs (.dump / .show / .explore) keep the generic behaviour -- they
# operate on file content, not metadata.
_FOLDER_SUBS["list"] = Command(
    args="",
    help="List .run scripts with docstring summaries.",
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
        **_FOLDER_SUBS,
    },
)
