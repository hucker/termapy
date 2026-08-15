"""USB lookup tables for serial-port debugging tools.

A small, self-contained package of curated data + lookup helpers that
sit alongside (not inside) pyserial.  Given the values pyserial's
``ListPortInfo`` already exposes (``vid``, ``pid``, ``manufacturer``),
this package answers three questions:

  - **Manufacturer string -> short display alias** for narrow UI columns
    (``Future Technology Devices, Inc.`` -> ``FTDI``).  See
    :func:`mfg` and :data:`MANUFACTURER_ALIASES`.
  - **VID -> canonical vendor name** with a curated short-form table
    backed by an auto-generated USB-IF fallback (~3,400 entries).  See
    :func:`vendor_for` and :data:`USB_VENDORS`.
  - **(VID, PID) -> chip model + USB speed + datasheet max baud**, a
    hand-curated table of common USB-serial bridges (FTDI, SiLabs,
    WCH, Prolific, ...) and USB-native serial devices (STM32, ESP32,
    Arduino, Teensy, Pico).  See :func:`chip` and :data:`USB_SERIAL_CHIPS`.

These three tables intentionally answer different questions:

  - :data:`MANUFACTURER_ALIASES` works on the **descriptor / driver INF
    string** the OS reports.  Display-only.
  - :data:`USB_VENDORS` works on the **VID integer** from the USB
    enumeration; independent of any OS reporting.  Canonical names.

The two can disagree -- a Microchip USB-serial chip running on
Microsoft's generic ``usbser.sys`` reports manufacturer ``"Microsoft"``
via the driver INF, but VID ``0x04D8`` belongs to Microchip.  Surface
both so the user can see the mismatch.

Library use (standalone, no termapy-engine deps)::

    from termapy.usb import chip, vendor_for, mfg

    info = chip(vid=0x10C4, pid=0xEA60)
    if info is not None:
        print(f"{info.model}  ({info.speed}-speed, max {info.max_baud} baud)")
    else:
        # Unknown VID:PID -- fall back to "who made the silicon"?
        vendor = vendor_for(vid=0x10C4)
        # And clean up the OS-reported manufacturer string for display.
        short = mfg("Silicon Laboratories Inc.")

Files in this package:

  - ``aliases.py``        -- ``mfg()`` + ``MANUFACTURER_ALIASES``
  - ``chips.py``          -- ``chip()`` + ``USB_SERIAL_CHIPS`` + ``ChipInfo``
  - ``vendors.py``        -- ``vendor_for()`` + ``USB_VENDORS``
  - ``_vendors_full.py``  -- auto-generated ``USB_VENDORS_FULL`` from
                             upstream usb.ids; regenerate via
                             ``scripts/refresh_usb_ids.py``

Dependencies outside the package boundary: none.  This subpackage is
positioned as a viable standalone PyPI release (``pyserial-vendors``
or similar) -- termapy uses it directly today; nothing here imports
termapy-engine, MCP, or Textual code.
"""

from __future__ import annotations

from termapy.usb.aliases import (
    MANUFACTURER_ALIASES,
    VendorAlias,
    mfg,
)
from termapy.usb.chips import (
    USB_SERIAL_CHIPS,
    ChipInfo,
    chip,
)
from termapy.usb.vendors import (
    USB_VENDORS,
    vendor_for,
)

__all__ = [
    # aliases
    "MANUFACTURER_ALIASES",
    "VendorAlias",
    "mfg",
    # chips
    "USB_SERIAL_CHIPS",
    "ChipInfo",
    "chip",
    # vendors
    "USB_VENDORS",
    "vendor_for",
]
