"""Command, transform, directive, and lifecycle dataclasses.

The shapes plugin authors fill in to declare a command:

  - ``Command`` -- the COMMAND export.  Name, args, help, handler,
    capability needs, optional sub_commands.
  - ``Transform`` -- the TRANSFORM export.  Rewrites REPL or serial input.
  - ``Directive`` -- the DIRECTIVE export.  Pre-dispatch line rewriter.

The shapes the loader produces from those declarations:

  - ``PluginInfo`` -- a flattened command (one per leaf node).
  - ``TransformInfo`` -- a Transform with source metadata.
  - ``DirectiveInfo`` -- a Directive with source metadata.
  - ``LifecycleHook`` -- a top-level lifecycle function discovered on a plugin module.

Plus the small return shapes:

  - ``CmdResult`` -- the value every handler returns.
  - ``DirectiveResult`` -- the value every directive returns.
  - ``LoadResult`` -- the bundle returned by ``load_plugins_from_dir``.

And a couple of small utilities:

  - ``interpolate_help`` -- ``{prefix}`` substitution in help strings.
  - ``resolve_long_help`` -- evaluate static or callable ``long_help``.

These shapes have no Textual or pyserial dependencies; they're pure data
plus a couple of helpers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Union

from termapy.plugins.capabilities import CapabilitySet
from termapy.plugins.params import (
    ParamSpec,
    synthesize_synopsis,
    validate_param_specs,
)

if TYPE_CHECKING:
    from termapy.plugins.context import PluginContext


# Type alias for the ``long_help`` field on Command and PluginInfo. A plugin
# can supply either a static string or a callable that receives the live
# PluginContext and returns a string. Callables let a command's DESCRIPTION
# section reflect runtime state (loaded files, current connection, cached
# counts) without having to wire up a custom render path.
#
# Uses the quoted string "PluginContext" because that class is defined in
# the sibling ``context`` module; this module avoids importing context to
# keep the import graph one-way.
LongHelp = Union[str, Callable[["PluginContext"], str]]


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


class UsageError(Exception):
    """Raise from a handler when the arguments don't match the synopsis.

    The dispatcher catches this and formats the canonical usage line from
    the command's *registered* name and argument declaration -- the same
    synopsis ``/help`` shows -- prefixed with the active REPL prefix.
    Handlers never hand-write ``"Usage: ..."`` strings: one owner
    (``format_usage``) keeps the error, ``/help``, and the configured
    prefix from ever disagreeing.

    Args:
        detail: Optional first line shown above the usage line, e.g.
            ``"Invalid count: 'xyz'"``.  Empty shows the usage line alone.

    Example::

        def _handler(ctx: PluginContext, args: str) -> CmdResult:
            if not args.strip():
                raise UsageError()
    """

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(detail)


def usage_synopsis(command: "Command | PluginInfo") -> str:
    """Return the argument synopsis for *command*.

    Params-declared commands synthesize it from their ``ParamSpec`` list
    (exactly what the dispatcher's parse-error path shows); hand-rolled
    commands use their ``args`` declaration verbatim.
    """
    if command.params:
        return synthesize_synopsis(command.params)
    return command.args


def format_usage(
    prefix: str, name: str, command: "Command | PluginInfo", detail: str = ""
) -> str:
    """Format the canonical usage message for *command*.

    Single owner for every ``Usage:`` line the dispatcher emits, so the
    rendered prefix and synopsis always match the registration (and
    therefore ``/help``).

    Args:
        prefix: Active REPL prefix (``ctx.prefix``), e.g. ``"/"``.
        name: Full registered (dotted) command name, e.g. ``"log.dump"``.
        command: The resolved Command whose synopsis to render.
        detail: Optional line shown above the usage line.

    Returns:
        ``"Usage: <prefix><name> <synopsis>"``, preceded by *detail* on
        its own line when given.
    """
    synopsis = usage_synopsis(command)
    line = f"Usage: {prefix}{name} {synopsis}".rstrip()
    return f"{detail}\n{line}" if detail else line


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
    def ok(cls, *, value: Any) -> "CmdResult":
        """Return a successful result with a value.

        ``value`` is REQUIRED -- pass ``value=""`` explicitly for
        truly side-effect commands that have nothing scriptable to
        return (e.g. ``/cls``, ``/exit``).  This forces every handler
        author to make a deliberate choice and lets the type checker
        catch silent gaps where a handler computes something but
        forgets to expose it via ``CmdResult.value``.

        ``value`` is typically a string (script-readable), but profile
        executors pass typed shapes (dict/list/number) that survive
        through to the MCP response.

        **Path values auto-resolve to absolute strings.** When ``value``
        is a ``pathlib.Path``, it is converted via ``str(value.resolve())``
        so callers can hand a Path directly and get a canonical,
        working-directory-independent string.  This means handlers can
        write ``CmdResult.ok(value=path)`` instead of repeatedly
        constructing ``str(path.resolve())`` at every call site.
        Multi-path values (newline-joined lists) still need explicit
        ``.resolve()`` per item -- the conversion only fires for a
        single Path.
        """
        from pathlib import Path

        if isinstance(value, Path):
            value = str(value.resolve())
        return cls(value=value)

    @classmethod
    def fail(cls, msg: str = "", *, value: Any = "") -> "CmdResult":
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
    params: list[ParamSpec] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate the parameter declaration at construction (== load) time.

        A broken ``params`` declaration should fail loudly when the plugin is
        imported/loaded, not at first dispatch.  ``params``-free commands
        (the default) skip all of this and are byte-identical to before.
        """
        if not self.params:
            return
        validate_param_specs(self.params, self.name)
        # args and params are two ways to declare the same thing -- pick one.
        if self.args:
            raise ValueError(
                f"/{self.name or '<command>'}: declare params OR a hand-written "
                f"args string, not both (args is synthesized from params)"
            )
        # NOTE: params ARE allowed on raw_args=True commands.  raw_args only
        # skips the $(VAR)/$(env) *transform* step (in dispatch_full, upstream);
        # it does not skip flag or param parsing, so a raw_args command can
        # declare params and still receive its values literally (untransformed)
        # -- which is exactly what raw_args wants.  The declarer is responsible
        # for a param-compatible grammar: a command whose literal args use bare
        # ``-flag`` tokens (e.g. /search's ``-term`` exclusion) must stay
        # hand-rolled, since flag parsing would consume them.


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
    params: list[ParamSpec] = field(default_factory=list)


def interpolate_help(text: str, prefix: str) -> str:
    """Substitute ``{prefix}`` in a help string with the live REPL prefix.

    Plugin authors write cross-references to other commands like
    ``"See {prefix}cfg.auto"`` instead of a hardcoded ``"See /cfg.auto"``
    so the rendered output honors a user's ``cmd_prefix`` override.
    Called by every help-rendering path (both short ``help=`` and
    long ``long_help=``) so the substitution is uniform.

    ``prefix`` is a plain string (usually ``ctx.prefix`` at the
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
    return interpolate_help(hp or "", ctx.prefix)


# Device commands live in ``active_profile.commands`` (dicts in the
# profile schema shape) and are rendered for /help and /search via
# ``termapy.profile.profile_command_view``.
