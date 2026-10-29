"""TUI-specific REPL hooks registered against the app's ReplEngine.

These hook handlers and the registration function previously lived as
methods on ``SerialTerminal`` in ``app.py``.  Each handler now takes
the app as its first argument and reaches widget / engine / cfg
state via ``app.X``.  Hooks that need TUI capabilities (modals,
screen capture, GUI editors) still only run in TUI mode -- the
``CapabilitySet`` declarations on each ``register_hook`` enforce
that.

Pattern: each ``_hook_X`` is a free function ``(app, ctx, args)``.
``register_tui_hooks(app)`` binds each one with ``_bind(app, _hook_X)``,
a ``(ctx, args)`` handler that keeps the hook's docstring, so
``/help.dev`` shows it (registry ``handler-docstring``); a bare lambda
has none.

The cfg-confirm callback (``_hook_cfg_confirm``) is NOT here -- it
isn't a REPL hook but an ``internal.confirm_save_cfg`` callback; it
stays on ``SerialTerminal`` next to the other internal-handle wiring
in ``_build_context``.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.folders import ensure_folder
from termapy.legacy import make_forwarder
from termapy.plugins import CapabilitySet, CmdResult, UsageError
from termapy.scripting import (
    filename_timestamp,
    parse_count_arg,
    select_lines,
)

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _bind(app, hook: Callable) -> Callable:
    """Bind ``hook(app, ctx, args)`` to ``app`` as a ``(ctx, args)`` handler that keeps its docstring.

    A bare lambda has no ``__doc__``, so ``/help.dev`` and developer
    search answered "(no docstring)" for every TUI command while the
    code was documented (registry ``handler-docstring``).
    """

    @functools.wraps(hook)
    def handler(ctx: PluginContext, args: str) -> CmdResult:
        return hook(app, ctx, args)

    return handler


# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).


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


def register_tui_hooks(app) -> None:
    """Register TUI-specific commands as plugin hooks."""

    # Make a bare /cfg, /run, /proto, /port (and every other command that
    # declares picker=) behave like clicking the matching title-bar
    # button: the dispatcher calls this for them.  CLI and MCP leave it
    # None, so their handlers' own bare fallbacks run.
    app.repl.ctx.internal.open_picker = app._open_picker

    # Named, documented handlers: /help.dev prints these docstrings
    # (registry handler-docstring); a lambda would have none.
    def _switch_to_cli(ctx: PluginContext, args: str) -> CmdResult:
        """Leave the TUI for the CLI on the same config and port."""
        return app._switch_to_cli()

    def _switch_to_vt100(ctx: PluginContext, args: str) -> CmdResult:
        """Hand the device to the VT100 passthrough view; Ctrl-] returns."""
        return app._switch_to_vt100()

    def _switch_to_vt100_demo(ctx: PluginContext, args: str) -> CmdResult:
        """Open the VT100 widget-tour demo device; Ctrl-] returns."""
        return app._switch_to_vt100(demo=True)

    def _already_tui(ctx: PluginContext, args: str) -> CmdResult:
        """Already in TUI mode: a no-op, so a script written for the CLI runs unchanged."""
        return CmdResult.ok(value="")

    app.repl.register_hook(
        "ss.svg",
        "{name}",
        "Save SVG screenshot. Name defaults to 'screenshot'.",
        _bind(app, _hook_ss_svg),
        source="app",
        needs=CapabilitySet.SCREEN_CAPTURE,
    )
    app.repl.register_hook(
        "ss.svg.silent",
        "{name}",
        "Save SVG screenshot silently (no status message).",
        _bind(app, _hook_ss_svg_quiet),
        source="app",
        needs=CapabilitySet.SCREEN_CAPTURE,
    )
    app.repl.register_hook(
        "ss.svg.quiet",
        "{name}",
        "Legacy alias for {prefix}ss.svg.silent.",
        make_forwarder("ss.svg.quiet", "ss.svg.silent"),
        source="app",
        needs=CapabilitySet.SCREEN_CAPTURE,
        hidden=True,
    )
    app.repl.register_hook(
        "ss.txt",
        "{name} {N}",
        "Save text screenshot; N>0 last N lines, N<0 first N.",
        _bind(app, _hook_ss_txt),
        source="app",
        needs=CapabilitySet.SCREEN_CAPTURE,
    )
    # /run and its sub_commands (.help, .legacy, .list, .dump, .show,
    # .explore, .profile) are owned by the run.py builtin -- its
    # picker="run" opens the Run picker through ctx.internal.open_picker,
    # wired at the top of this function.
    app.repl.register_hook(
        "cli",
        "",
        "Switch to CLI mode.",
        _switch_to_cli,
        source="app",
        group="terminal",
        needs=CapabilitySet.INTERACTIVE,
    )
    app.repl.register_hook(
        "vt100",
        "",
        "Switch to VT100 passthrough on the current device (Ctrl-] returns).",
        _switch_to_vt100,
        source="app",
        group="terminal",
        # Only the TUI offers it: the round trip returns here on Ctrl-].
        needs=CapabilitySet.TUI_MODE,
    )
    app.repl.register_hook(
        "demo.vt100",
        "",
        "Switch to the VT100 widget-tour demo device (Ctrl-] returns).",
        _switch_to_vt100_demo,
        source="app",
        needs=CapabilitySet.TUI_MODE,
    )
    app.repl.register_hook(
        "tui",
        "",
        "Already in TUI mode.",
        # SPECIAL CASE: this hook fires when the user types /tui while
        # already in TUI mode -- no state change, no scriptable output.
        _already_tui,
        source="app",
        group="terminal",
        needs=CapabilitySet.INTERACTIVE,
    )
    app.repl.register_hook(
        "help.open",
        "{topic}",
        "Open help file in system viewer.",
        app._hook_help_open,  # TerminalHost's: the CLI's handler too
        source="app",
        needs=CapabilitySet.GUI_APPS,
    )
