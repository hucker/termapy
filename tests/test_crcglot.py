"""Tests for CRC code generation - verify generated code computes correct CRC."""

from __future__ import annotations

import pytest

from crcglot import (
    CRC_CATALOGUE,
    GENERATORS,
    generate_c,
    generate_python,
    generate_rust,
    generate_vhdl,
)


# Standard check string used by the reveng catalogue
CHECK_DATA = b"123456789"


class TestGeneratePython:
    """Verify generated Python code computes correct CRC values."""

    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_generated_code_matches_check(self, name):
        # Arrange
        entry = CRC_CATALOGUE[name]
        expected = entry["check"]
        code = generate_python(name)
        assert code is not None, f"generate_python returned code for {name}"

        # Act - execute the generated function
        ns: dict = {}
        exec(code, ns)
        func_name = name.replace("-", "_").replace(".", "_")
        actual = ns[func_name](CHECK_DATA)

        # Assert
        assert actual == expected, f"{name}: {actual:#x} != {expected:#x}"

    def test_unknown_algorithm(self):
        # Assert
        assert generate_python("nonexistent") is None, "unknown algorithm should return None"

    def test_has_docstring(self):
        # Act
        code = generate_python("crc16-modbus")

        # Assert
        assert code is not None, "generator returned code"
        assert '"""' in code, "has docstring"
        assert "crc16-modbus" in code, "names the algorithm"

    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_table_driven_matches_check(self, name):
        # Arrange
        entry = CRC_CATALOGUE[name]
        expected = entry["check"]
        code = generate_python(name, table=True)
        assert code is not None, f"generate_python(table=True) returned code for {name}"

        # Act - execute the generated table-driven function
        ns: dict = {}
        exec(code, ns)
        func_name = name.replace("-", "_").replace(".", "_")
        actual = ns[func_name](CHECK_DATA)

        # Assert
        assert actual == expected, f"{name} table: {actual:#x} != {expected:#x}"


class TestGeneratedPythonStreaming:
    """The streaming primitives (init / update / finalize) must satisfy
    the splittability invariant: for any input, computing in chunks
    must produce the same result as the one-shot wrapper, which in
    turn must equal the reveng catalogue's check value.

    Three patterns are exercised per algorithm:

    1. **Split mid-input** (init -> update("1234") -> update("56789") ->
       finalize) -- catches wrong state shape, broken update
       accumulator, or accidentally re-applying finalize logic in update.
    2. **Empty chunk at start** (init -> update(b"") -> update(full)
       -> finalize) -- catches loops that misbehave on zero-length
       input.
    3. **Empty chunk at end** (init -> update(full) -> update(b"") ->
       finalize) -- catches a different class of zero-length bug.

    All three must equal the reveng check value AND equal the
    one-shot wrapper's result.
    """

    @pytest.mark.parametrize("table", [False, True])
    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_streaming_matches_oneshot(self, name, table):
        # Arrange
        entry = CRC_CATALOGUE[name]
        expected = entry["check"]
        code = generate_python(name, table=table)
        assert code is not None, f"generate_python({name!r}) returned code"

        ns: dict = {}
        exec(code, ns)
        fname = name.replace("-", "_").replace(".", "_")
        init_fn = ns[f"{fname}_init"]
        update_fn = ns[f"{fname}_update"]
        finalize_fn = ns[f"{fname}_finalize"]

        # Pattern 1 -- split at byte 4
        state = init_fn()
        state = update_fn(state, b"1234")
        state = update_fn(state, b"56789")
        split_result = finalize_fn(state)

        # Pattern 2 -- empty chunk first
        state = init_fn()
        state = update_fn(state, b"")
        state = update_fn(state, b"123456789")
        empty_first_result = finalize_fn(state)

        # Pattern 3 -- empty chunk last
        state = init_fn()
        state = update_fn(state, b"123456789")
        state = update_fn(state, b"")
        empty_last_result = finalize_fn(state)

        # Assert -- all three patterns equal the reveng check value
        assert split_result == expected, (
            f"{name} (table={table}): split-at-4 streamed result "
            f"{split_result:#x} != check {expected:#x}"
        )
        assert empty_first_result == expected, (
            f"{name} (table={table}): empty-chunk-first streamed result "
            f"{empty_first_result:#x} != check {expected:#x}"
        )
        assert empty_last_result == expected, (
            f"{name} (table={table}): empty-chunk-last streamed result "
            f"{empty_last_result:#x} != check {expected:#x}"
        )


class TestGenerateC:
    """generate_c returns a (header, source) pair of complete files.

    The header has the standard ``extern "C"`` guard for C++ interop;
    the source ``#include``s the header and emits a ``_self_test()``
    function callers can invoke for runtime verification.  See
    ``test_crc_codegen_exec.py`` for the execution-verified tests
    (compile + run) that pin correctness for every algorithm.
    """

    def test_generates_pair(self):
        # Act
        result = generate_c("crc16-modbus")

        # Assert -- tuple shape and basic content
        assert result is not None, "generator returned a pair"
        header, source = result
        assert "extern \"C\"" in header, "header has extern \"C\" guard for C++ interop"
        assert "uint16_t crc16_modbus(" in header, "header declares the function"
        assert "int crc16_modbus_self_test(" in header, "header declares self_test"
        assert "#include \"crc16_modbus.h\"" in source, "source includes its header"
        assert "crc16_modbus_self_test" in source, "source defines self_test"
        assert "0x4B37" in source, "self_test asserts the canonical check value"

    def test_unknown_algorithm(self):
        # Assert
        assert generate_c("nonexistent") is None, "unknown algorithm should return None"

    def test_crc8_uses_uint8(self):
        # Act
        result = generate_c("crc8")

        # Assert
        assert result is not None, "generator returned a pair"
        _header, source = result
        assert "uint8_t" in source, "CRC-8 should use uint8_t"

    def test_crc32_uses_uint32(self):
        # Act
        result = generate_c("crc32")

        # Assert
        assert result is not None, "generator returned a pair"
        _header, source = result
        assert "uint32_t" in source, "CRC-32 should use uint32_t"


class TestGenerateRust:
    """generate_rust returns a single .rs source string.

    Includes a ``#[cfg(test)] mod tests`` block at the bottom; idiomatic
    Rust testing -- ``cargo test`` discovers it, and crcglot's pytest
    runs it via ``rustc --test``.  See ``test_crcglot_exec.py`` for
    the parameterized execution-verified tests.
    """

    def test_generates_code(self):
        # Act
        code = generate_rust("crc16-modbus")

        # Assert
        assert code is not None, "generator returned code"
        assert "fn crc16_modbus" in code, "function name"
        assert "u16" in code, "correct type"
        assert "0x4B37" in code, "check value"
        assert "#[cfg(test)]" in code, "cfg(test) gated test module emitted"
        assert "#[test]" in code, "individual #[test] attribute present"

    def test_unknown_algorithm(self):
        # Assert
        assert generate_rust("nonexistent") is None, "unknown algorithm should return None"

    def test_crc8_uses_u8(self):
        # Act
        code = generate_rust("crc8")

        # Assert
        assert code is not None, "generator returned code"
        assert "u8" in code, "CRC-8 should use u8"

    def test_crc32_uses_u32(self):
        # Act
        code = generate_rust("crc32")

        # Assert
        assert code is not None, "generator returned code"
        assert "u32" in code, "CRC-32 should use u32"


class TestGenerateVhdl:
    """generate_vhdl returns a complete .vhd package source.

    Includes a ``<fname>_self_test`` boolean function that crcglot's
    pytest harness exercises by synthesizing a testbench (see
    ``test_crcglot_exec.py``).  Bit-by-bit only -- table-driven
    VHDL is a future enhancement; the ``table=True`` parameter is
    accepted for API symmetry but ignored.
    """

    def test_generates_code(self):
        # Act
        code = generate_vhdl("crc16-modbus")

        # Assert
        assert code is not None, "generator returned code"
        assert "package crc16_modbus_pkg" in code, "package header present"
        assert "function crc16_modbus(" in code, "compute function declared"
        assert (
            "function crc16_modbus_self_test return boolean" in code
        ), "self_test function declared"
        assert "ieee.numeric_std" in code, "uses numeric_std for unsigned arithmetic"
        assert "0x4B37" in code or "19255" in code, "self_test checks against reveng value"

    def test_unknown_algorithm(self):
        # Assert
        assert generate_vhdl("nonexistent") is None, "unknown algorithm returns None"

    def test_table_parameter_accepted_but_ignored(self):
        # Act -- table=True should not raise; bit-by-bit is always emitted.
        bit_code = generate_vhdl("crc16-modbus", table=False)
        table_code = generate_vhdl("crc16-modbus", table=True)

        # Assert
        actual = table_code
        expected = bit_code
        assert actual == expected, (
            "table=True must produce identical output to table=False (ignored param)"
        )


class TestGenerators:
    def test_all_languages_present(self):
        # Assert
        assert set(GENERATORS.keys()) == {"c", "python", "rust", "vhdl"}, (
            "expected c, python, rust, vhdl generators"
        )

    @pytest.mark.parametrize("lang", ["c", "python", "rust", "vhdl"])
    def test_reflected_algorithm(self, lang):
        """Verify reflected algorithms (refin=True) generate code."""
        # Act - crc16-modbus is reflected
        result = GENERATORS[lang]("crc16-modbus")

        # Assert -- C returns a (header, source) pair; others return a string.
        assert result is not None, f"{lang} generator returned None for reflected algorithm"
        body = "".join(result) if isinstance(result, tuple) else result
        assert len(body) > 100, "non-trivial output"

    @pytest.mark.parametrize("lang", ["c", "python", "rust", "vhdl"])
    def test_normal_algorithm(self, lang):
        """Verify normal algorithms (refin=False) generate code."""
        # Act - crc16-xmodem is normal
        result = GENERATORS[lang]("crc16-xmodem")

        # Assert -- C returns a (header, source) pair; others return a string.
        assert result is not None, f"{lang} generator returned None for normal algorithm"
        body = "".join(result) if isinstance(result, tuple) else result
        assert len(body) > 100, "non-trivial output"
