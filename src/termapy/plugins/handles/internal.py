"""InternalHandle -- the privileged internal interface for built-in plugins.

Reached as ``ctx.internal``.  Named for the one property every slot
shares: it's *internal* -- not part of the external plugin API.  The
sibling handles (``ctx.io`` / ``ctx.serial`` / ``ctx.fs`` / ``ctx.ui``)
are clean capability domains; this one is deliberately the bucket for
everything privileged that doesn't fit a domain, so naming it after a
real domain (it used to be ``engine``) made it read like a peer when
it isn't.

External plugin authors should not reach for this; it's intentionally
unstable.  Use the four capability handles for the supported, gated API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class InternalHandle:
    """Privileged handle exposed to built-in plugins via ``ctx.internal``.

    It serves two distinct jobs, grouped below:

    1. **Frontend escape hatch** -- slots that genuinely need Textual or
       threads (a confirm modal, capture, the proto-debug and picker
       screens, the @work script run).  The plugin layer must not import
       those, so the host injects them.  Several are ``None`` in CLI/MCP,
       so the same command adapts per frontend.  (Serial connect/disconnect
       used to live here too; they moved to ``ctx.serial`` -- a plugin
       looking for "connect the port" expects them in the serial domain.)

    2. **Engine forwarders** -- slots that just forward to the live
       ``ReplEngine``.  They're injected rather than imported because
       ``repl.py`` imports the plugins package, so a plugin importing
       ``repl`` would be circular.  Pure engine logic the plugin can't
       reach any other way -- not frontend-coupled.  (This job is why the
       handle was once called ``engine``; it names only half of what's
       here, which is why the handle is now ``internal``.)

    For plain session state (flags, counters, scratch) use ``ctx.ns()``
    instead; anything that could live in a dict was moved off this handle
    on purpose.  External plugins should not use ``ctx.internal`` -- it is
    unstable and may change between versions.

    What's left is the irreducible core, deliberately not the leftovers
    of a half-finished cleanup.  Members with a real home already moved:
    pure data (``prefix`` -> ``ctx.prefix``, type coercion ->
    ``scripting``) and serial lifecycle (``connect`` / ``disconnect`` /
    ... -> ``ctx.serial``).  The escape-hatch slots that remain are
    per-command TUI screens (one command each, no-ops outside the TUI):
    promoting them to ``ctx.ui`` would pollute that clean, general API
    with one-off methods, so they stay here where "unstable, built-ins
    only" is the contract.
    """

    # ═══ Engine forwarders ════════════════════════════════════════════
    # Forward to the live ReplEngine.  Injected (not imported) because
    # repl.py imports the plugins package -- a plugin importing repl would
    # be circular.  Nothing here is frontend-coupled.

    plugins: dict = field(default_factory=dict)
    directives: list = field(default_factory=list)

    # Bare REPL dispatch through the plugin pipeline (capability gates,
    # flag parsing) WITHOUT ctx.dispatch's serial-output sugar.  Legacy
    # forwarders (/echo -> /term.echo) use this -- dispatch_full would
    # misread the un-prefixed target as a serial send.
    dispatch: Callable = lambda _line: None

    apply_cfg: Callable = lambda key, val: None  # set cfg in-memory, no dialog
    in_script: Callable = lambda: False
    start_script: Callable | None = None  # (args) -> (Path | None, CmdResult)
    # script_stop()/script_stop_event: signal the engine's script runner
    # to abort (a threading.Event it owns); reached here, not imported.
    script_stop: Callable = lambda: None
    script_stop_event: Any = None
    add_post_dispatch_observer: Callable | None = None  # (cb) -> token
    remove_post_dispatch_observer: Callable | None = None  # (token) -> None
    is_recording: Callable | None = None  # () -> bool; TUI Record button reads this

    # Raw pyserial handle (serial.Serial | None).  The deliberate escape
    # hatch for built-ins that need pyserial's own API -- DTR/RTS/CTS/DSR,
    # break, byte-size/parity/stop-bits, in_waiting.  Kept OFF ctx.serial
    # (the stable, third-party-facing handle) so external plugins can't
    # couple to pyserial through the clean API; port_control is the seam
    # that actually drives it.
    port: Callable = lambda: None  # -> serial.Serial | None

    # ═══ Frontend escape hatch ════════════════════════════════════════
    # Genuinely need Textual or threads, which the plugin layer can't
    # import.  Wired by the host; several are None in CLI/MCP, so the
    # same command adapts per frontend.  These are per-command screens
    # (proto-debug, the pickers, the find bar) -- they stay here rather
    # than on ctx.ui because each is used by exactly one command and is a
    # no-op everywhere else; ctx.ui is the clean general TUI API and
    # shouldn't collect one-off methods.

    # confirm_save_cfg(key, val): ask the frontend to confirm (TUI modal),
    # then apply on Yes.  None in CLI/MCP -> caller applies directly via
    # apply_cfg (above).  The pure JSON write lives in config.py.
    confirm_save_cfg: Callable | None = None

    # load_config(name): resolve name, disconnect + reconnect; -> CmdResult.
    # Stays here (not on ctx.serial): it's a config-switch orchestration
    # that happens to touch the port, not a serial primitive.
    load_config: Callable = lambda name: None

    start_capture: Callable = lambda **kw: None
    stop_capture: Callable = lambda: None
    open_proto_debug: Callable = lambda path, script: None  # TUI: ProtoDebugScreen

    run_script: Callable | None = None  # (path, profile, verbose); TUI via @work

    xfer_cancel: Any = None  # threading.Event - Escape cancels a transfer

    # open_picker(name "cfg"/"run"/"proto") -> CmdResult; opens the dialog.
    open_picker: Callable | None = None
    # update_find_bar(state dict | None) -> None; /find re-renders the FindBar.
    update_find_bar: Callable | None = None
