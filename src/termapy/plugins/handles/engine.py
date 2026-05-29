"""EngineHandle -- the privileged internal interface for built-in plugins.

This is the renamed ``EngineAPI``.  Same shape, same fields, same usage
(``ctx.engine.<x>``).  The rename emphasizes that it's *one of* the
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

    # ── Engine + registry ────────────────────────────────────────────
    prefix: str = DEFAULT_CMD_PREFIX
    plugins: dict = field(default_factory=dict)
    directives: list = field(default_factory=list)

    # ── Command dispatch ─────────────────────────────────────────────
    # Bare REPL dispatch through the plugin pipeline (capability gates,
    # flag parsing) WITHOUT the serial-output sugar ctx.dispatch adds via
    # dispatch_full.  Legacy forwarders (/echo -> /term.echo) use this --
    # re-entering dispatch_full would misread the un-prefixed target as a
    # serial send.
    dispatch: Callable = lambda _line: None

    # ── Config ───────────────────────────────────────────────────────
    # save_cfg(key, val) -> confirm dialog; None = apply with no confirm.
    save_cfg: Callable | None = None
    apply_cfg: Callable = lambda key, val: None  # set in-memory, no dialog
    coerce_type: Callable = lambda val, existing: val
    # load_config(name): resolve name + (re)connect; returns CmdResult.
    load_config: Callable = lambda name: None

    # ── Serial port ──────────────────────────────────────────────────
    connect: Callable = lambda port=None: None
    disconnect: Callable = lambda: None
    update_port: Callable = lambda name: None
    apply_port_effects: Callable = lambda effects: None
    rx_queue: Any = None  # queue.Queue[bytes] - raw RX for protocol handlers

    # ── Capture + protocol debug ─────────────────────────────────────
    start_capture: Callable = lambda **kw: None
    stop_capture: Callable = lambda: None
    open_proto_debug: Callable = lambda path, script: None

    # ── Script execution ─────────────────────────────────────────────
    in_script: Callable = lambda: False
    script_stop: Callable = lambda: None
    script_stop_event: Any = None  # threading.Event - /stop aborts a running script
    # start_script(args): resolve a filename via the REPL's script-path
    # logic.  run_script(path, ...): execute it -- synchronous in CLI/MCP,
    # threaded (@work) in TUI via a host-subclass override.  The /run
    # builtin uses both, so one builtin serves all three hosts.
    start_script: Callable | None = None  # (args) -> (Path | None, CmdResult)
    run_script: Callable | None = None  # (path, profile=False, verbose=False) -> None

    # ── File transfer ────────────────────────────────────────────────
    xfer_cancel: Any = None  # threading.Event - Escape cancels a transfer

    # ── TUI-only UI (None in CLI/MCP, so builtins fall through to text) ──
    # open_picker(name "cfg"/"run"/"proto") -> CmdResult; opens the dialog.
    open_picker: Callable | None = None
    # update_find_bar(state dict | None) -> None; /find re-renders the FindBar.
    update_find_bar: Callable | None = None

    # ── Post-dispatch observers (/run.record, audit, MCP stream) ─────
    # Wired to the ReplEngine methods of the same name.
    add_post_dispatch_observer: Callable | None = None  # (cb) -> token
    remove_post_dispatch_observer: Callable | None = None  # (token) -> None
    is_recording: Callable | None = None  # () -> bool; the TUI Record button reads this


# Backward-compat alias.  Existing code does ``from termapy.plugins import
# EngineAPI`` and ``EngineAPI()`` -- both keep working.  External plugin
# authors aren't expected to touch this, but the alias keeps the migration
# free of import churn.
EngineAPI = EngineHandle
