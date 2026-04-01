"""Visualizer plugin discovery and loading.

Pure functions and classes with no Textual or pyserial dependencies.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class VisualizerInfo:
    """Metadata and formatting functions for a packet visualizer.

    Visualizers use the column-based API:
    - ``format_columns(data)`` returns ``(headers, values)``
    - ``diff_columns(actual, expected, mask)`` returns
      ``(headers, expected_values, actual_values, statuses)``

    Attributes:
        name: Display label for checkbox / table header.
        description: Tooltip text describing the visualizer.
        sort_order: Controls checkbox ordering (lower = first).
        format_columns: Returns (headers, values) for data bytes.
        diff_columns: Returns (headers, exp_vals, act_vals, statuses).
        format_spec: Returns the raw format spec string for data bytes.
        source: Where the visualizer was loaded from.
    """

    name: str
    description: str
    sort_order: int
    format_columns: Callable[
        [bytes], tuple[list[str], list[str]]
    ]
    diff_columns: Callable[
        [bytes, bytes, bytes],
        tuple[list[str], list[str], list[str], list[str]],
    ]
    format_spec: Callable[[bytes], str] = lambda data: ""
    source: str = "built-in"


def builtins_viz_dir() -> Path:
    """Return the path to the built-in visualizer directory."""
    return Path(__file__).parent / "builtins" / "viz"


def load_visualizers_from_dir(
    folder: Path, source: str = "global"
) -> list[VisualizerInfo]:
    """Discover and load visualizer .py files from a directory.

    Each file must define ``NAME`` (str), ``format_columns(data)``, and
    ``diff_columns(actual, expected, mask)``. ``SORT_ORDER`` (int) and
    ``DESCRIPTION`` (str) are optional.

    Files starting with ``_`` are skipped. Files that fail to load
    print a warning to stderr.

    Args:
        folder: Directory to scan for .py visualizer files.
        source: Label for where the visualizer came from.

    Returns:
        List of VisualizerInfo, one per valid visualizer file found.
    """
    visualizers: list[VisualizerInfo] = []
    if not folder.is_dir():
        return visualizers
    for py_file in sorted(folder.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        try:
            info = _load_visualizer_file(py_file, source)
            if info:
                visualizers.append(info)
        except Exception as e:
            print(
                f"termapy: failed to load visualizer {py_file.name}: {e}",
                file=sys.stderr,
            )
    return visualizers


def _load_visualizer_file(path: Path, source: str) -> VisualizerInfo | None:
    """Import a single visualizer file and extract its VisualizerInfo.

    A valid visualizer module must define ``NAME`` (str),
    ``format_columns`` (callable), and ``diff_columns`` (callable).
    Optional: ``SORT_ORDER`` (int, default 50), ``DESCRIPTION``
    (str, default ``""``).

    Args:
        path: Path to the .py visualizer file.
        source: Label for the visualizer's origin.

    Returns:
        VisualizerInfo if the file is a valid visualizer, None otherwise.
    """
    module_name = f"termapy_viz_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    name = getattr(mod, "NAME", None)
    fmt_cols = getattr(mod, "format_columns", None)
    diff_cols = getattr(mod, "diff_columns", None)

    if (
        not isinstance(name, str)
        or not callable(fmt_cols)
        or not callable(diff_cols)
    ):
        return None

    fmt_spec = getattr(mod, "format_spec", None)
    return VisualizerInfo(
        name=name,
        description=getattr(mod, "DESCRIPTION", ""),
        sort_order=getattr(mod, "SORT_ORDER", 50),
        format_columns=fmt_cols,
        diff_columns=diff_cols,
        format_spec=fmt_spec if callable(fmt_spec) else lambda data: "",
        source=source,
    )
