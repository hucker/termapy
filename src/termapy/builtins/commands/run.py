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
(``picker="run"``: the dispatcher opens it where
``ctx.internal.open_picker`` is set, the TUI); CLI and MCP run
``/run.list`` instead (``bare_sub="list"``), the picker's contents.  Folder subcommands (.list/.dump/.show/.explore)
come from the shared ``build_folder_subcommands`` helper, and
``/run.edit`` is ``/edit.run`` under its noun-first name
(``edit.edit_command``).

``/run.profile`` runs a script with per-line timing through the same
``ctx.internal.run_script``, and its results folder ``prof/`` has the
shared folder family.  Until 2026-10-16 it was a hook the TUI and the
CLI each registered, with a hand-written copy of that family (review
2026-10-15, D8): MCP never had it although ``/help`` said it did.

``/run.last`` lists the lines typed at the prompt and replays the newest
of them, through ``ctx.dispatch`` as ``/repeat`` does.  The lines are the
engine's (``termapy.typed_lines``), read through
``ctx.internal.typed_lines``.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.builtins.commands._run_record import (
    _LONG_HELP as _RECORD_LONG_HELP,
    _handler as _handler_record,
    end_recording,
)
from termapy.folder_ops import (
    build_folder_subcommands,
    edit_command,
    file_table,
    list_entries,
)
from termapy.folders import (
    FOLDER_EXTS,
    FOLDER_PATTERNS,
    PROFILE_TMP_PREFIX,
    RUN,
    default_file_name,
    ensure_folder,
    file_suggestion,
)
from termapy.plugins import CapabilitySet, CmdResult, Column, Command, UsageError
from termapy.plugins.params import ParamSpec
from termapy.run_docstring import extract_docstring

if TYPE_CHECKING:
    from termapy.plugins import PluginContext
    from termapy.typed_lines import TypedLine


def _resolve_script(ctx: PluginContext, name: str) -> Path | None:
    """Resolve a script name to a .run path in the config's run/ dir.

    Accepts the bare stem (``welcome``) or the full filename
    (``welcome.run``), through ``folders.default_file_name``.  Returns
    ``None`` if the file doesn't exist or no config is loaded.
    """
    path = default_file_name(ctx.fs.scripts_dir / name, FOLDER_EXTS[RUN])
    return path if path.is_file() else None


# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).


def _handler_help(ctx: PluginContext, args: str) -> CmdResult:
    """``/run.help`` (bare) or ``/run.help <script>``.

    Bare: same as ``/help run``, which ends with the scripts ``/run.list`` shows.
    With an argument: print the script's leading ``#`` docstring
    block (the convention is described in
    ``termapy.run_docstring``).  Missing or undocumented scripts
    fail with a clear message rather than silently printing nothing.
    """
    name = args.strip()
    if name:
        path = _resolve_script(ctx, name)
        if path is None:
            hint = file_suggestion(ctx.fs.scripts_dir, name, FOLDER_EXTS[RUN])
            return CmdResult.fail(msg=f"Script not found: {name}{hint}")
        _summary, full = extract_docstring(path)
        if not full:
            return CmdResult.fail(msg=f"{path.name}: no docstring (add # comments at the top).")
        ctx.io.output(full)
        return CmdResult.ok(value=full)

    # Bare /run.help is /help run: the page, ending with /run.list's scripts.
    return ctx.internal.dispatch("help run")


def _handler_list(ctx: PluginContext, args: str) -> CmdResult:
    """``/run.list`` -- list .run scripts with their titles.

    The generic folder listing plus a TITLE column (and a ``title`` key in
    each data record), the first line of the docstring, as ``/proto.list``
    shows a ``.pro`` file's declared name.  Scripts without a docstring
    still appear with an empty title, so the listing is never silently
    filtered.
    """
    files = list_entries(ctx.fs.scripts_dir, FOLDER_PATTERNS[RUN])
    title = Column("TITLE", lambda record: record["title"])
    return file_table(
        "run/",
        "script",
        files,
        extra=((title, lambda file: extract_docstring(file)[0], "title"),),
        run=lambda record: f"run {record['name']}",
        edit=lambda record: f"run.edit {record['name']}",
    )


def _handler_root(ctx: PluginContext, args: str) -> CmdResult:
    """``/run`` -- with a filename, dispatches to the host's script runner.

    A bare call never arrives: the dispatcher opens the Run picker in the
    TUI (``picker="run"``) and runs ``/run.list`` elsewhere
    (``bare_sub="list"``).

    The host wires ``start_script`` and ``run_script`` onto
    ``ctx.internal`` in ``TerminalHost._build_context``; this handler
    is host-agnostic and works in every frontend.
    """
    arg = args.strip()
    if not arg:
        raise UsageError()

    if ctx.internal.start_script is None or ctx.internal.run_script is None:
        # Defensive: a host that doesn't wire the script runner can't
        # execute scripts.  Should not happen in practice -- TerminalHost
        # wires both -- but guard so a misconfigured embed doesn't crash.
        return CmdResult.fail(msg="This host does not support script execution.")

    verbose = ctx.output_level == "verbose"
    # --echo shows each command line as it runs, as if typed at the prompt
    # (a script otherwise forces echo off).  The locals name what the user
    # watches: the progress/timing line (verbose) and the command echo.
    echo = ctx.flag("--echo")
    path, result = ctx.internal.start_script(arg)
    if path:
        ctx.internal.run_script(path, verbose=verbose, echo=echo)
    return result


def _handler_profile(ctx: PluginContext, args: str) -> CmdResult:
    """``/run.profile <script>`` -- run a script with per-line timing.

    The runner writes the timings to ``prof/<script>_<timestamp>.csv``.
    Inside a script the runner intercepts this line itself, to run the
    nested script inline (``ReplEngine._BLOCKING_COMMANDS``).
    """
    arg = args.strip()
    if not arg:
        raise UsageError()
    if ctx.internal.start_script is None or ctx.internal.run_script is None:
        return CmdResult.fail(msg="This host does not support script execution.")
    path, result = ctx.internal.start_script(arg)
    if path:
        ctx.internal.run_script(path, profile=True, verbose=ctx.output_level == "verbose")
    return result


def _handler_profile_cmd(ctx: PluginContext, args: str) -> CmdResult:
    """``/run.profile.cmd <command>`` -- profile one command as a one-line script.

    One command string, never split at a newline (the one-command-string
    rule): several commands are a ``.run`` file and ``/run.profile``.  A
    first token with a dot and no prefix is taken as a command
    (``port.info``).
    """
    line = args.strip()
    if not line:
        raise UsageError()
    if ctx.internal.start_script is None or ctx.internal.run_script is None:
        return CmdResult.fail(msg="This host does not support script execution.")
    if not line.startswith(ctx.prefix) and "." in line.split()[0]:
        line = ctx.prefix + line
    name = f"{PROFILE_TMP_PREFIX}{int(time.time() * 1000)}{FOLDER_EXTS[RUN]}"
    (ensure_folder(ctx.fs.scripts_dir) / name).write_text(line + "\n", encoding="utf-8")
    path, result = ctx.internal.start_script(name)
    if path:
        ctx.internal.run_script(path, profile=True)
    return result


# A number fills the input with its replay rather than running it: a replay
# sends to the device, so the click leaves the Enter to the user.
_LAST_COLUMNS = (
    Column(
        "#",
        lambda record: str(record["number"] or ""),
        right=True,
        prefill=lambda record: f"run.last {record['number']}",
    ),
    Column("COMMAND", lambda record: record["command"]),
    Column(
        "RESULT",
        lambda record: "ok" if record["success"] else record["error"].partition("\n")[0],
        style=lambda record: "" if record["success"] else "red",
    ),
)


def _last_records(lines: list[TypedLine]) -> list[dict]:
    """One record per typed line, oldest first; a line that succeeded is numbered, newest = 1."""
    remaining = sum(line.success for line in lines)
    records = []
    for line in lines:
        records.append(
            {
                "number": remaining if line.success else None,
                "command": line.text,
                "success": line.success,
                "error": line.error,
            }
        )
        remaining -= line.success
    return records


def _handler_last(ctx: PluginContext, args: str) -> CmdResult:
    """``/run.last`` lists the lines typed at the prompt; ``/run.last <count>`` replays the newest.

    The lines are the active config's, as typed (``ctx.internal.typed_lines``,
    fed by ``TerminalHost._dispatch_typed``): a typed ``/run`` is one line,
    whose replay runs the script and so its steps, and the lines inside a
    script, a paste or a replay are never listed.  Only a line that
    succeeded is numbered and replayed, so a
    typo stays in the Up-arrow history to fix and never runs again from
    here.  A replay runs rows ``count``..1 in the order typed, each command
    echoed as it runs (as ``/run --echo`` does; a device line follows the
    ``echo`` flag, as when typed), and stops at Escape or at the first line
    that fails, whose failure is the answer.
    """
    lines = ctx.internal.typed_lines()
    count = ctx.arg("count")
    if count is None:
        stem = Path(ctx.config_path).stem
        title = f"typed in {stem}" if stem else "typed"
        return CmdResult.table(title, "line", _LAST_COLUMNS, _last_records(lines))
    numbered = [line.text for line in lines if line.success]
    if not numbered:
        return CmdResult.fail(msg="No commands to replay.")
    if count > len(numbered):
        word = "command" if len(numbered) == 1 else "commands"
        return CmdResult.fail(msg=f"Only {len(numbered)} {word} to replay")
    return _replay(ctx, numbered[-count:])


# Whether a replay is running, so a replay never starts another.  Module
# state rather than ctx.ns(): a config load empties the namespaces, and a
# replayed /cfg.load makes one part way through.
_replaying = False

# The namespace holding what a replay puts back when it ends.
_REPLAY_NS = "replay"


def _replay(ctx: PluginContext, lines: list[str]) -> CmdResult:
    """Run ``lines`` in order, echoed, until Escape or the first failure.

    The gate keeps ``/run.last`` out of scripts (``script_idle``) and this
    refuses one inside a replay, so a replay is always the outermost
    cancellable operation: it clears a stale stop before it starts and
    consumes the one that ends it, as ``/repeat`` does at the prompt
    (registry ``plugin-never-clears-stop``).  Echo is forced on for the
    replay and put back after, as ``/run --echo`` forces it for a run.  The
    value to put back waits in a namespace, so a replayed ``/cfg.load`` takes
    it with everything else and the new config's start stands (registry
    ``config-load-deterministic``).
    """
    global _replaying
    if _replaying:
        return CmdResult.fail(msg="A replay is already running.")
    stop = ctx.internal.script_stop_event
    if stop is not None:
        stop.clear()
    flags = ctx.ns("flags")
    saved = ctx.ns(_REPLAY_NS)
    saved["echo_repl"] = flags.get("echo_repl", False)
    flags["echo_repl"] = True
    _replaying = True
    ran = 0
    try:
        for line in lines:
            if stop is not None and stop.is_set():
                break
            result = ctx.dispatch(line, report=False)
            if not result.success:
                return result
            ran += 1
    finally:
        _replaying = False
        if "echo_repl" in saved:
            flags["echo_repl"] = saved.pop("echo_repl")
    if ran < len(lines):
        if stop is not None:
            stop.clear()
        ctx.io.result(f"Replay stopped after {ran} of {len(lines)} commands.")
    return CmdResult.ok(value=ran)


def on_config_load(ctx: PluginContext) -> None:
    """A recording writes into the config it started in, so a load ends it."""
    end_recording(ctx)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
_FOLDER_SUBS = build_folder_subcommands("run")
# Override .list with the docstring-aware version.  The other folder
# subs (.dump / .show / .explore) keep the generic behavior -- they
# operate on file content, not metadata.
_FOLDER_SUBS["list"] = Command(
    args="",
    help="List .run scripts, newest first, with size, age, and title.",
    handler=_handler_list,
    shows=True,
    state=True,
    needs=CapabilitySet.CONFIG_LOADED,
)


COMMAND = Command(
    group="terminal",
    name="run",
    args="{filename}",
    help="Run a script file, or list available scripts.",
    intro=(
        "A script is a text file of commands and device lines, run one after another."
        " Run, record and time the scripts in this config's run/ folder, and manage "
        "them here."
    ),
    handler=_handler_root,
    flags={"--echo": "Echo each command line as it runs (a script is quiet by default)."},
    files="run",
    picker="run",
    bare_sub="list",
    help_list="list",
    sub_commands={
        "help": Command(
            args="{script}",
            help="Show {prefix}run help, or print a script's docstring.",
            long_help=(
                "Bare {prefix}run.help shows the {prefix}help run landscape with "
                "the list of available .run scripts.\n"
                "\n"
                "{prefix}run.help <script> prints the script's leading "
                "# docstring block -- the convention is one or more "
                "# comments at the very top of the file, ending at the "
                "first blank or non-comment line.  Mirrors Python's "
                "module-docstring shape.\n"
                "\n"
                "If the script has no docstring, {prefix}run.help reports "
                "that rather than printing silence -- the LLM / human "
                "knows whether to look inside the file."
            ),
            handler=_handler_help,
            shows=True,
            files="run",
        ),
        "record": Command(
            args="{filename}",
            help="Record commands to a .run script (bare {prefix}run.record stops).",
            long_help=_RECORD_LONG_HELP,
            handler=_handler_record,
        ),
        "edit": edit_command("run"),
        "last": Command(
            help="Replay the last <count> commands typed (bare {prefix}run.last lists them).",
            long_help=(
                "Bare {prefix}run.last lists the lines typed at the prompt in this\n"
                "config, oldest first.  A line that succeeded gets a number, the\n"
                "newest 1; a failed line is listed unnumbered with its error, and\n"
                "stays in the Up-arrow history to fix.\n"
                "\n"
                "{prefix}run.last <count> runs the numbered lines <count>..1 again,\n"
                "in the order they were typed.  Each command is echoed as it runs,\n"
                "as {prefix}run --echo does; a device line echoes as the echo\n"
                "setting says, as it did when typed.  Escape stops the replay, and\n"
                "so does the first line that fails.\n"
                "\n"
                "The list is what you typed, line for line.  A typed\n"
                "{prefix}run my_script is one line: replaying it runs the script\n"
                "again, and the script runs its own steps.  The lines inside a\n"
                "script are never listed, nor a paste, nor {prefix}run.last itself.\n"
                "Each config keeps its own list while termapy runs; a restart\n"
                "starts every list empty.\n"
                "\n"
                "It is a convenience for the prompt: a script that runs\n"
                "{prefix}run.last is refused, and so is a replay inside a replay."
            ),
            handler=_handler_last,
            params=[
                ParamSpec(
                    "count",
                    "int",
                    positional=True,
                    min=1,
                    help="how many of the newest numbered lines to replay",
                ),
            ],
            # At the prompt only: a human typing (not MCP), and not a script line.
            needs=CapabilitySet(interactive=True, script_idle=True),
        ),
        "profile": Command(
            args="<filename>",
            help="Run a script with per-line timing (CSV to prof/).",
            intro=(
                "Run a script and time each line, with the timings saved as CSV in the "
                "config's prof/ folder, and manage those files."
            ),
            handler=_handler_profile,
            files="run",
            sub_commands={
                "cmd": Command(
                    args="<command>",
                    help="Profile a single command.",
                    handler=_handler_profile_cmd,
                ),
                **build_folder_subcommands("prof", noun="profile", command="run.profile"),
            },
        ),
        **_FOLDER_SUBS,
    },
)
