"""CRC source code generation for C, Python, and Rust.

Generates standalone CRC functions from catalogue parameters.
Pure functions, no dependencies beyond protocol_crc.
"""

from __future__ import annotations

from termapy.protocol_crc import CRC_CATALOGUE, _reflect


def _func_name(algo_name: str) -> str:
    """Convert algorithm name to a valid function name."""
    return algo_name.replace("-", "_").replace(".", "_")


def _hex(value: int, width: int) -> str:
    """Format an integer as a hex literal with appropriate width."""
    hex_w = (width + 3) // 4  # hex digits needed
    return f"0x{value:0{hex_w}X}"


def _mask(width: int) -> str:
    """Format the bit mask for a given width."""
    return _hex((1 << width) - 1, width)


def generate_c(name: str) -> str | None:
    """Generate a C function for a CRC algorithm.

    Args:
        name: Algorithm name from CRC_CATALOGUE.

    Returns:
        C source code string, or None if algorithm not found.
    """
    entry = CRC_CATALOGUE.get(name)
    if entry is None:
        return None

    w = entry["width"]
    poly = entry["poly"]
    init = entry["init"]
    refin = entry["refin"]
    refout = entry["refout"]
    xorout = entry["xorout"]
    check = entry["check"]
    desc = entry.get("desc", "")
    fname = _func_name(name)
    mask = _mask(w)

    if w <= 8:
        ctype = "uint8_t"
    elif w <= 16:
        ctype = "uint16_t"
    else:
        ctype = "uint32_t"

    lines = []
    lines.append(f"/* {name} - {desc} */")
    lines.append(f"/* check: crc(\"{'{'}123456789{'}'}\") == {_hex(check, w)} */")
    lines.append(f"{ctype} {fname}(const uint8_t *data, size_t len) {{")

    if refin:
        ref_poly = _reflect(poly, w)
        ref_init = _reflect(init, w)
        lines.append(f"    {ctype} crc = {_hex(ref_init, w)};")
        lines.append(f"    for (size_t i = 0; i < len; i++) {{")
        lines.append(f"        crc ^= data[i];")
        lines.append(f"        for (int j = 0; j < 8; j++) {{")
        lines.append(f"            if (crc & 1)")
        lines.append(f"                crc = (crc >> 1) ^ {_hex(ref_poly, w)};")
        lines.append(f"            else")
        lines.append(f"                crc >>= 1;")
        lines.append(f"        }}")
        lines.append(f"    }}")
    else:
        lines.append(f"    {ctype} crc = {_hex(init, w)};")
        lines.append(f"    for (size_t i = 0; i < len; i++) {{")
        lines.append(f"        crc ^= ({ctype})data[i] << {w - 8};")
        lines.append(f"        for (int j = 0; j < 8; j++) {{")
        lines.append(f"            if (crc & {_hex(1 << (w - 1), w)})")
        lines.append(f"                crc = (crc << 1) ^ {_hex(poly, w)};")
        lines.append(f"            else")
        lines.append(f"                crc <<= 1;")
        lines.append(f"            crc &= {mask};")
        lines.append(f"        }}")
        lines.append(f"    }}")

    if refout != refin:
        lines.append(f"    /* reflect output */")
        lines.append(f"    {ctype} reflected = 0;")
        lines.append(f"    for (int k = 0; k < {w}; k++)")
        lines.append(f"        reflected |= ((crc >> k) & 1) << ({w - 1} - k);")
        lines.append(f"    crc = reflected;")

    if xorout:
        lines.append(f"    return crc ^ {_hex(xorout, w)};")
    else:
        lines.append(f"    return crc;")
    lines.append(f"}}")

    return "\n".join(lines)


def generate_python(name: str) -> str | None:
    """Generate a Python function for a CRC algorithm.

    Args:
        name: Algorithm name from CRC_CATALOGUE.

    Returns:
        Python source code string, or None if algorithm not found.
    """
    entry = CRC_CATALOGUE.get(name)
    if entry is None:
        return None

    w = entry["width"]
    poly = entry["poly"]
    init = entry["init"]
    refin = entry["refin"]
    refout = entry["refout"]
    xorout = entry["xorout"]
    check = entry["check"]
    desc = entry.get("desc", "")
    fname = _func_name(name)
    mask = _mask(w)

    lines = []
    lines.append(f"def {fname}(data: bytes) -> int:")
    lines.append(f'    """{name} - {desc}')
    lines.append(f"")
    lines.append(f"    check: crc(b'123456789') == {_hex(check, w)}")
    lines.append(f'    """')

    if refin:
        ref_poly = _reflect(poly, w)
        ref_init = _reflect(init, w)
        lines.append(f"    crc = {_hex(ref_init, w)}")
        lines.append(f"    for byte in data:")
        lines.append(f"        crc ^= byte")
        lines.append(f"        for _ in range(8):")
        lines.append(f"            if crc & 1:")
        lines.append(f"                crc = (crc >> 1) ^ {_hex(ref_poly, w)}")
        lines.append(f"            else:")
        lines.append(f"                crc >>= 1")
    else:
        lines.append(f"    crc = {_hex(init, w)}")
        lines.append(f"    for byte in data:")
        lines.append(f"        crc ^= byte << {w - 8}")
        lines.append(f"        for _ in range(8):")
        lines.append(f"            if crc & {_hex(1 << (w - 1), w)}:")
        lines.append(f"                crc = (crc << 1) ^ {_hex(poly, w)}")
        lines.append(f"            else:")
        lines.append(f"                crc <<= 1")
        lines.append(f"            crc &= {mask}")

    if refout != refin:
        lines.append(f"    # reflect output")
        lines.append(f"    crc = sum(((crc >> k) & 1) << ({w - 1} - k) for k in range({w}))")

    if xorout:
        lines.append(f"    return crc ^ {_hex(xorout, w)}")
    else:
        lines.append(f"    return crc")

    return "\n".join(lines)


def generate_rust(name: str) -> str | None:
    """Generate a Rust function for a CRC algorithm.

    Args:
        name: Algorithm name from CRC_CATALOGUE.

    Returns:
        Rust source code string, or None if algorithm not found.
    """
    entry = CRC_CATALOGUE.get(name)
    if entry is None:
        return None

    w = entry["width"]
    poly = entry["poly"]
    init = entry["init"]
    refin = entry["refin"]
    refout = entry["refout"]
    xorout = entry["xorout"]
    check = entry["check"]
    desc = entry.get("desc", "")
    fname = _func_name(name)
    mask = _mask(w)

    if w <= 8:
        rtype = "u8"
    elif w <= 16:
        rtype = "u16"
    else:
        rtype = "u32"

    lines = []
    lines.append(f"/// {name} - {desc}")
    lines.append(f"/// check: crc(b\"123456789\") == {_hex(check, w)}")
    lines.append(f"fn {fname}(data: &[u8]) -> {rtype} {{")

    if refin:
        ref_poly = _reflect(poly, w)
        ref_init = _reflect(init, w)
        lines.append(f"    let mut crc: {rtype} = {_hex(ref_init, w)};")
        lines.append(f"    for &byte in data {{")
        lines.append(f"        crc ^= byte as {rtype};")
        lines.append(f"        for _ in 0..8 {{")
        lines.append(f"            if crc & 1 != 0 {{")
        lines.append(f"                crc = (crc >> 1) ^ {_hex(ref_poly, w)};")
        lines.append(f"            }} else {{")
        lines.append(f"                crc >>= 1;")
        lines.append(f"            }}")
        lines.append(f"        }}")
        lines.append(f"    }}")
    else:
        lines.append(f"    let mut crc: {rtype} = {_hex(init, w)};")
        lines.append(f"    for &byte in data {{")
        lines.append(f"        crc ^= (byte as {rtype}) << {w - 8};")
        lines.append(f"        for _ in 0..8 {{")
        lines.append(f"            if crc & {_hex(1 << (w - 1), w)} != 0 {{")
        lines.append(f"                crc = (crc << 1) ^ {_hex(poly, w)};")
        lines.append(f"            }} else {{")
        lines.append(f"                crc <<= 1;")
        lines.append(f"            }}")
        lines.append(f"            crc &= {mask};")
        lines.append(f"        }}")
        lines.append(f"    }}")

    if refout != refin:
        lines.append(f"    // reflect output")
        lines.append(f"    let mut reflected: {rtype} = 0;")
        lines.append(f"    for k in 0..{w} {{")
        lines.append(f"        reflected |= ((crc >> k) & 1) << ({w - 1} - k);")
        lines.append(f"    }}")
        lines.append(f"    crc = reflected;")

    if xorout:
        lines.append(f"    crc ^ {_hex(xorout, w)}")
    else:
        lines.append(f"    crc")
    lines.append(f"}}")

    return "\n".join(lines)


GENERATORS: dict[str, callable] = {
    "c": generate_c,
    "python": generate_python,
    "rust": generate_rust,
}
