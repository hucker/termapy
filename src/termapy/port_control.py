"""Pure functions for serial port control - no Textual, no pyserial imports.

Each function accepts a serial-like object (or None), config dict, and args,
and returns a list of (text, color) message tuples plus a dict of side effects
for the caller to apply.  A function that refuses (a bad value, no open port,
a read-only signal, a pyserial error) returns exactly one red or yellow
message and no side effects; ``problem`` reads that refusal back out, so the
``/port.*`` commands answer it as a failure.

Side effects dict keys:
    update_title: bool - refresh the title bar
    sync_hw: bool - update hardware button visibility/state
    cfg_update: dict - keys to update in the in-memory config
"""

from __future__ import annotations

import functools
import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from types import ModuleType

from termapy import env_flags
from termapy.defaults import (
    VALID_BYTE_SIZES,
    VALID_FLOW_CONTROLS,
    VALID_PARITIES,
    VALID_STOP_BITS,
    cfg_value,
)
from termapy.plugins.capabilities import STATE_MESSAGES
from termapy.plugins.views import Item, TextLine
from termapy.port_allow import (
    LOOPBACK_SCHEME,
    NETWORK_SCHEMES,
    WRAPPER_SCHEMES,
    PortNotAllowedError,
    PortPolicy,
    not_allowed_message,
    url_scheme,
    wrapped_port,
)
from termapy.scripting import format_duration, parse_bool
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


# The keyword a command that sets a rate takes to ask for one off the standard list.
_CUSTOM_KEYWORD = "custom="


def take_custom(tokens: list[str]) -> tuple[list[str], bool, str | None]:
    """Split a ``custom=<bool>`` token off ``tokens``: the rest, its value and any error.

    The one reader of ``custom=`` for every command that sets a rate
    (``/port.baud_rate``, ``/port.mode``, ``/port.connect`` and the
    ``--new`` / ``/cfg.new`` settings): any ``parse_bool`` token, absent
    meaning false.

    Returns:
        ``(rest, custom, error)``; ``error`` is the message for a bad or
        repeated token, and then the other two mean nothing.
    """
    rest: list[str] = []
    custom: bool | None = None
    for token in tokens:
        if not token.lower().startswith(_CUSTOM_KEYWORD):
            rest.append(token)
            continue
        if custom is not None:
            return [], False, f"Duplicate custom: {token}"
        try:
            custom = parse_bool(token[len(_CUSTOM_KEYWORD) :], what="custom")
        except ValueError as e:
            return [], False, str(e)
    return rest, bool(custom), None


def baud_error(baud: int, custom: bool) -> str | None:
    """The refusal for a typed rate, or None (``config.check_baud``, registry ``baud-standard-unless-custom``)."""
    from termapy.config import check_baud

    reason = check_baud(baud, custom=custom, remedy="add custom=true")
    return f"Invalid baud rate: {baud} ({reason})" if reason else None


def parse_open_args(
    args: str,
) -> tuple[
    str | None,
    int | None,
    tuple[str, int, float] | None,
    str | None,
    bool | None,
    bool,
    str | None,
]:
    """Parse /port.connect arguments.

    Syntax: ``{port=<name> | <name>} {baud} {mode} {line_ending} {echo}
    {custom=<bool>}``.  A *bare* port name, if supplied, MUST be the first
    token.  The explicit ``port=<name>`` form may appear anywhere and forces
    the value to be the port -- use it for a purely-numeric serial number,
    which a bare token would otherwise read as a baud rate.  Everything else
    is order-independent; all fields optional (unspecified -> current cfg).

    Token classification:

    * ``port=<name>`` -- explicit port (device / SN / ``SN|COM`` chain / URL)
    * ``custom=<bool>`` -- the baud rate is deliberately off the standard list
    * ``cr`` / ``lf`` / ``crlf`` -- line ending
    * ``echo`` / ``noecho`` -- echo toggle
    * ``N81`` / ``8N1`` / etc. -- serial mode, either field order (parse_mode)
    * purely numeric -- baud rate, standard unless ``custom=true``
    * a bare first token matching none of the above -- port name

    Args:
        args: Raw argument string from the REPL.

    Returns:
        Tuple of ``(port_name, baud_rate, mode_tuple, line_ending,
        echo, custom, error_message)``.  ``line_ending`` is the literal
        ``"\\r"`` / ``"\\n"`` / ``"\\r\\n"`` (or None).  ``echo`` is
        True / False / None.  ``custom`` is whether ``custom=true`` was
        given.  ``error_message`` is non-None only when a token cannot be
        classified or the rate is refused (``baud_error``).
    """
    port: str | None = None
    baud: int | None = None
    mode: tuple[str, int, float] | None = None
    line_ending: str | None = None
    echo: bool | None = None
    first = True

    def _err(msg: str) -> tuple[None, None, None, None, None, bool, str]:
        return (None, None, None, None, None, False, msg)

    tokens, custom, custom_error = take_custom(args.split())
    if custom_error:
        return _err(custom_error)
    for token in tokens:
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
            f"baud / mode / cr|lf|crlf / echo|noecho / custom=true."
        )
    refusal = baud_error(baud, custom) if baud is not None else None
    if refusal:
        return _err(refusal)
    return port, baud, mode, line_ending, echo, custom, None


def baud_facts(cfg: Mapping[str, Any], rate: str | None = None) -> dict[str, Any]:
    """The rate as data, ``{"baud", "custom"}``: the one producer of those two keys.

    Args:
        cfg: Config dict; ``custom`` is its ``custom_baud``.
        rate: The rate as read, which bare ``/port.baud_rate`` takes from
            the open port; ``None`` takes the config's, as ``/port.mode``
            prints it.  Digits become a number, anything else stays text.
    """
    baud: Any = cfg["serial"]["baud_rate"] if rate is None else rate
    if isinstance(baud, str) and baud.isdigit():
        baud = int(baud)
    return {"baud": baud, "custom": bool(cfg_value(cfg, "serial.custom_baud"))}


def mode_facts(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """The frame bare ``/port.mode`` reports, with JSON types: its ``data``.

    The config's frame, as the line ``/port.mode`` prints it, with the rate
    from ``baud_facts``.  ``/port.baud_rate`` reads the open port's rate
    instead, so the two differ only when the port and the config do.
    """
    serial = cfg["serial"]
    return {
        **baud_facts(cfg),
        "byte_size": serial["byte_size"],
        "parity": serial["parity"],
        "stop_bits": serial["stop_bits"],
    }


def set_mode(ser: Any | None, cfg: Mapping[str, Any], args: str) -> Result:
    """Set serial frame parameters from a mode string and optional baud rate.

    Accepts: {baud} {mode} {custom=<bool>}, e.g. '9600 N81', 'E71', '115200',
    '9460 N81 custom=true'.  A rate off the standard list needs
    ``custom=true`` (``baud_error``), which also sets ``custom_baud``.

    Args:
        ser: Serial-like object, or None if disconnected.
        cfg: Config dict.
        args: Mode string with optional baud rate prefix.
    """
    if not args.strip():
        serial = cfg["serial"]
        sb = serial["stop_bits"]
        sb_str = str(int(sb)) if sb == int(sb) else str(sb)
        current = f"{serial['baud_rate']} {serial['byte_size']}{serial['parity']}{sb_str}"
        suffix = " (disconnected)" if ser is None else ""
        return _result([_msg(f"{current}{suffix}")])

    baud: int | None = None
    mode: tuple[str, int, float] | None = None
    tokens, custom, custom_error = take_custom(args.split())
    if custom_error:
        return _result([_msg(custom_error, "red")])
    for token in tokens:
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
        return _result([_msg(f"Invalid mode argument: {token} (use e.g. 9600 N81)", "red")])

    if baud is None and mode is None:
        return _result([_msg("Nothing to set.", "yellow")])
    refusal = baud_error(baud, custom) if baud is not None else None
    if refusal:
        return _result([_msg(refusal, "red")])

    if ser is None:
        return _not_connected()

    cfg_update: dict[str, Any] = {}
    msgs: list[Msg] = []
    try:
        if baud is not None:
            ser.baudrate = baud
            cfg_update["baud_rate"] = baud
            if custom:
                cfg_update["custom_baud"] = True
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


def _not_connected() -> Result:
    """The refusal for a port operation with no open port, in the gate's words."""
    return _result([_msg(STATE_MESSAGES["serial_connected"], "yellow")])


def problem(result: Result) -> str | None:
    """The refusal a result carries, or None when the function did its job.

    A refusal is the red or yellow message (see the module docstring); the
    ``/port.*`` setters return it as ``CmdResult.fail`` rather than printing
    it beside a success (review 2026-10-15, S1).

    Args:
        result: What a port_control function returned.

    Returns:
        The refusal's text, or None.
    """
    msgs, _ = result
    for text, color in msgs:
        if color in ("red", "yellow"):
            return text
    return None


def list_ports(*, source: PortSource | None = None) -> Result:
    """List available serial ports as a picker-style table.

    Output matches the TUI port picker and the ``--ports`` CLI flag:
    one line per port with PORT / MFG / DESCRIPTION / CHIP / SPEED /
    VID:PID / SN columns.  Width adapts to the current terminal so
    low-priority columns (speed, chip, vid_pid) drop before the row
    wraps.

    Args:
        source: Substitute port list to render instead of enumerating.
            See ``resolve_port_source``.

    Returns:
        Messages: header, separator, then one row per port.
    """
    import shutil

    from termapy.port_format import format_table

    # fast=True: the /port.list table has no in_use column, so don't run
    # the invasive probe just to discard the result.  enrich=True: it DOES
    # have location and driver columns, and filling those opens no port.
    facts_list = _gather_all_chip_facts(fast=True, enrich=True, source=source)
    if not facts_list:
        return _result([_msg("No serial ports found", "yellow")])
    row_width = shutil.get_terminal_size((80, 24)).columns
    return _result([_msg(line) for line in format_table(facts_list, row_width)])


def list_port_records(*, source: PortSource | None = None) -> list[dict]:
    """List available serial ports as stable JSON-shaped records.

    The structured twin of :func:`list_ports`: same gather (fast, enriched
    -- no port is opened), but each port becomes the fixed-schema dict of
    ``port_format.facts_to_json_record`` instead of a table row.  The
    records ``/port.list`` answers with (its table is
    ``port_format.PORT_TABLE_COLUMNS`` over them) and any other caller that
    wants machine-readable port facts; ``--ports --json`` uses the same
    record builder, so shell and agent consumers see identical shapes.

    Args:
        source: Substitute port list instead of enumerating.  See
            ``resolve_port_source``.

    Returns:
        One record per port, empty list when no ports exist.
    """
    from termapy.port_format import facts_to_json_record

    facts_list = _gather_all_chip_facts(fast=True, enrich=True, source=source, sessions=True)
    return [facts_to_json_record(facts) for facts in facts_list]


def ports_note(*, policy: PortPolicy | None = None) -> str | None:
    """What a port listing leaves out under ``TERMAPY_ALLOWED_PORTS``; None when it leaves out nothing."""
    return _policy(policy).listing_note()


def ports_title(*, policy: PortPolicy | None = None) -> str:
    """The title of a port listing: ``Ports``, and ``ports_note`` when the list is limited."""
    note = ports_note(policy=policy)
    return f"Ports ({note})" if note else "Ports"


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
    "interface_number",
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

# The fields only the in-use probe fills: finding them can open the port
# (``_check_in_use``), so a ``fast`` lookup leaves them None.
PROBED_CHIP_FIELDS: tuple[str, ...] = ("permissions", "in_use")

# The chip fields a lookup fills without opening any port.
UNPROBED_CHIP_FIELDS: tuple[str, ...] = tuple(
    field_name for field_name in CHIP_FIELDS if field_name not in PROBED_CHIP_FIELDS
)

# The serial settings a port page reports, as the config's ``serial`` keys.
PORT_SETTINGS: tuple[str, ...] = ("baud_rate", "byte_size", "parity", "stop_bits", "flow_control")

# The six modem lines, as the serial object's attributes.  Reading one
# changes nothing: DTR and RTS report what was set, the rest the line's level.
SIGNAL_LINES: tuple[str, ...] = ("dtr", "rts", "cts", "dsr", "ri", "cd")

# Field labels, in Title Case like every display label on a label/value page:
# the rows of the /port.chip and /port.info pages, and the
# title and column header of a /port.chip.<field> table, so one field reads
# the same everywhere.  Width-padded to 14 characters so values line up.
CHIP_FIELD_LABELS: dict[str, str] = {
    "device": "Device",
    "description": "Description",
    "manufacturer": "Manufacturer",
    "vendor": "Vendor",
    "product": "Product",
    "serial": "Serial #",
    "location": "Location",
    "interface_number": "Interface #",
    "interface": "Interface",
    "vid_pid": "VID:PID",
    "model": "Model",
    "usb_speed": "USB Speed",
    "negotiated": "Negotiated",
    "driver": "Driver",
    "latency_timer": "Latency Timer",
    "max_baud": "Max Baud",
    "permissions": "Permissions",
    "in_use": "In Use",
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
    # bInterfaceNumber, split out of the location string so ``location``
    # is the physical path and nothing else.  Only composite devices
    # have one -- see ``split_location_interface``.
    interface_number: str | None = None
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
    # The termapy session holding the port, from the breadcrumbs, when a
    # listing asked (``fill_sessions``): ``this session`` or ``termapy mcp,
    # PID 17140``.  None when no termapy holds it, which says nothing about
    # any other program.
    session: str | None = None
    # Silicon-vendor name resolved from the VID via ``usb_vendor``.
    # Independent of ``manufacturer`` (which is the descriptor / INF
    # string) -- they can disagree, and that disagreement is itself
    # diagnostic information.
    vendor: str | None = None
    # Color hint for the usb_speed line in the full dump (not a field).
    _usb_speed_color: str | None = None


LOOPBACK_PORT = "loop://"


def loopback_port_facts() -> ChipFacts:
    """Honest ChipFacts for the pyserial loopback (``loop://``): a name and a description.

    A loopback is not a device, so every USB field stays ``None`` and renders
    blank, never invented identity; nor is it a reserved DEMO port, which
    simulates a device that answers.
    """
    return ChipFacts(
        device=LOOPBACK_PORT,
        description="Pyserial Loopback",
    )


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
        enum_root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum")
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
                                winreg,
                                hwid_key,
                                inst,
                                device,
                                facts,
                                _DEVICE_ID_SEP.join([bus, hwid, inst]),
                            ):
                                return
    finally:
        enum_root.Close()


def _enum_subkeys(winreg_mod: ModuleType, key: object) -> list[str]:
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


# Windows device instance IDs are backslash-joined, e.g.
# FTDIBUS\VID_0403+PID_6015+D20JSV68A\0000
_DEVICE_ID_SEP = "\\"

# cfgmgr32 constants (cfgmgr32.h).  CM_DRP_* are the SPDRP_* value + 1.
_CM_LOCATE_DEVNODE_NORMAL = 0
_CM_DRP_LOCATION_PATHS = 0x24
_CR_SUCCESS = 0
# DEVPKEY_Device_BusReportedDeviceDesc (devpkey.h): the string the device
# itself reports, as the (GUID, property id) pair ``_devpropkey`` lays out.
_BUS_REPORTED_DEVICE_DESC = ("540b947e-8b40-45bc-a8a2-6a0b894cbda2", 4)
_DEVPROP_TYPE_STRING = 0x12


def _devpropkey(guid: str, pid: int) -> bytes:
    """A DEVPROPKEY as it sits in memory: the GUID in Windows' little-endian layout, then the id."""
    import uuid

    return uuid.UUID(guid).bytes_le + pid.to_bytes(4, "little")


# USB allows at most 7 tiers of hubs, so the node that knows the topology
# is always a few hops up.  Bound the walk anyway.
_MAX_DEVNODE_HOPS = 8


class _CfgMgr32(NamedTuple):
    """The cfgmgr32 calls the devnode walks use, with the ctypes types they take."""

    ctypes: Any
    DWORD: Any
    ULONG: Any
    locate: Any
    get_parent: Any
    get_prop: Any
    get_id: Any
    get_devprop: Any


@functools.lru_cache(maxsize=1)
def _cfgmgr32() -> _CfgMgr32 | None:
    """Bind the cfgmgr32 calls the devnode walks need, once.

    Lazy and cached because ``port_control`` sits on the CLI's fast path
    (``termapy --ports`` exists to be quick), so the DLL is not loaded
    unless a Windows location or product name is actually asked for.

    Returns:
        The bound calls, or None off Windows or if the bindings cannot
        be made.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes.wintypes import DWORD, PWCHAR, ULONG

        cfgmgr = ctypes.WinDLL("cfgmgr32")
        locate = cfgmgr.CM_Locate_DevNodeW
        locate.argtypes = [ctypes.POINTER(DWORD), PWCHAR, ULONG]
        locate.restype = ctypes.c_long
        get_parent = cfgmgr.CM_Get_Parent
        get_parent.argtypes = [ctypes.POINTER(DWORD), DWORD, ULONG]
        get_parent.restype = ctypes.c_long
        get_prop = cfgmgr.CM_Get_DevNode_Registry_PropertyW
        get_prop.argtypes = [
            DWORD,
            ULONG,
            ctypes.POINTER(ULONG),
            ctypes.c_void_p,
            ctypes.POINTER(ULONG),
            ULONG,
        ]
        get_prop.restype = ctypes.c_long
        get_id = cfgmgr.CM_Get_Device_IDW
        get_id.argtypes = [DWORD, PWCHAR, ULONG, ULONG]
        get_id.restype = ctypes.c_long
        get_devprop = cfgmgr.CM_Get_DevNode_PropertyW
        get_devprop.argtypes = [
            DWORD,
            ctypes.c_void_p,
            ctypes.POINTER(ULONG),
            ctypes.c_void_p,
            ctypes.POINTER(ULONG),
            ULONG,
        ]
        get_devprop.restype = ctypes.c_long
        return _CfgMgr32(ctypes, DWORD, ULONG, locate, get_parent, get_prop, get_id, get_devprop)
    except (ImportError, OSError, AttributeError):
        return None


def parse_location_paths(paths: str) -> str | None:
    """Turn a Windows LOCATION_PATHS string into a USB bus-port chain.

    This is pyserial's own rule, reproduced deliberately so that both
    ways termapy can learn a location produce the same spelling -- see
    the "calculate a location string" block in
    ``vendor/serial/tools/list_ports_windows.py``.  ``USBROOT(n)`` is the
    bus, numbered from 1; each ``USB(n)`` is one hop, joined by ``-`` at
    the first level and ``.`` below it.

    ``PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(8)#USB(3)`` becomes ``1-8.3``.

    A trailing ``USBMI(n)`` names one interface of a multi-function
    device and becomes the ``:x.n`` suffix, matching what pyserial
    appends for a composite device (the ``x`` stands in for a
    configuration value neither of us can read on Windows -- pyserial
    hardcodes it too).  Reading it here is what keeps the channels of a
    multi-port chip apart: an FT2232H's two COM ports hang off one
    shared parent USB node, so the hop chain alone is identical for
    both, and only the interface distinguishes them.  pyserial ignores
    this token because it takes the number from the hardware ID instead,
    which is a route a devnode walk doesn't have.

    ``...#USB(8)#USB(4)#USBMI(1)`` becomes ``1-8.4:x.1``.

    Args:
        paths: One LOCATION_PATHS entry.

    Returns:
        The chain, or None if the string names no USB hop at all (a
        non-USB serial port -- PCI, Bluetooth, a virtual pair).
    """
    chain: list[str] = []
    for match in re.finditer(r"USBROOT\((\w+)\)|#USB\((\w+)\)", paths):
        if match.group(1):
            chain.append(f"{int(match.group(1)) + 1:d}")
        else:
            chain.append("." if len(chain) > 1 else "-")
            chain.append(match.group(2))
    if not chain:
        return None
    interface = re.search(r"#USBMI\((\w+)\)", paths)
    if interface:
        chain.append(f":x.{interface.group(1)}")
    return "".join(chain)


# A location's optional "<config>.<interface>" tail.  Linux names the
# real configuration value (":1.1"); pyserial hardcodes "x" on Windows
# because it can't read one, and so do we.  Either way the part after
# the dot is bInterfaceNumber.
_LOCATION_INTERFACE_RE = re.compile(r"^(?P<path>[^:]+):\w+\.(?P<interface>\d+)$")


def split_location_interface(location: str | None) -> tuple[str | None, str | None]:
    """Separate a location string into physical path and interface number.

    Every backend spells a location the same way -- ``bus-port.port``,
    with an optional ``:<config>.<interface>`` tail naming one function
    of a composite device.  Only composite devices carry the tail: one
    with several functions, or a firmware USB stack (TinyUSB) that
    declares even a lone serial port that way, which then reads
    interface 0.  So the raw string changes shape from row to row: a
    plain adapter reads ``1-8.3`` while a debugger with a CDC function
    alongside it reads ``1-8.4:x.1``.

    Splitting here means the two facts are displayed in their own
    columns, so every location cell holds the same kind of value and
    ports can be compared down the column.  It runs on whatever ended up
    in the field, whichever backend produced it -- pyserial on Linux
    (``1-8.4:1.1``), pyserial on Windows and ``parse_location_paths``
    (``1-8.4:x.1``), or macOS, which never appends a tail at all.

    A string that doesn't match the pattern is returned untouched, which
    covers the registry hub/port fallback (``Hub_#0011.Port_#0003``) and
    anything a future backend invents.

    Args:
        location: Raw location, or None.

    Returns:
        ``(path, interface_number)``; the second is None when the device
        is not composite.
    """
    if not location:
        return None, None
    match = _LOCATION_INTERFACE_RE.match(location)
    if match is None:
        return location, None
    return match.group("path"), match.group("interface")


def _windows_location_paths(device_id: str, *, walk: bool = True) -> str | None:
    """Return the raw LOCATION_PATHS property for a device instance.

    With *walk* (the default) the search climbs to the nearest ancestor
    that has the property, which is what an FTDIBUS node needs since it
    has none of its own.  Enumerating the bus wants the opposite: read it
    at the node or not at all, because inheriting an ancestor's path
    would file two devices under one position.

    Args:
        device_id: Device instance ID.
        walk: Climb to an ancestor when this node has no property.

    Returns:
        The raw property string, or None.
    """
    bound = _cfgmgr32()
    if bound is None:
        return None
    ctypes, DWORD, ULONG = bound.ctypes, bound.DWORD, bound.ULONG
    locate, get_parent, get_prop = bound.locate, bound.get_parent, bound.get_prop
    devinst = DWORD()
    located = locate(
        ctypes.byref(devinst),
        ctypes.create_unicode_buffer(device_id),
        _CM_LOCATE_DEVNODE_NORMAL,
    )
    if located != _CR_SUCCESS:
        return None
    for _ in range(_MAX_DEVNODE_HOPS):
        buf = ctypes.create_unicode_buffer(1024)
        size = ULONG(ctypes.sizeof(buf))
        reg_type = ULONG(0)
        got = get_prop(
            devinst,
            _CM_DRP_LOCATION_PATHS,
            ctypes.byref(reg_type),
            ctypes.byref(buf),
            ctypes.byref(size),
            0,
        )
        if got == _CR_SUCCESS and buf.value:
            return buf.value
        if not walk:
            return None
        parent = DWORD()
        if get_parent(ctypes.byref(parent), devinst, 0) != _CR_SUCCESS:
            return None
        devinst = parent
    return None


def _windows_location_chain(device_id: str) -> str | None:
    """Return the USB bus-port chain for a Windows device instance.

    Reads LOCATION_PATHS (climbing to an ancestor if this node has none)
    and parses it with ``parse_location_paths``.  The climb is the whole
    point: pyserial reads the property off the port's *own* node, and an
    FTDIBUS node doesn't have it -- pyserial's source says so outright
    ("USB location is hidden by FDTI driver :(") and gives up.  The parent
    USB node one hop up does have it, so an FTDI port can be reported in
    the same notation as every other port instead of blank.

    LOCATION_PATHS is a synthesized PnP property rather than a stored
    registry value, which is why this goes through cfgmgr32 instead of
    winreg like its neighbors here.

    Args:
        device_id: Device instance ID, i.e. the Enum path components
            joined -- bus, hardware ID, then instance.

    Returns:
        A chain like ``1-8.3``, or None on any failure.  This is
        best-effort enrichment and never the reason a lookup fails.
    """
    paths = _windows_location_paths(device_id)
    return parse_location_paths(paths) if paths else None


def _is_usb_device_node(node_id: str, vid_pid: str) -> bool:
    """True when a Windows device instance ID names the USB device ``vid_pid`` itself.

    The device's ID is the bus ``USB``, then ``VID_04D8&PID_9036``, then
    the instance.  An interface of it adds ``&MI_01`` to the middle part,
    and a vendor bus such as FTDIBUS spells it ``VID_0403+PID_6001+...``,
    so neither is the device.  Instance IDs are case-insensitive.

    Args:
        node_id: A device instance ID.
        vid_pid: ``VID:PID`` in hex, e.g. ``"04D8:9036"``.

    Returns:
        Whether the node is that USB device.
    """
    parts = node_id.upper().split(_DEVICE_ID_SEP)
    if len(parts) < 3 or ":" not in vid_pid:
        return False
    vid, pid = vid_pid.upper().split(":", 1)
    return parts[0] == "USB" and parts[1] == f"VID_{vid}&PID_{pid}"


def _windows_usb_product(device_id: str, vid_pid: str) -> str | None:
    """Return the product name the USB device behind a Windows port reports.

    pyserial reads this string (the device's iProduct) on Linux and
    macOS, but on Windows reports only the names drivers give a port
    ("USB Serial Device").  Windows keeps the device's own string as the
    PnP property BusReportedDeviceDesc.  The port's node is often not
    the device: an interface of a composite device (an ``&MI_01`` node)
    or an FTDIBUS node.  So climb to the USB device node itself, the
    first whose instance ID is the bus ``USB`` and ``VID_xxxx&PID_yyyy``
    with no interface, as pyserial reads the device and
    not the interface elsewhere.  An interface node holds its interface
    string instead, which firmware can leave as a placeholder (the
    PICkit 5's reads ``USB_DEVICE_CDC_NAME``).

    Args:
        device_id: Device instance ID of the port's node.
        vid_pid: The port's ``VID:PID``, e.g. ``"04D8:9036"``.

    Returns:
        The device's product string, or None on any failure.  Best-effort
        enrichment, never the reason a lookup fails.
    """
    bound = _cfgmgr32()
    if bound is None:
        return None
    ctypes = bound.ctypes
    devinst = bound.DWORD()
    if (
        bound.locate(
            ctypes.byref(devinst),
            ctypes.create_unicode_buffer(device_id),
            _CM_LOCATE_DEVNODE_NORMAL,
        )
        != _CR_SUCCESS
    ):
        return None
    for _ in range(_MAX_DEVNODE_HOPS):
        node_id = ctypes.create_unicode_buffer(512)
        if bound.get_id(devinst, node_id, len(node_id), 0) != _CR_SUCCESS:
            return None
        if _is_usb_device_node(node_id.value, vid_pid):
            key = ctypes.create_string_buffer(_devpropkey(*_BUS_REPORTED_DEVICE_DESC))
            value = ctypes.create_unicode_buffer(256)
            size = bound.ULONG(ctypes.sizeof(value))
            prop_type = bound.ULONG(0)
            got = bound.get_devprop(
                devinst, key, ctypes.byref(prop_type), value, ctypes.byref(size), 0
            )
            if got != _CR_SUCCESS or prop_type.value != _DEVPROP_TYPE_STRING:
                return None
            return value.value.strip() or None
        parent = bound.DWORD()
        if bound.get_parent(ctypes.byref(parent), devinst, 0) != _CR_SUCCESS:
            return None
        devinst = parent
    return None


def _windows_match_inst(
    winreg_mod: ModuleType,
    parent_key: object,
    inst: str,
    device: str,
    facts: ChipFacts,
    device_id: str = "",
) -> bool:
    """Check one Enum instance node for a PortName match; populate
    ``facts.driver`` and (if pyserial didn't already) ``facts.location``.

    Args:
        winreg_mod: The ``winreg`` module, passed in so the import stays
            at the caller.
        parent_key: Open handle to the hardware-ID key being walked.
        inst: Instance subkey name under *parent_key*.
        device: The COM port being looked for, e.g. ``"COM4"``.
        facts: Record to populate in place.
        device_id: Full device instance ID of this node, used to locate
            the devnode for the location walk.  Empty skips that walk.

    Returns:
        True if the instance owns ``device`` (the caller stops walking).
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
        if not same_port(port_name, device):
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
            # Preferred: the same bus-port chain pyserial reports for
            # every port it can manage one for, so the LOCATION column
            # speaks one language.  Fallback: the hub/port pair from the
            # registry -- a different addressing scheme, but better than
            # blank if the devnode walk comes up empty.
            facts.location = (
                _windows_location_chain(device_id) if device_id else None
            ) or _windows_lookup_location(winreg_mod, inst_key)
        if not facts.product and device_id and facts.vid_pid:
            facts.product = _windows_usb_product(device_id, facts.vid_pid)
        return True


def _windows_lookup_location(winreg_mod: ModuleType, inst_key: object) -> str | None:
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
                            cid, _ = winreg_mod.QueryValueEx(node, "ContainerID")
                        except OSError:
                            continue
                        if cid != container_id:
                            continue
                        try:
                            loc, _ = winreg_mod.QueryValueEx(node, "LocationInformation")
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


#: The session that holds a port when it is this process: ``in_use`` and
#: ``ChipFacts.session`` both read ``this session``.
THIS_SESSION = "this session"


def port_state_style(session: str | None) -> str:
    """The style that marks a port by the termapy session holding it: green this one, yellow another.

    ``""`` when no termapy session holds it: free, or held by a program
    termapy cannot see.  The one owner of these colors: ``/port.list``,
    the port picker, ``/port.usb`` and ``termapy --ports`` read them here.
    """
    if not session:
        return ""
    return "green" if session == THIS_SESSION else "yellow"


def session_holding(
    device: str, crumbs: list[Any], own_pid: int, *, hides_host: bool | None = None
) -> str | None:
    """The termapy session holding ``device``, by its breadcrumb; None when none does.

    Only termapy sessions are seen, on every OS alike: a breadcrumb is a
    file every running termapy writes, so reading them opens no port.  This
    process's own is ``this session``, another's names it (``termapy mcp,
    PID 17140``), and under MCP without ``TERMAPY_MCP_HOST_INFO`` another is
    ``another termapy session`` (decision 2026-11-12).

    Args:
        device: The port, as the system lists it.
        crumbs: The running termapys (``breadcrumbs.read_running``).
        own_pid: This process's id.
        hides_host: Leave another session unnamed; None asks this process's policy.
    """
    for crumb in crumbs:
        if not crumb.open_port or not same_port(crumb.open_port, device):
            continue
        if crumb.pid == own_pid:
            return THIS_SESSION
        if hides_host is None:
            hides_host = env_flags.port_policy().hides_host
        return (
            "another termapy session"
            if hides_host
            else f"termapy {crumb.frontend}, PID {crumb.pid}"
        )
    return None


def fill_sessions(facts_list: list[ChipFacts]) -> None:
    """Mark each port a termapy session holds, from the breadcrumbs, opening nothing.

    A listing that colors ports by state calls this; resolution and the
    reconnect loop never do.  A port no session holds keeps ``session``
    None: termapy does not see other programs here.
    """
    from termapy import breadcrumbs

    crumbs = breadcrumbs.read_running()
    if not crumbs:
        return
    own_pid = os.getpid()
    for fact in facts_list:
        if fact.device:
            fact.session = session_holding(fact.device, crumbs, own_pid)


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
    if connected_port and same_port(device, connected_port):
        return f"yes ({THIS_SESSION})"
    if sys.platform != "win32":
        # Non-invasive: lsof never opens the port.  Names the holder.
        from termapy.serial_engine import _find_port_holder, shown_holder

        holder = _find_port_holder(device)
        return f"yes ({shown_holder(holder)})" if holder else "no"
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
    p: Any, connected_port: str = "", *, fast: bool = False, enrich: bool = False
) -> ChipFacts:
    """Build a ChipFacts from a pyserial ListPortInfo plus platform extras.

    Two independent costs live below, and they are gated separately
    because they are not the same kind of expensive:

    ``fast=True`` skips the ``in_use``/``permissions`` probe.  That probe
    *opens the port* on Windows (~250 ms each, and it pulses DTR/RTS,
    which resets auto-reset boards), so it stays off for every surface
    that does not display in-use.  Fast-gathered records have
    ``in_use=None`` and ``permissions=None`` so callers can tell the
    field is missing rather than False.

    ``enrich=True`` adds the platform metadata lookup -- driver,
    location, latency timer, negotiated speed -- from sysfs on Linux and
    the registry on Windows.  It opens nothing and costs single-digit
    milliseconds per port, so any surface that *displays* those columns
    should ask for it even in fast mode.  It defaults off because the
    hot callers (``resolve_port`` and the 2.5 s reconnect loop) read
    identity fields only.

    Enrichment also happens implicitly when ``fast=False``: a caller
    paying for the probe has already accepted a far larger cost.
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
        # Even for a VID:PID the chip table does not know.
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
    if enrich or not fast:
        # sysfs / registry reads only -- no port is opened here, which is
        # why this is NOT gated on `fast`.  Bundling it with the probe is
        # what silently emptied the LOCATION and DRIVER columns of the
        # --ports table, /port.list and the picker (they display both and
        # gather fast).  --watch and resolution stay unenriched: they read
        # identity only and run on a timer.
        _gather_linux_extras(facts, p.device)
        _gather_windows_extras(facts, p.device)
        if (
            facts.latency_timer is None
            and sys.platform == "win32"
            and facts.model
            and facts.model.startswith("FT")
        ):
            facts.latency_timer = "n/a (Windows - check Device Manager)"
    # Last, because enrichment above may have supplied the location that
    # pyserial couldn't.  Splitting whatever ended up in the field keeps
    # `location` to the physical path on every platform and every path
    # through this function.
    facts.location, facts.interface_number = split_location_interface(facts.location)
    return facts


def gather_chip_facts(
    port_name: str,
    connected_port: str = "",
    *,
    fast: bool = False,
    enrich: bool = False,
    source: PortSource | None = None,
    trust_env: bool = True,
    policy: PortPolicy | None = None,
) -> ChipFacts | None:
    """Look up the named port and return all known facts about it.

    A port the policy refuses is not found, as if it were not plugged in.

    ``fast=True`` skips the ``in_use``/``permissions`` probe (which opens
    the port on Windows -- see ``_check_in_use``).  Pass it from any
    surface that doesn't display ``in_use`` so single-port lookups stay
    non-invasive.  ``enrich=True`` still fills in driver / location /
    latency from sysfs or the registry, which opens nothing -- pass it
    from any surface that displays those.  See ``_facts_from_port_info``.

    Port discovery follows the three layers described on
    ``resolve_port_source``.  A substitute fleet (injected or from the
    environment) is authoritative: a name it doesn't list returns None
    without falling back to the reserved-port synthesis below.

    Args:
        port_name: Device name (``COM3``, ``/dev/ttyUSB0``), matched as the OS
            opens it (``same_port``).
        connected_port: The port termapy currently has open, if any.
        fast: Skip the in_use/permissions probe.
        enrich: Look up driver / location / latency anyway.
        source: Callable returning a substitute port list; wins over the
            environment and over real hardware.
        trust_env: Whether the environment may supply a fleet.
        policy: The port policy; None is this process's.

    Returns:
        ChipFacts on success, or None if no allowed port matches.
    """
    policy = _policy(policy)
    fleet = resolve_port_source(source, trust_env)
    if fleet is not None:
        for facts in fleet:
            if facts.device is not None and same_port(facts.device, port_name):
                return facts if policy.allows_device(facts.device, facts.serial) else None
        return None
    for port in system_ports(policy=policy):
        if same_port(port.device, port_name):
            return _facts_from_port_info(port, connected_port, fast=fast, enrich=enrich)
    # OS didn't enumerate it; fall back to synthesizing a record for
    # reserved virtual ports (DEMO, DEMO_FAIL) so /port.info DEMO and
    # `termapy --info DEMO` work without hardware.
    return synthetic_facts_for_reserved(port_name)


def serial_number(port_name: str, *, source: PortSource | None = None) -> str:
    """The named port's USB serial number, or "" when it reports none or is not found.

    One enumeration and nothing opened: no in-use probe (``gather_chip_facts``
    with ``fast=True``).

    Args:
        port_name: Device name, matched as the OS opens it.
        source: Callable returning a substitute port list.
    """
    facts = gather_chip_facts(port_name, fast=True, source=source)
    return (facts.serial or "") if facts is not None else ""


# ─ Where ports come from ──────────────────────────────────────────────────
# Three layers, in order: an explicitly injected source, then the
# environment, then real hardware.  Sibling hooks: cfg["serial"]["port"] =
# "DEMO" (fake open) and "DEMO_FAIL" (raise on open).
_DEMO_FLEET_ENV = "TERMAPY_DEMO_FLEET"

# A callable that returns a substitute port list.
PortSource = Callable[[], list[ChipFacts]]


def resolve_port_source(source: PortSource | None, trust_env: bool) -> list[ChipFacts] | None:
    """Return a substitute port list, or None to enumerate real hardware.

    Three layers, highest priority first:

    1. **An explicit source.** The caller hands over a callable and gets
       exactly what it returns.  Deterministic, visible in the call, and
       safe under parallel tests -- nothing global is touched.
    2. **The environment** (``TERMAPY_DEMO_FLEET``), unless *trust_env*
       is False.  This layer exists because the party who wants fake
       ports is usually NOT the calling code: a CI job, a screenshot
       run, a docs build.  Injection alone cannot fake ports underneath
       a program you don't control, which is most of what the hook is
       for.
    3. **Real hardware** -- returning None here means "go enumerate".

    ``trust_env`` is what keeps layer 2 honest: a caller that needs to
    reach real hardware while the variable happens to be set opts out
    here rather than deleting someone else's environment.

    **Surfaces above this one take *source* only, not *trust_env*.**
    ``list_ports``, ``chip_info``, ``chip_field``, ``port_info`` and the
    ``cli_flags.run_*`` handlers all forward a *source* down to here and
    leave layer 2 alone.  Deciding whether the environment may speak is
    this layer's job; the layers above only need to be able to hand a
    fleet down, and an injected one outranks the variable anyway.

    Args:
        source: Caller-supplied port list factory, or None.
        trust_env: Whether the environment may supply a fleet.

    Returns:
        A list of ChipFacts to use instead of real ports, or None when
        the caller should enumerate the machine.
    """
    if source is not None:
        return list(source())
    if trust_env and os.environ.get(_DEMO_FLEET_ENV):
        return _build_demo_fleet()
    return None


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
    connected_port: str = "",
    *,
    fast: bool = False,
    enrich: bool = False,
    source: PortSource | None = None,
    trust_env: bool = True,
    policy: PortPolicy | None = None,
    sessions: bool = False,
) -> list[ChipFacts]:
    """Return ChipFacts for every allowed port, sorted by device name.

    A port the policy refuses (``TERMAPY_ALLOWED_PORTS``) is left out
    before anything is looked up, so the in-use probe never opens it, and no
    listing, resolution or hint built on this one names it (registry
    ``port-policy-at-opener``).

    ``fast=True`` skips the per-port ``_check_in_use`` probe so the
    gather doesn't scale linearly with port count -- ``--watch`` uses
    this to keep the poll loop responsive when many ports are
    enumerated.  Records returned in fast mode have ``in_use=None``
    and ``permissions=None``.

    ``enrich=True`` fills in driver / location / latency from sysfs or
    the registry without opening anything, for surfaces that display
    those columns.  See ``_facts_from_port_info`` for why the two are
    separate knobs.

    Port discovery follows the three layers described on
    ``resolve_port_source``.  A substitute fleet is sorted the same way
    real enumeration is, so the sort contract holds on every path.

    Args:
        connected_port: The port termapy currently has open, if any.
        fast: Skip the per-port in_use/permissions probe.
        enrich: Look up driver / location / latency anyway.
        source: Callable returning a substitute port list; wins over the
            environment and over real hardware.  It is filtered as the
            machine's would be.
        trust_env: Whether the environment may supply a fleet.
        policy: The port policy; None is this process's.
        sessions: Mark the ports a termapy session holds, from the
            breadcrumbs (``fill_sessions``), for a listing that colors
            ports by state.
    """
    policy = _policy(policy)
    fleet = resolve_port_source(source, trust_env)
    if fleet is not None:
        allowed = [fact for fact in fleet if policy.allows_device(fact.device, fact.serial)]
        facts_list = sorted(allowed, key=lambda f: f.device or "")
    else:
        facts_list = [
            _facts_from_port_info(p, connected_port, fast=fast, enrich=enrich)
            for p in sorted(system_ports(policy=policy), key=lambda x: x.device)
        ]
    if sessions:
        fill_sessions(facts_list)
    return facts_list


def _policy(policy: PortPolicy | None) -> PortPolicy:
    """``policy``, or this process's when None (``env_flags.port_policy``)."""
    return env_flags.port_policy() if policy is None else policy


def system_ports(*, policy: PortPolicy | None = None) -> list[Any]:
    """The serial ports the system lists that the policy allows: the one call to ``comports()``.

    Every other module asks here (registry ``ports-enumerated-once``), so a
    port ``TERMAPY_ALLOWED_PORTS`` leaves out is left out of every picker,
    hint and listing.  Opens nothing.

    Args:
        policy: The port policy; None is this process's.

    Returns:
        pyserial ``ListPortInfo`` entries, in the system's order.
    """
    from serial.tools.list_ports import comports

    policy = _policy(policy)
    return [port for port in comports() if policy.allows_device(port.device, port.serial_number)]


#: The reserved virtual ports and their descriptions, the one list of them.
#: None enumerates; each opens directly (``config.open_serial`` builds its
#: simulator) and answers to its own serial number, ``reserved_serial``.
_RESERVED_PORTS: dict[str, str] = {
    "DEMO": "Termapy simulated device",
    "DEMO_FAIL": "Termapy simulated device (connect always fails)",
    "DEMO_JSON": "Termapy simulated NDJSON device (modern path)",
    "DEMO_VT100": "Termapy simulated VT100 device (cursor-addressed)",
}


def reserved_serial(name: str) -> str:
    """The USB serial number a reserved port answers to (``TERMAPY-DEMO``).

    A config naming it connects to the simulator the way a real config
    connects to a board by its serial number, so serial-number configs can
    be tried, and tested, with no hardware attached.
    """
    return f"TERMAPY-{name}"


def synthetic_facts_for_reserved(name: str) -> ChipFacts | None:
    """Return synthesized ChipFacts for a reserved virtual port, or None.

    The DEMO family is not enumerated by the OS but is reachable through
    termapy's runtime serial paths.  Surfacing them here lets
    ``termapy --ports DEMO --json`` produce a real record so CI pipelines
    can exercise the CLI without hardware -- the same way ``loop://`` and
    other pyserial URL handlers are reachable only when explicitly named.

    Returns None for any name that isn't a recognized reserved port,
    so callers can fall through to "no match" handling.
    """
    description = _RESERVED_PORTS.get(name)
    if description is None:
        return None
    return ChipFacts(
        device=name,
        description=description,
        manufacturer="termapy",
        serial=reserved_serial(name),
        model=name,
        usb_speed="virtual (not a USB device)",
        vid_pid="not a USB device",
        in_use="no",
        permissions="ok",
    )


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
            f"serial number {sn!r} matches {len(matches)} connected devices: {', '.join(matches)}"
        )


#: Match-reason strings returned by ``resolve_port_trace``.
MATCH_LITERAL = "literal"
MATCH_SERIAL = "serial_number"
MATCH_RESERVED = "reserved"
MATCH_URL = "url"
MATCH_AMBIGUOUS = "ambiguous"


_WIN32_DEVICE_PREFIX = "\\\\.\\"


def _port_key(name: str) -> str:
    """The identity of a port name as the OS opens it.

    Windows device names ignore case (``com3`` opens ``COM3``) and may carry
    the Win32 device prefix pyserial itself uses above COM9
    (``\\\\.\\COM3``); ``os.path.normcase`` applies the case rule.  On Linux
    and macOS names are case-sensitive, and a symlink such as
    ``/dev/serial/by-id/usb-FTDI_...`` names the device it points to, so an
    absolute name is resolved with ``os.path.realpath``.
    """
    if os.name == "nt":
        if name.startswith(_WIN32_DEVICE_PREFIX):
            name = name[len(_WIN32_DEVICE_PREFIX) :]
        return os.path.normcase(name)
    return os.path.realpath(name) if os.path.isabs(name) else name


def same_port(a: str, b: str) -> bool:
    """Whether two port names open the same port: the one comparison for port names.

    A name the user typed and the one the system enumerates compare here,
    never with ``==`` (registry ``port-name-one-compare``): ``com3`` and
    ``\\\\.\\COM3`` are ``COM3`` on Windows, a ``/dev/serial/by-id`` link is
    its ``/dev/ttyUSB0`` elsewhere.  Found 2026-10-29, when a config's
    ``com3`` connected but every lookup read the port as unplugged.
    """
    return _port_key(a) == _port_key(b)


def _match_candidate(candidate: str, facts: list[ChipFacts]) -> tuple[str, str] | None:
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
    # A device-name match wins before SN lookup so a user who types a
    # literal COM name always gets exactly that port.  The name matches as
    # the OS would open it (``same_port``), and the enumerated spelling is
    # returned, so a config's ``com3`` connects and shows as ``COM3``.
    for fact in facts:
        if fact.device is not None and same_port(fact.device, candidate):
            return (fact.device, MATCH_LITERAL)
    # Case-insensitive SN match.  Burn-ins are sometimes uppercase on
    # Windows and mixed case on other platforms, so normalize both sides.
    # Filter out any facts with a None device so the matches list is a
    # clean list[str] for the ambiguity-error constructor.
    needle = candidate.lower()
    sn_matches: list[str] = [
        fact.device
        for fact in facts
        if fact.device is not None and fact.serial is not None and fact.serial.lower() == needle
    ]
    if len(sn_matches) == 1:
        return (sn_matches[0], MATCH_SERIAL)
    if len(sn_matches) > 1:
        raise AmbiguousSerialNumberError(candidate, sn_matches)
    # A simulated port answers to its own serial number, after every real one.
    for name in _RESERVED_PORTS:
        if reserved_serial(name).lower() == needle:
            return (name, MATCH_SERIAL)
    return None


def resolve_port(
    spec: str,
    connected_port: str = "",
    *,
    source: PortSource | None = None,
    trust_env: bool = True,
) -> str:
    """Resolve a port spec string to an actual device name.

    The spec is a ``|``-separated list of candidates; each candidate is
    tried in order and the first to resolve wins.  If nothing resolves,
    the *last* candidate is returned verbatim so the downstream
    ``open_serial()`` failure message refers to the user's intended spec.

    Port discovery follows the three layers described on
    ``resolve_port_source``; *source* and *trust_env* are forwarded
    straight through.  Resolving against an injected fleet needs no
    hardware and no environment variable.

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
        source: Callable returning a substitute port list; wins over the
            environment and over real hardware.
        trust_env: Whether the environment may supply a fleet.

    Returns:
        A device name suitable for opening (e.g. ``"COM3"``,
        ``"/dev/ttyUSB0"``), a reserved name (``"DEMO"``,
        ``"DEMO_FAIL"``), or a pyserial URL.

    Raises:
        AmbiguousSerialNumberError: when a candidate SN matches two or
            more connected devices.  The caller is expected to surface
            this as a user-facing error rather than silently picking one.
    """
    facts = _gather_all_chip_facts(connected_port, fast=True, source=source, trust_env=trust_env)
    return _resolve_against(spec, facts)


def _resolve_against(spec: str, facts: list[ChipFacts]) -> str:
    """``resolve_port``'s matching, against a port list already gathered."""
    candidates = spec.split("|")
    for candidate in candidates:
        result = _match_candidate(candidate, facts)
        if result is not None:
            return result[0]
    # No candidate resolved.  Return the last one so open_serial's error
    # message names what the user most explicitly asked for.
    return candidates[-1]


def resolve_port_for_open(
    spec: str, *, source: PortSource | None = None, policy: PortPolicy | None = None
) -> str:
    """Resolve a port spec to the name to open, refusing what the port policy does not allow.

    The one gate ``config.open_serial`` passes every open through (registry
    ``port-policy-at-opener``): resolution sees only allowed ports, and
    ``check_open`` judges what it resolved to against the same list.

    A person's session is told which a refused device is, plugged in or not
    there (``_refused_device``); an MCP server is not, so an agent cannot
    map the ports the listings hide.

    Raises:
        AmbiguousSerialNumberError: As ``resolve_port``.
        PortNotAllowedError: The policy refuses the port; nothing was opened.
    """
    policy = _policy(policy)
    facts = _gather_all_chip_facts(fast=True, source=source, policy=policy)
    resolved = _resolve_against(spec, facts)
    try:
        return check_open(resolved, facts, policy=policy)
    except PortNotAllowedError:
        if policy.under_mcp or policy.allowlist is None or url_scheme(resolved) is not None:
            raise
        present = _refused_device(spec, source)
        message = not_allowed_message(resolved, policy, present=present, absent=present is None)
        raise PortNotAllowedError(message) from None


def _refused_device(spec: str, source: PortSource | None) -> str | None:
    """The plugged-in device a refused device spec names, or None when it names none.

    For a person's denial only: it looks past the policy, at every port the
    system lists, and opens nothing.
    """
    everything = _gather_all_chip_facts(fast=True, source=source, policy=PortPolicy())
    try:
        hidden = _resolve_against(spec, everything)
    except AmbiguousSerialNumberError:
        return None
    devices = {fact.device for fact in everything}
    return hidden if hidden in devices else None


# A Windows device name the policy can read as written: no path, drive, stream
# or extension syntax that Windows might map onto a different device.
_PLAIN_WINDOWS_NAME = re.compile(r"[A-Za-z0-9_-]+")


def port_identity(name: str) -> str | None:
    r"""The canonical name the port policy reads for a name the system did not list.

    Never the spelling as typed.  On Windows, ``COM5:``, ``COM2\..\COM5`` and
    ``//./COM5`` may all open COM5, so only a plain name (once the Win32
    ``\\.\`` prefix is dropped) has an identity; any other spelling has
    none and is refused.  On Linux and macOS the identity is the real path,
    with symlinks and ``..`` resolved, so a link named to fit a pattern is
    read as the device it opens.
    """
    if not name:
        return None
    if os.name == "nt":
        if name.startswith(_WIN32_DEVICE_PREFIX):
            name = name[len(_WIN32_DEVICE_PREFIX) :]
        return name if _PLAIN_WINDOWS_NAME.fullmatch(name) else None
    return os.path.realpath(name)


def check_open(resolved: str, facts: list[ChipFacts], *, policy: PortPolicy) -> str:
    """The name to open for a resolved port, or ``PortNotAllowedError``.

    With no policy in force every port opens as resolved.  Otherwise:

    - a simulator port and ``loop://`` open: they reach no device and no network;
    - a device is judged by the identities of the port it is (the name the
      system lists and its serial number), or, for a name the system did not
      list, by its canonical name (``port_identity``);
    - ``spy://`` and ``alt://`` are judged by the port they wrap;
    - a network URL must be named exactly (``PortPolicy.network_refusal``);
    - ``hwgrep://`` and every other scheme are refused: termapy cannot
      check what they open.

    Args:
        resolved: The resolved port, ``_resolve_against``'s answer.
        facts: The allowed ports the resolution was made against.
        policy: The port policy.

    Raises:
        PortNotAllowedError: The policy refuses the port; nothing was opened.
    """
    if not policy.in_force or resolved.upper() in _RESERVED_PORTS:
        return resolved
    scheme = url_scheme(resolved)
    if scheme is None:
        return _check_device(resolved, resolved, facts, policy)
    if scheme == LOOPBACK_SCHEME:
        return resolved
    if scheme in WRAPPER_SCHEMES:
        try:
            inner = wrapped_port(resolved)
        except ValueError:
            raise PortNotAllowedError(not_allowed_message(resolved, policy)) from None
        checked = _check_device(inner, resolved, facts, policy)
        if checked == inner:
            return resolved
        # Open the port that was checked: the listed spelling, or a real path.
        head, _, query = resolved.partition("?")
        return f"{head.split('://', 1)[0]}://{checked}" + (f"?{query}" if query else "")
    if scheme in NETWORK_SCHEMES:
        refusal = policy.network_refusal(resolved)
        if refusal is not None:
            raise PortNotAllowedError(refusal)
        return resolved
    raise PortNotAllowedError(not_allowed_message(resolved, policy, scheme=scheme))


def _check_device(name: str, shown: str, facts: list[ChipFacts], policy: PortPolicy) -> str:
    """The name to open for device ``name``, or ``PortNotAllowedError`` naming ``shown``.

    ``facts`` is normally the allowed list already; the port is still judged
    here, so a caller that hands in the whole list cannot open a refused one.
    """
    for fact in facts:
        if fact.device is not None and same_port(fact.device, name):
            if policy.allows_device(fact.device, fact.serial):
                return fact.device
            break
    identity = port_identity(name)
    if identity is None or not policy.allows_device(identity):
        raise PortNotAllowedError(not_allowed_message(shown, policy))
    # On Linux and macOS open the path that was checked, not a link that
    # could be pointed elsewhere; a Windows name opens as written.
    return name if os.name == "nt" else identity


def resolve_port_trace(
    spec: str,
    connected_port: str = "",
    *,
    source: PortSource | None = None,
    trust_env: bool = True,
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
    facts = _gather_all_chip_facts(connected_port, fast=True, source=source, trust_env=trust_env)
    trace: list[tuple[str, str | None]] = []
    for candidate in spec.split("|"):
        try:
            result = _match_candidate(candidate, facts)
        except AmbiguousSerialNumberError:
            trace.append((candidate, MATCH_AMBIGUOUS))
            continue
        trace.append((candidate, result[1] if result else None))
    return trace


def present_ports(*, source: PortSource | None = None) -> list[str]:
    """The device names of the ports here now, as the system spells them; opens none.

    Args:
        source: A substitute port list, as ``resolve_port`` takes it.
    """
    facts = _gather_all_chip_facts(fast=True, source=source)
    return [fact.device for fact in facts if fact.device is not None]


def port_present(spec: str, *, source: PortSource | None = None) -> bool:
    """Whether connecting to ``spec`` would find its port now: the one "is it here?" test.

    Decided the way ``resolve_port`` resolves, candidate by candidate: the
    first ``|`` alternative that matches -- a device name, a USB serial
    number, a reserved port or its serial number, a URL -- makes it
    present; a serial number two devices share before any match makes it
    absent, because connecting refuses it.  Loading a config asks this
    before choosing to show the port picker, and the config editor to
    color the port field (registry ``port-present-one-decider``).

    Args:
        spec: The config's port value, ``|``-separated candidates.
        source: A substitute port list, as ``resolve_port`` takes it.
    """
    if not spec:
        return False
    for _, reason in resolve_port_trace(spec, source=source):
        if reason == MATCH_AMBIGUOUS:
            return False
        if reason is not None:
            return True
    return False


def port_display(
    spec: str, open_device: str = "", *, source: PortSource | None = None
) -> tuple[str, ChipFacts | None]:
    """The device a config's port is, for display, and its facts; opens no port.

    The device is the one open now, else the one connecting would resolve
    ``spec`` to (a USB serial number or a fallback chain names a device),
    else ``spec`` as written.  The facts are that device's, its serial
    number among them, so a display shows ``COM9`` as the port and the
    serial number as the serial number however the config names it.
    Only the device this session holds gets an in-use line: for it that
    is known without opening anything, and any other would be probed
    (``_check_in_use``), which a display must never do (registry
    ``port-display``).

    Args:
        spec: The config's port value.
        open_device: The device this session has open, ``""`` when none
            (``SerialEngine.open_device``).
        source: A substitute port list, as ``resolve_port`` takes it.

    Returns:
        ``(device, facts)``; facts are None when the device is not
        attached or the spec names nothing.
    """
    if open_device:
        return open_device, gather_chip_facts(open_device, open_device, source=source)
    if not spec:
        return "", None
    try:
        device = resolve_port(spec, source=source)
    except AmbiguousSerialNumberError:
        return spec, None
    return device, gather_chip_facts(device, fast=True, enrich=True, source=source)


def listing_devices(
    specs: Mapping[str, str],
    *,
    active: str | None = None,
    open_device: str = "",
    source: PortSource | None = None,
) -> dict[str, str]:
    """The device each config's port spec names, for a listing's PORT column; opens no port.

    What the Cfg picker and ``/cfg.list`` show as a config's port (registry
    ``port-display``): the device the spec resolves to against the ports
    attached now, as ``port_display`` shows one config's, but enumerated
    once for the whole list rather than once per row.  The active config
    shows the device this session has open, when it has one, whatever its
    file says (``/port COM9`` changes the session, not the file).  A spec
    that names nothing attached, or two devices (an ambiguous serial
    number), shows as written: an unplugged board has no device to name.

    Args:
        specs: Each config's key (its path as a string) to its port value
            as written in the file.
        active: The active config's key, or None when none is loaded.
        open_device: The device this session has open, ``""`` when none
            (``SerialEngine.open_device``).
        source: A substitute port list, as ``resolve_port`` takes it.

    Returns:
        Each config's key to the device to show.
    """
    facts = _gather_all_chip_facts("", fast=True, source=source)

    def snapshot() -> list[ChipFacts]:
        return facts

    devices: dict[str, str] = {}
    for key, spec in specs.items():
        if key == active and open_device:
            devices[key] = open_device
        elif not spec:
            devices[key] = ""
        else:
            try:
                devices[key] = resolve_port(spec, source=snapshot)
            except AmbiguousSerialNumberError:
                devices[key] = spec
    return devices


def _chip_rows(facts: ChipFacts) -> list[tuple[str, str] | tuple[str, str, str]]:
    """The chip fields as record-page rows, each color-coded where it carries advice.

    Shared by ``/port.chip`` and ``/port.info``.  The device name is left
    out: both show it already, as a header line and as the Port row.
    """
    rows: list[tuple[str, str] | tuple[str, str, str]] = []
    for field_name in CHIP_FIELDS:
        value = getattr(facts, field_name)
        if field_name == "device" or value is None:
            continue
        label = CHIP_FIELD_LABELS[field_name]
        sval = str(value)
        if field_name == "usb_speed" and facts._usb_speed_color:
            rows.append((label, sval, facts._usb_speed_color))
        elif (
            field_name == "latency_timer"
            and isinstance(value, str)
            and value[:1].isdigit()
            and value != "1 ms"
        ):
            # Only warn when the timer is a numeric ms value above 1.
            # Strings like "n/a" or "n/a (Windows - check Device Manager)"
            # mean we don't know -- no actionable advice to give.
            rows.append((label, f"{sval}  (set to 1 for low latency)", "yellow"))
        elif field_name == "permissions" and value == "denied":
            rows.append((label, sval, "red"))
        else:
            rows.append((label, sval))
    return rows


def chip_record(facts: ChipFacts) -> dict[str, str | None]:
    """The chip facts as data, keyed by their ``/port.chip.<field>`` names."""
    return {field_name: getattr(facts, field_name) for field_name in CHIP_FIELDS}


def _chip_page(facts: ChipFacts) -> list[Item]:
    """One port's ``/port.chip`` page: its device as a heading, then the chip rows.

    Per-row coloring -- USB-speed indicator, latency-timer warning, denied
    permissions -- is the row's third element (``_chip_rows``).
    """
    items: list[Item] = [TextLine(facts.device or "?", "green"), *_chip_rows(facts)]
    # Nudge: when we see a real USB device whose VID:PID isn't in the
    # chip table, invite the user to report it so the table can grow.
    if facts.model == "unknown" and facts.vid_pid and facts.vid_pid != "not a USB device":
        items.append(
            TextLine(
                f"(chip {facts.vid_pid} not in termapy's lookup table -- "
                f"please report at https://github.com/hucker/termapy/issues "
                f"so we can add it)",
                "dim",
            )
        )
    return items


@dataclass(frozen=True)
class Report:
    """A port page: its facts as data and its prose as view items, or why there is none.

    ``/port.info`` and ``/port.chip`` return it through
    ``CmdResult.record`` (registry ``record-one-format``); the ``--info``
    flag renders the same items for stdout.

    Attributes:
        data: The record, or a list of records for every port; None with
            a problem.
        items: The page for ``views.RecordView``: rows, headings, blank lines.
        value: The scriptable scalar: the port name, or the device names
            newline-joined for every port.
        problem: The message shown instead of a page (``No port matching
            ...``), or None.
    """

    data: Any = None
    items: tuple[Item, ...] = ()
    value: str = ""
    problem: Msg | None = None


def chip_info(
    arg: str,
    current_port: str,
    connected_port: str = "",
    *,
    source: PortSource | None = None,
) -> Report:
    """Show full chip info for one port, all ports, or the current port.

    Args:
        arg: Empty string (use current_port), exact device name, or
            ``"*"`` for all connected ports.
        current_port: The port name from ``cfg["serial"]["port"]``, used when arg
            is empty.
        connected_port: The port termapy currently has open, if any.
        source: Substitute port list to report on instead of enumerating.
            See ``resolve_port_source``.

    Returns:
        One port's chip record and page, every port's (a list of records,
        pages a blank line apart) for ``*``, or the problem that stopped it.
    """
    arg = arg.strip()

    # All-ports mode
    if arg == "*":
        all_facts = _gather_all_chip_facts(connected_port, source=source)
        if not all_facts:
            return Report(problem=_msg("No serial ports found", "yellow"))
        items: list[Item] = []
        for index, facts in enumerate(all_facts):
            if index:
                items.append(None)
            items.extend(_chip_page(facts))
        devices = "\n".join(facts.device or "?" for facts in all_facts)
        return Report([chip_record(facts) for facts in all_facts], tuple(items), devices)

    # Specific port name (or current port if arg is empty)
    target = arg or current_port
    if not target:
        return Report(problem=_msg("No current port set.", "red"))
    # Resolve an SN / fallback-chain spec (e.g. "A1B2C3D4" or "SN|COM3") to
    # a real device first -- gather_chip_facts matches literal device names
    # only, so without this /port.chip fails under an SN-based config even
    # while connected (port_info already resolves; this brings /port.chip
    # into line).
    facts = gather_chip_facts(
        resolve_port(target, connected_port, source=source),
        connected_port,
        source=source,
    )
    if facts is None:
        return Report(problem=_msg(f"No port matching {target!r}", "yellow"))
    return Report(chip_record(facts), tuple(_chip_page(facts)), facts.device or "")


def chip_field(
    field: str,
    arg: str,
    current_port: str,
    connected_port: str = "",
    *,
    source: PortSource | None = None,
) -> Result:
    """Show a single field's value for one port.

    Every port at once (``*``) is a table instead: ``chip_field_records``.

    Args:
        field: Name of the ChipFacts field to query (e.g. ``"driver"``).
        arg: Empty string (use current_port) or exact device name.
        current_port: The port name from ``cfg["serial"]["port"]``, used when arg
            is empty.
        connected_port: The port termapy currently has open, if any.
        source: Substitute port list to query instead of enumerating.
            See ``resolve_port_source``.

    Returns:
        One message: the value, or why there is none.
    """
    if field not in CHIP_FIELDS:
        return _result([_msg(f"Unknown chip field: {field!r}", "red")])

    target = arg.strip() or current_port
    if not target:
        return _result([_msg("No current port set.", "red")])
    # Resolve SN / fallback specs before the literal-name lookup (see
    # chip_info -- keeps /port.chip.<field> working under an SN config).
    facts = gather_chip_facts(
        resolve_port(target, connected_port, source=source),
        connected_port,
        source=source,
    )
    if facts is None:
        return _result([_msg(f"No port matching {target!r}", "yellow")])
    value = getattr(facts, field)
    display = "n/a" if value is None else str(value)
    return _result([_msg(display)])


def chip_field_records(
    field: str,
    connected_port: str = "",
    *,
    source: PortSource | None = None,
) -> list[dict[str, str | None]]:
    """One record per port for ``/port.chip.<field> *``: ``{"device", <field>}``.

    Args:
        field: A ``CHIP_FIELDS`` name (e.g. ``"driver"``).
        connected_port: The port termapy currently has open, if any.
        source: Substitute port list to query instead of enumerating.
            See ``resolve_port_source``.

    Returns:
        The records in port order; empty when no port is present.
    """
    return [
        {"device": facts.device, field: getattr(facts, field)}
        for facts in _gather_all_chip_facts(connected_port, source=source)
    ]


def port_info(
    cfg: Mapping[str, Any],
    ser: Any | None,
    *,
    source: PortSource | None = None,
) -> Report:
    """Report comprehensive port status, frame, USB chip info, and live signals.

    The record holds the settings, ``chip`` (the chip record, or None) and
    ``signals`` (the six lines as booleans, or None when disconnected).
    The page is organized into three sections:

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
        source: Substitute port list to resolve and report against instead
            of enumerating.  See ``resolve_port_source``.
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
            actual = resolve_port(spec, source=source)
        except AmbiguousSerialNumberError:
            actual = spec  # stay honest; chip section will skip

    # Top section: configured serial parameters.  Port row carries
    # extra trailing context (state, "resolved from spec") on the
    # value side -- the helper just sees one big value string.
    if spec and spec != actual:
        port_value = f"{actual}  ({state})  (resolved from {spec})"
    else:
        port_value = f"{actual or '?'}  ({state})"

    top_rows: list[tuple[str, str]] = [
        ("Port", port_value),
        ("Baud Rate", str(serial["baud_rate"])),
        ("Frame", f"{serial['byte_size']}{serial['parity']}{sb_str}"),
        ("Flow Control", str(serial["flow_control"])),
        ("Encoding", str(cfg_value(cfg, "encoding"))),
    ]
    xfer_root = cfg_value(cfg, "file_xfer_root")
    if xfer_root:
        top_rows.append(("Xfer root", str(xfer_root)))

    data: dict[str, Any] = {
        "port": actual or None,
        "connected": connected,
        "spec": spec or None,
        **{key: serial[key] for key in PORT_SETTINGS},
        "encoding": cfg_value(cfg, "encoding"),
        "xfer_root": xfer_root or None,
        "chip": None,
        "signals": None,
    }
    items: list[Item] = list(top_rows)

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
        facts = gather_chip_facts(port_name, connected_port, source=source)
        if facts is not None:
            data["chip"] = chip_record(facts)
            items += [None, *_chip_rows(facts)]

    if connected:
        try:
            signals = {name: bool(getattr(ser, name)) for name in SIGNAL_LINES}
        except OSError:
            signals = None
        if signals is not None:
            data["signals"] = signals
            items += [None, *((name.upper(), str(int(state))) for name, state in signals.items())]
    return Report(data, tuple(items), actual or "")


def get_set_prop(ser: Any | None, cfg: Mapping[str, Any], key: str, args: str) -> Result:
    """Get or set a serial port property.

    The baud rate also takes ``custom=<bool>``: a rate off the standard
    list needs ``custom=true`` (``baud_error``), which also sets
    ``custom_baud``.

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
    custom = False
    if key == "baud_rate":
        tokens, custom, custom_error = take_custom(val.split())
        if custom_error:
            return _result([_msg(custom_error, "red")])
        val = " ".join(tokens)
    if not connected:
        return _not_connected()
    try:
        if key == "parity":
            val = val.upper()
        typed = coerce(val)
        if valid and typed not in valid:
            opts = ", ".join(sorted(str(v) for v in valid))
            return _result([_msg(f"Invalid {desc.lower()}: {val} (use {opts})", "red")])
        is_rate = key == "baud_rate" and isinstance(typed, int)
        refusal = baud_error(typed, custom) if is_rate else None
        if refusal:
            return _result([_msg(refusal, "red")])
        setattr(ser, attr, typed)
        cfg_update: dict[str, Any] = {key: typed}
        if custom:
            cfg_update["custom_baud"] = True
        return _result(
            [_msg(f"{desc} -> {typed}")],
            update_title=True,
            cfg_update=cfg_update,
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
        return _not_connected()
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
            return _not_connected()
        try:
            return _result([_msg(f"{int(getattr(ser, line))}")])
        except OSError as e:
            return _result([_msg(f"{label} read error: {e}", "red")])
    if not connected:
        return _not_connected()
    try:
        state = parse_bool(args, what=f"{label} value")
    except ValueError as e:
        return _result([_msg(str(e), "red")])
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
        return _not_connected()
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
        return _not_connected()
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
        return _result([_msg(f"Break sent ({format_duration(duration)})")])
    except OSError as e:
        return _result([_msg(f"Break error: {e}", "red")])
