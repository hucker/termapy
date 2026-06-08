"""Tests for the ``style=`` kv arg and multi-algorithm bundling on
``/proto.crc.<lang>``.

These exercise the wrapper in
``termapy/builtins/commands/proto.py:_crc_codegen`` that:

* parses ``style=NAME`` from the kv tokens and validates it against
  the per-language allowed set from crcglot
  (``comment_style_for`` / ``styles_for_language``);
* threads the resulting ``comment_style`` kwarg into both the
  catalogue ``gen()`` and the custom ``gen_entry()`` paths;
* accepts multiple algorithm names as positional args, calling the
  generator once per algorithm and merging the results via the
  language's ``combiner`` callable on ``LanguageInfo``;
* rejects ``symbol=`` when more than one algorithm is supplied (the
  merged unit keeps each algorithm's own default symbol).

Pure crcglot-side codegen behavior (what the doc style actually looks
like, what the combined file contains byte-for-byte) lives in
crcglot's own tests; here we just verify the wrapper plumbs the
kwargs through.
"""

from __future__ import annotations

from termapy.builtins.commands.proto import _crc_codegen
from termapy.plugins import IOHandle, PluginContext


def _build_stub_ctx():
    """Minimal PluginContext that captures output() and output_markup().

    Returns (ctx, captured_text, captured_markup) where:
      * captured_text is a list of (text, color) tuples from output()
      * captured_markup is a list of strings from output_markup()
    """
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


class TestStyleKvArg:
    """``style=NAME`` is validated per-language and forwarded as
    ``comment_style`` into the crcglot generator."""

    def test_style_google_python_emits_google_docstring(self):
        # Arrange
        ctx, _captured, captured_markup = _build_stub_ctx()

        # Act
        result = _crc_codegen(ctx, "crc32 style=google", "python")

        # Assert -- handler succeeded and Google-style docstring markers
        # (Args: / Returns:) appear in the emitted source.
        assert result.success, (
            f"style=google python should succeed, got {result.error!r}"
        )
        emitted = "".join(captured_markup)
        assert "Args:" in emitted, (
            f"Google-style docstring should include Args:, got: {emitted[:300]!r}"
        )

    def test_style_doxygen_c_emits_doxygen_marker(self):
        # Arrange
        ctx, _captured, captured_markup = _build_stub_ctx()

        # Act
        result = _crc_codegen(ctx, "crc16-modbus style=doxygen", "c")

        # Assert
        assert result.success, (
            f"style=doxygen C should succeed, got {result.error!r}"
        )
        emitted = "".join(captured_markup)
        assert ("@brief" in emitted or "@param" in emitted), (
            f"Doxygen marker should appear in emitted C; got: {emitted[:300]!r}"
        )

    def test_style_rustdoc_rust_emits_rustdoc_marker(self):
        # Arrange -- rustdoc style emits ``///`` triple-slash blocks.
        ctx, _captured, captured_markup = _build_stub_ctx()

        # Act
        result = _crc_codegen(ctx, "crc32 style=rustdoc", "rust")

        # Assert
        assert result.success, (
            f"style=rustdoc rust should succeed, got {result.error!r}"
        )
        emitted = "".join(captured_markup)
        assert "///" in emitted, (
            f"rustdoc style should emit /// comments; got: {emitted[:300]!r}"
        )

    def test_style_invalid_for_python_rejected_with_allowed_list(self):
        # Arrange -- doxygen is a C/Java style; not valid for Python.
        ctx, _captured, _markup = _build_stub_ctx()

        # Act
        result = _crc_codegen(ctx, "crc32 style=doxygen", "python")

        # Assert -- error names the allowed Python set so the user can
        # pick a valid one without consulting docs.
        assert not result.success, "style=doxygen on python should fail"
        msg = result.error or ""
        assert "allowed for python" in msg, (
            f"error should list allowed styles; got: {msg!r}"
        )
        for expected in ("plain", "google", "numpy", "rest"):
            assert expected in msg, (
                f"allowed list should mention {expected}; got: {msg!r}"
            )

    def test_style_unknown_rejected(self):
        # Arrange -- "nonsense" isn't a real crcglot style.
        ctx, _captured, _markup = _build_stub_ctx()

        # Act
        result = _crc_codegen(ctx, "crc32 style=nonsense", "python")

        # Assert
        assert not result.success, "unknown style should fail"
        assert "Unknown style" in (result.error or ""), (
            f"error msg should say 'Unknown style'; got: {result.error!r}"
        )

    def test_style_empty_value_rejected(self):
        # Arrange -- ``style=`` with no value mirrors the file=/symbol=
        # empty-value errors.
        ctx, _captured, _markup = _build_stub_ctx()

        # Act
        result = _crc_codegen(ctx, "crc32 style=", "python")

        # Assert
        assert not result.success, "style= with empty value should fail"
        assert "requires a value" in (result.error or ""), (
            f"empty style= should mention 'requires a value'; "
            f"got: {result.error!r}"
        )


class TestMultiAlgorithmBundle:
    """Multiple positional algorithm names bundle into a single output."""

    def test_bundle_three_c_algorithms_stdout_has_all_names(self):
        # Arrange
        ctx, _captured, captured_markup = _build_stub_ctx()

        # Act
        result = _crc_codegen(ctx, "crc16-modbus crc32 crc8", "c")

        # Assert -- handler succeeded; every algorithm name appears in
        # the emitted (header, source) output.
        assert result.success, (
            f"3-algo C bundle should succeed, got {result.error!r}"
        )
        emitted = "".join(captured_markup).lower()
        for expected in ("crc16_modbus", "crc32", "crc8"):
            assert expected in emitted, (
                f"bundle output should include {expected}; "
                f"got first 400 chars: {emitted[:400]!r}"
            )

    def test_bundle_python_succeeds(self):
        # Arrange -- Python bundling exercises the string-returning
        # combiner path (distinct from C's tuple-returning combiner).
        ctx, _captured, captured_markup = _build_stub_ctx()

        # Act
        result = _crc_codegen(ctx, "crc16-modbus crc32", "python")

        # Assert
        assert result.success, (
            f"python bundle should succeed, got {result.error!r}"
        )
        emitted = "".join(captured_markup)
        assert len(emitted) > 0, "python bundle should emit some source"

    def test_bundle_rejects_symbol_override(self):
        # Arrange -- in bundle mode every algorithm keeps its own
        # default symbol; allowing one symbol= would be ambiguous.
        ctx, _captured, _markup = _build_stub_ctx()

        # Act
        result = _crc_codegen(
            ctx, "crc16-modbus crc32 symbol=foo", "c",
        )

        # Assert
        assert not result.success, "symbol= with bundle should fail"
        msg = result.error or ""
        assert "symbol=" in msg, (
            f"error should mention symbol=; got: {msg!r}"
        )
        assert "bundling" in msg or "multiple" in msg, (
            f"error should explain bundling; got: {msg!r}"
        )

    def test_bundle_unknown_algorithm_reports_which_one(self):
        # Arrange -- a single bad name among good ones should fail with
        # that name in the error, not a generic "unknown algorithm".
        ctx, _captured, _markup = _build_stub_ctx()

        # Act
        result = _crc_codegen(ctx, "crc32 nosuchcrc crc8", "c")

        # Assert
        assert not result.success, "bundle with bad name should fail"
        assert "nosuchcrc" in (result.error or ""), (
            f"error should name the bad algorithm; got: {result.error!r}"
        )

    def test_bundle_with_style_combines_styles(self):
        # Arrange -- style= applies to every algorithm in the bundle.
        ctx, _captured, captured_markup = _build_stub_ctx()

        # Act
        result = _crc_codegen(
            ctx, "crc16-modbus crc32 style=doxygen", "c",
        )

        # Assert
        assert result.success, (
            f"bundle + style=doxygen should succeed, got {result.error!r}"
        )
        emitted = "".join(captured_markup)
        assert ("@brief" in emitted or "@param" in emitted), (
            f"bundle should propagate doxygen style; got: {emitted[:300]!r}"
        )

    def test_bundle_to_file_writes_one_file_per_extension(self, tmp_path, monkeypatch):
        # Arrange -- file= mode writes the merged output to disk; the
        # combined unit goes to a single STEM.<ext> set (e.g. for C:
        # one my_crcs.h + one my_crcs.c containing both algorithms).
        monkeypatch.chdir(tmp_path)
        ctx, _captured, _markup = _build_stub_ctx()

        # Act
        result = _crc_codegen(
            ctx, "crc16-modbus crc32 file=my_crcs", "c",
        )

        # Assert
        assert result.success, (
            f"bundle + file= should succeed, got {result.error!r}"
        )
        h = tmp_path / "my_crcs.h"
        c = tmp_path / "my_crcs.c"
        assert h.exists(), f"expected my_crcs.h to be written; got: {list(tmp_path.iterdir())}"
        assert c.exists(), f"expected my_crcs.c to be written; got: {list(tmp_path.iterdir())}"
        c_text = c.read_text().lower()
        assert "crc16_modbus" in c_text, "crc16_modbus missing from merged .c"
        assert "crc32" in c_text, "crc32 missing from merged .c"
