"""Tests for /proto.crc.find ``form=NAME`` (crcglot 0.23+ payload forms).

A "form" is a CRC-bearing text wrapper (currently just ``crclink``: a
JSON object with a trailing ``"crc"`` field).  termapy passes ``form=``
through to ``crcglot.detect``; on a hit, the wrapper info shows up in
the rendered match line and a supplementary "Wrapper:" line carries
the recovered message text.

These tests exercise the wrapper plumbing, the validation guards
(unknown form, empty form, form+asc combo), and the render path.
The form-matching algorithm itself lives in crcglot.
"""

from __future__ import annotations

from termapy.builtins.commands.proto import _crc_find
from termapy.plugins import IOHandle, PluginContext

_CRCLINK_FRAME = '{"t":1234,"v":42,"crc":"1352"}'


def _build_stub_ctx():
    """Minimal PluginContext capturing output() and output_markup()."""
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


def _hex(text: str) -> str:
    """Space-separated lowercase hex bytes for a UTF-8 string."""
    return " ".join(f"{byte:02x}" for byte in text.encode())


class TestFormHappyPath:
    """A crclink frame passed via bin= + form=crclink identifies as
    crc16-xmodem with the wrapper info surfaced."""

    def test_crclink_frame_identifies_with_form_tag(self):
        # Arrange
        ctx, _captured, captured_markup = _build_stub_ctx()

        # Act
        result = _crc_find(ctx, f"form=crclink bin={_hex(_CRCLINK_FRAME)}")

        # Assert -- match line includes the algorithm + form tag
        assert result.success, f"crclink frame should match: {result.error!r}"
        match_line = "".join(captured_markup)
        assert "crc16-xmodem" in match_line, (
            f"crclink frame uses crc16-xmodem; got {match_line!r}"
        )
        assert "form=crclink" in match_line, (
            f"match line should advertise form=crclink; got {match_line!r}"
        )

    def test_crclink_match_shows_wrapper_info(self):
        # Arrange
        ctx, captured, _captured_markup = _build_stub_ctx()

        # Act
        _crc_find(ctx, f"form=crclink bin={_hex(_CRCLINK_FRAME)}")

        # Assert -- a "Wrapper:" line carries the recovered message
        wrapper_lines = [t for t, _color in captured if "Wrapper:" in t]
        assert wrapper_lines, (
            f"a Wrapper: line should accompany a form match; "
            f"got captured={captured}"
        )
        assert "crclink JSON frame" in wrapper_lines[0], (
            f"wrapper line should name the form; got {wrapper_lines[0]!r}"
        )
        assert '{"t":1234,"v":42,' in wrapper_lines[0], (
            f"wrapper line should carry the recovered message; "
            f"got {wrapper_lines[0]!r}"
        )


class TestFormValidation:
    """form= rejects bad inputs at the wrapper layer."""

    def test_unknown_form_rejected_with_known_list(self):
        # Arrange
        ctx, _captured, _markup = _build_stub_ctx()

        # Act
        result = _crc_find(ctx, "form=nosuch bin=01 02 03")

        # Assert -- error names what's known so the user can pick
        assert not result.success, "unknown form should fail"
        msg = result.error or ""
        assert "Unknown form: nosuch" in msg, (
            f"error should name the unknown form; got {msg!r}"
        )
        assert "crclink" in msg, (
            f"error should list crclink in known forms; got {msg!r}"
        )

    def test_empty_form_value_rejected(self):
        # Arrange
        ctx, _captured, _markup = _build_stub_ctx()

        # Act
        result = _crc_find(ctx, "form= bin=01 02 03")

        # Assert
        assert not result.success, "empty form= should fail"
        assert "requires a value" in (result.error or ""), (
            f"empty form= should mention 'requires a value'; "
            f"got {result.error!r}"
        )

    def test_form_with_asc_rejected(self):
        # Arrange -- asc= splits trailing hex; form= matches a full
        # wrapper.  The two are mutually exclusive by construction.
        ctx, _captured, _markup = _build_stub_ctx()

        # Act
        result = _crc_find(ctx, "form=crclink asc=foo")

        # Assert
        assert not result.success, "form= with asc= should fail"
        msg = result.error or ""
        assert "form= is not compatible with asc=" in msg, (
            f"error should explain the incompatibility; got {msg!r}"
        )
        assert "bin=" in msg, (
            f"error should point at bin= as the alternative; got {msg!r}"
        )


class TestFormBackwardCompat:
    """Without form=, the existing detect path is unchanged."""

    def test_no_form_means_bare_detect(self):
        # Arrange -- the canonical CRC-32 packet still identifies via
        # bare detection (no form= involved).
        ctx, _captured, captured_markup = _build_stub_ctx()

        # Act
        result = _crc_find(ctx, "bin=31 32 33 34 35 36 37 38 39 CB F4 39 26")

        # Assert
        assert result.success, f"bare detect should still work: {result.error!r}"
        match_line = "".join(captured_markup)
        assert "crc32" in match_line, (
            f"bare CRC32 packet should identify as crc32; got {match_line!r}"
        )
        assert "form=" not in match_line, (
            f"no form tag should appear when none matched; got {match_line!r}"
        )
