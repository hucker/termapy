"""EngineHandle -- the privileged internal SPI for built-in plugins.

This is the renamed ``EngineAPI``.  Same shape, same fields, same usage
(``ctx.engine.<x>``).  The rename emphasises that it's *one of* the
namespaced handles on ``PluginContext``, not a parallel concept.

External plugin authors should not reach for this; it's intentionally
unstable.  Use ``ctx.io`` / ``ctx.serial`` / ``ctx.fs`` / ``ctx.ui``
for the supported, gated handles.

The ``EngineAPI`` alias is preserved so existing imports keep working
unchanged through the migration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from termapy.defaults import DEFAULT_CMD_PREFIX


@dataclass
class EngineHandle:
    """Privileged escape hatch exposed to built-in plugins only.

    Holds Textual, threading, and pyserial handles that are genuinely
    frontend-specific and cannot be generified: the plugin registry,
    config apply hooks, port connect/disconnect, capture lifecycle,
    proto debug screen, the raw RX queue, cancel/stop events, etc.

    For session state (flags, counters, target commands, per-plugin
    scratch space) use ``ctx.ns()`` instead.  That is the supported API
    for both built-in and external plugins.  Anything that could live in
    a plain dict has been migrated off ``EngineHandle`` on purpose --
    what's left is the set of things that must remain frontend-coupled.

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


# Backward-compat alias.  Existing code does ``from termapy.plugins import
# EngineAPI`` and ``EngineAPI()`` -- both keep working.  External plugin
# authors aren't expected to touch this, but the alias keeps the migration
# free of import churn.
EngineAPI = EngineHandle
