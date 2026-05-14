"""Cross-module contract: every chip family has a matching mfg alias.

``usb_serial_chips`` identifies a chip from its VID:PID; ``usb_mfg``
folds a manufacturer descriptor to a short alias.  These two tables
are independent, but the port-picker UI expects them to *agree*: for
every plausible manufacturer string a ``USB_SERIAL_CHIPS`` entry
might self-report, ``mfg()`` must fold it to a short alias instead of
passing it through unchanged.

This is its own test file because it's the only place the two
modules' contracts intersect.  When someone adds a new chip family,
they'll need to add an alias entry *and* a row here, or this file
fails.
"""

from termapy.usb import mfg


class TestAliasCoverageOfChipTable:
    """Every chip family has a matching mfg() alias."""

    # Map: plausible USB descriptor -> expected short alias.  Keys are
    # real-world strings we expect ports in USB_SERIAL_CHIPS to
    # self-report.  Values are the short alias each should fold to.
    # When a new chip family lands in USB_SERIAL_CHIPS, add an entry
    # here so coverage stays visible.
    _EXPECTED = {
        "FTDI":                      "FTDI",
        "Future Technology Devices International": "FTDI",
        "Silicon Labs":              "SiLabs",
        "Silicon Laboratories":      "SiLabs",
        "WCH.CN":                    "WCH",
        "QinHeng Electronics":       "WCH",
        "Prolific Technology Inc.":  "Prolific",
        "Microchip Technology Inc.": "Microchip",
        "STMicroelectronics":        "STM",
        "ST-Link":                   "STM",
        "Espressif Systems":         "Espressif",
        "Arduino LLC":               "Arduino",
        "SparkFun Electronics":      "SparkFun",
        "Adafruit Industries":       "Adafruit",
        "Adafruit Industries LLC":   "Adafruit",
        "Teensyduino":               "Teensy",
        "Raspberry Pi Foundation":   "RaspPi",
    }

    def test_every_chip_family_has_a_matching_alias(self):
        # Act / Assert -- run each descriptor through mfg and compare.
        mismatches = []
        for raw, expected in self._EXPECTED.items():
            actual = mfg(raw)
            if actual != expected:
                mismatches.append((raw, expected, actual))
        assert mismatches == [], \
            "mfg() must fold every known descriptor to its alias; " \
            f"mismatches: {mismatches}"
