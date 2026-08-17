"""Tests for termapy.usb.chips -- VID:PID -> ChipInfo lookup."""

from termapy.usb import USB_SERIAL_CHIPS, ChipInfo, chip


class TestChipLookup:
    """VID:PID -> ChipInfo lookup.

    Spot-checks one entry per vendor family so a breaking restructure
    (e.g. accidentally truncating the table) fails visibly.  The full
    table is covered indirectly by the structural guards below.
    """

    def test_ftdi_ft232r(self):
        # Act
        actual = chip(0x0403, 0x6001)

        # Assert
        assert actual is not None, "FT232R in table"
        assert actual.model.startswith("FTDI FT232R"), "chip name"
        assert actual.speed == "full", "FT232R is Full-Speed"
        assert actual.max_baud == 3_000_000, "datasheet max baud"

    def test_ftdi_ft232h_is_high_speed(self):
        # Act
        actual = chip(0x0403, 0x6014)

        # Assert
        assert actual is not None, "FT232H in table"
        assert actual.speed == "high", "FT232H is High-Speed"

    def test_silabs_cp2102(self):
        # Act
        actual = chip(0x10C4, 0xEA60)

        # Assert
        assert actual is not None, "CP2102 in table"
        assert "CP2102" in actual.model, "chip name mentions CP2102"

    def test_wch_ch340(self):
        # Act
        actual = chip(0x1A86, 0x7523)

        # Assert
        assert actual is not None, "CH340 in table"
        assert actual.model == "WCH CH340", "chip name"

    def test_sparkfun(self):
        # Act
        actual = chip(0x1B4F, 0x9206)

        # Assert
        assert actual is not None, "SparkFun Pro Micro 5V in table"
        assert "SparkFun" in actual.model, "named SparkFun"

    def test_adafruit(self):
        # Act
        actual = chip(0x239A, 0x800B)

        # Assert
        assert actual is not None, "Adafruit Metro/Feather M4 in table"
        assert "Adafruit" in actual.model, "named Adafruit"

    def test_unknown_vid_pid_returns_none(self):
        # Act -- a made-up VID:PID
        actual = chip(0xDEAD, 0xBEEF)

        # Assert
        assert actual is None, "unknown VID:PID returns None"

    def test_dict_and_function_agree(self):
        """chip() is a thin wrapper over USB_SERIAL_CHIPS.get()."""
        # Act / Assert -- round-trip for a known entry
        actual_func = chip(0x0403, 0x6001)
        actual_dict = USB_SERIAL_CHIPS.get((0x0403, 0x6001))
        assert actual_func is actual_dict, \
            "chip() returns the same object the dict does"


class TestChipTableStructure:
    """Structural guards on USB_SERIAL_CHIPS itself."""

    def test_every_value_is_chipinfo(self):
        """Every table value uses the ChipInfo dataclass."""
        # Act / Assert
        wrong_types = [
            type(value).__name__ for value in USB_SERIAL_CHIPS.values()
            if not isinstance(value, ChipInfo)
        ]
        assert wrong_types == [], \
            f"all values must be ChipInfo; got: {wrong_types}"

    def test_speed_values_are_full_or_high(self):
        """The ``speed`` field accepts only the two documented values."""
        # Act / Assert
        bad = [(k, v.speed) for k, v in USB_SERIAL_CHIPS.items()
               if v.speed not in ("full", "high")]
        assert bad == [], \
            f"speed must be 'full' or 'high'; violations: {bad}"

    def test_keys_are_2_tuples_of_ints(self):
        """Dict keys are ``(vid, pid)`` integer pairs."""
        # Act / Assert
        bad_keys = [
            k for k in USB_SERIAL_CHIPS
            if not (isinstance(k, tuple) and len(k) == 2
                    and all(isinstance(x, int) for x in k))
        ]
        assert bad_keys == [], f"bad keys: {bad_keys}"

    def test_max_baud_is_non_negative(self):
        """``max_baud`` is a non-negative int (0 means 'not a UART')."""
        # Act / Assert
        bad = [(k, v.max_baud) for k, v in USB_SERIAL_CHIPS.items()
               if not (isinstance(v.max_baud, int) and v.max_baud >= 0)]
        assert bad == [], f"max_baud must be >= 0 int; violations: {bad}"
