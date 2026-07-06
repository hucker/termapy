"""Tests for /proto.crc.detect -- single-frame catalogue CRC identification.

Drives the real command through ``ReplEngine`` dispatch (params ->
``crcglot.detect``) and asserts the wiring: a known frame resolves to its
catalogue name, the ``endian`` enum alias works, and a missing frame yields
the uniform param error.  crcglot owns the detection maths (and tests it);
these prove only that termapy plumbs a frame in and a name out.
"""
from __future__ import annotations

from termapy.repl import ReplEngine

# A real CRC-16/MODBUS frame: payload 01 03 00 00 00 0A + little-endian
# trailer C5 CD (built with crcglot in the scratchpad, verified by detect).
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
            "value names the matched catalogue algorithm so it can pipe onward"
        )

    def test_endian_alias_le_resolves(self):
        # Act -- 'le' is an alias for the canonical 'little'
        result = _engine().dispatch(f"proto.crc.detect endian=le {MODBUS_FRAME}")

        # Assert
        assert "crc16-modbus" in result.value, "le alias reaches the little-endian detect"

    def test_missing_frame_is_uniform_param_error(self):
        # Act
        result = _engine().dispatch("proto.crc.detect")

        # Assert
        assert not result.success, "a missing required frame fails"
        assert "missing required parameter 'frame'" in result.error, (
            "the declared param produces the uniform dispatcher error"
        )
