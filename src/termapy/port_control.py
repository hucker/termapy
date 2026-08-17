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
import re
import sys
from dataclasses import dataclass
from typing import Any, Mapping

from termapy.defaults import (
    VALID_BYTE_SIZES,
    VALID_FLOW_CONTROLS,
    VALID_PARITIES,
    VALID_STOP_BITS,
)
from termapy.scripting import format_duration
from termapy.usb import chip as _usb_chip, vendor_for as _usb_vendor_for

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

# Regex for mode strings like N81, E71, O81.5
# Parity-first ("N81"): termapy's original form.  Parity is N/E/O/M/S,
# data bits 5-8, stop bits 1 / 1.5 / 2.
_MODE_RE = re.compile(r"^([NEOMS])([5-8])(1\.5|[12])$", re.IGNORECASE)
# Data-bits-first ("8N1"): the near-universal serial convention (PuTTY,
# Tera Term, minicom, screen).  Same three fields, first two swapped.
_MODE_RE_DATABITS_FIRST = re.compile(r"^([5-8])([NEOMS])(1\.5|[12])$", re.IGNORECASE)


def parse_mode(mode: str) -> tuple[str, int, float] | None:
    """Parse a serial mode string into ``(parity, byte_size, stop_bits)``.

    Accepts both field orderings; they can't collide because one starts
    with a parity letter and the other with a data-bits digit:

    * parity-first -- ``'N81'``, ``'E71'``, ``'O81.5'``
    * data-bits-first -- ``'8N1'``, ``'7E1'``, ``'8O1.5'`` (the conventional
      form most serial tools use)

    Parity is N/E/O/M/S, data bits 5-8, stop bits 1 / 1.5 / 2.

    Returns:
        Tuple of (parity, byte_size, stop_bits), or None if invalid.
    """
    s = mode.strip()
    m = _MODE_RE.match(s)
    if m:
        parity, byte_size, stop_bits = m.group(1), m.group(2), m.group(3)
    else:
        m = _MODE_RE_DATABITS_FIRST.match(s)
        if not m:
            return None
        byte_size, parity, stop_bits = m.group(1), m.group(2), m.group(3)
    return parity.upper(), int(byte_size), float(stop_bits)


#: Line-ending tokens accepted by /port.connect.  Values are the literal
#: strings stored in cfg["eol"].
_LINE_ENDING_TOKENS: dict[str, str] = {
    "cr": "\r",
    "lf": "\n",
    "crlf": "\r\n",
}


def parse_open_args(
    args: str,
) -> tuple[
    str | None,
    int | None,
    tuple[str, int, float] | None,
    str | None,
    bool | None,
    str | None,
]:
    """Parse /port.connect arguments.

    Syntax: ``{port=<name> | <name>} {baud} {mode} {line_ending} {echo}``.
    A *bare* port name, if supplied, MUST be the first token.  The
    explicit ``port=<name>`` form may appear anywhere and forces the value
    to be the port -- use it for a purely-numeric serial number, which a
    bare token would otherwise read as a baud rate.  Everything else is
    order-independent; all fields optional (unspecified -> current cfg).

    Token classification:

    * ``port=<name>`` -- explicit port (device / SN / ``SN|COM`` chain / URL)
    * ``cr`` / ``lf`` / ``crlf`` -- line ending
    * ``echo`` / ``noecho`` -- echo toggle
    * ``N81`` / ``8N1`` / etc. -- serial mode, either field order (parse_mode)
    * purely numeric -- baud rate
    * a bare first token matching none of the above -- port name

    Args:
        args: Raw argument string from the REPL.

    Returns:
        Tuple of ``(port_name, baud_rate, mode_tuple, line_ending,
        echo, error_message)``.  ``line_ending`` is the literal
        ``"\\r"`` / ``"\\n"`` / ``"\\r\\n"`` (or None).  ``echo`` is
        True / False / None.  ``error_message`` is non-None only when
        a token cannot be classified.
    """
    port: str | None = None
    baud: int | None = None
    mode: tuple[str, int, float] | None = None
    line_ending: str | None = None
    echo: bool | None = None
    first = True

    def _err(msg: str) -> tuple[
        None, None, None, None, None, str
    ]:
        return (None, None, None, None, None, msg)

    for token in args.split():
        # Explicit port=<name> may appear anywhere and wins outright --
        # the escape hatch for a numeric SN that a bare token would read
        # as baud.  Case-insensitive key, value kept verbatim.
        if token[:5].lower() == "port=":
            if port is not None:
                return _err(f"Duplicate port: {token}")
            value = token[5:]
            if not value:
                return _err("port= requires a value")
            port = value
            first = False  # the port slot is now filled
            continue
        # Otherwise a bare port name is always first, or not supplied at
        # all.  We decide on the first token: if it classifies as one of
        # the later fields, there's no port; otherwise it's the port name.
        if first:
            first = False
            # Peek each classifier before falling through to "port".
            parsed_mode = parse_mode(token)
            lower = token.lower()
            if (
                parsed_mode is None
                and not token.isdigit()
                and lower not in _LINE_ENDING_TOKENS
                and lower not in ("echo", "noecho")
            ):
                port = token
                continue
            # Fall through: first token is not a port, classify it
            # normally below.

        lower = token.lower()
        if lower in _LINE_ENDING_TOKENS:
            if line_ending is not None:
                return _err(f"Duplicate line ending: {token}")
            line_ending = _LINE_ENDING_TOKENS[lower]
            continue
        if lower == "echo":
            if echo is not None:
                return _err(f"Duplicate echo token: {token}")
            echo = True
            continue
        if lower == "noecho":
            if echo is not None:
                return _err(f"Duplicate echo token: {token}")
            echo = False
            continue
        parsed = parse_mode(token)
        if parsed:
            if mode is not None:
                return _err(f"Duplicate mode: {token}")
            mode = parsed
            continue
        if token.isdigit():
            if baud is not None:
                return _err(f"Duplicate baud rate: {token}")
            baud = int(token)
            continue
        # Reached only when a later token looks like a port name but
        # port-first is required, so this is an error.
        return _err(
            f"Unexpected argument: {token!r}. "
            f"Port name must come first; other tokens are "
            f"baud / mode / cr|lf|crlf / echo|noecho."
        )
    return port, baud, mode, line_ending, echo, None


def set_mode(ser: Any | None, cfg: Mapping[str, Any], args: str) -> Result:
    """Set serial frame parameters from a mode string and optional baud rate.

    Accepts: {baud} {mode}, e.g. '9600 N81', 'E71', '115200'.

    Args:
        ser: Serial-like object, or None if disconnected.
        cfg: Config dict.
        args: Mode string with optional baud rate prefix.
    """
    if not args.strip():
        serial = cfg["serial"]
        sb = serial["stop_bits"]
        sb_str = str(int(sb)) if sb == int(sb) else str(sb)
        current = (
            f"{serial['baud_rate']} "
            f"{serial['byte_size']}{serial['parity']}{sb_str}"
        )
        suffix = " (disconnected)" if ser is None else ""
        return _result([_msg(f"{current}{suffix}")])

    baud: int | None = None
    mode: tuple[str, int, float] | None = None
    for token in args.split():
        parsed = parse_mode(token)
        if parsed:
            if mode is not None:
                return _result([_msg(f"Duplicate mode: {token}", "red")])
            mode = parsed
            continue
        if token.isdigit():
            if baud is not None:
                return _result([_msg(f"Duplicate baud rate: {token}", "red")])
            baud = int(token)
            continue
        return _result(
            [_msg(f"Invalid mode argument: {token} (use e.g. 9600 N81)", "red")]
        )

    if baud is None and mode is None:
        return _result([_msg("Nothing to set.", "yellow")])

    if ser is None:
        return _result([_msg("Not connected.", "yellow")])

    cfg_update: dict[str, Any] = {}
    msgs: list[Msg] = []
    try:
        if baud is not None:
            ser.baudrate = baud
            cfg_update["baud_rate"] = baud
        if mode is not None:
            parity, byte_size, stop_bits = mode
            ser.parity = parity
            ser.bytesize = byte_size
            ser.stopbits = stop_bits
            cfg_update["parity"] = parity
            cfg_update["byte_size"] = byte_size
            cfg_update["stop_bits"] = stop_bits
        # Build summary
        serial = cfg["serial"]
        sb = cfg_update.get("stop_bits", serial["stop_bits"])
        sb_str = str(int(sb)) if sb == int(sb) else str(sb)
        summary = (
            f"{cfg_update.get('baud_rate', serial['baud_rate'])} "
            f"{cfg_update.get('byte_size', serial['byte_size'])}"
            f"{cfg_update.get('parity', serial['parity'])}"
            f"{sb_str}"
        )
        msgs.append(_msg(f"Mode -> {summary}"))
    except (ValueError, OSError) as e:
        return _result([_msg(f"Mode error: {e}", "red")])
    return _result(msgs, update_title=True, cfg_update=cfg_update)


def _msg(text: str, color: str | None = None) -> Msg:
    return (text, color)


def _result(msgs: list[Msg], **side_effects: Any) -> Result:
    return msgs, side_effects


def list_ports() -> Result:
    """List available serial ports as a picker-style table.

    Output matches the TUI port picker and the ``--ports`` CLI flag:
    one line per port with PORT / MFG / DESCRIPTION / CHIP / SPEED /
    VID:PID / SN columns.  Width adapts to the current terminal so
    low-priority columns (speed, chip, vid_pid) drop before the row
    wraps.

    Returns:
        Messages: header, separator, then one row per port.
    """
    import shutil

    from termapy.port_format import format_table

    # fast=True: the /port.list table has no in_use column, so don't run
    # the invasive probe just to discard the result.
    facts_list = _gather_all_chip_facts(fast=True)
    if not facts_list:
        return _result([_msg("No serial ports found", "yellow")])
    row_width = shutil.get_terminal_size((80, 24)).columns
    return _result([_msg(line) for line in format_table(facts_list, row_width)])


# Field names exposed by /port.chip.<field> subcommands.  Order is the
# display order in the full /port.chip dump.
CHIP_FIELDS: tuple[str, ...] = (
    "device",
    "description",
    "manufacturer",
    "vendor",
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
    "device": "Device",
    "description": "Description",
    "manufacturer": "Manufacturer",
    "vendor": "Vendor",
    "product": "Product",
    "serial": "Serial",
    "location": "Location",
    "interface": "Interface",
    "vid_pid": "VID:PID",
    "model": "Model",
    "usb_speed": "USB speed",
    "negotiated": "Negotiated",
    "driver": "Driver",
    "latency_timer": "Latency timer",
    "max_baud": "Max baud",
    "permissions": "Permissions",
    "in_use": "In use",
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
    # Silicon-vendor name resolved from the VID via ``usb_vendor``.
    # Independent of ``manufacturer`` (which is the descriptor / INF
    # string) -- they can disagree, and that disagreement is itself
    # diagnostic information.
    vendor: str | None = None
    # Color hint for the usb_speed line in the full dump (not a field).
    _usb_speed_color: str | None = None


LOOPBACK_PORT = "loop://"


def loopback_port_facts() -> ChipFacts:
    """Honest ChipFacts for the pyserial loopback (``loop://``).

    Deliberately NOT a reserved name and NOT part of the DEMO machinery:
    a loopback isn't a device, so it gets only a name + description and
    leaves every USB field (VID:PID, serial, chip, ...) as ``None`` -- they
    render as blanks, never invented identity.  The port picker surfaces
    this as a selectable row; the spec itself already opens via
    ``serial.serial_for_url`` (it echoes whatever you write straight back,
    which is handy for exercising the real read/write path in CI or by
    hand -- unlike DEMO, which simulates a *responding* device).
    """
    return ChipFacts(
        device=LOOPBACK_PORT,
        description="Pyserial Loopback",
    )


def opens_without_enumeration(port: str) -> bool:
    """True if ``port`` can be opened without appearing in ``comports()``.

    pyserial URL handlers (``loop://``, ``socket://``, ``rfc2217://``, ...)
    and the reserved virtual ports (the ``DEMO`` family) open directly and
    never enumerate.  The "is it in comports()?" availability gate used when
    loading a config must not reject them, or a config with e.g.
    ``"port": "loop://"`` would wrongly pop the port picker instead of
    connecting.  ``synthetic_facts_for_reserved`` is the single source of
    truth for which names are reserved.
    """
    if not port:
        return False
    if "://" in port:
        return True
    return synthetic_facts_for_reserved(port.upper()) is not None


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


def _gather_windows_extras(facts: ChipFacts, device: str) -> None:
    """Best-effort driver-name lookup on Windows via the registry.

    Walks ``HKLM\\SYSTEM\\CurrentControlSet\\Enum`` looking for a device
    whose ``Device Parameters\\PortName`` value matches ``device``
    (e.g. "COM4").  Reads the ``Service`` value at that key, which is
    the driver service name (e.g. "FTDIBUS", "usbser", "silabser").

    Stdlib only -- ``winreg`` is part of CPython on Windows.  Silent on
    non-Windows hosts and on any registry error.  Bounded walk so a
    massive Enum tree doesn't slow down ``--ports``.
    """
    if sys.platform != "win32":
        return
    try:
        import winreg  # stdlib on Windows
    except ImportError:
        return

    # Walk Enum\<bus>\<vid_pid>\<instance> looking for our COM port.
    # Most USB-serial devices live under USB or USBSER, but some
    # virtual ports (Bluetooth, com0com) live elsewhere -- so walk all
    # top-level bus subkeys.
    try:
        enum_root = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum"
        )
    except OSError:
        return
    try:
        for bus in _enum_subkeys(winreg, enum_root):
            try:
                bus_key = winreg.OpenKey(enum_root, bus)
            except OSError:
                continue
            with bus_key:
                for hwid in _enum_subkeys(winreg, bus_key):
                    try:
                        hwid_key = winreg.OpenKey(bus_key, hwid)
                    except OSError:
                        continue
                    with hwid_key:
                        for inst in _enum_subkeys(winreg, hwid_key):
                            if _windows_match_inst(
                                winreg, hwid_key, inst, device, facts
                            ):
                                return
    finally:
        enum_root.Close()


def _enum_subkeys(winreg_mod, key) -> list[str]:
    """Return every immediate subkey name of ``key`` (winreg-only)."""
    names: list[str] = []
    i = 0
    while True:
        try:
            names.append(winreg_mod.EnumKey(key, i))
        except OSError:
            break
        i += 1
    return names


def _windows_match_inst(
    winreg_mod, parent_key, inst: str, device: str, facts: ChipFacts
) -> bool:
    """Check one Enum instance node for a PortName match; populate
    ``facts.driver`` and (if pyserial didn't already) ``facts.location``.

    Returns True if the instance owns ``device`` (the caller stops
    walking).  False otherwise.
    """
    try:
        inst_key = winreg_mod.OpenKey(parent_key, inst)
    except OSError:
        return False
    with inst_key:
        # Device Parameters\PortName carries "COMx" for serial-bound nodes.
        try:
            params = winreg_mod.OpenKey(inst_key, "Device Parameters")
        except OSError:
            return False
        with params:
            try:
                port_name, _ = winreg_mod.QueryValueEx(params, "PortName")
            except OSError:
                return False
        if port_name != device:
            return False
        try:
            service, _ = winreg_mod.QueryValueEx(inst_key, "Service")
        except OSError:
            service = None
        facts.driver = str(service) if service else None
        # Pyserial returns None for facts.location on FTDI ports because
        # the FTDIBUS pseudo-bus driver hides location info from
        # SetupAPI.  Fall back to the registry: LocationInformation
        # may be at this key directly (Microsoft's usbser does this),
        # or under the USB partner device that shares our ContainerID
        # (FTDI presents both an FTDIBUS\... port node and a USB\...
        # bus node; LocationInformation lives on the USB side).
        if not facts.location:
            facts.location = _windows_lookup_location(
                winreg_mod, inst_key
            )
        return True


def _windows_lookup_location(winreg_mod, inst_key) -> str | None:
    """Return the device's bus-location string, walking via ContainerID
    if necessary.

    Microsoft's ``usbser`` puts ``LocationInformation`` directly on the
    COM port's Enum node.  FTDI's ``FTSER2K`` puts the COM node under
    ``FTDIBUS\\...`` which has no location; the matching USB device
    (under ``USB\\...``) carries it.  Both nodes share the same
    ``ContainerID``, so we use that as the join key.

    The returned string is normalized so hub appears before port
    (``Hub_#0009.Port_#0004`` instead of the registry's port-first
    ``Port_#0004.Hub_#0009``) -- top-of-tree first, matching the way
    Linux's bus-port path reads.
    """
    # Direct: most non-FTDI drivers populate LocationInformation here.
    try:
        loc, _ = winreg_mod.QueryValueEx(inst_key, "LocationInformation")
        if loc:
            return _normalize_windows_location(str(loc))
    except OSError:
        pass
    # Indirect: walk Enum\USB looking for a node with the same
    # ContainerID, then read LocationInformation from there.
    try:
        container_id, _ = winreg_mod.QueryValueEx(inst_key, "ContainerID")
    except OSError:
        return None
    if not container_id:
        return None
    try:
        usb_root = winreg_mod.OpenKey(
            winreg_mod.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Enum\USB",
        )
    except OSError:
        return None
    with usb_root:
        for hwid in _enum_subkeys(winreg_mod, usb_root):
            try:
                hwid_key = winreg_mod.OpenKey(usb_root, hwid)
            except OSError:
                continue
            with hwid_key:
                for inst in _enum_subkeys(winreg_mod, hwid_key):
                    try:
                        node = winreg_mod.OpenKey(hwid_key, inst)
                    except OSError:
                        continue
                    with node:
                        try:
                            cid, _ = winreg_mod.QueryValueEx(
                                node, "ContainerID"
                            )
                        except OSError:
                            continue
                        if cid != container_id:
                            continue
                        try:
                            loc, _ = winreg_mod.QueryValueEx(
                                node, "LocationInformation"
                            )
                        except OSError:
                            return None
                        if loc:
                            return _normalize_windows_location(str(loc))
    return None


def _normalize_windows_location(loc: str) -> str:
    """Reorder ``Port_#NNNN.Hub_#NNNN`` to ``Hub_#NNNN.Port_#NNNN``.

    Windows' ``LocationInformation`` registry value puts the leaf
    (port) before the parent (hub), which reads backward.  Swap to
    hub-then-port so the string reads top-of-tree first, matching
    Linux's ``1-2.3`` and the way users describe physical hardware
    ("plugged into hub 9, port 4").

    Strings that don't match the simple ``Port_#X.Hub_#Y`` shape are
    returned as-is -- some devices report a multi-hub chain or a
    pre-formatted string we shouldn't mangle.
    """
    import re
    m = re.match(r"^(Port_#\d+)\.(Hub_#\d+)$", loc)
    if not m:
        return loc
    return f"{m.group(2)}.{m.group(1)}"


def _check_in_use(device: str, connected_port: str = "") -> str:
    """Report whether another process holds *device* -- non-invasively where possible.

    Returns ``"yes (this session)"``, ``"yes ..."``, or ``"no"``.

    IMPORTANT: callers must only reach this on an *explicit* monitoring
    surface.  Resolution and plain enumeration pass ``fast=True`` so this
    never runs for them -- on Windows it opens the port, and opening a
    bystander port asserts DTR/RTS, which resets Arduino/ESP32 auto-reset
    boards.

    - If *device* is the port this session already holds, report that
      without probing (never open our own port to test it).
    - Linux/macOS: ``lsof`` (via ``_find_port_holder``) reads the kernel
      open-file table and never opens the port, so it cannot disturb any
      board -- and it names the holder, which is exactly what you want
      when an MCP server has grabbed a port with no visible terminal
      running (``"yes (python (PID 8842))"``).
    - Windows: no non-invasive equivalent exists, so briefly open the
      port with DTR and RTS held de-asserted -- the least-invasive probe
      available (avoids pulsing the auto-reset line).
    """
    if connected_port and device == connected_port:
        return "yes (this session)"
    if sys.platform != "win32":
        # Non-invasive: lsof never opens the port.  Names the holder.
        from termapy.serial_engine import _find_port_holder

        holder = _find_port_holder(device)
        return f"yes ({holder})" if holder else "no"
    # Windows: create closed, de-assert DTR/RTS, then open so the DCB
    # applies DTR_CONTROL_DISABLE / RTS_CONTROL_DISABLE instead of the
    # pyserial default (both ENABLE), which is what pulses auto-reset.
    import serial

    s = serial.Serial()
    try:
        s.port = device
        s.timeout = 0
        s.write_timeout = 0
        s.dtr = False
        s.rts = False
        s.open()
        s.close()
        return "no"
    except (OSError, serial.SerialException):
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


def _facts_from_port_info(
    p: Any, connected_port: str = "", *, fast: bool = False
) -> ChipFacts:
    """Build a ChipFacts from a pyserial ListPortInfo plus platform extras.

    ``fast=True`` skips the per-port ``_check_in_use`` probe (which
    opens each port to detect contention -- ~250 ms per port on
    Windows).  Used by ``--watch`` so the poll loop doesn't scale
    linearly with port count.  Fast-gathered records have
    ``in_use=None`` and ``permissions=None`` so callers can tell the
    field is missing rather than False.
    """
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
        # Silicon vendor by VID -- independent of the descriptor / INF
        # string in facts.manufacturer.  Populated even when the (VID,
        # PID) pair isn't in the chip table.
        facts.vendor = _usb_vendor_for(p.vid)
        chip = _usb_chip(p.vid, p.pid)
        if chip:
            facts.model = chip.model
            if chip.speed == "full":
                facts.usb_speed = "USB Full-Speed (1 ms min latency)"
                facts._usb_speed_color = "yellow"
            else:
                facts.usb_speed = "USB High-Speed (125 us min latency)"
                facts._usb_speed_color = "green"
            facts.max_baud = f"{chip.max_baud:,} baud"
        else:
            facts.model = "unknown"
            facts.usb_speed = "unknown (chip not in lookup table)"
            facts._usb_speed_color = "yellow"
    else:
        facts.vid_pid = "not a USB device"
    if not fast:
        facts.permissions = _check_permissions(p.device)
        facts.in_use = _check_in_use(p.device, connected_port)
        # Per-port enrichment (driver, latency_timer, negotiated speed)
        # reads sysfs / the registry.  Cheap relative to _check_in_use
        # but unnecessary for --watch's plug-event detection, which
        # reads only the always-populated identity fields (device,
        # description, model, vid_pid, serial).
        _gather_linux_extras(facts, p.device)
        _gather_windows_extras(facts, p.device)
        if (
            facts.latency_timer is None
            and sys.platform == "win32"
            and facts.model
            and facts.model.startswith("FT")
        ):
            facts.latency_timer = "n/a (Windows - check Device Manager)"
    return facts


def gather_chip_facts(
    port_name: str, connected_port: str = "", *, fast: bool = False
) -> ChipFacts | None:
    """Look up the named port and return all known facts about it.

    ``fast=True`` skips the ``in_use``/``permissions`` probe (which opens
    the port on Windows -- see ``_check_in_use``).  Pass it from any
    surface that doesn't display ``in_use`` so single-port lookups stay
    non-invasive.

    Honors ``TERMAPY_DEMO_FLEET``: when set, searches the synthetic
    fleet instead of real enumeration.  See ``_build_demo_fleet``.

    Args:
        port_name: Exact device name (e.g. ``COM3`` or ``/dev/ttyUSB0``).
        connected_port: The port termapy currently has open, if any.

    Returns:
        ChipFacts on success, or None if no connected port matches.
    """
    if os.environ.get(_DEMO_FLEET_ENV):
        for facts in _build_demo_fleet():
            if facts.device == port_name:
                return facts
        return None
    from serial.tools.list_ports import comports

    for port in comports():
        if port.device == port_name:
            return _facts_from_port_info(port, connected_port, fast=fast)
    # OS didn't enumerate it; fall back to synthesizing a record for
    # reserved virtual ports (DEMO, DEMO_FAIL) so /port.info DEMO and
    # `termapy --info DEMO` work without hardware.
    return synthetic_facts_for_reserved(port_name)


# ─ Demo fleet ─────────────────────────────────────────────────────────────
# When TERMAPY_DEMO_FLEET is set, _gather_all_chip_facts() returns these
# synthetic ports instead of calling comports().  Useful for screenshots,
# docs, hardware-free demos, and cross-platform tests.  Sibling hooks:
# cfg["serial"]["port"] = "DEMO" (fake open) and "DEMO_FAIL" (raise on open).
_DEMO_FLEET_ENV = "TERMAPY_DEMO_FLEET"


def _build_demo_fleet() -> list[ChipFacts]:
    """Return a fixed three-port synthetic fleet."""
    return [
        ChipFacts(
            device="COM3",
            description="USB Serial Port (COM3)",
            manufacturer="FTDI",
            product="FT232R USB UART",
            serial="A1B2C3D4",
            vid_pid="0403:6001",
            model="FTDI FT232R / FT245R",
            usb_speed="USB Full-Speed (1 ms min latency)",
            max_baud="3,000,000 baud",
        ),
        ChipFacts(
            device="COM4",
            description="USB Serial Port (COM4)",
            manufacturer="Silicon Labs",
            product="CP2102 USB to UART Bridge Controller",
            serial="0001",
            vid_pid="10C4:EA60",
            model="Silicon Labs CP2102",
            usb_speed="USB Full-Speed (1 ms min latency)",
            max_baud="1,000,000 baud",
        ),
        ChipFacts(
            device="COM7",
            description="USB Serial Port (COM7)",
            manufacturer="Microsoft",
            product="USB Serial Device",
            serial="020026702RYN040952",
            vid_pid="04D8:9036",
            model="-",
            usb_speed="USB Full-Speed (1 ms min latency)",
        ),
    ]


def _gather_all_chip_facts(
    connected_port: str = "", *, fast: bool = False
) -> list[ChipFacts]:
    """Return ChipFacts for every connected port, sorted by device name.

    ``fast=True`` skips the per-port ``_check_in_use`` probe so the
    gather doesn't scale linearly with port count -- ``--watch`` uses
    this to keep the poll loop responsive when many ports are
    enumerated.  Records returned in fast mode have ``in_use=None``
    and ``permissions=None``.

    Honors the ``TERMAPY_DEMO_FLEET`` env var: when set to any
    non-empty value, returns a fixed synthetic fleet instead of
    enumerating real ports.  See ``_build_demo_fleet`` for the roster.
    """
    if os.environ.get(_DEMO_FLEET_ENV):
        return _build_demo_fleet()
    from serial.tools.list_ports import comports

    return [
        _facts_from_port_info(p, connected_port, fast=fast)
        for p in sorted(comports(), key=lambda x: x.device)
    ]


def synthetic_facts_for_reserved(name: str) -> ChipFacts | None:
    """Return synthesized ChipFacts for a reserved virtual port, or None.

    DEMO / DEMO_FAIL are not enumerated by the OS but are reachable
    through termapy's runtime serial paths.  Surfacing them here lets
    ``termapy --ports DEMO --json`` produce a real record so CI
    pipelines can exercise the CLI without hardware -- the same way
    ``loop://`` and other pyserial URL handlers are reachable only
    when explicitly named.

    Returns None for any name that isn't a recognized reserved port,
    so callers can fall through to "no match" handling.
    """
    if name == "DEMO":
        return ChipFacts(
            device="DEMO",
            description="Termapy simulated device",
            manufacturer="termapy",
            model="DEMO",
            usb_speed="virtual (not a USB device)",
            vid_pid="not a USB device",
            in_use="no",
            permissions="ok",
        )
    if name == "DEMO_FAIL":
        return ChipFacts(
            device="DEMO_FAIL",
            description="Termapy simulated device (connect always fails)",
            manufacturer="termapy",
            model="DEMO_FAIL",
            usb_speed="virtual (not a USB device)",
            vid_pid="not a USB device",
            in_use="no",
            permissions="ok",
        )
    if name == "DEMO_JSON":
        return ChipFacts(
            device="DEMO_JSON",
            description="Termapy simulated NDJSON device (modern path)",
            manufacturer="termapy",
            model="DEMO_JSON",
            usb_speed="virtual (not a USB device)",
            vid_pid="not a USB device",
            in_use="no",
            permissions="ok",
        )
    if name == "DEMO_VT100":
        return ChipFacts(
            device="DEMO_VT100",
            description="Termapy simulated VT100 device (cursor-addressed)",
            manufacturer="termapy",
            model="DEMO_VT100",
            usb_speed="virtual (not a USB device)",
            vid_pid="not a USB device",
            in_use="no",
            permissions="ok",
        )
    return None


# ─ Port spec resolution ───────────────────────────────────────────────────
# A `port` spec can be a single value or a '|'-separated fallback chain.
# Each candidate is tried in order; first to resolve wins.  Candidates can
# be literal device names ("COM3", "/dev/ttyUSB0"), USB serial numbers
# (stable across replugs), reserved names ("DEMO", "DEMO_FAIL"), or
# pyserial URLs ("rfc2217://host:2217").  See help/ports.md for the
# user-facing docs.


class AmbiguousSerialNumberError(Exception):
    """A serial-number candidate matched more than one connected device.

    Cheap USB-serial clones (CH340, some PL-2303, generic CP2102 knockoffs)
    often burn a non-unique serial number like ``"0001"`` or leave it
    empty.  Silently picking one of the matches would defeat the point of
    serial-number-based resolution, which is stable unambiguous
    identification.  Resolve this by disambiguating with the literal COM
    name or by using a fallback chain: ``"0001|COM3"``.
    """

    def __init__(self, sn: str, matches: list[str]) -> None:
        self.sn = sn
        self.matches = matches
        super().__init__(
            f"serial number {sn!r} matches {len(matches)} connected "
            f"devices: {', '.join(matches)}"
        )


#: Reserved port names that bypass enumeration.
_RESERVED_PORTS = frozenset({"DEMO", "DEMO_FAIL", "DEMO_JSON"})

#: Match-reason strings returned by ``resolve_port_trace``.
MATCH_LITERAL = "literal"
MATCH_SERIAL = "serial_number"
MATCH_RESERVED = "reserved"
MATCH_URL = "url"


def _match_candidate(
    candidate: str, facts: list[ChipFacts]
) -> tuple[str, str] | None:
    """Try to resolve a single candidate against a port list.

    Returns ``(resolved_device, match_reason)`` on success or ``None`` if
    the candidate matches nothing.  Raises ``AmbiguousSerialNumberError``
    if the candidate matches two or more ports by serial number.
    """
    if not candidate:
        return None
    if candidate.upper() in _RESERVED_PORTS:
        return (candidate, MATCH_RESERVED)
    if "://" in candidate:
        return (candidate, MATCH_URL)
    # Exact device-name match wins before SN lookup so a user who types
    # a literal COM name always gets exactly that port.
    for fact in facts:
        if fact.device == candidate and fact.device is not None:
            return (fact.device, MATCH_LITERAL)
    # Case-insensitive SN match.  Burn-ins are sometimes uppercase on
    # Windows and mixed case on other platforms, so normalize both sides.
    # Filter out any facts with a None device so the matches list is a
    # clean list[str] for the ambiguity-error constructor.
    needle = candidate.lower()
    sn_matches: list[str] = [
        fact.device for fact in facts
        if fact.device is not None
        and fact.serial is not None
        and fact.serial.lower() == needle
    ]
    if len(sn_matches) == 1:
        return (sn_matches[0], MATCH_SERIAL)
    if len(sn_matches) > 1:
        raise AmbiguousSerialNumberError(candidate, sn_matches)
    return None


def resolve_port(spec: str, connected_port: str = "") -> str:
    """Resolve a port spec string to an actual device name.

    The spec is a ``|``-separated list of candidates; each candidate is
    tried in order and the first to resolve wins.  If nothing resolves,
    the *last* candidate is returned verbatim so the downstream
    ``open_serial()`` failure message refers to the user's intended spec.

    Honors ``TERMAPY_DEMO_FLEET`` automatically because
    ``_gather_all_chip_facts()`` does.

    Uses ``fast=True``: resolution matches on identity fields only
    (``device`` / ``serial`` -- see ``_match_candidate``) and must never
    trigger the ``_check_in_use`` probe, which opens every other
    enumerated port and asserts DTR/RTS on it (a reset on Arduino/ESP32
    auto-reset boards).  Since ``open_serial`` calls this on every connect
    and the reconnect loop calls it every 2.5 s, probing here would strobe
    DTR on bystander devices -- so identity-only resolution is both
    sufficient and required.

    Args:
        spec: The raw ``cfg["serial"]["port"]`` value, post-env-expansion.
        connected_port: The currently-connected port.  With ``fast=True``
            this is unused by resolution (kept for signature stability).

    Returns:
        A device name suitable for opening (e.g. ``"COM3"``,
        ``"/dev/ttyUSB0"``), a reserved name (``"DEMO"``,
        ``"DEMO_FAIL"``), or a pyserial URL.

    Raises:
        AmbiguousSerialNumberError: when a candidate SN matches two or
            more connected devices.  The caller is expected to surface
            this as a user-facing error rather than silently picking one.
    """
    facts = _gather_all_chip_facts(connected_port, fast=True)
    candidates = spec.split("|")
    for candidate in candidates:
        result = _match_candidate(candidate, facts)
        if result is not None:
            return result[0]
    # No candidate resolved.  Return the last one so open_serial's error
    # message names what the user most explicitly asked for.
    return candidates[-1]


def resolve_port_trace(
    spec: str, connected_port: str = ""
) -> list[tuple[str, str | None]]:
    """Return per-candidate resolution results for error reporting.

    For each ``|``-separated candidate, returns ``(candidate,
    match_reason)`` where ``match_reason`` is one of ``"literal"``,
    ``"serial_number"``, ``"reserved"``, ``"url"``, ``"ambiguous"``, or
    ``None`` (no match).

    Unlike ``resolve_port``, this function never raises on ambiguity --
    ambiguous candidates are reported as ``"ambiguous"`` so the caller
    can build a single message that describes every candidate in one
    go.
    """
    # fast=True for the same safety reason as resolve_port: tracing must
    # not open bystander ports (identity fields are all _match_candidate
    # reads).
    facts = _gather_all_chip_facts(connected_port, fast=True)
    trace: list[tuple[str, str | None]] = []
    for candidate in spec.split("|"):
        try:
            result = _match_candidate(candidate, facts)
        except AmbiguousSerialNumberError:
            trace.append((candidate, "ambiguous"))
            continue
        trace.append((candidate, result[1] if result else None))
    return trace


def _format_facts_full(facts: ChipFacts) -> list[Msg]:
    """Format a single ChipFacts as a multi-line dump.

    Uses ``format_kv_lines()`` for consistent label coloring and column
    alignment with the other info commands (/term.info, /term.usb_db,
    /port.info, /proto.crc.info).  Per-row coloring -- USB-speed
    indicator, latency-timer warning, denied permissions -- is encoded
    inline as Rich markup on the value side; the cyan label color
    comes from the helper.
    """
    from termapy.plugins import format_kv_lines

    msgs: list[Msg] = [_msg(f"{facts.device}", "green")]

    # Build (label, value) rows, embedding any per-row coloring inline
    # via Rich markup.  All rows then flow through format_kv_lines()
    # for consistent cyan-label / colon / aligned-width formatting.
    rows: list[tuple[str, str]] = []
    for field_name in CHIP_FIELDS:
        if field_name == "device":
            continue  # already shown as the header line
        value = getattr(facts, field_name)
        if value is None:
            continue
        label = CHIP_FIELD_LABELS[field_name]
        sval = str(value)
        if field_name == "usb_speed" and facts._usb_speed_color:
            sval = f"[{facts._usb_speed_color}]{sval}[/]"
        elif (
            field_name == "latency_timer"
            and isinstance(value, str)
            and value
            and value[0].isdigit()
            and value != "1 ms"
        ):
            # Only warn when the timer is a numeric ms value above 1.
            # Strings like "n/a" or "n/a (Windows - check Device Manager)"
            # mean we don't know -- no actionable advice to give.
            sval = f"[yellow]{sval}  (set to 1 for low latency)[/]"
        elif field_name == "permissions" and value == "denied":
            sval = f"[red]{sval}[/]"
        rows.append((label, sval))

    for line in format_kv_lines(rows):
        msgs.append(_msg(line))

    # Nudge: when we see a real USB device whose VID:PID isn't in the
    # chip table, invite the user to report it so the table can grow.
    if (
        facts.model == "unknown"
        and facts.vid_pid
        and facts.vid_pid != "not a USB device"
    ):
        msgs.append(_msg(
            f"  (chip {facts.vid_pid} not in termapy's lookup table -- "
            f"please report at https://github.com/hucker/termapy/issues "
            f"so we can add it)",
            "dim",
        ))
    return msgs


def chip_info(arg: str, current_port: str, connected_port: str = "") -> Result:
    """Show full chip info for one port, all ports, or the current port.

    Args:
        arg: Empty string (use current_port), exact device name, or
            ``"*"`` for all connected ports.
        current_port: The port name from ``cfg["serial"]["port"]``, used when arg
            is empty.
        connected_port: The port termapy currently has open, if any.

    Returns:
        Messages with per-port chip information.
    """
    arg = arg.strip()

    # All-ports mode
    if arg == "*":
        all_facts = _gather_all_chip_facts(connected_port)
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
    # Resolve an SN / fallback-chain spec (e.g. "A1B2C3D4" or "SN|COM3") to
    # a real device first -- gather_chip_facts matches literal device names
    # only, so without this /port.chip fails under an SN-based config even
    # while connected (port_info already resolves; this brings /port.chip
    # into line).
    facts = gather_chip_facts(resolve_port(target, connected_port), connected_port)
    if facts is None:
        return _result([_msg(f"No port matching {target!r}", "yellow")])
    return _result(_format_facts_full(facts))


def chip_field(
    field: str, arg: str, current_port: str, connected_port: str = ""
) -> Result:
    """Show a single field's value for one or more ports.

    Args:
        field: Name of the ChipFacts field to query (e.g. ``"driver"``).
        arg: Empty string (use current_port), exact device name, or
            ``"*"`` for all connected ports.
        current_port: The port name from ``cfg["serial"]["port"]``, used when arg
            is empty.

    Returns:
        Messages with one line per port (just the value if a single
        port was requested, or ``"<device>: <value>"`` for ``*``).
    """
    if field not in CHIP_FIELDS:
        return _result([_msg(f"Unknown chip field: {field!r}", "red")])

    arg = arg.strip()

    if arg == "*":
        all_facts = _gather_all_chip_facts(connected_port)
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
    # Resolve SN / fallback specs before the literal-name lookup (see
    # chip_info -- keeps /port.chip.<field> working under an SN config).
    facts = gather_chip_facts(resolve_port(target, connected_port), connected_port)
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
    serial = cfg["serial"]
    sb = serial["stop_bits"]
    sb_str = str(int(sb)) if sb == int(sb) else str(sb)

    # Determine the actual device name and whether it differs from the
    # configured spec.  When connected, trust what the Serial object
    # actually opened.  When disconnected, best-effort resolve the spec
    # so the chip section below still finds the right device.
    spec = serial["port"] or ""
    if ser is not None:
        actual = getattr(ser, "port", spec) or spec
    else:
        try:
            actual = resolve_port(spec)
        except AmbiguousSerialNumberError:
            actual = spec  # stay honest; chip section will skip

    from termapy.plugins import format_kv_lines

    # Top section: configured serial parameters.  Port row carries
    # extra trailing context (state, "resolved from spec") on the
    # value side -- the helper just sees one big value string.
    if spec and spec != actual:
        # Parens instead of square brackets -- Rich would otherwise
        # try to interpret "[resolved from X]" as markup.
        port_value = f"{actual}  ({state})  (resolved from {spec})"
    else:
        port_value = f"{actual or '?'}  ({state})"

    top_rows: list[tuple[str, str]] = [
        ("Port", port_value),
        ("Baud rate", str(serial["baud_rate"])),
        ("Frame", f"{serial['byte_size']}{serial['parity']}{sb_str}"),
        ("Flow control", str(serial["flow_control"])),
        ("Encoding", str(cfg.get("encoding", "utf-8"))),
    ]
    xfer_root = cfg.get("file_xfer_root", "")
    if xfer_root:
        top_rows.append(("Xfer root", str(xfer_root)))

    msgs: list[Msg] = [_msg(line) for line in format_kv_lines(top_rows)]

    # USB chip section -- looked up from the OS, not from the open Serial
    # object, so it works whether or not the port is currently connected.
    # Shows the same fields as /port.chip (minus the device name, which
    # is already shown at the top of this report as "Port:").  Skipped
    # silently if the port name doesn't match any enumerable device
    # (e.g. FakeSerial / DEMO, unplugged cable, non-USB port).
    #
    # Uses the RESOLVED device name rather than the raw spec, so a
    # spec like "A1B2C3D4|COM3" still finds the chip info for the
    # device actually opened.
    port_name = actual
    connected_port = port_name if ser is not None else ""
    if port_name:
        facts = gather_chip_facts(port_name, connected_port)
        if facts is not None:
            msgs.append(_msg(""))
            chip_rows: list[tuple[str, str]] = []
            for field_name in CHIP_FIELDS:
                if field_name == "device":
                    continue  # already shown as the Port: header
                value = getattr(facts, field_name)
                if value is None:
                    continue
                label = CHIP_FIELD_LABELS[field_name]
                sval = str(value)
                if field_name == "usb_speed" and facts._usb_speed_color:
                    sval = f"[{facts._usb_speed_color}]{sval}[/]"
                elif field_name == "latency_timer" and value != "1 ms":
                    sval = f"[yellow]{sval}  (set to 1 for low latency)[/]"
                elif field_name == "permissions" and value == "denied":
                    sval = f"[red]{sval}[/]"
                chip_rows.append((label, sval))
            for line in format_kv_lines(chip_rows):
                msgs.append(_msg(line))

    if connected:
        msgs.append(_msg(""))
        try:
            hw_rows = [
                (name.upper(), str(int(getattr(ser, name))))
                for name in ("dtr", "rts", "cts", "dsr", "ri", "cd")
            ]
            for line in format_kv_lines(hw_rows):
                msgs.append(_msg(line))
        except OSError:
            pass
    return _result(msgs)


def get_set_prop(
    ser: Any | None, cfg: Mapping[str, Any], key: str, args: str
) -> Result:
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
    # Keys for the pyserial constructor live under cfg["serial"]
    # (post-v22).  Other keys (e.g. encoding) stay flat.  Read from
    # whichever location actually holds the value.
    serial = cfg.get("serial", {})
    cfg_val = serial.get(key, cfg.get(key, "?"))
    if not val:
        if not connected:
            return _result([_msg(f"{cfg_val} (disconnected)")])
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
        fc = cfg["serial"]["flow_control"]
        suffix = " (disconnected)" if ser is None else ""
        return _result([_msg(f"{fc}{suffix}")])
    if ser is None:
        return _result([_msg("Not connected.", "yellow")])
    if val not in VALID_FLOW_CONTROLS:
        return _result(
            [
                _msg(
                    f"Invalid flow control: {val} (use none/rtscts/xonxoff/manual)",
                    "red",
                )
            ]
        )
    try:
        ser.rtscts = val == "rtscts"
        ser.xonxoff = val == "xonxoff"
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
            return _result(
                [_msg("Invalid duration (use milliseconds, e.g. 250)", "red")]
            )
    try:
        ser.send_break(duration=duration)
        return _result([_msg(f"Break sent ({format_duration(duration)})")])
    except OSError as e:
        return _result([_msg(f"Break error: {e}", "red")])
