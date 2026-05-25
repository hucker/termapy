"""Tests for the custom CRC and symbol-override paths on /proto.crc.<lang>.

The codegen subcommands accept two invocation shapes:

* **Catalogue lookup** -- ``<algo-name> [file=stem] [symbol=name]``
  generates from a CRC_CATALOGUE entry; ``symbol=`` overrides the
  function name; ``file=`` writes to disk (and also sets the function
  name from the file basename if ``symbol=`` is not given).

* **Custom CRC** -- ``width=N poly=X [init=...] [refin=...] [refout=...]
  [xorout=...] [name=...] [desc=...] [file=...] [symbol=...]`` builds
  a synthetic catalogue entry from raw Rocksoft/Williams parameters
  and computes the check value via the same generic engine that
  drives the bundled catalogue.

These tests exercise:

- The custom-params path produces functions that compute the same CRC
  as the corresponding bundled catalogue entry (when the params match).
- The symbol override (explicit ``symbol=``) renames the function.
- The symbol-from-file default kicks in when no explicit symbol is
  given but ``file=`` is set.
- Helper parsers (`_parse_int_value` / `_parse_bool_value` /
  `_symbol_from_stem`) behave correctly on edge cases.
"""

from __future__ import annotations

import pytest

from termapy.builtins.commands.proto import (
    _parse_bool_value,
    _parse_int_value,
    _symbol_from_stem,
)
from termapy.protocol import (
    generate_c,
    generate_c_from_entry,
    generate_python,
    generate_python_from_entry,
    generate_rust,
    generate_vhdl,
)
from termapy.protocol.crc import _generic_crc


class TestParseHelpers:
    """The small parsers used to coerce key=value strings."""

    def test_parse_int_decimal(self):
        # Act / Assert
        assert _parse_int_value("16", "width") == 16, "decimal parses"
        assert _parse_int_value("0", "init") == 0, "zero parses"

    def test_parse_int_hex(self):
        # Act / Assert
        assert _parse_int_value("0x8005", "poly") == 0x8005, "hex parses"
        assert _parse_int_value("0xFFFFFFFF", "init") == 0xFFFFFFFF, (
            "32-bit hex parses"
        )
        assert _parse_int_value("0XfFfF", "init") == 0xFFFF, (
            "case-insensitive 0x prefix"
        )

    def test_parse_int_invalid(self):
        # Act / Assert
        with pytest.raises(ValueError):
            _parse_int_value("not-a-number", "width")

    @pytest.mark.parametrize("token", ["true", "True", "1", "yes", "ON"])
    def test_parse_bool_truthy(self, token):
        # Assert
        assert _parse_bool_value(token, "refin") is True, (
            f"token {token!r} parses as True"
        )

    @pytest.mark.parametrize("token", ["false", "False", "0", "no", "OFF"])
    def test_parse_bool_falsy(self, token):
        # Assert
        assert _parse_bool_value(token, "refin") is False, (
            f"token {token!r} parses as False"
        )

    def test_parse_bool_invalid(self):
        # Act / Assert
        with pytest.raises(ValueError):
            _parse_bool_value("maybe", "refin")

    def test_symbol_from_stem_basename(self):
        # Act / Assert
        assert _symbol_from_stem("crc16_modbus") == "crc16_modbus"
        assert _symbol_from_stem("subdir/crc16_modbus") == "crc16_modbus"
        assert _symbol_from_stem("/abs/path/crc16_modbus") == "crc16_modbus"

    def test_symbol_from_stem_sanitizes(self):
        # Act / Assert
        assert _symbol_from_stem("my-crc") == "my_crc"
        assert _symbol_from_stem("crc.v1") == "crc_v1"
        assert _symbol_from_stem("subdir/my-crc.v2") == "my_crc_v2"


# Reveng-derived canonical check values for the algorithms used in
# the round-trip tests below.  These are HARDCODED on purpose: they
# come from the reveng CRC catalogue
# (https://reveng.sourceforge.io/crc-catalogue/all.htm) and serve as
# external ground truth for the entire chain.  Deriving them from
# ``CRC_CATALOGUE`` or ``_generic_crc`` instead would make the tests
# circular -- the catalogue's check field IS populated by the same
# engine, so the tests would assert ``engine(x) == engine(x)`` and
# pass even if the engine were silently wrong.  By hardcoding from
# the external source, a regression in either the engine OR the
# generators surfaces as a real failure.
_REVENG_CHECK_VALUES = {
    "crc16-modbus":  (16, 0x8005,     0xFFFF,     True,  True,  0x0000,     0x4B37),
    "crc16-xmodem":  (16, 0x1021,     0x0000,     False, False, 0x0000,     0x31C3),
    "crc16-ibm-3740": (16, 0x1021,    0xFFFF,     False, False, 0x0000,     0x29B1),
    "crc32":         (32, 0x04C11DB7, 0xFFFFFFFF, True,  True,  0xFFFFFFFF, 0xCBF43926),
    "crc32-bzip2":   (32, 0x04C11DB7, 0xFFFFFFFF, False, False, 0xFFFFFFFF, 0xFC891918),
    "crc8":          (8,  0x07,       0x00,       False, False, 0x00,       0xF4),
    "crc8-maxim":    (8,  0x31,       0x00,       True,  True,  0x00,       0xA1),
    "crc64-xz":      (64, 0x42F0E1EBA9EA3693, 0xFFFFFFFFFFFFFFFF,
                      True, True, 0xFFFFFFFFFFFFFFFF, 0x995DC9BBDF1939FA),
}


class TestCustomCrcChainAgainstRevengTruth:
    """End-to-end verification of the custom-params path against
    HARDCODED reveng check values (not engine-derived) so a bug in
    either the engine OR the generators is caught for real."""

    @pytest.mark.parametrize("algo_name", sorted(_REVENG_CHECK_VALUES.keys()))
    def test_engine_matches_reveng(self, algo_name):
        """``_generic_crc`` with hardcoded params produces the
        reveng-published check value -- proves the engine itself is
        correct independent of catalogue / generator paths."""
        # Arrange
        w, poly, init, refin, refout, xorout, expected = (
            _REVENG_CHECK_VALUES[algo_name]
        )

        # Act
        actual = _generic_crc(
            b"123456789", w, poly, init, refin, refout, xorout
        )

        # Assert
        assert actual == expected, (
            f"{algo_name}: _generic_crc gave {actual:#x}, "
            f"reveng-canonical is {expected:#x}"
        )

    @pytest.mark.parametrize("algo_name", sorted(_REVENG_CHECK_VALUES.keys()))
    def test_generated_python_matches_reveng_via_custom_params(self, algo_name):
        """The Python generator, fed a synthetic entry built from
        HARDCODED params, produces code whose function returns the
        HARDCODED reveng check.  This is the real test of the
        custom-params path -- if either the entry-dict generator or
        the engine that computed ``check`` is wrong, the test fails."""
        # Arrange -- hardcoded params + hardcoded expected check.
        w, poly, init, refin, refout, xorout, expected = (
            _REVENG_CHECK_VALUES[algo_name]
        )
        # Build a synthetic entry the way the CLI custom-params path
        # does -- check field populated via the engine, but we ALSO
        # verify the generated code matches the hardcoded reveng value
        # below so the engine's contribution is verified against
        # external truth.
        entry = {
            "width": w, "poly": poly, "init": init,
            "refin": refin, "refout": refout, "xorout": xorout,
            "check": expected, "desc": f"hardcoded-canonical for {algo_name}",
        }
        # Use a symbol that's a valid identifier regardless of the
        # source name's punctuation.
        symbol = algo_name.replace("-", "_")

        # Act -- generate code and execute it.
        code = generate_python_from_entry(algo_name, entry, symbol=symbol)
        ns: dict = {}
        exec(code, ns)
        actual = ns[symbol](b"123456789")

        # Assert -- generated function matches the EXTERNAL reveng
        # truth, not the engine's own computation.
        assert actual == expected, (
            f"{algo_name}: generated Python (via from_entry, "
            f"hardcoded reveng params) returned {actual:#x}, "
            f"reveng-canonical is {expected:#x}"
        )

    def test_generate_c_from_entry_header_uses_symbol(self):
        """Structural -- ``symbol=`` renames everything consistently
        across the .h header (declarations, include guard) and the
        .c source.  Value correctness is covered by the parameterized
        round-trip tests above."""
        # Arrange -- crc16-modbus params (any valid CRC works for
        # this structural test).
        w, poly, init, refin, refout, xorout, check = (
            _REVENG_CHECK_VALUES["crc16-modbus"]
        )
        entry = {
            "width": w, "poly": poly, "init": init,
            "refin": refin, "refout": refout, "xorout": xorout,
            "check": check, "desc": "structural test",
        }

        # Act
        result = generate_c_from_entry(
            "my_modbus", entry, symbol="my_modbus",
        )

        # Assert
        assert result is not None, "generator returned a pair"
        header, source = result
        assert "#ifndef MY_MODBUS_H" in header, (
            "include guard derives from symbol"
        )
        assert "uint16_t my_modbus_init(void)" in header, (
            "header declaration uses symbol"
        )
        assert "uint16_t my_modbus_init(void)" in source, (
            "source definition uses symbol"
        )
        assert '#include "my_modbus.h"' in source, (
            "source #include matches symbol-named header"
        )


class TestSymbolResolution:
    """The symbol used in the generated code follows the precedence:
    explicit ``symbol=`` > basename of ``file=`` > generator default."""

    def test_explicit_symbol_overrides_algorithm_name(self):
        # Arrange / Act -- catalogue name crc16-modbus, override symbol
        from termapy.protocol import generate_python

        code = generate_python("crc16-modbus", symbol="renamed_func")

        # Assert
        assert code is not None
        assert "def renamed_func(" in code, "symbol override renames"
        assert "def crc16_modbus(" not in code, (
            "original algorithm-based name is replaced"
        )

    def test_no_symbol_uses_algorithm_name(self):
        # Arrange / Act -- no symbol override; default = _func_name(name)
        from termapy.protocol import generate_python

        code = generate_python("crc16-modbus")

        # Assert
        assert code is not None
        assert "def crc16_modbus(" in code, (
            "default symbol comes from algorithm name"
        )

    def test_symbol_from_path_basename(self):
        # The handler computes ``_symbol_from_stem(file=)`` when no
        # explicit symbol= is given; verify the helper produces the
        # basename of common path shapes.
        # Act / Assert
        assert _symbol_from_stem("c:/tmp/my_crc") == "my_crc", (
            "absolute path stem -> basename"
        )
        assert _symbol_from_stem("out/sub/my-crc") == "my_crc", (
            "relative path with dashes -> sanitized basename"
        )


class TestGenerateFromEntryAcceptsSyntheticEntry:
    """The generators accept entry dicts for algorithms not in any
    catalogue -- the whole point of the custom-params path."""

    def test_generator_and_engine_agree_on_synthetic_crc(self):
        """For a made-up CRC (no external truth available), assert
        that the GENERATED code computes the SAME value as the
        ENGINE on the same input.  This is a self-consistency check
        between the two implementations of the algorithm, NOT a
        check against an external canonical value -- because for a
        made-up CRC there is no external canonical value to check
        against.

        The reveng-canonical tests above (TestCustomCrcChainAgainstRevengTruth)
        cover external-truth verification.  This test covers the
        complementary property: whatever the engine computes, the
        generated code computes the same thing.  Together they pin
        both halves of the custom-params path.
        """
        # Arrange -- a deliberately weird (but valid) CRC-16 spec
        # that's not in any catalogue.
        width, poly, init = 16, 0x1234, 0xABCD
        refin, refout, xorout = False, False, 0x5678
        engine_result = _generic_crc(
            b"123456789", width, poly, init, refin, refout, xorout
        )
        entry = {
            "width": width, "poly": poly, "init": init,
            "refin": refin, "refout": refout, "xorout": xorout,
            "check": engine_result, "desc": "Made-up CRC, no reveng truth",
        }

        # Act -- generate Python, exec, run on the check input.
        code = generate_python_from_entry("madeup", entry, symbol="madeup")
        ns: dict = {}
        exec(code, ns)
        generated_result = ns["madeup"](b"123456789")

        # Assert -- generator output matches engine output for the
        # same params and input.  (Both could be wrong in the same
        # way for THIS made-up CRC -- the reveng-canonical tests
        # rule that out for known algorithms.)
        assert generated_result == engine_result, (
            f"generator and engine disagree on synthetic CRC: "
            f"generator={generated_result:#x}, engine={engine_result:#x}"
        )


class TestSliceBy8GeneratorAPI:
    """Structural tests for the slice8 generator parameter.

    Execution-correctness tests live in test_crc_codegen_exec.py
    (TestGeneratedCSliceBy8Executes / TestGeneratedRustSliceBy8Executes).
    These tests verify the API surface: returns aren't None, output
    contains the right markers, and out-of-range widths raise cleanly.
    """

    def test_c_slice8_emits_8_tables(self):
        # Arrange + Act
        result = generate_c("crc32", slice8=True)

        # Assert -- generator returned a (header, source) pair and the
        # source contains the 2D slice-table declaration.
        assert result is not None, "generate_c crc32 slice8=True returned None"
        _header, source = result
        assert "crc_slice_tables[8][256]" in source, (
            "C source missing 2D slice-table declaration"
        )

    def test_rust_slice8_emits_8_tables(self):
        # Arrange + Act
        code = generate_rust("crc32", slice8=True)

        # Assert
        assert code is not None, "generate_rust crc32 slice8=True returned None"
        assert "CRC_SLICE_TABLES: [[u32; 256]; 8]" in code, (
            "Rust source missing 2D slice-table declaration"
        )

    @pytest.mark.parametrize("algo", ["crc8", "crc16-modbus"])
    def test_c_slice8_rejects_narrow_widths(self, algo):
        # Act + Assert
        with pytest.raises(ValueError, match="slice8=True requires width"):
            generate_c(algo, slice8=True)

    @pytest.mark.parametrize("algo", ["crc8", "crc16-modbus"])
    def test_rust_slice8_rejects_narrow_widths(self, algo):
        # Act + Assert
        with pytest.raises(ValueError, match="slice8=True requires width"):
            generate_rust(algo, slice8=True)

    def test_python_generate_has_no_slice8_kwarg(self):
        """generate_python intentionally has no slice8 parameter: Python's
        per-int overhead eats the speedup, so emitting slice-by-8 in
        Python would add code without any throughput benefit."""
        # Act + Assert -- passing slice8= must raise TypeError.
        with pytest.raises(TypeError):
            generate_python("crc32", slice8=True)  # type: ignore[call-arg]

    def test_vhdl_generate_has_no_slice8_kwarg(self):
        """Same rationale for VHDL: the generator is simulator-focused
        (a reference implementation), not synthesizable hardware where
        throughput optimization would matter."""
        # Act + Assert
        with pytest.raises(TypeError):
            generate_vhdl("crc32", slice8=True)  # type: ignore[call-arg]


class TestPythonSlice8Fallback:
    """``/proto.crc.python --slice8`` falls back to ``--table`` with a note.

    Slice-by-8 in CPython is empirically a 0.79x performance regression
    (PyLong allocations eat the loop-iteration savings).  Rather than
    error out and force the user to retype the command, the handler
    accepts ``--slice8``, emits a note explaining the fallback, and
    proceeds as if they'd typed ``--table``.
    """

    def _build_stub_ctx(self):
        """Minimal ctx that captures ``ctx.io.output(*)`` calls.

        Enough of the PluginContext surface for ``_crc_codegen`` to
        run without crashing -- engine.prefix for the legacy error
        path, IOHandle with capturing _write callbacks for output().
        """
        from termapy.plugins import IOHandle, PluginContext
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

    def test_python_slice8_falls_back_to_table_with_note(self):
        # Arrange
        from termapy.builtins.commands.proto import _crc_codegen
        ctx, captured, captured_markup = self._build_stub_ctx()
        ctx.active_flags = {"--slice8": True}

        # Act
        result = _crc_codegen(ctx, "crc32", "python")

        # Assert -- handler succeeded and the fallback note was emitted.
        assert result.success, f"handler should succeed, got {result.error!r}"
        note_msgs = [t for t, _color in captured if "slice8" in t.lower()]
        assert note_msgs, (
            f"fallback note about --slice8 should appear in output; "
            f"got captured={captured}"
        )
        # The emitted code should be table-driven (contains _TABLE),
        # NOT slice-by-8 (would contain CRC_SLICE_TABLES).
        emitted = "".join(captured_markup)
        assert "_TABLE = (" in emitted, (
            "Python slice8 fallback should emit table-driven code "
            "(no Python slice-by-8 implementation exists)"
        )
        assert "SLICE" not in emitted.upper(), (
            "Python fallback must NOT emit slice-by-8 code"
        )

    def test_python_slice8_with_table_is_rejected(self):
        # Arrange
        from termapy.builtins.commands.proto import _crc_codegen
        ctx, _captured, _markup = self._build_stub_ctx()
        ctx.active_flags = {"--slice8": True, "--table": True}

        # Act
        result = _crc_codegen(ctx, "crc32", "python")

        # Assert -- mutually-exclusive error takes precedence over the
        # python fallback.
        assert not result.success, "--slice8 + --table should be rejected"
        assert "mutually exclusive" in (result.error or ""), (
            f"error msg should mention mutual exclusion, got {result.error!r}"
        )
