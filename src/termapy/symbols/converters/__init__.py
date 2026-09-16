"""Symbol converters -- vendor map text in, termapy symbols out.

Table-driven like ``folders.FOLDERS``: one master tuple, derived views.
Vendor knowledge lives only in the converter modules; this file knows
names and sniff strings.  The record type and the lookup are shared with
the device registry -- see :mod:`termapy.converters` for the four names a
converter module exports and the optional ``KIND``.

Adding a toolchain = one module, one tuple entry, one fixture under
``tests/fixtures/maps/`` (the pairwise-exclusivity test picks it up; the
fixture stem before the first ``_`` must equal ``FORMAT``, so a key with
an underscore breaks it).  The authoring rules a converter must follow --
purity, VMA-not-LMA, the section vocabulary, DETECT uniqueness -- are in
``help/symbols.md`` under "Your own converter, as a plugin"; they are the
same for a built-in and a plugin converter.

**A plugin file may export the same four names** and becomes a converter
for the config that loaded it -- same contract, same shape, only
discovery differs.  That is what makes the map-to-table step a PIPELINE
the user owns: their ``convert`` can call a built-in, drop what they
don't want (statics, function addresses), and add the typed rows a linker
map cannot express.  Because the script is the single producer,
re-running it reproduces the whole table and there is nothing to merge::

    from termapy.symbols.converters import xc32

    FORMAT = "myboard"
    DESCRIPTION = "xc32 map, no statics, plus typed SFRs"
    DETECT = ()                 # explicit-only: /sym.import map= format=myboard

    def convert(text):
        rows = [s for s in xc32.convert(text) if s.section != "text"]
        return rows + SFR_ROWS

Plugin converters live on the ``ReplEngine`` (with a ``source`` label),
not in :data:`CONVERTERS`, so a config switch drops them exactly like
commands and transforms.
"""

from __future__ import annotations

from typing import Final, Iterable

from termapy.converters import SYMBOLS, ConverterSpec, find_converter as _find_converter
from termapy.symbols.converters import xc32

# Order = sniffing precedence: a specific toolchain before a future generic
# gcc-map, so the specific one claims its maps first.
CONVERTERS: Final[tuple[ConverterSpec, ...]] = (
    ConverterSpec(xc32.FORMAT, xc32.DETECT, xc32.convert, xc32.DESCRIPTION, kind=SYMBOLS),
)

# Derived: feeds the /sym.import format= help and every error message.
FORMATS: Final[tuple[str, ...]] = tuple(spec.format for spec in CONVERTERS)


def find_converter(
    format_name: str = "", text: str = "", extra: Iterable[ConverterSpec] = (),
) -> ConverterSpec | None:
    """The symbol registry's lookup -- see :func:`termapy.converters.find_converter`."""
    return _find_converter(format_name, text, extra, registry=CONVERTERS)
