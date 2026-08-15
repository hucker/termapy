"""CRC plugin loading and registry (termapy-side).

The pure CRC math and the named-algorithm catalog live in the
``crcglot`` package (a multi-language CRC code generator that's
independently usable).  This module re-exports the pure bits for
backward-compatible imports from termapy code, and adds the
termapy-specific concerns: ``CrcAlgorithm`` plugin loading from
``builtins/crc/`` and ``termapy_cfg/<name>/crc/`` directories, and
the lazily-built registry that the protocol toolkit consumes.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Adapt crcglot's public API to the shapes termapy's internal callers
# (get_crc_registry, proto.py) have historically consumed:
#
#   * crcglot exposes the typed ``ALGORITHMS`` (dict[str,
#     AlgorithmInfo]); termapy's registry code reads dict-of-dicts
#     ``CRC_CATALOGUE`` with ``entry["width"]`` access.  Rebuild that
#     legacy shape here so internal callers don't all have to change.
#   * Alias the compute helper to a leading-underscore name for
#     backward-compatible internal imports.
#
# New termapy code should prefer ``from crcglot import ALGORITHMS,
# generic_crc`` directly rather than these shims.
from crcglot import ALGORITHMS as _ALGORITHMS, Crc as _Crc, generic_crc as _generic_crc

from termapy.plugins import BoundaryException

CRC_CATALOGUE: dict[str, dict] = {
    name: {
        "width": a.width, "poly": a.poly, "init": a.init,
        "refin": a.refin, "refout": a.refout, "xorout": a.xorout,
        "check": a.check, "desc": a.desc,
    }
    for name, a in _ALGORITHMS.items()
}


def _make_crc_compute(entry: dict) -> Callable[[bytes], int]:
    """Create a compute closure for a catalog entry.

    Args:
        entry: Catalog dict with width, poly, init, refin, refout, xorout.

    Returns:
        Function ``(data: bytes) -> int``.
    """
    # crcglot 0.23 narrowed ``generic_crc`` to take a single ``Crc``
    # (or ``AlgorithmInfo``) value object instead of seven positional
    # ints.  Bind one ``Crc`` per entry at registry-build time so the
    # hot path is still a single function call.
    crc = _Crc(
        width=entry["width"], poly=entry["poly"], init=entry["init"],
        refin=entry["refin"], refout=entry["refout"], xorout=entry["xorout"],
    )
    return lambda data: _generic_crc(data, crc)


# ---------------------------------------------------------------------------
# CRC plugin discovery
# ---------------------------------------------------------------------------


@dataclass
class CrcAlgorithm:
    """Loaded CRC algorithm plugin.

    Attributes:
        name: Algorithm identifier (e.g. ``"crc16-modbus"``).
        width: CRC width in bytes (1, 2, or 4).
        compute: Function that computes the CRC value.
        refout: Reflect-output bit, also a strong indicator of the
            algorithm's natural wire order (``refout=True`` -> bytes
            flow low-first on the wire, like Modbus; ``refout=False``
            -> high-first, like XMODEM).  Defaults to ``False`` for
            plugin-supplied algorithms (sum8/sum16) which don't have a
            real reflection step.
    """

    name: str
    width: int
    compute: Callable[[bytes], int]
    refout: bool = False


def builtins_crc_dir() -> Path:
    """Return the path to the built-in CRC plugin directory.

    Resolved relative to the termapy package root (one level above
    this submodule), since the plugin dir lives at
    ``termapy/builtins/crc/``, not ``termapy/protocol/builtins/crc/``.
    """
    return Path(__file__).parent.parent / "builtins" / "crc"


def load_crc_plugins(
    *dirs: Path,
) -> dict[str, CrcAlgorithm]:
    """Discover and load CRC algorithm plugins from directories.

    Each .py file must define ``NAME`` (str), ``WIDTH`` (int),
    and ``compute(data: bytes) -> int``.

    Args:
        *dirs: Directories to scan. Later entries override earlier.

    Returns:
        Dict of algorithm name -> CrcAlgorithm.
    """
    algorithms: dict[str, CrcAlgorithm] = {}
    for folder in dirs:
        if not folder.is_dir():
            continue
        for py_file in sorted(folder.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            try:
                module_name = f"termapy_crc_{py_file.stem}"
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)

                name = getattr(mod, "NAME", None)
                width = getattr(mod, "WIDTH", None)
                compute_fn = getattr(mod, "compute", None)
                if (
                    isinstance(name, str)
                    and isinstance(width, int)
                    and callable(compute_fn)
                ):
                    algorithms[name] = CrcAlgorithm(
                        name=name, width=width, compute=compute_fn
                    )
            # Plugin file: third-party code being imported; its
            # top-level can raise anything (import errors, syntax,
            # missing CRC_ALGORITHM).  Report to stderr and keep
            # loading the rest.
            except BoundaryException as e:
                print(
                    f"termapy: failed to load CRC plugin {py_file.name}: {e}",
                    file=sys.stderr,
                )
    return algorithms


# Module-level CRC registry - populated on first use
_crc_registry: dict[str, CrcAlgorithm] | None = None


def get_crc_registry() -> dict[str, CrcAlgorithm]:
    """Get the CRC algorithm registry, loading catalog + plugins on first call.

    Build order: catalog entries first, then plugin files. Plugins override
    catalog entries of the same name.

    Returns:
        Dict of algorithm name -> CrcAlgorithm.
    """
    global _crc_registry
    if _crc_registry is None:
        # 1. Build from catalog
        registry: dict[str, CrcAlgorithm] = {}
        for name, entry in CRC_CATALOGUE.items():
            width_bytes = (entry["width"] + 7) // 8
            registry[name] = CrcAlgorithm(
                name=name, width=width_bytes,
                compute=_make_crc_compute(entry),
                refout=entry["refout"],
            )
        # 2. Overlay plugins (sum8, sum16, user-custom)
        registry.update(load_crc_plugins(builtins_crc_dir()))
        _crc_registry = registry
    return _crc_registry


def reset_crc_registry() -> None:
    """Reset the CRC registry, forcing reload on next access."""
    global _crc_registry
    _crc_registry = None
