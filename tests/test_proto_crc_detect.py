"""Tests for /proto.crc.detect -- generated from crcglot.VERBS.

The command's params are generated from ``crcglot.VERBS['detect']`` and it
executes via ``crcglot.call_verb``, returning the JSON-ready wire dict as JSON
in ``CmdResult.value``.  ``detect`` accepts a frame LIST, so the renderer gives
it two input forms: a variadic ``<frames>...`` positional (one frame per
argument) and a ``frame=`` rest keyword (one frame that may contain spaces).

These drive the real command through ``ReplEngine`` dispatch and prove only the
termapy plumbing -- frames in, manifest-typed params through, a structured
result out.  crcglot owns (and tests) the maths, and owns the rule that the two
input forms are mutually exclusive.
"""
from __future__ import annotations

from termapy import variables as var
from termapy.repl import ReplEngine

# Modbus frames.  SPACED is the historical single-frame spelling; the unspaced
# forms are distinct frames that intersect to one algorithm.
SPACED_FRAME = "01 03 00 00 00 0a c5 cd"
FRAME_1 = "010300000002c40b"
FRAME_2 = "01030000000305cb"
FRAME_3 = "0103000000044409"


def _engine() -> ReplEngine:
    """A ReplEngine with the real builtins loaded and a no-op writer."""
    eng = ReplEngine({}, "", lambda t, c=None: None)
    eng.ctx.dispatch = eng.dispatch
    return eng


def _dispatch_full(eng: ReplEngine, line: str):
    """Route through the FULL pipeline (directives + transforms + dispatch).

    ``$(NAME)`` splice happens in ``dispatch_full``; ``$(*NAME)`` dereference
    happens further down in the param binder, so it is reachable from plain
    ``dispatch``.  That split is the design -- the two forms live on different
    layers -- so a splice test must enter here or it proves nothing.
    """
    return eng.dispatch_full(
        line,
        log=lambda d, t: None,
        echo_markup=lambda t: None,
        status=lambda t, c="": None,
        serial_write=lambda d: None,
        serial_write_raw=lambda t: None,
        is_connected=lambda: False,
        eol_label=lambda le: "",
    )


class TestCrcDetectFrames:
    """The variadic ``<frames>...`` positional."""

    def test_identifies_modbus_single_frame(self):
        # Act
        result = _engine().dispatch(f"proto.crc.detect {FRAME_1}")

        # Assert
        assert result.success, "detect ran cleanly"
        assert "crc16-modbus" in result.value, (
            "the wire-dict value carries the matched catalog algorithm"
        )

    def test_multiple_frames_intersect(self):
        # Act -- several frames is the whole point: one frame alone is often
        # ambiguous, and crcglot intersects candidates across the set
        result = _engine().dispatch(
            f"proto.crc.detect {FRAME_1} {FRAME_2} {FRAME_3}"
        )

        # Assert
        assert result.success, "detect ran cleanly over three frames"
        assert "crc16-modbus" in result.value, "three frames identify modbus"

    def test_byte_separators_inside_one_frame(self):
        # Act -- commas keep a single frame in ONE argument, which is the
        # no-variable replacement for the old space-separated spelling
        result = _engine().dispatch("proto.crc.detect 01,03,00,00,00,0a,c5,cd")

        # Assert
        assert "crc16-modbus" in result.value, (
            "comma-separated bytes stay one frame and identify modbus"
        )

    def test_no_input_is_a_usage_error(self):
        # Act
        result = _engine().dispatch("proto.crc.detect")

        # Assert -- termapy answers in ITS argument names, not crcglot's
        # manifest names (packet_hex / packet_text), which no user types
        assert not result.success, "a bare invocation fails"
        assert "Usage:" in result.error, "the synthesized synopsis is shown"
        assert "packet_hex" not in result.error, (
            "the arity error is termapy's, so it must not leak manifest names"
        )


class TestCrcDetectFrameKeyword:
    """The ``frame=`` rest keyword -- one frame that may contain spaces."""

    def test_spaced_frame_via_keyword(self):
        # Act -- pasting a spaced frame from a log needs no variable round-trip
        result = _engine().dispatch(f"proto.crc.detect frame={SPACED_FRAME}")

        # Assert
        assert result.success, "frame= accepted a space-separated frame"
        assert "crc16-modbus" in result.value, "the spaced frame identifies modbus"

    def test_keyword_composes_with_other_keywords_before_it(self):
        # Act -- frame= runs to end of line, so other keywords precede it
        result = _engine().dispatch(f"proto.crc.detect width=16 frame={SPACED_FRAME}")

        # Assert
        assert "crc16-modbus" in result.value, (
            "a keyword before frame= binds normally"
        )

    def test_both_input_forms_is_crcglot_error(self):
        # Act
        result = _engine().dispatch(
            f"proto.crc.detect {FRAME_1} frame={SPACED_FRAME}"
        )

        # Assert -- the exclusion is crcglot's rule; termapy surfaces it
        # verbatim rather than policing it itself
        assert not result.success, "supplying both input forms fails"
        assert "not both" in result.error, "crcglot's exclusion message surfaces"


class TestCrcDetectVariableForms:
    """``$(NAME)`` splice and ``$(*NAME)`` dereference, on the real command."""

    def test_plain_splice_still_reaches_detect(self):
        # Arrange -- REGRESSION GUARD.  An earlier prototype set raw_args=True
        # on detect to get per-token dereference, which switched the blanket
        # $() transform off for the whole command; $(F) then arrived literal,
        # failed hex parsing, and reported "No match found" -- a wrong answer,
        # not an error.  Nothing in the suite caught it.
        eng = _engine()
        var._VARS["F"] = FRAME_1

        # Act -- through the FULL pipeline, which is where splice happens
        result = _dispatch_full(eng, "/proto.crc.detect $(F)")

        # Assert
        assert result.success, "detect ran cleanly"
        assert "crc16-modbus" in result.value, (
            "a $(NAME) splice must still expand for detect -- guards against "
            "raw_args=True disabling the blanket transform"
        )
        var._VARS.clear()

    def test_splice_and_deref_agree_on_a_clean_value(self):
        # Arrange -- a value with no spaces: the two forms must reach the same
        # answer, which is what makes deref a safe drop-in for clean data
        eng = _engine()
        var._VARS["F"] = FRAME_1

        # Act
        spliced = _dispatch_full(eng, "/proto.crc.detect $(F)")
        dereffed = _dispatch_full(eng, "/proto.crc.detect $(*F)")

        # Assert
        assert spliced.value == dereffed.value, (
            "on a whitespace-free value the two forms are interchangeable"
        )
        var._VARS.clear()

    def test_deref_binds_one_frame_per_reference(self):
        # Arrange
        eng = _engine()
        var._VARS.update({"p1": FRAME_1, "p2": FRAME_2, "p3": FRAME_3})

        # Act
        result = eng.dispatch("proto.crc.detect $(*p1) $(*p2) $(*p3)")

        # Assert
        assert "crc16-modbus" in result.value, "three dereferenced frames identify modbus"
        var._VARS.clear()

    def test_deref_value_with_spaces_stays_one_frame(self):
        # Arrange -- the arity-1 guarantee, on the real command.  Spliced, this
        # value would fork into eight arguments and match nothing.
        eng = _engine()
        var._VARS["spaced"] = SPACED_FRAME

        # Act
        result = eng.dispatch("proto.crc.detect $(*spaced)")

        # Assert
        assert "crc16-modbus" in result.value, (
            "a dereferenced value containing spaces binds as ONE frame"
        )
        var._VARS.clear()

    def test_mixed_literal_and_reference(self):
        # Arrange
        eng = _engine()
        var._VARS["p2"] = FRAME_2

        # Act
        result = eng.dispatch(f"proto.crc.detect {FRAME_1} $(*p2) {FRAME_3}")

        # Assert
        assert "crc16-modbus" in result.value, "literal and reference tokens mix"
        var._VARS.clear()

    def test_unknown_reference_errors(self):
        # Act
        result = _engine().dispatch("proto.crc.detect $(*nope)")

        # Assert -- a deref asserts the name exists, so a typo fails loudly
        # rather than reaching crcglot as the literal text "$(*nope)"
        assert not result.success, "an undefined reference fails"
        assert "unknown variable: 'nope'" in result.error, (
            "the failure names the missing variable"
        )

    def test_embedded_reference_errors(self):
        # Act
        result = _engine().dispatch("proto.crc.detect x$(*p1)y")

        # Assert
        assert not result.success, "a partial reference fails"
        assert "invalid reference" in result.error, (
            "a reference embedded in a larger token is a mistake, not a literal"
        )


class TestCrcDetectKeywordTyping:
    """Keyword coercion, which hand-rolled parsing had silently dropped."""

    def test_width_is_coerced_to_int(self):
        # Act -- crcglot rejects a STRING width, so an uncoerced "16" silently
        # produced matched=False; this pins the int coercion
        result = _engine().dispatch(f"proto.crc.detect {FRAME_1} width=16")

        # Assert
        assert result.success, "width=16 dispatched cleanly"
        assert "crc16-modbus" in result.value, "an int-coerced width still matches"

    def test_bad_width_fails_before_crcglot(self):
        # Act
        result = _engine().dispatch(f"proto.crc.detect {FRAME_1} width=abc")

        # Assert
        assert not result.success, "a non-integer width fails"
        assert "invalid width: 'abc'" in result.error, (
            "the dispatcher validates the type before calling crcglot"
        )

    def test_endian_enum_little(self):
        # Act -- 'little' is a crcglot enum value, typed straight from the manifest
        result = _engine().dispatch(f"proto.crc.detect endian=little {FRAME_1}")

        # Assert
        assert "crc16-modbus" in result.value, (
            "the manifest-generated endian enum reaches the little-endian detect"
        )
