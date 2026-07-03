"""Shared TUI/CLI hooks for the /run.profile.* command family.

Lives at the package top-level (not in ``builtins/commands/``)
because the handlers depend on host-specific machinery:
``app._run_script`` in TUI is a Textual ``@work(thread=True)``
method; in CLI it's a synchronous wrapper.  ``app._prof_dir`` is
shared but reaches into the host's ``config_path``.

Both hosts implement ``_run_script`` and ``_prof_dir`` on their
terminal class, and this module's ``register_run_profile_hooks(app)``
wires the six handlers as REPL hooks so both hosts get parity.

MCP does not call this -- MCP receives only built-in plugins, no
hook layer.  Promoting the read-only subcommands (``.dump``,
``.list``) to builtins is blocked today by the leaf ``/run.profile``
hook tree-wiping any pre-existing children at registration time;
revisit if MCP access to profile files becomes a requirement.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from termapy.defaults import cmd_prefix
from termapy.plugins import CapabilitySet, CmdResult

if TYPE_CHECKING:
    pass

# Was a class attribute on SerialTerminal (``app._PROFILE_TMP_PREFIX``);
# pulled here so the temp-script naming is consistent across TUI and CLI.
PROFILE_TMP_PREFIX = "_profile_tmp_"


def _hook_run_profile(app, ctx, args: str) -> CmdResult:
    """Run a .run script with per-line timing instrumentation.

    Dispatches to ``app._run_script(path, profile=True)`` which
    captures per-line wall-clock + dispatch times and writes a CSV
    profile to ``<prof_dir>/<script>_<timestamp>.csv``.

    Respects the live ``output_level``: ``verbose`` echoes each
    dispatched command (matches the un-profiled ``/run`` behavior).
    """
    path, result = app.repl.start_script(args)
    if path:
        verbose = ctx.output_level == "verbose"
        app._run_script(path, profile=True, verbose=verbose)
    return result


def _hook_run_profile_cmd(app, ctx, args: str) -> CmdResult:
    """Profile a single command by writing it to a temp .run script."""
    line = args.strip()
    if not line:
        ctx.io._write("Usage: /run.profile.cmd <command>", "red")
        return CmdResult.fail(msg="Usage: /run.profile.cmd <command>")
    prefix = cmd_prefix(app.cfg)
    if not line.startswith(prefix) and "." in line.split()[0]:
        line = prefix + line
    ts = str(int(time.time() * 1000))
    tmp_name = f"{PROFILE_TMP_PREFIX}{ts}.run"
    tmp_path = app.repl.scripts_dir / tmp_name
    parts = line.replace("\\n", "\n").split("\n")
    tmp_path.write_text(
        "\n".join(p.strip() for p in parts) + "\n", encoding="utf-8"
    )
    path, result = app.repl.start_script(tmp_name)
    if path:
        app._run_script(path, profile=True)
    return result


def _hook_run_profile_show(app, ctx, args: str) -> CmdResult:
    """Open the newest .csv profile file in the system viewer."""
    prof_dir = app._prof_dir()
    if not prof_dir:
        ctx.io._write("No config loaded.", "red")
        return CmdResult.fail(msg="No config loaded.")
    profs = sorted(prof_dir.glob("*.csv"), key=lambda f: f.stat().st_mtime)
    if not profs:
        ctx.io.output("No profile files found.")
        return CmdResult.fail(msg="No profile files found.")
    newest = profs[-1]
    ctx.io._write(f"Opening {newest.name}")
    ctx.fs.open_file(str(newest))
    return CmdResult.ok(value=newest)


def _hook_run_profile_dump(app, ctx, args: str) -> CmdResult:
    """Print newest (or named) profile file to the terminal."""
    prof_dir = app._prof_dir()
    if not prof_dir:
        ctx.io._write("No config loaded.", "red")
        return CmdResult.fail(msg="No config loaded.")
    name = args.strip()
    if name:
        path = prof_dir / name
        if not path.exists():
            ctx.io._write(f"File not found: {name}", "red")
            return CmdResult.fail(msg=f"File not found: {name}")
    else:
        profs = sorted(prof_dir.glob("*.csv"), key=lambda f: f.stat().st_mtime)
        if not profs:
            ctx.io.output("No profile files found.")
            return CmdResult.fail(msg="No profile files found.")
        path = profs[-1]
    try:
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            ctx.io.output(line)
    except OSError as e:
        ctx.io._write(f"Read error: {e}", "red")
        return CmdResult.fail(msg=f"Read error: {e}")
    return CmdResult.ok(value=text)


def _hook_run_profile_explore(app, ctx, args: str) -> CmdResult:
    """Open the prof/ directory in the system file browser."""
    prof_dir = app._prof_dir()
    if not prof_dir:
        ctx.io._write("No config loaded.", "red")
        return CmdResult.fail(msg="No config loaded.")
    prof_dir.mkdir(exist_ok=True)
    ctx.fs.open_file(str(prof_dir))
    return CmdResult.ok(value=prof_dir)


def _hook_run_profile_list(app, ctx, args: str) -> CmdResult:
    """List .csv profile files in prof/."""
    prof_dir = app._prof_dir()
    if not prof_dir:
        ctx.io._write("No config loaded.", "red")
        return CmdResult.fail(msg="No config loaded.")
    if not prof_dir.exists():
        ctx.io.output("  (no profile files)")
        return CmdResult.ok(value="")
    profs = sorted(prof_dir.glob("*.csv"))
    if not profs:
        ctx.io.output("  (no profile files)")
        return CmdResult.ok(value="")
    for f in profs:
        ctx.io._write(f"  {f.name}")
    return CmdResult.ok(value="\n".join(f.name for f in profs))


def register_run_profile_hooks(app) -> None:
    """Register the six /run.profile.* hooks on ``app.repl``.

    Called by both ``SerialTerminal`` (TUI, from
    ``register_tui_hooks``) and ``CLITerminal`` (from
    ``_register_hooks``) so both hosts get parity.  The leaf
    ``/run.profile`` registers first because ``register_hook``
    tree-wipes any pre-existing children at the same name.
    """
    app.repl.register_hook(
        "run.profile",
        "<filename>",
        "Run a script with per-line timing.",
        lambda ctx, args: _hook_run_profile(app, ctx, args),
        source="app",
    )
    app.repl.register_hook(
        "run.profile.show",
        "",
        "Open the newest .csv profile in system viewer.",
        lambda ctx, args: _hook_run_profile_show(app, ctx, args),
        source="app",
        needs=CapabilitySet(gui_apps=True),
    )
    app.repl.register_hook(
        "run.profile.explore",
        "",
        "Open the prof/ directory in file explorer.",
        lambda ctx, args: _hook_run_profile_explore(app, ctx, args),
        source="app",
        needs=CapabilitySet(gui_apps=True),
    )
    app.repl.register_hook(
        "run.profile.cmd",
        "<command>",
        "Profile a single command.",
        lambda ctx, args: _hook_run_profile_cmd(app, ctx, args),
        source="app",
    )
    app.repl.register_hook(
        "run.profile.dump",
        "{filename}",
        "Print newest (or named) profile to the terminal.",
        lambda ctx, args: _hook_run_profile_dump(app, ctx, args),
        source="app",
    )
    app.repl.register_hook(
        "run.profile.list",
        "",
        "List profile (.csv) files.",
        lambda ctx, args: _hook_run_profile_list(app, ctx, args),
        source="app",
    )
