"""USB manufacturer descriptor -> short display alias.

Folds the free-form USB manufacturer string a device reports ("Silicon
Labs" vs "Silicon Laboratories", "Microsoft" vs "Microsoft
Corporation") to a compact alias suitable for a narrow UI column.

Each entry is its own dataclass so adding wider display tiers later
(``mfg16``, ``mfg_full``) is purely additive (new field with default).

Scope note: this module is USB-generic.  The alias table could
legitimately grow to cover non-serial USB devices (keyboards, drives,
audio interfaces).  The serial-specific ``USB_SERIAL_CHIPS`` table
lives separately in the sibling ``chips`` module.

Design rules:

    - Display-only.  ``mfg()`` never touches identity data.  Callers
      that need the raw manufacturer string for comparison, storage,
      or scripting use pyserial's ``ListPortInfo.manufacturer`` or
      termapy's ``ChipFacts.manufacturer`` directly.

    - Don't merge distinct brand identities.  A chip that reports
      ``Cypress`` is shown as ``Cypress``, not ``Infineon``; a chip
      that reports ``Atmel`` is shown as ``Atmel``, not ``Microchip``.
      Respecting what the hardware reports is more useful than
      deduplicating corporate history.

    - Unknown values pass through unchanged.  Callers that need to
      truncate do so themselves; the library never silently invents a
      short form.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VendorAlias:
    """One entry in the manufacturer alias table.

    ``prefix`` is a lowercase substring tested against the start of
    the raw USB descriptor (case-insensitively, via ``startswith``).
    One entry per distinct reported root name is enough:
    ``prefix="microsoft"`` matches ``"Microsoft"``, ``"Microsoft
    Corporation"``, etc.

    ``mfg`` is the short alias returned for display in narrow
    columns -- the longest entries today (``Microchip``, ``Espressif``,
    ``Parallels``) are 9 characters.
    """

    prefix: str
    mfg: str


# Case-insensitive ``startswith`` lookup table.  Order matters only
# when two prefixes overlap; none of the current entries do.
MANUFACTURER_ALIASES: list[VendorAlias] = [
    VendorAlias(prefix="ftdi",                  mfg="FTDI"),
    VendorAlias(prefix="future technology",     mfg="FTDI"),
    VendorAlias(prefix="microsoft",             mfg="MSFT"),
    VendorAlias(prefix="silicon lab",           mfg="SiLabs"),
    VendorAlias(prefix="prolific",              mfg="Prolific"),
    VendorAlias(prefix="wch",                   mfg="WCH"),
    VendorAlias(prefix="qinheng",               mfg="WCH"),
    VendorAlias(prefix="arduino",               mfg="Arduino"),
    VendorAlias(prefix="stmicro",               mfg="STM"),
    # Some boards/clones report the product name as the manufacturer.
    VendorAlias(prefix="st-link",               mfg="STM"),
    VendorAlias(prefix="atmel",                 mfg="Atmel"),
    VendorAlias(prefix="microchip",             mfg="Microchip"),
    VendorAlias(prefix="nxp",                   mfg="NXP"),
    VendorAlias(prefix="nordic",                mfg="Nordic"),
    VendorAlias(prefix="espressif",             mfg="Espressif"),
    VendorAlias(prefix="raspberry pi",          mfg="RaspPi"),
    VendorAlias(prefix="teensy",                mfg="Teensy"),
    VendorAlias(prefix="pjrc",                  mfg="Teensy"),
    VendorAlias(prefix="adafruit",              mfg="Adafruit"),
    VendorAlias(prefix="sparkfun",              mfg="SparkFun"),
    VendorAlias(prefix="cypress",               mfg="Cypress"),
    VendorAlias(prefix="infineon",              mfg="Infineon"),
    VendorAlias(prefix="texas instruments",     mfg="TI"),
    VendorAlias(prefix="segger",                mfg="Segger"),
    VendorAlias(prefix="vmware",                mfg="VMware"),
    VendorAlias(prefix="parallels",             mfg="Parallels"),
    # Windows native / built-in COM ports report this literal string.
    VendorAlias(prefix="(standard port types)", mfg=""),
]


def mfg(raw: str | None) -> str:
    """Return a short display alias for a USB manufacturer descriptor.

    Case-insensitive ``startswith`` match against
    ``MANUFACTURER_ALIASES``.  Unknown vendors pass through unchanged.
    ``None`` / empty input returns ``""``.

    Never modifies or returns identity data -- this is display-only.
    """
    if not raw:
        return ""
    needle = raw.strip().lower()
    for entry in MANUFACTURER_ALIASES:
        if needle.startswith(entry.prefix):
            return entry.mfg
    return raw
