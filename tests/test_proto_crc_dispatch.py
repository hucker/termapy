"""Smoke tests for the ``/proto.crc.<lang>`` REPL handlers.

Termapy's role for CRC code generation is **dispatch**: parse REPL
args, set the right flags, hand off to crcglot.  Generator
correctness (every algorithm in every language matching the reveng
check value) is crcglot's own test suite -- running over a thousand
exec tests per push -- and termapy does not re-verify it.

These tests therefore check ONE thing per language × variant:
"the wrapper dispatched and produced non-empty output."  If that
holds, the connection between termapy's REPL and the installed
crcglot package is intact; whether the output is correct is a
crcglot question.

The matrix is one row per (lang, variant) pair, not one row per
(algorithm, lang, variant) cell.  See the architectural note in
[ARCHITECTURE.md] under "Test boundaries after crcglot extraction".
"""

from __future__ import annotations

import pytest

from termapy.builtins.commands.proto import _crc_codegen


def _build_stub_ctx():
    """Minimal ctx that captures ``ctx.io.output*`` calls.

    Enough of the PluginContext surface for ``_crc_codegen`` to run
    without crashing -- an IOHandle with capturing _write callbacks
    plus an empty active_flags dict (tests mutate this per case).
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


def _dispatch_cells():
    """One (lang, variant, flags) row per language × native variant.

    Derived from ``crcglot.LANGUAGES`` so every language crcglot ships
    (and every future one) is smoke-tested automatically -- the test
    matches termapy's now-dynamic /proto.crc.<lang> registration.  We
    test only NATIVE variants here; the --slice8 FALLBACK behaviour
    (Python, and any future table-but-no-slice8 language) is covered by
    its own test in test_proto_crc_custom.py.
    """
    from crcglot import LANGUAGES
    cells = []
    for code, info in sorted(LANGUAGES.items()):
        cells.append((code, "bitwise", {}))
        if "table" in info.variants:
            cells.append((code, "table", {"--table": True}))
        if "slice8" in info.variants:
            cells.append((code, "slice8", {"--slice8": True}))
    return cells


_DISPATCH_CELLS = _dispatch_cells()


class TestDispatchSmoke:
    """One smoke per (language, variant) -- proves the wrapper routes."""

    @pytest.mark.parametrize("lang,variant,flags", _DISPATCH_CELLS)
    def test_dispatch_produces_output(self, lang, variant, flags):
        # Arrange
        ctx, _captured, captured_markup = _build_stub_ctx()
        ctx.active_flags = flags

        # Act -- use crc32 as the canonical "any algorithm" smoke target.
        # Coverage of OTHER algorithms is crcglot's job, not termapy's.
        result = _crc_codegen(ctx, "crc32", lang)

        # Assert -- handler succeeded and produced non-empty output via
        # the markup channel (which is where generated code lands).
        assert result.success, (
            f"/proto.crc.{lang} crc32 ({variant}) should succeed, "
            f"got error: {result.error!r}"
        )
        emitted = "".join(captured_markup)
        assert emitted, (
            f"/proto.crc.{lang} crc32 ({variant}) produced no markup output"
        )
        # Tiny content check: the algorithm name should appear in the
        # output (header comment, function name, or docstring -- exact
        # location is crcglot's concern, presence is termapy's).
        assert "crc32" in emitted, (
            f"/proto.crc.{lang} crc32 ({variant}) output should mention "
            f"the algorithm name; got: {emitted[:200]!r}"
        )


class TestUnsupportedVariantFlagRejected:
    """Bitwise-only languages reject --table / --slice8 with a clear error.

    These languages (verilog, vhdl in crcglot 0.8.0) register no variant
    flags, so the dispatcher passes a stray --table/--slice8 through as a
    bare token.  _crc_codegen catches it and errors rather than silently
    ignoring the flag and emitting bitwise anyway.
    """

    @pytest.mark.parametrize("lang", ["vhdl", "verilog"])
    @pytest.mark.parametrize("flag", ["--table", "--slice8"])
    def test_bitwise_only_rejects_variant_flag(self, lang, flag):
        # Arrange -- pass the unsupported flag as a bare arg token (it
        # isn't a registered flag for these languages, so it arrives in
        # the args string, mimicking real dispatch).
        ctx, _captured, _markup = _build_stub_ctx()

        # Act
        result = _crc_codegen(ctx, f"crc32 {flag}", lang)

        # Assert -- clean failure naming the rejected flag, not silent
        # success.
        assert not result.success, (
            f"/proto.crc.{lang} {flag} should be rejected, not silently "
            f"ignored"
        )
        assert flag in (result.error or ""), (
            f"error should name the rejected flag {flag!r}; "
            f"got {result.error!r}"
        )

    def test_native_flag_still_accepted(self):
        # Guard against over-rejection: a language that DOES support the
        # flag must still accept it (c has native --slice8).
        ctx, _captured, captured_markup = _build_stub_ctx()
        ctx.active_flags = {"--slice8": True}
        result = _crc_codegen(ctx, "crc32", "c")
        assert result.success, (
            f"c --slice8 should still work, got {result.error!r}"
        )
        assert "".join(captured_markup), "c --slice8 should produce output"
