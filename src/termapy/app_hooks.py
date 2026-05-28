"""TUI-specific REPL hooks registered against the app's ReplEngine.

These hook handlers and the registration function previously lived as
methods on ``SerialTerminal`` in ``app.py``.  Each handler now takes
the app as its first argument and reaches widget / engine / cfg
state via ``app.X``.  Hooks that need TUI capabilities (modals,
screen capture, GUI editors) still only run in TUI mode -- the
``CapabilitySet`` declarations on each ``register_hook`` enforce
that.

Pattern: each ``_hook_X`` is a free function ``(app, ctx, args)``.
The ``register_tui_hooks(app)`` function wraps each handler in a
``lambda ctx, args: _hook_X(app, ctx, args)`` so the REPL's
register_hook contract is preserved.

The cfg-confirm callback (``_hook_cfg_confirm``) is NOT here -- it
isn't a REPL hook but an engine.save_cfg callback; it stays on
``SerialTerminal`` next to the other engine wiring in
``_build_context``.
"""

from __future__ import annotations

import time
import webbrowser
from datetime import datetime
from importlib.resources import files as pkg_files
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.app import CONFIG_LOAD_ERRORS
from termapy.run_profile_hooks import register_run_profile_hooks
from termapy.builtins.commands.edit import (
    _make_edit_handler,
    _make_explore_handler,
    _make_list_handler,
)
from termapy.config import open_with_system
from termapy.defaults import cmd_prefix
from termapy.dialogs import ConfigEditor, ProtoEditor, ScriptEditor
from termapy.legacy import make_forwarder
from termapy.plugins import CapabilitySet, CmdResult
from termapy.scripting import parse_count_arg, parse_duration, select_lines

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _hook_help_open(app, ctx: "PluginContext | None", args: str) -> CmdResult:
    """Open a help topic in the local docs server."""

    html_dir = pkg_files("termapy").joinpath("html")
    topic = args.strip()
    if not topic:
        page = "index.html"
    else:
        topic = topic.replace(".md", "").replace(".html", "")
        page = f"{topic}.html"
    if not Path(str(html_dir.joinpath(page))).exists():
        msg = (
            f"Unknown topic: {topic!r}. "
            f"Available: {', '.join(app._HELP_TOPICS)}"
        )
        app._status(msg, "red")
        return CmdResult.fail(msg=msg)
    port = app._ensure_help_server()

    url = f"http://127.0.0.1:{port}/{page}"
    webbrowser.open(url)
    # Return the URL so scripts can capture / log which help page was opened.
    return CmdResult.ok(value=url)


def _hook_ss_svg(app, ctx, args: str) -> CmdResult:
    """Save a timestamped SVG screenshot of the terminal.

    Writes to ``<ss_dir>/<name>_<YYYYmmdd_HHMMSS>.svg`` and prints a
    green status line with the resolved path.  Bumps the SS button
    counter so the title-bar tooltip reflects the new file.

    Args:
        app: The SerialTerminal instance.
        ctx: PluginContext (unused; the hook signature requires it).
        args: Optional name stem; defaults to ``"screenshot"``.

    Returns:
        ``CmdResult.ok()`` -- the save itself is best-effort.
    """
    base = args.strip() or "screenshot"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = str((app.repl.ss_dir / f"{base}_{ts}.svg").resolve())
    app._on_main(app.save_screenshot, path)
    app.last_screenshot = path
    app._status(f"SVG screenshot saved: {path}", "green")
    app._on_main(app._sync_ss_button)
    return CmdResult.ok(value=path)


def _hook_ss_svg_quiet(app, ctx, args: str) -> CmdResult:
    """Save an SVG screenshot without a status message (scripting friendly).

    Used by ``/ss.svg.silent`` for scripts that want a screenshot
    without cluttering the output stream.  Args is the file stem
    (no timestamp suffix unlike ``ss.svg``); ``.svg`` is appended if
    not already present.

    Args:
        app: The SerialTerminal instance.
        ctx: PluginContext (unused).
        args: File stem; defaults to ``"screenshot"``.

    Returns:
        ``CmdResult.ok()``.
    """
    name = args.strip() or "screenshot"
    if not name.endswith(".svg"):
        name += ".svg"
    path = str((app.repl.ss_dir / name).resolve())
    app._on_main(app.save_screenshot, path)
    app.last_screenshot = path
    app._on_main(app._sync_ss_button)
    return CmdResult.ok(value=path)


def _hook_ss_txt(app, ctx, args: str) -> CmdResult:
    """Save a timestamped plain-text screenshot of the terminal scrollback.

    Renders ``app._get_screen_text()`` (the visible scrollback as
    text) to ``<ss_dir>/<name>_<YYYYmmdd_HHMMSS>.txt``.  An optional
    integer selects a slice: ``N>0`` saves the last N lines (most
    recent), ``N<0`` the first N; omit it for the whole buffer.  Print
    a green status line with the resolved path and the line count, and
    bump the SS button counter.

    Args:
        app: The SerialTerminal instance.
        ctx: PluginContext (unused).
        args: Optional name stem and/or line count; name defaults to
            ``"screenshot"``.

    Returns:
        ``CmdResult.ok(value=path)``, or ``CmdResult.fail`` on a bad count.
    """
    try:
        base, n = parse_count_arg(args, "screenshot")
    except ValueError as e:
        return CmdResult.fail(msg=str(e))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = str((app.repl.ss_dir / f"{base}_{ts}.txt").resolve())
    raw = str(app._on_main(app._get_screen_text) or "")
    text = "\n".join(select_lines(raw.splitlines(), n))
    Path(path).write_text(text, encoding="utf-8")
    app.last_screenshot = path
    saved = len(text.splitlines()) if text else 0
    app._status(f"Text screenshot saved ({saved} lines): {path}", "green")
    app._on_main(app._sync_ss_button)
    return CmdResult.ok(value=path)


def _hook_delay(app, ctx, args: str) -> CmdResult:
    """Pause script execution for a duration, with a TUI progress bar.

    Short delays (< 1s) block on ``time.sleep`` and print a single
    "done" status.  Longer delays mount a progress bar at the bottom
    of the terminal that updates every 100ms.

    Args:
        app: The SerialTerminal instance.
        ctx: PluginContext (unused).
        args: Duration string -- ``"500ms"``, ``"1.5s"``, ``"2m"``, etc.

    Returns:
        ``CmdResult.ok()`` on success, or
        ``CmdResult.fail(msg=...)`` if the duration string is invalid.
    """
    try:
        seconds = parse_duration(args)
    except ValueError as e:
        app._status(str(e), "red")
        return CmdResult.fail(msg=str(e))
    if seconds < 1:
        time.sleep(seconds)
        app._on_main(app._status, f"Delay {args} done.")
        return CmdResult.ok(value=str(seconds))
    app._run_progress_bar(seconds, args.strip())
    return CmdResult.ok(value=str(seconds))


def _hook_delay_quiet(app, ctx, args: str) -> CmdResult:
    """Wait silently - non-blocking timer, no output."""
    try:
        seconds = parse_duration(args)
    except ValueError as e:
        app._status(str(e), "red")
        return CmdResult.fail(msg=str(e))
    app._on_main(app.set_timer, seconds, lambda: None)
    return CmdResult.ok(value=str(seconds))


def _hook_line_no(app, ctx, args: str) -> CmdResult:
    """Toggle line numbers, or set explicitly with ``on``/``off``."""
    arg = args.strip().lower()
    if arg == "":
        # Bare invocation toggles -- matches the {on|off} optional
        # convention in CLAUDE.md.
        new_state = not app._show_line_numbers
    elif arg == "on":
        new_state = True
    elif arg == "off":
        new_state = False
    else:
        app._status(f"Invalid line_no: {arg}", "yellow")
        return CmdResult.fail(msg=f"Invalid line_no: {arg}")

    app._show_line_numbers = new_state
    label = "on" if new_state else "off"
    app._status(f"Line numbers {label.upper()}")
    return CmdResult.ok(value=label)


def _hook_edit_cfg(app) -> CmdResult:
    """Open the config editor modal."""
    app._on_main(
        app.push_screen,
        ConfigEditor(dict(app.cfg), app.config_path),
        callback=app._on_config_result,
    )
    return CmdResult.ok(value=Path(app.config_path) if app.config_path else "")


def _hook_edit_info(app) -> CmdResult:
    """Open the info report in the system viewer."""
    if not app.config_path:
        app.repl.write("No config loaded.", "red")
        return CmdResult.fail(msg="No config loaded.")
    stem = Path(app.config_path).stem
    path = Path(app.config_path).parent / f"{stem}.md"
    if path.exists():
        open_with_system(str(path))
        return CmdResult.ok(value=path)
    else:
        app.repl.write("No info report yet. Run /cfg.info first.", "red")
        return CmdResult.fail(msg="No info report yet. Run /cfg.info first.")


def _hook_edit(app, ctx, args: str) -> CmdResult:
    """Edit a project file using the same dialogs as the UI menus.

    Routes to ScriptEditor (.run) or ProtoEditor (.pro).

    Args:
        ctx: Plugin context (unused).
        args: Filename (scripts/proto path).
    """
    filename = args.strip()
    if not filename:
        app.repl.write("Usage: /edit <filename>", "red")
        return CmdResult.fail(msg="Usage: /edit <filename>")

    # Resolve prefixed or bare filename
    path = app._resolve_project_file(filename)
    if path is None:
        app.repl.write(f"File not found: {filename}", "red")
        return CmdResult.fail(msg=f"File not found: {filename}")

    ext = path.suffix.lower()
    if ext == ".run":
        app._on_main(
            app.push_screen,
            ScriptEditor(app.repl.scripts_dir, str(path)),
            callback=app._on_script_saved,
        )
    elif ext == ".pro":
        app._on_main(
            app.push_screen,
            ProtoEditor(app.repl.proto_dir, str(path)),
            callback=app._on_proto_saved,
        )
    return CmdResult.ok(value=path)


def _hook_edit_folder(app, ctx, args: str, folder: str, ext: str) -> CmdResult:
    """Edit a file from a specific folder using Textual modal."""
    name = args.strip()
    if not name:
        dir_map = {"run": app.repl.scripts_dir, "proto": app.repl.proto_dir}
        base = dir_map.get(folder)
        listed: list[str] = []
        if base and base.is_dir():
            files = sorted(base.glob(f"*{ext}"))
            if files:
                app.repl.write("  Available file(s):")
                for f in files:
                    app.repl.write(f"    {f.name}")
                    listed.append(f.name)
            else:
                app.repl.write("  (empty)")
        else:
            app.repl.write("  (no directory)")
        return CmdResult.ok(value="\n".join(listed))
    if not name.endswith(ext):
        name += ext
    dir_map = {"run": app.repl.scripts_dir, "proto": app.repl.proto_dir}
    base = dir_map.get(folder)
    if not base:
        return CmdResult.fail(msg=f"Unknown folder: {folder}")
    path = base / name
    if not path.exists():
        app.repl.write(f"File not found: {name}", "red")
        return CmdResult.fail(msg=f"File not found: {name}")
    if ext == ".run":
        app._on_main(
            app.push_screen,
            ScriptEditor(base, str(path)),
            callback=app._on_script_saved,
        )
    elif ext == ".pro":
        app._on_main(
            app.push_screen,
            ProtoEditor(base, str(path)),
            callback=app._on_proto_saved,
        )
    return CmdResult.ok(value=path)


def _hook_cfg_load(app, ctx, args: str) -> CmdResult:
    """Switch to a different config by name or path."""
    name = args.strip()
    if not name:
        app.repl.write("Usage: /cfg.load <name>", "red")
        return CmdResult.fail(msg="Usage: /cfg.load <name>")
    path = Path(name)
    # Try as a bare name: termapy_cfg/<name>/<name>.cfg
    if not path.exists():
        from termapy.config import cfg_path_for_name

        path = cfg_path_for_name(name)
    # Try appending .cfg
    if not path.exists() and not path.suffix:
        path = Path(str(path) + ".cfg")
    if not path.exists():
        app.repl.write(f"Config not found: {name}", "red")
        return CmdResult.fail(msg=f"Config not found: {name}")
    try:
        from termapy.config import load_config

        cfg = load_config(str(path))
    except CONFIG_LOAD_ERRORS as e:
        app.repl.write(f"Failed to load config: {e}", "red")
        return CmdResult.fail(msg=f"Failed to load config: {e}")
    app._on_main(app._switch_config, cfg, str(path))
    app._on_main(app._show_config_info, str(path))
    return CmdResult.ok(value=path)


def _hook_proto_load(app, ctx, args: str) -> CmdResult:
    """Run a protocol test script (delegates to /proto.run)."""
    prefix = cmd_prefix(app.cfg)
    app._dispatch_single(f"{prefix}proto.run {args}")
    # Dispatch is fire-and-forget here; the args carry the script
    # identifier so scripts can confirm what was requested.
    return CmdResult.ok(value=args.strip())


def register_tui_hooks(app) -> None:
    """Register TUI-specific commands as plugin hooks."""

    # Make bare /cfg, /run, /proto behave like clicking the matching
    # title-bar button.  Plugin handlers (cfg.py, proto.py) and the
    # /run hook check this callback when invoked with no args; CLI
    # leaves it None so they fall through to their CLI fallbacks.
    app.repl.ctx.engine.open_picker = app._open_picker
    app.repl.register_hook(
        "ss.svg",
        "{name}",
        "Save SVG screenshot. Name defaults to 'screenshot'.",
        lambda ctx, args: _hook_ss_svg(app, ctx, args),
        source="app",
        needs=CapabilitySet(screen_capture=True),
    )
    app.repl.register_hook(
        "ss.svg.silent",
        "{name}",
        "Save SVG screenshot silently (no status message).",
        lambda ctx, args: _hook_ss_svg_quiet(app, ctx, args),
        source="app",
        needs=CapabilitySet(screen_capture=True),
    )
    app.repl.register_hook(
        "ss.svg.quiet",
        "{name}",
        "Legacy alias for /ss.svg.silent.",
        make_forwarder("ss.svg.quiet", "ss.svg.silent"),
        source="app",
        needs=CapabilitySet(screen_capture=True),
        hidden=True,
    )
    app.repl.register_hook(
        "ss.txt",
        "{name} {N}",
        "Save text screenshot; N>0 last N lines, N<0 first N.",
        lambda ctx, args: _hook_ss_txt(app, ctx, args),
        source="app",
        needs=CapabilitySet(screen_capture=True),
    )
    app.repl.register_hook(
        "delay",
        "<duration>",
        "Wait for duration (e.g. 500ms, 1.5s).",
        lambda ctx, args: _hook_delay(app, ctx, args),
        source="app",
    )
    app.repl.register_hook(
        "delay.silent",
        "<duration>",
        "Wait silently (no output).",
        lambda ctx, args: _hook_delay_quiet(app, ctx, args),
        source="app",
    )
    app.repl.register_hook(
        "delay.quiet",
        "<duration>",
        "Legacy alias for /delay.silent.",
        make_forwarder("delay.quiet", "delay.silent"),
        source="app",
        hidden=True,
    )
    # /run and its sub_commands (.help, .legacy, .list, .dump, .show,
    # .explore) are owned by the run.py builtin -- TUI just needs to
    # add the picker-on-bare-/run behaviour via ctx.engine.open_picker,
    # which is wired further down in this function.  /run.profile.*
    # stays as hooks because the runner is host-specific (threaded in
    # TUI, synchronous everywhere else).
    register_run_profile_hooks(app)
    app.repl.register_hook(
        "demo",
        "",
        "Switch to the built-in demo device.",
        lambda ctx, args: app._start_demo(args),
        source="app",
        needs=CapabilitySet(interactive=True),
    )
    app.repl.register_hook(
        "demo.force",
        "",
        "Switch to demo device, overwriting existing config.",
        lambda ctx, args: app._start_demo("--force"),
        source="app",
        needs=CapabilitySet(interactive=True),
    )
    app.repl.register_hook(
        "cli",
        "",
        "Switch to CLI mode.",
        lambda ctx, args: app._switch_to_cli(),
        source="app",
        needs=CapabilitySet(interactive=True),
    )
    app.repl.register_hook(
        "tui",
        "",
        "Already in TUI mode.",
        # SPECIAL CASE: this hook fires when the user types /tui while
        # already in TUI mode -- no state change, no scriptable output.
        lambda ctx, args: CmdResult.ok(value=""),
        source="app",
        needs=CapabilitySet(interactive=True),
    )
    app.repl.register_hook(
        "term.line_no",
        "{on|off}",
        "Toggle line numbers in serial output (TUI only).",
        lambda ctx, args: _hook_line_no(app, ctx, args),
        source="app",
        needs=CapabilitySet(tui_mode=True),
    )
    # /edit - TUI overrides root (Textual modals for .run/.pro)
    # This wipes all edit.* children from the plugin, so we must
    # re-register every subcommand the TUI wants to expose.
    app.repl.register_hook(
        "edit",
        "<filename>",
        "Edit a project file (scripts/proto path).",
        lambda ctx, args: _hook_edit(app, ctx, args),
        source="app",
        needs=CapabilitySet(gui_apps=True),
    )
    app.repl.register_hook(
        "edit.cfg",
        "",
        "Edit the current config file.",
        lambda ctx, args: app._hook_edit_cfg(),
        source="app",
        needs=CapabilitySet(gui_apps=True),
    )
    app.repl.register_hook(
        "log.delete",
        "",
        "Delete the session log file.",
        lambda ctx, args: app._tui_hook_log_delete(),
        source="app",
    )
    # /log.clear is a hidden legacy alias -- "clear" should mean
    # "empty visible state," and the canonical "delete the file"
    # verb is /log.delete.  Forwarder pattern matches /port.open
    # -> /port.connect and the rest of the v0.64 renames.
    app.repl.register_hook(
        "log.clear",
        "",
        "Legacy alias for /log.delete.",
        make_forwarder("log.clear", "log.delete"),
        source="app",
        hidden=True,
    )
    # /log.show, /log.dump, /log.fingerprint live as builtin plugins
    # in termapy/builtins/commands/log_*.py so MCP gets them too.
    # /edit.log -- hidden legacy forwarder to /log.show.
    app.repl.register_hook(
        "edit.log",
        "",
        "Open the session log in the system viewer.",
        make_forwarder("edit.log", "log.show"),
        source="app",
        needs=CapabilitySet(gui_apps=True),
    )
    app.repl._plugins["edit.log"].hidden = True
    app.repl.register_hook(
        "edit.info",
        "",
        "Open the info report in the system viewer.",
        lambda ctx, args: app._hook_edit_info(),
        source="app",
        needs=CapabilitySet(gui_apps=True),
    )
    # Re-register folder subcommands (wiped by /edit override)

    for folder, get_dir, ext, pat in (
        ("run", lambda ctx: ctx.fs.scripts_dir, ".run", "*.run"),
        ("proto", lambda ctx: ctx.fs.proto_dir, ".pro", "*.pro"),
        (
            "plugin",
            lambda ctx: Path(ctx.config_path).parent / "plugin"
            if ctx.config_path
            else Path("."),
            ".py",
            "*.py",
        ),
    ):
        # TUI uses Textual modals for run and proto edit
        if folder in ("run", "proto"):
            app.repl.register_hook(
                f"edit.{folder}",
                "{{filename}}",
                f"Edit a {ext} file.",
                (
                    lambda f=folder, e=ext: lambda ctx,
                    args: app._hook_edit_folder(ctx, args, f, e)
                )(),
                source="app",
                needs=CapabilitySet(gui_apps=True),
            )
        else:
            app.repl.register_hook(
                f"edit.{folder}",
                "{{filename}}",
                f"Open a {ext} file in the system editor.",
                _make_edit_handler(get_dir, ext, pat),
                source="app",
                needs=CapabilitySet(gui_apps=True),
            )
        # /edit.<folder>.list is the only branch where listing is useful
        # to the LLM -- it's a discovery tool, not an editor invocation.
        app.repl.register_hook(
            f"edit.{folder}.list",
            "",
            f"List {ext} files.",
            _make_list_handler(get_dir, pat),
            source="app",
        )
        app.repl.register_hook(
            f"edit.{folder}.explore",
            "",
            f"Open {folder}/ in file explorer.",
            _make_explore_handler(get_dir),
            source="app",
            needs=CapabilitySet(gui_apps=True),
        )
    app.repl.register_hook(
        "cfg.load",
        "<name>",
        "Switch to a different config by name.",
        lambda ctx, args: _hook_cfg_load(app, ctx, args),
        source="app",
    )
    # /run.load was a redundant alias for bare /run; dropped with the
    # /run-as-builtin migration.  Bare /run takes a filename.
    app.repl.register_hook(
        "proto.load",
        "<filename>",
        "Run a protocol test script (same as /proto.run).",
        lambda ctx, args: _hook_proto_load(app, ctx, args),
        source="app",
    )
    app.repl.register_hook(
        "raw",
        "<text>",
        "Send text to serial with no variable expansion or transforms.",
        lambda ctx, args: app._tui_hook_raw(args),
        source="app",
    )
    app.repl.register_hook(
        "help.open",
        "{topic}",
        "Open help file in system viewer.",
        lambda ctx, args: _hook_help_open(app, ctx, args),
        source="app",
        needs=CapabilitySet(gui_apps=True),
    )

