"""USB-serial chip identification by (vendor_id, product_id).

Curated lookup table of USB-to-serial bridge chips and USB-native
serial devices.  For a given VID:PID, returns the chip's full model
name, USB speed class, and datasheet-maximum baud rate.

Each entry is a ``ChipInfo`` dataclass, so adding new fields (e.g.
``max_buffer_size``, ``driver_required``) is purely additive.

Scope note: this table is USB-serial-specific on purpose.  A chip is
included when it shows up as a COM port on some OS (FTDI bridges,
USB-native CDC devices, USB-JTAG with a VCP endpoint, etc.).
Non-serial USB devices belong elsewhere.

The companion module ``aliases`` holds the manufacturer-string alias
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


# Each vendor block names the primary source its PIDs and part names were
# checked against on 2026-10-25: the vendor's own allocation list or driver
# source, or usb.ids where the vendor keeps one there.  A block marked "not
# verified" is curated without one.  Until then the table named parts from
# memory beside correct PIDs, and about a quarter of its names were a
# neighboring part's (review 2026-07-03, R2607-04, reopened 2026-10-25).
# ``speed`` and ``max_baud`` travel with the part name they were written for.
USB_SERIAL_CHIPS: dict[tuple[int, int], ChipInfo] = {
    # FTDI (vid 0x0403) -- Linux drivers/usb/serial/ftdi_sio_ids.h
    (0x0403, 0x6001): ChipInfo("FTDI FT232R / FT245R", "full", 3_000_000),
    (0x0403, 0x6006): ChipInfo("FTDI FT232R / FT245R (alternate PID)", "full", 3_000_000),
    (0x0403, 0x6010): ChipInfo("FTDI FT2232C/D/H", "high", 12_000_000),
    (0x0403, 0x6011): ChipInfo("FTDI FT4232H", "high", 12_000_000),
    (0x0403, 0x6014): ChipInfo("FTDI FT232H", "high", 12_000_000),
    (0x0403, 0x6015): ChipInfo("FTDI FT230X / FT231X / FT234XD", "full", 3_000_000),
    (0x0403, 0x6040): ChipInfo("FTDI FT2233HP", "high", 12_000_000),
    (0x0403, 0x6041): ChipInfo("FTDI FT4233HP", "high", 12_000_000),
    (0x0403, 0x6042): ChipInfo("FTDI FT2232HP", "high", 12_000_000),
    (0x0403, 0x6043): ChipInfo("FTDI FT4232HP", "high", 12_000_000),
    (0x0403, 0x6044): ChipInfo("FTDI FT233HP", "high", 12_000_000),
    (0x0403, 0x6045): ChipInfo("FTDI FT232HP", "high", 12_000_000),
    (0x0403, 0x6048): ChipInfo("FTDI FT4232HA", "high", 12_000_000),
    # Silicon Labs (vid 0x10C4) -- Linux drivers/usb/serial/cp210x.c, usb.ids
    (0x10C4, 0xEA60): ChipInfo("Silicon Labs CP2102 / CP2102N", "full", 921_600),
    (0x10C4, 0xEA63): ChipInfo(
        "Silicon Labs CP2101-4 / CP2102N (Windows Update PID)", "full", 921_600
    ),
    (0x10C4, 0xEA70): ChipInfo("Silicon Labs CP2105 (dual UART)", "full", 921_600),
    (0x10C4, 0xEA71): ChipInfo("Silicon Labs CP2108 (quad UART)", "full", 921_600),
    (0x10C4, 0xEA7A): ChipInfo("Silicon Labs CP2105 (Windows Update PID)", "full", 921_600),
    (0x10C4, 0xEA7B): ChipInfo("Silicon Labs CP2108 (Windows Update PID)", "full", 921_600),
    (0x10C4, 0xEA80): ChipInfo("Silicon Labs CP2110 (HID)", "full", 1_000_000),
    (0x10C4, 0xEA90): ChipInfo("Silicon Labs CP2112 (HID-I2C)", "full", 0),  # not UART
    # WCH (vid 0x1A86) - cheap chips on most $5 dev boards -- WCH's own Linux
    # driver (WCHSoftGroup/ch343ser_linux) for 55Dx, usb.ids for the rest
    (0x1A86, 0x5512): ChipInfo("WCH CH341 (parallel mode)", "full", 2_000_000),
    (0x1A86, 0x5523): ChipInfo("WCH CH341 (serial mode)", "full", 2_000_000),
    (0x1A86, 0x7522): ChipInfo("WCH CH340", "full", 2_000_000),
    (0x1A86, 0x7523): ChipInfo("WCH CH340", "full", 2_000_000),
    (0x1A86, 0x55D2): ChipInfo("WCH CH342 (dual UART)", "full", 6_000_000),
    (0x1A86, 0x55D3): ChipInfo("WCH CH343", "full", 6_000_000),
    (0x1A86, 0x55D4): ChipInfo("WCH CH9102", "full", 4_000_000),
    (0x1A86, 0x55D5): ChipInfo("WCH CH344 (quad UART)", "high", 6_000_000),
    (0x1A86, 0x55D7): ChipInfo("WCH CH9103 (dual UART)", "full", 4_000_000),
    (0x1A86, 0x55D8): ChipInfo("WCH CH9101", "full", 4_000_000),
    (0x1A86, 0x55DA): ChipInfo("WCH CH347T (mode 0)", "high", 9_000_000),
    (0x1A86, 0x55DB): ChipInfo("WCH CH347T (mode 1)", "high", 9_000_000),
    (0x1A86, 0x55DD): ChipInfo("WCH CH347T (mode 3)", "high", 9_000_000),
    # Prolific (vid 0x067B) -- Linux drivers/usb/serial/pl2303.h
    (0x067B, 0x2303): ChipInfo("Prolific PL2303 (HX/TA/XA/EA)", "full", 1_500_000),
    (0x067B, 0x23A3): ChipInfo("Prolific PL2303GC", "full", 12_000_000),
    (0x067B, 0x23B3): ChipInfo("Prolific PL2303GB", "full", 12_000_000),
    (0x067B, 0x23C3): ChipInfo("Prolific PL2303GT", "full", 12_000_000),
    (0x067B, 0x23D3): ChipInfo("Prolific PL2303GL", "full", 12_000_000),
    (0x067B, 0x23E3): ChipInfo("Prolific PL2303GE", "full", 12_000_000),
    (0x067B, 0x23F3): ChipInfo("Prolific PL2303GS", "full", 12_000_000),
    # Microchip (vid 0x04D8) -- Linux drivers/hid/hid-ids.h
    (0x04D8, 0x00DD): ChipInfo("Microchip MCP2221 (UART+I2C)", "full", 460_800),
    (0x04D8, 0x00DF): ChipInfo("Microchip MCP2200", "full", 1_000_000),
    # 0x9036 is the MPLAB PICkit 5 debugger, a composite device: Microchip's
    # WinUSB driver INF (mchpwinusbdevice.inf 3.1.0.0, 2025-06-11) binds its
    # interface 0 as "MPLAB PICkit 5 In-Circuit Debugger", and interface 1
    # is the CDC serial port its user's guide calls VCOM.  High-Speed per
    # that guide (DS-50003525A p. 80: "USB speeds of 480 Mbit/s").
    # max_baud not verified: the guide states no VCOM limit.
    (0x04D8, 0x9036): ChipInfo("Microchip MPLAB PICkit 5 (VCOM)", "high", 1_000_000),
    # STMicroelectronics (vid 0x0483) -- usb.ids; 374F "Mini" not verified
    (0x0483, 0x5740): ChipInfo("STM32 Virtual COM Port", "full", 2_000_000),
    (0x0483, 0x374B): ChipInfo("ST-Link V2-1 (with VCP)", "full", 2_000_000),
    (0x0483, 0x374E): ChipInfo("ST-Link V3", "high", 8_000_000),
    (0x0483, 0x374F): ChipInfo("ST-Link V3 Mini", "high", 8_000_000),
    # Espressif (vid 0x303A) - ESP32 native USB -- esp-pylib constants (1001),
    # esptool (ROM download mode enumerates with PID = the chip's image ID:
    # S2 is 2, S3 is 9), esp-usb's esp_tinyusb descriptors (4001 = 0x4000 | CDC)
    (0x303A, 0x0002): ChipInfo("Espressif ESP32-S2 ROM download (USB-OTG)", "full", 2_000_000),
    (0x303A, 0x0009): ChipInfo("Espressif ESP32-S3 ROM download (USB-OTG)", "full", 2_000_000),
    (0x303A, 0x1001): ChipInfo("Espressif USB-Serial/JTAG (ESP32-C3/S3/C6/H2)", "full", 2_000_000),
    (0x303A, 0x4001): ChipInfo("Espressif TinyUSB CDC (ESP-IDF default)", "full", 2_000_000),
    # Arduino (vid 0x2341) -- usb.ids; 8037, 804E, 8054, 8057 not verified
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
    # SparkFun (vid 0x1B4F) -- not verified
    (0x1B4F, 0x9203): ChipInfo("SparkFun Pro Micro 3.3V", "full", 2_000_000),
    (0x1B4F, 0x9206): ChipInfo("SparkFun Pro Micro 5V", "full", 2_000_000),
    (0x1B4F, 0x9207): ChipInfo("SparkFun Pro Micro (bootloader)", "full", 2_000_000),
    # Adafruit (vid 0x239A) -- not verified
    (0x239A, 0x8014): ChipInfo("Adafruit Metro M0 Express", "full", 2_000_000),
    (0x239A, 0x8019): ChipInfo("Adafruit Feather M0 Express", "full", 2_000_000),
    (0x239A, 0x800B): ChipInfo("Adafruit Metro M4 / Feather M4", "full", 2_000_000),
    (0x239A, 0x80CB): ChipInfo("Adafruit Trinket M0", "full", 2_000_000),
    (0x239A, 0x80F3): ChipInfo("Adafruit QT Py RP2040", "full", 2_000_000),
    # PJRC Teensy (vid 0x16C0) -- PJRC's core, teensy4/usb_desc.h, and usb.ids.
    # A PID names the USB type the sketch was built with, the same on every
    # Teensy model, so speed follows the dominant in-field part (Teensy 4.x,
    # High-Speed), as the FT2232C/D/H row does.
    (0x16C0, 0x0478): ChipInfo("Teensy bootloader (HalfKay)", "full", 0),
    (0x16C0, 0x0483): ChipInfo("Teensy USB Serial (any model)", "high", 12_000_000),
    (0x16C0, 0x0486): ChipInfo("Teensy RawHID", "high", 0),
    (0x16C0, 0x0487): ChipInfo("Teensy Serial+Keyboard+Mouse+Joystick", "high", 12_000_000),
    (0x16C0, 0x0489): ChipInfo("Teensy Serial+MIDI", "high", 12_000_000),
    # Raspberry Pi (vid 0x2E8A) -- Raspberry Pi's PID list, github.com/raspberrypi/usb-pid
    (0x2E8A, 0x0003): ChipInfo("Raspberry Pi RP2040 bootloader (RPI-RP2)", "full", 0),
    (0x2E8A, 0x0004): ChipInfo("Raspberry Pi PicoProbe (obsolete)", "full", 2_000_000),
    (0x2E8A, 0x0005): ChipInfo("Raspberry Pi Pico MicroPython firmware", "full", 2_000_000),
    (0x2E8A, 0x0009): ChipInfo("Raspberry Pi Pico SDK USB serial", "full", 2_000_000),
    (0x2E8A, 0x000A): ChipInfo("Raspberry Pi Pico SDK USB serial (RP2040)", "full", 2_000_000),
    (0x2E8A, 0x000B): ChipInfo("Raspberry Pi Pico CircuitPython firmware", "full", 2_000_000),
    (0x2E8A, 0x000C): ChipInfo("Raspberry Pi Debug Probe", "full", 2_000_000),
    (0x2E8A, 0x000F): ChipInfo("Raspberry Pi RP2350 bootloader", "full", 0),
}


def chip(vid: int, pid: int) -> ChipInfo | None:
    """Return ``ChipInfo`` for a VID:PID pair, or ``None`` if unknown.

    Thin wrapper over the ``USB_SERIAL_CHIPS`` dict for callers that
    prefer a function boundary.  Direct dict access is equally valid.
    """
    return USB_SERIAL_CHIPS.get((vid, pid))
