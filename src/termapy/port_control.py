"""Pure functions for serial port control — no Textual, no pyserial imports.

Each function accepts a serial-like object (or None), config dict, and args,
and returns a list of (text, color) message tuples plus a dict of side effects
for the caller to apply.

Side effects dict keys:
    update_title: bool — refresh the title bar
    sync_hw: bool — update hardware button visibility/state
    cfg_update: dict — keys to update in the in-memory config
"""

from __future__ import annotations

from typing import Any

from termapy.defaults import VALID_BYTE_SIZES, VALID_FLOW_CONTROLS, VALID_PARITIES, VALID_STOP_BITS

# Type alias for message lists: (text, color_or_None)
Msg = tuple[str, str | None]
Result = tuple[list[Msg], dict[str, Any]]

SERIAL_KEYS = {
    "port",
    "baud_rate",
    "byte_size",
    "parity",
    "stop_bits",
    "flow_control",
}

# Maps config key → (pyserial attribute, type coercion, description, valid values)
PORT_PROPS = {
    "baud_rate": ("baudrate", int, "Baud rate", None),
    "byte_size": ("bytesize", int, "Data bits", VALID_BYTE_SIZES),
    "parity": ("parity", str, "Parity", VALID_PARITIES),
    "stop_bits": ("stopbits", float, "Stop bits", VALID_STOP_BITS),
}


def _msg(text: str, color: str | None = None) -> Msg:
    return (text, color)


def _result(msgs: list[Msg], **side_effects: Any) -> Result:
    return msgs, side_effects


def list_ports() -> Result:
    """List available serial ports.

    Returns:
        Messages with port device and description.
    """
    from serial.tools.list_ports import comports

    ports = sorted(comports(), key=lambda p: p.device)
    if not ports:
        return _result([_msg("No serial ports found", "yellow")])
    return _result([_msg(f"  {p.device}  {p.description or ''}") for p in ports])


def port_info(cfg: dict, ser: Any | None) -> Result:
    """Format comprehensive port status.

    Args:
        cfg: Config dict.
        ser: Serial-like object, or None if disconnected.
    """
    connected = ser is not None
    state = "connected" if connected else "disconnected"
    sb = cfg.get("stop_bits", 1)
    sb_str = str(int(sb)) if sb == int(sb) else str(sb)
    msgs: list[Msg] = [
        _msg(f"  Port:         {cfg.get('port', '?')}  ({state})"),
        _msg(f"  Baud rate:    {cfg.get('baud_rate', '?')}"),
        _msg(f"  Frame:        {cfg.get('byte_size', 8)}"
             f"{cfg.get('parity', 'N')}{sb_str}"),
        _msg(f"  Flow control: {cfg.get('flow_control', 'none')}"),
        _msg(f"  Encoding:     {cfg.get('encoding', 'utf-8')}"),
    ]
    if connected:
        try:
            for name in ("dtr", "rts", "cts", "dsr", "ri", "cd"):
                msgs.append(_msg(f"  {name.upper()}:          {int(getattr(ser, name))}"))
        except OSError:
            pass
    return _result(msgs)


def get_set_prop(ser: Any | None, cfg: dict, key: str, args: str) -> Result:
    """Get or set a serial port property.

    Args:
        ser: Serial-like object, or None if disconnected.
        cfg: Config dict.
        key: Config key (e.g. "baud_rate").
        args: User-provided value string, or empty to read.
    """
    attr, coerce, desc, valid = PORT_PROPS[key]
    val = args.strip()
    connected = ser is not None
    if not val:
        if not connected:
            return _result([_msg(f"{cfg.get(key, '?')} (disconnected)")])
        try:
            return _result([_msg(f"{getattr(ser, attr)}")])
        except OSError as e:
            return _result([_msg(f"{desc} read error: {e}", "red")])
    if not connected:
        return _result([_msg("Not connected", "yellow")])
    try:
        if key == "parity":
            val = val.upper()
        typed = coerce(val)
        if valid and typed not in valid:
            opts = ", ".join(sorted(str(v) for v in valid))
            return _result([_msg(f"Invalid {desc.lower()}: {val} (use {opts})", "red")])
        setattr(ser, attr, typed)
        return _result(
            [_msg(f"{desc} → {typed}")],
            update_title=True,
            cfg_update={key: typed},
        )
    except ValueError:
        return _result([_msg(f"Invalid {desc.lower()}: {val}", "red")])
    except OSError as e:
        return _result([_msg(f"{desc} error: {e}", "red")])


def get_set_flow(ser: Any | None, cfg: dict, args: str) -> Result:
    """Get or set flow control mode.

    Args:
        ser: Serial-like object, or None if disconnected.
        cfg: Config dict.
        args: Flow mode string, or empty to read.
    """
    val = args.strip().lower()
    connected = ser is not None
    if not val:
        fc = cfg.get("flow_control", "none")
        suffix = " (disconnected)" if not connected else ""
        return _result([_msg(f"{fc}{suffix}")])
    if not connected:
        return _result([_msg("Not connected", "yellow")])
    if val not in VALID_FLOW_CONTROLS:
        return _result([_msg(
            f"Invalid flow control: {val} (use none/rtscts/xonxoff/manual)", "red"
        )])
    try:
        ser.rtscts = (val == "rtscts")
        ser.xonxoff = (val == "xonxoff")
        return _result(
            [_msg(f"Flow control → {val}")],
            update_title=True,
            sync_hw=True,
            cfg_update={"flow_control": val},
        )
    except OSError as e:
        return _result([_msg(f"Flow control error: {e}", "red")])


def parse_bool_value(val: str) -> bool | None:
    """Parse a boolean-like string. Returns True, False, or None if invalid."""
    if val in ("1", "on", "true", "high"):
        return True
    if val in ("0", "off", "false", "low"):
        return False
    return None


def get_set_hw_line(ser: Any | None, line: str, args: str) -> Result:
    """Get or set a hardware line (DTR or RTS).

    Args:
        ser: Serial-like object, or None if disconnected.
        line: Line name ("dtr" or "rts").
        args: Value string, or empty to read.
    """
    label = line.upper()
    val = args.strip().lower()
    connected = ser is not None
    if not val:
        if not connected:
            return _result([_msg("Not connected", "yellow")])
        try:
            return _result([_msg(f"{int(getattr(ser, line))}")])
        except OSError as e:
            return _result([_msg(f"{label} read error: {e}", "red")])
    if not connected:
        return _result([_msg("Not connected", "yellow")])
    state = parse_bool_value(val)
    if state is None:
        return _result([_msg(f"Invalid {label} value: {val} (use 0/1/on/off)", "red")])
    try:
        setattr(ser, line, state)
        return _result(
            [_msg(f"{label} → {int(state)}")],
            sync_hw=True,
        )
    except OSError as e:
        return _result([_msg(f"{label} error: {e}", "red")])


def read_signal(ser: Any | None, signal: str, args: str) -> Result:
    """Read a read-only input signal (CTS, DSR, RI, CD).

    Args:
        ser: Serial-like object, or None if disconnected.
        signal: Signal name ("cts", "dsr", "ri", "cd").
        args: Should be empty (read-only).
    """
    label = signal.upper()
    if args.strip():
        return _result([_msg(f"{label} is read-only", "yellow")])
    if ser is None:
        return _result([_msg("Not connected", "yellow")])
    try:
        return _result([_msg(f"{int(getattr(ser, signal))}")])
    except OSError as e:
        return _result([_msg(f"{label} read error: {e}", "red")])


def send_break(ser: Any | None, args: str) -> Result:
    """Send a break signal on the serial line.

    Args:
        ser: Serial-like object, or None if disconnected.
        args: Duration in milliseconds, or empty for default (250ms).
    """
    if ser is None:
        return _result([_msg("Not connected", "yellow")])
    val = args.strip()
    duration = 0.25
    if val:
        try:
            duration = int(val) / 1000.0
            if duration <= 0:
                raise ValueError
        except ValueError:
            return _result([_msg("Invalid duration (use milliseconds, e.g. 250)", "red")])
    try:
        ser.send_break(duration=duration)
        return _result([_msg(f"Break sent ({int(duration * 1000)}ms)")])
    except OSError as e:
        return _result([_msg(f"Break error: {e}", "red")])
