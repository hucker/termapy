"""Tests for termapy.usb_mfg -- USB manufacturer alias folding."""

from termapy.usb_mfg import MANUFACTURER_ALIASES, VendorAlias, mfg


class TestMfg:
    """Short-alias lookup for USB manufacturer descriptors.

    The raw descriptor varies between driver versions and OSes; ``mfg``
    folds all known variants to a compact label suitable for a narrow
    column.  These tests guard both the folding rules and the
    "unknown passes through" contract.
    """

    def test_empty_returns_empty(self):
        # Act / Assert
        assert mfg("") == "", "empty string returns empty"
        assert mfg(None) == "", "None returns empty"

    def test_case_insensitive(self):
        # Act / Assert -- same alias regardless of case
        assert mfg("FTDI") == "FTDI", "upper case"
        assert mfg("ftdi") == "FTDI", "lower case"
        assert mfg("Ftdi") == "FTDI", "mixed case"

    def test_ftdi_variants(self):
        # Act / Assert -- the long descriptor also maps to FTDI
        actual = mfg("Future Technology Devices International")
        assert actual == "FTDI", "FTDI long form collapses"

    def test_microsoft_variants(self):
        # Act / Assert -- short and long forms both become MSFT
        assert mfg("Microsoft") == "MSFT", "Microsoft short"
        assert mfg("Microsoft Corporation") == "MSFT", "Microsoft long"

    def test_silabs_variants(self):
        # Act / Assert -- both the short and long forms collapse
        assert mfg("Silicon Labs") == "SiLabs", "short form"
        assert mfg("Silicon Laboratories") == "SiLabs", "long form"

    def test_wch_variants(self):
        # Act / Assert -- two distinct reported strings, same alias
        assert mfg("WCH.CN") == "WCH", "WCH short"
        assert mfg("QinHeng Electronics") == "WCH", "QinHeng becomes WCH"

    def test_distinct_brands_not_merged(self):
        """Cypress and Infineon are the same company but report separately.

        Guards against a well-meaning refactor that would collapse them
        into a single alias.  If the chip self-identifies as Cypress,
        the user sees Cypress.
        """
        # Act / Assert
        assert mfg("Cypress") == "Cypress", "Cypress stays Cypress"
        assert mfg("Infineon") == "Infineon", "Infineon stays Infineon"
        # Same rule for Atmel vs Microchip
        assert mfg("Atmel") == "Atmel", "Atmel stays Atmel"
        assert mfg("Microchip Technology") == "Microchip", \
            "Microchip stays Microchip"

    def test_unknown_passes_through(self):
        # Act / Assert -- unrecognized vendors are returned verbatim
        assert mfg("Acme Corp") == "Acme Corp", "unknown vendor unchanged"

    def test_whitespace_stripped(self):
        # Act / Assert -- leading/trailing whitespace doesn't break match
        assert mfg("  FTDI  ") == "FTDI", "whitespace tolerated"

    def test_windows_standard_port_types_empty(self):
        """Windows built-in COM ports get blanked (not interesting)."""
        # Act / Assert
        assert mfg("(Standard port types)") == "", \
            "Windows generic reports as empty"


class TestAliasTable:
    """Structural guards on MANUFACTURER_ALIASES itself."""

    def test_every_alias_fits_column_budget(self):
        """All aliases fit in a ~9-char column.

        Guards the display contract.  If a future alias addition
        exceeds this width, the port-picker column will truncate
        (which defeats the point of the aliasing).
        """
        # Act / Assert
        long_aliases = [e.mfg for e in MANUFACTURER_ALIASES if len(e.mfg) > 9]
        assert long_aliases == [], \
            f"all aliases must be <= 9 chars; too long: {long_aliases}"

    def test_prefixes_are_lowercase(self):
        """Prefixes must be lowercase so the runtime ``.lower()`` match works."""
        # Act / Assert
        non_lower = [e.prefix for e in MANUFACTURER_ALIASES
                     if e.prefix != e.prefix.lower()]
        assert non_lower == [], \
            f"prefixes must be lowercase; found: {non_lower}"

    def test_entries_are_vendoralias_instances(self):
        """Every table entry uses the VendorAlias dataclass, not raw tuples."""
        # Act / Assert
        wrong_types = [type(e).__name__ for e in MANUFACTURER_ALIASES
                       if not isinstance(e, VendorAlias)]
        assert wrong_types == [], \
            f"all entries must be VendorAlias; got: {wrong_types}"
