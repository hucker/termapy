"""USB Vendor ID -> silicon-vendor name lookup.

A small curated table that translates a USB-IF-assigned VID to the
silicon vendor that owns it.  Complements ``usb_serial_chips.py``
(which is keyed on (VID, PID) pairs and gives chip model + speed +
max baud) by answering "even if we don't recognize the specific
chip, who made the silicon?"

Distinct from ``usb_mfg.py``:

  - ``usb_mfg.MANUFACTURER_ALIASES`` cleans up the *string the device
    descriptor or driver INF reports* (e.g. "Future Technology
    Devices, Inc." -> "FTDI").  String input -> short display alias.
  - ``USB_VENDORS`` here is a pure VID lookup -- the descriptor / INF
    string is irrelevant.  VID input -> canonical vendor name.

These two answer different questions and can disagree (e.g. a
Microchip USB-serial chip running on Microsoft's generic ``usbser.sys``
reports manufacturer = "Microsoft" via the driver INF, but VID 0x04D8
belongs to Microchip).  Termapy exposes both so the user can see the
mismatch.

For narrow-column display, callers feed the canonical vendor name
through ``usb_mfg.mfg()`` -- entries here use canonical names so the
existing alias table can collapse them ("Silicon Labs" -> "SiLabs",
"Future Technology" -> "FTDI", etc.).

Scope: USB-IF-assigned VIDs commonly seen on embedded / USB-serial
hardware.  The full ``usb.ids`` from linux-usb.org has ~30,000
entries; this table aims for "the 50 vendors most relevant to
serial-port debugging."  Grow as users encounter unknowns.
"""

from __future__ import annotations


# Canonical vendor names per USB-IF assignment.  Keep in sync with
# https://www.usb.org/developers/usbif-resources/sublicensed-vendor-id  -- and
# www.linux-usb.org/usb.ids when adding entries.
USB_VENDORS: dict[int, str] = {
    # USB-serial bridge / dual-mode silicon
    0x0403: "FTDI",
    0x10C4: "Silicon Labs",
    0x1A86: "WCH",
    0x067B: "Prolific",
    0x04D8: "Microchip",
    0x03EB: "Atmel",                # now Microchip; descriptor-honest
    0x04B4: "Cypress",
    0x058B: "Infineon",

    # Microcontroller / SoC vendors that present native USB serial
    0x0451: "Texas Instruments",
    0x0483: "STMicro",
    0x1FC9: "NXP",
    0x1915: "Nordic",
    0x303A: "Espressif",
    0x16C0: "Teensy",               # PJRC, Teensy series
    0x2E8A: "Raspberry Pi",
    0x239A: "Adafruit",
    0x1B4F: "SparkFun",
    0x2341: "Arduino",
    0x2A03: "Arduino",              # alternate Arduino VID
    0x0D28: "ARM mbed",

    # Debug / programmer hardware
    0x1366: "Segger",
    0x0925: "Lakeview Research",    # used by Saleae and some hobby kit
    0x1B3F: "Generalplus",          # CDC ACM clones
    0x0BDA: "Realtek",

    # Driver INFs likely to appear as "manufacturer" via generic OS drivers.
    # Including these lets us still surface a vendor for devices using
    # vendor-neutral hardware.
    0x045E: "Microsoft",
    0x05AC: "Apple",
    0x046D: "Logitech",
}


def vendor_for(vid: int | None) -> str | None:
    """Return the canonical silicon-vendor name for a VID, or None.

    Args:
        vid: USB Vendor ID as an integer, or None.

    Returns:
        Canonical vendor name (e.g. ``"Silicon Labs"``) when the VID
        is in ``USB_VENDORS``.  None for unknown VIDs or ``None`` input.
        Callers that want a column-friendly short form should pass the
        result through ``usb_mfg.mfg()``.
    """
    if vid is None:
        return None
    return USB_VENDORS.get(vid)
