"""Title-bar rendering for the TUI App.

The title bar carries three live indicators: the current config name
(center button), the port + connection state (left button), and the
script/proto-mode markers (right buttons).  Tooltips and color
update on cfg load, on connect/disconnect, and on serial-line
changes.  Previously lived as four ``_*`` methods on
``SerialTerminal`` in ``app.py``; extracted here so the title-bar
surface is a named subsystem in the filesystem.

Each function takes the app as its first argument and reaches widget
state via ``app.query_one(...)``.  ``SerialTerminal`` keeps thin
method stubs (``self._update_title`` etc.) that delegate -- those
let internal call sites stay unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text
from textual.css.query import NoMatches
from textual.widgets import Button

# Mirror of app.py's SHUTDOWN_RACE for the same reason capture_view
# has one -- widget-touching code in here must swallow the Textual
# teardown race the same way.
SHUTDOWN_RACE: tuple[type[BaseException], ...] = (NoMatches, RuntimeError)

if TYPE_CHECKING:
    from termapy.app import SerialTerminal  # noqa: F401 -- type-hint surface
    from termapy.port_control import ChipFacts


def _hotkey_label(btn_id: str) -> str:
    """Return the bracketed hotkey suffix for a title-bar button.

    Local re-export of ``app._hotkey_label`` -- importing it
    directly would create a circular module reference (app imports
    title_bar via lazy stubs).  Resolved on first call instead.
    """
    from termapy.app import _hotkey_label as fn
    return fn(btn_id)


def format_tooltip_value(value: object) -> str:
    """Render a config value for display in a tooltip body line.

    Booleans become ``ON``/``OFF`` (matching how the user toggles
    them in config).  ``None`` becomes ``(none)``.  Strings with
    non-printable characters (like ``\\r``) are wrapped in repr()
    so they're visually distinct from regular text.  Numbers and
    plain strings pass through unchanged.
    """
    if value is None:
        return "(none)"
    if isinstance(value, bool):
        return "ON" if value else "OFF"
    if isinstance(value, str):
        if not value:
            return "(empty)"
        if any(not c.isprintable() for c in value):
            return repr(value)
        return value
    return str(value)


def port_tooltip_pairs(facts: ChipFacts | None) -> list[tuple[str, object]]:
    """Build the kv body for the port-button tooltip from chip facts.

    Pure selection logic (tested in ``tests/test_title_tooltip.py``):
    picks the display fields in a fixed order, drops the ones the
    platform couldn't determine (``None``), and falls back to a single
    status line when the port wasn't enumerable at all (DEMO, unplugged
    cable, non-USB device).
    """
    if facts is None:
        return [("status", "no USB chip info available")]
    pairs: list[tuple[str, object]] = []
    for field_name in (
        "description",
        "manufacturer",
        "vendor",
        "model",
        "usb_speed",
        "vid_pid",
        "location",
        "serial",
        "negotiated",
        "driver",
        "latency_timer",
        "max_baud",
        "in_use",
    ):
        value = getattr(facts, field_name)
        if value is None:
            continue
        pairs.append((field_name, value))
    return pairs


def format_title_tooltip(
    title: str, kv_pairs: list[tuple[str, object]], action: str
) -> str:
    """Render a title-bar tooltip with the shared three-section layout.

    Pure string formatting (no app, no widgets) -- shared by the config,
    connection-status, and port tooltips, and tested directly in
    ``tests/test_title_tooltip.py``.

    Layout::

        <title>

        key1            = value1
        key2            = value2
        key3            = value3

        Click to: <action>

    Keys are left-aligned and padded so the ``=`` signs line up
    across rows.  Values are formatted with ``format_tooltip_value``
    so booleans appear as ``ON``/``OFF``, ``None`` becomes ``(none)``,
    and strings with control characters are repr'd.

    Args:
        title: Heading line shown at the top.
        kv_pairs: List of (key, value) tuples for the body.  Order
            preserved.  Empty keys are silently skipped.
        action: Verb fragment shown after ``Click to:`` at the bottom.

    Returns:
        Formatted tooltip string with embedded newlines.
    """
    body_pairs = [(k, v) for k, v in kv_pairs if k]
    key_width = max((len(k) for k, _ in body_pairs), default=0)
    lines: list[str] = [title, ""]
    for key, value in body_pairs:
        display = format_tooltip_value(value)
        lines.append(f"{key.ljust(key_width)}  = {display}")
    lines.append("")
    lines.append(f"Click to: {action}")
    return "\n".join(lines)


def update_title(app) -> None:
    """Refresh all three title-bar buttons with the current cfg/connection state.

    Called from lifecycle hooks (on_mount, _on_connected,
    _on_config_result) and config switches.  Recomputes the center
    button label (cfg title), the cfg-button tooltip (kv summary), and
    delegates port-button refresh to ``update_conn_tooltip``.

    During teardown or before initial mount the title-bar widgets
    aren't available; bails quietly instead of raising NoMatches into
    the caller.

    Args:
        app: The SerialTerminal instance.
    """
    # Called from lifecycle hooks (on_mount, _on_connected,
    # _on_config_result) and config switches.  During teardown or
    # before initial mount the title-bar widgets aren't available;
    # bail quietly instead of raising NoMatches into the caller.
    if app._shutting_down:
        return
    try:
        center = app.query_one("#title-center", Button)
    except SHUTDOWN_RACE:
        return
    title = app.cfg.get("title", "") or app.config_path
    center.label = Text(title)

    # Cfg button (center) tooltip
    cfg_title = app.cfg.get("title", "") or (
        Path(app.config_path).stem if app.config_path else "Config"
    )
    serial = app.cfg["serial"]
    sb = serial["stop_bits"]
    sb_str = str(int(sb)) if sb == int(sb) else str(sb)
    frame = f"{serial['byte_size']}{serial['parity']}{sb_str}"
    cfg_pairs: list[tuple[str, object]] = [
        ("config_path", app.config_path or "(none)"),
        ("port", serial["port"] or "?"),
        ("baud_rate", serial["baud_rate"]),
        ("frame", frame),
        ("flow_control", serial["flow_control"]),
        ("encoding", app.cfg.get("encoding", "utf-8")),
        ("eol", app.cfg.get("eol", "\r")),
        ("on_connect_cmd", app.cfg.get("on_connect_cmd") or None),
        ("auto_connect", bool(app.cfg.get("auto_connect"))),
        ("auto_reconnect", bool(app.cfg.get("auto_reconnect"))),
        # Live echo flags (what /term.echo and /term.echo_repl toggle), so
        # the tooltip reflects current state and moves with the toggles.
        # echo = device-command echo (flag seeded from cfg); echo_repl
        # = REPL/slash-command echo (session-only, no cfg key).
        ("echo", bool(app.repl.echo)),
        ("echo_repl", bool(app.repl.echo_repl)),
        ("timestamps", bool(app.cfg.get("timestamps"))),
    ]
    center.tooltip = format_title_tooltip(
        cfg_title, cfg_pairs, f"edit config ({_hotkey_label('title-center')})"
    )

    # Port button (left) tooltip + label
    try:
        port_btn = app.query_one("#title-left", Button)
        port_btn.label = app._port_info_str()
        port_btn.tooltip = app._port_button_tooltip()
    except SHUTDOWN_RACE:
        pass

    # Connection status button (right) tooltip
    app._update_conn_tooltip()


def update_conn_tooltip(app, widget: Button | None = None) -> None:
    """Update the connection button tooltip with status and config.

    Uses the shared three-section layout (title / kv body / action).
    Title is the live connection state (Connected / Disconnected).
    Body shows port name and the auto-connect / auto-reconnect flags
    as ON/OFF so the user can discover them without opening config.
    Action verb tracks state: "disconnect" when connected,
    "connect" when not.
    """
    try:
        if widget is None:
            widget = app.query_one("#title-right", Button)
        connected = app.is_connected
        title = "Connected" if connected else "Disconnected"
        action = "disconnect" if connected else "connect"
        pairs: list[tuple[str, object]] = [
            ("port", app.cfg["serial"]["port"] or "?"),
            ("auto_connect", bool(app.cfg.get("auto_connect"))),
            ("auto_reconnect", bool(app.cfg.get("auto_reconnect"))),
        ]
        widget.tooltip = format_title_tooltip(title, pairs, action)
    except SHUTDOWN_RACE:
        pass  # title widgets unmounted during teardown / reload


def update_port(app, port: str) -> None:
    """Change serial port for this session and reconnect.

    Does not write to disk - the config editor is the only path
    that persists changes.  This keeps $(env.NAME) templates intact.
    """
    import copy
    # app.cfg is a read-only MappingProxyType view (ReplEngine owns the dict).
    # dict() un-proxies the top level so deepcopy can build a fully mutable
    # copy; deepcopy chokes on a mappingproxy ("cannot pickle 'mappingproxy'").
    cfg = copy.deepcopy(dict(app.cfg))
    cfg["serial"]["port"] = port
    app._switch_config(cfg, app.config_path)
    if app.is_connected:
        app._status(f"Port changed to {port} (session)", "green")


