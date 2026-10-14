"""The line ending on a cmd= capture is stripped only when it is one (/proto.crc.find and .reverse).

A text device ends each reply with a line ending that is not part of the
CRC'd data; a binary device's last CRC byte is 0x0A one frame in 256. The
capture helper used to strip every trailing LF, eating that CRC byte. The
end-to-end check on a real port is ``test_sw_loopback_crc_find.py``.
"""

from __future__ import annotations

from termapy.builtins.commands.proto import _strip_common_terminator


class TestStripCommonTerminator:
    """Strip a line ending only when every frame of the batch ends with it."""

    def test_a_crlf_on_every_frame_is_stripped(self):
        """A text device's CRLF comes off every frame."""
        actual = _strip_common_terminator([b"AB\r\n", b"CD\r\n"])
        assert actual == [b"AB", b"CD"]

    def test_an_lf_on_every_frame_is_stripped(self):
        """A bare-LF device's ending comes off every frame."""
        actual = _strip_common_terminator([b"AB\n", b"CD\n"])
        assert actual == [b"AB", b"CD"]

    def test_one_frame_ending_in_0a_keeps_its_crc_byte(self):
        """A binary batch with one CRC ending in 0x0A is left whole: that byte is data, not a terminator."""
        frames = [b"\x01\x02\x0a", b"\x01\x03\x7f"]
        actual = _strip_common_terminator(frames)
        assert actual == frames, "the strip ate the last CRC byte of one frame"

    def test_a_frame_that_is_only_the_ending_keeps_its_bytes(self):
        """Stripping never leaves an empty frame, nor a lone CR from a CRLF."""
        actual = _strip_common_terminator([b"\r\n"])
        assert actual == [b"\r\n"]
