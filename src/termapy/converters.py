"""Converter registries -- vendor files in, termapy data out, one record type.

Two registries share this shape and this lookup:

- ``symbols/converters``  -- a linker map or an ELF -> ``list[Symbol]`` (``/sym.import``)
- ``devices/converters``  -- a vendor register description (CMSIS-SVD, ...)
                             -> a device document              (``/dev.import``)

A converter is four module-level names, the same for a built-in module and
for a plugin file in ``plugin/``::

    FORMAT: str                          # registry key ("xc32", "svd")
    DESCRIPTION: str                     # one line for /help
    DETECT: tuple[str, ...]              # substrings; any one identifies the format
    def convert(text: str): ...          # pure; never raises on odd input

plus an optional fifth, ``KIND`` -- ``"symbols"`` (the default, so every
existing plugin converter keeps working) or ``"device"`` -- which says
which import the converter serves.  The two kinds are separate namespaces:
a device converter named ``svd`` and a symbol converter named ``svd`` do not
collide, and ``/sym.import`` never offers a device converter as a map format.

A binary format (an ELF, say) adds ``MAGIC: bytes``, its file signature:
its ``convert`` receives the file's bytes instead of its text, and it is
recognized by ``data.startswith(MAGIC)`` BEFORE any text marker is tried,
because a binary file can contain another format's marker (an XC32 ELF
holds "/xc32/" in its debug strings).  A binary converter's ``DETECT`` is
ignored.

A symbol converter may also export ``def layout(source)``, what the file
says about the table as a whole (``termapy.symbols.Layout``: its memory
regions, and the target's address width and byte order when the format
states them), pure and never raising like ``convert``.  Without it a table
has no regions and the default width and byte order.

Each registry keeps its own ``CONVERTERS`` tuple and wraps
:func:`find_converter` with it; the plugin loader uses
:func:`converter_from_module` and hands every kind to the engine, which
keys them by ``(kind, format)``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from pathlib import Path

SYMBOLS: Final[str] = "symbols"
DEVICE: Final[str] = "device"
KINDS: Final[tuple[str, ...]] = (SYMBOLS, DEVICE)


@dataclass(frozen=True)
class ConverterSpec:
    """One registered converter.

    Attributes:
        format: Registry key and the ``format=`` value.
        detect: Substrings; ANY one present in the input identifies the
            format.  Empty = never sniffed, ``format=`` only (the default
            for a plugin converter: it is one board's pipeline, not a
            toolchain, and its input may be a file a built-in would also
            claim).
        convert: Pure source-in / data-out function: the file's text, or
            its bytes for a binary format (``magic``).
        description: One line for ``/help``.
        source: Where it came from -- ``"built-in"`` or the plugin layer
            (``"global"`` / the config name), so ``ReplEngine`` can drop
            folder-loaded converters on a config switch.
        kind: Which import this serves (:data:`KINDS`).
        layout: A symbol converter's optional source-in / ``Layout``-out
            function; None when the format states nothing about the table.
        magic: The file signature of a binary format, empty for text.
    """

    format: str
    detect: tuple[str, ...]
    convert: Callable[[Any], Any]
    description: str = ""
    source: str = "built-in"
    kind: str = SYMBOLS
    layout: Callable[[Any], Any] | None = None
    magic: bytes = b""

    def source_of(self, data: bytes, text: str) -> bytes | str:
        """What this converter reads: the file's bytes for a binary format, else its text."""
        return data if self.magic else text


def read_source(path: Path) -> tuple[bytes, str]:
    """A build file's bytes and its text, read once for every converter.

    The text is decoded with ``errors="replace"`` -- a map with one stray
    byte still converts -- and doubles as the sniff input for the text
    formats; the bytes are what a binary format reads and sniffs.

    Raises:
        OSError: The file could not be read.
    """
    data = path.read_bytes()
    return data, data.decode("utf-8", errors="replace")


def find_converter(
    format_name: str = "",
    text: str = "",
    extra: Iterable[ConverterSpec] = (),
    *,
    registry: Iterable[ConverterSpec] = (),
    data: bytes = b"",
) -> ConverterSpec | None:
    """Pick a converter by explicit name, else by sniffing the input.

    A binary format is recognized first, by its ``magic`` at the start of
    ``data``: a signature is decisive, a text marker is only a substring,
    and a binary file can carry another format's marker inside it.

    The sniff scans the WHOLE text, not a window: a marker may sit deep in
    a large file (the XC32 summary header is ~49 KB into a real map), and
    ``in`` over a few MB is milliseconds.

    ``extra`` (the engine's plugin converters, already filtered to one
    kind) is searched FIRST by name, so a config folder can override a
    built-in format for its own board.  Sniffing still prefers the
    built-ins: a plugin converter opts in to detection by declaring a
    non-empty ``DETECT``.

    Args:
        format_name: Explicit registry key; empty = sniff.
        text: The input text to sniff.
        extra: Converters loaded from plugin folders.
        registry: The built-in converters of one kind.
        data: The input's bytes, for a binary format's signature.

    Returns:
        The spec, or None when nothing matches (the caller owns the error
        sentence).
    """
    extras = tuple(extra)
    builtins = tuple(registry)
    if format_name:
        for spec in (*extras, *builtins):
            if spec.format == format_name:
                return spec
        return None
    for spec in (*builtins, *extras):
        if spec.magic and data.startswith(spec.magic):
            return spec
    for spec in (*builtins, *extras):
        if not spec.magic and any(marker in text for marker in spec.detect):
            return spec
    return None


def converter_from_module(module: Any, source: str) -> ConverterSpec | None:
    """Build a spec from a plugin module's converter exports, or None.

    A module exporting no ``FORMAT`` simply has no converter; a module with
    a ``FORMAT`` but no callable ``convert``, or a ``KIND`` outside
    :data:`KINDS`, is an authoring error and raises, matching the loader's
    fail-loud stance on a malformed export.

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
    if not isinstance(detect, tuple) or not all(isinstance(marker, str) for marker in detect):
        raise ValueError(f"converter {fmt!r}: DETECT must be a tuple of strings")
    description = getattr(module, "DESCRIPTION", "")
    if not isinstance(description, str):
        raise ValueError(f"converter {fmt!r}: DESCRIPTION must be a string")
    kind = getattr(module, "KIND", SYMBOLS)
    if kind not in KINDS:
        raise ValueError(f"converter {fmt!r}: KIND must be one of {', '.join(KINDS)}")
    layout = getattr(module, "layout", None)
    if layout is not None and not callable(layout):
        raise ValueError(f"converter {fmt!r}: layout must be a function")
    magic = getattr(module, "MAGIC", b"")
    if not isinstance(magic, bytes):
        raise ValueError(f"converter {fmt!r}: MAGIC must be bytes")
    return ConverterSpec(
        fmt,
        detect,
        convert,
        description,
        source=source,
        kind=kind,
        layout=layout,
        magic=magic,
    )
