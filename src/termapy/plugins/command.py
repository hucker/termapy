"""Command, transform, directive, and lifecycle dataclasses.

The shapes plugin authors fill in to declare a command:

  - ``Command`` -- the COMMAND export.  Name, args, help, handler,
    capability needs, optional sub_commands.
  - ``Transform`` -- the TRANSFORM export.  Rewrites REPL or serial input.
  - ``Directive`` -- the DIRECTIVE export.  Pre-dispatch line rewriter.

A plugin may also export a symbol-map converter as four top-level names
(``FORMAT`` / ``DESCRIPTION`` / ``DETECT`` / ``convert``), the shape the
built-in converters use; that one has no dataclass here because it is a
function plus two strings.  See ``termapy.symbols.converters``.

The shapes the loader produces from those declarations:

  - ``PluginInfo`` -- a flattened command (one per leaf node).
  - ``TransformInfo`` -- a Transform with source metadata.
  - ``DirectiveInfo`` -- a Directive with source metadata.
  - ``LifecycleHook`` -- a top-level lifecycle function discovered on a plugin module.

Plus the small return shapes:

  - ``CmdResult`` -- the value every handler returns.
  - ``DirectiveResult`` -- the value every directive returns.
  - ``LoadResult`` -- the bundle returned by ``load_plugins_from_dir``,
    one ``ModuleLoad`` per plugin file.

And a few small utilities:

  - ``interpolate_help`` -- ``{prefix}`` substitution in help strings.
  - ``resolve_long_help`` -- evaluate static or callable ``long_help``.
  - ``synopsis_pieces`` / ``synopsis_markup`` -- a synopsis colored by its
    grammar, and ``canonical_flags`` -- a ``flags`` mapping as rows; what
    ``/help`` and ``/search`` render a declaration with.

These shapes have no Textual or pyserial dependencies; they're pure data
plus a couple of helpers.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from termapy.folders import FOLDER_NAMES
from termapy.plugins.capabilities import CapabilitySet
from termapy.plugins.params import (
    ParamSpec,
    synthesize_synopsis,
    validate_param_specs,
)
from termapy.plugins.views import (
    Column,
    Item,
    Line,
    Piece,
    RecordView,
    TableView,
    TextLine,
    pieces_markup,
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
LongHelp = str | Callable[["PluginContext"], str]


# The pickers a host's ``ctx.internal.open_picker`` opens -- each one the
# dialog behind a button (Cfg, Run, Proto; the port name for ``port``; the
# config picker's New, the Quick Setup wizard, for ``setup``).  The
# vocabulary of ``Command.picker``; the TUI opening every one of them is
# pinned by a Pilot test.
PICKERS: tuple[str, ...] = ("cfg", "port", "proto", "run", "setup")


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


def usage_synopsis(command: Command | PluginInfo) -> str:
    """Return the argument synopsis for *command*.

    Params-declared commands synthesize it from their ``ParamSpec`` list
    (exactly what the dispatcher's parse-error path shows); hand-rolled
    commands use their ``args`` declaration verbatim.
    """
    if command.params:
        return synthesize_synopsis(command.params)
    return command.args


def format_usage(prefix: str, name: str, command: Command | PluginInfo, detail: str = "") -> str:
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


def validate_synopsis(args: str) -> str:
    """Check an args synopsis against the project grammar.

    The grammar (see CLAUDE.md Conventions): ``""`` = no args,
    ``{braces}`` = optional, ``<angle>`` = required, tight alternation
    (``{on|off}``, never ``{on | off}``), nesting allowed
    (``{algo{_le|_be}{_ascii}}``).  Square brackets are not part of the
    grammar, and a ``{{`` sequence is an f-string escape artifact that
    would render literally in /help.

    Returns:
        ``""`` when valid, else a short description of the first problem
        (callers prepend the command name).
    """
    if "[" in args or "]" in args:
        return "square brackets are not synopsis grammar (use {braces}/<angle>)"
    if "{{" in args:
        return "doubled '{{' renders literally (f-string escape artifact?)"
    if " | " in args:
        return "spaced alternation; write {a|b}, not {a | b}"
    for open_ch, close_ch, label in (("{", "}", "braces"), ("<", ">", "angles")):
        depth = 0
        for ch in args:
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth < 0:
                    return f"unbalanced {label}"
        if depth != 0:
            return f"unbalanced {label}"
    return ""


# ── Groups ───────────────────────────────────────────────────────────────────
#
# A command declares what it is FOR, and ``/help`` lays itself out from the
# declarations (registry ``help-group-declared``).  The vocabulary is open:
# these five are what the built-ins use, not a permitted list, so a plugin
# may declare a group of its own and get its own row, or join one of these
# and sit among the built-ins that declared it.  Nothing about a command's
# origin decides where it appears.

# The group shown in full; every other group is one folded row.
GROUP_TERMINAL = "terminal"

# What a command that declares no group belongs to.
GROUP_DEFAULT = "plugins"

# The groups the built-ins use.  Not a whitelist -- ``/help`` reads the
# groups the loaded commands actually declare -- but the names to reach
# for first, so related commands land together instead of near-missing.
GROUPS = (GROUP_TERMINAL, "protocol", "devices", "advanced", GROUP_DEFAULT)

# How settled a command is (registry ``maturity-declared-once``).  A
# command declares one, its subcommands inherit it from the nearest
# ancestor that declares one, and any of them may override; an
# undeclared root is stable.  The words follow Node.js's stability index
# and Python's PEP 411 provisional packages: anything not stable is
# outside termapy's compatibility promise.  ``/help`` shows the level in
# its color, the MCP catalog carries it, and a prototype family notes
# itself once a session when it first runs.
MATURITY_LEVELS = ("stable", "preview", "prototype")

# The color each level is shown in.
MATURITY_COLORS = {"stable": "green", "preview": "yellow", "prototype": "red"}

# What each level promises, in the words /help and the MCP catalog use.
MATURITY_MEANINGS = {
    "stable": "covered by termapy's compatibility promise",
    "preview": "works, but may change in any release, outside the compatibility promise",
    "prototype": "new and incomplete: may change or be removed in any release",
}


def validate_group(group: str) -> str:
    """Check a group name's shape; membership is not checked, the vocabulary is open.

    A group is a bare lowercase word: letters, digits, ``-`` and ``_``.
    The shape is checked so a name cannot collide with ``/help``'s own
    syntax or render oddly; the NAME is not checked against ``GROUPS``,
    because a plugin declaring a group of its own is the point.

    Returns:
        ``""`` when valid, else a short description of the problem
        (callers prepend the command name).
    """
    if not group:
        return "empty group name"
    if group != group.strip():
        return "surrounding whitespace"
    if any(ch.isspace() for ch in group):
        return "a group name is one word (no spaces)"
    if not all(ch.isalnum() or ch in "-_" for ch in group):
        return "letters, digits, '-' and '_' only"
    if group != group.lower():
        return "lowercase only"
    return ""


# ── Rendering a declaration ──────────────────────────────────────────────────
#
# A synopsis and a flags mapping are shown in two places, ``/help``'s page
# and ``/search``'s hits, so the grammar ``validate_synopsis`` checks is
# colored here, once, beside it.

# The colors a synopsis renders in, wherever one is shown.
SYNOPSIS_OPTIONAL_STYLE = "green"  # {optional} groups
SYNOPSIS_REQUIRED_STYLE = "yellow"  # <required> groups

_OPTIONAL_RE = re.compile(r"(\{[^}]+\})")
_REQUIRED_RE = re.compile(r"(<[^>]+>)")


def synopsis_pieces(args: str) -> list[Piece]:
    """A synopsis as pieces: ``{optional}`` groups green, ``<required>`` yellow, the rest plain.

    A ``<required>`` inside an ``{optional}`` group (``{timeout=<dur>}``)
    is yellow within the green, as the nested tags always drew it.
    """
    pieces: list[Piece] = []
    for group in _OPTIONAL_RE.split(args):
        outer = SYNOPSIS_OPTIONAL_STYLE if _OPTIONAL_RE.fullmatch(group) else ""
        pieces += [
            Piece(part, None, SYNOPSIS_REQUIRED_STYLE if _REQUIRED_RE.fullmatch(part) else outer)
            for part in _REQUIRED_RE.split(group)
            if part
        ]
    return pieces


def synopsis_markup(args: str) -> str:
    """A synopsis, escaped, with its ``{optional}`` and ``<required>`` groups colored."""
    return pieces_markup(synopsis_pieces(args))


def canonical_flags(flags: Mapping[str, str]) -> list[tuple[str, list[str], str]]:
    """A ``Command.flags`` mapping as one ``(canonical, aliases, description)`` row per flag.

    Sorted by canonical name.  An alias entry (``"-t": "--table"``)
    collapses onto its canonical row.
    """
    if not flags:
        return []
    aliases_of: dict[str, list[str]] = {}
    descriptions: dict[str, str] = {}
    for flag, val in flags.items():
        if val.startswith("-") and val in flags:
            aliases_of.setdefault(val, []).append(flag)
        else:
            descriptions[flag] = val
            aliases_of.setdefault(flag, [])
    return [(flag, sorted(aliases_of[flag]), descriptions[flag]) for flag in sorted(descriptions)]


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
    # ``value`` is the scriptable SCALAR: what ``$(X) <- cmd`` captures and
    # what quiet-mode scripts read.  Keep it a string (or number) -- shaped
    # structures belong in ``data``.  (Historically profile executors put
    # dicts here; that traffic moved to ``data`` when the MCP envelope was
    # unified.)
    value: Any = ""
    # ``data`` is the STRUCTURED view of the same result: a JSON-shaped
    # dict/list/scalar for agent consumers (MCP, ``--json``).  ``None``
    # means "this command has no structured form" -- a legitimate permanent
    # state for side-effect and prose-page commands.  Never put a prose
    # string here; that is what ``value``/``output_lines`` are for.
    # A handler that hand-renders a large listing should build EITHER the
    # prose OR ``data``, branching on ``ctx.wants_data``, so neither
    # audience pays for the other's rendering -- or, better, return
    # ``CmdResult.table(...)`` and let dispatch choose.
    data: Any = None
    # ``view`` is how ``data`` reads as prose (a ``views.TableView`` or
    # ``views.RecordView``):
    # dispatch's exit renders it on the output channel when the caller
    # did not ask for data, and strips it either way -- a view never
    # leaves dispatch, and no envelope or MCP response carries one.
    view: Any = None

    @classmethod
    def table(
        cls,
        title: str,
        noun: str,
        columns: Sequence[Column],
        records: Sequence[dict[str, Any]],
        *,
        value: Any,
        data: dict[str, Any] | None = None,
    ) -> CmdResult:
        """A list answer: ``records`` as ``data``, their table as the view.

        The handler renders nothing; dispatch prints the table, or hands
        the records to a caller that wants data (``--json``,
        ``/term.request``, MCP).  Registry ``list-one-format``.

        Args:
            title: What is listed (``run/``).
            noun: Singular noun the count is in (``script``).
            columns: The columns the prose shows, left to right.
            records: One raw-valued dict per row, in display order.
            value: The scriptable scalar (usually the newline-joined names).
            data: A name-to-value mapping to answer with instead of the
                records, when each row is one value under a unique name
                (``/env.list``): a consumer looks a name up rather than
                searching.  The table still shows the records.
        """
        rows = tuple(records)
        return cls(
            value=value,
            data=list(rows) if data is None else data,
            view=TableView(title, noun, tuple(columns), rows),
        )

    @classmethod
    def record(
        cls,
        data: Any,
        rows: Sequence[Item],
        *,
        value: Any,
        title: Sequence[Line] = (),
        notes: Sequence[Line] = (),
    ) -> CmdResult:
        """A status answer: ``data`` with real JSON types, ``rows`` as its view.

        The handler renders nothing; dispatch prints the aligned rows, or
        hands ``data`` alone to a caller that wants data (``--json``,
        ``/term.request``, MCP), so the facts never ride twice.  Registry
        ``record-one-format``, the single-record twin of ``table``.

        Args:
            data: The facts, JSON-shaped (ints, bools, None, lists), with
                the same keys a script or agent will read -- one dict, or
                a list of them for a page per item (``/port.chip *``).
            rows: The prose twin: ``(label, text)`` or ``(label, text,
                color)`` rows, ``views.TextLine`` plain lines, and ``None``
                for a blank line; each run of rows aligns on its own.
                Leave a row out to omit it.
            value: The scriptable scalar.
            title: ``(text, color)`` lines before the rows.
            notes: ``(text, color)`` lines after the rows.
        """
        items = (
            *(TextLine(text, color) for text, color in title),
            *rows,
            *(TextLine(text, color) for text, color in notes),
        )
        return cls(value=value, data=data, view=RecordView(items))

    @classmethod
    def ok(cls, *, value: Any, data: Any = None) -> CmdResult:
        """Return a successful result with a value.

        ``value`` is REQUIRED -- pass ``value=""`` explicitly for
        truly side-effect commands that have nothing scriptable to
        return (e.g. ``/cls``, ``/exit``).  This forces every handler
        author to make a deliberate choice and lets the type checker
        catch silent gaps where a handler computes something but
        forgets to expose it via ``CmdResult.value``.

        ``value`` is the scriptable scalar (script-readable text or a
        number).  ``data`` is the optional structured twin -- JSON-shaped,
        for agent consumers; see the field comment for the contract.

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
        return cls(value=value, data=data)

    @classmethod
    def fail(cls, msg: str = "", *, value: Any = "", data: Any = None) -> CmdResult:
        """Return a failure result with an error message.

        ``value`` is optional; profile executors set it on parse-failure
        so the LLM still sees the raw response text alongside the error.
        ``data`` is optional structured detail about the failure (same
        contract as on success -- never a prose string).
        """
        return cls(success=False, error=msg, value=value, data=data)

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
        bare_sub: The subcommand a BARE invocation runs instead of the
            handler (or the children listing) -- ``/mem`` with
            ``bare_sub="info"`` runs ``/mem.info`` (the bare-queries
            convention: bare shows state).  With ``picker=`` it is the
            fallback where the host has no picker: ``/run`` runs
            ``/run.list`` in the CLI.  Must name a declared subcommand;
            the loader fails loud otherwise.
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
        safety: Safety tier, the profile vocabulary -- ``safe`` (default),
            ``readonly``, ``mutable``, ``destructive``.  Over MCP a
            destructive command needs ``confirm=true`` exactly like a
            destructive profile entry; the TUI and CLI never gate (the
            human is at the keyboard).  Validated at construction.
        files: The ``folders.FOLDERS`` folder (``"run"``, ``"proto"``,
            ...) whose files the FIRST argument names, so frontends can
            suggest them as the user types.  ``""`` (default) means the
            first argument is not a data-folder file.  Validated at load.
        cfg: The config keys the command depends on, each with how it
            uses the value: ``{"eol": "appended to each sent line"}``.
            A key is a path as ``defaults.cfg_key_paths`` spells it
            (``serial.baud_rate``); a bare leaf of a nested key is
            resolved.  ``/help`` renders them as the page's CONFIG
            section with their live values, ``/help <key>``, the config
            editor and ``/search`` say which commands use a key, and the
            MCP catalog carries them, so no long_help has to say what a
            key does to the command or where a change lasts (registry
            ``cfg-declared``).  An unknown key or an empty phrase fails
            at construction.
        picker: The picker (one of ``PICKERS``) a BARE invocation opens
            where the host has pickers (the TUI) -- the same dialog its
            button opens.  The dispatcher opens it instead of calling the
            handler, which then sees a bare call only in hosts without
            pickers (CLI, MCP).  ``/help`` renders the bare form from this
            and from ``bare_sub``.  Validated at load.
        help_list: The subcommand whose table ends this command's
            ``/help`` page -- ``/help cfg`` shows the configs
            (``help_list="list"``), each name clickable as in the list.
            Must name a declared subcommand that takes no arguments; the
            loader fails loud otherwise.
        group: What the command is FOR, which is how ``/help`` lays
            itself out: ``GROUP_TERMINAL`` is shown in full and every
            other group folds to one clickable row (registry
            ``help-group-declared``).  Declared on a ROOT command only;
            subcommands inherit it.  ``GROUPS`` are the names the
            built-ins use, but the vocabulary is OPEN -- a plugin may
            declare one of its own, or declare ``terminal`` and appear
            among the built-ins that did.  Only the shape is validated.
            Defaults to ``GROUP_DEFAULT``.
        maturity: How settled the command is, one of ``MATURITY_LEVELS``:
            ``"stable"``, ``"preview"`` or ``"prototype"``.  ``""`` (default)
            inherits from the nearest ancestor that declares one, and a root
            that declares none is stable; any subcommand may override.
            Validated at construction (registry ``maturity-declared-once``).
        shows: True when the command only shows or opens something -- a
            page, a list, a lookup, a file in a viewer or an editor -- and
            changes nothing in the session, the files or the device.  A
            click on a link to it runs it at once; a link to any other
            command fills the input line for Enter (registry
            ``links-are-data``).  Not inherited: each command says so for
            itself.  Requires ``safety="safe"``, checked at construction.
    """

    help: str
    name: str = ""
    args: str = ""
    long_help: LongHelp = ""
    handler: Callable | None = None
    sub_commands: dict[str, Command] | None = None
    bare_sub: str = ""
    raw_args: bool = False
    flags: dict[str, str] = field(default_factory=dict)
    needs: CapabilitySet = field(default_factory=CapabilitySet)
    hidden: bool = False
    params: list[ParamSpec] = field(default_factory=list)
    safety: str = "safe"
    files: str = ""
    cfg: dict[str, str] = field(default_factory=dict)
    picker: str = ""
    help_list: str = ""
    group: str = GROUP_DEFAULT
    maturity: str = ""
    shows: bool = False

    def __post_init__(self) -> None:
        """Validate the declaration at construction (== load) time.

        A broken ``params`` declaration should fail loudly when the plugin is
        imported/loaded, not at first dispatch.  ``params``-free commands
        (the default) skip all of this and are byte-identical to before.
        """
        # Lazy: the profile package is a leaf, but this module is imported
        # by everything, so keep the dependency off the import path.
        from termapy.profile.loader import SAFETY_TIERS

        if self.safety not in SAFETY_TIERS:
            raise ValueError(
                f"/{self.name or '<command>'}: unknown safety tier {self.safety!r} "
                f"(use {'/'.join(SAFETY_TIERS)})"
            )
        group_error = validate_group(self.group)
        if group_error:
            raise ValueError(
                f"/{self.name or '<command>'}: invalid group {self.group!r}: {group_error}"
            )
        if self.maturity and self.maturity not in MATURITY_LEVELS:
            raise ValueError(
                f"/{self.name or '<command>'}: unknown maturity {self.maturity!r} "
                f"(use {'/'.join(MATURITY_LEVELS)})"
            )
        if self.shows and self.safety != "safe":
            raise ValueError(
                f"/{self.name or '<command>'}: shows= is for a command that changes "
                f"nothing, and safety {self.safety!r} says this one does"
            )
        self.cfg = validate_cfg_uses(self.cfg, self.name)
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

    - ``on_app_prestart`` - fires once before ``on_app_start``, after the
                            frontend's own pre-start work (the TUI's session
                            log opens), for what must run before the session
                            starts.  Built-ins only: folder plugins load
                            during ``on_app_start``, so the engine skips a
                            folder plugin's ``on_app_prestart`` with a warning.
    - ``on_app_start``    - fires once after plugins are loaded and the
                            context is wired, before first dispatch and
                            before any startup auto-connect (every frontend).
    - ``on_app_stop``     - fires once during graceful shutdown.  Not
                            guaranteed on crash.
    - ``on_connect``      - fires after the serial port is successfully opened.
    - ``on_disconnect``   - fires before the serial port is closed (user-initiated).
    - ``on_config_load``  - fires after switching to a new config via ``/cfg.load``.
    - ``on_script_start`` - fires when a script begins executing.
    - ``on_script_stop``  - fires after a script finishes, including on
                            ``/stop`` or exception.  Mirrors ``on_script_start``.

    Core listeners (symbol auto-load from ``<cfg>.symbols.json``) run inside
    ``ReplEngine.fire_lifecycle`` BEFORE plugin hooks for ``on_app_start``
    and ``on_config_load``, so a hook already sees ``ctx.ns("symbols")``.

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
    "on_app_prestart",
    "on_app_start",
    "on_app_stop",
    "on_connect",
    "on_disconnect",
    "on_config_load",
    "on_script_start",
    "on_script_stop",
)


@dataclass
class ModuleLoad:
    """What one plugin file produced: its exports, or why it has none.

    Attributes:
        file: The file name (``traffic.py``).
        plugins: Its PluginInfo entries (one per command node).
        transforms: Its TransformInfo entries.
        directives: Its DirectiveInfo entries.
        lifecycle_hooks: Its LifecycleHook entries.
        converters: Its ConverterSpec entries (``termapy.converters``).
        error: Why the file failed to load; ``""`` when it loaded.
    """

    file: str
    plugins: list = field(default_factory=list)
    transforms: list = field(default_factory=list)
    directives: list = field(default_factory=list)
    lifecycle_hooks: list[LifecycleHook] = field(default_factory=list)
    converters: list = field(default_factory=list)
    error: str = ""

    @property
    def exported(self) -> bool:
        """True when the file exported anything the loader recognizes."""
        return bool(
            self.plugins
            or self.transforms
            or self.directives
            or self.lifecycle_hooks
            or self.converters
        )


@dataclass
class LoadResult:
    """Result of loading plugins from a directory: one ``ModuleLoad`` per file.

    The per-kind lists are views across every file, in file order, for
    callers that register everything regardless of where it came from;
    the load report reads ``modules`` to name each file.

    Attributes:
        modules: Every plugin file seen, in file order, loaded or not.
    """

    modules: list[ModuleLoad] = field(default_factory=list)

    def _every(self, kind: str) -> list:
        return [item for module in self.modules for item in getattr(module, kind)]

    @property
    def plugins(self) -> list:
        """Successfully loaded PluginInfo entries."""
        return self._every("plugins")

    @property
    def transforms(self) -> list:
        """Successfully loaded TransformInfo entries."""
        return self._every("transforms")

    @property
    def directives(self) -> list:
        """Successfully loaded DirectiveInfo entries."""
        return self._every("directives")

    @property
    def lifecycle_hooks(self) -> list[LifecycleHook]:
        """LifecycleHook entries discovered on plugin modules."""
        return self._every("lifecycle_hooks")

    @property
    def converters(self) -> list:
        """ConverterSpec entries discovered on plugin modules."""
        return self._every("converters")

    @property
    def skipped(self) -> list[str]:
        """File names that loaded but exported nothing."""
        return [module.file for module in self.modules if not module.error and not module.exported]

    @property
    def errors(self) -> list[str]:
        """``"<file>: <problem>"`` for each file that raised during loading."""
        return [f"{module.file}: {module.error}" for module in self.modules if module.error]


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
        safety: Safety tier inherited from ``Command.safety``.
        files: Data folder the first argument's file lives in, inherited
            from ``Command.files``; ``""`` when it names no data-folder file.
        cfg: The config keys it depends on, each path to how it uses the
            value, inherited from ``Command.cfg`` (registry ``cfg-declared``).
        picker: Picker a bare invocation opens in a host with pickers,
            inherited from ``Command.picker``.
        bare_sub: Subcommand a bare invocation runs (after ``picker``,
            where the host has none), inherited from ``Command.bare_sub``.
        help_list: Subcommand whose table ends the ``/help`` page,
            inherited from ``Command.help_list``.
        group: What the command is for, inherited from ``Command.group``
            -- on a subcommand, from its ROOT, so a family stays
            together however deep it nests.  ``/help`` lays the landscape
            out from these (registry ``help-group-declared``).
        maturity: How settled the command is, resolved at load: its own
            ``Command.maturity``, else the nearest ancestor's, else
            ``"stable"`` (registry ``maturity-declared-once``).
        shows: ``Command.shows``: it only shows or opens, so a click on a
            link to it runs it.
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
    safety: str = "safe"
    files: str = ""
    cfg: dict[str, str] = field(default_factory=dict)
    picker: str = ""
    bare_sub: str = ""
    help_list: str = ""
    group: str = GROUP_DEFAULT
    maturity: str = "stable"
    shows: bool = False

    def __post_init__(self) -> None:
        """Validate the args synopsis at registration time.

        Every command -- builtins via the loader, host commands via
        ``register_hook`` -- flows through PluginInfo construction, so this
        is the single chokepoint where malformed synopsis data fails loud
        instead of rendering wrong in ``/help`` and usage lines (e.g. an
        f-string escape artifact like ``"{{filename}}"`` displaying its
        literal doubled braces).  User plugins with a bad synopsis are
        rejected by the loader's per-plugin error handling, same as any
        other invalid COMMAND declaration.
        """
        error = validate_synopsis(self.args)
        if error:
            raise ValueError(f"/{self.name}: invalid args synopsis {self.args!r}: {error}")
        if self.files and self.files not in FOLDER_NAMES:
            raise ValueError(
                f"/{self.name}: unknown files folder {self.files!r} (use {', '.join(FOLDER_NAMES)})"
            )
        if self.picker and self.picker not in PICKERS:
            raise ValueError(
                f"/{self.name}: unknown picker {self.picker!r} (use {', '.join(PICKERS)})"
            )
        if self.maturity not in MATURITY_LEVELS:
            raise ValueError(
                f"/{self.name}: unknown maturity {self.maturity!r} "
                f"(use {'/'.join(MATURITY_LEVELS)})"
            )
        group_error = validate_group(self.group)
        if group_error:
            raise ValueError(f"/{self.name}: invalid group {self.group!r}: {group_error}")
        self.cfg = validate_cfg_uses(self.cfg, self.name)


def validate_cfg_uses(cfg: Mapping[str, str], name: str) -> dict[str, str]:
    """A ``cfg=`` declaration with every key resolved to its path; ``ValueError`` names the first bad entry.

    The one check for ``Command`` and ``PluginInfo`` alike: a key
    ``defaults.cfg_key_path`` cannot place (a typo, a key that is no
    longer in ``DEFAULT_CFG``) and a phrase that says nothing both fail
    at load, as a bad synopsis does, rather than rendering a wrong page.
    """
    # Lazy as the other validators' imports are: defaults is pure data,
    # but this module is imported by everything.
    from termapy.defaults import cfg_key_path

    resolved: dict[str, str] = {}
    for key, use in cfg.items():
        path = cfg_key_path(key)
        if path is None:
            raise ValueError(f"/{name or '<command>'}: unknown config key {key!r} in cfg=")
        if not isinstance(use, str) or not use.strip():
            raise ValueError(f"/{name or '<command>'}: cfg= must say how {key!r} is used")
        resolved[path] = use.strip()
    return resolved


def cfg_users(plugins: Mapping[str, PluginInfo]) -> dict[str, list[tuple[str, str]]]:
    """Key path -> ``(command name, how it uses the key)`` for every visible command that declared it, names sorted.

    The reverse of the declarations, read by ``/help <key>``, the config
    editor's help line and anything else that answers "who uses this
    key" (registry ``cfg-declared``).  Hidden commands (legacy aliases)
    are left out as every discovery surface leaves them out.
    """
    users: dict[str, list[tuple[str, str]]] = {}
    for name in sorted(plugins):
        info = plugins[name]
        if info.hidden:
            continue
        for path, use in info.cfg.items():
            users.setdefault(path, []).append((name, use))
    return users


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


def resolve_long_help(plugin: PluginInfo, ctx: PluginContext) -> str:
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
