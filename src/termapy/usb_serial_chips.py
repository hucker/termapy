"""USB-serial chip identification by (vendor_id, product_id).

Curated lookup table of USB-to-serial bridge chips and USB-native
serial devices.  For a given VID:PID, returns the chip's full model
name, USB speed class, and datasheet-maximum baud rate.

Internal to termapy today.  Designed so the data set can be extracted
into a standalone pyserial-adjacent library with minimal churn --
each entry is a ``ChipInfo`` dataclass, so adding new fields (e.g.
``max_buffer_size``, ``driver_required``) is purely additive.

Scope note: this table is USB-serial-specific on purpose.  A chip is
included when it shows up as a COM port on some OS (FTDI bridges,
USB-native CDC devices, USB-JTAG with a VCP endpoint, etc.).
Non-serial USB devices belong elsewhere.

The companion module ``usb_mfg`` holds the manufacturer-string alias
table and ``mfg()`` display helper.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChipInfo:
    """Everything we know about one USB-serial chip from its VID:PID.

    ``model`` is the full chip/product name suitable for a wide column
    or a detailed dump.  It's not a display-budget alias -- long names
    like ``"FTDI FT230X / FT231X / FT234XD"`` are intentional, since
    the same chip ships under multiple part numbers.  Callers that
    need to fit it into a narrow column truncate themselves.

    ``speed`` is the USB enumeration class: ``"full"`` (12 Mbit/s, 1 ms
    min latency) or ``"high"`` (480 Mbit/s, 125 us).  Full-speed chips
    cannot achieve sub-millisecond round-trip latency regardless of
    host or driver tuning.

    ``max_baud`` is the highest baud rate the chip supports per its
    datasheet.  ``0`` means "not a UART" (HID-only, bootloader, etc.).
    """

    model: str
    speed: str
    max_baud: int


# Source: chip datasheets and FTDI/Silicon Labs/WCH product pages.
USB_SERIAL_CHIPS: dict[tuple[int, int], ChipInfo] = {
    # FTDI (vid 0x0403)
    (0x0403, 0x6001): ChipInfo("FTDI FT232R / FT245R", "full", 3_000_000),
    (0x0403, 0x6006): ChipInfo("FTDI FT8U100AX (legacy)", "full", 115_200),
    (0x0403, 0x6010): ChipInfo("FTDI FT2232C/D/H", "high", 12_000_000),
    (0x0403, 0x6011): ChipInfo("FTDI FT4232H", "high", 12_000_000),
    (0x0403, 0x6014): ChipInfo("FTDI FT232H", "high", 12_000_000),
    (0x0403, 0x6015): ChipInfo("FTDI FT230X / FT231X / FT234XD", "full", 3_000_000),
    (0x0403, 0x6040): ChipInfo("FTDI FT4233HP", "high", 12_000_000),
    (0x0403, 0x6041): ChipInfo("FTDI FT4232HP", "high", 12_000_000),
    (0x0403, 0x6042): ChipInfo("FTDI FT2232HP", "high", 12_000_000),
    (0x0403, 0x6043): ChipInfo("FTDI FT232HP", "high", 12_000_000),
    # Silicon Labs (vid 0x10C4)
    (0x10C4, 0xEA60): ChipInfo("Silicon Labs CP2102 / CP2102N", "full", 921_600),
    (0x10C4, 0xEA63): ChipInfo("Silicon Labs CP210x (variant)", "full", 921_600),
    (0x10C4, 0xEA70): ChipInfo("Silicon Labs CP2105 (dual UART)", "full", 921_600),
    (0x10C4, 0xEA71): ChipInfo("Silicon Labs CP2108 (quad UART)", "full", 921_600),
    (0x10C4, 0xEA7A): ChipInfo("Silicon Labs CP2102N-QFN28", "full", 3_000_000),
    (0x10C4, 0xEA7B): ChipInfo("Silicon Labs CP2102N-QFN24", "full", 3_000_000),
    (0x10C4, 0xEA80): ChipInfo("Silicon Labs CP2110 (HID)", "full", 1_000_000),
    (0x10C4, 0xEA90): ChipInfo("Silicon Labs CP2112 (HID-I2C)", "full", 0),  # not UART
    # WCH (vid 0x1A86) - cheap chips on most $5 dev boards
    (0x1A86, 0x5512): ChipInfo("WCH CH341 (parallel mode)", "full", 2_000_000),
    (0x1A86, 0x5523): ChipInfo("WCH CH341 (serial mode)", "full", 2_000_000),
    (0x1A86, 0x7522): ChipInfo("WCH CH340", "full", 2_000_000),
    (0x1A86, 0x7523): ChipInfo("WCH CH340", "full", 2_000_000),
    (0x1A86, 0x55D2): ChipInfo("WCH CH9101", "full", 4_000_000),
    (0x1A86, 0x55D3): ChipInfo("WCH CH343", "full", 6_000_000),
    (0x1A86, 0x55D4): ChipInfo("WCH CH9102", "full", 4_000_000),
    (0x1A86, 0x55D5): ChipInfo("WCH CH9103 (dual UART)", "full", 4_000_000),
    (0x1A86, 0x55D7): ChipInfo("WCH CH344 (quad UART)", "high", 6_000_000),
    (0x1A86, 0x55D8): ChipInfo("WCH CH347 (USB-MPSSE)", "high", 9_000_000),
    # Prolific (vid 0x067B)
    (0x067B, 0x2303): ChipInfo("Prolific PL2303 (HX/TA/XA/EA)", "full", 1_500_000),
    (0x067B, 0x23A3): ChipInfo("Prolific PL2303GC", "full", 12_000_000),
    (0x067B, 0x23B3): ChipInfo("Prolific PL2303GL", "full", 12_000_000),
    (0x067B, 0x23C3): ChipInfo("Prolific PL2303GT", "full", 12_000_000),
    (0x067B, 0x23D3): ChipInfo("Prolific PL2303GS", "full", 12_000_000),
    (0x067B, 0x23E3): ChipInfo("Prolific PL2303GE", "full", 12_000_000),
    (0x067B, 0x23F3): ChipInfo("Prolific PL2303GR", "full", 12_000_000),
    # Microchip (vid 0x04D8)
    (0x04D8, 0x00DD): ChipInfo("Microchip MCP2200", "full", 1_000_000),
    (0x04D8, 0x00DF): ChipInfo("Microchip MCP2221 (UART+I2C)", "full", 460_800),
    # 0x9036 is a composite (MI_xx) Microchip USB-CDC device -- not one
    # of the dedicated bridge ICs.  Most likely a dev board / instrument
    # using a PIC or SAMD MCU's native USB stack to expose CDC.  Keep
    # the entry generic until the specific product is identified.
    (0x04D8, 0x9036): ChipInfo("Microchip USB-CDC device", "full", 1_000_000),
    # STMicroelectronics (vid 0x0483)
    (0x0483, 0x5740): ChipInfo("STM32 Virtual COM Port", "full", 2_000_000),
    (0x0483, 0x374B): ChipInfo("ST-Link V2 (with VCP)", "full", 2_000_000),
    (0x0483, 0x374E): ChipInfo("ST-Link V3", "high", 8_000_000),
    (0x0483, 0x374F): ChipInfo("ST-Link V3 Mini", "high", 8_000_000),
    # Espressif (vid 0x303A) - ESP32 native USB
    (0x303A, 0x0002): ChipInfo("Espressif ESP32-S2 JTAG/CDC", "full", 2_000_000),
    (0x303A, 0x1001): ChipInfo("Espressif ESP32-S2/S3 native CDC", "full", 2_000_000),
    (0x303A, 0x1002): ChipInfo("Espressif ESP32-S3 USB-JTAG", "full", 2_000_000),
    (0x303A, 0x4001): ChipInfo("Espressif ESP32-C3 USB-JTAG", "full", 2_000_000),
    # Arduino (vid 0x2341)
    (0x2341, 0x0001): ChipInfo("Arduino Uno (original)", "full", 115_200),
    (0x2341, 0x0043): ChipInfo("Arduino Uno R3 (ATmega16U2 native)", "full", 2_000_000),
    (0x2341, 0x0042): ChipInfo("Arduino Mega 2560 R3", "full", 2_000_000),
    (0x2341, 0x003B): ChipInfo("Arduino Serial Adapter", "full", 115_200),
    (0x2341, 0x003D): ChipInfo("Arduino Due (programming port)", "full", 2_000_000),
    (0x2341, 0x003E): ChipInfo("Arduino Due (native USB)", "full", 12_000_000),
    (0x2341, 0x8036): ChipInfo("Arduino Leonardo (ATmega32U4)", "full", 2_000_000),
    (0x2341, 0x8037): ChipInfo("Arduino Micro (ATmega32U4)", "full", 2_000_000),
    (0x2341, 0x804E): ChipInfo("Arduino MKR Zero", "full", 2_000_000),
    (0x2341, 0x8054): ChipInfo("Arduino MKR WiFi 1010", "full", 2_000_000),
    (0x2341, 0x8057): ChipInfo("Arduino Nano 33 IoT", "full", 2_000_000),
    # SparkFun (vid 0x1B4F)
    (0x1B4F, 0x9203): ChipInfo("SparkFun Pro Micro 3.3V", "full", 2_000_000),
    (0x1B4F, 0x9206): ChipInfo("SparkFun Pro Micro 5V", "full", 2_000_000),
    (0x1B4F, 0x9207): ChipInfo("SparkFun Pro Micro (bootloader)", "full", 2_000_000),
    # Adafruit (vid 0x239A)
    (0x239A, 0x8014): ChipInfo("Adafruit Metro M0 Express", "full", 2_000_000),
    (0x239A, 0x8019): ChipInfo("Adafruit Feather M0 Express", "full", 2_000_000),
    (0x239A, 0x800B): ChipInfo("Adafruit Metro M4 / Feather M4", "full", 2_000_000),
    (0x239A, 0x80CB): ChipInfo("Adafruit Trinket M0", "full", 2_000_000),
    (0x239A, 0x80F3): ChipInfo("Adafruit QT Py RP2040", "full", 2_000_000),
    # PJRC Teensy (vid 0x16C0)
    (0x16C0, 0x0476): ChipInfo("Teensy RawHID", "full", 0),
    (0x16C0, 0x0478): ChipInfo("Teensy bootloader (HalfKay)", "full", 0),
    (0x16C0, 0x0483): ChipInfo("Teensy 2.x (ATmega32U4 native USB)", "full", 2_000_000),
    (0x16C0, 0x0486): ChipInfo("Teensy Serial+Keyboard+Mouse+Joystick", "full", 2_000_000),
    (0x16C0, 0x0487): ChipInfo("Teensy HID only", "full", 0),
    (0x16C0, 0x0489): ChipInfo("Teensy 3.x / 4.x (ARM native USB)", "high", 12_000_000),
    # Raspberry Pi (vid 0x2E8A)
    (0x2E8A, 0x0003): ChipInfo("Raspberry Pi RP2040 bootloader (RPI-RP2)", "full", 0),
    (0x2E8A, 0x0005): ChipInfo("Raspberry Pi Debug Probe", "full", 2_000_000),
    (0x2E8A, 0x000A): ChipInfo("Raspberry Pi RP2040 (Pico) native USB", "full", 2_000_000),
    (0x2E8A, 0x000B): ChipInfo("Raspberry Pi Pico MicroPython", "full", 2_000_000),
    (0x2E8A, 0x000C): ChipInfo("Raspberry Pi Pico CircuitPython", "full", 2_000_000),
    (0x2E8A, 0x0009): ChipInfo("Raspberry Pi Pico W (default)", "full", 2_000_000),
}


def chip(vid: int, pid: int) -> ChipInfo | None:
    """Return ``ChipInfo`` for a VID:PID pair, or ``None`` if unknown.

    Thin wrapper over the ``USB_SERIAL_CHIPS`` dict for callers that
    prefer a function boundary.  Direct dict access is equally valid.
    """
    return USB_SERIAL_CHIPS.get((vid, pid))
