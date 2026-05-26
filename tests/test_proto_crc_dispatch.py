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


# One row per (lang, variant) the wrapper supports.  Python --slice8
# is its own test (separate file) because it tests the FALLBACK behaviour,
# not the dispatch path; VHDL doesn't have --table or --slice8.
_DISPATCH_CELLS = [
    ("c", "bitbybit", {}),
    ("c", "table", {"--table": True}),
    ("c", "slice8", {"--slice8": True}),
    ("python", "bitbybit", {}),
    ("python", "table", {"--table": True}),
    ("rust", "bitbybit", {}),
    ("rust", "table", {"--table": True}),
    ("rust", "slice8", {"--slice8": True}),
    ("vhdl", "bitbybit", {}),
]


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
