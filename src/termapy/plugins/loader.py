"""Plugin discovery and loading.

Scans a directory for ``.py`` files exporting a ``COMMAND``,
``TRANSFORM``, ``DIRECTIVE``, a symbol-map converter, or top-level
lifecycle functions.  Returns a ``LoadResult`` bundling everything
found, plus skipped files and errors.

The single entry point is :func:`load_plugins_from_dir`.  Internals
(``_load_plugin_file``, ``_flatten_command``, ``_make_interior_handler``)
are package-private; tests reach them only when exercising the loader
itself.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from termapy import diag_log
from termapy.converters import converter_from_module
from termapy.folders import FOLDER_PATTERNS, PLUGIN
from termapy.plugins.command import (
    LIFECYCLE_HOOK_NAMES,
    BoundaryException,
    CmdResult,
    Command,
    Directive,
    DirectiveInfo,
    LifecycleHook,
    LoadResult,
    ModuleLoad,
    PluginInfo,
    Transform,
    TransformInfo,
    interpolate_help,
    intro_text,
)
from termapy.plugins.params import synthesize_synopsis
from termapy.plugins.views import Column

if TYPE_CHECKING:
    from termapy.plugins.context import PluginContext


def builtins_dir() -> Path:
    """Return the path to the built-in commands directory shipped with termapy."""
    # __file__ is .../termapy/plugins/loader.py; built-in command files
    # live at .../termapy/builtins/commands/.  (The ``plugins/`` package
    # next to this file is the plugin-author API, not the commands.)
    return Path(__file__).parent.parent / "builtins" / "commands"


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

    Each file may export a ``COMMAND`` (Command dataclass), a
    ``TRANSFORM`` (Transform dataclass), a ``DIRECTIVE``, a symbol-map
    converter, and/or lifecycle functions.  Files starting with '_' are
    skipped.

    Args:
        folder: Directory to scan for .py plugin files.
        source: Label for where the plugin came from (e.g. "global", config name).

    Returns:
        LoadResult with one ModuleLoad per file: its exports, or the
        error that stopped it.
    """
    result = LoadResult()
    if not folder.is_dir():
        return result
    _clean_stale_pyc(folder)
    for py_file in sorted(folder.glob(FOLDER_PATTERNS[PLUGIN])):
        if py_file.name.startswith("_"):
            continue
        module = ModuleLoad(py_file.name)
        try:
            (
                module.plugins,
                module.transforms,
                module.directives,
                module.lifecycle_hooks,
                module.converters,
            ) = _load_plugin_file(py_file, source)
        # Plugin file being loaded is third-party code; its top-level
        # can raise anything (import errors, syntax, config reads).
        # Record the failure for reporting and keep loading the rest.
        except BoundaryException as e:
            diag_log.exception("plugin file failed to load: %s", py_file)
            module.error = f"{type(e).__name__}: {e}"
        result.modules.append(module)
    return result


def _load_plugin_file(
    path: Path,
    source: str,
) -> tuple[
    list[PluginInfo],
    list[TransformInfo],
    list[DirectiveInfo],
    list[LifecycleHook],
    list,
]:
    """Import a plugin file and extract everything it exports.

    A valid plugin module may export a ``COMMAND`` instance (a ``Command``
    dataclass), a ``TRANSFORM`` instance (a ``Transform`` dataclass),
    a ``DIRECTIVE`` instance (a ``Directive`` dataclass), top-level
    lifecycle functions named in :data:`LIFECYCLE_HOOK_NAMES`, and/or a
    converter (four top-level names plus an optional ``KIND`` -- see
    ``termapy.converters``).

    Args:
        path: Path to the .py plugin file.
        source: Label for the plugin's origin.

    Returns:
        Tuple of (PluginInfo list, TransformInfo list, DirectiveInfo list,
        LifecycleHook list, ConverterSpec list).

    Raises:
        ValueError: A converter export is malformed (``FORMAT`` present
            but ``convert`` missing or a field the wrong type).  The
            caller records it like any other plugin load failure.
    """
    # Derive the package name if this is a builtin command, so the module
    # is registered under both the dynamic name and the package path.
    # This prevents duplicate module state when app.py/cli.py imports
    # builtins via the package path (e.g. termapy.builtins.commands.var).
    module_name = f"termapy_plugin_{path.stem}"
    pkg_name = None
    try:
        # __file__ is .../termapy/plugins/loader.py; built-ins live at
        # .../termapy/builtins/, so step up one to find them.
        builtins_root = Path(__file__).parent.parent / "builtins"
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
            return [], [], [], [], []
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

    # Converters -- four top-level names plus an optional KIND, the shape
    # the built-in symbol and device converters use.
    converters: list = []
    if isinstance(getattr(mod, "FORMAT", None), str):
        spec = converter_from_module(mod, source)
        if spec is not None:
            converters.append(spec)

    return plugins, transforms, directives, lifecycle_hooks, converters


def _flatten_command(
    node: Command,
    prefix: str,
    source: str,
    group: str = "",
    maturity: str = "",
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

    ``group`` is the exception: a family belongs to one group, so the
    ROOT declares it and every descendant inherits it (registry
    ``help-group-declared``).  A group on a subcommand is ignored --
    ``/sym.search`` cannot leave ``/sym``'s group.

    ``maturity`` inherits too, but a subcommand MAY declare its own: the
    nearest declaration wins, either way (``/sym`` preview, one
    ``/sym.x`` prototype), and an undeclared root is stable (registry
    ``maturity-declared-once``).

    Args:
        node: Command instance with name/help/handler/sub_commands.
        prefix: Dotted path prefix (empty for root).
        source: Plugin source label.
        group: The root's group, passed down the recursion; ``""`` at
            the root, where the node's own declaration is taken.
        maturity: The parent's resolved maturity, passed down the
            recursion; ``""`` at the root.

    Returns:
        List of PluginInfo for this node and all descendants.
    """
    name = node.name
    full_name = f"{prefix}.{name}".lower() if prefix else name.lower()
    sub_commands = node.sub_commands or {}
    children: list[str] = []
    result: list[PluginInfo] = []
    group = group or node.group
    maturity = node.maturity or maturity or "stable"

    # Recurse into sub_commands first so we can build the children list
    for sub_name, sub_node in sub_commands.items():
        # Set name on sub-node so recursion works uniformly
        sub_node.name = sub_name
        child_infos = _flatten_command(sub_node, full_name, source, group, maturity)
        result.extend(child_infos)
        children.append(f"{full_name}.{sub_name}".lower())

    _check_child(full_name, children, "bare_sub", node.bare_sub)
    _check_child(full_name, children, "help_list", node.help_list)
    listing = sub_commands.get(node.help_list) if node.help_list else None
    if listing is not None and (listing.args or listing.params):
        # /help runs it bare, as a list is run.
        raise ValueError(f"{full_name}: help_list {node.help_list!r} takes arguments")
    handler = node.handler
    if not handler and children:
        # Synthetic handler for interior nodes: lists subcommands, or
        # redirects the space form.  A declared bare_sub is the
        # dispatcher's, so a bare call never reaches this handler.
        handler = _make_interior_handler(full_name, children, node.intro)

    if not handler:
        return result

    # When a command declares params, its help synopsis is synthesized from
    # them (a params command has no hand-written args string -- enforced in
    # Command.__post_init__).  The synthesized synopsis flows through the same
    # ``args`` field, so help/catalog consumers see no shape change.
    args = synthesize_synopsis(node.params) if node.params else node.args

    info = PluginInfo(
        name=full_name,
        args=args,
        help=node.help,
        long_help=node.long_help,
        handler=handler,
        source=source,
        children=children,
        raw_args=node.raw_args,
        flags=dict(node.flags),
        needs=node.needs,
        params=node.params,
        safety=node.safety,
        files=node.files,
        picker=node.picker,
        bare_sub=node.bare_sub,
        help_list=node.help_list,
        group=group,
        maturity=maturity,
        shows=node.shows,
        cfg=dict(node.cfg),
        state=node.state,
        intro=node.intro,
    )
    result.insert(0, info)
    return result


def _check_child(full_name: str, children: list[str], key: str, sub: str) -> None:
    """Fail at load when a declaration (``bare_sub``, ``help_list``) names no child of ``full_name``.

    Load time, so a typo fails at boot rather than rendering a wrong bare
    behavior or help page.  Any node may declare one, with or without a
    handler; the dispatcher runs a ``bare_sub``
    (``ReplEngine._bare_handler``), ``/help`` a ``help_list``.

    Raises:
        ValueError: ``sub`` names no declared subcommand.
    """
    if sub and f"{full_name}.{sub}".lower() not in children:
        short_names = ", ".join(name.rsplit(".", 1)[1] for name in children)
        raise ValueError(
            f"{full_name}: {key} {sub!r} names no subcommand (subcommands: {short_names})"
        )


def _make_interior_handler(full_name: str, children: list[str], intro: str = "") -> Callable:
    """Create a synthetic handler for an interior command node.

    Bare (``/proto.crc``), the handler says what the family is for and
    returns its subcommands as the standard table (registry
    ``list-one-format``): a name is clickable and opens that command's help,
    and the JSON is the same records.  A node that
    declares ``bare_sub`` never gets here bare, because the dispatcher
    runs that child first (``/mem`` -> ``/mem.info``).  With arguments,
    the first token is treated as a subcommand name -- ``/mem dump 0x0``
    redirects to ``/mem.dump 0x0`` (the dotted form stays the canonical
    grammar; the space form just works), and a token that names no child
    fails with the list.  Ignoring the arguments, as this handler once
    did, read as success while doing nothing.

    Args:
        full_name: Dotted command path (e.g. "proto").
        children: Dotted names of direct subcommands.
        intro: ``Command.intro``, shown above the list so a bare call says
            what the family is for.

    Returns:
        A handler callable with the standard (ctx, args) signature.
    """

    def _handler(ctx: PluginContext, args: str) -> CmdResult:
        prefix = ctx.prefix
        if args.strip():
            first, _, rest = args.strip().partition(" ")
            child_name = f"{full_name}.{first.lower()}"
            if child_name in children:
                # Re-dispatch the dotted form through the ENGINE
                # (ctx.internal.dispatch = ReplEngine.dispatch, prefixless
                # command semantics in every host).  ctx.dispatch is the
                # full pipeline, where an unprefixed line is a bare DEVICE
                # line -- the gold caught that version sending
                # "mem.dump ..." to the wire.  ($(VAR) forms were already
                # expanded once; the second pass is a no-op unless a value
                # itself contains "$(".)
                return ctx.internal.dispatch(f"{child_name} {rest}".rstrip())
            short_names = ", ".join(name.rsplit(".", 1)[1] for name in children)
            return CmdResult.fail(msg=f"Unknown subcommand: {first} (subcommands: {short_names})")
        if intro and not ctx.wants_data:
            ctx.io.output(intro_text(intro, prefix), "")
            ctx.io.output("", "")
        plugins = ctx.internal.plugins
        records = [
            {"command": child_name, "help": interpolate_help(plugins[child_name].help, prefix)}
            for child_name in children
            if child_name in plugins
        ]
        columns = (
            Column(
                "COMMAND",
                lambda record: prefix + record["command"],
                run=lambda record: f"help {record['command']}",
            ),
            Column("DESCRIPTION", lambda record: record["help"]),
        )
        return CmdResult.table(prefix + full_name, "subcommand", columns, records)

    # The /help.dev text of each command built here (registry handler-docstring).
    _handler.__doc__ = f"Bare {full_name} lists its subcommands; with arguments, the first token names the subcommand to run."
    return _handler
