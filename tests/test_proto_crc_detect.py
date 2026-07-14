"""Tests for /proto.crc.detect -- generated from crcglot.VERBS.

The command's params are generated from ``crcglot.VERBS['detect']`` and it
executes via ``crcglot.call_verb``, returning the JSON-ready wire dict as JSON
in ``CmdResult.value``.  These drive the real command through ``ReplEngine``
dispatch and prove only the termapy plumbing -- a frame in, the manifest-typed
params through, a structured result out.  crcglot owns (and tests) the maths.
"""
from __future__ import annotations

from termapy.repl import ReplEngine

MODBUS_FRAME = "01 03 00 00 00 0a c5 cd"


def _engine() -> ReplEngine:
    """A ReplEngine with the real builtins loaded and a no-op writer."""
    eng = ReplEngine({}, "", lambda t, c=None: None)
    eng.ctx.dispatch = eng.dispatch
    return eng


class TestCrcDetect:
    def test_identifies_modbus(self):
        # Act
        result = _engine().dispatch(f"proto.crc.detect {MODBUS_FRAME}")

        # Assert
        assert result.success, "detect ran cleanly"
        assert "crc16-modbus" in result.value, (
            "the wire-dict value carries the matched catalog algorithm"
        )

    def test_endian_enum_little(self):
        # Act -- 'little' is a crcglot enum value, typed straight from the manifest
        result = _engine().dispatch(f"proto.crc.detect endian=little {MODBUS_FRAME}")

        # Assert
        assert "crc16-modbus" in result.value, (
            "the manifest-generated endian enum reaches the little-endian detect"
        )

    def test_missing_frame_is_uniform_param_error(self):
        # Act
        result = _engine().dispatch("proto.crc.detect")

        # Assert
        assert not result.success, "a missing required frame fails"
        assert "missing required parameter 'frame'" in result.error, (
            "the manifest-declared frame param produces the uniform dispatcher error"
        )
