"""Plugin discovery and loading.

Scans a directory for ``.py`` files exporting a ``COMMAND``,
``TRANSFORM``, ``DIRECTIVE``, or top-level lifecycle functions.
Returns a ``LoadResult`` bundling everything found, plus skipped files
and errors.

The single entry point is :func:`load_plugins_from_dir`.  Internals
(``_load_plugin_file``, ``_flatten_command``, ``_make_interior_handler``)
are package-private; tests reach them only when exercising the loader
itself.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Callable

from termapy.plugins.command import (
    BoundaryException,
    Command,
    Directive,
    DirectiveInfo,
    LIFECYCLE_HOOK_NAMES,
    LifecycleHook,
    LoadResult,
    PluginInfo,
    Transform,
    TransformInfo,
    interpolate_help,
)


def builtins_dir() -> Path:
    """Return the path to the built-in plugins directory shipped with termapy."""
    # __file__ is .../termapy/plugins/loader.py; built-ins live at
    # .../termapy/builtins/plugins/, so step up one and across.
    return Path(__file__).parent.parent / "builtins" / "plugins"


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

    def _handler(ctx, args: str) -> None:
        prefix = ctx.engine.prefix
        ctx.io.write(f"Subcommands of {prefix}{full_name}:")
        plugins = ctx.engine.plugins
        for child_name in children:
            child = plugins.get(child_name)
            if child:
                arg_str = f" {child.args}" if child.args else ""
                help_text = interpolate_help(child.help, prefix)
                ctx.io.write(f"  {prefix}{child_name}{arg_str} - {help_text}")

    return _handler
