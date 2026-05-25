"""Tests for the ``file=stem`` option on ``/proto.crc.<lang>`` commands.

The CLI command ``/proto.crc.<lang> <algo> file=STEM`` redirects the
generated source to disk instead of stdout -- the right shape for
invoking from a Makefile rule or other build automation.  C writes
``STEM.h`` + ``STEM.c``; the other languages each write a single
``STEM.<ext>`` (.py / .rs / .vhd).

These tests exercise ``_write_crc_codegen_files`` directly with
synthetic generator output so the behaviour is verified without
having to spin up a full PluginContext.  End-to-end CLI-dispatch
verification is covered by manual smoke (the dispatch path uses
``ctx.dispatch`` which has its own test surface).
"""

from __future__ import annotations

from termapy.builtins.commands.proto import _write_crc_codegen_files


class TestWriteCrcCodegenFiles:
    """``_write_crc_codegen_files`` writes the right number of files
    with the right extensions and the right content per language."""

    def test_python_writes_single_py_file(self, tmp_path):
        # Act
        written = _write_crc_codegen_files(
            "def crc16_modbus(data): return 0", "python", "my_crc", tmp_path,
        )

        # Assert
        actual = [p.name for p in written]
        expected = ["my_crc.py"]
        assert actual == expected, f"python writes one .py file; got {actual}"
        assert written[0].read_text() == "def crc16_modbus(data): return 0", (
            "file content matches generator output"
        )

    def test_rust_writes_single_rs_file(self, tmp_path):
        # Act
        written = _write_crc_codegen_files(
            "fn crc16_modbus(data: &[u8]) -> u16 { 0 }",
            "rust", "my_crc", tmp_path,
        )

        # Assert
        actual = [p.name for p in written]
        expected = ["my_crc.rs"]
        assert actual == expected, f"rust writes one .rs file; got {actual}"

    def test_vhdl_writes_single_vhd_file(self, tmp_path):
        # Act
        written = _write_crc_codegen_files(
            "package my_crc_pkg is end package;",
            "vhdl", "my_crc", tmp_path,
        )

        # Assert
        actual = [p.name for p in written]
        expected = ["my_crc.vhd"]
        assert actual == expected, f"vhdl writes one .vhd file; got {actual}"

    def test_c_writes_header_and_source_pair(self, tmp_path):
        # Arrange -- generate_c returns a (header, source) tuple
        header = "/* header */\nint crc16_modbus(...);\n"
        source = "/* source */\nint crc16_modbus(...) { return 0; }\n"

        # Act
        written = _write_crc_codegen_files(
            (header, source), "c", "my_crc", tmp_path,
        )

        # Assert -- exactly two files, .h first then .c
        actual_names = [p.name for p in written]
        expected_names = ["my_crc.h", "my_crc.c"]
        assert actual_names == expected_names, (
            f"C writes .h + .c (in that order); got {actual_names}"
        )
        assert written[0].read_text() == header, "header content matches"
        assert written[1].read_text() == source, "source content matches"

    def test_stem_with_path_prefix(self, tmp_path):
        """Stem can be a relative path including subdirectories.

        Callers pass ``cwd`` as the base, so a stem like ``out/my_crc``
        writes to ``<cwd>/out/my_crc.py`` -- but only if the subdir
        already exists.  We don't auto-mkdir (matches pycrc and other
        codegen tools: the user is responsible for the output dir).
        """
        # Arrange
        sub = tmp_path / "out"
        sub.mkdir()

        # Act
        written = _write_crc_codegen_files(
            "code", "python", "out/my_crc", tmp_path,
        )

        # Assert
        actual = written[0]
        expected = tmp_path / "out" / "my_crc.py"
        assert actual == expected, (
            f"stem with subdir prefix writes into the subdir; "
            f"actual={actual}, expected={expected}"
        )
        assert actual.read_text() == "code", "content written correctly"
