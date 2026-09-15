"""Converter registry -- vendor map text in, termapy symbols out.

Table-driven like ``folders.FOLDERS``: one frozen record type, one master
tuple, derived views.  Vendor knowledge lives only in the converter
modules; this file knows names and sniff strings.

A converter module exposes four names, all required at import so an
incomplete module fails the registry import loudly::

    FORMAT: str                          # registry key ("xc32")
    DESCRIPTION: str                     # one line for /help sym.import
    DETECT: tuple[str, ...]              # substrings; any one identifies the format
    def convert(text: str) -> list[Symbol]   # pure; never raises on odd input

Adding a toolchain = one module, one tuple entry, one fixture under
``tests/fixtures/maps/`` (the pairwise-exclusivity test picks it up).
Step-by-step instructions, written to be handed to an LLM together with
a map file: ``docs/symbol-converter-guide.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Final

from termapy.symbols.converters import xc32
from termapy.symbols.table import Symbol


@dataclass(frozen=True)
class ConverterSpec:
    """One registered converter.

    Attributes:
        format: Registry key and the ``format=`` value (``"xc32"``;
            ``"xc32-2.x"`` if versions ever diverge).
        detect: Substrings; ANY one present in the map text identifies
            the format.
        convert: Pure text-in / symbols-out function.
        description: One line for ``/help sym.import``.
    """

    format: str
    detect: tuple[str, ...]
    convert: Callable[[str], list[Symbol]]
    description: str = ""


# Order = sniffing precedence: a specific toolchain before a future generic
# gcc-map, so the specific one claims its maps first.
CONVERTERS: Final[tuple[ConverterSpec, ...]] = (
    ConverterSpec(xc32.FORMAT, xc32.DETECT, xc32.convert, xc32.DESCRIPTION),
)

# Derived: feeds the /sym.import format= enum and every error message.
FORMATS: Final[tuple[str, ...]] = tuple(spec.format for spec in CONVERTERS)


def find_converter(format_name: str = "", text: str = "") -> ConverterSpec | None:
    """Pick a converter by explicit name, else by sniffing ``text``.

    The sniff scans the WHOLE text, not a window: the XC32 summary header
    sits ~49 KB into a real map, after the archive-member table, and
    ``in`` over a few MB is milliseconds.

    Args:
        format_name: Explicit registry key; empty = sniff.
        text: The map text to sniff.

    Returns:
        The spec, or None when nothing matches (the caller owns the error
        sentence).
    """
    if format_name:
        return next((spec for spec in CONVERTERS if spec.format == format_name), None)
    for spec in CONVERTERS:
        if any(marker in text for marker in spec.detect):
            return spec
    return None
