"""CRC engine, catalogue, and plugin loading.

Generic Rocksoft/Williams CRC computation with 62+ named algorithms from
the reveng catalogue. Supports user-defined CRC plugins via .py files.

Pure functions and classes with no Textual or pyserial dependencies.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


# ---------------------------------------------------------------------------
# Generic CRC engine - Rocksoft/Williams parameterization
# ---------------------------------------------------------------------------


def _reflect(value: int, width: int) -> int:
    """Bit-reverse a value within the given bit width.

    Args:
        value: Integer to reflect.
        width: Number of bits to reverse.

    Returns:
        Bit-reversed value.
    """
    result = 0
    for _ in range(width):
        result = (result << 1) | (value & 1)
        value >>= 1
    return result


def _generic_crc(
    data: bytes,
    width: int,
    poly: int,
    init: int,
    refin: bool,
    refout: bool,
    xorout: int,
) -> int:
    """Compute CRC using Rocksoft/Williams parameterization.

    Args:
        data: Payload bytes.
        width: CRC bit width (8, 16, 32, etc.).
        poly: Generator polynomial in normal (MSB-first) form.
        init: Initial register value.
        refin: True to reflect each input byte.
        refout: True to reflect the final CRC value.
        xorout: XOR applied to the final CRC value.

    Returns:
        Computed CRC value.
    """
    crc = init
    if refin:
        # Reflected algorithm: process LSB-first with reflected polynomial.
        # Init must also be reflected to match the reversed register layout.
        ref_poly = _reflect(poly, width)
        crc = _reflect(init, width)
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ ref_poly
                else:
                    crc >>= 1
    else:
        # Normal algorithm: process MSB-first
        msb_mask = 1 << (width - 1)
        for byte in data:
            crc ^= byte << (width - 8)
            for _ in range(8):
                if crc & msb_mask:
                    crc = (crc << 1) ^ poly
                else:
                    crc <<= 1
            crc &= (1 << width) - 1
    if refout != refin:
        crc = _reflect(crc, width)
    return crc ^ xorout


# ---------------------------------------------------------------------------
# CRC catalogue - named algorithms from the reveng catalogue
# ---------------------------------------------------------------------------
# Source: https://reveng.sourceforge.io/crc-catalogue/all.htm
# Each entry: width, poly (normal form), init, refin, refout, xorout, check.
# check = CRC of b"123456789" - used as test vectors.

CRC_CATALOGUE: dict[str, dict] = {
    # ---- CRC-8 (20 algorithms) ----
    "crc8":             {"width": 8, "poly": 0x07, "init": 0x00, "refin": False, "refout": False, "xorout": 0x00, "check": 0xF4, "desc": "ITU-T I.432.1 (ATM HEC), ISDN"},
    "crc8-autosar":     {"width": 8, "poly": 0x2F, "init": 0xFF, "refin": False, "refout": False, "xorout": 0xFF, "check": 0xDF, "desc": "AUTOSAR automotive E2E profiles"},
    "crc8-bluetooth":   {"width": 8, "poly": 0xA7, "init": 0x00, "refin": True,  "refout": True,  "xorout": 0x00, "check": 0x26, "desc": "Bluetooth HEC (header error check)"},
    "crc8-cdma2000":    {"width": 8, "poly": 0x9B, "init": 0xFF, "refin": False, "refout": False, "xorout": 0x00, "check": 0xDA, "desc": "CDMA2000 mobile telephony"},
    "crc8-darc":        {"width": 8, "poly": 0x39, "init": 0x00, "refin": True,  "refout": True,  "xorout": 0x00, "check": 0x15, "desc": "DARC (Data Radio Channel)"},
    "crc8-dvb-s2":      {"width": 8, "poly": 0xD5, "init": 0x00, "refin": False, "refout": False, "xorout": 0x00, "check": 0xBC, "desc": "DVB-S2 satellite TV baseband frames"},
    "crc8-gsm-a":       {"width": 8, "poly": 0x1D, "init": 0x00, "refin": False, "refout": False, "xorout": 0x00, "check": 0x37, "desc": "GSM/3GPP control channel (type A)"},
    "crc8-gsm-b":       {"width": 8, "poly": 0x49, "init": 0x00, "refin": False, "refout": False, "xorout": 0xFF, "check": 0x94, "desc": "GSM/3GPP control channel (type B)"},
    "crc8-hitag":       {"width": 8, "poly": 0x1D, "init": 0xFF, "refin": False, "refout": False, "xorout": 0x00, "check": 0xB4, "desc": "Philips HITAG RFID transponders"},
    "crc8-i-432-1":     {"width": 8, "poly": 0x07, "init": 0x00, "refin": False, "refout": False, "xorout": 0x55, "check": 0xA1, "desc": "ITU-T I.432.1 ATM HEC (alt init)"},
    "crc8-i-code":      {"width": 8, "poly": 0x1D, "init": 0xFD, "refin": False, "refout": False, "xorout": 0x00, "check": 0x7E, "desc": "Philips ICODE RFID SLI systems"},
    "crc8-lte":         {"width": 8, "poly": 0x9B, "init": 0x00, "refin": False, "refout": False, "xorout": 0x00, "check": 0xEA, "desc": "3GPP LTE (Long Term Evolution)"},
    "crc8-maxim":       {"width": 8, "poly": 0x31, "init": 0x00, "refin": True,  "refout": True,  "xorout": 0x00, "check": 0xA1, "desc": "Dallas/Maxim 1-Wire bus (DOW CRC)"},
    "crc8-mifare-mad":  {"width": 8, "poly": 0x1D, "init": 0xC7, "refin": False, "refout": False, "xorout": 0x00, "check": 0x99, "desc": "NXP MIFARE Application Directory"},
    "crc8-nrsc-5":      {"width": 8, "poly": 0x31, "init": 0xFF, "refin": False, "refout": False, "xorout": 0x00, "check": 0xF7, "desc": "NRSC-5 HD Radio digital broadcast"},
    "crc8-opensafety":  {"width": 8, "poly": 0x2F, "init": 0x00, "refin": False, "refout": False, "xorout": 0x00, "check": 0x3E, "desc": "OpenSAFETY industrial safety protocol"},
    "crc8-rohc":        {"width": 8, "poly": 0x07, "init": 0xFF, "refin": True,  "refout": True,  "xorout": 0x00, "check": 0xD0, "desc": "ROHC (Robust Header Compression)"},
    "crc8-sae-j1850":   {"width": 8, "poly": 0x1D, "init": 0xFF, "refin": False, "refout": False, "xorout": 0xFF, "check": 0x4B, "desc": "SAE J1850 automotive OBD-II bus"},
    "crc8-tech-3250":   {"width": 8, "poly": 0x1D, "init": 0xFF, "refin": True,  "refout": True,  "xorout": 0x00, "check": 0x97, "desc": "EBU Tech 3250 (AES3 audio)"},
    "crc8-wcdma":       {"width": 8, "poly": 0x9B, "init": 0x00, "refin": True,  "refout": True,  "xorout": 0x00, "check": 0x25, "desc": "WCDMA/UMTS 3G mobile embedded"},
    # ---- CRC-16 (30 algorithms) ----
    "crc16-arc":            {"width": 16, "poly": 0x8005, "init": 0x0000, "refin": True,  "refout": True,  "xorout": 0x0000, "check": 0xBB3D, "desc": "ARC archive, LHA (IBM CRC-16)"},
    "crc16-cdma2000":       {"width": 16, "poly": 0xC867, "init": 0xFFFF, "refin": False, "refout": False, "xorout": 0x0000, "check": 0x4C06, "desc": "CDMA2000 mobile telephony"},
    "crc16-cms":            {"width": 16, "poly": 0x8005, "init": 0xFFFF, "refin": False, "refout": False, "xorout": 0x0000, "check": 0xAEE7, "desc": "CMS (RPM package format)"},
    "crc16-dds-110":        {"width": 16, "poly": 0x8005, "init": 0x800D, "refin": False, "refout": False, "xorout": 0x0000, "check": 0x9ECF, "desc": "ELV DDS-110 weather station"},
    "crc16-dect-r":         {"width": 16, "poly": 0x0589, "init": 0x0000, "refin": False, "refout": False, "xorout": 0x0001, "check": 0x007E, "desc": "DECT cordless telephony (R-CRC)"},
    "crc16-dect-x":         {"width": 16, "poly": 0x0589, "init": 0x0000, "refin": False, "refout": False, "xorout": 0x0000, "check": 0x007F, "desc": "DECT cordless telephony (X-CRC)"},
    "crc16-dnp":            {"width": 16, "poly": 0x3D65, "init": 0x0000, "refin": True,  "refout": True,  "xorout": 0xFFFF, "check": 0xEA82, "desc": "DNP3 (Distributed Network Protocol)"},
    "crc16-en-13757":       {"width": 16, "poly": 0x3D65, "init": 0x0000, "refin": False, "refout": False, "xorout": 0xFFFF, "check": 0xC2B7, "desc": "EN 13757 wireless M-Bus metering"},
    "crc16-genibus":        {"width": 16, "poly": 0x1021, "init": 0xFFFF, "refin": False, "refout": False, "xorout": 0xFFFF, "check": 0xD64E, "desc": "GENIBUS (EPC Gen2 RFID)"},
    "crc16-gsm":            {"width": 16, "poly": 0x1021, "init": 0x0000, "refin": False, "refout": False, "xorout": 0xFFFF, "check": 0xCE3C, "desc": "GSM mobile network control channel"},
    "crc16-ibm-3740":       {"width": 16, "poly": 0x1021, "init": 0xFFFF, "refin": False, "refout": False, "xorout": 0x0000, "check": 0x29B1, "desc": "IBM 3740 floppy disk, CCITT-FALSE"},
    "crc16-ibm-sdlc":       {"width": 16, "poly": 0x1021, "init": 0xFFFF, "refin": True,  "refout": True,  "xorout": 0xFFFF, "check": 0x906E, "desc": "IBM SDLC, ISO HDLC, X.25 FCS"},
    "crc16-iso-iec-14443-3-a": {"width": 16, "poly": 0x1021, "init": 0xC6C6, "refin": True, "refout": True, "xorout": 0x0000, "check": 0xBF05, "desc": "ISO 14443-3 Type A NFC/RFID"},
    "crc16-kermit":         {"width": 16, "poly": 0x1021, "init": 0x0000, "refin": True,  "refout": True,  "xorout": 0x0000, "check": 0x2189, "desc": "Kermit file transfer protocol"},
    "crc16-lj1200":         {"width": 16, "poly": 0x6F63, "init": 0x0000, "refin": False, "refout": False, "xorout": 0x0000, "check": 0xBDF4, "desc": "LJ1200 telemetry"},
    "crc16-m17":            {"width": 16, "poly": 0x5935, "init": 0xFFFF, "refin": False, "refout": False, "xorout": 0x0000, "check": 0x772B, "desc": "M17 Project digital voice radio"},
    "crc16-maxim":          {"width": 16, "poly": 0x8005, "init": 0x0000, "refin": True,  "refout": True,  "xorout": 0xFFFF, "check": 0x44C2, "desc": "Maxim/Dallas 1-Wire 16-bit"},
    "crc16-mcrf4xx":        {"width": 16, "poly": 0x1021, "init": 0xFFFF, "refin": True,  "refout": True,  "xorout": 0x0000, "check": 0x6F91, "desc": "Microchip MCRF4xx RFID tags"},
    "crc16-modbus":         {"width": 16, "poly": 0x8005, "init": 0xFFFF, "refin": True,  "refout": True,  "xorout": 0x0000, "check": 0x4B37, "desc": "Modbus RTU serial protocol"},
    "crc16-nrsc-5":         {"width": 16, "poly": 0x080B, "init": 0xFFFF, "refin": True,  "refout": True,  "xorout": 0x0000, "check": 0xA066, "desc": "NRSC-5 HD Radio digital broadcast"},
    "crc16-opensafety-a":   {"width": 16, "poly": 0x5935, "init": 0x0000, "refin": False, "refout": False, "xorout": 0x0000, "check": 0x5D38, "desc": "OpenSAFETY field A"},
    "crc16-opensafety-b":   {"width": 16, "poly": 0x755B, "init": 0x0000, "refin": False, "refout": False, "xorout": 0x0000, "check": 0x20FE, "desc": "OpenSAFETY field B"},
    "crc16-profibus":       {"width": 16, "poly": 0x1DCF, "init": 0xFFFF, "refin": False, "refout": False, "xorout": 0xFFFF, "check": 0xA819, "desc": "PROFIBUS industrial fieldbus"},
    "crc16-riello":         {"width": 16, "poly": 0x1021, "init": 0xB2AA, "refin": True,  "refout": True,  "xorout": 0x0000, "check": 0x63D0, "desc": "Riello UPS dialog protocol"},
    "crc16-spi-fujitsu":    {"width": 16, "poly": 0x1021, "init": 0x1D0F, "refin": False, "refout": False, "xorout": 0x0000, "check": 0xE5CC, "desc": "Fujitsu SPI bus, AUG-CCITT"},
    "crc16-t10-dif":        {"width": 16, "poly": 0x8BB7, "init": 0x0000, "refin": False, "refout": False, "xorout": 0x0000, "check": 0xD0DB, "desc": "SCSI T10 Data Integrity Field"},
    "crc16-teledisk":       {"width": 16, "poly": 0xA097, "init": 0x0000, "refin": False, "refout": False, "xorout": 0x0000, "check": 0x0FB3, "desc": "TeleDisk floppy disk archiver"},
    "crc16-tms37157":       {"width": 16, "poly": 0x1021, "init": 0x89EC, "refin": True,  "refout": True,  "xorout": 0x0000, "check": 0x26B1, "desc": "TI TMS37157 RFID transponder"},
    "crc16-umts":           {"width": 16, "poly": 0x8005, "init": 0x0000, "refin": False, "refout": False, "xorout": 0x0000, "check": 0xFEE8, "desc": "UMTS/WCDMA 3G (BUYPASS)"},
    "crc16-xmodem":         {"width": 16, "poly": 0x1021, "init": 0x0000, "refin": False, "refout": False, "xorout": 0x0000, "check": 0x31C3, "desc": "XMODEM, ZMODEM, ACORN, LTE"},
    # ---- CRC-32 (12 algorithms) ----
    "crc32":            {"width": 32, "poly": 0x04C11DB7, "init": 0xFFFFFFFF, "refin": True,  "refout": True,  "xorout": 0xFFFFFFFF, "check": 0xCBF43926, "desc": "ISO 3309, ITU-T V.42, Ethernet, PKZIP, PNG"},
    "crc32-aixm":       {"width": 32, "poly": 0x814141AB, "init": 0x00000000, "refin": False, "refout": False, "xorout": 0x00000000, "check": 0x3010BF7F, "desc": "AIXM (Aeronautical Information Exchange)"},
    "crc32-autosar":    {"width": 32, "poly": 0xF4ACFB13, "init": 0xFFFFFFFF, "refin": True,  "refout": True,  "xorout": 0xFFFFFFFF, "check": 0x1697D06A, "desc": "AUTOSAR automotive E2E Profile 4"},
    "crc32-base91-d":   {"width": 32, "poly": 0xA833982B, "init": 0xFFFFFFFF, "refin": True,  "refout": True,  "xorout": 0xFFFFFFFF, "check": 0x87315576, "desc": "base91 encoding (CRC-32D)"},
    "crc32-bzip2":      {"width": 32, "poly": 0x04C11DB7, "init": 0xFFFFFFFF, "refin": False, "refout": False, "xorout": 0xFFFFFFFF, "check": 0xFC891918, "desc": "bzip2 file compression, AAL5"},
    "crc32-cd-rom-edc": {"width": 32, "poly": 0x8001801B, "init": 0x00000000, "refin": True,  "refout": True,  "xorout": 0x00000000, "check": 0x6EC2EDC4, "desc": "CD-ROM Error Detection Code"},
    "crc32-cksum":      {"width": 32, "poly": 0x04C11DB7, "init": 0x00000000, "refin": False, "refout": False, "xorout": 0xFFFFFFFF, "check": 0x765E7680, "desc": "POSIX cksum command"},
    "crc32-iscsi":      {"width": 32, "poly": 0x1EDC6F41, "init": 0xFFFFFFFF, "refin": True,  "refout": True,  "xorout": 0xFFFFFFFF, "check": 0xE3069283, "desc": "iSCSI, SCTP, Castagnoli (CRC-32C)"},
    "crc32-jamcrc":     {"width": 32, "poly": 0x04C11DB7, "init": 0xFFFFFFFF, "refin": True,  "refout": True,  "xorout": 0x00000000, "check": 0x340BC6D9, "desc": "Altera Jam STAPL programming language"},
    "crc32-mef":        {"width": 32, "poly": 0x741B8CD7, "init": 0xFFFFFFFF, "refin": True,  "refout": True,  "xorout": 0x00000000, "check": 0xD2C22F51, "desc": "Metro Ethernet Forum (MEF)"},
    "crc32-mpeg-2":     {"width": 32, "poly": 0x04C11DB7, "init": 0xFFFFFFFF, "refin": False, "refout": False, "xorout": 0x00000000, "check": 0x0376E6E7, "desc": "MPEG-2 transport stream"},
    "crc32-xfer":       {"width": 32, "poly": 0x000000AF, "init": 0x00000000, "refin": False, "refout": False, "xorout": 0x00000000, "check": 0xBD0BE338, "desc": "XFER file transfer protocol"},
}

# Backward-compatible aliases for old short names
CRC_CATALOGUE["crc16m"] = CRC_CATALOGUE["crc16-modbus"]
CRC_CATALOGUE["crc16x"] = CRC_CATALOGUE["crc16-xmodem"]


def _make_crc_compute(entry: dict) -> Callable[[bytes], int]:
    """Create a compute closure for a catalogue entry.

    Args:
        entry: Catalogue dict with width, poly, init, refin, refout, xorout.

    Returns:
        Function ``(data: bytes) -> int``.
    """
    w, p, i, ri, ro, xo = (
        entry["width"], entry["poly"], entry["init"],
        entry["refin"], entry["refout"], entry["xorout"],
    )
    return lambda data: _generic_crc(data, w, p, i, ri, ro, xo)


# ---------------------------------------------------------------------------
# CRC plugin discovery
# ---------------------------------------------------------------------------


@dataclass
class CrcAlgorithm:
    """Loaded CRC algorithm plugin.

    Attributes:
        name: Algorithm identifier (e.g. ``"crc16m"``).
        width: CRC width in bytes (1, 2, or 4).
        compute: Function that computes the CRC value.
    """

    name: str
    width: int
    compute: Callable[[bytes], int]


def builtins_crc_dir() -> Path:
    """Return the path to the built-in CRC plugin directory."""
    return Path(__file__).parent / "builtins" / "crc"


def load_crc_plugins(
    *dirs: Path,
) -> dict[str, CrcAlgorithm]:
    """Discover and load CRC algorithm plugins from directories.

    Each .py file must define ``NAME`` (str), ``WIDTH`` (int),
    and ``compute(data: bytes) -> int``.

    Args:
        *dirs: Directories to scan. Later entries override earlier.

    Returns:
        Dict of algorithm name -> CrcAlgorithm.
    """
    algorithms: dict[str, CrcAlgorithm] = {}
    for folder in dirs:
        if not folder.is_dir():
            continue
        for py_file in sorted(folder.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            try:
                module_name = f"termapy_crc_{py_file.stem}"
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)

                name = getattr(mod, "NAME", None)
                width = getattr(mod, "WIDTH", None)
                compute_fn = getattr(mod, "compute", None)
                if (
                    isinstance(name, str)
                    and isinstance(width, int)
                    and callable(compute_fn)
                ):
                    algorithms[name] = CrcAlgorithm(
                        name=name, width=width, compute=compute_fn
                    )
            except Exception as e:
                print(
                    f"termapy: failed to load CRC plugin {py_file.name}: {e}",
                    file=sys.stderr,
                )
    return algorithms


# Module-level CRC registry - populated on first use
_crc_registry: dict[str, CrcAlgorithm] | None = None


def get_crc_registry() -> dict[str, CrcAlgorithm]:
    """Get the CRC algorithm registry, loading catalogue + plugins on first call.

    Build order: catalogue entries first, then plugin files. Plugins override
    catalogue entries of the same name.

    Returns:
        Dict of algorithm name -> CrcAlgorithm.
    """
    global _crc_registry
    if _crc_registry is None:
        # 1. Build from catalogue
        registry: dict[str, CrcAlgorithm] = {}
        for name, entry in CRC_CATALOGUE.items():
            width_bytes = (entry["width"] + 7) // 8
            registry[name] = CrcAlgorithm(
                name=name, width=width_bytes, compute=_make_crc_compute(entry),
            )
        # 2. Overlay plugins (sum8, sum16, user-custom)
        registry.update(load_crc_plugins(builtins_crc_dir()))
        _crc_registry = registry
    return _crc_registry


def reset_crc_registry() -> None:
    """Reset the CRC registry, forcing reload on next access."""
    global _crc_registry
    _crc_registry = None
