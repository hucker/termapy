"""Tests for termapy's CLI handler around the CRC codegen surface.

These tests cover the wrapper layer inside
``termapy/builtins/commands/proto.py`` that:

* parses ``width=N poly=X ...`` tokens from the CLI args string
  (``_parse_int_value`` / ``_parse_bool_value``)
* resolves the function symbol from ``symbol=`` or ``file=``
  (``_symbol_from_stem``)
* falls back from ``--slice8`` to ``--table`` for Python (with a
  user-visible note) since slice-by-8 is empirically slower than
  table-driven in CPython

Pure crcglot tests of the generators themselves live in
``test_crcglot.py``, ``test_crcglot_custom.py``, and
``test_crcglot_exec.py``.
"""

from __future__ import annotations

import pytest

from termapy.builtins.commands.proto import (
    _parse_bool_value,
    _parse_int_value,
    _symbol_from_stem,
)


class TestParseHelpers:
    """The custom-CRC CLI accepts ``key=value`` tokens; verify the
    helpers that parse them handle the edge cases the rest of the
    handler relies on."""

    def test_parse_int_decimal(self):
        # Act / Assert
        assert _parse_int_value("16", "width") == 16, "decimal int"
        assert _parse_int_value("0", "init") == 0, "zero"
        assert _parse_int_value("4294967295", "xorout") == 4294967295, "32-bit max"

    def test_parse_int_hex(self):
        # Act / Assert -- hex with and without 0x prefix
        assert _parse_int_value("0x1021", "poly") == 0x1021, "0x-prefixed"
        assert _parse_int_value("0X1021", "poly") == 0x1021, "0X-prefixed"
        assert _parse_int_value("0xFFFFFFFF", "xorout") == 0xFFFFFFFF, (
            "0x-prefixed full-width"
        )

    def test_parse_int_invalid(self):
        # Act / Assert -- invalid strings should raise ValueError
        # (the underlying int() call propagates "invalid literal ...")
        with pytest.raises(ValueError, match="invalid literal"):
            _parse_int_value("not_a_number", "width")

    @pytest.mark.parametrize("token", ["true", "True", "TRUE", "1"])
    def test_parse_bool_truthy(self, token):
        # Act / Assert
        assert _parse_bool_value(token, "refin") is True, f"{token!r} -> True"

    @pytest.mark.parametrize("token", ["false", "False", "FALSE", "0"])
    def test_parse_bool_falsy(self, token):
        # Act / Assert
        assert _parse_bool_value(token, "refin") is False, f"{token!r} -> False"

    def test_parse_bool_invalid(self):
        # Act / Assert
        with pytest.raises(ValueError, match="true/false"):
            _parse_bool_value("maybe", "refin")

    def test_symbol_from_stem_basename(self):
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

    def test_symbol_from_stem_sanitizes(self):
        # Act / Assert -- dashes and dots become underscores; other
        # characters that aren't C-identifier-safe also get normalized.
        assert _symbol_from_stem("crc-32") == "crc_32", "dash -> underscore"
        assert _symbol_from_stem("my.crc") == "my_crc", "dot -> underscore"


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
        # The emitted code should be table-driven (a 256-entry lookup
        # table), NOT slice-by-8 (would contain CRC_SLICE_TABLES).
        # crcglot 0.12 renamed the table variable from the generic
        # ``_TABLE`` to a per-algorithm ``_crcglot_table_<name>`` for
        # symbol uniqueness; check the family pattern instead of the
        # exact old name.
        emitted = "".join(captured_markup)
        assert "_crcglot_table_" in emitted, (
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


class TestCustomParamsDispatch:
    """``/proto.crc.<lang> width=N poly=X ...`` (custom Rocksoft/Williams
    parameters, no catalogue lookup) dispatches and produces output.

    Regression guard for two things:
      1. crcglot 0.8.0's ``*_from_entry`` generators take a typed
         ``AlgorithmInfo``, not a dict -- termapy builds the dataclass.
      2. Custom params + C + stdout (no file=) previously crashed on an
         unbound ``name`` in the stdout banner; it's unified with the
         catalogue branch's ``name`` now.
    """

    def _build_stub_ctx(self):
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

    def test_custom_params_c_stdout(self):
        # Arrange -- C output (tuple result) to stdout exercises the
        # banner path that referenced the formerly-unbound ``name``.
        from termapy.builtins.commands.proto import _crc_codegen
        ctx, _captured, captured_markup = self._build_stub_ctx()

        # Act -- crc16-ccitt params via the custom path, no file=.
        result = _crc_codegen(
            ctx,
            "width=16 poly=0x1021 init=0xFFFF refin=true refout=true "
            "xorout=0x0000 name=mycrc",
            "c",
        )

        # Assert
        assert result.success, (
            f"custom-params C/stdout should succeed, got {result.error!r}"
        )
        emitted = "".join(captured_markup)
        assert "mycrc" in emitted, (
            f"custom name should appear in generated C; got {emitted[:200]!r}"
        )

    def test_custom_params_builds_algorithm_info(self):
        # Arrange -- Python output (string result) confirms the
        # AlgorithmInfo handoff to *_from_entry works.
        from termapy.builtins.commands.proto import _crc_codegen
        ctx, _captured, captured_markup = self._build_stub_ctx()

        # Act
        result = _crc_codegen(
            ctx,
            "width=16 poly=0x8005 init=0xFFFF refin=true refout=true "
            "xorout=0x0000 name=cust16",
            "python",
        )

        # Assert
        assert result.success, (
            f"custom-params Python should succeed, got {result.error!r}"
        )
        emitted = "".join(captured_markup)
        assert "cust16" in emitted, (
            f"custom name should appear in generated Python; "
            f"got {emitted[:200]!r}"
        )
