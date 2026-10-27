"""Tests for termapy.usb.chips -- VID:PID -> ChipInfo lookup."""

import pytest

from termapy.usb import USB_SERIAL_CHIPS, ChipInfo, chip

# Rows the 2026-10-25 audit corrected, each with the part its vendor's own
# list or driver gives the PID.  The table had the right PIDs and a
# neighboring part's name; these pin the names to the sources cited beside
# each vendor block in usb/chips.py.
SOURCED_NAMES: list[tuple[int, int, str]] = [
    (0x2E8A, 0x0005, "MicroPython"),  # raspberrypi/usb-pid
    (0x2E8A, 0x0009, "Pico SDK"),
    (0x2E8A, 0x000B, "CircuitPython"),
    (0x2E8A, 0x000C, "Debug Probe"),
    (0x2E8A, 0x000F, "RP2350 bootloader"),
    (0x04D8, 0x00DD, "MCP2221"),  # Linux hid-ids.h
    (0x04D8, 0x00DF, "MCP2200"),
    (0x067B, 0x23B3, "PL2303GB"),  # Linux pl2303.h
    (0x067B, 0x23D3, "PL2303GL"),
    (0x067B, 0x23F3, "PL2303GS"),
    (0x1A86, 0x55D2, "CH342"),  # WCH ch343ser_linux
    (0x1A86, 0x55D5, "CH344"),
    (0x1A86, 0x55D7, "CH9103"),
    (0x1A86, 0x55D8, "CH9101"),
    (0x1A86, 0x55DA, "CH347"),
    (0x10C4, 0xEA7A, "CP2105"),  # Linux cp210x.c
    (0x10C4, 0xEA7B, "CP2108"),
    (0x0403, 0x6006, "FT232R"),  # Linux ftdi_sio_ids.h
    (0x0403, 0x6040, "FT2233HP"),
    (0x0403, 0x6041, "FT4233HP"),
    (0x0403, 0x6043, "FT4232HP"),
    (0x0403, 0x6045, "FT232HP"),
    (0x16C0, 0x0483, "USB Serial"),  # PJRC teensy4/usb_desc.h
    (0x16C0, 0x0486, "RawHID"),
    (0x16C0, 0x0487, "Serial+Keyboard"),
    (0x16C0, 0x0489, "Serial+MIDI"),
    (0x0483, 0x374B, "V2-1"),  # usb.ids
    (0x303A, 0x1001, "USB-Serial/JTAG"),  # esp-pylib constants
    (0x303A, 0x4001, "TinyUSB"),  # esp-usb esp_tinyusb descriptors
]


class TestChipLookup:
    """VID:PID -> ChipInfo lookup.

    Spot-checks one entry per vendor family so a breaking restructure
    (e.g. accidentally truncating the table) fails visibly.  The full
    table is covered indirectly by the structural guards below.
    """

    def test_ftdi_ft232r(self):
        """The FTDI FT232R row carries its Full-Speed rating and 3 Mbaud datasheet max."""
        # Act
        actual = chip(0x0403, 0x6001)

        # Assert
        assert actual is not None, "FT232R in table"
        assert actual.model.startswith("FTDI FT232R"), "chip name"
        assert actual.speed == "full", "FT232R is Full-Speed"
        assert actual.max_baud == 3_000_000, "datasheet max baud"

    def test_ftdi_ft232h_is_high_speed(self):
        """FT232H is marked High-Speed, the one FTDI part here that differs from FT232R."""
        # Act
        actual = chip(0x0403, 0x6014)

        # Assert
        assert actual is not None, "FT232H in table"
        assert actual.speed == "high", "FT232H is High-Speed"

    def test_silabs_cp2102(self):
        """The Silicon Labs CP2102 VID:PID resolves to a CP2102 model name."""
        # Act
        actual = chip(0x10C4, 0xEA60)

        # Assert
        assert actual is not None, "CP2102 in table"
        assert "CP2102" in actual.model, "chip name mentions CP2102"

    def test_wch_ch340(self):
        """The WCH CH340 VID:PID resolves to the exact model name "WCH CH340"."""
        # Act
        actual = chip(0x1A86, 0x7523)

        # Assert
        assert actual is not None, "CH340 in table"
        assert actual.model == "WCH CH340", "chip name"

    def test_sparkfun(self):
        """A SparkFun board PID (Pro Micro 5V) resolves to a SparkFun-named entry."""
        # Act
        actual = chip(0x1B4F, 0x9206)

        # Assert
        assert actual is not None, "SparkFun Pro Micro 5V in table"
        assert "SparkFun" in actual.model, "named SparkFun"

    def test_adafruit(self):
        """An Adafruit board PID (Metro/Feather M4) resolves to an Adafruit-named entry."""
        # Act
        actual = chip(0x239A, 0x800B)

        # Assert
        assert actual is not None, "Adafruit Metro/Feather M4 in table"
        assert "Adafruit" in actual.model, "named Adafruit"

    def test_unknown_vid_pid_returns_none(self):
        """An unlisted VID:PID gives None rather than raising or guessing a vendor."""
        # Act -- a made-up VID:PID
        actual = chip(0xDEAD, 0xBEEF)

        # Assert
        assert actual is None, "unknown VID:PID returns None"

    def test_dict_and_function_agree(self):
        """chip() is a thin wrapper over USB_SERIAL_CHIPS.get()."""
        # Act / Assert -- round-trip for a known entry
        actual_func = chip(0x0403, 0x6001)
        actual_dict = USB_SERIAL_CHIPS.get((0x0403, 0x6001))
        assert actual_func is actual_dict, "chip() returns the same object the dict does"


class TestChipTableStructure:
    """Structural guards on USB_SERIAL_CHIPS itself."""

    def test_every_value_is_chipinfo(self):
        """Every table value uses the ChipInfo dataclass."""
        # Act / Assert
        wrong_types = [
            type(value).__name__
            for value in USB_SERIAL_CHIPS.values()
            if not isinstance(value, ChipInfo)
        ]
        assert wrong_types == [], f"all values must be ChipInfo; got: {wrong_types}"

    def test_speed_values_are_full_or_high(self):
        """The ``speed`` field accepts only the two documented values."""
        # Act / Assert
        bad = [(k, v.speed) for k, v in USB_SERIAL_CHIPS.items() if v.speed not in ("full", "high")]
        assert bad == [], f"speed must be 'full' or 'high'; violations: {bad}"

    def test_keys_are_2_tuples_of_ints(self):
        """Dict keys are ``(vid, pid)`` integer pairs."""
        # Act / Assert
        bad_keys = [
            k
            for k in USB_SERIAL_CHIPS
            if not (isinstance(k, tuple) and len(k) == 2 and all(isinstance(x, int) for x in k))
        ]
        assert bad_keys == [], f"bad keys: {bad_keys}"

    def test_max_baud_is_non_negative(self):
        """``max_baud`` is a non-negative int (0 means 'not a UART')."""
        # Act / Assert
        bad = [
            (k, v.max_baud)
            for k, v in USB_SERIAL_CHIPS.items()
            if not (isinstance(v.max_baud, int) and v.max_baud >= 0)
        ]
        assert bad == [], f"max_baud must be >= 0 int; violations: {bad}"


class TestSourcedNames:
    """The corrected rows name the part their vendor's own source gives the PID."""

    @pytest.mark.parametrize(("vid", "pid", "expected"), SOURCED_NAMES)
    def test_row_names_the_sourced_part(self, vid, pid, expected):
        """A row names the part its vendor's list or driver gives the PID, not a neighbor's (audit 2026-10-25)."""
        # Act
        actual = chip(vid, pid)

        # Assert
        assert actual is not None, f"{vid:04X}:{pid:04X} is in the table"
        assert expected in actual.model, f"{vid:04X}:{pid:04X} is {expected}, got {actual.model}"

    def test_no_model_names_two_pids_of_one_vendor_as_one_part(self):
        """Within one vendor no two PIDs carry the same model text, except where the source shares it.

        The audit's errors were names shuffled among neighbors, which can
        leave two PIDs naming one part.  WCH's CH340 is the one sourced
        pair: usb.ids lists 0x7522 and 0x7523 as the same chip.
        """
        # Arrange
        allowed = {(0x1A86, "WCH CH340")}
        seen: dict[tuple[int, str], int] = {}
        duplicates: list[str] = []

        # Act
        for (vid, pid), info in USB_SERIAL_CHIPS.items():
            key = (vid, info.model)
            if key in seen and key not in allowed:
                duplicates.append(f"{vid:04X}:{seen[key]:04X} and {pid:04X}: {info.model}")
            seen.setdefault(key, pid)

        # Assert
        assert duplicates == [], f"one part named by two PIDs: {duplicates}"
