"""Shared folder-operation handlers for per-config data folders.

Every top-level plugin that owns a data folder (run/, proto/, ss/,
cap/, prof/, viz/, plugin/, or the cfg config dir itself) exposes
the same uniform subcommand family:

  - ``list``    -- list files in the folder
  - ``explore`` -- open the folder in the system file explorer
  - ``show``    -- open the newest file in the system viewer (showable)
  - ``dump``    -- print the newest (or named) file to the terminal
                   (dumpable)
  - ``clear``   -- delete all files in the folder (clearable)

Which of ``show``, ``dump``, ``clear`` are exposed is driven by the
``showable / dumpable / clearable`` flags on each ``FolderSpec`` in
``termapy.folders`` -- no per-plugin decision, no duplication.

These factories used to live in ``cfg.py``.  They moved here so that
``ss.py``, ``cap.py``, ``proto.py``, and the ``/run.*`` hook registrations
can all reuse the same handler bodies instead of re-implementing the
folder operations per plugin.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from termapy.config import open_with_system
from termapy.folders import FOLDERS, FolderSpec
from termapy.plugins import CapabilitySet, CmdResult, Command
from termapy.scripting import format_age, format_size

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _mtime(path: Path) -> float:
    """Modification time, or 0.0 when the file vanished between glob and stat."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def list_entries(path: Path, pattern: str) -> list[Path]:
    """Files matching ``pattern`` in ``path``, newest first (name breaks ties).

    Dotfiles (``.cmd_history.txt``, ``.gitignore``) are never listed.
    Newest-first is the order that answers "which one did I just make?",
    the question every ``/x.list`` exists for.
    """
    if not path.is_dir():
        return []
    files = [
        file for file in path.glob(pattern)
        if file.is_file() and not file.name.startswith(".")
    ]
    return sorted(files, key=lambda file: (-_mtime(file), file.name))


def file_record(path: Path, *, now: float | None = None) -> dict[str, Any]:
    """Structured metadata for one file: the ``CmdResult.data`` twin of a listing line.

    Shared by the ``/x.list`` handlers and the MCP capture records so every
    structured consumer sees the same shape.  Numbers stay numbers -- the
    humanized strings are prose-only.

    Args:
        path: The file.
        now: Reference POSIX timestamp for ``age_s``; defaults to the current
            time.  Exists so tests can pin the output.
    """
    try:
        st = path.stat()
    except OSError:
        return {"name": path.name, "bytes": 0, "mtime": None, "age_s": None}
    reference = time.time() if now is None else now
    return {
        "name": path.name,
        "bytes": st.st_size,
        "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        "age_s": round(reference - st.st_mtime, 3),
    }


@dataclass(frozen=True)
class FileColumns:
    """One listing row, pre-formatted and pre-padded: ``name  size  age``.

    ``name`` is padded to the widest name in the batch and ``size`` is
    right-aligned to the widest size, so a consumer that joins the three
    with two spaces gets aligned columns; a consumer that styles them
    (the TUI pickers dim the metadata) keeps the same alignment.
    """

    name: str
    size: str
    age: str


def file_columns(files: list[Path]) -> list[FileColumns]:
    """Aligned listing columns for ``files``, one row per file.

    The prose twin of ``file_record``.  A file that vanishes between
    glob and stat shows ``?`` for size and age rather than failing the
    whole listing.
    """
    if not files:
        return []
    rows: list[tuple[str, str, str]] = []
    for file in files:
        try:
            st = file.stat()
        except OSError:
            rows.append((file.name, "?", "?"))
            continue
        rows.append((file.name, format_size(st.st_size), format_age(st.st_mtime)))
    name_width = max(len(name) for name, _, _ in rows)
    size_width = max(len(size) for _, size, _ in rows)
    return [
        FileColumns(f"{name:<{name_width}}", f"{size:>{size_width}}", age)
        for name, size, age in rows
    ]


def format_file_lines(files: list[Path]) -> list[str]:
    """``name  size  age`` listing lines, one per file, columns aligned.

    Callers that add a trailing column (``/run.list`` appends the docstring
    summary) get consistent alignment for free.
    """
    return [f"{row.name}  {row.size}  {row.age}" for row in file_columns(files)]


def _folder_path(ctx: PluginContext, folder: str) -> Path | None:
    """Return the absolute per-config path for ``folder``, or None."""
    if not ctx.config_path:
        return None
    return Path(ctx.config_path).parent / folder


def _make_list_handler(folder: str, pattern: str):
    """Handler: list files in the folder, newest first, with size and age.

    ``value`` is the newline-joined names (the scriptable scalar);
    ``data`` is one ``file_record`` per file for structured consumers,
    who skip the prose entirely.
    """

    def handler(ctx: PluginContext, args: str) -> CmdResult:
        data_dir = _folder_path(ctx, folder)
        if data_dir is None:
            return CmdResult.fail(msg="No config loaded.")
        files = list_entries(data_dir, pattern)
        names = "\n".join(file.name for file in files)
        if ctx.wants_data:
            return CmdResult.ok(value=names, data=[file_record(file) for file in files])
        if not files:
            ctx.io.output(f"  {folder}/ (empty)")
            return CmdResult.ok(value="")
        ctx.io._write(f"  {folder}/")
        for line in format_file_lines(files):
            ctx.io._write(f"    {line}")
        return CmdResult.ok(value=names)

    return handler


def _make_explore_handler(folder: str):
    """Handler: open the folder in the system file explorer."""

    def handler(ctx: PluginContext, args: str) -> CmdResult:
        data_dir = _folder_path(ctx, folder)
        if data_dir is None:
            return CmdResult.fail(msg="No config loaded.")
        open_with_system(str(data_dir))
        return CmdResult.ok(value=data_dir)

    return handler


def _make_show_handler(folder: str, pattern: str):
    """Handler: open the newest file in the folder with the system viewer."""

    def handler(ctx: PluginContext, args: str) -> CmdResult:
        data_dir = _folder_path(ctx, folder)
        if data_dir is None:
            return CmdResult.fail(msg="No config loaded.")
        if not data_dir.exists():
            ctx.io.output(f"  {folder}/ is empty.")
            return CmdResult.ok(value="")
        if pattern == "*":
            files = [file for file in data_dir.glob(pattern) if file.is_file()]
        else:
            files = list(data_dir.glob(pattern))
        if not files:
            ctx.io.output(f"  {folder}/ is empty.")
            return CmdResult.ok(value="")
        newest = max(files, key=lambda f: f.stat().st_mtime)
        ctx.io._write(f"Opening {newest.name}")
        open_with_system(str(newest))
        return CmdResult.ok(value=newest)

    return handler


def _make_dump_handler(folder: str, pattern: str):
    """Handler: print the newest (or named) file to the terminal."""

    def handler(ctx: PluginContext, args: str) -> CmdResult:
        data_dir = _folder_path(ctx, folder)
        if data_dir is None:
            return CmdResult.fail(msg="No config loaded.")
        name = args.strip()
        if name:
            # Containment: .dump reads files from within the folder only.
            # ``data_dir / name`` with an absolute name (or ``../``) would
            # otherwise escape to any path on disk -- an arbitrary-file
            # read for an MCP client (/cap.dump C:\\Users\\me\\.secrets).
            # Resolve and verify the target stays inside the folder, the
            # same guard the MCP capture resource uses.  /show is the
            # command for reading files outside the config dir, and it is
            # capability-gated.
            path = (data_dir / name).resolve()
            try:
                inside = path.is_relative_to(data_dir.resolve())
            except OSError:
                inside = False
            if not inside:
                return CmdResult.fail(msg=f"Path escapes {folder}/: {name}")
            if not path.exists():
                return CmdResult.fail(msg=f"File not found: {name}")
        else:
            if not data_dir.exists():
                ctx.io.output(f"  {folder}/ is empty.")
                return CmdResult.ok(value="")
            if pattern == "*":
                files = [file for file in data_dir.glob(pattern) if file.is_file()]
            else:
                files = list(data_dir.glob(pattern))
            if not files:
                ctx.io.output(f"  {folder}/ is empty.")
                return CmdResult.ok(value="")
            path = max(files, key=lambda f: f.stat().st_mtime)
        try:
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                ctx.io.output(line)
        except OSError as e:
            return CmdResult.fail(msg=f"Read error: {e}")
        # Return the dumped contents so scripts can capture / grep them.
        return CmdResult.ok(value=text)

    return handler


def _make_clear_handler(folder: str, pattern: str):
    """Handler: delete all files in the folder matching the pattern."""

    def handler(ctx: PluginContext, args: str) -> CmdResult:
        data_dir = _folder_path(ctx, folder)
        if data_dir is None:
            return CmdResult.fail(msg="No config loaded.")
        if not data_dir.exists():
            ctx.io.output(f"  {folder}/ is already empty.")
            return CmdResult.ok(value="0")
        if pattern == "*":
            files = [file for file in data_dir.glob(pattern) if file.is_file()]
        else:
            files = list(data_dir.glob(pattern))
        if not files:
            ctx.io.output(f"  {folder}/ is already empty.")
            return CmdResult.ok(value="0")
        for file in files:
            file.unlink()
        ctx.io._write(f"  Deleted {len(files)} file(s) from {folder}/.")
        return CmdResult.ok(value=str(len(files)))

    return handler


def _spec_for(folder: str) -> FolderSpec | None:
    """Look up the FolderSpec for a folder name, or None."""
    for spec in FOLDERS:
        if spec.name == folder:
            return spec
    return None


def build_folder_subcommands(folder: str) -> dict[str, Command]:
    """Build the standard ``list/explore/show/dump/clear`` subcommand family.

    Driven by the ``FolderSpec`` for the given folder name.  Always
    includes ``list`` and ``explore``; adds ``show`` / ``dump`` /
    ``clear`` according to the spec's capability flags.

    Caller merges the result into its plugin's ``sub_commands=`` dict:

        sub_commands={
            "send": ...,           # plugin-specific subcommands
            "run": ...,
            **build_folder_subcommands("proto"),   # folder ops
        }
    """
    spec = _spec_for(folder)
    if spec is None:
        raise ValueError(f"Unknown folder: {folder!r}")
    pattern = spec.pattern

    subs: dict[str, Command] = {
        "list": Command(
            help=f"List files in {folder}/, newest first, with size and age.",
            handler=_make_list_handler(folder, pattern),
        ),
        "explore": Command(
            help=f"Open {folder}/ in the system file explorer.",
            handler=_make_explore_handler(folder),
            needs=CapabilitySet(gui_apps=True),
        ),
    }
    if spec.showable:
        subs["show"] = Command(
            help=f"Open the newest file in {folder}/ with the system viewer.",
            handler=_make_show_handler(folder, pattern),
            needs=CapabilitySet(gui_apps=True),
        )
    if spec.dumpable:
        subs["dump"] = Command(
            args="{filename}",
            help=f"Print the newest (or named) file from {folder}/ to the terminal.",
            handler=_make_dump_handler(folder, pattern),
        )
    if spec.clearable:
        subs["clear"] = Command(
            help=f"Delete all files in {folder}/.",
            handler=_make_clear_handler(folder, pattern),
        )
    return subs
