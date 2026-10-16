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
isn't a REPL hook but an ``internal.confirm_save_cfg`` callback; it
stays on ``SerialTerminal`` next to the other internal-handle wiring
in ``_build_context``.
"""

from __future__ import annotations

import time
import webbrowser
from importlib.resources import files as pkg_files
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.app import CONFIG_LOAD_ERRORS
from termapy.builtins.commands.edit import (
    make_edit_handler,
    make_explore_handler,
    make_list_handler,
)
from termapy.config import open_with_system
from termapy.config_resolve import resolve_config
from termapy.dialogs import ConfigEditor, ProtoEditor, ScriptEditor
from termapy.folders import default_file_name, ensure_folder
from termapy.help_dynamic import compose, folder_line
from termapy.legacy import make_forwarder
from termapy.plugins import CapabilitySet, CmdResult, UsageError
from termapy.run_profile_hooks import register_run_profile_hooks
from termapy.scripting import (
    SETTING_QUERY,
    SETTING_TOGGLE,
    filename_timestamp,
    parse_bool_setting,
    parse_count_arg,
    parse_duration,
    select_lines,
)

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
        return CmdResult.fail(
            msg=f"Unknown topic: {topic!r}. Available: {', '.join(app._HELP_TOPICS)}"
        )
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
    ts = filename_timestamp()
    path = str((ensure_folder(app.repl.ss_dir) / f"{base}_{ts}.svg").resolve())
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
    path = str((ensure_folder(app.repl.ss_dir) / name).resolve())
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
        raise UsageError(str(e)) from None
    ts = filename_timestamp()
    path = str((ensure_folder(app.repl.ss_dir) / f"{base}_{ts}.txt").resolve())
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
    of the terminal that updates every 250ms.

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
        return CmdResult.fail(msg=str(e))
    if seconds < 1:
        time.sleep(seconds)
        app._on_main(app._status, f"Delay {args} done.")
        return CmdResult.ok(value=str(seconds))
    # Free function since the capture-view extraction; it is not an app
    # method (lazy import: capture_view is UI-layer, same as app.py does).
    from termapy.capture_view import _run_progress_bar

    _run_progress_bar(app, seconds, args.strip())
    return CmdResult.ok(value=str(seconds))


def _hook_delay_quiet(app, ctx, args: str) -> CmdResult:
    """Wait silently - non-blocking timer, no output."""
    try:
        seconds = parse_duration(args)
    except ValueError as e:
        return CmdResult.fail(msg=str(e))
    app._on_main(app.set_timer, seconds, lambda: None)
    return CmdResult.ok(value=str(seconds))


def _hook_line_no(app, ctx, args: str) -> CmdResult:
    """Query, set, or toggle line numbers.

    Bare invocation QUERIES (reports state, mutates nothing); ``on``/``off``
    sets; the explicit verb ``toggle`` flips; anything else errors.  A
    setting command never mutates on a bare invocation -- see CLAUDE.md.
    """
    try:
        action = parse_bool_setting(args)
    except ValueError as e:
        return CmdResult.fail(msg=str(e))
    if action is SETTING_QUERY:
        new_state = app._show_line_numbers
    elif action is SETTING_TOGGLE:
        new_state = not app._show_line_numbers
    else:
        new_state = action

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
    """Open the info report in the system viewer.

    A failure is returned, not also written: the dispatcher prints it.
    """
    if not app.config_path:
        return CmdResult.fail(msg="No config loaded.")
    stem = Path(app.config_path).stem
    path = Path(app.config_path).parent / f"{stem}.md"
    if path.exists():
        open_with_system(str(path))
        return CmdResult.ok(value=path)
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
        raise UsageError()

    # Resolve prefixed or bare filename
    path = app._resolve_project_file(filename)
    if path is None:
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
    """Edit a file from a specific folder using Textual modal.

    A bare call never arrives: the hook declares ``picker=<folder>``, so
    the dispatcher opens the folder's picker (whose Edit button reaches
    the same editor) before this runs.
    """
    name = args.strip()
    if not name:
        raise UsageError()
    dir_map = {"run": app.repl.scripts_dir, "proto": app.repl.proto_dir}
    base = dir_map.get(folder)
    if not base:
        return CmdResult.fail(msg=f"Unknown folder: {folder}")
    # The extension is optional (registry ``file-name-default-ext``).
    path = default_file_name(base / name, ext)
    if not path.is_file():
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
        raise UsageError()
    # The CLI and MCP's chain (TerminalHost._load_config), not a copy of it.
    found = resolve_config(name)
    if found is None:
        return CmdResult.fail(msg=f"Config not found: {name}")
    path = Path(found)
    try:
        from termapy.config import load_config

        cfg = load_config(str(path))
    except CONFIG_LOAD_ERRORS as e:
        return CmdResult.fail(msg=f"Failed to load config: {e}")
    app._on_main(app._switch_config, cfg, str(path))
    app._on_main(app._show_config_info, str(path))
    return CmdResult.ok(value=path)


def register_tui_hooks(app) -> None:
    """Register TUI-specific commands as plugin hooks."""

    # Make a bare /cfg, /run, /proto, /port (and every other command that
    # declares picker=) behave like clicking the matching title-bar
    # button: the dispatcher calls this for them.  CLI and MCP leave it
    # None, so their handlers' own bare fallbacks run.
    app.repl.ctx.internal.open_picker = app._open_picker
    app.repl.register_hook(
        "ss.svg",
        "{name}",
        "Save SVG screenshot. Name defaults to 'screenshot'.",
        lambda ctx, args: _hook_ss_svg(app, ctx, args),
        source="app",
        needs=CapabilitySet.SCREEN_CAPTURE,
    )
    app.repl.register_hook(
        "ss.svg.silent",
        "{name}",
        "Save SVG screenshot silently (no status message).",
        lambda ctx, args: _hook_ss_svg_quiet(app, ctx, args),
        source="app",
        needs=CapabilitySet.SCREEN_CAPTURE,
    )
    app.repl.register_hook(
        "ss.svg.quiet",
        "{name}",
        "Legacy alias for /ss.svg.silent.",
        make_forwarder("ss.svg.quiet", "ss.svg.silent"),
        source="app",
        needs=CapabilitySet.SCREEN_CAPTURE,
        hidden=True,
    )
    app.repl.register_hook(
        "ss.txt",
        "{name} {N}",
        "Save text screenshot; N>0 last N lines, N<0 first N.",
        lambda ctx, args: _hook_ss_txt(app, ctx, args),
        source="app",
        needs=CapabilitySet.SCREEN_CAPTURE,
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
    # .explore) are owned by the run.py builtin -- its picker="run" opens
    # the Run picker through ctx.internal.open_picker, wired at the top
    # of this function.  /run.profile.*
    # stays as hooks because the runner is host-specific (threaded in
    # TUI, synchronous everywhere else).
    register_run_profile_hooks(app)
    app.repl.register_hook(
        "demo",
        "",
        "Switch to the built-in demo device.",
        lambda ctx, args: app._start_demo(args),
        source="app",
        needs=CapabilitySet.INTERACTIVE,
    )
    app.repl.register_hook(
        "demo.force",
        "",
        "Switch to demo device, overwriting existing config.",
        lambda ctx, args: app._start_demo("--force"),
        source="app",
        needs=CapabilitySet.INTERACTIVE,
    )
    app.repl.register_hook(
        "cli",
        "",
        "Switch to CLI mode.",
        lambda ctx, args: app._switch_to_cli(),
        source="app",
        needs=CapabilitySet.INTERACTIVE,
    )
    app.repl.register_hook(
        "vt100",
        "",
        "Switch to VT100 passthrough on the current device (Ctrl-] returns).",
        lambda ctx, args: app._switch_to_vt100(),
        source="app",
        needs=CapabilitySet.INTERACTIVE,
    )
    app.repl.register_hook(
        "demo.vt100",
        "",
        "Switch to the VT100 widget-tour demo device (Ctrl-] returns).",
        lambda ctx, args: app._switch_to_vt100(demo=True),
        source="app",
        needs=CapabilitySet.INTERACTIVE,
    )
    app.repl.register_hook(
        "tui",
        "",
        "Already in TUI mode.",
        # SPECIAL CASE: this hook fires when the user types /tui while
        # already in TUI mode -- no state change, no scriptable output.
        lambda ctx, args: CmdResult.ok(value=""),
        source="app",
        needs=CapabilitySet.INTERACTIVE,
    )
    app.repl.register_hook(
        "term.line_no",
        "{on|off}",
        "Toggle line numbers in serial output (TUI only).",
        lambda ctx, args: _hook_line_no(app, ctx, args),
        source="app",
        needs=CapabilitySet.TUI_MODE,
    )
    # /edit - TUI overrides root (Textual modals for .run/.pro)
    # This wipes all edit.* children from the plugin, so we must
    # re-register every subcommand the TUI wants to expose.  The hooks
    # that open termapy's own editors need the TUI, not a desktop: they
    # work over SSH, where GUI_APPS is false.  Only the ones that hand a
    # file to the system viewer or file manager need GUI_APPS.
    app.repl.register_hook(
        "edit",
        "<filename>",
        "Edit a project file (scripts/proto path).",
        lambda ctx, args: _hook_edit(app, ctx, args),
        source="app",
        needs=CapabilitySet.TUI_MODE,
    )
    for name in ("edit.cfg", "cfg.edit"):
        app.repl.register_hook(
            name,
            "",
            "Edit the current config file.",
            lambda ctx, args: _hook_edit_cfg(app),
            source="app",
            long_help=(
                "Opens the loaded config in termapy's config editor, as F10 "
                "does.  /cfg.edit and /edit.cfg are the same command."
            ),
            needs=CapabilitySet.TUI_MODE,
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
        needs=CapabilitySet.GUI_APPS,
    )
    app.repl._plugins["edit.log"].hidden = True
    app.repl.register_hook(
        "edit.info",
        "",
        "Open the info report in the system viewer.",
        lambda ctx, args: _hook_edit_info(app),
        source="app",
        needs=CapabilitySet.GUI_APPS,
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
        # TUI uses Textual modals for run and proto edit, under both
        # spellings: /edit.run and its noun-first twin /run.edit.
        if folder in ("run", "proto"):
            text = (
                f"With a name, opens that existing file from {folder}/ in "
                f"termapy's editor (the {ext} is optional); a missing name "
                f"fails.  In the {folder.capitalize()} picker, Edit opens the "
                f"highlighted file.  /{folder}.edit and /edit.{folder} are "
                f"the same command."
            )
            for name in (f"edit.{folder}", f"{folder}.edit"):
                app.repl.register_hook(
                    name,
                    "{filename}",
                    f"Edit a {ext} file in termapy's editor.",
                    (
                        lambda f=folder, e=ext: lambda ctx,
                        args: _hook_edit_folder(app, ctx, args, f, e)
                    )(),
                    source="app",
                    long_help=(
                        lambda f=folder, t=text: lambda ctx:
                        compose(folder_line(ctx, f, noun="script"), t)
                    )(),
                    needs=CapabilitySet.TUI_MODE,
                    files=folder,
                    picker=folder,
                )
        else:
            # /plugin.edit is not wiped by the /edit override; the builtin
            # twin already runs this same make_edit_handler.
            app.repl.register_hook(
                f"edit.{folder}",
                "{filename}",
                f"Open a {ext} file in the system editor.",
                make_edit_handler(folder, get_dir, ext),
                source="app",
                needs=CapabilitySet.GUI_APPS,
                files=folder,
            )
        # /edit.<folder>.list is the only branch where listing is useful
        # to the LLM -- it's a discovery tool, not an editor invocation.
        app.repl.register_hook(
            f"edit.{folder}.list",
            "",
            f"List {ext} files.",
            make_list_handler(folder),
            source="app",
        )
        app.repl.register_hook(
            f"edit.{folder}.explore",
            "",
            f"Open {folder}/ in file explorer.",
            make_explore_handler(get_dir),
            source="app",
            needs=CapabilitySet.GUI_APPS,
        )
    app.repl.register_hook(
        "cfg.load",
        "<name>",
        "Switch to a different config by name.",
        lambda ctx, args: _hook_cfg_load(app, ctx, args),
        source="app",
    )
    app.repl.register_hook(
        "help.open",
        "{topic}",
        "Open help file in system viewer.",
        lambda ctx, args: _hook_help_open(app, ctx, args),
        source="app",
        needs=CapabilitySet.GUI_APPS,
    )

