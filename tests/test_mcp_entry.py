"""Tests for the --mcp / --mcp-verbose entry-point wiring (Phase 1).

The MCP server itself isn't built yet; these tests verify the entry
flag plumbing: parser accepts the flags, dispatch reaches the stub,
the stub exits cleanly when the SDK is present (or with a useful hint
when it isn't), and importing termapy.entry doesn't pull in the mcp
SDK unintentionally.
"""

from __future__ import annotations

import subprocess
import sys

import pytest


# ── Argparse: flags exist and default False ─────────────────────────────────


class TestArgparse:
    def test_mcp_flag_parses(self):
        # Arrange
        from termapy.entry import _build_parser

        parser = _build_parser()
        # Act
        args = parser.parse_args(["--mcp"])
        # Assert
        assert args.mcp is True, "--mcp parses to True"

    def test_mcp_verbose_flag_parses(self):
        # Arrange
        from termapy.entry import _build_parser

        parser = _build_parser()
        # Act
        args = parser.parse_args(["--mcp", "--mcp-verbose"])
        # Assert
        assert args.mcp is True, "--mcp set"
        assert args.mcp_verbose is True, "--mcp-verbose set"

    def test_mcp_default_false(self):
        # Arrange
        from termapy.entry import _build_parser

        parser = _build_parser()
        # Act
        args = parser.parse_args([])
        # Assert
        assert args.mcp is False, "--mcp defaults False"
        assert args.mcp_verbose is False, "--mcp-verbose defaults False"


# ── Lazy import: mcp SDK not loaded by entry ────────────────────────────────


class TestLazyImport:
    def test_entry_import_does_not_pull_mcp_sdk(self):
        """Importing termapy.entry must not import the heavy mcp package.

        Run a fresh subprocess so this test can't be polluted by sibling
        tests that may have imported the SDK already.
        """
        # Arrange / Act
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; import termapy.entry; "
                "print('mcp_loaded' if 'mcp' in sys.modules else 'mcp_absent')",
            ],
            capture_output=True,
            text=True,
        )
        # Assert
        assert result.returncode == 0, f"import failed: {result.stderr}"
        assert "mcp_absent" in result.stdout, (
            "mcp SDK must not be imported as a side effect of "
            "loading termapy.entry"
        )


# ── --mcp dispatch reaches the stub ─────────────────────────────────────────


class TestMcpDispatch:
    def test_mcp_dispatch_with_sdk_exits_zero(self):
        """--mcp on a system with the SDK installed exits cleanly (stub)."""
        # Arrange — only meaningful if the mcp SDK is importable.
        try:
            import mcp  # noqa: F401
        except ImportError:
            pytest.skip("mcp SDK not installed; nothing to dispatch")
        # Act
        result = subprocess.run(
            [sys.executable, "-m", "termapy", "--mcp"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Assert
        assert result.returncode == 0, (
            f"stub should exit 0 when SDK present; stderr: {result.stderr}"
        )

    def test_mcp_dispatch_without_sdk_prints_install_hint(self):
        """When the mcp SDK is missing, --mcp must give a clean install hint."""
        # Arrange — only meaningful when the SDK is NOT installed.
        try:
            import mcp  # noqa: F401
        except ImportError:
            pass
        else:
            pytest.skip(
                "mcp SDK is installed; cannot exercise the missing-SDK path"
            )
        # Act
        result = subprocess.run(
            [sys.executable, "-m", "termapy", "--mcp"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Assert
        assert result.returncode == 1, "missing SDK exits 1"
        assert "termapy[mcp]" in result.stderr, (
            "install hint mentions the extra: pip install termapy[mcp]"
        )

    def test_mcp_verbose_emits_stub_notice_to_stderr(self):
        """--mcp --mcp-verbose prints a one-line dev notice to stderr."""
        # Arrange — only when SDK installed (otherwise we exit before verbose path).
        try:
            import mcp  # noqa: F401
        except ImportError:
            pytest.skip("mcp SDK not installed; verbose path unreachable")
        # Act
        result = subprocess.run(
            [sys.executable, "-m", "termapy", "--mcp", "--mcp-verbose"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Assert
        assert result.returncode == 0, "stub still exits 0 in verbose mode"
        assert "Phase 1 stub" in result.stderr, (
            "verbose flag prints the stub notice to stderr"
        )
        assert result.stdout == "", (
            "stdout must stay clean -- it's reserved for MCP protocol frames"
        )
