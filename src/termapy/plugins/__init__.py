"""Plugin system for termapy - discovery, loading, and context API.

Plugins are .py files that export a ``COMMAND`` instance describing the
command hierarchy::

    from termapy.plugins import Command

    def _handler(ctx, args):
        ctx.io._write("Hello from plugin!")

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

This package re-exports the public API so plugin authors can keep using
``from termapy.plugins import Command, CmdResult, PluginContext`` -- the
internals are split across :mod:`.context`, :mod:`.command`,
:mod:`.capabilities`, and :mod:`.loader` for navigability.
"""

from termapy.plugins.capabilities import (
    CapabilitySet,
    ENVIRONMENTS,
    MissingCapability,
    detect_gui_apps,
)
from termapy.plugins.command import (
    BoundaryException,
    CmdResult,
    Command,
    Directive,
    DirectiveInfo,
    DirectiveResult,
    LIFECYCLE_HOOK_NAMES,
    LifecycleHook,
    LoadResult,
    LongHelp,
    PluginInfo,
    Transform,
    TransformInfo,
    interpolate_help,
    resolve_long_help,
)
from termapy.plugins.context import (
    PluginConfig,
    PluginContext,
)
from termapy.plugins.output_levels import (
    DEFAULT_OUTPUT_LEVEL,
    LEVEL_FLAGS,
    OUTPUT_LEVELS,
    format_kv_lines,
    parse_output_level,
)
from termapy.plugins.handles import (
    EngineAPI,
    EngineHandle,
    FilesystemHandle,
    IOHandle,
    SerialHandle,
    UIHandle,
)
from termapy.plugins.loader import (
    builtins_dir,
    load_plugins_from_dir,
)


__all__ = [
    # Capabilities
    "CapabilitySet",
    "ENVIRONMENTS",
    "MissingCapability",
    "detect_gui_apps",
    # Command + return types + helpers
    "BoundaryException",
    "CmdResult",
    "Command",
    "Directive",
    "DirectiveInfo",
    "DirectiveResult",
    "LIFECYCLE_HOOK_NAMES",
    "LifecycleHook",
    "LoadResult",
    "LongHelp",
    "PluginInfo",
    "Transform",
    "TransformInfo",
    "interpolate_help",
    "resolve_long_help",
    # Context + config + output-level utilities
    "DEFAULT_OUTPUT_LEVEL",
    "LEVEL_FLAGS",
    "OUTPUT_LEVELS",
    "PluginConfig",
    "PluginContext",
    "format_kv_lines",
    "parse_output_level",
    # Capability-domain handles (also re-exports EngineAPI for back-compat)
    "EngineAPI",
    "EngineHandle",
    "FilesystemHandle",
    "IOHandle",
    "SerialHandle",
    "UIHandle",
    # Loader
    "builtins_dir",
    "load_plugins_from_dir",
]
