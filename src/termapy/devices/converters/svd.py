"""CMSIS-SVD converter -- ARM's System View Description into a device document.

SVD is the one register-description format with an ecosystem: every ARM
vendor ships one per part, RISC-V vendors adopted it for the tooling, and
the cmsis-svd-data repository collects thousands.  It is XML, 1-5 MB per
part, with inheritance (``derivedFrom``) and arrays (``dim``) that must be
resolved -- which is exactly why termapy reads its own flat file and not
SVD directly.  This module does the resolving, once, at import.

What is handled (the shapes a real Atmel / ST / NXP file uses):

- ``derivedFrom`` on a peripheral (SERCOM1 from SERCOM0: same registers,
  new base) and on a register within a peripheral (fields/size/access
  copied when absent).  Chains resolve; a cycle stops.
- ``dim`` / ``dimIncrement`` / ``dimIndex`` on peripherals, clusters,
  registers and fields: ``%s`` in the name becomes each index label
  (``0,1,2`` / ``A,B,C`` / ``0-2``; default ``0..n-1``); ``[%s]`` loses
  its brackets.
- Nested clusters: the register name is ``PERIPHERAL_CLUSTER_REGISTER``
  and the address is the sum of the offsets.
- ``size`` / ``access`` / ``resetValue`` inherited device -> peripheral ->
  cluster -> register.
- ``access`` -> ``rw`` / ``ro`` / ``wo``; ``readAction`` present ->
  ``read_effect``; ``modifiedWriteValues`` other than ``modify`` ->
  ``rmw: false`` (a write-1-to-clear register must never be mask-written).
- Fields by ``bitOffset``+``bitWidth``, ``bitRange`` (``[msb:lsb]``) or
  ``lsb``/``msb``; ``enumeratedValues`` -> ``values`` (``0x``, decimal
  and ``#`` binary; wildcard and default-only entries are skipped).

Names are sanitized to identifiers (anything else becomes ``_``); a name
that still repeats gets a numeric suffix rather than failing a
7000-register file over one alias.  Not handled: ``derivedFrom`` on
clusters and fields, ``dimArrayIndex``, non-byte ``addressUnitBits``.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Final

FORMAT: Final[str] = "svd"
DESCRIPTION: Final[str] = "CMSIS-SVD (ARM System View Description) device file"
# The schema reference every SVD carries, plus the one element that makes
# it a device description rather than any other CMSIS XML.
DETECT: Final[tuple[str, ...]] = ("CMSIS-SVD", "<peripherals>")

_ACCESS: Final[dict[str, str]] = {
    "read-only": "ro",
    "write-only": "wo",
    "writeOnce": "wo",
    "read-write": "rw",
    "read-writeOnce": "rw",
}

_NOT_IDENT: Final = re.compile(r"[^A-Za-z0-9_]")
_BIT_RANGE: Final = re.compile(r"^\[(\d+):(\d+)\]$")
_WS: Final = re.compile(r"\s+")


def convert(text: str) -> dict[str, Any]:
    """SVD text -> a device document; ``{}`` when the text is not an SVD.

    Args:
        text: The whole SVD file.

    Returns:
        The document ``termapy.devices.parse_device`` accepts, or an empty
        dict for junk, a parse error, or a file with no peripherals.
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return {}
    if _local(root.tag) != "device":
        return {}
    peripherals_el = root.find("peripherals")
    if peripherals_el is None:
        return {}
    name = _text(root, "name")
    defaults = _defaults({"size": 32, "access": "rw", "reset": None}, root)
    by_name = {_text(el, "name"): el for el in peripherals_el.findall("peripheral")}
    builder = _Builder()
    for el in peripherals_el.findall("peripheral"):
        builder.peripheral(el, by_name, defaults)
    doc: dict[str, Any] = {
        "device_version": 1,
        "device": _device_name(name),
        "description": _clean(_text(root, "description")),
        "vendor": _text(root, "vendor") or _text(root, "vendorID"),
        "source": f"CMSIS-SVD {root.get('schemaVersion', '?')}: {name} v{_text(root, 'version')}".rstrip(" v"),
        "parts": [name] if name else [],
        "license": _license(text, _text(root, "licenseText")),
        "registers": builder.registers,
    }
    return doc


# ── Element helpers ──────────────────────────────────────────────────────────


def _local(tag: str) -> str:
    """Tag without a namespace prefix (some vendors namespace the root)."""
    return tag.rsplit("}", 1)[-1]


def _text(el: ET.Element | None, tag: str) -> str:
    if el is None:
        return ""
    child = el.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _int(text: str) -> int | None:
    """An SVD integer: decimal, ``0x`` hex, or ``#`` binary (``x`` = wildcard)."""
    value = text.strip()
    if not value:
        return None
    if value.startswith("#"):
        bits = value[1:].lower().replace("0b", "")
        if "x" in bits:
            return None
        try:
            return int(bits, 2)
        except ValueError:
            return None
    try:
        return int(value, 0)
    except ValueError:
        return None


def _clean(text: str) -> str:
    """Collapse the multi-line, multi-space descriptions vendors write."""
    return _WS.sub(" ", text).strip()


def _first_line(text: str) -> str:
    """The first non-empty line of a license blob (SVD spells newlines ``\\n``)."""
    for line in text.replace("\\n", "\n").splitlines():
        if line.strip():
            return _clean(line)
    return ""


_SPDX: Final = re.compile(r"SPDX-License-Identifier:\s*([A-Za-z0-9.+-]+)")


def _license(text: str, license_text: str) -> str:
    """The SPDX tag from the header comment, else ``licenseText``'s first line.

    Microchip's files carry ``SPDX-License-Identifier: Apache-2.0`` in an
    XML comment above ``<device>`` and no ``licenseText`` at all; the
    parser drops comments, so the raw head is searched.  The tag is what
    a shared or bundled file needs recorded.
    """
    match = _SPDX.search(text[:4000])
    if match:
        return match.group(1)
    return _first_line(license_text)


def _ident(name: str) -> str:
    out = _NOT_IDENT.sub("_", name) or "_"
    return out if not out[0].isdigit() else f"_{out}"


def _device_name(name: str) -> str:
    """``ATSAME54P20A`` -> ``atsame54p20a``, shaped for the ``device`` field."""
    return (_ident(name).lower().strip("_") or "device")


def _defaults(inherited: dict[str, Any], el: ET.Element) -> dict[str, Any]:
    """Inherit ``size`` / ``access`` / ``resetValue`` one level down."""
    out = dict(inherited)
    size = _int(_text(el, "size"))
    if size:
        out["size"] = size
    access = _text(el, "access")
    if access in _ACCESS:
        out["access"] = _ACCESS[access]
    reset = _int(_text(el, "resetValue"))
    if reset is not None:
        out["reset"] = reset
    return out


def _dims(el: ET.Element) -> list[tuple[str, int]]:
    """``[(label, offset)]`` for a dim'd element; one ``("", 0)`` otherwise."""
    count = _int(_text(el, "dim"))
    if not count:
        return [("", 0)]
    increment = _int(_text(el, "dimIncrement")) or 0
    labels = _dim_labels(_text(el, "dimIndex"), count)
    return [(labels[i], i * increment) for i in range(count)]


def _dim_labels(spec: str, count: int) -> list[str]:
    """``0,1,2`` / ``A,B,C`` / ``0-2`` -> labels; default ``0..count-1``."""
    if "," in spec:
        labels = [part.strip() for part in spec.split(",")]
    elif "-" in spec:
        lo, _, hi = spec.partition("-")
        try:
            labels = [str(i) for i in range(int(lo), int(hi) + 1)]
        except ValueError:
            labels = []
    else:
        labels = []
    if len(labels) != count:
        labels = [str(i) for i in range(count)]
    return labels


def _expand(name: str, label: str) -> str:
    """Substitute a dim label into ``%s`` (or append it) and drop brackets."""
    expanded = name.replace("%s", label) if "%s" in name else (name + label if label else name)
    return expanded.replace("[", "").replace("]", "")


def _resolve(el: ET.Element, by_name: dict[str, ET.Element]) -> list[ET.Element]:
    """``[el, base, base's base, ...]`` following ``derivedFrom``; cycles stop."""
    chain = [el]
    seen = {id(el)}
    current = el
    while True:
        base_name = current.get("derivedFrom", "")
        base = by_name.get(base_name.rsplit(".", 1)[-1]) if base_name else None
        if base is None or id(base) in seen:
            return chain
        chain.append(base)
        seen.add(id(base))
        current = base


def _first(chain: list[ET.Element], tag: str) -> str:
    """The first element in the chain that has ``tag``, as text."""
    for el in chain:
        value = _text(el, tag)
        if value:
            return value
    return ""


def _first_child(chain: list[ET.Element], tag: str) -> ET.Element | None:
    for el in chain:
        child = el.find(tag)
        if child is not None:
            return child
    return None


# ── The walk ─────────────────────────────────────────────────────────────────


class _Builder:
    """Accumulates flattened register rows, keeping names unique."""

    def __init__(self) -> None:
        self.registers: list[dict[str, Any]] = []
        self._names: set[str] = set()

    def peripheral(
        self, el: ET.Element, by_name: dict[str, ET.Element], defaults: dict[str, Any],
    ) -> None:
        chain = _resolve(el, by_name)
        name = _text(el, "name")
        group = _first(chain, "groupName")
        base = _int(_first(chain, "baseAddress")) or 0
        registers_el = _first_child(chain, "registers")
        if registers_el is None:
            return
        per_defaults = defaults
        for base_el in reversed(chain):
            per_defaults = _defaults(per_defaults, base_el)
        reg_by_name = {
            _text(reg, "name"): reg for reg in registers_el.iter("register")
        }
        for label, offset in _dims(el):
            per_name = _ident(_expand(name, label))
            self._registers(
                registers_el, per_name, base + offset, per_defaults,
                peripheral=per_name, group=group, reg_by_name=reg_by_name,
            )

    def _registers(
        self,
        parent: ET.Element,
        prefix: str,
        base: int,
        defaults: dict[str, Any],
        *,
        peripheral: str,
        group: str,
        reg_by_name: dict[str, ET.Element],
    ) -> None:
        for el in parent:
            tag = _local(el.tag)
            if tag == "cluster":
                cluster_defaults = _defaults(defaults, el)
                offset = _int(_text(el, "addressOffset")) or 0
                for label, dim_offset in _dims(el):
                    cluster_name = _ident(_expand(_text(el, "name"), label))
                    self._registers(
                        el, f"{prefix}_{cluster_name}", base + offset + dim_offset,
                        cluster_defaults, peripheral=peripheral, group=group,
                        reg_by_name=reg_by_name,
                    )
            elif tag == "register":
                self._register(
                    el, prefix, base, defaults,
                    peripheral=peripheral, group=group, reg_by_name=reg_by_name,
                )

    def _register(
        self,
        el: ET.Element,
        prefix: str,
        base: int,
        defaults: dict[str, Any],
        *,
        peripheral: str,
        group: str,
        reg_by_name: dict[str, ET.Element],
    ) -> None:
        chain = _resolve(el, reg_by_name)
        offset = _int(_text(el, "addressOffset"))
        if offset is None:
            return
        reg_defaults = defaults
        for base_el in reversed(chain):
            reg_defaults = _defaults(reg_defaults, base_el)
        size_bits = reg_defaults["size"]
        size = max(1, (size_bits + 7) // 8)
        description = _clean(_first(chain, "description"))
        read_effect = bool(_first(chain, "readAction"))
        modified = _first(chain, "modifiedWriteValues")
        rmw = modified in ("", "modify")
        fields_el = _first_child(chain, "fields")
        fields = _fields(fields_el, size) if fields_el is not None else []
        for label, dim_offset in _dims(el):
            name = self._unique(f"{prefix}_{_ident(_expand(_text(el, 'name'), label))}")
            row: dict[str, Any] = {
                "name": name,
                "addr": f"0x{base + offset + dim_offset:08X}",
                "size": size,
                "peripheral": peripheral,
            }
            if group:
                row["group"] = group
            if description:
                row["description"] = description
            if reg_defaults["reset"] is not None:
                row["reset"] = f"0x{reg_defaults['reset']:0{size * 2}X}"
            if reg_defaults["access"] != "rw":
                row["access"] = reg_defaults["access"]
            if read_effect:
                row["read_effect"] = True
            if not rmw:
                row["rmw"] = False
            if fields:
                row["fields"] = fields
            self.registers.append(row)

    def _unique(self, name: str) -> str:
        candidate = name
        n = 2
        while candidate in self._names:
            candidate = f"{name}_{n}"
            n += 1
        self._names.add(candidate)
        return candidate


def _fields(fields_el: ET.Element, size: int) -> list[dict[str, Any]]:
    """``<fields>`` -> the document's ``fields`` list; malformed ones are skipped."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for el in fields_el.findall("field"):
        position = _bit_position(el)
        if position is None:
            continue
        bit, width = position
        if width < 1 or bit + width > size * 8:
            continue
        for label, dim_offset in _dims(el):
            name = _ident(_expand(_text(el, "name"), label))
            if name in seen:
                continue
            seen.add(name)
            field: dict[str, Any] = {"name": name, "bit": bit + dim_offset}
            if width != 1:
                field["width"] = width
            description = _clean(_text(el, "description"))
            if description:
                field["description"] = description
            values = _enumerated(el)
            if values:
                field["values"] = values
            out.append(field)
    return out


def _bit_position(el: ET.Element) -> tuple[int, int] | None:
    """``(bit, width)`` from whichever of the three SVD spellings is present."""
    offset = _int(_text(el, "bitOffset"))
    if offset is not None:
        width = _int(_text(el, "bitWidth"))
        return offset, width if width else 1
    match = _BIT_RANGE.match(_text(el, "bitRange"))
    if match:
        msb, lsb = int(match.group(1)), int(match.group(2))
        return lsb, msb - lsb + 1
    lsb = _int(_text(el, "lsb"))
    msb = _int(_text(el, "msb"))
    if lsb is not None and msb is not None:
        return lsb, msb - lsb + 1
    return None


def _enumerated(el: ET.Element) -> dict[str, str]:
    """The first ``<enumeratedValues>`` block as ``{"value": "NAME"}``."""
    block = el.find("enumeratedValues")
    if block is None:
        return {}
    values: dict[str, str] = {}
    for item in block.findall("enumeratedValue"):
        value = _int(_text(item, "value"))
        name = _text(item, "name")
        if value is None or not name:
            continue
        values[str(value)] = _ident(name)
    return values
