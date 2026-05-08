"""Plugin system for termapy - discovery, loading, and context API.

Plugins are .py files that export a ``COMMAND`` instance describing the
command hierarchy::

    from termapy.plugins import Command

    def _handler(ctx, args):
        ctx.write("Hello from plugin!")

    COMMAND = Command(
        name="mycommand",
        args="{arg1}",
        help="What this command does.",
        handler=_handler,
    )

Subcommands are declared with ``sub_commands``::

    COMMAND = Command(
        name="tool",
        help="A tool with subcommands.",
        sub_commands={
            "run": Command(args="<file>", help="Run a file.", handler=_run),
            "status": Command(help="Show status.", handler=_status),
        },
    )

Users invoke subcommands with dot notation: ``/tool.run myfile``.

Input transforms are declared with ``TRANSFORM``::

    TRANSFORM = Transform(
        name="vars",
        help="Expand $variables in serial commands.",
        serial=lambda s: expand_vars(s),
    )

A file may export both ``COMMAND`` and ``TRANSFORM``.

The PluginContext provides a stable API for plugins to interact with
the terminal, serial port, config, and filesystem without touching
Textual or serial internals.

Load order: built-ins -> global plugins -> per-config plugins.
Later plugins can override earlier ones by using the same name.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field, fields
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, ClassVar, Generator, Union

from termapy.defaults import DEFAULT_CMD_PREFIX


# Type alias for the ``long_help`` field on Command and PluginInfo. A plugin
# can supply either a static string or a callable that receives the live
# PluginContext and returns a string. Callables let a command's DESCRIPTION
# section reflect runtime state (loaded files, current connection, cached
# counts) without having to wire up a custom render path.
#
# Uses the quoted string "PluginContext" because that class is defined later
# in this same module.
LongHelp = Union[str, Callable[["PluginContext"], str]]


# ─────────────────────────────────────────────────────────────────────────────
# Output levels
# ─────────────────────────────────────────────────────────────────────────────
#
# A single dial controls how loud commands are.  Four monotonic levels
# stratify the three output channels (result, output, status).  Set the
# default for the session with ``/term.output <level>``; override for a
# single call with ``cmd --<level>`` or ``cmd.<level>``.

#: Canonical level names, ordered from quietest to loudest.
OUTPUT_LEVELS: tuple[str, ...] = ("silent", "quiet", "normal", "verbose")

#: Default level when nothing has been set explicitly.
DEFAULT_OUTPUT_LEVEL = "normal"

_OUTPUT_LEVEL_RANK: dict[str, int] = {
    name: rank for rank, name in enumerate(OUTPUT_LEVELS)
}

# Channel→minimum-rank mapping.  A channel writes only when the active
# level's rank is at least this.
_RESULT_MIN_RANK = _OUTPUT_LEVEL_RANK["quiet"]    # quiet, normal, verbose
_OUTPUT_MIN_RANK = _OUTPUT_LEVEL_RANK["normal"]   # normal, verbose
_STATUS_MIN_RANK = _OUTPUT_LEVEL_RANK["verbose"]  # verbose only

#: Per-call flag tokens that override the level for one dispatch.  Stripped
#: from args before per-command flag parsing in ``ReplEngine.dispatch``.
LEVEL_FLAGS: dict[str, str] = {f"--{name}": name for name in OUTPUT_LEVELS}


def parse_output_level(s: str) -> str | None:
    """Return the canonical level name for ``s``, or None if unknown."""
    s = s.strip().lower()
    return s if s in _OUTPUT_LEVEL_RANK else None


# ─────────────────────────────────────────────────────────────────────────────
# Shared key/value rendering for info-style commands
# ─────────────────────────────────────────────────────────────────────────────
#
# Every command that emits a "label: value" table -- /term.info, /term.usb_db,
# /port.info, /proto.crc.info, etc. -- routes through ``format_kv_lines()``
# below so they render with one consistent style:
#
#   - Two-space indent.
#   - Cyan label, padded to the widest in the set.
#   - Colon + single space between label and value.
#   - Optional per-row color baked into the value via Rich markup.
#
# Adding a new info command?  Build ``[(label, value), ...]``, call
# ``format_kv_lines()``, write each line via ``ctx.write_markup()``.  Don't
# roll your own padding -- consistency across info commands matters.


def format_kv_lines(
    rows: "list[tuple[str, str]]",
    indent: str = "  ",
    label_color: str = "cyan",
) -> "list[str]":
    """Render a list of ``(label, value)`` pairs as cyan-key markup lines.

    Pads labels to the widest in the set and adds a colon-space
    separator between label and value.  Returns a list of markup
    strings ready to pass to ``ctx.write_markup()``.

    Per-row coloring of the *value* is the caller's responsibility:
    embed Rich markup directly in the value string (e.g.
    ``"[yellow]warning[/]"``) and it'll render on top of the cyan
    label.  The label itself is always rendered in ``label_color``
    (default cyan) for consistency.

    Args:
        rows: Sequence of ``(label, value)`` tuples.
        indent: String prefix on each line (default two spaces).
        label_color: Rich color name for the label (default
            ``"cyan"``).

    Returns:
        A list of pre-formatted markup strings, one per row.  Empty
        list if ``rows`` is empty.
    """
    if not rows:
        return []
    width = max(len(label) for label, _ in rows)
    return [
        f"{indent}[{label_color}]{label:<{width}}[/]: {value}"
        for label, value in rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Capability model
# ─────────────────────────────────────────────────────────────────────────────
#
# Every command declares *what the environment must provide* for its handler
# to run.  That declaration is a ``CapabilitySet`` on the ``Command`` (and
# carried through to the registered ``PluginInfo``).  Every execution
# environment (REPL prompt, script runner, CLI, TUI) publishes the
# capabilities it provides on ``PluginContext.capabilities``.
#
# Dispatch is a simple check: if the command's ``needs`` aren't satisfied
# by the context's ``capabilities``, the command fails with a clear message
# naming what's missing.  No special cases; commands that can run anywhere
# declare an empty ``needs`` (the default).
#
# **This is a fundamental aspect of every command.**  Handlers may not
# silently no-op when a capability is missing -- they must either declare
# the need (so dispatch gates them) or not use the capability at all.
#
# Why a closed dataclass of booleans rather than a free-form set of strings?
#   - Typos fail at import time (``needs=CapabilitySet(bloc_until=True)``
#     is an immediate error), not silently at runtime.
#   - The fields below are the single source of truth for the vocabulary.
#     Grep-friendly: every consumer reads ``caps.block_until`` by name.
#   - Extending is cheap: add a field with a default of ``False``, and
#     every environment and command stays source-compatible.
#
# Add a capability by:
#   1. Add a new field here with a ``bool = False`` default and a comment
#      explaining *what* it means and *where* it's provided.
#   2. The environments that provide it set the field to True when they
#      build ``ctx.capabilities`` (typically in ``app.py`` / ``cli.py`` /
#      the script runner in ``repl.py``).
#   3. Commands that require it set the field in their ``Command.needs``.


@dataclass(frozen=True)
class CapabilitySet:
    """Declarative set of environment capabilities.

    Serves two roles with the same shape:

      - ``Command.needs`` -- what a handler requires to run.
      - ``PluginContext.capabilities`` -- what the environment provides.

    A command is allowed to run when::

        command.needs.satisfied_by(ctx.capabilities)

    Fields come in two groups with **different defaults**:

      - **Baseline** (default ``True``): things every execution environment
        that termapy ships is guaranteed to provide (terminal output,
        serial I/O, nested dispatch, config access).  A command declares
        these by leaving them alone; they show up in ``CapabilitySet()``
        automatically.  A hypothetical restricted environment (sand-boxed
        runner, web preview) can flip one ``False`` and dispatch will
        gate every command that depends on it, without any command
        author needing to change declarations.

      - **Restrictive** (default ``False``): things only some environments
        provide (blocking, UI dialogs, screen capture).  A command
        declares a need by setting the field ``True``; an environment
        advertises availability the same way.

    **Adding a capability** is adding a field below with a ``# What:`` /
    ``# Where:`` comment block.  Choose the default based on whether
    every termapy environment can provide it or not.
    """

    # ── Baseline (default True) ──────────────────────────────────────────
    # Every termapy environment provides these.  Listed explicitly so that
    # (a) readers can see the full contract, and (b) a restricted
    # environment can selectively opt out.

    # What:  ``ctx.write`` / ``ctx.write_markup`` write to a visible sink
    #        (terminal, log, or captured buffer).
    # Where: CLI, TUI, script runner, test harness.
    terminal_output: bool = True

    # What:  ``ctx.serial_write`` / ``ctx.serial_send`` / ``ctx.serial_*``
    #        can talk to the serial engine.  Note: being *connected* to a
    #        port is the ``serial_connected`` capability below -- this one
    #        only says the API is wired up.
    # Where: CLI, TUI, script runner.
    serial_io: bool = True

    # What:  ``ctx.dispatch(cmd)`` routes a command through the full
    #        dispatch pipeline (directives, transforms, REPL/serial).
    # Where: Every environment; scripts nest dispatch heavily.
    dispatch: bool = True

    # What:  ``ctx.cfg``, ``ctx.config_path``, and the folder paths
    #        (``scripts_dir``, ``proto_dir``, ...) are populated and
    #        readable.
    # Where: Every environment.  Tests sometimes use a synthetic config.
    config_read: bool = True

    # ── Restrictive (default False) ──────────────────────────────────────
    # Only some environments provide these.  Commands opt in by setting
    # ``needs=CapabilitySet(<name>=True)``; environments opt in by
    # setting the same field in their ``ctx.capabilities``.

    # What:  Handler can block the calling thread waiting for serial input
    #        or a user response (e.g. /expect, /confirm).
    # Where: Script runner only.  Blocking at the REPL would freeze the
    #        TUI's event loop; blocking in the CLI event path would hang
    #        stdin echo.  The script runner already executes on a
    #        background worker thread that it's safe to block.
    block_until: bool = False

    # What:  ``ctx.confirm(message)`` can show a real Yes/Cancel dialog
    #        and return the user's answer synchronously.
    # Where: TUI + script runner.  Implies block_until (the handler stops
    #        until the user answers).  CLI has no dialog today, though a
    #        text-mode prompt could provide this later.
    confirm_dialog: bool = False

    # What:  ``ctx.notify(text)`` shows a transient toast-style message
    #        that does not pollute the main output stream.
    # Where: TUI only.
    ui_notify: bool = False

    # What:  ``ctx.status_bar(text, timeout)`` updates the bottom-of-screen
    #        status line.  No-op elsewhere.
    # Where: TUI only.
    status_bar: bool = False

    # What:  Can capture the rendered screen (``save_screenshot``,
    #        ``get_screen_text``).  Requires a graphical render surface.
    # Where: TUI only.  CLI renders to a plain terminal; there is no
    #        serialized screen state to capture.
    screen_capture: bool = False

    # What:  Running inside the TUI (Textual) rather than the CLI.
    #        Commands that tweak TUI-specific display settings (line
    #        numbers, scrollback rendering, modal dialogs) declare this.
    # Where: TUI only.  Distinct from ``screen_capture`` -- that's about
    #        *reading* the render surface; this is about *using* TUI-only
    #        features at runtime.
    tui_mode: bool = False

    # What:  A serial port is currently open and transmitting.
    # Where: Dynamic -- evaluated per dispatch by checking
    #        ``ctx.is_connected()``.  Any environment can publish this,
    #        but only when a port is actually open.  Commands that send
    #        bytes (/proto.send, /xmodem.send, ...) declare this need
    #        so dispatch gives a clear "not connected" error instead of
    #        each handler re-implementing the check.
    serial_connected: bool = False

    # What:  An interactive session -- a human at a terminal with
    #        persistent scrollback, modal dialog support, or in-band UI
    #        chrome (mode switch, screen clear, line numbers).  Also
    #        the home of legacy aliases retained for human typing
    #        convenience (``/echo`` -> ``/term.echo``).
    # Where: TUI, CLI (whether running locally or over SSH).  Not MCP --
    #        an LLM client has no interactive session.
    interactive: bool = False

    # What:  Host can launch external desktop apps the user must visually
    #        see -- system editor, file viewer, browser, file explorer.
    #        Distinct from ``interactive`` because an SSH user IS
    #        interactive but has no local display, so calls like
    #        ``webbrowser.open()`` succeed silently while opening on
    #        the remote machine the user can't see.
    # Where: TUI / CLI when running locally on a graphical desktop.
    #        Detected at host startup via env vars; users can override
    #        with ``TERMAPY_GUI=1`` / ``TERMAPY_GUI=0``.  Not MCP.
    gui_apps: bool = False

    def satisfied_by(self, provided: "CapabilitySet") -> bool:
        """True iff every capability set in ``self`` is also set in ``provided``."""
        return all(
            not getattr(self, f.name) or getattr(provided, f.name) for f in fields(self)
        )

    def missing_from(self, provided: "CapabilitySet") -> list[str]:
        """Return field names required by ``self`` that ``provided`` lacks.

        Order matches declaration order above, which is a stable, reviewable
        vocabulary (not alphabetical).
        """
        return [
            f.name
            for f in fields(self)
            if getattr(self, f.name) and not getattr(provided, f.name)
        ]

    def union(self, other: "CapabilitySet") -> "CapabilitySet":
        """Return a new set that has every capability provided by either side.

        Useful when deriving one environment from another, e.g. the script
        runner's capabilities are the REPL's plus ``block_until``.
        """
        return CapabilitySet(
            **{
                f.name: getattr(self, f.name) or getattr(other, f.name)
                for f in fields(self)
            }
        )


# ─────────────────────────────────────────────────────────────────────────────
# GUI-apps detection + environment capability sets
# ─────────────────────────────────────────────────────────────────────────────


def detect_gui_apps() -> bool:
    """Heuristic: can this process launch external desktop apps the user sees?

    SSH users have an *interactive* session but typically no local desktop --
    ``webbrowser.open()`` and ``os.startfile()`` succeed silently while
    opening on the remote machine.  This detector lets hosts advertise
    ``gui_apps`` correctly so commands like ``/help.open`` and ``/edit``
    are gated when they wouldn't actually be useful.

    Detection order:

      1. ``TERMAPY_GUI`` env override (``1``/``yes``/``true``/``on`` -> True;
         ``0``/``no``/``false``/``off`` -> False).  Escape hatch for users
         whose environment fools the heuristic (mosh, tmux-in-SSH, WSLg,
         X2Go, ...).
      2. SSH session (``SSH_CONNECTION`` or ``SSH_TTY`` set): True only if
         X11 forwarding is also configured (``DISPLAY`` set).
      3. Linux / macOS: True iff ``DISPLAY`` or ``WAYLAND_DISPLAY`` is set.
      4. Windows: True (assume native graphical session).
      5. Unknown platform: False (fail safe).

    The heuristic is best-effort, not authoritative.  Real-world environments
    that fool it are handled by the override.

    Returns:
        True if external desktop apps will likely be visible to the user.
    """
    override = os.environ.get("TERMAPY_GUI", "").strip().lower()
    if override in ("1", "yes", "true", "on"):
        return True
    if override in ("0", "no", "false", "off"):
        return False
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return bool(os.environ.get("DISPLAY"))
    if sys.platform.startswith("linux") or sys.platform == "darwin":
        return bool(
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
        )
    if sys.platform == "win32":
        return True
    return False


def _build_environments() -> dict[str, "CapabilitySet"]:
    """Build the per-environment capability sets used by ``/help`` rendering.

    The single source of truth for "where can this command run."  The
    ``AVAILABLE`` row in the man page and ``/help --mcp`` both derive from
    this map.  Adding a new host (web preview, CI runner, ...) means adding
    a row here.

    ``gui_apps`` is computed once at import time via :func:`detect_gui_apps`.
    Long-running servers don't change desktop state mid-process; users who
    SSH in and out and reload termapy get fresh detection.
    """
    gui = detect_gui_apps()
    return {
        # TUI (Textual app): everything an interactive desktop terminal has.
        "TUI": CapabilitySet(
            interactive=True,
            gui_apps=gui,
            tui_mode=True,
            screen_capture=True,
            confirm_dialog=True,
            ui_notify=True,
            status_bar=True,
        ),
        # CLI (interactive REPL or --run script): interactive but no Textual UI.
        "CLI": CapabilitySet(interactive=True, gui_apps=gui),
        # MCP (stdio server): no human, no display.  Pure machine-driven.
        "MCP": CapabilitySet(),
    }


ENVIRONMENTS: dict[str, "CapabilitySet"] = _build_environments()


@dataclass
class CmdResult:
    """Result returned by every plugin/hook handler and by dispatch().

    Handlers return ``CmdResult.ok()`` on success or
    ``CmdResult.fail(msg="...")`` on error.  ``dispatch()`` sets
    ``elapsed_s`` automatically after the handler returns.

    **The ``value`` field matters for scripting.** When a script runs a
    command in quiet mode, the result is the value.  Handlers that produce
    scriptable data (a config value, a port property, a CRC, a ping time,
    a toggle state) should pass ``value=`` so scripts can capture it:

        return CmdResult.ok(value=str(baud_rate))

    Handlers that only perform side effects (clear screen, edit file,
    start capture, send break) don't need a value.  When in doubt: if a
    user might want to assign this command's output to a variable, set
    ``value=``.
    """

    err_prefix: ClassVar[str] = "Error:"  # Set globally: CmdResult.err_prefix = "Fail:"

    success: bool = True
    error: str = ""
    elapsed_s: float = 0.0
    # ``value`` is loosely typed because most handlers return strings
    # (scripting/quiet-mode reads it as text), but profile-aware MCP
    # dispatches return shaped data (dicts, lists, numbers) per the
    # device profile's response schema.  Callers that stringify must
    # do so explicitly.
    value: Any = ""

    @classmethod
    def ok(cls, value: Any = "") -> CmdResult:
        """Return a successful result, optionally with a value.

        ``value`` is typically a string (script-readable), but profile
        executors pass typed shapes (dict/list/number) that survive
        through to the MCP response.
        """
        return cls(value=value)

    @classmethod
    def fail(cls, msg: str = "", *, value: Any = "") -> CmdResult:
        """Return a failure result with an error message.

        ``value`` is optional; profile executors set it on parse-failure
        so the LLM still sees the raw response text alongside the error.
        """
        return cls(success=False, error=msg, value=value)

    @property
    def err_msg(self) -> str:
        """Formatted error string with class-level prefix for display."""
        if not self.error:
            return ""
        return f"{self.err_prefix} {self.error}"


# Alias for ``Exception``, used at call sites where we deliberately
# invoke code we don't own -- plugin handlers, lifecycle hooks, script
# bodies, RX/TX observers, dynamic ``long_help`` callables, and plugin
# module loads.  Catching broadly is the right call at a trust
# boundary: the callee can raise anything, and we need to report the
# failure without letting one misbehaving plugin crash the host.
#
# Rules for using this alias:
#   - every site MUST report the exception somewhere the user or
#     plugin author will see it (log, CmdResult.fail, status line);
#     never a silent ``pass``.
#   - use ONLY at a trust boundary.  For code we own (our own
#     functions, stdlib calls, well-known libraries) narrow the
#     except to the specific exception types that can realistically
#     raise.  ``BoundaryException`` exists to flag that THIS broad
#     catch has been reviewed for intent.
BoundaryException = Exception


@dataclass
class Command:
    """Plugin command declaration.

    Every plugin file must export a ``COMMAND`` instance at module level.
    Root commands require ``name``; sub_commands entries get their name
    from the dict key.

    Attributes:
        help: One-line description shown by ``/help``.
        name: Command name (lowercase). Required at root level, empty
            for sub_commands entries (name comes from the dict key).
        args: Argument spec for help display. ``""`` = no args,
            ``"{opt}"`` = optional, ``"<required>"`` = required.
        long_help: Extended help shown by ``/help <cmd>``.  May be a string
            or a callable ``(PluginContext) -> str``.  Callables are
            invoked at render time so the help can reflect live runtime
            state (loaded files, current connection, etc.).  See
            ``resolve_long_help`` and the "Dynamic help" section of
            ``writing-plugins.md``.
        handler: The command function. Required for leaf nodes.
            Signature: ``handler(ctx: PluginContext, args: str) -> None``.
        sub_commands: Dict mapping subcommand names to ``Command`` instances.
        raw_args: When True, REPL transforms are skipped for this command.
            Use for commands that take variable names as arguments.
        flags: Mapping of ``--flag`` (or short ``-f``) to either a
            description string (canonical flag) or another flag name
            (alias).  Declared flags are parsed out of the args string
            before the handler runs; the handler reads them via
            ``ctx.flag("--name")``.  Unknown ``-x`` / ``--xxx`` tokens
            fail dispatch with a "did you mean" suggestion.  Commands
            with an empty ``flags`` dict do no flag parsing at all,
            preserving full back-compat.
        needs: Environment capabilities the handler requires.  Default
            is ``CapabilitySet()`` -- the baseline every environment
            provides, nothing more.  Dispatch gates the handler when
            the context lacks any declared capability, failing with a
            clear message naming what's missing.  See ``CapabilitySet``
            for the full vocabulary.
        hidden: When True, this command is omitted from ``/help``
            listings, the ``/`` popup, ``/search`` results, and other
            discovery surfaces.  Still dispatches normally -- so legacy
            aliases (``/echo`` -> ``/term.echo``) can forward silently
            without polluting the user's sense of the "real" command
            surface.  ``/help <name>`` with an exact hidden name still
            shows the help.
    """

    help: str
    name: str = ""
    args: str = ""
    long_help: LongHelp = ""
    handler: Callable | None = None
    sub_commands: dict[str, "Command"] | None = None
    raw_args: bool = False
    flags: dict[str, str] = field(default_factory=dict)
    needs: CapabilitySet = field(default_factory=CapabilitySet)
    hidden: bool = False


@dataclass
class DirectiveResult:
    """Result from a pre-dispatch directive handler.

    Attributes:
        action: What to do - ``"rewrite"`` dispatches payload as a REPL
            command, ``"warn"`` shows payload in yellow, ``"error"`` shows
            payload in red, ``"none"`` means no directive matched.
        payload: Command string (for rewrite) or message (for warn/error).
    """

    action: str = "none"
    payload: str = ""


@dataclass
class Directive:
    """Pre-dispatch line rewriter declaration.

    Plugin files that intercept raw input lines before REPL/serial routing
    export a ``DIRECTIVE`` instance at module level.  Directives run in load
    order - built-ins first, then global, then per-config.  A file may export
    ``COMMAND``, ``TRANSFORM``, and/or ``DIRECTIVE``.

    The handler receives the raw input line and returns a ``DirectiveResult``
    or ``None`` to pass to the next directive.

    Attributes:
        name: Identifier for the directive (shown in /help).
        help: One-line description.
        pattern: Human-readable syntax hint (e.g. ``"$(NAME) = value"``).
        handler: ``(str) -> DirectiveResult | None``.
    """

    name: str
    help: str
    pattern: str = ""
    handler: Callable | None = None


@dataclass
class DirectiveInfo:
    """Loaded directive with source metadata.

    Attributes:
        name: Identifier for the directive.
        help: One-line description.
        pattern: Human-readable syntax hint.
        handler: Pre-dispatch rewriter function.
        source: Where the directive was loaded from.
    """

    name: str
    help: str
    pattern: str = ""
    handler: Callable | None = None
    source: str = "built-in"


@dataclass
class Transform:
    """Input rewriter declaration.

    Plugin files that rewrite command input export a ``TRANSFORM`` instance
    at module level.  Transforms run in load order - built-ins first, then
    global, then per-config.  A file may export both ``COMMAND`` and
    ``TRANSFORM``.

    Attributes:
        name: Identifier for the transform (shown in /info listings).
        help: One-line description of what the transform does.
        repl: Rewriter for REPL commands.  ``(str) -> str``.
        serial: Rewriter for device commands.  ``(str) -> str``.
    """

    name: str
    help: str
    repl: Callable | None = None  # (str) -> str, rewrites REPL commands
    serial: Callable | None = None  # (str) -> str, rewrites device commands


@dataclass
class TransformInfo:
    """Loaded transform with source metadata.

    Attributes:
        name: Identifier for the transform.
        help: One-line description.
        repl: REPL rewriter function, or None.
        serial: Serial rewriter function, or None.
        source: Where the transform was loaded from.
    """

    name: str
    help: str
    repl: Callable | None = None
    serial: Callable | None = None
    source: str = "built-in"


@dataclass
class LifecycleHook:
    """A lifecycle hook discovered on a plugin module.

    Plugins declare hooks by exporting top-level functions with specific
    names.  There is no base class and no decorators - a plugin is a module
    that exports stuff, and lifecycle functions are just more stuff it can
    export.

    Supported hook names (see :data:`LIFECYCLE_HOOK_NAMES`):

    - ``on_app_start``    - fires once after plugins are loaded and the
                            context is wired, before first dispatch.
    - ``on_app_stop``     - fires once during graceful shutdown.  Not
                            guaranteed on crash.
    - ``on_connect``      - fires after the serial port is successfully opened.
    - ``on_disconnect``   - fires before the serial port is closed (user-initiated).
    - ``on_config_load``  - fires after switching to a new config via ``/cfg.load``.
    - ``on_script_start`` - fires when a script begins executing.
    - ``on_script_stop``  - fires after a script finishes, including on
                            ``/stop`` or exception.  Mirrors ``on_script_start``.

    Attributes:
        name: The hook name (e.g. ``"on_app_start"``).
        handler: The function the plugin module exported.  Signature:
            ``handler(ctx: PluginContext) -> None``.
        source: Where the plugin was loaded from ("built-in", "global",
            or a config name).  Used for diagnostics only.
        plugin: The plugin file stem, for error messages.
    """

    name: str
    handler: Callable
    source: str = "built-in"
    plugin: str = ""


# Lifecycle hook names plugins may export as top-level functions.  Adding
# a new hook is: append here, then call ReplEngine.fire_lifecycle(name)
# from the matching dispatch point.
LIFECYCLE_HOOK_NAMES = (
    "on_app_start",
    "on_app_stop",
    "on_connect",
    "on_disconnect",
    "on_config_load",
    "on_script_start",
    "on_script_stop",
)


@dataclass
class LoadResult:
    """Result of loading plugins from a directory.

    Attributes:
        plugins: Successfully loaded PluginInfo entries.
        transforms: Successfully loaded TransformInfo entries.
        directives: Successfully loaded DirectiveInfo entries.
        lifecycle_hooks: LifecycleHook entries discovered on plugin modules.
        skipped: File names that were skipped (no COMMAND instance).
        errors: File names that raised exceptions during loading.
    """

    plugins: list = field(default_factory=list)
    transforms: list = field(default_factory=list)
    directives: list = field(default_factory=list)
    lifecycle_hooks: list[LifecycleHook] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class EngineAPI:
    """Privileged escape hatch exposed to built-in plugins only.

    Holds Textual, threading, and pyserial handles that are genuinely
    frontend-specific and cannot be generified: the plugin registry,
    config apply hooks, port connect/disconnect, capture lifecycle,
    proto debug screen, the raw RX queue, cancel/stop events, etc.

    For session state (flags, counters, target commands, per-plugin
    scratch space) use ``ctx.ns()`` instead.  That is the supported API
    for both built-in and external plugins.  Anything that could live in
    a plain dict has been migrated off ``EngineAPI`` on purpose -- what's
    left is the set of things that must remain frontend-coupled.

    Access from built-in plugins via ``ctx.engine``.  External plugins
    should not use this; it is unstable and may change between versions.
    """

    prefix: str = DEFAULT_CMD_PREFIX
    plugins: dict = field(default_factory=dict)
    in_script: Callable = lambda: False
    script_stop: Callable = lambda: None
    # Internal dispatch: run a REPL command through the plugin pipeline
    # (capability gates, flag parsing, etc.) without the serial-output
    # sugar that ``ctx.dispatch`` adds via dispatch_full.  Used by legacy
    # forwarders (/echo -> /term.echo) where going back out to
    # dispatch_full would misinterpret the un-prefixed target as a
    # serial command.
    dispatch: Callable = lambda _line: None
    save_cfg: Callable | None = None  # (key, val) -> confirm dialog; None = no confirm
    apply_cfg: Callable = lambda key, val: None
    coerce_type: Callable = lambda val, existing: val
    open_proto_debug: Callable = lambda path, script: None
    start_capture: Callable = lambda **kw: None
    stop_capture: Callable = lambda: None
    directives: list = field(default_factory=list)
    connect: Callable = lambda port=None: None
    disconnect: Callable = lambda: None
    update_port: Callable = lambda name: None
    apply_port_effects: Callable = lambda effects: None
    # Load a named config file, disconnecting + reconnecting as needed.
    # Name is resolved via config_resolve.resolve_config, so a bare
    # "myproj" or a full path both work.  Returns a ``CmdResult`` so
    # the caller can surface success/failure via their frontend.
    load_config: Callable = lambda name: None
    rx_queue: Any = None  # queue.Queue[bytes] - raw RX for protocol handlers
    xfer_cancel: Any = None  # threading.Event - set by Escape to cancel transfers
    script_stop_event: Any = None  # threading.Event - set by /stop to abort scripts
    # Open the picker/dialog associated with a top-level command name
    # ("cfg", "run", "proto").  TUI installs this in _register_tui_hooks;
    # CLI leaves it None so plugin handlers fall through to their existing
    # bare-args behaviour (JSON dump, script list, long-help, etc.).
    open_picker: Callable | None = None  # (name: str) -> CmdResult


class PluginConfig:
    """Persistent per-config key-value storage for a plugin.

    Each plugin's config is a JSON file at a deterministic path::

        termapy_cfg/<config>/plugin/<plugin_name>.cfg

    The config is loaded lazily on first access and cached in memory.
    Call ``save()`` to write changes to disk.

    Usage::

        def _handler(ctx, args):
            cfg = ctx.plugin_cfg("pic_map")
            cfg["map_path"] = "/path/to/mem.map"
            cfg.save()

            # Read back
            path = cfg.get("map_path", "")

    The dict-like interface supports ``get()``, ``[]``, ``[]=``,
    ``pop()``, ``in``, ``del``, and iteration.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict | None = None

    @property
    def path(self) -> Path:
        """The on-disk path to this config file."""
        return self._path

    def _ensure_loaded(self) -> dict:
        if self._data is None:
            if self._path.exists():
                try:
                    self._data = json.loads(self._path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    self._data = {}
            else:
                self._data = {}
        return self._data

    def save(self) -> None:
        """Write the current config to disk.

        Creates the parent directory if needed.

        Raises:
            OSError: If the file cannot be written.
        """
        data = self._ensure_loaded()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value, returning *default* if the key is absent."""
        return self._ensure_loaded().get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self._ensure_loaded()[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._ensure_loaded()[key] = value

    def __delitem__(self, key: str) -> None:
        del self._ensure_loaded()[key]

    def __contains__(self, key: str) -> bool:
        return key in self._ensure_loaded()

    def __iter__(self):
        return iter(self._ensure_loaded())

    def __len__(self) -> int:
        return len(self._ensure_loaded())

    def pop(self, key: str, *args: Any) -> Any:
        """Remove and return a value.  Accepts an optional default."""
        return self._ensure_loaded().pop(key, *args)

    def items(self):
        """Return key-value pairs."""
        return self._ensure_loaded().items()

    def __repr__(self) -> str:
        return f"PluginConfig({self._path})"


@dataclass
class PluginContext:
    """Stable API for plugin interaction with the terminal.

    Every plugin handler receives a PluginContext as its first argument.
    This is the only interface plugins should use - it insulates them
    from Textual, pyserial, and internal engine details.

    Attributes:
        write: Output text to the terminal. Signature: ``write(text, color="dim")``.
            Color can be any Rich color name (e.g. ``"red"``, ``"green"``, ``"dim"``).
        write_markup: Output Rich markup text to the terminal. Signature:
            ``write_markup(text)``. Supports Rich markup tags like
            ``[bold red]text[/]``.
        log: Write a timestamped line to the session log file. Signature:
            ``log(prefix, text)`` where prefix is ``">"`` (TX), ``"<"`` (RX),
            or ``"#"`` (status). Independent of screen output - always logged
            regardless of echo settings.
        cfg: Read-only config dict (``MappingProxyType``). Access any config
            field with ``ctx.cfg.get("key", default)``. Do not mutate.
        config_path: Absolute path to the current JSON config file on disk.
        port: The underlying pyserial ``Serial`` object (or ``None`` when
            disconnected). Returns the live object - properties like
            ``ctx.port().baudrate``, ``ctx.port().dtr``, etc. reflect current
            state. This is a callable; use ``ctx.port()`` not ``ctx.port``.
        is_connected: Returns ``True`` if the serial port is open.
        serial_write: Send raw bytes to the serial port. No line ending is
            appended - pass exactly the bytes you want transmitted.
        serial_wait_idle: Block until the serial port has been quiet for ~400ms.
            Useful in scripts to wait for a device response before the next command.
        serial_read_raw: Collect raw bytes from the serial port with timeout-based
            framing. Signature: ``serial_read_raw(timeout_ms=1000) -> bytes``.
            Returns a complete frame (bytes) or ``b""`` on timeout.
        serial_claim: Suppress normal terminal display and claim exclusive access
            to incoming serial bytes. Low-level primitive - prefer ``serial_io()``
            context manager instead.
        serial_release: Resume normal terminal display. Low-level primitive -
            prefer ``serial_io()`` context manager instead.
        ss_dir: Path to the per-config screenshots directory (auto-created).
        scripts_dir: Path to the per-config scripts directory (auto-created).
        proto_dir: Path to the per-config protocol test scripts directory (auto-created).
        cap_dir: Path to the per-config cap/ directory (auto-created).
        prof_dir: Path to the per-config prof/ directory (auto-created).
        dispatch: Route a raw command through the full dispatch pipeline
            (directives, transforms, REPL/serial). Signature: ``dispatch(cmd)``.
            Thread-safe when called via ``call_from_thread``.
        confirm: Show a Yes/Cancel confirmation dialog and return the result.
            Signature: ``confirm(message) -> bool``. **Must be called from a
            background thread** (e.g. inside a ``@work(thread=True)`` handler).
            Blocks the calling thread until the user responds.
        notify: Show a toast notification. Signature: ``notify(text, **kw)``.
            Keyword args are passed to Textual's ``App.notify()``.
        clear_screen: Clear the terminal output and reset the line counter.
        save_screenshot: Save the terminal view. Signature: ``save_screenshot(path)``.
        get_screen_text: Return all visible terminal output as a plain-text string.
        exit_app: Exit the application.
        engine: Privileged escape hatch (``EngineAPI``).  Textual, threading,
            and pyserial handles that cannot be generified.  **Built-in
            plugins only** -- unstable, may change between versions.  For
            session state, prefer ``ctx.ns()``.
        ns: Return a session-scoped namespace dict, creating it on first
            access.  The supported API for storing per-session state in
            both built-in and third-party plugins.  See ``PluginContext.ns``.
            Engine toggles like ``echo``, ``hex_mode``, and ``output_level``
            live in the reserved ``flags`` namespace; plugins should use their
            own namespace name (e.g. ``ctx.ns("myplugin")``) to avoid
            collision.
        plugin_cfg: Return a persistent ``PluginConfig`` object for a named
            plugin.  The config file is stored at
            ``termapy_cfg/<config>/plugin/<name>.cfg``.  Loaded lazily,
            cached per session.  Call ``.save()`` to write to disk.
            Use ``ns()`` for session-only state, ``plugin_cfg()`` for
            state that must survive across sessions.
        status_bar: Show transient text in the bottom status bar (TUI only).
            The text shares space with the REPL input and auto-clears after
            a timeout (default 5 seconds).  No-op in CLI mode.
            Signature: ``status_bar(text, timeout=5.0)``.
        add_rx_observer: Register a callback that receives every raw RX byte
            chunk from the serial port.  Observers see data alongside the
            normal display pipeline -- they cannot modify or block it.
            Callbacks fire on the reader background thread.
            Signature: ``add_rx_observer(cb: Callable[[bytes], None])``.
        remove_rx_observer: Unregister an RX observer callback.
            Signature: ``remove_rx_observer(cb: Callable[[bytes], None])``.
        add_tx_observer: Register a callback that receives every TX byte
            chunk sent to the serial port.  Observers see data alongside
            the normal write path.  Callbacks fire on the calling thread.
            Signature: ``add_tx_observer(cb: Callable[[bytes], None])``.
        remove_tx_observer: Unregister a TX observer callback.
            Signature: ``remove_tx_observer(cb: Callable[[bytes], None])``.
    """

    # Core I/O
    write: Callable  # write(text, color="dim") -> None
    write_markup: Callable = lambda text: None  # write(text) with Rich markup
    cfg: MappingProxyType | dict = field(default_factory=dict)
    config_path: str = ""

    # Logging - log(prefix, text) writes a timestamped line to the session log.
    # Prefixes: ">" TX (commands sent), "<" RX (device responses), "#" status.
    log: Callable = lambda prefix, text: None

    # Serial port
    port: Callable = lambda: None  # -> serial.Serial | None
    is_connected: Callable = lambda: False
    serial_write: Callable = lambda data: None
    serial_send: Callable = (
        lambda text: None
    )  # send text with configured line ending + encoding
    serial_wait_for_data: Callable = lambda timeout_ms=250: False  # wait for first byte
    serial_wait_idle: Callable = lambda timeout_ms=400: None
    serial_read_raw: Callable = lambda timeout_ms=1000, frame_gap_ms=0: b""
    serial_drain: Callable = lambda: 0
    serial_claim: Callable = lambda: None  # suppress terminal display, claim raw bytes
    serial_release: Callable = lambda: None  # resume normal terminal display
    wait_for_match: Callable = (
        lambda predicate, timeout=5.0: None
    )  # block until line matches

    # Status bar - transient text in the bottom bar (TUI only, no-op in CLI)
    status_bar: Callable = lambda text, timeout=5.0: None

    # Serial observers - see raw bytes without disrupting the pipeline
    add_rx_observer: Callable = lambda cb: None
    remove_rx_observer: Callable = lambda cb: None
    add_tx_observer: Callable = lambda cb: None
    remove_tx_observer: Callable = lambda cb: None

    # Filesystem
    ss_dir: Path = field(default_factory=lambda: Path("."))
    scripts_dir: Path = field(default_factory=lambda: Path("."))
    proto_dir: Path = field(default_factory=lambda: Path("."))
    cap_dir: Path = field(default_factory=lambda: Path("."))
    prof_dir: Path = field(default_factory=lambda: Path("."))

    # Dispatch - route a raw command through the full dispatch pipeline
    # (directives, transforms, REPL/serial). Thread-safe when wired via
    # call_from_thread in app.py.
    dispatch: Callable = lambda cmd: None  # dispatch(cmd) -> None

    # UI
    confirm: Callable = (
        lambda message: False
    )  # confirm(msg) -> bool (worker thread only)
    notify: Callable = lambda text, **kw: None
    clear_screen: Callable = lambda: None
    save_screenshot: Callable = lambda path: None
    get_screen_text: Callable = lambda: ""
    open_file: Callable = lambda path: None  # open file in system viewer/editor
    exit_app: Callable = lambda: None

    # Engine internals - used by built-in commands only
    engine: EngineAPI = field(default_factory=EngineAPI)

    # Per-dispatch flag set - populated by ReplEngine.dispatch from the
    # invoking command's declared Command.flags before the handler runs.
    # Reset on every dispatch. Handlers read via ``ctx.flag("--name")``
    # which normalizes aliases. Distinct from the ``ns("flags")``
    # namespace (echo/hex_mode toggles).
    active_flags: set[str] = field(default_factory=set)

    # Per-call output-level override.  None means "use the global level
    # from ctx.ns('flags')['output_level']".  Set by ReplEngine.dispatch
    # when the user invokes a command with a level suffix or flag
    # (e.g. ``cmd.quiet`` or ``cmd --silent``); cleared after dispatch.
    _call_level: str | None = None

    # Capabilities this environment provides.  Dispatch compares a
    # command's declared ``needs`` against this set before calling the
    # handler and fails with a clear message if anything is missing.
    # The REPL, script runner, CLI, TUI, and test harness each publish
    # their own capabilities when the context is constructed.  See the
    # ``CapabilitySet`` docstring for the full capability vocabulary.
    capabilities: CapabilitySet = field(default_factory=CapabilitySet)

    # Namespace registry - plugin/builtin session-scoped state.
    # See ctx.ns() below for the public interface.
    _namespaces: dict[str, dict] = field(default_factory=dict)

    # Plugin config cache - keyed by plugin name, lazy-loaded from disk.
    _plugin_cfgs: dict[str, PluginConfig] = field(default_factory=dict)

    # -- Namespaces ------------------------------------------------------------

    def ns(self, name: str) -> dict:
        """Return a session-scoped namespace dict, creating it on first access.

        Namespaces are uniform mutable ``dict`` s keyed by name.  They live for
        the lifetime of the ``PluginContext`` (one app session) and are the
        supported way for both built-in and third-party plugins to keep
        per-session state.  Prefer this over monkey-patching ``ctx`` or using
        module-level globals.

        Namespaces are not isolated -- any caller can read or write any
        namespace by name.  The naming convention is collision avoidance, not
        access control.  Plugins that publish state for other plugins to read
        should document their key schema.

        The ``flags`` namespace is engine-reserved for toggles like ``echo``,
        ``hex_mode``, and the ``output_level`` dial.  Third-party plugins
        should use their own namespace name (conventionally the plugin name).

        Example::

            def _handler(ctx, args):
                store = ctx.ns("myplugin")
                store["requests_sent"] = store.get("requests_sent", 0) + 1
                ctx.write(f"sent {store['requests_sent']} requests")

        Args:
            name: Namespace identifier.  Created empty on first access.

        Returns:
            The namespace dict.  Mutations persist for the life of the
            ``PluginContext``.  Successive calls with the same name return
            the same dict.
        """
        if name not in self._namespaces:
            self._namespaces[name] = {}
        return self._namespaces[name]

    def flag(self, name: str) -> bool:
        """Return True if the given flag was passed on the invoking command.

        Handlers declare flags on their ``Command(flags={...})`` dict; the
        dispatcher strips them from the args string and records them in
        ``ctx.active_flags`` before calling the handler. Aliases resolve
        to the canonical name, so ``ctx.flag("--table")`` is true whether
        the user typed ``-t`` or ``--table``.

        Args:
            name: Canonical flag name including the leading dashes
                (e.g. ``"--table"``).

        Returns:
            True if the flag was present on the invocation.
        """
        return name in self.active_flags

    def plugin_cfg(self, name: str) -> PluginConfig:
        """Return a persistent config object for a plugin.

        The config file lives at a deterministic path::

            termapy_cfg/<config>/plugin/<name>.cfg

        The file is loaded lazily on first access and cached for the
        session.  Call ``.save()`` to write changes to disk.

        Example::

            def _handler(ctx, args):
                cfg = ctx.plugin_cfg("pic_map")
                cfg["map_path"] = args.strip()
                cfg.save()

        Args:
            name: Plugin name.  Used as the config file stem.

        Returns:
            A ``PluginConfig`` instance backed by the JSON file.

        Raises:
            RuntimeError: If no config is loaded (no ``config_path``).
        """
        if name in self._plugin_cfgs:
            return self._plugin_cfgs[name]
        if not self.config_path:
            raise RuntimeError(
                f"Cannot access plugin config for {name!r}: no config loaded"
            )
        path = Path(self.config_path).parent / "plugin" / f"{name}.cfg"
        pc = PluginConfig(path)
        self._plugin_cfgs[name] = pc
        return pc

    # -- Output channels -------------------------------------------------------

    @property
    def output_level(self) -> str:
        """Active output level, honoring any per-call override.

        Falls back to the global level in ``ctx.ns("flags")`` and finally
        to ``DEFAULT_OUTPUT_LEVEL`` -- never raises on a missing key.
        """
        if self._call_level is not None:
            return self._call_level
        return self.ns("flags").get("output_level", DEFAULT_OUTPUT_LEVEL)

    def _shows(self, min_rank: int) -> bool:
        rank = _OUTPUT_LEVEL_RANK.get(self.output_level, _OUTPUT_LEVEL_RANK[DEFAULT_OUTPUT_LEVEL])
        return rank >= min_rank

    def result(self, text: str, color: str = "green") -> None:
        """Write a command result (single-line answer). Shown at quiet+."""
        if self._shows(_RESULT_MIN_RANK):
            self.write(text, color)

    def output(self, text: str, color: str = "dim") -> None:
        """Write data output (listings, dumps, file contents). Shown at normal+."""
        if self._shows(_OUTPUT_MIN_RANK):
            self.write(text, color)

    def status(self, text: str) -> None:
        """Write a status/progress message. Shown only at verbose."""
        if self._shows(_STATUS_MIN_RANK):
            self.write(text, "dim")

    @contextmanager
    def serial_io(self) -> Generator[None, None, None]:
        """The synchronous-serial-read primitive.

        Claims the serial port for the duration of the block (suppresses
        display, queues bytes for ``serial_read_raw()`` instead of feeding
        ``on_lines``).  Releases on every exit path including exceptions.
        Use this for any drain -> write -> read cycle.

        This is the only public path -- the bare flag setter is
        intentionally unreachable from plugin code so exception safety
        is structural rather than a rule contributors have to remember.

        Usage::

            with ctx.serial_io():
                ctx.serial_drain()
                ctx.serial_write(payload)
                response = ctx.serial_read_raw()
        """
        self.serial_claim()
        try:
            yield
        finally:
            self.serial_release()


@dataclass
class PluginInfo:
    """Metadata and handler for a single plugin command or subcommand.

    Attributes:
        name: Dotted command path (lowercase). Users type ``/name`` or
            ``/parent.child`` to invoke.
        args: Argument spec for help display. ``""`` = no args,
            ``"{opt}"`` = optional, ``"<required>"`` = required.
        help: One-line description shown by ``/help``.
        long_help: Extended help shown by ``/help <cmd>``. May be a string
            (static prose, possibly multi-line) or a callable
            ``(PluginContext) -> str`` that returns the current text at
            render time.  When empty/callable-returning-empty, the one-line
            ``help`` is shown instead.  See ``resolve_long_help``.
        handler: The command function. Signature:
            ``handler(ctx: PluginContext, args: str) -> None``.
        source: Where the plugin was loaded from (``"built-in"``, ``"global"``,
            or the config name).
        children: Dotted names of direct subcommands (empty for leaf commands).
        raw_args: When True, REPL transforms are skipped for this command.
        flags: Resolved flag map inherited from ``Command.flags``. Keys
            are canonical flag names (e.g. ``--table``); values are
            either a description (canonical) or another flag key (alias).
            Empty dict means the command opts out of flag parsing.
        needs: Environment capabilities the handler requires (inherited
            from ``Command.needs``).  See ``CapabilitySet``.
    """

    name: str
    args: str
    help: str
    handler: Callable  # handler(ctx: PluginContext, args: str) -> None
    long_help: LongHelp = ""
    source: str = "built-in"
    children: list[str] = field(default_factory=list)
    raw_args: bool = False
    flags: dict[str, str] = field(default_factory=dict)
    needs: CapabilitySet = field(default_factory=CapabilitySet)
    hidden: bool = False


def interpolate_help(text: str, prefix: str) -> str:
    """Substitute ``{prefix}`` in a help string with the live REPL prefix.

    Plugin authors write cross-references to other commands like
    ``"See {prefix}cfg.auto"`` instead of a hardcoded ``"See /cfg.auto"``
    so the rendered output honours a user's ``cmd_prefix`` override.
    Called by every help-rendering path (both short ``help=`` and
    long ``long_help=``) so the substitution is uniform.

    ``prefix`` is a plain string (usually ``ctx.engine.prefix`` at the
    call site) rather than a ``PluginContext``, because several help
    renderers already have the prefix in hand and don't need to thread
    ctx through just to reach it.

    Safe on empty / None input: returns ``""`` / ``None`` unchanged.
    """
    if not text:
        return text
    return text.replace("{prefix}", prefix)


def resolve_long_help(plugin: PluginInfo, ctx: "PluginContext") -> str:
    """Return ``plugin.long_help`` as a prefix-interpolated string.

    Static strings pass through with ``{prefix}`` substituted.  Callables
    are invoked with the live ``PluginContext`` and their return value
    gets the same substitution.

    Any exception raised by a callable is caught and returned as a
    fallback string so that ``/help`` rendering can never itself fail.
    A broken help function is a bug to fix, not a condition that should
    make ``/help`` unusable -- the fallback string puts the error right
    where the author will notice it.
    """
    hp = plugin.long_help
    if not isinstance(hp, str):
        # Plugin-supplied callable: boundary catch so a broken help
        # function doesn't take down /help.  The message is shown in
        # the /help body where the author will notice.
        try:
            hp = hp(ctx)
        except BoundaryException as e:
            hp = f"(dynamic help failed: {e})"
    return interpolate_help(hp or "", ctx.engine.prefix)


@dataclass
class TargetCommand:
    """Help-only command imported from a connected device.

    These are NOT REPL commands -- they have no handler and no prefix.
    They appear in help output and suggestions only.

    ``long_help`` and ``flags`` mirror the corresponding fields on
    ``PluginInfo`` so that ``/help <target>`` and ``/search`` can give
    device commands the same first-class treatment as built-in plugins.
    Both are optional -- a device JSON entry that supplies only
    ``help`` + ``args`` (the old shape) still works unchanged.

    **v2 profile fields** (``typed_args`` through ``subcommands``) are
    optional metadata consumed by the MCP server and codegen tools.
    All have defaults so v1 manifests round-trip unchanged: a manifest
    with only ``help`` + ``args`` produces a TargetCommand whose v2
    fields are at their defaults, and ``_to_json_dict`` omits them.

    Attributes:
        name: Command name as the device expects it (no / prefix).
        help: One-line description.
        args: Argument spec string (may be empty).
        long_help: Optional extended prose rendered in the DESCRIPTION
            section of ``/help <target>``. Plain string only -- no
            callables -- because device-published help is data, not code.
        flags: Optional flag map, same shape as ``Command.flags``.  Keys
            are canonical flag names (e.g. ``--table``) or aliases
            (key = alias, value = canonical name).  Rendered in the
            FLAGS section of ``/help <target>``.
        typed_args: v2.  Structured argument schemas: list of dicts
            ``{name, type, required, default, help, min, max, enum}``.
            Consumed by the MCP server (typed signatures) and codegen.
        send_template: v2.  Python-format-style template for the
            outbound bytes, e.g. ``"AT+VOLT={mv}"``.  Empty = use the
            command name verbatim.  For NDJSON protocol, the bridge
            JSON-serializes args instead of using the template.
        response: v2.  Response shape descriptor: dict with ``format``
            (none/literal/lines/regex/json), ``pattern``, ``types``,
            ``terminator``, ``line_pattern``, ``line_types``,
            ``timeout_ms``.  See ``response_parsers.parse_response``.
        safety: v2.  Safety tier: ``"safe"`` (default), ``"readonly"``,
            or ``"destructive"``.  ``destructive`` surfaces the MCP
            ``annotations.destructiveHint=true`` so clients prompt for
            confirmation.
        rate_limit_hz: v2.  Bridge-enforced rate limit.  ``0.0`` = no
            limit.
        timeout_ms: v2.  Per-command outer timeout (overrides config
            default).  ``0`` = use the default.
        subcommands: v2.  Nested commands, same shape (recursive).
    """

    name: str
    help: str
    args: str = ""
    long_help: str = ""
    flags: dict[str, str] = field(default_factory=dict)
    # v2 profile fields (all optional, all have v1-preserving defaults)
    typed_args: list[dict] = field(default_factory=list)
    send_template: str = ""
    response: dict = field(default_factory=dict)
    safety: str = "safe"
    # ``enabled`` defaults True so existing curated profiles and device-
    # self-published manifests stay exposed without an explicit opt-in.
    # Profiles drafted from legacy help dumps (where the engineer hasn't
    # audited each entry yet) explicitly emit enabled=False.
    enabled: bool = True
    rate_limit_hz: float = 0.0
    timeout_ms: int = 0
    subcommands: dict[str, "TargetCommand"] = field(default_factory=dict)


def builtins_dir() -> Path:
    """Return the path to the built-in plugins directory shipped with termapy."""
    return Path(__file__).parent / "builtins" / "plugins"


def _clean_stale_pyc(folder: Path) -> None:
    """Remove orphaned .pyc files whose .py source no longer exists.

    Prevents stale bytecode from being loaded after a plugin file is
    deleted or renamed.
    """
    cache = folder / "__pycache__"
    if not cache.is_dir():
        return
    for pyc in cache.glob("*.pyc"):
        # PEP 3147: foo.cpython-311.pyc → foo.py
        stem = pyc.stem.split(".")[0]
        if not (folder / f"{stem}.py").exists():
            try:
                pyc.unlink()
            except OSError:
                pass
    # Remove __pycache__ if empty
    try:
        next(cache.iterdir())
    except (StopIteration, OSError):
        try:
            cache.rmdir()
        except OSError:
            pass


def load_plugins_from_dir(folder: Path, source: str = "global") -> LoadResult:
    """Discover and load plugin .py files from a directory.

    Each file may export a ``COMMAND`` (Command dataclass) and/or a
    ``TRANSFORM`` (Transform dataclass).  Files starting with '_' are
    skipped.

    Args:
        folder: Directory to scan for .py plugin files.
        source: Label for where the plugin came from (e.g. "global", config name).

    Returns:
        LoadResult with plugins, transforms, skipped file names, and error file names.
    """
    result = LoadResult()
    if not folder.is_dir():
        return result
    _clean_stale_pyc(folder)
    for py_file in sorted(folder.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        try:
            infos, xforms, dirs, hooks = _load_plugin_file(py_file, source)
            if infos:
                result.plugins.extend(infos)
            if xforms:
                result.transforms.extend(xforms)
            if dirs:
                result.directives.extend(dirs)
            if hooks:
                result.lifecycle_hooks.extend(hooks)
            if not infos and not xforms and not dirs and not hooks:
                result.skipped.append(py_file.name)
        # Plugin file being loaded is third-party code; its top-level
        # can raise anything (import errors, syntax, config reads).
        # Record the failure for reporting and keep loading the rest.
        except BoundaryException as e:
            result.errors.append(f"{py_file.name}: {e}")
    return result


def _load_plugin_file(
    path: Path,
    source: str,
) -> tuple[
    list[PluginInfo], list[TransformInfo], list[DirectiveInfo], list[LifecycleHook]
]:
    """Import a single plugin file and extract commands, transforms, directives, and hooks.

    A valid plugin module may export a ``COMMAND`` instance (a ``Command``
    dataclass), a ``TRANSFORM`` instance (a ``Transform`` dataclass),
    a ``DIRECTIVE`` instance (a ``Directive`` dataclass), and/or top-level
    lifecycle functions named in :data:`LIFECYCLE_HOOK_NAMES`.

    Args:
        path: Path to the .py plugin file.
        source: Label for the plugin's origin.

    Returns:
        Tuple of (PluginInfo list, TransformInfo list, DirectiveInfo list,
        LifecycleHook list).
    """
    # Derive the package name if this is a builtin plugin, so the module
    # is registered under both the dynamic name and the package path.
    # This prevents duplicate module state when app.py/cli.py imports
    # builtins via the package path (e.g. termapy.builtins.plugins.var).
    module_name = f"termapy_plugin_{path.stem}"
    pkg_name = None
    try:
        builtins_root = Path(__file__).parent / "builtins"
        rel = path.resolve().relative_to(builtins_root.resolve())
        parts = list(rel.parent.parts) + [rel.stem]
        pkg_name = "termapy.builtins." + ".".join(parts)
    except ValueError:
        pass

    # If already loaded via package import, reuse that module
    if pkg_name and pkg_name in sys.modules:
        mod = sys.modules[pkg_name]
    else:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            return [], [], [], []
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        if pkg_name:
            sys.modules[pkg_name] = mod
        spec.loader.exec_module(mod)

    # Commands
    plugins: list[PluginInfo] = []
    cmd = getattr(mod, "COMMAND", None)
    if isinstance(cmd, Command) and cmd.name:
        plugins = _flatten_command(cmd, prefix="", source=source)

    # Transforms
    transforms: list[TransformInfo] = []
    xform = getattr(mod, "TRANSFORM", None)
    if isinstance(xform, Transform) and xform.name:
        transforms.append(
            TransformInfo(
                name=xform.name,
                help=xform.help,
                repl=xform.repl,
                serial=xform.serial,
                source=source,
            )
        )

    # Directives
    directives: list[DirectiveInfo] = []
    directive = getattr(mod, "DIRECTIVE", None)
    if isinstance(directive, Directive) and directive.name:
        directives.append(
            DirectiveInfo(
                name=directive.name,
                help=directive.help,
                pattern=directive.pattern,
                handler=directive.handler,
                source=source,
            )
        )

    # Lifecycle hooks -- top-level functions named in LIFECYCLE_HOOK_NAMES
    lifecycle_hooks: list[LifecycleHook] = []
    for hook_name in LIFECYCLE_HOOK_NAMES:
        handler = getattr(mod, hook_name, None)
        if callable(handler):
            lifecycle_hooks.append(
                LifecycleHook(
                    name=hook_name,
                    handler=handler,
                    source=source,
                    plugin=path.stem,
                )
            )

    return plugins, transforms, directives, lifecycle_hooks


def _flatten_command(
    node: Command,
    prefix: str,
    source: str,
) -> list[PluginInfo]:
    """Recursively flatten a Command tree into PluginInfo entries.

    Each node in the tree becomes a PluginInfo. Interior nodes (those
    with ``sub_commands``) get a synthetic handler that lists their
    subcommands. Leaf nodes must have a ``handler`` callable.

    Each child declares its own ``needs`` independently.  When a parent
    is gated out of an environment by capabilities (e.g. ``/edit`` with
    ``needs.gui_apps=True``), children that should also be gated must
    declare the same need explicitly.  This keeps the gate local and
    auditable.

    Args:
        node: Command instance with name/help/handler/sub_commands.
        prefix: Dotted path prefix (empty for root).
        source: Plugin source label.

    Returns:
        List of PluginInfo for this node and all descendants.
    """
    name = node.name
    full_name = f"{prefix}.{name}".lower() if prefix else name.lower()
    sub_commands = node.sub_commands or {}
    children: list[str] = []
    result: list[PluginInfo] = []

    # Recurse into sub_commands first so we can build the children list
    for sub_name, sub_node in sub_commands.items():
        # Set name on sub-node so recursion works uniformly
        sub_node.name = sub_name
        child_infos = _flatten_command(sub_node, full_name, source)
        result.extend(child_infos)
        children.append(f"{full_name}.{sub_name}".lower())

    handler = node.handler
    if not handler and children:
        # Synthetic handler for interior nodes - lists subcommands
        handler = _make_interior_handler(full_name, children)

    if not handler:
        return result

    info = PluginInfo(
        name=full_name,
        args=node.args,
        help=node.help,
        long_help=node.long_help,
        handler=handler,
        source=source,
        children=children,
        raw_args=node.raw_args,
        flags=dict(node.flags),
        needs=node.needs,
        hidden=node.hidden,
    )
    result.insert(0, info)
    return result


def _make_interior_handler(
    full_name: str,
    children: list[str],
) -> Callable:
    """Create a synthetic handler for an interior command node.

    The handler lists available subcommands when the user invokes the
    interior node directly (e.g. ``/proto`` with no subcommand).

    Args:
        full_name: Dotted command path (e.g. "proto").
        children: Dotted names of direct subcommands.

    Returns:
        A handler callable with the standard (ctx, args) signature.
    """

    def _handler(ctx: PluginContext, args: str) -> None:
        prefix = ctx.engine.prefix
        ctx.write(f"Subcommands of {prefix}{full_name}:")
        plugins = ctx.engine.plugins
        for child_name in children:
            child = plugins.get(child_name)
            if child:
                arg_str = f" {child.args}" if child.args else ""
                help_text = interpolate_help(child.help, prefix)
                ctx.write(f"  {prefix}{child_name}{arg_str} - {help_text}")

    return _handler
