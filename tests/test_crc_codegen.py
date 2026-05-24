"""Tests for CRC code generation - verify generated code computes correct CRC."""

from __future__ import annotations

import pytest

from termapy.protocol import generate_c, generate_python, generate_rust, GENERATORS
from termapy.protocol import CRC_CATALOGUE


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
    Rust testing -- ``cargo test`` discovers it, and termapy's pytest
    runs it via ``rustc --test``.  See ``test_crc_codegen_exec.py`` for
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


class TestGenerators:
    def test_all_languages_present(self):
        # Assert
        assert set(GENERATORS.keys()) == {"c", "python", "rust"}, "expected c, python, rust generators"

    @pytest.mark.parametrize("lang", ["c", "python", "rust"])
    def test_reflected_algorithm(self, lang):
        """Verify reflected algorithms (refin=True) generate code."""
        # Act - crc16-modbus is reflected
        result = GENERATORS[lang]("crc16-modbus")

        # Assert -- C returns a (header, source) pair; others return a string.
        assert result is not None, f"{lang} generator returned None for reflected algorithm"
        body = "".join(result) if isinstance(result, tuple) else result
        assert len(body) > 100, "non-trivial output"

    @pytest.mark.parametrize("lang", ["c", "python", "rust"])
    def test_normal_algorithm(self, lang):
        """Verify normal algorithms (refin=False) generate code."""
        # Act - crc16-xmodem is normal
        result = GENERATORS[lang]("crc16-xmodem")

        # Assert -- C returns a (header, source) pair; others return a string.
        assert result is not None, f"{lang} generator returned None for normal algorithm"
        body = "".join(result) if isinstance(result, tuple) else result
        assert len(body) > 100, "non-trivial output"
