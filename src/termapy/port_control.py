"""Pure functions for serial port control - no Textual, no pyserial imports.

Each function accepts a serial-like object (or None), config dict, and args,
and returns a list of (text, color) message tuples plus a dict of side effects
for the caller to apply.

Side effects dict keys:
    update_title: bool - refresh the title bar
    sync_hw: bool - update hardware button visibility/state
    cfg_update: dict - keys to update in the in-memory config
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any, Mapping

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

# Maps config key -> (pyserial attribute, type coercion, description, valid values)
PORT_PROPS = {
    "baud_rate": ("baudrate", int, "Baud rate", None),
    "byte_size": ("bytesize", int, "Data bits", VALID_BYTE_SIZES),
    "parity": ("parity", str, "Parity", VALID_PARITIES),
    "stop_bits": ("stopbits", float, "Stop bits", VALID_STOP_BITS),
}

# USB-serial chip identification by (vendor_id, product_id).
# Tuple is (chip_model_name, usb_speed_class, max_baud).
#   usb_speed: "full" (12 Mbit/s, 1 ms minimum latency) or
#              "high" (480 Mbit/s, 125 us minimum latency).
#              Full-speed chips cannot reach sub-millisecond round-trip
#              latency regardless of host or driver tuning.
#   max_baud:  highest baud rate the chip supports per its datasheet.
# Source: chip datasheets and FTDI/Silicon Labs/WCH product pages.
USB_SERIAL_CHIPS: dict[tuple[int, int], tuple[str, str, int]] = {
    # FTDI - Future Technology Devices International (vid 0x0403)
    (0x0403, 0x6001): ("FTDI FT232R / FT245R", "full", 3_000_000),
    (0x0403, 0x6010): ("FTDI FT2232C/D/H", "high", 12_000_000),
    (0x0403, 0x6011): ("FTDI FT4232H", "high", 12_000_000),
    (0x0403, 0x6014): ("FTDI FT232H", "high", 12_000_000),
    (0x0403, 0x6015): ("FTDI FT230X / FT231X / FT234XD", "full", 3_000_000),
    (0x0403, 0x6040): ("FTDI FT4233HP", "high", 12_000_000),
    (0x0403, 0x6041): ("FTDI FT4232HP", "high", 12_000_000),
    (0x0403, 0x6042): ("FTDI FT2232HP", "high", 12_000_000),
    (0x0403, 0x6043): ("FTDI FT232HP", "high", 12_000_000),
    # Silicon Labs (vid 0x10C4)
    (0x10C4, 0xEA60): ("Silicon Labs CP2102 / CP2102N", "full", 921_600),
    (0x10C4, 0xEA70): ("Silicon Labs CP2105", "full", 921_600),
    (0x10C4, 0xEA71): ("Silicon Labs CP2108", "full", 921_600),
    (0x10C4, 0xEA80): ("Silicon Labs CP2110", "full", 1_000_000),
    # WCH (vid 0x1A86) - cheap chips on most $5 dev boards
    (0x1A86, 0x7522): ("WCH CH340", "full", 2_000_000),
    (0x1A86, 0x7523): ("WCH CH340", "full", 2_000_000),
    (0x1A86, 0x5523): ("WCH CH341", "full", 2_000_000),
    (0x1A86, 0x55D3): ("WCH CH343", "full", 6_000_000),
    (0x1A86, 0x55D4): ("WCH CH9102", "full", 4_000_000),
    # Prolific (vid 0x067B) - older USB-serial chips
    (0x067B, 0x2303): ("Prolific PL2303", "full", 1_500_000),
    (0x067B, 0x23A3): ("Prolific PL2303GC", "full", 12_000_000),
    (0x067B, 0x23B3): ("Prolific PL2303GL", "full", 12_000_000),
    # Native USB CDC from microcontroller vendors (common)
    (0x2341, 0x0043): ("Arduino Uno (ATmega16U2 native USB)", "full", 2_000_000),
    (0x2341, 0x8036): ("Arduino Leonardo (ATmega32U4 native USB)", "full", 2_000_000),
    (0x16C0, 0x0483): ("Teensy 2.x (ATmega32U4 native USB)", "full", 2_000_000),
    (0x16C0, 0x0489): ("Teensy 3.x / 4.x (ARM native USB)", "high", 12_000_000),
    (0x239A, 0x800B): ("Adafruit Metro M4 / Feather M4", "full", 2_000_000),
    (0x2E8A, 0x000A): ("Raspberry Pi RP2040 (Pico) native USB", "full", 2_000_000),
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


# Field names exposed by /port.chip.<field> subcommands.  Order is the
# display order in the full /port.chip dump.
CHIP_FIELDS: tuple[str, ...] = (
    "device",
    "description",
    "manufacturer",
    "product",
    "serial",
    "location",
    "interface",
    "vid_pid",
    "model",
    "usb_speed",
    "negotiated",
    "driver",
    "latency_timer",
    "max_baud",
    "permissions",
    "in_use",
)

# Field labels for the multi-line dump.  Width-padded to 14 characters
# so values line up.
CHIP_FIELD_LABELS: dict[str, str] = {
    "device":        "Device",
    "description":   "Description",
    "manufacturer":  "Manufacturer",
    "product":       "Product",
    "serial":        "Serial",
    "location":      "Location",
    "interface":     "Interface",
    "vid_pid":       "VID:PID",
    "model":         "Model",
    "usb_speed":     "USB speed",
    "negotiated":    "Negotiated",
    "driver":        "Driver",
    "latency_timer": "Latency timer",
    "max_baud":      "Max baud",
    "permissions":   "Permissions",
    "in_use":        "In use",
}


@dataclass
class ChipFacts:
    """Everything we know about one connected serial port.

    Fields default to ``None`` so the gather function can populate only
    what it can determine on the current platform.  ``None`` renders as
    ``n/a`` in command output.
    """

    device: str | None = None
    description: str | None = None
    manufacturer: str | None = None
    product: str | None = None
    serial: str | None = None
    location: str | None = None
    interface: str | None = None
    vid_pid: str | None = None
    model: str | None = None
    usb_speed: str | None = None
    negotiated: str | None = None
    driver: str | None = None
    latency_timer: str | None = None
    max_baud: str | None = None
    permissions: str | None = None
    in_use: str | None = None
    # Color hint for the usb_speed line in the full dump (not a field).
    _usb_speed_color: str | None = None


def _read_sysfs(*parts: str) -> str | None:
    """Read a single line from a sysfs file.  Returns None on any error."""
    try:
        path = os.path.join("/sys", *parts)
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def _linux_usb_path_for_tty(device: str) -> str | None:
    """Resolve a /dev/ttyUSB* or /dev/ttyACM* device to its USB sysfs path.

    Returns the sysfs directory of the parent USB interface, or None if
    the device isn't a USB serial port.  Used to read driver, latency
    timer, and negotiated speed for that specific port.
    """
    if sys.platform != "linux":
        return None
    name = os.path.basename(device)
    sysfs = f"/sys/class/tty/{name}"
    if not os.path.exists(sysfs):
        return None
    try:
        return os.path.realpath(sysfs)
    except OSError:
        return None


def _gather_linux_extras(facts: ChipFacts, device: str) -> None:
    """Populate driver/latency_timer/negotiated fields from Linux sysfs."""
    sysfs = _linux_usb_path_for_tty(device)
    if not sysfs:
        return
    # Walk up to find the USB device that exposes idVendor/speed.
    usb_dev = sysfs
    for _ in range(8):  # bounded to avoid runaway
        if os.path.exists(os.path.join(usb_dev, "idVendor")):
            break
        parent = os.path.dirname(usb_dev)
        if parent == usb_dev:
            break
        usb_dev = parent
    speed = _read_sysfs(usb_dev, "speed") if os.path.isabs(usb_dev) else None
    if speed:
        if speed == "12":
            facts.negotiated = "12 Mbit/s (Full-Speed)"
        elif speed == "480":
            facts.negotiated = "480 Mbit/s (High-Speed)"
        elif speed == "5000":
            facts.negotiated = "5 Gbit/s (Super-Speed)"
        elif speed == "10000":
            facts.negotiated = "10 Gbit/s (Super-Speed+)"
        else:
            facts.negotiated = f"{speed} Mbit/s"
    # Driver: look at /sys/class/tty/<name>/device/driver
    driver_link = f"/sys/class/tty/{os.path.basename(device)}/device/driver"
    if os.path.islink(driver_link):
        try:
            facts.driver = os.path.basename(os.readlink(driver_link))
        except OSError:
            pass
    # FTDI latency timer: /sys/bus/usb-serial/devices/<name>/latency_timer
    lt_path = f"/sys/bus/usb-serial/devices/{os.path.basename(device)}/latency_timer"
    try:
        with open(lt_path, encoding="utf-8") as f:
            facts.latency_timer = f"{f.read().strip()} ms"
    except OSError:
        pass


def _check_in_use(device: str) -> str:
    """Return 'yes' or 'no' for whether the device is currently open.

    Cross-platform implementation: try to open the port read-only with
    the most-non-disruptive flags possible (no flow control, no DSR/DTR
    changes), then immediately close it.  If the open succeeds, nothing
    else has the port (we just briefly held it).  If the open fails,
    something else has the port -- typically the current termapy session
    if connected, or another process otherwise.

    The open attempt uses ``timeout=0`` so it doesn't block waiting for
    data, and explicitly disables hardware flow control to minimise the
    chance of glitching DTR or RTS on the connected device (the famous
    Arduino auto-reset gotcha).  Even so, briefly opening a serial port
    can momentarily toggle modem-control lines on some hardware; this
    is unavoidable for any "is the port busy" check that doesn't rely
    on platform-specific kernel introspection.
    """
    try:
        import serial

        s = serial.Serial(
            port=device,
            timeout=0,
            write_timeout=0,
            rtscts=False,
            dsrdtr=False,
            xonxoff=False,
        )
        s.close()
        return "no"
    except (OSError, serial.SerialException):
        # Could be "busy", "permission denied", or "device does not
        # exist" -- but in the context of checking a port we already
        # know exists from comports(), the overwhelmingly likely cause
        # is that another process (or the current termapy session) has
        # it open.  Report "yes" rather than over-classifying.
        return "yes"


def _check_permissions(device: str) -> str:
    """Return 'ok' if r/w access, 'denied' otherwise, 'n/a' on Windows."""
    if sys.platform == "win32":
        # Windows has different ACL semantics; os.access on COM ports is
        # unreliable.  Skip the check rather than report a false answer.
        return "n/a"
    if not os.path.exists(device):
        return "n/a"
    if os.access(device, os.R_OK | os.W_OK):
        return "ok"
    return "denied"


def _facts_from_port_info(p: Any) -> ChipFacts:
    """Build a ChipFacts from a pyserial ListPortInfo plus platform extras."""
    facts = ChipFacts(
        device=p.device,
        description=p.description if p.description and p.description != "n/a" else None,
        manufacturer=p.manufacturer,
        product=p.product,
        serial=p.serial_number,
        location=p.location,
        interface=p.interface,
    )
    if p.vid is not None and p.pid is not None:
        facts.vid_pid = f"{p.vid:04X}:{p.pid:04X}"
        chip = USB_SERIAL_CHIPS.get((p.vid, p.pid))
        if chip:
            chip_name, speed, max_baud = chip
            facts.model = chip_name
            if speed == "full":
                facts.usb_speed = "USB Full-Speed (1 ms min latency)"
                facts._usb_speed_color = "yellow"
            else:
                facts.usb_speed = "USB High-Speed (125 us min latency)"
                facts._usb_speed_color = "green"
            facts.max_baud = f"{max_baud:,} baud"
        else:
            facts.model = "unknown"
            facts.usb_speed = "unknown (chip not in lookup table)"
            facts._usb_speed_color = "yellow"
    else:
        facts.vid_pid = "not a USB device"
    facts.permissions = _check_permissions(p.device)
    facts.in_use = _check_in_use(p.device)
    _gather_linux_extras(facts, p.device)
    if (
        facts.latency_timer is None
        and sys.platform == "win32"
        and facts.model
        and facts.model.startswith("FT")
    ):
        facts.latency_timer = "n/a (Windows - check Device Manager)"
    return facts


def gather_chip_facts(port_name: str) -> ChipFacts | None:
    """Look up the named port and return all known facts about it.

    Args:
        port_name: Exact device name (e.g. ``COM3`` or ``/dev/ttyUSB0``).

    Returns:
        ChipFacts on success, or None if no connected port matches.
    """
    from serial.tools.list_ports import comports

    for p in comports():
        if p.device == port_name:
            return _facts_from_port_info(p)
    return None


def _gather_all_chip_facts() -> list[ChipFacts]:
    """Return ChipFacts for every connected port, sorted by device name."""
    from serial.tools.list_ports import comports

    return [
        _facts_from_port_info(p)
        for p in sorted(comports(), key=lambda x: x.device)
    ]


def _format_facts_full(facts: ChipFacts) -> list[Msg]:
    """Format a single ChipFacts as a multi-line dump."""
    msgs: list[Msg] = [_msg(f"{facts.device}", "green")]
    for field_name in CHIP_FIELDS:
        if field_name == "device":
            continue  # already shown as the header line
        value = getattr(facts, field_name)
        if value is None:
            continue
        label = CHIP_FIELD_LABELS[field_name]
        line = f"  {label:<14}{value}"
        if field_name == "usb_speed" and facts._usb_speed_color:
            msgs.append(_msg(line, facts._usb_speed_color))
        elif field_name == "latency_timer" and value != "1 ms":
            msgs.append(_msg(line + "  (set to 1 for low latency)", "yellow"))
        elif field_name == "permissions" and value == "denied":
            msgs.append(_msg(line, "red"))
        else:
            msgs.append(_msg(line))
    return msgs


def chip_info(arg: str, current_port: str) -> Result:
    """Show full chip info for one port, all ports, or the current port.

    Args:
        arg: Empty string (use current_port), exact device name, or
            ``"*"`` for all connected ports.
        current_port: The port name from ``cfg["port"]``, used when arg
            is empty.

    Returns:
        Messages with per-port chip information.
    """
    arg = arg.strip()

    # All-ports mode
    if arg == "*":
        all_facts = _gather_all_chip_facts()
        if not all_facts:
            return _result([_msg("No serial ports found", "yellow")])
        msgs: list[Msg] = []
        for i, facts in enumerate(all_facts):
            if i > 0:
                msgs.append(_msg(""))
            msgs.extend(_format_facts_full(facts))
        return _result(msgs)

    # Specific port name (or current port if arg is empty)
    target = arg or current_port
    if not target:
        return _result([_msg("No current port set.", "red")])
    facts = gather_chip_facts(target)
    if facts is None:
        return _result([_msg(f"No port matching {target!r}", "yellow")])
    return _result(_format_facts_full(facts))


def chip_field(field: str, arg: str, current_port: str) -> Result:
    """Show a single field's value for one or more ports.

    Args:
        field: Name of the ChipFacts field to query (e.g. ``"driver"``).
        arg: Empty string (use current_port), exact device name, or
            ``"*"`` for all connected ports.
        current_port: The port name from ``cfg["port"]``, used when arg
            is empty.

    Returns:
        Messages with one line per port (just the value if a single
        port was requested, or ``"<device>: <value>"`` for ``*``).
    """
    if field not in CHIP_FIELDS:
        return _result([_msg(f"Unknown chip field: {field!r}", "red")])

    arg = arg.strip()

    if arg == "*":
        all_facts = _gather_all_chip_facts()
        if not all_facts:
            return _result([_msg("No serial ports found", "yellow")])
        msgs: list[Msg] = []
        for facts in all_facts:
            value = getattr(facts, field)
            display = "n/a" if value is None else str(value)
            msgs.append(_msg(f"{facts.device}: {display}"))
        return _result(msgs)

    target = arg or current_port
    if not target:
        return _result([_msg("No current port set.", "red")])
    facts = gather_chip_facts(target)
    if facts is None:
        return _result([_msg(f"No port matching {target!r}", "yellow")])
    value = getattr(facts, field)
    display = "n/a" if value is None else str(value)
    return _result([_msg(display)])


def port_info(cfg: Mapping[str, Any], ser: Any | None) -> Result:
    """Format comprehensive port status, frame, USB chip info, and live signals.

    Output is organized into three sections:

    1. Configured serial parameters (port name, state, baud, frame, flow,
       encoding, file-transfer root if set)
    2. USB chip identification (model, USB speed class, VID:PID, driver,
       latency timer, max baud) -- only included if the configured port
       is currently enumerable via comports().  Skipped silently for
       FakeSerial (DEMO) or non-USB ports.
    3. Live hardware signal lines (DTR, RTS, CTS, DSR, RI, CD) -- only
       included when the port is connected.

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
    xfer_root = cfg.get("file_xfer_root", "")
    if xfer_root:
        msgs.append(_msg(f"  Xfer root:    {xfer_root}"))

    # USB chip section -- looked up from the OS, not from the open Serial
    # object, so it works whether or not the port is currently connected.
    # Shows the same fields as /port.chip (minus the device name, which
    # is already shown at the top of this report as "Port:").  Skipped
    # silently if the port name doesn't match any enumerable device
    # (e.g. FakeSerial / DEMO, unplugged cable, non-USB port).
    port_name = cfg.get("port", "")
    if port_name:
        facts = gather_chip_facts(port_name)
        if facts is not None:
            msgs.append(_msg(""))
            for field_name in CHIP_FIELDS:
                if field_name == "device":
                    continue  # already shown as the Port: header
                value = getattr(facts, field_name)
                if value is None:
                    continue
                label = CHIP_FIELD_LABELS[field_name]
                line = f"  {label:<14s}{value}"
                if field_name == "usb_speed" and facts._usb_speed_color:
                    msgs.append(_msg(line, facts._usb_speed_color))
                elif field_name == "latency_timer" and value != "1 ms":
                    msgs.append(
                        _msg(line + "  (set to 1 for low latency)", "yellow")
                    )
                else:
                    msgs.append(_msg(line))

    if connected:
        msgs.append(_msg(""))
        try:
            for name in ("dtr", "rts", "cts", "dsr", "ri", "cd"):
                label = f"{name.upper()}:"
                msgs.append(_msg(f"  {label:<14s}{int(getattr(ser, name))}"))
        except OSError:
            pass
    return _result(msgs)


def get_set_prop(ser: Any | None, cfg: Mapping[str, Any], key: str, args: str) -> Result:
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
        return _result([_msg("Not connected.", "yellow")])
    try:
        if key == "parity":
            val = val.upper()
        typed = coerce(val)
        if valid and typed not in valid:
            opts = ", ".join(sorted(str(v) for v in valid))
            return _result([_msg(f"Invalid {desc.lower()}: {val} (use {opts})", "red")])
        setattr(ser, attr, typed)
        return _result(
            [_msg(f"{desc} -> {typed}")],
            update_title=True,
            cfg_update={key: typed},
        )
    except ValueError:
        return _result([_msg(f"Invalid {desc.lower()}: {val}", "red")])
    except OSError as e:
        return _result([_msg(f"{desc} error: {e}", "red")])


def get_set_flow(ser: Any | None, cfg: Mapping[str, Any], args: str) -> Result:
    """Get or set flow control mode.

    Args:
        ser: Serial-like object, or None if disconnected.
        cfg: Config dict.
        args: Flow mode string, or empty to read.
    """
    val = args.strip().lower()
    if not val:
        fc = cfg.get("flow_control", "none")
        suffix = " (disconnected)" if ser is None else ""
        return _result([_msg(f"{fc}{suffix}")])
    if ser is None:
        return _result([_msg("Not connected.", "yellow")])
    if val not in VALID_FLOW_CONTROLS:
        return _result([_msg(
            f"Invalid flow control: {val} (use none/rtscts/xonxoff/manual)", "red"
        )])
    try:
        ser.rtscts = (val == "rtscts")
        ser.xonxoff = (val == "xonxoff")
        return _result(
            [_msg(f"Flow control -> {val}")],
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
            return _result([_msg("Not connected.", "yellow")])
        try:
            return _result([_msg(f"{int(getattr(ser, line))}")])
        except OSError as e:
            return _result([_msg(f"{label} read error: {e}", "red")])
    if not connected:
        return _result([_msg("Not connected.", "yellow")])
    state = parse_bool_value(val)
    if state is None:
        return _result([_msg(f"Invalid {label} value: {val} (use 0/1/on/off)", "red")])
    try:
        setattr(ser, line, state)
        return _result(
            [_msg(f"{label} -> {int(state)}")],
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
        return _result([_msg("Not connected.", "yellow")])
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
        return _result([_msg("Not connected.", "yellow")])
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
