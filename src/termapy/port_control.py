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

import re

from termapy import usb_serial_chips
from termapy.defaults import (
    VALID_BYTE_SIZES,
    VALID_FLOW_CONTROLS,
    VALID_PARITIES,
    VALID_STOP_BITS,
)

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
_MODE_RE = re.compile(r"^([NEOMS])([5-8])(1\.5|[12])$", re.IGNORECASE)


def parse_mode(mode: str) -> tuple[str, int, float] | None:
    """Parse a serial mode string like 'N81' into (parity, byte_size, stop_bits).

    Args:
        mode: Mode string, e.g. 'N81', 'E71', 'O81.5'.

    Returns:
        Tuple of (parity, byte_size, stop_bits), or None if invalid.
    """
    m = _MODE_RE.match(mode.strip())
    if not m:
        return None
    parity = m.group(1).upper()
    byte_size = int(m.group(2))
    stop_bits = float(m.group(3))
    return parity, byte_size, stop_bits


#: Line-ending tokens accepted by /port.open.  Values are the literal
#: strings stored in cfg["line_ending"].
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
    """Parse /port.open arguments.

    Syntax: ``{name} {baud} {mode} {line_ending} {echo}``.  The port
    name, if supplied, MUST be the first token -- everything else is
    order-independent.  All fields are optional; unspecified values
    fall back to the current cfg values at the call site.

    Token classification (after the first token):

    * ``cr`` / ``lf`` / ``crlf`` -- line ending
    * ``echo`` / ``noecho`` -- echo_input toggle
    * ``N81`` / ``E71`` / etc. -- serial mode (parse_mode)
    * purely numeric -- baud rate
    * anything else is an error (would previously have been interpreted
      as a second port name)

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
        # Port name is always first, or not supplied at all.  We decide
        # on the first token: if it classifies as one of the later
        # fields, there's no port; otherwise it's the port name.
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
        sb = cfg.get("stop_bits", 1)
        sb_str = str(int(sb)) if sb == int(sb) else str(sb)
        current = (
            f"{cfg.get('baud_rate', '?')} "
            f"{cfg.get('byte_size', 8)}{cfg.get('parity', 'N')}{sb_str}"
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
        sb = cfg_update.get("stop_bits", cfg.get("stop_bits", 1))
        sb_str = str(int(sb)) if sb == int(sb) else str(sb)
        summary = (
            f"{cfg_update.get('baud_rate', cfg.get('baud_rate', '?'))} "
            f"{cfg_update.get('byte_size', cfg.get('byte_size', 8))}"
            f"{cfg_update.get('parity', cfg.get('parity', 'N'))}"
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
    "device": "Device",
    "description": "Description",
    "manufacturer": "Manufacturer",
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


def _check_in_use(device: str, connected_port: str = "") -> str:
    """Return 'yes', 'yes (this session)', or 'no'.

    If *device* matches *connected_port*, the port is known to be open
    by this termapy session - return immediately without probing.  This
    avoids the Windows issue where a process can re-open its own COM
    port, which would falsely report "no".

    For other ports, try to open briefly.  If the open succeeds, nothing
    else has the port.  If it fails, something else has it open.
    """
    if connected_port and device == connected_port:
        return "yes (this session)"
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


def _facts_from_port_info(p: Any, connected_port: str = "") -> ChipFacts:
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
        chip = usb_serial_chips.chip(p.vid, p.pid)
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
    facts.permissions = _check_permissions(p.device)
    facts.in_use = _check_in_use(p.device, connected_port)
    _gather_linux_extras(facts, p.device)
    if (
        facts.latency_timer is None
        and sys.platform == "win32"
        and facts.model
        and facts.model.startswith("FT")
    ):
        facts.latency_timer = "n/a (Windows - check Device Manager)"
    return facts


def gather_chip_facts(port_name: str, connected_port: str = "") -> ChipFacts | None:
    """Look up the named port and return all known facts about it.

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

    for p in comports():
        if p.device == port_name:
            return _facts_from_port_info(p, connected_port)
    return None


# ─ Demo fleet ─────────────────────────────────────────────────────────────
# When TERMAPY_DEMO_FLEET is set, _gather_all_chip_facts() returns these
# synthetic ports instead of calling comports().  Useful for screenshots,
# docs, hardware-free demos, and cross-platform tests.  Sibling hooks:
# cfg["port"] = "DEMO" (fake open) and "DEMO_FAIL" (raise on open).
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


def _gather_all_chip_facts(connected_port: str = "") -> list[ChipFacts]:
    """Return ChipFacts for every connected port, sorted by device name.

    Honors the ``TERMAPY_DEMO_FLEET`` env var: when set to any non-empty
    value, returns a fixed synthetic fleet instead of enumerating real
    ports.  See ``_build_demo_fleet`` for the roster.
    """
    if os.environ.get(_DEMO_FLEET_ENV):
        return _build_demo_fleet()
    from serial.tools.list_ports import comports

    return [
        _facts_from_port_info(p, connected_port)
        for p in sorted(comports(), key=lambda x: x.device)
    ]


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
_RESERVED_PORTS = frozenset({"DEMO", "DEMO_FAIL"})

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
    for f in facts:
        if f.device == candidate and f.device is not None:
            return (f.device, MATCH_LITERAL)
    # Case-insensitive SN match.  Burn-ins are sometimes uppercase on
    # Windows and mixed case on other platforms, so normalize both sides.
    # Filter out any facts with a None device so the matches list is a
    # clean list[str] for the ambiguity-error constructor.
    needle = candidate.lower()
    sn_matches: list[str] = [
        f.device for f in facts
        if f.device is not None
        and f.serial is not None
        and f.serial.lower() == needle
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

    Args:
        spec: The raw ``cfg["port"]`` value, post-env-expansion.
        connected_port: The currently-connected port (used for the
            ``in_use`` annotation inside ``ChipFacts``; not consulted by
            resolution itself).

    Returns:
        A device name suitable for opening (e.g. ``"COM3"``,
        ``"/dev/ttyUSB0"``), a reserved name (``"DEMO"``,
        ``"DEMO_FAIL"``), or a pyserial URL.

    Raises:
        AmbiguousSerialNumberError: when a candidate SN matches two or
            more connected devices.  The caller is expected to surface
            this as a user-facing error rather than silently picking one.
    """
    facts = _gather_all_chip_facts(connected_port)
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
    facts = _gather_all_chip_facts(connected_port)
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
        current_port: The port name from ``cfg["port"]``, used when arg
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
    facts = gather_chip_facts(target, connected_port)
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
    facts = gather_chip_facts(target, connected_port)
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

    # Determine the actual device name and whether it differs from the
    # configured spec.  When connected, trust what the Serial object
    # actually opened.  When disconnected, best-effort resolve the spec
    # so the chip section below still finds the right device.
    spec = cfg.get("port", "") or ""
    if ser is not None:
        actual = getattr(ser, "port", spec) or spec
    else:
        try:
            actual = resolve_port(spec)
        except AmbiguousSerialNumberError:
            actual = spec  # stay honest; chip section will skip

    if spec and spec != actual:
        # Use parens instead of square brackets -- Rich treats square
        # brackets as markup and would silently eat "[resolved from X]".
        port_line = f"  Port:         {actual}  ({state})  (resolved from {spec})"
    else:
        port_line = f"  Port:         {actual or '?'}  ({state})"

    msgs: list[Msg] = [
        _msg(port_line),
        _msg(f"  Baud rate:    {cfg.get('baud_rate', '?')}"),
        _msg(
            f"  Frame:        {cfg.get('byte_size', 8)}"
            f"{cfg.get('parity', 'N')}{sb_str}"
        ),
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
                    msgs.append(_msg(line + "  (set to 1 for low latency)", "yellow"))
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
        return _result([_msg(f"Break sent ({int(duration * 1000)}ms)")])
    except OSError as e:
        return _result([_msg(f"Break error: {e}", "red")])
