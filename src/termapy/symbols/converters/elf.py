"""ELF converter -- a build's symbols, with their C types from its debug info.

One converter for every toolchain that writes an ELF (GCC, Clang, XC32,
IAR, Arm Compiler, ...) on any CPU, and the only home of ELF and DWARF
knowledge.  pyelftools does the parsing; it is imported inside the
functions, because the converter registry loads at every engine start and
the library is wanted only by an import.

The symbol table (``.symtab``) gives every name its address, size and
section, the same facts a linker map gives, read from the linker's output
rather than its report:

- OBJECT and FUNC symbols, plus defined NOTYPE globals (linker-script
  symbols) as size-0 ``global``.  NOTYPE locals are skipped: they are
  ARM's ``$t`` / ``$d`` mapping symbols and assembler labels.
- A function address drops its instruction-set bit (bit 0 marks a Thumb
  or microMIPS function); the other functions on those CPUs are aligned,
  so clearing it never moves one.
- The section kind comes from the section header's FLAGS (executable =
  ``text``, writable no-bits = ``bss``, writable = ``data``, allocated =
  ``rodata``), never its name, so a toolchain's naming (XC32's
  ``.rodata%246``) does not matter.
- ``file`` is the source file: the STT_FILE entry that leads each run of
  local symbols, else the compile unit that defines the address.

The DWARF debug info, when the build has it (``-g``), gives the C type of
every variable at a fixed address, matched to its symbol BY ADDRESS (GCC
renames a function-local static ``count`` to ``count.0``).  A type is
written only when DWARF states it plainly -- by encoding and byte size,
never by the C name -- and anything else stays untyped, which is what a
map gives (registry ``elf-types-or-none``):

- a base type: ``u8``..``u64``, ``i8``..``i64``, ``f32``, ``f64``; a bool
  is ``u8``; a ``char`` is signed or unsigned as DWARF says (it differs
  by CPU);
- a pointer: the unsigned integer of its size;
- an enum: its underlying type, or (DWARF 2 producers that omit it)
  unsigned unless an enumerator is negative;
- an array of scalars: the element's type, the symbol's size the total;
- a struct: a format spec of its members in memory order, byte ranges in
  the ELF's byte order (``count:U4-3`` little-endian), ``S`` for a char
  array, ``H`` for anything else; bit-field members are left out for now;
- ``const`` anywhere in the chain: ``access: ro``;
- a ``const`` scalar carries its initializer as ``value``, decoded from
  the bytes the file loads at its address (an integer, an enumerator, a
  float to single precision, the address a pointer holds), and a pointer
  says what it points at in ``pointee`` (``scalar``, ``struct``,
  ``other``); a variable, an array and a struct carry no value (registry
  ``elf-values-const-only``).

Producer flavors handled: a fixed address as ``DW_OP_addr`` or Clang's
DWARF 5 ``DW_OP_addrx``; a definition that names its declaration with
``DW_AT_specification`` (a global declared in a header); member offsets
as a constant or a DWARF 2 location expression; two compile units that
disagree about one address (no type).

:func:`layout` reads the ELF header (address width, byte order) and one
memory region per loadable segment.  An ELF does not carry the linker
script's region names, so each is named after its first section's base
name (``vectors``, ``rodata``, ``bss``).
"""

from __future__ import annotations

import io
import re
import struct
from dataclasses import dataclass, replace
from pathlib import PureWindowsPath
from typing import TYPE_CHECKING, Any, Final

from termapy.symbols.table import SCALAR_TYPES, Layout, Region, Symbol, check_type

if TYPE_CHECKING:
    from elftools.dwarf.die import DIE
    from elftools.elf.elffile import ELFFile

FORMAT: Final[str] = "elf"
DESCRIPTION: Final[str] = "ELF executable (GCC, Clang, ...): symbols, C types from debug info"
DETECT: Final[tuple[str, ...]] = ()
MAGIC: Final[bytes] = b"\x7fELF"

# CPUs whose function addresses carry the instruction-set bit in bit 0.
_ISA_BIT_MACHINES: Final[frozenset[str]] = frozenset({"EM_ARM", "EM_MIPS"})

# DW_ATE_* base-type encodings (DWARF 2-5, section 7.8).
_ATE_BOOLEAN: Final[int] = 0x02
_ATE_FLOAT: Final[int] = 0x04
_ATE_SIGNED: Final[int] = 0x05
_ATE_SIGNED_CHAR: Final[int] = 0x06
_ATE_UNSIGNED: Final[int] = 0x07
_ATE_UNSIGNED_CHAR: Final[int] = 0x08

# Type modifiers a variable's type chain passes through to reach its type.
_QUALIFIERS: Final[frozenset[str]] = frozenset(
    {
        "DW_TAG_typedef",
        "DW_TAG_const_type",
        "DW_TAG_volatile_type",
        "DW_TAG_restrict_type",
        "DW_TAG_atomic_type",
    }
)

# Byte size of every scalar token; a constant is read only at exactly its size.
_SCALAR_BYTES: Final[dict[str, int]] = {token: int(token[1:]) // 8 for token in SCALAR_TYPES}

# A struct member becomes a format-spec column under its own name.
_MEMBER_NAME_RE: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _parse_errors() -> tuple[type[BaseException], ...]:
    """What pyelftools raises on a malformed or truncated file.

    Built on first use, for the same reason the library is imported late.
    """
    from elftools.common.exceptions import DWARFError, ELFError
    from elftools.construct import ConstructError

    return (ELFError, DWARFError, ConstructError, ValueError, KeyError, IndexError, struct.error)


def _open(data: bytes) -> ELFFile | None:
    """An ``ELFFile`` over ``data``, or None when it does not parse as one."""
    from elftools.elf.elffile import ELFFile

    try:
        return ELFFile(io.BytesIO(data))
    except _parse_errors():
        return None


@dataclass(frozen=True, slots=True)
class _Facts:
    """What DWARF says about the variable at one address.

    ``pointee`` is what a pointer-typed variable points at (``scalar``,
    ``struct``, ``other``), ``""`` for anything else.
    """

    type: str
    readonly: bool
    file: str
    pointee: str


def convert(data: bytes) -> list[Symbol]:
    """The ELF's symbols, typed where its debug info states the type.

    Args:
        data: The whole ELF file.

    Returns:
        The symbols, unsorted; empty when ``data`` is not an ELF.  Never
        raises on odd input: a malformed symbol table ends the pass with
        what was gathered, and malformed or missing debug info leaves the
        symbols untyped.
    """
    elf = _open(data)
    if elf is None:
        return []
    errors = _parse_errors()
    try:
        variables, functions = _dwarf_facts(elf)
    except errors:
        variables, functions = {}, {}
    symbols: list[Symbol] = []
    try:
        _read_symbols(elf, variables, functions, symbols)
    except errors:
        pass
    return symbols


def layout(data: bytes) -> Layout:
    """Address width and byte order from the header; a region per loadable segment.

    Never raises on odd input: a file that does not parse gives the
    table's defaults.
    """
    from elftools.elf.constants import P_FLAGS, SH_FLAGS

    elf = _open(data)
    if elf is None:
        return Layout()
    try:
        sections = [
            section
            for section in elf.iter_sections()
            if section["sh_flags"] & SH_FLAGS.SHF_ALLOC and section["sh_size"]
        ]
        regions: list[Region] = []
        for segment in elf.iter_segments():
            if segment["p_type"] != "PT_LOAD" or not segment["p_memsz"]:
                continue
            first = next(
                (section.name for section in sections if segment.section_in_segment(section)), ""
            )
            regions.append(
                Region(
                    _region_name(first, len(regions)),
                    segment["p_vaddr"],
                    segment["p_memsz"],
                    "rw" if segment["p_flags"] & P_FLAGS.PF_W else "ro",
                )
            )
        return Layout(tuple(regions), 64 if elf.elfclass == 64 else 32, _endian(elf))
    except _parse_errors():
        return Layout()


def _endian(elf: ELFFile) -> str:
    return "le" if elf.little_endian else "be"


def _region_name(section: str, index: int) -> str:
    """A section's base name: ``.rodata%246`` and ``.bss.sState`` -> ``rodata``, ``bss``.

    A segment with no named section is ``load<n>``.  The base name drops a
    per-object suffix (``-ffunction-sections`` / ``-fdata-sections``) and
    XC32's ``%NNN``, so a region reads as its kind of memory.
    """
    name = re.split(r"[.%]", section.lstrip("."), maxsplit=1)[0]
    return name or f"load{index}"


def _basename(path: str) -> str:
    """A source path as DWARF or the symbol table spells it, reduced to its name."""
    return PureWindowsPath(path).name


# ── the symbol table ────────────────────────────────────────────────────────


def _read_symbols(
    elf: ELFFile,
    variables: dict[int, _Facts],
    functions: dict[int, str],
    out: list[Symbol],
) -> None:
    """Append every nameable symbol of ``.symtab`` to ``out``."""
    from elftools.elf.sections import SymbolTableSection

    symtab = elf.get_section_by_name(".symtab")
    if not isinstance(symtab, SymbolTableSection):
        return
    kinds = [_section_kind(section) for section in elf.iter_sections()]
    isa_bit = elf["e_machine"] in _ISA_BIT_MACHINES
    # Locals come first, each file's after its STT_FILE entry; from
    # sh_info on, every symbol is global and the file is DWARF's to say.
    first_global = symtab["sh_info"]
    local_file = ""
    seen: set[tuple[str, int]] = set()
    endian = _endian(elf)
    errors = _parse_errors()
    images: dict[int, tuple[int, bytes] | None] = {}
    for index, sym in enumerate(symtab.iter_symbols()):
        info = sym["st_info"]
        kind = info["type"]
        if kind == "STT_FILE":
            local_file = _basename(sym.name)
            continue
        shndx = sym["st_shndx"]
        if not sym.name or shndx == "SHN_UNDEF":
            continue
        if kind not in ("STT_OBJECT", "STT_FUNC", "STT_NOTYPE"):
            continue
        if kind == "STT_NOTYPE" and info["bind"] == "STB_LOCAL":
            continue
        addr = sym["st_value"]
        if kind == "STT_FUNC" and isa_bit:
            addr &= ~1
        key = (sym.name, addr)
        if key in seen:
            continue
        seen.add(key)
        if kind == "STT_NOTYPE" or not isinstance(shndx, int):
            section = "global"
        else:
            section = kinds[shndx] if shndx < len(kinds) else ""
        facts = variables.get(addr) if kind == "STT_OBJECT" else None
        if index < first_global:
            file = local_file
        elif facts is not None:
            file = facts.file
        elif kind == "STT_FUNC":
            file = functions.get(addr, "")
        else:
            # A linker-script symbol: it may share a function's address,
            # but no source file defines it.
            file = ""
        value: int | float | None = None
        pointee = ""
        if facts is not None and facts.readonly and isinstance(shndx, int):
            # A const object's bytes are its value for the program's life;
            # a variable's are only its initializer, so nothing is read.
            try:
                value = _constant_value(
                    elf, images, shndx, addr, sym["st_size"], facts.type, endian
                )
            except errors:
                value = None
            if value is not None:
                pointee = facts.pointee
        out.append(
            Symbol(
                sym.name,
                addr,
                sym["st_size"],
                section,
                file=file,
                type=facts.type if facts is not None else "",
                access="ro" if facts is not None and facts.readonly else "",
                value=value,
                pointee=pointee,
            )
        )


def _section_kind(section: Any) -> str:
    """The section vocabulary from a section header's flags; ``""`` when not loaded."""
    from elftools.elf.constants import SH_FLAGS

    flags = section["sh_flags"]
    if not flags & SH_FLAGS.SHF_ALLOC:
        return ""
    if flags & SH_FLAGS.SHF_EXECINSTR:
        return "text"
    if flags & SH_FLAGS.SHF_WRITE:
        return "bss" if section["sh_type"] == "SHT_NOBITS" else "data"
    return "rodata"


def _constant_value(
    elf: ELFFile,
    images: dict[int, tuple[int, bytes] | None],
    shndx: int,
    addr: int,
    size: int,
    token: str,
    endian: str,
) -> int | float | None:
    """A const scalar's initializer: the bytes its section loads, decoded by its type.

    None when the type is not a scalar (an array, a struct, untyped), the
    size is not the scalar's (an array typed by its element), or the
    section has no file contents.
    """
    if token not in _SCALAR_BYTES or size != _SCALAR_BYTES[token]:
        return None
    image = _section_image(elf, images, shndx)
    if image is None:
        return None
    start, data = image
    offset = addr - start
    if offset < 0 or offset + size > len(data):
        return None
    raw = data[offset : offset + size]
    if token[0] == "f":
        code = ("<" if endian == "le" else ">") + ("f" if size == 4 else "d")
        number = float(struct.unpack(code, raw)[0])
        # A float's shortest spelling at its own precision: 0.1f reads
        # back as 0.1, not as its 17-digit double expansion.
        return float(f"{number:.7g}") if size == 4 else number
    return int.from_bytes(raw, "little" if endian == "le" else "big", signed=token[0] == "i")


def _section_image(
    elf: ELFFile,
    images: dict[int, tuple[int, bytes] | None],
    shndx: int,
) -> tuple[int, bytes] | None:
    """``(sh_addr, contents)`` of a section, read once per index; None for a NOBITS section."""
    if shndx not in images:
        section = elf.get_section(shndx)
        images[shndx] = (
            None if section["sh_type"] == "SHT_NOBITS" else (section["sh_addr"], section.data())
        )
    return images[shndx]


# ── the debug info ──────────────────────────────────────────────────────────


def _dwarf_facts(elf: ELFFile) -> tuple[dict[int, _Facts], dict[int, str]]:
    """Type facts per variable address, and the compile unit per function address."""
    if not elf.has_dwarf_info():
        return {}, {}
    from elftools.dwarf.dwarf_expr import DWARFExprParser

    dwarf = elf.get_dwarf_info()
    endian = _endian(elf)
    variables: dict[int, _Facts] = {}
    functions: dict[int, str] = {}
    disputed: set[int] = set()
    for cu in dwarf.iter_CUs():
        unit_file = _basename(_text(cu.get_top_DIE(), "DW_AT_name"))
        parser = DWARFExprParser(cu.structs)
        for die in cu.iter_DIEs():
            if die.tag == "DW_TAG_subprogram":
                low_pc = die.attributes.get("DW_AT_low_pc")
                if low_pc is not None and isinstance(low_pc.value, int):
                    functions.setdefault(low_pc.value, unit_file)
                continue
            if die.tag != "DW_TAG_variable":
                continue
            addr = _fixed_address(die, parser, dwarf)
            if addr is None:
                continue
            declaration = _declaration_of(die)
            spec, readonly = _type_of(declaration, endian, parser)
            facts = _Facts(spec, readonly, unit_file, _pointee_of(declaration))
            known = variables.setdefault(addr, facts)
            if known.type != facts.type:
                disputed.add(addr)
    for addr in disputed:
        variables[addr] = replace(variables[addr], type="", pointee="")
    return variables, functions


def _fixed_address(die: DIE, parser: Any, dwarf: Any) -> int | None:
    """The address of a variable that lives at one, or None.

    Only a location that is exactly one ``DW_OP_addr`` (DWARF 2-4) or
    ``DW_OP_addrx`` (an index into ``.debug_addr``, Clang's DWARF 5) is a
    fixed address; a register, a stack slot, a location list or a
    computed value is not.
    """
    location = die.attributes.get("DW_AT_location")
    if location is None or not isinstance(location.value, list):
        return None
    ops = parser.parse_expr(location.value)
    if len(ops) != 1 or not ops[0].args:
        return None
    op = ops[0]
    if op.op_name == "DW_OP_addr":
        return op.args[0]
    if op.op_name in ("DW_OP_addrx", "DW_OP_GNU_addr_index"):
        return dwarf.get_addr(die.cu, op.args[0])
    return None


def _declaration_of(die: DIE) -> DIE:
    """The DIE carrying a variable's type: itself, or what its definition points to.

    A global declared in a header is described twice: a declaration with
    the name and type, and a definition with the address that names the
    declaration through ``DW_AT_specification`` (or, for an inlined or
    abstract instance, ``DW_AT_abstract_origin``).
    """
    for _ in range(4):
        if "DW_AT_type" in die.attributes:
            return die
        for link in ("DW_AT_specification", "DW_AT_abstract_origin"):
            if link in die.attributes:
                die = die.get_DIE_from_attribute(link)
                break
        else:
            return die
    return die


def _type_of(die: DIE, endian: str, parser: Any) -> tuple[str, bool]:
    """``(type, readonly)`` for the type ``die`` refers to; ``""`` when not plain."""
    node = _referenced(die)
    readonly = False
    while node is not None and node.tag in _QUALIFIERS:
        readonly = readonly or node.tag == "DW_TAG_const_type"
        node = _referenced(node)
    if node is None:
        return "", readonly
    if node.tag == "DW_TAG_array_type":
        element, element_readonly = _type_of(node, endian, parser)
        return (element if element in SCALAR_TYPES else ""), readonly or element_readonly
    if node.tag == "DW_TAG_structure_type":
        return _struct_spec(node, endian, parser), readonly
    return _scalar(node), readonly


def _referenced(die: DIE) -> DIE | None:
    """The DIE ``die``'s ``DW_AT_type`` names, or None (``void``, or none given)."""
    if "DW_AT_type" not in die.attributes:
        return None
    return die.get_DIE_from_attribute("DW_AT_type")


def _pointee_of(die: DIE) -> str:
    """What a pointer variable points at: ``scalar``, ``struct`` or ``other``; ``""`` if no pointer.

    Qualifiers are skipped on both sides, so ``volatile PortGroup *const``
    is a pointer to a struct.  A pointer to void, to a function, to an
    array or to a union is ``other``: nothing downstream knows what to
    make of it, and the alias rule must not guess.
    """
    node = _referenced(die)
    while node is not None and node.tag in _QUALIFIERS:
        node = _referenced(node)
    if node is None or node.tag not in ("DW_TAG_pointer_type", "DW_TAG_reference_type"):
        return ""
    target = _referenced(node)
    while target is not None and target.tag in _QUALIFIERS:
        target = _referenced(target)
    if target is None:
        return "other"
    if target.tag == "DW_TAG_structure_type":
        return "struct"
    if target.tag in ("DW_TAG_base_type", "DW_TAG_enumeration_type", "DW_TAG_pointer_type"):
        return "scalar"
    return "other"


def _scalar(node: DIE) -> str:
    """A scalar token for a base type, pointer or enum; ``""`` for anything else."""
    size = _int(node, "DW_AT_byte_size")
    if node.tag == "DW_TAG_base_type":
        encoding = _int(node, "DW_AT_encoding")
        if encoding == _ATE_FLOAT:
            return {4: "f32", 8: "f64"}.get(size, "")
        if encoding in (_ATE_SIGNED, _ATE_SIGNED_CHAR):
            return _integer("i", size)
        if encoding in (_ATE_UNSIGNED, _ATE_UNSIGNED_CHAR, _ATE_BOOLEAN):
            return _integer("u", size)
        return ""
    if node.tag in ("DW_TAG_pointer_type", "DW_TAG_reference_type"):
        return _integer("u", size or node.cu["address_size"])
    if node.tag == "DW_TAG_enumeration_type":
        underlying = _referenced(node)
        while underlying is not None and underlying.tag in _QUALIFIERS:
            underlying = _referenced(underlying)
        if underlying is not None:
            return _scalar(underlying)
        negative = any(
            _int(child, "DW_AT_const_value") < 0
            for child in node.iter_children()
            if child.tag == "DW_TAG_enumerator"
        )
        return _integer("i" if negative else "u", size)
    return ""


def _integer(prefix: str, size: int) -> str:
    token = f"{prefix}{size * 8}"
    return token if token in SCALAR_TYPES else ""


def _byte_size(node: DIE) -> int:
    """A type's size in bytes; an array's is its element's times its count."""
    while node.tag in _QUALIFIERS:
        target = _referenced(node)
        if target is None:
            return 0
        node = target
    size = _int(node, "DW_AT_byte_size")
    if size or node.tag != "DW_TAG_array_type":
        return size
    element = _referenced(node)
    count = 1
    for child in node.iter_children():
        if child.tag != "DW_TAG_subrange_type":
            continue
        if "DW_AT_count" in child.attributes:
            count *= _int(child, "DW_AT_count")
        elif "DW_AT_upper_bound" in child.attributes:
            count *= _int(child, "DW_AT_upper_bound") + 1
        else:
            return 0
    return count * _byte_size(element) if element is not None else 0


def _struct_spec(node: DIE, endian: str, parser: Any) -> str:
    """A struct as a format spec of its members; ``""`` when none can be spelled."""
    columns: list[str] = []
    for member in node.iter_children():
        if member.tag != "DW_TAG_member":
            continue
        name = _text(member, "DW_AT_name")
        # Bit fields wait for the member-access step: DWARF 2 counts their
        # offsets from the storage unit's most significant bit.
        if not _MEMBER_NAME_RE.match(name) or "DW_AT_bit_size" in member.attributes:
            continue
        offset = _member_offset(member, parser)
        target = _referenced(member)
        if offset is None or target is None:
            continue
        token = _member_column(target, offset, endian, parser)
        if token:
            columns.append(f"{name}:{token}")
    spec = " ".join(columns)
    if not spec:
        return ""
    try:
        check_type(spec, "struct")
    except ValueError:
        return ""
    return spec


def _member_offset(member: DIE, parser: Any) -> int | None:
    """A member's byte offset: a constant, or a DWARF 2 ``DW_OP_plus_uconst``."""
    location = member.attributes.get("DW_AT_data_member_location")
    if location is None:
        return 0  # a union member, or the only one
    if isinstance(location.value, int):
        return location.value
    ops = parser.parse_expr(location.value)
    if len(ops) == 1 and ops[0].op_name == "DW_OP_plus_uconst":
        return ops[0].args[0]
    return None


def _member_column(target: DIE, offset: int, endian: str, parser: Any) -> str:
    """One member's column body: ``U4-3``, ``F8-5``, ``S9-16``, ``H17-20``."""
    size = _byte_size(target)
    if not size:
        return ""
    node = target
    while node.tag in _QUALIFIERS:
        following = _referenced(node)
        if following is None:
            return ""
        node = following
    first, last = offset + 1, offset + size
    ascending = f"{first}" if size == 1 else f"{first}-{last}"
    if node.tag == "DW_TAG_array_type":
        element = _scalar_of_array(node)
        return (
            f"S{ascending}" if element in ("u8", "i8") and _is_char_array(node) else f"H{ascending}"
        )
    scalar = _scalar(node)
    if not scalar:
        return f"H{ascending}"
    in_order = ascending if size == 1 or endian == "be" else f"{last}-{first}"
    code = "F" if scalar.startswith("f") else "I" if scalar.startswith("i") else "U"
    return f"{code}{in_order}"


def _scalar_of_array(node: DIE) -> str:
    element = _referenced(node)
    while element is not None and element.tag in _QUALIFIERS:
        element = _referenced(element)
    return _scalar(element) if element is not None else ""


def _is_char_array(node: DIE) -> bool:
    """True when the array's elements are a character type (a C string)."""
    element = _referenced(node)
    while element is not None and element.tag in _QUALIFIERS:
        element = _referenced(element)
    return (
        element is not None
        and element.tag == "DW_TAG_base_type"
        and _int(element, "DW_AT_encoding") in (_ATE_SIGNED_CHAR, _ATE_UNSIGNED_CHAR)
    )


def _int(die: DIE, attribute: str) -> int:
    value = die.attributes.get(attribute)
    return value.value if value is not None and isinstance(value.value, int) else 0


def _text(die: DIE, attribute: str) -> str:
    value = die.attributes.get(attribute)
    if value is None:
        return ""
    raw = value.value
    return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
