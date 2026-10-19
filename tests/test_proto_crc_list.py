"""``/proto.crc.list {pattern}``: the CRC catalogue filtered by a name pattern."""

from __future__ import annotations

from termapy.repl import ReplEngine


def _names(line: str) -> list[str]:
    """The algorithm names ``line`` answers with, through the real dispatch."""
    engine = ReplEngine({}, "", lambda text, color=None: None)
    result = engine.dispatch(line)
    assert result.success, result.error
    return result.value.splitlines() if result.value else []


def test_plain_text_is_contained():
    """name-one-matcher: modbus finds crc16-modbus without a wildcard, case ignored."""
    assert "crc16-modbus" in _names("proto.crc.list MODBUS")


def test_a_glob_is_anchored():
    """name-one-matcher: crc16* lists only names starting crc16."""
    names = _names("proto.crc.list crc16*")
    assert names, "the crc16 family is listed"
    assert all(name.startswith("crc16") for name in names), "anchored at the start"


def test_a_pattern_matching_nothing_is_an_empty_answer():
    """name-one-matcher: no algorithm matching succeeds with no names, not a failure."""
    assert _names("proto.crc.list zzz*") == []
