"""Render a 2-level file tree with box-drawing connectors.

Single source of truth for the tree shape used by ``/cfg.info``,
``/cfg.help``, ``/run.help``, and ``/proto.help``.

Pure module: no Textual or pyserial deps.  Output is either Rich
markup (``color=True``) or bare ASCII (``color=False``) suitable
for a markdown ``text`` code fence.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from termapy.scripting import format_size

# ── File metadata helpers (moved from cfg.py) ─────────────────────────────────


def _fmt_time(ts: float) -> str:
    """Format a Unix timestamp as 'YYYY-MM-DD HH:MM'."""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _created_time(st: os.stat_result) -> float:
    """Best-effort creation timestamp.

    macOS / *BSD / Windows (Python 3.12+) expose ``st_birthtime``.
    On Windows < 3.12, ``st_ctime`` is the NTFS creation time.
    On Linux without statx, no real birthtime is available -- fall
    back to mtime so the column is never blank.
    """
    if hasattr(st, "st_birthtime"):
        return st.st_birthtime
    if sys.platform == "win32":
        return st.st_ctime
    return st.st_mtime


def file_meta(path: Path) -> tuple[str, str, str]:
    """Return ``(size, created, modified)`` strings for a file.

    Returns ``("?", "?", "?")`` when the file can't be stat'd
    (deleted between glob and read, permission denied, etc.) so the
    tree never crashes mid-render.
    """
    try:
        st = path.stat()
    except OSError:
        return ("?", "?", "?")
    return (format_size(st.st_size), _fmt_time(_created_time(st)), _fmt_time(st.st_mtime))


# ── Tree renderer ─────────────────────────────────────────────────────────────


@dataclass
class FileTree:
    """A 2-level file-tree renderer.

    ``sections`` is a ``list[(name, files)]``.  Names ending in
    ``"/"`` are directories with ``files`` listed beneath; other
    names are top-level files (``files`` ignored).

    With ``file_dates=True`` each filename gets ``size / created /
    modified`` columns appended, and ``base_dir`` is required so the
    renderer can stat each file.

    With ``color=True`` (default) output uses Rich markup
    (``dim`` connectors, ``cyan`` directories, ``blue`` files); with
    ``color=False`` the output is plain ASCII suitable for a
    markdown ``text`` fence.

    ``name_width`` ljust-pads filenames so trailing metadata columns
    line up in a monospace fence.  Padding is applied **only when
    ``file_dates=True``** -- the no-dates variant has no trailing
    columns to align, and padding would just add invisible
    whitespace.
    """

    sections: list[tuple[str, list[str]]]
    base_dir: Path | None = None
    indent: str = ""
    file_dates: bool = False
    color: bool = True
    name_width: int = 0

    DIR_STYLE: str = field(default="cyan")
    FILE_STYLE: str = field(default="blue")
    TREE_STYLE: str = field(default="dim")

    def render(self) -> list[str]:
        """Return the tree as a list of lines (no trailing newlines)."""
        lines: list[str] = []
        n = len(self.sections)
        for i, (name, files) in enumerate(self.sections):
            is_last = i == n - 1
            connector = "└── " if is_last else "├── "
            child_indent = "    " if is_last else "│   "

            if name.endswith("/"):
                lines.append(self._dir_line(connector, name))
                folder_path = (
                    self.base_dir / name.rstrip("/")
                    if self.base_dir is not None
                    else None
                )
                for j, fname in enumerate(files):
                    child_conn = "└── " if j == len(files) - 1 else "├── "
                    fpath = folder_path / fname if folder_path is not None else None
                    lines.append(
                        self._file_line(child_indent + child_conn, fname, fpath)
                    )
            else:
                fpath = (
                    self.base_dir / name if self.base_dir is not None else None
                )
                lines.append(self._file_line(connector, name, fpath))
        return lines

    def _dir_line(self, connector: str, name: str) -> str:
        if self.color:
            return (
                f"{self.indent}[{self.TREE_STYLE}]{connector}[/]"
                f"[{self.DIR_STYLE}]{name}[/]"
            )
        return f"{self.indent}{connector}{name}"

    def _file_line(self, prefix: str, fname: str, fpath: Path | None) -> str:
        suffix = ""
        if self.file_dates and fpath is not None:
            size, created, modified = file_meta(fpath)
            suffix = f"  {size:>9}  {created}  {modified}"
        # Pad only when trailing columns are present (file_dates=True).
        # Padding without trailing columns would produce visible
        # trailing whitespace on every filename.
        padded = fname.ljust(self.name_width) if self.file_dates else fname
        if self.color:
            return (
                f"{self.indent}[{self.TREE_STYLE}]{prefix}[/]"
                f"[{self.FILE_STYLE}]{padded}[/]{suffix}"
            )
        return f"{self.indent}{prefix}{padded}{suffix}"
