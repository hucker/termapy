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

**A plugin file may export the same four names** and becomes a converter
for the config that loaded it -- same contract, same shape, only
discovery differs (``converter_from_module``).  That is what makes the
map-to-table step a PIPELINE the user owns: their ``convert`` can call a
built-in, drop what they don't want (statics, function addresses), and
add the typed rows a linker map cannot express -- SFRs with format specs
and ``rmw`` flags.  Because the script is the single producer, re-running
it reproduces the whole table and there is nothing to merge::

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

from dataclasses import dataclass
from typing import Any, Callable, Final, Iterable

from termapy.symbols.converters import xc32
from termapy.symbols.table import Symbol


@dataclass(frozen=True)
class ConverterSpec:
    """One registered converter.

    Attributes:
        format: Registry key and the ``format=`` value (``"xc32"``;
            ``"xc32-2.x"`` if versions ever diverge).
        detect: Substrings; ANY one present in the map text identifies
            the format.  Empty = never sniffed, ``format=`` only (the
            default for a plugin converter: it is one board's pipeline,
            not a toolchain, and its input may be a map a built-in would
            also claim).
        convert: Pure text-in / symbols-out function.
        description: One line for ``/help sym.import``.
        source: Where it came from -- ``"built-in"`` or the plugin layer
            (``"global"`` / the config name), so ``ReplEngine`` can drop
            folder-loaded converters on a config switch.
    """

    format: str
    detect: tuple[str, ...]
    convert: Callable[[str], list[Symbol]]
    description: str = ""
    source: str = "built-in"


# Order = sniffing precedence: a specific toolchain before a future generic
# gcc-map, so the specific one claims its maps first.
CONVERTERS: Final[tuple[ConverterSpec, ...]] = (
    ConverterSpec(xc32.FORMAT, xc32.DETECT, xc32.convert, xc32.DESCRIPTION),
)

# Derived: feeds the /sym.import format= enum and every error message.
FORMATS: Final[tuple[str, ...]] = tuple(spec.format for spec in CONVERTERS)


def find_converter(
    format_name: str = "",
    text: str = "",
    extra: Iterable[ConverterSpec] = (),
) -> ConverterSpec | None:
    """Pick a converter by explicit name, else by sniffing ``text``.

    The sniff scans the WHOLE text, not a window: the XC32 summary header
    sits ~49 KB into a real map, after the archive-member table, and
    ``in`` over a few MB is milliseconds.

    ``extra`` (the engine's plugin converters) is searched FIRST by name,
    so a config folder can override a built-in format for its own board.
    Sniffing still prefers the built-ins: a plugin converter opts in to
    detection by declaring a non-empty ``DETECT``, and the default empty
    tuple keeps it explicit-only.

    Args:
        format_name: Explicit registry key; empty = sniff.
        text: The map text to sniff.
        extra: Converters loaded from plugin folders.

    Returns:
        The spec, or None when nothing matches (the caller owns the error
        sentence).
    """
    if format_name:
        for spec in (*extra, *CONVERTERS):
            if spec.format == format_name:
                return spec
        return None
    for spec in (*CONVERTERS, *extra):
        if any(marker in text for marker in spec.detect):
            return spec
    return None


def converter_from_module(module: Any, source: str) -> ConverterSpec | None:
    """Build a spec from a plugin module's converter exports, or None.

    The four names a converter module exports are the ones the built-in
    converters already export (see the module docstring), so a plugin
    converter and ``xc32`` are the same shape -- only discovery differs.
    A module exporting no ``FORMAT`` simply has no converter; a module
    with a ``FORMAT`` but no callable ``convert`` is an authoring error
    and raises, matching the loader's fail-loud stance on a malformed
    export.

    Args:
        module: The imported plugin module.
        source: Layer label recorded on the spec.

    Returns:
        The spec, or None when the module declares no converter.

    Raises:
        ValueError: ``FORMAT`` is present but the rest is missing or the
            wrong type.
    """
    fmt = getattr(module, "FORMAT", None)
    if not isinstance(fmt, str) or not fmt:
        return None
    convert = getattr(module, "convert", None)
    if not callable(convert):
        raise ValueError(f"converter {fmt!r}: convert() is missing or not callable")
    detect = getattr(module, "DETECT", ())
    if not isinstance(detect, tuple) or not all(
        isinstance(marker, str) for marker in detect
    ):
        raise ValueError(f"converter {fmt!r}: DETECT must be a tuple of strings")
    description = getattr(module, "DESCRIPTION", "")
    if not isinstance(description, str):
        raise ValueError(f"converter {fmt!r}: DESCRIPTION must be a string")
    return ConverterSpec(fmt, detect, convert, description, source=source)
