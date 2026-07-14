"""Tests for the "did you mean" suggestion suffix on unknown algorithm names.

Exercises the wrapper in ``termapy/builtins/commands/proto.py:_did_you_mean``
that calls ``crcglot.suggest_algorithms`` (0.25+) and the five sites that
splice the resulting suffix into their ``Unknown algorithm:`` fail
messages: ``_crc_info``, ``_crc_calc``, ``_crc_codegen`` (catalog
single-algo + bundle paths), and ``_crc_verify``.

The suggestion logic itself lives in crcglot; here we verify only that
the wrapper forwards through and that every callsite renders the
suffix correctly.
"""

from __future__ import annotations

from termapy.builtins.commands.proto import (
    _crc_calc,
    _crc_codegen,
    _crc_info,
    _crc_verify,
    _did_you_mean,
)
from termapy.plugins import IOHandle, PluginContext


def _build_stub_ctx():
    """Minimal PluginContext capturing output() and output_markup()."""
    captured: list[tuple[str, str]] = []
    captured_markup: list[str] = []

    def write(text, color="dim"):
        captured.append((text, color))

    def write_markup(text):
        captured_markup.append(text)

    ctx = PluginContext(
        io=IOHandle(_write=write, _write_markup=write_markup),
        active_flags={},
    )
    return ctx, captured, captured_markup


class TestDidYouMeanHelper:
    """``_did_you_mean`` forwards to ``crcglot.suggest_algorithms``."""

    def test_typo_returns_suggestion_suffix(self):
        # Arrange / Act
        actual = _did_you_mean("crc16-modbsu")

        # Assert -- suffix starts with the expected sentinel and includes
        # the catalog name the user clearly intended.  Don't pin the
        # full suggestion list -- crcglot owns the ranking and may
        # reorder across bumps.
        assert actual.startswith("; did you mean: "), (
            f"suffix should start with sentinel; got {actual!r}"
        )
        assert "crc16-modbus" in actual, (
            f"crc16-modbus should be suggested for typo crc16-modbsu; "
            f"got {actual!r}"
        )

    def test_garbage_returns_empty_string(self):
        # Arrange / Act -- nothing close in the catalog.
        actual = _did_you_mean("floozle")

        # Assert
        assert actual == "", f"no suggestion expected for 'floozle'; got {actual!r}"

    def test_width_family_returns_variants(self):
        # Arrange / Act -- bare ``crc16`` names a family with no default;
        # crcglot suggests width-16 variants.
        actual = _did_you_mean("crc16")

        # Assert
        assert "crc16-modbus" in actual, (
            f"crc16 family should suggest crc16-modbus; got {actual!r}"
        )


class TestSuggestionRendersAtHandlerSites:
    """All five unknown-algo error sites carry the suggestion suffix."""

    def test_crc_info_suffix(self):
        # Arrange
        ctx, _captured, _markup = _build_stub_ctx()

        # Act
        result = _crc_info(ctx, "crc16-modbsu")

        # Assert
        assert not result.success, "typo'd algo should fail"
        assert "did you mean: " in (result.error or ""), (
            f"_crc_info should emit suggestion; got {result.error!r}"
        )
        assert "crc16-modbus" in (result.error or ""), (
            f"_crc_info should suggest crc16-modbus; got {result.error!r}"
        )

    def test_crc_calc_suffix(self):
        # Arrange
        ctx, _captured, _markup = _build_stub_ctx()

        # Act
        result = _crc_calc(ctx, "crc16-modbsu 01 02 03")

        # Assert
        assert not result.success, "typo'd algo should fail"
        assert "crc16-modbus" in (result.error or ""), (
            f"_crc_calc should suggest crc16-modbus; got {result.error!r}"
        )

    def test_crc_verify_suffix(self):
        # Arrange
        ctx, _captured, _markup = _build_stub_ctx()

        # Act
        result = _crc_verify(ctx, "crc16-modbsu 01 02 03 04 05")

        # Assert
        assert not result.success, "typo'd algo should fail"
        assert "crc16-modbus" in (result.error or ""), (
            f"_crc_verify should suggest crc16-modbus; got {result.error!r}"
        )

    def test_crc_codegen_single_suffix(self):
        # Arrange -- catalog single-algo path
        ctx, _captured, _markup = _build_stub_ctx()

        # Act
        result = _crc_codegen(ctx, "crc16-modbsu", "c")

        # Assert
        assert not result.success, "typo'd algo should fail"
        assert "crc16-modbus" in (result.error or ""), (
            f"_crc_codegen (single) should suggest crc16-modbus; "
            f"got {result.error!r}"
        )

    def test_crc_codegen_bundle_suffix(self):
        # Arrange -- bundle path: one good name + one bad name; the bad
        # name's error should carry the suggestion.
        ctx, _captured, _markup = _build_stub_ctx()

        # Act
        result = _crc_codegen(ctx, "crc32 crc16-modbsu", "c")

        # Assert
        assert not result.success, "bundle with bad algo should fail"
        assert "crc16-modbus" in (result.error or ""), (
            f"_crc_codegen (bundle) should suggest crc16-modbus for "
            f"the bad member; got {result.error!r}"
        )
