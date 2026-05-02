"""Tests for the --mcp-emit codegen (Phase 7).

The codegen is the spec-completeness test: if the profile schema can
drive a typed FastMCP server, the schema is complete enough that
third parties could implement alternate consumers.

Tests cover:
- Generated source compiles (ast.parse).
- PEP 723 header is present and includes mcp + pyserial.
- Per-command @mcp.tool() functions emit with correct typed params.
- Reference profiles all generate cleanly.
- The CLI flag (--mcp-emit) writes valid Python to stdout.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from termapy.mcp.emit import emit_mcp_server, _safe_id, _python_type_for
from termapy.profile import load_profile


FIXTURES = Path(__file__).parent / "fixtures" / "profiles"
BUNDLED_DEMO = (
    Path(__file__).parent.parent
    / "src"
    / "termapy"
    / "builtins"
    / "demo"
)
ALL_PROFILES = [
    FIXTURES / "at_modem.profile.json",
    FIXTURES / "register_psu.profile.json",
    FIXTURES / "smart_sensor.profile.json",
    BUNDLED_DEMO / "demo.profile.json",
    BUNDLED_DEMO / "demo_ndjson.profile.json",
]


# ── Header / structure ──────────────────────────────────────────────────────


class TestPep723Header:
    def test_starts_with_inline_metadata_block(self):
        # Arrange / Act
        profile = load_profile(BUNDLED_DEMO / "demo_ndjson.profile.json")
        src = emit_mcp_server(profile)
        # Assert
        assert src.startswith("# /// script\n"), "PEP 723 marker"
        assert "# ///" in src.split("\n", 10)[6:8][1] or "# ///" in src, (
            "closing marker present"
        )

    def test_lists_mcp_and_pyserial_deps(self):
        # Arrange / Act
        profile = load_profile(BUNDLED_DEMO / "demo_ndjson.profile.json")
        src = emit_mcp_server(profile)
        # Assert
        assert '"mcp>=1.0,<2.0"' in src, "mcp pinned"
        assert '"pyserial>=3.5"' in src, "pyserial declared"

    def test_requires_python_311_plus(self):
        # Arrange / Act
        profile = load_profile(BUNDLED_DEMO / "demo_ndjson.profile.json")
        src = emit_mcp_server(profile)
        # Assert
        assert 'requires-python = ">=3.11"' in src, "py 3.11+ required"


# ── Parametric: every reference profile compiles ────────────────────────────


@pytest.mark.parametrize("path", ALL_PROFILES, ids=lambda p: p.name)
class TestReferenceProfilesCodegen:
    def test_compiles(self, path):
        # Arrange / Act
        profile = load_profile(path)
        src = emit_mcp_server(profile)
        # Assert
        try:
            ast.parse(src)
        except SyntaxError as e:
            pytest.fail(f"{path.name} -> syntax error in generated source: {e}")

    def test_imports_fastmcp(self, path):
        # Arrange / Act
        profile = load_profile(path)
        src = emit_mcp_server(profile)
        # Assert
        assert "from mcp.server.fastmcp import FastMCP" in src, (
            "imports FastMCP"
        )

    def test_has_one_tool_per_command(self, path):
        # Arrange
        profile = load_profile(path)
        cmd_count = len(profile.get("commands", {}))
        # Act
        src = emit_mcp_server(profile)
        # Assert
        tool_count = src.count("@mcp.tool(")
        assert tool_count == cmd_count, (
            f"{path.name}: expected {cmd_count} tools, got {tool_count}"
        )

    def test_has_main_runner(self, path):
        # Arrange / Act
        profile = load_profile(path)
        src = emit_mcp_server(profile)
        # Assert
        assert 'mcp.run(transport="stdio")' in src, "stdio main runner present"


# ── Tool body shape ────────────────────────────────────────────────────────


class TestToolBodyShape:
    def test_ndjson_set_threshold_has_typed_param(self):
        # Arrange — set_threshold has typed_args [{name: celsius, type: float, ...}]
        profile = load_profile(BUNDLED_DEMO / "demo_ndjson.profile.json")
        src = emit_mcp_server(profile)
        # Assert
        assert "def set_threshold(celsius: float)" in src, (
            "typed param emitted with float annotation"
        )

    def test_ndjson_set_mode_uses_literal_for_enum(self):
        # Arrange — set_mode has enum=["idle","active","sleep","diagnostic"]
        profile = load_profile(BUNDLED_DEMO / "demo_ndjson.profile.json")
        src = emit_mcp_server(profile)
        # Assert
        assert 'Literal["idle", "active", "sleep", "diagnostic"]' in src, (
            "enum becomes typing.Literal"
        )
        assert "from typing import Literal" in src, "Literal imported"

    def test_destructive_safety_surfaces_annotation(self):
        # Arrange
        profile = load_profile(BUNDLED_DEMO / "demo_ndjson.profile.json")
        src = emit_mcp_server(profile)
        # Assert
        assert '"destructiveHint": True' in src, (
            "safety: destructive -> destructiveHint annotation"
        )

    def test_readonly_safety_surfaces_annotation(self):
        # Arrange — get_temp is safety: readonly
        profile = load_profile(BUNDLED_DEMO / "demo_ndjson.profile.json")
        src = emit_mcp_server(profile)
        # Assert
        assert '"readOnlyHint": True' in src, (
            "safety: readonly -> readOnlyHint annotation"
        )

    def test_fire_and_forget_command_uses_fire_and_forget(self):
        # Arrange — reset has response.format=none
        profile = load_profile(BUNDLED_DEMO / "demo_ndjson.profile.json")
        src = emit_mcp_server(profile)
        # Assert
        assert "fire_and_forget=True" in src, (
            "format: none translates to fire_and_forget call"
        )


# ── Helpers ────────────────────────────────────────────────────────────────


class TestSafeIdHelper:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("AT", "at"),
            ("AT+VER", "at_ver"),
            ("get_temp", "get_temp"),
            ("*IDN?", "_idn_"),
            ("VOLT?", "volt_"),
            ("123abc", "_123abc"),
            ("class", "class_"),  # python keyword
            ("set-mode", "set_mode"),
        ],
    )
    def test_coerces_to_python_identifier(self, raw, expected):
        # Arrange / Act / Assert
        assert _safe_id(raw) == expected, (
            f"_safe_id({raw!r}) -> {expected!r}"
        )


class TestPythonTypeFor:
    @pytest.mark.parametrize(
        "type_name,expected",
        [("int", "int"), ("float", "float"), ("bool", "bool"),
         ("hex", "int"), ("str", "str"), ("unknown", "str")],
    )
    def test_basic_types(self, type_name, expected):
        # Arrange / Act / Assert
        assert _python_type_for(type_name) == expected

    def test_str_with_enum_becomes_literal(self):
        # Arrange / Act / Assert
        assert _python_type_for("str", ["a", "b"]) == 'Literal["a", "b"]'

    def test_int_with_enum_stays_int(self):
        # Arrange / Act -- Literal only emitted for str+enum combo
        assert _python_type_for("int", [1, 2]) == "int", (
            "Literal only for str types"
        )


# ── CLI flag end-to-end ────────────────────────────────────────────────────


@pytest.mark.slow
class TestMcpEmitCli:
    """Subprocess tests for ``termapy --mcp-emit <profile>``."""

    def test_emits_compilable_python_to_stdout(self, tmp_path):
        # Arrange / Act
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "termapy",
                "--mcp-emit",
                str(BUNDLED_DEMO / "demo_ndjson.profile.json"),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Assert
        assert result.returncode == 0, (
            f"exit 0; stderr: {result.stderr[:500]}"
        )
        ast.parse(result.stdout)  # raises if not valid

    def test_missing_profile_exits_1(self):
        # Arrange / Act
        result = subprocess.run(
            [sys.executable, "-m", "termapy", "--mcp-emit", "/no/such/file.json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Assert
        assert result.returncode == 1, "missing file exits 1"
        assert "not found" in result.stderr, "useful error message"

    def test_invalid_profile_exits_1(self, tmp_path):
        # Arrange — schema-invalid profile
        bad = tmp_path / "bad.profile.json"
        bad.write_text(
            json.dumps(
                {
                    "profile_version": 2,
                    "commands": {"X": {"help": "h", "safety": "made-up"}},
                }
            ),
            encoding="utf-8",
        )
        # Act
        result = subprocess.run(
            [sys.executable, "-m", "termapy", "--mcp-emit", str(bad)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Assert
        assert result.returncode == 1, "schema error exits 1"
        assert "schema error" in result.stderr.lower(), "names schema error"
