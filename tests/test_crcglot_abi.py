"""Does crcglot's compiled extension run on THIS interpreter?

An environment check, not a re-test of crcglot.  crcglot ships one
``cp311-abi3`` wheel whose ``_c`` extension is meant to load on every
CPython from 3.11 up, and it falls back to pure Python silently when
the extension does not import.  A green suite therefore cannot tell
the two apart, and a new Python version is exactly what breaks a
compiled artifact.  So this file calls into the extension with one
known vector per entry point: one call per code path, never the
catalog.

The boundary: termapy tests the facts it promises (its own outputs)
and the environment it runs in (the installed wheel executes here);
it never enumerates or re-derives crcglot's facts (catalog, languages,
math).  Those are crcglot's own suite.

``crcglot._c`` is private to crcglot, documented only by its stub, so
this is the one place termapy names it.  The tests FAIL rather than
skip when it is missing: every platform termapy supports receives the
wheel, so a missing extension is a broken environment, not a
configuration.  When crcglot publishes an ``accelerated`` flag (noted
in its DECISIONS.md), replace the private import with that one public
assertion.
"""

from __future__ import annotations

import crcglot

# Modbus, not crc32: the IEEE CRC-32 algorithms route to zlib and would
# never touch the extension.
_ALGORITHM = "crc16-modbus"
_CHECK_INPUT = b"123456789"


def _params() -> dict:
    """The Rocksoft parameters of the probe algorithm, from the public catalog."""
    algo = crcglot.ALGORITHMS[_ALGORITHM]
    return {
        "width": algo.width,
        "poly": algo.poly,
        "init": algo.init,
        "refin": algo.refin,
        "refout": algo.refout,
        "xorout": algo.xorout,
    }


class TestCrcglotExtension:
    def test_extension_imports(self):
        # Act / Assert -- a loader error here is the abi3 wheel not working on this Python
        import crcglot._c  # noqa: F401

    def test_generic_crc_matches_catalog_check(self):
        # Arrange
        from crcglot import _c

        params = _params()
        expected = crcglot.ALGORITHMS[_ALGORITHM].check
        # Act
        actual = _c.c_generic_crc(
            _CHECK_INPUT,
            params["width"],
            params["poly"],
            params["init"],
            params["refin"],
            params["refout"],
            params["xorout"],
        )
        # Assert
        assert actual == expected, "the compiled engine must reproduce the catalog check value"

    def test_crc_stream_matches_catalog_check(self):
        # Arrange
        from crcglot import _c

        expected = crcglot.ALGORITHMS[_ALGORITHM].check
        stream = _c.CrcStream(**_params())
        # Act -- two chunks, so the running state crosses a call boundary
        stream.update(_CHECK_INPUT[:4])
        stream.update(_CHECK_INPUT[4:])
        actual = stream.digest()
        # Assert
        assert actual == expected, "the compiled stream must reproduce the catalog check value"
