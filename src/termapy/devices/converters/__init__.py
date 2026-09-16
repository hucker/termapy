"""Device converters -- a vendor's register description in, a device document out.

The device-file twin of ``symbols/converters``: one master tuple, derived
views, the record type and lookup shared through :mod:`termapy.converters`.
A converter's ``convert(text)`` returns a device DOCUMENT (the dict shape
``termapy.devices.parse_device`` validates), never a ``Device``: the file
``/dev.import`` writes is that document, so a converted part and a
hand-written one go through the same validation and the file on disk is
the source of truth.

A plugin file adds a vendor format with the same four names plus
``KIND = "device"`` -- Microchip's EDC ``.PIC`` XML for a PIC32, say --
without a PR.
"""

from __future__ import annotations

from typing import Final, Iterable

from termapy.converters import DEVICE, ConverterSpec, find_converter as _find_converter
from termapy.devices.converters import svd

CONVERTERS: Final[tuple[ConverterSpec, ...]] = (
    ConverterSpec(svd.FORMAT, svd.DETECT, svd.convert, svd.DESCRIPTION, kind=DEVICE),
)

FORMATS: Final[tuple[str, ...]] = tuple(spec.format for spec in CONVERTERS)


def find_converter(
    format_name: str = "", text: str = "", extra: Iterable[ConverterSpec] = (),
) -> ConverterSpec | None:
    """The device registry's lookup -- see :func:`termapy.converters.find_converter`."""
    return _find_converter(format_name, text, extra, registry=CONVERTERS)
