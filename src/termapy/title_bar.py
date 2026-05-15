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
    from termapy.app import SerialTerminal  # noqa: F401


def _hotkey_label(btn_id: str) -> str:
    """Return the bracketed hotkey suffix for a title-bar button.

    Local re-export of ``app._hotkey_label`` -- importing it
    directly would create a circular module reference (app imports
    title_bar via lazy stubs).  Resolved on first call instead.
    """
    from termapy.app import _hotkey_label as fn
    return fn(btn_id)


def format_title_tooltip(
    app, title: str, kv_pairs: list[tuple[str, object]], action: str
) -> str:
    """Render a title-bar tooltip with the shared three-section layout.

    Layout::

        <title>

        key1            = value1
        key2            = value2
        key3            = value3

        Click to: <action>

    Keys are left-aligned and padded so the ``=`` signs line up
    across rows.  Values are formatted with ``_format_tooltip_value``
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
        # ``_format_tooltip_value`` is a @staticmethod on SerialTerminal;
        # invoking via the instance works fine and avoids a runtime
        # import of the class (which would be circular).
        display = app._format_tooltip_value(value)
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
    sb = app.cfg.get("stop_bits", 1)
    sb_str = str(int(sb)) if sb == int(sb) else str(sb)
    frame = (
        f"{app.cfg.get('byte_size', 8)}" f"{app.cfg.get('parity', 'N')}{sb_str}"
    )
    cfg_pairs: list[tuple[str, object]] = [
        ("config_path", app.config_path or "(none)"),
        ("port", app.cfg.get("port", "?")),
        ("baud_rate", app.cfg.get("baud_rate", "?")),
        ("frame", frame),
        ("flow_control", app.cfg.get("flow_control", "none")),
        ("encoding", app.cfg.get("encoding", "utf-8")),
        ("line_ending", app.cfg.get("line_ending", "\r")),
        ("on_connect_cmd", app.cfg.get("on_connect_cmd") or None),
        ("auto_connect", bool(app.cfg.get("auto_connect"))),
        ("auto_reconnect", bool(app.cfg.get("auto_reconnect"))),
        ("echo_input", bool(app.cfg.get("echo_input"))),
        ("show_timestamps", bool(app.cfg.get("show_timestamps"))),
    ]
    center.tooltip = app._format_title_tooltip(
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
            ("port", app.cfg.get("port", "?")),
            ("auto_connect", bool(app.cfg.get("auto_connect"))),
            ("auto_reconnect", bool(app.cfg.get("auto_reconnect"))),
        ]
        widget.tooltip = app._format_title_tooltip(title, pairs, action)
    except SHUTDOWN_RACE:
        pass  # title widgets unmounted during teardown / reload


def update_port(app, port: str) -> None:
    """Change serial port for this session and reconnect.

    Does not write to disk - the config editor is the only path
    that persists changes.  This keeps $(env.NAME) templates intact.
    """
    cfg = dict(app.cfg)
    cfg["port"] = port
    app._switch_config(cfg, app.config_path)
    if app.is_connected:
        app._status(f"Port changed to {port} (session)", "green")


