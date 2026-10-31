"""Built-in plugin: inspect app-wide state and config.

Termapy has two scopes of configuration: per-project (lives in
``termapy_cfg/<name>/`` and is accessed via ``/cfg``) and **app-wide**
(lives in the OS's standard user data location and is accessed via
``/app``).

Command surface:

- ``/app.list``         list the app folder(s): files and the running sessions
- ``/app.explore``      open the app folder in the OS file manager
- ``/app.state``        print ``state.json``'s path
- ``/app.state.dump``   print ``state.json`` contents to the terminal
- ``/app.state.edit``   open ``state.json`` in the system editor
- ``/app.config``       print ``config.json``'s path
- ``/app.config.dump``  print ``config.json`` contents to the terminal
- ``/app.config.edit``  open ``config.json`` in the system editor
- ``/app.running``      list the termapys running on this machine
- ``/app.kill``         stop the termapys a target picks, after a yes

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
import os
from typing import TYPE_CHECKING

from termapy import breadcrumbs
from termapy.app_dirs import (
    app_config_dir,
    app_config_file,
    app_state_dir,
    app_state_file,
    load_app_config,
    load_app_state,
)
from termapy.folder_ops import FILE_COLUMNS, file_record, list_entries
from termapy.plugins import CapabilitySet, CmdResult, Column, Command
from termapy.plugins.params import ParamSpec
from termapy.plugins.views import counted

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).


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


_FILE_CELLS = {column.title: column for column in FILE_COLUMNS}


def _entry_size(record: dict) -> str:
    """A file's size, or for the ``running/`` folder the sessions it holds."""
    if "sessions" in record:
        return counted(record["sessions"], "session")
    return _FILE_CELLS["SIZE"].cell(record)


# The /app.list columns: the shared file columns, with NAME running each
# entry's viewer and SIZE counting the sessions in running/.
_LIST_COLUMNS = (
    Column("NAME", lambda record: record["name"], run=lambda record: record["open"]),
    Column("SIZE", _entry_size, right=True),
    _FILE_CELLS["UPDATED"],
)


def _running_record() -> dict | None:
    """The ``running/`` row, its size the live sessions in it; None before any session made it."""
    folder = breadcrumbs.running_dir()
    if not folder.is_dir():
        return None
    return {
        **file_record(folder),
        "name": f"{folder.name}/",
        "bytes": None,
        "folder": str(folder.parent),
        "sessions": len(breadcrumbs.read_running()),
        "open": "app.running",
    }


def _handler_list(ctx: PluginContext, args: str) -> CmdResult:
    """List the app folder, newest first: NAME, SIZE, UPDATED.

    The files, each record ``file_record`` plus ``folder`` (its folder's
    full path, so a structured consumer learns where the files are on
    every platform) and ``open`` (the command a click on its name runs:
    its ``.dump``, or the folder).  Ahead of them the ``running/`` folder of
    session breadcrumbs, sized by how many sessions run, opening
    ``/app.running``.  Windows and macOS keep state and config in one
    folder, which is the table's title; Linux splits them (XDG_STATE_HOME
    vs XDG_CONFIG_HOME), and then a FOLDER column shows it.  A folder that
    does not exist yet lists as empty: listing creates nothing.
    """
    viewers = {app_state_file().name: "app.state.dump", app_config_file().name: "app.config.dump"}
    folders = sorted({app_state_dir(), app_config_dir()})
    files = [file for folder in folders for file in list_entries(folder, "*")]
    records = [
        {
            **file_record(file),
            "folder": str(file.parent),
            "open": viewers.get(file.name, "app.explore"),
        }
        for file in files
    ]
    running = _running_record()
    if running is not None:
        records.insert(0, running)
    names = "\n".join(record["name"] for record in records)
    if len(folders) == 1:
        return CmdResult.table(str(folders[0]), "item", _LIST_COLUMNS, records, value=names)
    columns = (*_LIST_COLUMNS, Column("FOLDER", lambda record: record["folder"]))
    return CmdResult.table("app folders", "item", columns, records, value=names)


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


# ── /app.ver ──────────────────────────────────────────────────────────────────


def _installed_version() -> str:
    """Return the installed termapy version, or 'unknown' for source checkouts.

    PackageNotFoundError fires when termapy is being run from a git clone
    without ``pip install .`` -- common during development.  That's the
    only exception this path can legitimately raise.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("termapy")
    except PackageNotFoundError:
        return "unknown"


def _handler_ver(ctx: PluginContext, args: str) -> CmdResult:
    """Print the installed termapy version."""
    ver = _installed_version()
    ver_str = f"termapy v{ver}"
    ctx.io.result(ver_str)
    return CmdResult.ok(value=ver_str)


def _handler_ver_latest(ctx: PluginContext, args: str) -> CmdResult:
    """Return just the latest termapy version on PyPI (bare data getter).

    Symmetric with ``/app.ver``: that reports the LOCAL installed
    version, this reports the REMOTE PyPI version.  Bypasses the 7-day
    throttle used by the background banner -- the user typed the
    command, they want a fresh answer.
    """
    from termapy.update_check import check_now

    installed = _installed_version()
    latest, _ = check_now(installed if installed != "unknown" else "0")
    if latest is None:
        return CmdResult.fail(msg="Could not reach PyPI.")
    ctx.io.result(latest)
    return CmdResult.ok(value=latest)


def _handler_ver_info(ctx: PluginContext, args: str) -> CmdResult:
    """Verbose human-readable installed-vs-PyPI comparison.

    Matches the ``/X.info`` convention (/cfg.info, /port.info, etc.).
    Bypasses the 7-day throttle used by the background banner.
    """
    from termapy.update_check import check_now

    installed = _installed_version()
    if installed == "unknown":
        return CmdResult.fail(
            msg="Cannot check for updates: termapy is not installed "
            "(running from a source checkout?)."
        )

    latest, outdated = check_now(installed)
    if latest is None:
        return CmdResult.fail(msg="Could not reach PyPI.")

    if outdated:
        line = f"installed v{installed}  ->  latest v{latest}  (update available)"
    else:
        line = f"installed v{installed}  (up to date; latest v{latest})"
    ctx.io.result(line)
    return CmdResult.ok(value=latest)


_APP_LONG_HELP = """\
What belongs to termapy as a whole rather than to one config: the app
folder and its files, the version, and the termapy sessions running on
this machine.

Sessions: {prefix}app.running lists them; {prefix}app.kill others stops every
one but this, {prefix}app.kill all this one too (last, through {prefix}exit).

Files: {prefix}app.state and {prefix}app.config print a path; add .dump to print
the file, .edit to open it.  state.json is written by termapy (update-check
times), so a hand edit is usually overwritten; config.json is for global
preferences, though no feature reads it yet.  {prefix}app.ver.latest and
{prefix}app.ver.info compare with PyPI."""


# ── /app.running, /app.kill ───────────────────────────────────────────────────


def _session_record(crumb: breadcrumbs.Crumb) -> dict:
    """One running termapy as a record: the table's row and the JSON answer."""
    return {
        "pid": crumb.pid,
        "frontend": crumb.frontend,
        "config": crumb.config_name,
        "config_path": crumb.config,
        "port": crumb.port,
        "open_port": crumb.open_port,
        "started": crumb.started,
        "this_session": crumb.pid == os.getpid(),
    }


_SESSION_COLUMNS = (
    Column("PID", lambda record: str(record["pid"]), right=True),
    Column(
        "MODE",
        lambda record: record["frontend"] + (" (this)" if record["this_session"] else ""),
    ),
    Column("CONFIG", lambda record: record["config"] or "-"),
    Column("PORT", lambda record: record["open_port"] or "-"),
    Column("STARTED", lambda record: record["started"].replace("T", " ")),
)


def _handler_running(ctx: PluginContext, args: str) -> CmdResult:
    """List the termapys running on this machine, from their breadcrumbs (``breadcrumbs``)."""
    records = [_session_record(crumb) for crumb in breadcrumbs.read_running()]
    value = "\n".join(str(record["pid"]) for record in records)
    return CmdResult.table("running termapys", "session", _SESSION_COLUMNS, records, value=value)


def _handler_kill(ctx: PluginContext, args: str) -> CmdResult:
    """Stop the termapys a target picks, after a yes (``breadcrumbs.stop``).

    ``others`` is every session but this one (tmux's ``kill-session -a``);
    ``all`` and any target that picks this session include it.  This
    session is never killed: it goes last, through ``exit``, so it saves
    its history and ends its log like any exit.  If another session would
    not stop, this one stays open to show why.
    """
    target = ctx.arg("target")
    running = breadcrumbs.read_running()
    picked = breadcrumbs.select(running, target)
    if target.casefold() == "others":
        picked = [crumb for crumb in picked if crumb.pid != os.getpid()]
        if not picked:
            return CmdResult.fail(msg="No other termapy is running.")
    if not picked:
        return CmdResult.fail(
            msg=f"No running termapy matches {target}{breadcrumbs.suggest(running, target)}"
        )
    own = any(crumb.pid == os.getpid() for crumb in picked)
    others = [crumb for crumb in picked if crumb.pid != os.getpid()]
    noun = "session" if len(picked) == 1 else "sessions"
    listing = "; ".join(f"{crumb.pid} ({crumb.summary})" for crumb in others)
    if own:
        listing = f"{listing}; {os.getpid()} (this session)" if listing else "this session"
    if not ctx.ui.confirm(f"Stop {len(picked)} termapy {noun}: {listing}?"):
        return CmdResult.fail(msg="Nothing stopped.")
    breadcrumbs.prune_stale()
    stopped = [crumb for crumb in others if breadcrumbs.stop(crumb)]
    for crumb in stopped:
        ctx.io.result(f"Stopped {crumb.pid} ({crumb.summary})")
    failed = [crumb for crumb in others if crumb not in stopped]
    if failed:
        return CmdResult.fail(
            msg="Could not stop " + ", ".join(f"{crumb.pid} ({crumb.summary})" for crumb in failed)
        )
    if own:
        return ctx.internal.dispatch("exit")
    return CmdResult.ok(
        value="\n".join(str(crumb.pid) for crumb in stopped),
        data=[_session_record(crumb) for crumb in stopped],
    )


_KILL_LONG_HELP = (
    "Stops termapy sessions, after a yes.  <target> is one of:\n"
    "\n"
    "  others   every session but this one\n"
    "  all      every session, this one last\n"
    "  COM3     the session holding that port\n"
    "  rig      the sessions on that config (rig* picks several)\n"
    "\n"
    "This session is never killed: when a target picks it, it goes last,\n"
    "through {prefix}exit, so it saves its history and ends its log.  Use it\n"
    "for a clean system, say before an update, when a forgotten MCP server\n"
    "still holds a port.  Not over MCP: an agent cannot end your sessions.\n"
    "{prefix}app.running lists the sessions; termapy --kill does the same\n"
    "from the command line."
)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    group="advanced",
    name="app",
    help="App-wide state, config and running sessions.",
    long_help=_APP_LONG_HELP,
    handler=None,
    sub_commands={
        "running": Command(
            help="List the termapy sessions running on this machine.",
            long_help=(
                "Every running termapy -- a TUI, a CLI, an MCP server, a web or\n"
                "VT100 session -- with its mode, config and the port it has open.\n"
                "This session is marked (this).  {prefix}app.kill stops them."
            ),
            handler=_handler_running,
            shows=True,
        ),
        "kill": Command(
            help="Stop termapy sessions: others, all, a port or a config (asks first).",
            long_help=_KILL_LONG_HELP,
            handler=_handler_kill,
            params=[
                ParamSpec(
                    "target",
                    "str",
                    positional=True,
                    required=True,
                    hint="<target>",
                    help="others, all, a port such as COM3, or a config name",
                )
            ],
            needs=CapabilitySet(interactive=True, confirm_dialog=True),
            safety="destructive",
        ),
        "list": Command(
            help="List the app folder: its files and the running sessions, newest first.",
            handler=_handler_list,
            shows=True,
        ),
        "explore": Command(
            help="Open the app folder in the file manager.",
            handler=_handler_explore,
            shows=True,
            needs=CapabilitySet.GUI_APPS,
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
                    shows=True,
                ),
                "edit": Command(
                    help="Open state.json in the system editor.",
                    handler=_handler_state_edit,
                    needs=CapabilitySet.GUI_APPS,
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
                    shows=True,
                ),
                "edit": Command(
                    help="Open config.json in the system editor.",
                    handler=_handler_config_edit,
                    needs=CapabilitySet.GUI_APPS,
                ),
            },
        ),
        "ver": Command(
            help="Print termapy version (installed).",
            handler=_handler_ver,
            sub_commands={
                "latest": Command(
                    help="Print the latest termapy version on PyPI (bare value).",
                    long_help=(
                        "Fetches the latest termapy version from PyPI and "
                        "prints just the version string -- the data-getter "
                        "symmetric to {prefix}app.ver (which reports installed).  "
                        "Set as CmdResult.value so scripts can capture it "
                        "with $(LATEST) <- {prefix}app.ver.latest.\n"
                        "\n"
                        "Bypasses the 7-day throttle used by the background "
                        "banner -- this always hits PyPI fresh.  Network "
                        "failure returns an error.  Times out after 2 seconds.\n"
                        "\n"
                        "See {prefix}app.ver.info for a verbose installed-vs-PyPI compare."
                    ),
                    handler=_handler_ver_latest,
                ),
                "info": Command(
                    help="Print installed-vs-PyPI version comparison.",
                    long_help=(
                        "Fetches the latest termapy version from PyPI and "
                        "compares it to the installed version.  Prints a "
                        "human-readable line: either 'up to date' or "
                        "'update available' along with both version numbers.\n"
                        "\n"
                        "Bypasses the 7-day throttle used by the background "
                        "banner -- this always hits PyPI fresh.  Network "
                        "failure returns an error.  Times out after 2 seconds.\n"
                        "\n"
                        "Sets CmdResult.value to the latest PyPI version.  "
                        "See {prefix}app.ver.latest for just the bare PyPI value, or "
                        "{prefix}app.ver for just the installed version."
                    ),
                    handler=_handler_ver_info,
                ),
            },
        ),
    },
)
