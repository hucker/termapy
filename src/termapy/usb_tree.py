"""Enumerate the whole USB tree, not just the ports that carry a UART.

``port_control`` answers "what serial ports are there?".  This module
answers "what does the bus look like?" -- every hub, every device, and
every interface, arranged the way they are physically connected.  The two
views are deliberately separate: a serial listing that included cameras
and keyboards would stop being a serial listing, which is the whole point
of it.

They meet in one place.  Nodes that carry a serial port are tagged with
its name, so the tree shows *where* a COM port lives -- which hub, and
which function of which device.

Notation matches ``port_control``: ``1-8.4`` is bus 1, root-hub port 8,
then port 4 of the hub plugged in there.  An interface hangs below its
device rather than being spelled inline, because ``.`` already means "one
tier deeper in the hub tree" and reusing it for interfaces would make
``1-8.4.1`` ambiguous -- a device on a hub at ``1-8.4``, or function 1 of
the device at ``1-8.4``?  The tree draws the distinction instead of
encoding it.

Pure of Textual and pyserial.  Two backends (Windows via cfgmgr32 and the
registry, Linux via sysfs) feed one pure tree builder, so the shape of
the output is testable on any platform regardless of what is plugged in.
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from typing import Callable

# ── Records ───────────────────────────────────────────────────────────────


@dataclass
class UsbRecord:
    """One flat entry from a platform backend, before the tree is built.

    A record is either a device (``interface_number`` is None) or one
    interface of a device (it isn't).  ``path`` is the bus-port chain and
    is shared by a device and all of its interfaces.
    """

    path: str
    interface_number: str | None = None
    description: str | None = None
    vid_pid: str | None = None
    serial: str | None = None
    driver: str | None = None


@dataclass
class UsbNode:
    """One node of the assembled tree."""

    path: str
    interface_number: str | None = None
    description: str | None = None
    vid_pid: str | None = None
    serial: str | None = None
    driver: str | None = None
    # Serial port carried by this node, filled in by ``tag_serial_ports``.
    port_name: str | None = None
    children: list[UsbNode] = field(default_factory=list)

    @property
    def label(self) -> str:
        """How this node names itself in the tree.

        Devices show their path; an interface shows only ``:N``, since
        the path is on the parent line directly above it.  No ``x``
        placeholder here -- pyserial writes one inline because it has
        nowhere else to put the configuration value it cannot read, and
        a tree does not have that problem.
        """
        if self.interface_number is None:
            return self.path
        return f":{self.interface_number}"

    def walk(self):
        """Yield this node and every descendant, depth first."""
        yield self
        for child in self.children:
            yield from child.walk()


# A backend returns the flat record list; ``gather`` assembles it.
UsbSource = Callable[[], "list[UsbRecord]"]


# ── Tree assembly (pure) ──────────────────────────────────────────────────


def _parent_path(path: str) -> str | None:
    """The path one tier up, or None for a root.

    ``1-8.4`` -> ``1-8``, ``1-8`` -> ``1``, ``1`` -> None.
    """
    if "." in path:
        return path.rsplit(".", 1)[0]
    if "-" in path:
        return path.split("-", 1)[0]
    return None


def _sort_key(path: str) -> tuple:
    """Order paths the way the bus is laid out, numerically per tier.

    Plain string ordering puts ``1-10`` before ``1-2``; ports are
    numbers, so compare them as numbers.
    """
    parts = re.split(r"[-.]", path)
    return tuple(int(part) if part.isdigit() else 0 for part in parts)


def build_tree(records: list[UsbRecord]) -> list[UsbNode]:
    """Assemble flat backend records into a forest, ordered by position.

    Devices nest under the hub they are plugged into, inferred from the
    path (``1-8.4`` sits under ``1-8``).  Interfaces nest under their own
    device, which is what keeps them distinguishable from a deeper hub
    tier.

    A device whose parent hub was not reported becomes a root rather than
    being dropped -- a partial tree is still useful, and silently losing
    a device would be worse than showing it unattached.

    Args:
        records: Flat entries from a backend, in any order.

    Returns:
        Root nodes, each with children attached, sorted by bus position.
    """
    devices: dict[str, UsbNode] = {}
    interfaces: list[UsbRecord] = []
    for record in records:
        if record.interface_number is None:
            # A duplicate path means the backend reported the same device
            # twice; first wins rather than growing a second subtree.
            devices.setdefault(record.path, UsbNode(
                path=record.path,
                description=record.description,
                vid_pid=record.vid_pid,
                serial=record.serial,
                driver=record.driver,
            ))
        else:
            interfaces.append(record)

    for record in interfaces:
        parent = devices.get(record.path)
        if parent is None:
            # An interface whose device wasn't reported: synthesize the
            # device so the interface still appears somewhere sensible.
            parent = UsbNode(path=record.path)
            devices[record.path] = parent
        parent.children.append(UsbNode(
            path=record.path,
            interface_number=record.interface_number,
            description=record.description,
            vid_pid=record.vid_pid,
            serial=record.serial,
            driver=record.driver,
        ))

    roots: list[UsbNode] = []
    for path in sorted(devices, key=_sort_key):
        node = devices[path]
        parent_path = _parent_path(path)
        parent = devices.get(parent_path) if parent_path else None
        if parent is None:
            roots.append(node)
        else:
            parent.children.append(node)

    for node in devices.values():
        node.children.sort(
            key=lambda child: (
                # Interfaces first: they belong to this device, while the
                # other children are separate devices below it.
                child.interface_number is None,
                int(child.interface_number or 0),
                _sort_key(child.path),
            )
        )
    return roots


def tag_serial_ports(roots: list[UsbNode], facts_list: list) -> None:
    """Mark the nodes that carry a serial port, in place.

    Matching is exact rather than fuzzy: ``ChipFacts`` already splits a
    location into path and interface number (see
    ``port_control.split_location_interface``), which is precisely the
    pair identifying a node here.

    Args:
        roots: Tree to annotate.
        facts_list: ChipFacts for the ports currently enumerated.
    """
    by_key = {
        (facts.location, facts.interface_number): facts.device
        for facts in facts_list
        if facts.location
    }
    for root in roots:
        for node in root.walk():
            name = by_key.get((node.path, node.interface_number))
            if name is None and node.interface_number is None:
                # A single-function device carries its port on the device
                # node itself, so an unqualified match belongs here.
                name = by_key.get((node.path, None))
            if name:
                node.port_name = name


# ── Rendering (pure) ──────────────────────────────────────────────────────

# ASCII connectors, not the box-drawing FileTree uses.  This text goes
# to stdout through plain print(), and a Windows console on a legacy
# codepage raises UnicodeEncodeError on the box characters -- verified,
# not theorized.  The same strings are also what /port.usb renders.
_LAST, _MID, _GAP, _BAR = "`-- ", "+-- ", "    ", "|   "


def _describe(node: UsbNode) -> str:
    """The right-hand side of a tree line: what this thing is."""
    bits = [node.description or "(unknown device)"]
    if node.vid_pid:
        bits.append(node.vid_pid)
    if node.driver:
        bits.append(node.driver)
    if node.serial:
        bits.append(f"SN {node.serial}")
    return "  ".join(bits)


def render_tree(roots: list[UsbNode], label_width: int = 0) -> list[str]:
    """Render the forest as aligned text lines.

    Args:
        roots: Tree to draw.
        label_width: Column budget for the connector+label side.  Zero
            measures the tree and fits it.

    Returns:
        Lines without trailing newlines.
    """
    if not roots:
        return ["(no USB devices found)"]
    if not label_width:
        label_width = max(
            len(prefix) + len(node.label)
            for prefix, node in _flatten(roots)
        )
    lines = []
    for prefix, node in _flatten(roots):
        left = f"{prefix}{node.label}".ljust(label_width)
        port = f"  -> {node.port_name}" if node.port_name else ""
        lines.append(f"{left}  {_describe(node)}{port}".rstrip())
    return lines


def _flatten(roots: list[UsbNode], prefix: str = "") -> list[tuple[str, UsbNode]]:
    """Pair every node with the connector prefix that draws it."""
    out: list[tuple[str, UsbNode]] = []
    for i, node in enumerate(roots):
        last = i == len(roots) - 1
        if prefix == "" and not node.path.count("-"):
            # Root hubs sit flush left; everything else hangs off one.
            out.append(("", node))
            out.extend(_flatten(node.children, ""))
            continue
        out.append((prefix + (_LAST if last else _MID), node))
        out.extend(_flatten(node.children, prefix + (_GAP if last else _BAR)))
    return out


def to_json_records(roots: list[UsbNode]) -> list[dict]:
    """Convert the tree to nested dicts for ``--json``.

    Shape is fixed -- every node carries every key, ``null`` where a
    value is unknown -- so consumers can rely on it.
    """
    return [
        {
            "path": node.path,
            "interface_number": node.interface_number,
            "description": node.description,
            "vid_pid": node.vid_pid,
            "serial_number": node.serial,
            "driver": node.driver,
            "port": node.port_name,
            "children": to_json_records(node.children),
        }
        for node in roots
    ]


# ── Backends ──────────────────────────────────────────────────────────────


def _clean_desc(raw: str | None) -> str | None:
    """Strip Windows' INF indirection from a DeviceDesc value.

    ``@usbser.inf,%usbserial.devicedesc%;USB Serial Device`` is stored
    with its localizable key in front of the resolved text; only the text
    after the last ``;`` is meant for a human.

    The result is folded to ASCII.  Device names carry trademark and
    accented characters (``MPLAB(R) PICkit(TM)5``), this text is printed
    with a plain ``print()``, and a Windows console on a legacy codepage
    raises UnicodeEncodeError on them -- a port listing must not die
    because a webcam has a trademark sign in its name.
    """
    if not raw:
        return None
    return _ascii_fold(raw.rsplit(";", 1)[-1].strip()) or None


def _ascii_fold(text: str) -> str:
    """Best-effort ASCII rendering of an OS-supplied string.

    Decomposes accents and drops what will not encode, so a name stays
    readable rather than becoming mojibake or an exception.
    """
    # Symbols go first.  NFKD *expands* them -- it turns a trademark
    # sign into the letters "TM", so "PICkit(TM)5" would come out
    # "PICkitTM5".  Dropping them outright reads the way the name is
    # meant to be said.
    without_symbols = "".join(
        char for char in text if unicodedata.category(char) != "So"
    )
    decomposed = unicodedata.normalize("NFKD", without_symbols)
    plain = decomposed.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s{2,}", " ", plain).strip()


def _windows_records() -> list[UsbRecord]:
    """Enumerate the USB tree from the Windows registry + cfgmgr32.

    Topology comes from each devnode's LOCATION_PATHS, the same property
    ``port_control.parse_location_paths`` reads, so the paths here and the
    ones in the LOCATION column are the same strings.
    """
    import winreg

    from termapy.port_control import (
        _DEVICE_ID_SEP,
        _cfgmgr32,
        _windows_location_paths,
        parse_location_paths,
    )

    if _cfgmgr32() is None:
        return []
    records: list[UsbRecord] = []
    seen: set[tuple[str, str | None]] = set()
    try:
        root = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            _DEVICE_ID_SEP.join(
                ["SYSTEM", "CurrentControlSet", "Enum", "USB"]
            ),
        )
    except OSError:
        return []
    with root:
        for hwid in _subkeys(winreg, root):
            try:
                hwid_key = winreg.OpenKey(root, hwid)
            except OSError:
                continue
            with hwid_key:
                for inst in _subkeys(winreg, hwid_key):
                    device_id = _DEVICE_ID_SEP.join(["USB", hwid, inst])
                    paths = _windows_location_paths(device_id, walk=False)
                    if not paths:
                        continue
                    path = parse_location_paths(paths)
                    if not path:
                        continue
                    interface = re.search(r"#USBMI\((\w+)\)", paths)
                    number = interface.group(1) if interface else None
                    # parse_location_paths appends the interface tail; the
                    # tree keeps the two apart, so take the path alone.
                    path = path.split(":", 1)[0]
                    if (path, number) in seen:
                        continue
                    seen.add((path, number))
                    try:
                        node = winreg.OpenKey(hwid_key, inst)
                    except OSError:
                        continue
                    with node:
                        records.append(UsbRecord(
                            path=path,
                            interface_number=number,
                            description=_clean_desc(
                                _reg_value(winreg, node, "DeviceDesc")
                            ),
                            vid_pid=_vid_pid_from_hwid(hwid),
                            serial=inst if _looks_like_serial(inst) else None,
                            driver=_reg_value(winreg, node, "Service"),
                        ))
    return records


def _subkeys(winreg_mod, key) -> list[str]:
    """Every immediate subkey name of ``key``."""
    names: list[str] = []
    i = 0
    while True:
        try:
            names.append(winreg_mod.EnumKey(key, i))
        except OSError:
            return names
        i += 1


def _reg_value(winreg_mod, key, name: str) -> str | None:
    """One registry value, or None if it isn't there."""
    try:
        value, _ = winreg_mod.QueryValueEx(key, name)
    except OSError:
        return None
    return str(value) if value else None


def _vid_pid_from_hwid(hwid: str) -> str | None:
    """``VID_0403&PID_6001&MI_00`` -> ``0403:6001``."""
    match = re.search(r"VID_([0-9A-F]{4})&PID_([0-9A-F]{4})", hwid, re.I)
    return f"{match.group(1).upper()}:{match.group(2).upper()}" if match else None


def _looks_like_serial(inst: str) -> bool:
    """True for a real serial number, False for a Windows ephemeral ID.

    Windows synthesizes an instance id like ``6&1c949859&0&0001`` for a
    device with no serial number; the ``&`` is what gives it away.
    """
    return "&" not in inst


def _linux_records() -> list[UsbRecord]:
    """Enumerate the USB tree from sysfs.

    Linux does most of the work already: the directory names under
    ``/sys/bus/usb/devices`` *are* the topology.  ``1-8.4`` is a device,
    ``1-8.4:1.1`` is one of its interfaces, and ``usb1`` is a root hub.
    """
    root = "/sys/bus/usb/devices"
    if not os.path.isdir(root):
        return []
    records: list[UsbRecord] = []
    for name in os.listdir(root):
        node = os.path.join(root, name)
        if name.startswith("usb"):
            # Root hub: sysfs calls it usb1, the topology calls it 1.
            path, number = name[3:], None
        elif ":" in name:
            device, _, tail = name.partition(":")
            path, number = device, tail.split(".", 1)[-1]
        else:
            path, number = name, None
        if not path or not re.match(r"^\d+(-[\d.]+)?$", path):
            continue
        if number is None:
            records.append(UsbRecord(
                path=path,
                description=_read(node, "product") or _read(node, "manufacturer"),
                vid_pid=_linux_vid_pid(node),
                serial=_read(node, "serial"),
                driver=_link_name(os.path.join(node, "driver")),
            ))
        else:
            records.append(UsbRecord(
                path=path,
                interface_number=number,
                description=_read(node, "interface"),
                driver=_link_name(os.path.join(node, "driver")),
            ))
    return records


def _read(node: str, name: str) -> str | None:
    """One sysfs attribute, stripped, or None."""
    try:
        with open(os.path.join(node, name), encoding="utf-8") as handle:
            return handle.read().strip() or None
    except OSError:
        return None


def _link_name(path: str) -> str | None:
    """Basename of a sysfs symlink target (how a driver names itself)."""
    try:
        return os.path.basename(os.readlink(path)) or None
    except OSError:
        return None


def _linux_vid_pid(node: str) -> str | None:
    """``idVendor``/``idProduct`` as ``0403:6001``."""
    vid, pid = _read(node, "idVendor"), _read(node, "idProduct")
    return f"{vid.upper()}:{pid.upper()}" if vid and pid else None


class UnsupportedPlatform(Exception):
    """Raised when no backend exists for the current platform."""


def gather_usb_tree(
    source: UsbSource | None = None, facts_list: list | None = None
) -> list[UsbNode]:
    """Return the USB tree for this machine, ports tagged.

    Args:
        source: Callable returning ``UsbRecord``s, used instead of
            enumerating.  Same injection contract as
            ``port_control.resolve_port_source`` -- it exists so callers
            can drive the tree against a known bus.
        facts_list: ChipFacts used to tag nodes that carry a serial port.
            None gathers them.

    Returns:
        Root nodes.

    Raises:
        UnsupportedPlatform: No backend for this platform.
    """
    if source is not None:
        records = list(source())
    elif sys.platform == "win32":
        records = _windows_records()
    elif sys.platform.startswith("linux"):
        records = _linux_records()
    else:
        raise UnsupportedPlatform(
            f"USB tree is not available on {sys.platform} "
            "(Windows and Linux only)"
        )
    roots = build_tree(records)
    if facts_list is None:
        from termapy.port_control import _gather_all_chip_facts

        facts_list = _gather_all_chip_facts(fast=True, enrich=True)
    tag_serial_ports(roots, facts_list)
    return roots
