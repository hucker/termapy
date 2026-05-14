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
    def test_generates_code(self):
        # Act
        code = generate_c("crc16-modbus")

        # Assert
        assert code is not None, "generator returned code"
        assert "uint16_t" in code, "correct type"
        assert "crc16_modbus" in code, "function name"
        assert "0x4B37" in code, "check value in comment"

    def test_unknown_algorithm(self):
        # Assert
        assert generate_c("nonexistent") is None, "unknown algorithm should return None"

    def test_crc8_uses_uint8(self):
        # Act
        code = generate_c("crc8")

        # Assert
        assert code is not None, "generator returned code"
        assert "uint8_t" in code, "CRC-8 should use uint8_t"

    def test_crc32_uses_uint32(self):
        # Act
        code = generate_c("crc32")

        # Assert
        assert code is not None, "generator returned code"
        assert "uint32_t" in code, "CRC-32 should use uint32_t"


class TestGenerateRust:
    def test_generates_code(self):
        # Act
        code = generate_rust("crc16-modbus")

        # Assert
        assert code is not None, "generator returned code"
        assert "fn crc16_modbus" in code, "function name"
        assert "u16" in code, "correct type"
        assert "0x4B37" in code, "check value"

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
        code = GENERATORS[lang]("crc16-modbus")

        # Assert
        assert code is not None, f"{lang} generator returned None for reflected algorithm"
        assert len(code) > 100, "non-trivial output"

    @pytest.mark.parametrize("lang", ["c", "python", "rust"])
    def test_normal_algorithm(self, lang):
        """Verify normal algorithms (refin=False) generate code."""
        # Act - crc16-xmodem is normal
        code = GENERATORS[lang]("crc16-xmodem")

        # Assert
        assert code is not None, f"{lang} generator returned None for normal algorithm"
        assert len(code) > 100, "non-trivial output"
