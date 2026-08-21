"""Built-in plugin: user-defined variables with $(NAME) syntax.

The command surface only.  The namespace itself -- storage, resolution, and
``$(NAME)`` expansion -- lives in :mod:`termapy.variables`, because the REPL
engine and every frontend need it and core must not import from ``builtins/``.
This file owns ``/var`` and its subcommands, the ``$(NAME) = value`` directive,
and the transform registration; it holds no state.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from termapy.help_dynamic import compose, green
from termapy.plugins import CmdResult, Command, Directive, DirectiveResult, Transform, UsageError
from termapy.variables import (
    clear_vars,
    context_vars,
    datetime_var_names,
    expand_vars,
    launch_var,
    launch_vars,
    resolve_datetime_var,
    set_user_var,
    snapshot as variables_snapshot,
    user_vars,
)

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

# Match $(NAME) = value assignment (with or without spaces around =)
_VAR_ASSIGN_RE = re.compile(r"^\$\(([A-Za-z_][A-Za-z0-9_]*)\)\s*=\s*(.+)$")

# Match $(NAME) <- value capture (run value as command, store result)
_VAR_CAPTURE_RE = re.compile(r"^\$\(([A-Za-z_][A-Za-z0-9_]*)\)\s*<-\s*(.+)$")

# Match bare $NAME = value (old syntax) for helpful warning
_BARE_ASSIGN_RE = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)\s*=\s*.*$")

# Match $(NAME) = (with no value) for error
_EMPTY_ASSIGN_RE = re.compile(r"^\$\(([A-Za-z_][A-Za-z0-9_]*)\)\s*=\s*$")

# Match $(NAME) <- (with no value) for error
_EMPTY_CAPTURE_RE = re.compile(r"^\$\(([A-Za-z_][A-Za-z0-9_]*)\)\s*<-\s*$")

# Match optional $(NAME) or bare NAME wrapper for user input stripping
_STRIP_WRAPPER_RE = re.compile(r"^\$\((.+)\)$")


def rewrite_assignment(line: str) -> str | None:
    """Rewrite ``$(VAR) = value`` into ``var.set VAR value``.

    Called very early in dispatch, before the REPL/serial decision.
    Returns the rewritten REPL command string, or None if the line
    is not a variable assignment.

    Args:
        line: Raw input line.

    Returns:
        Rewritten command for REPL dispatch, or None.
    """
    m = _VAR_ASSIGN_RE.match(line)
    if m:
        return f"var.set {m.group(1)} {m.group(2)}"
    return None


def rewrite_capture(line: str) -> str | None:
    """Rewrite ``$(VAR) <- command`` into ``var.capture VAR command``.

    The command may be a REPL command (starts with ``/``) or a device
    command (sent to serial).  The captured result becomes the variable's
    value.

    Args:
        line: Raw input line.

    Returns:
        Rewritten command for REPL dispatch, or None.
    """
    m = _VAR_CAPTURE_RE.match(line)
    if m:
        return f"var.capture {m.group(1)} {m.group(2)}"
    return None


def check_bare_dollar(line: str) -> str | None:
    """Check for bare ``$NAME = value`` and return a warning message.

    Args:
        line: Raw input line.

    Returns:
        Warning string if bare syntax detected, or None.
    """
    m = _BARE_ASSIGN_RE.match(line)
    if m:
        name = m.group(1)
        return f"Did you mean $({name}) = ...?  Variables use $(NAME) syntax."
    return None


# -- Handlers ----------------------------------------------------------------


def _handler_list(ctx: PluginContext, args: str) -> CmdResult:
    """List all defined variables, or show one by name.

    Args:
        ctx: Plugin context for output.
        args: Optional variable name to show.
    """
    raw = args.strip()
    m = _STRIP_WRAPPER_RE.match(raw)
    name = m.group(1) if m else raw
    users = user_vars()
    launches = launch_vars()
    contexts = context_vars()
    if name:
        val = users.get(name)
        if val is None:
            val = launch_var(name)
        if val is None:
            val = resolve_datetime_var(name, None)
        if val is None:
            ctx_fn = contexts.get(name)
            if ctx_fn is not None:
                val = ctx_fn()
        if val is not None:
            if not ctx.wants_data:
                ctx.io.output_markup(f"  [cyan]$({name})[/] = [green]{val}[/]")
            return CmdResult.ok(value=str(val))
        if not ctx.wants_data:
            ctx.io.output(f"  $({name}) - not defined", "red")
        return CmdResult.ok(value="")
    # Structured consumer: the resolved namespace snapshot, skipping the
    # markup listing entirely.  ``value`` stays the flat name=value lines
    # (the scriptable form) built from the same snapshot.
    if ctx.wants_data:
        snap = variables_snapshot()
        flat = [
            f"{name}={val}"
            for namespace in ("user", "launch", "datetime", "context")
            for name, val in sorted(snap[namespace].items())
        ]
        return CmdResult.ok(value="\n".join(flat), data=snap)
    lines: list[str] = []
    for k in sorted(users):
        ctx.io.output_markup(f"  [cyan]$({k})[/] = [green]{users[k]}[/]")
        lines.append(f"{k}={users[k]}")
    for k in sorted(launches):
        ctx.io.output_markup(f"  [cyan]$({k})[/] = [green]{launches[k]}[/]  [dim](launch)[/]")
        lines.append(f"{k}={launches[k]}")
    for k, tag in datetime_var_names():
        rendered = resolve_datetime_var(k, None) or ""
        ctx.io.output_markup(f"  [cyan]$({k})[/] = [green]{rendered}[/]  [dim]({tag})[/]")
        lines.append(f"{k}={rendered}")
    for k in sorted(contexts):
        rendered = contexts[k]()
        ctx.io.output_markup(f"  [cyan]$({k})[/] = [green]{rendered}[/]  [dim](context)[/]")
        lines.append(f"{k}={rendered}")
    return CmdResult.ok(value="\n".join(lines))


def _handler_set(ctx: PluginContext, args: str) -> CmdResult:
    """Set a user variable.

    Args:
        ctx: Plugin context for output.
        args: ``"NAME value"`` string (both required).
    """
    parts = args.strip().split(None, 1)
    if len(parts) < 2:
        raise UsageError()
    m = _STRIP_WRAPPER_RE.match(parts[0])
    name = m.group(1) if m else parts[0]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return CmdResult.fail(msg="Variable names must be letters, digits, or underscore")
    value = parts[1]
    set_user_var(name, value)
    ctx.io.output_markup(f"  [cyan]$({name})[/] = [green]{value}[/]")
    return CmdResult.ok(value=value)


def _handler_capture(ctx: PluginContext, args: str) -> CmdResult:
    """Capture command output into a user variable.

    If the command starts with the REPL prefix (e.g. ``/port.baud_rate``),
    dispatch it and use ``CmdResult.value``.  Otherwise treat it as a
    device command: send to serial and capture the response.

    Args:
        ctx: Plugin context.
        args: ``"NAME command"`` string.
    """
    parts = args.strip().split(None, 1)
    if len(parts) < 2:
        raise UsageError()
    m = _STRIP_WRAPPER_RE.match(parts[0])
    name = m.group(1) if m else parts[0]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return CmdResult.fail(msg="Variable names must be letters, digits, or underscore")
    cmd = parts[1]
    prefix = ctx.prefix
    if cmd.startswith(prefix):
        # REPL command: dispatch with .silent suffix to suppress terminal
        # output, then use CmdResult.value.  The user doesn't want to see
        # the inner command's output -- only the variable assignment.
        parts_cmd = cmd.split(None, 1)
        silent_cmd = parts_cmd[0] + ".silent"
        if len(parts_cmd) > 1:
            silent_cmd += " " + parts_cmd[1]
        result = ctx.dispatch(silent_cmd)
        if not result.success:
            # The inner dispatch already wrote the error via
            # ``_dispatch_inner``'s unconditional `self.write(err_msg)`
            # call (`.silent` only stubs the io write primitive, not
            # ``self.write``).  Returning the same error text again
            # via ``CmdResult.fail(msg=result.error)`` would make the
            # outer dispatch's same `self.write` line fire a second
            # time -- double-printing the error.  Suppress the outer
            # write by returning an empty msg; the variable is still
            # left unassigned (the user saw the error from the inner
            # dispatch and knows the capture aborted).
            return CmdResult.fail(msg="")
        value = result.value
    else:
        # Device command: send to serial and capture response
        if not ctx.serial.is_connected():
            return CmdResult.fail(msg="Not connected.")
        encoding = ctx.cfg.get("encoding", "utf-8")
        with ctx.serial.io():
            ctx.serial.drain()
            ctx.serial.send(cmd)
            response = ctx.serial.read_raw(timeout_ms=1000)
        if not response:
            return CmdResult.fail(msg=f"No response from device: {cmd}")
        value = response.decode(encoding, errors="replace").strip()
    set_user_var(name, value)
    ctx.io.output_markup(f"  [cyan]$({name})[/] = [green]{value}[/]")
    return CmdResult.ok(value=value)


def _handler_clear(ctx: PluginContext, args: str) -> CmdResult:
    """Clear all user variables.

    Args:
        ctx: Plugin context for output.
        args: Unused.
    """
    count = clear_vars()
    ctx.io.output(f"Cleared {count} variable(s).", "green")
    return CmdResult.ok(value=str(count))


# ── Dynamic long_help ─────────────────────────────────────────────────────────


def _var_state_line(ctx: PluginContext) -> str:
    """Green one-liner: current user + launch + context var counts."""
    # ctx is unused -- vars live in termapy.variables' module-level storage,
    # same across all PluginContexts within a session. Signature matches the
    # dynamic long_help contract.
    _ = ctx
    user = len(user_vars())
    dt = len(datetime_var_names())
    ctxv = len(context_vars())
    launch = len(launch_vars())
    return green(
        f"Currently defined: {user} user, {dt} datetime, "
        f"{ctxv} context, {launch} launch"
    )


_VAR_PROSE = """\
User-defined variables use $(NAME) syntax (case-sensitive).

Setting variables (no {prefix} prefix needed):
  $(PORT) = COM7                - store literal value
  $(BAUD) <- {prefix}port.baud_rate    - capture REPL command output
  $(TEMP) <- AT+TEMP            - capture device response

Using variables in commands:
  {prefix}print $(PORT)
  AT+PORT=$(PORT)

Commands:
  {prefix}var                   - list all variables
  {prefix}var PORT              - show one variable (bare name or $(PORT))
  {prefix}var.list              - list all variables (alias of bare {prefix}var)
  {prefix}var.set PORT val      - set a variable (literal string)
  {prefix}var.capture NAME cmd  - run cmd and store its result as NAME
  {prefix}var.clear             - clear all variables

Dynamic variables (current clock at point of use):
  $(DATE)              - current date (YYYY-MM-DD)
  $(TIME)              - current time (HH:MM:SS)
  $(DATETIME)          - current date and time

Custom format (any datetime variable, strftime after a colon):
  $(DATETIME:%Y%m%d_%H%M%S)  - filename-safe stamp (colon-free)
  $(TIME:%H%M)               - hour+minute only
  $(SESSION_DATE:%d-%b)      - works on frozen vars too

Launch variables (frozen when the app starts):
  $(LAUNCH_DATE)       - app start date
  $(LAUNCH_TIME)       - app start time
  $(LAUNCH_DATETIME)   - app start date and time

Session variables (set once per script launch, frozen):
  $(SESSION_DATE)      - script start date
  $(SESSION_TIME)      - script start time
  $(SESSION_DATETIME)  - script start date and time

Config variables (resolved paths from current config):
  $(CFG)               - config name (e.g. demo)
  $(CFG.DIR)           - config directory (resolved)
  $(CFG.FILE)          - config file path (resolved)
  $(CFG.LOG_FILE)      - log file path (resolved)
  $(CFG.RUN_DIR)       - scripts directory
  $(CFG.PROTO_DIR)     - protocol test directory
  $(CFG.PLUGIN_DIR)    - plugin directory
  $(CFG.SS_DIR)        - screenshot directory
  $(CFG.CAP_DIR)       - capture directory
  $(CFG.PROF_DIR)      - profile directory
  $(CFG.VIZ_DIR)       - visualizer directory
  $(CFG.PORT)          - serial port name
  $(CFG.BAUD)          - baud rate
  $(CFG.PORT_CFG)      - port config (e.g. 9600 8N1)
  $(CFG.PORT_FULL)     - full connection (e.g. COM4 9600 8N1)

Escaping (when your data contains literal $):
  \\$(PORT)           - literal $(PORT) (not expanded)
  {prefix}raw $(GPS),NMEA,0 - send entire line verbatim (no expansion)

Scope: variables persist for the session. They are cleared
automatically when a script is launched from the Scripts button
or the Run menu, but NOT when {prefix}run is typed interactively.
Use {prefix}var.clear to reset manually."""


def _var_long_help(ctx: PluginContext) -> str:
    return compose(_var_state_line(ctx), _VAR_PROSE)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="var",
    args="{name}",
    help="List user variables, or show one by name.",
    long_help=_var_long_help,
    handler=_handler_list,
    raw_args=True,
    sub_commands={
        "list": Command(
            args="{name}",
            help="Explicit alias for bare /var.",
            long_help=_var_state_line,
            handler=_handler_list,
            raw_args=True,
        ),
        "set": Command(
            args="<NAME> <value>",
            help="Set a user variable to a literal value.",
            long_help=_var_state_line,
            handler=_handler_set,
            raw_args=True,
        ),
        "capture": Command(
            args="<NAME> <command>",
            help="Capture command output into a user variable.",
            long_help=_var_state_line,
            handler=_handler_capture,
            raw_args=True,
        ),
        "clear": Command(
            help="Clear all user variables.",
            long_help=_var_state_line,
            handler=_handler_clear,
            raw_args=True,
        ),
    },
)

TRANSFORM = Transform(
    name="var",
    help="Expand $(NAME) placeholders from user-defined variables.",
    repl=expand_vars,
    serial=expand_vars,
)


def _directive_var_assign(line: str) -> DirectiveResult | None:
    """Handle $(VAR) = value, $(VAR) <- command, and related syntax errors."""
    # Capture syntax: $(VAR) <- command
    captured = rewrite_capture(line)
    if captured is not None:
        return DirectiveResult("rewrite", captured)
    m = _EMPTY_CAPTURE_RE.match(line)
    if m:
        return DirectiveResult("error", f"$({m.group(1)}) <- requires a command.")
    # Literal assignment: $(VAR) = value
    rewritten = rewrite_assignment(line)
    if rewritten is not None:
        return DirectiveResult("rewrite", rewritten)
    m = _EMPTY_ASSIGN_RE.match(line)
    if m:
        return DirectiveResult("error", f"$({m.group(1)}) = requires a value.")
    warning = check_bare_dollar(line)
    if warning is not None:
        return DirectiveResult("warn", warning)
    return None


DIRECTIVE = Directive(
    name="var_assign",
    help="Assign user variables with $(NAME) = value syntax.",
    pattern="$(NAME) = value",
    handler=_directive_var_assign,
)
