"""CRC source-code generators for Python, C, Rust, and VHDL.

A focused sub-package whose only job is to turn a CRC algorithm name
(from :data:`termapy.protocol.crc.CRC_CATALOGUE`) into a complete,
runnable implementation file in a target language.  Each generated
implementation is verified by execution against the canonical reveng
``check`` value -- see ``tests/test_crc_codegen_exec.py``.

Public API
----------

- :func:`generate_python` -- returns a complete ``.py`` module string.
- :func:`generate_c` -- returns a ``(header, source)`` tuple of complete
  ``.h`` + ``.c`` files.  The header uses the ``extern "C"`` guard so
  the same code drops into C *and* C++ projects.
- :func:`generate_rust` -- returns a complete ``.rs`` file including an
  idiomatic ``#[cfg(test)] mod tests`` block.
- :func:`generate_vhdl` -- returns a complete ``.vhd`` package file
  with a ``_self_test`` boolean function callable from a testbench.
- :data:`GENERATORS` -- ``{"python", "c", "rust", "vhdl"}`` -> the
  matching ``generate_*`` callable, for language-parameterized callers
  (e.g. the ``/proto.crc.<lang>`` CLI subcommands).

Layout
------

One module per target language plus a shared ``_helpers`` module for
the language-agnostic primitives (function-name sanitization, hex
formatting, table pre-computation).  Adding a new target language is
a drop-in new file, no surgery in a monolithic generator.

Standalone-library positioning
------------------------------

This package, together with the CRC catalogue in
``termapy.protocol.crc``, is positioned for eventual extraction as
its own PyPI release (working name: ``crc-codegen``).  Nothing here
imports termapy-engine, MCP, or Textual; the only external dependency
is the catalogue itself.  Until then, the public API stays stable
and behind a small, language-organized facade.
"""

from __future__ import annotations

from typing import Callable

from termapy.protocol.crcgen.c import generate_c, generate_c_from_entry
from termapy.protocol.crcgen.python import generate_python, generate_python_from_entry
from termapy.protocol.crcgen.rust import generate_rust, generate_rust_from_entry
from termapy.protocol.crcgen.vhdl import generate_vhdl, generate_vhdl_from_entry


# Language code -> name-lookup generator callable.  Used by the CLI
# dispatcher (``/proto.crc.<lang>``) and by tests that parameterize
# over targets.
GENERATORS: dict[str, Callable] = {
    "c": generate_c,
    "python": generate_python,
    "rust": generate_rust,
    "vhdl": generate_vhdl,
}


# Language code -> entry-dict generator callable.  Used by the CLI
# dispatcher's custom-params path (``/proto.crc.<lang> width=N poly=X ...``)
# which builds a synthetic entry instead of looking up by name.
GENERATORS_FROM_ENTRY: dict[str, Callable] = {
    "c": generate_c_from_entry,
    "python": generate_python_from_entry,
    "rust": generate_rust_from_entry,
    "vhdl": generate_vhdl_from_entry,
}


__all__ = [
    "GENERATORS",
    "GENERATORS_FROM_ENTRY",
    "generate_c",
    "generate_c_from_entry",
    "generate_python",
    "generate_python_from_entry",
    "generate_rust",
    "generate_rust_from_entry",
    "generate_vhdl",
    "generate_vhdl_from_entry",
]
