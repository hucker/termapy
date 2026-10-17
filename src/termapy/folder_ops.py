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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from termapy.config import validate_file_stem
from termapy.folders import FOLDERS, FolderSpec, default_file_name, ensure_folder
from termapy.plugins import CapabilitySet, CmdResult, Command
from termapy.plugins.params import ParamSpec
from termapy.plugins.views import Column
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


def _size_cell(record: dict[str, Any]) -> str:
    """``2.3 KB``, or ``?`` for a file that vanished between glob and stat."""
    return "?" if record["age_s"] is None else format_size(record["bytes"])


def _age_cell(record: dict[str, Any]) -> str:
    """``3 min ago`` from the record's ``age_s``, or ``?`` for a vanished file."""
    if record["age_s"] is None:
        return "?"
    return format_age(time.time() - record["age_s"])


# The listing columns over ``file_record``: every file list starts with
# these three and may append its own (``/run.list`` adds SUMMARY).
FILE_COLUMNS: tuple[Column, ...] = (
    Column("NAME", lambda record: record["name"]),
    Column("SIZE", _size_cell, right=True),
    Column("UPDATED", _age_cell),
)


@dataclass(frozen=True)
class FileColumns:
    """One listing row, pre-formatted and pre-padded: ``name  size  age``.

    ``name`` and ``age`` are padded to the widest in the batch and ``size``
    is right-aligned to the widest size, so a consumer that joins the three
    with two spaces gets aligned columns -- and anything appended after
    ``age`` (a docstring summary, a config's port) lines up too.  A
    consumer that styles them (the TUI pickers dim the metadata) keeps the
    same alignment.
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
    age_width = max(len(age) for _, _, age in rows)
    return [
        FileColumns(f"{name:<{name_width}}", f"{size:>{size_width}}", f"{age:<{age_width}}")
        for name, size, age in rows
    ]


def format_file_header(rows: list[FileColumns], detail: str = "") -> str:
    """``NAME  SIZE  UPDATED  <detail>`` sized to ``rows``' columns.

    Every header is left-aligned at its column's left edge, including the
    one over the right-aligned sizes -- one rule reads as a header row,
    per-column alignment reads as random.  Shared by the prose listings
    and the TUI pickers so a file reads the same wherever it is listed.
    ("UPDATED" fits: the narrowest age string, "just now", is 8 cells.)
    """
    if not rows:
        return ""
    first = rows[0]
    return (
        f"{'NAME':<{len(first.name)}}  {'SIZE':<{len(first.size)}}  "
        f"{'UPDATED':<{len(first.age)}}  {detail}"
    ).rstrip()


def format_file_lines(files: list[Path]) -> list[str]:
    """``name  size  age`` listing lines, one per file, columns aligned.

    The age pad is kept so a caller that appends a trailing column
    (``/run.list`` adds the docstring summary) gets it aligned; a caller
    that prints the line as-is should ``rstrip()`` it.  Print
    ``format_file_header`` above them so the columns are labeled.
    """
    return [f"{row.name}  {row.size}  {row.age}" for row in file_columns(files)]


def _folder_path(ctx: PluginContext, folder: str) -> Path:
    """Return the absolute per-config path for ``folder``.

    Every folder command declares ``config_loaded``, so dispatch has
    answered "No config loaded." before a handler asks.
    """
    return Path(ctx.config_path).parent / folder


def file_table(
    title: str,
    noun: str,
    files: list[Path],
    extra: tuple[tuple[Column, Callable[[Path], Any], str], ...] = (),
) -> CmdResult:
    """A file list answer (``CmdResult.table``): NAME, SIZE, UPDATED, then ``extra``.

    ``value`` is the newline-joined names (the scriptable scalar); ``data``
    is one ``file_record`` per file, and dispatch prints the table from
    them unless the caller wants the data.

    Args:
        title: The folder (``run/``).
        noun: Singular noun the count is in (``script``).
        files: The files, newest first.
        extra: ``(column, value_of, key)`` for each column after UPDATED:
            ``value_of(path)`` is stored under ``key`` in that file's
            record, and the column shows it (``/run.list``'s SUMMARY).
    """
    records = []
    for file in files:
        record = file_record(file)
        for _, value_of, key in extra:
            record[key] = value_of(file)
        records.append(record)
    columns = (*FILE_COLUMNS, *(column for column, _, _ in extra))
    names = "\n".join(file.name for file in files)
    return CmdResult.table(title, noun, columns, records, value=names)


def _make_list_handler(folder: str, pattern: str, noun: str = "file"):
    """Handler: list files in the folder, newest first, with size and age."""

    def handler(ctx: PluginContext, args: str) -> CmdResult:
        data_dir = _folder_path(ctx, folder)
        return file_table(f"{folder}/", noun, list_entries(data_dir, pattern))

    return handler


def _make_explore_handler(folder: str):
    """Handler: open the folder in the system file explorer.

    Creates the folder first so the user has somewhere to drop a file:
    opening it is the one read that counts as a write.
    """

    def handler(ctx: PluginContext, args: str) -> CmdResult:
        data_dir = _folder_path(ctx, folder)
        ctx.fs.open_file(ensure_folder(data_dir))
        return CmdResult.ok(value=data_dir)

    return handler


def _make_show_handler(folder: str, pattern: str):
    """Handler: open the newest file in the folder with the system viewer."""

    def handler(ctx: PluginContext, args: str) -> CmdResult:
        data_dir = _folder_path(ctx, folder)
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
        # Through the fs handle, as .explore does: it carries the gui_apps
        # gate, and a test hands it a recorder instead of a viewer.
        ctx.fs.open_file(str(newest))
        return CmdResult.ok(value=newest)

    return handler


def _make_dump_handler(folder: str, pattern: str, ext: str = "*"):
    """Handler: print the newest (or named) file to the terminal.

    A named file may leave off the folder's ``ext`` (registry
    ``file-name-default-ext``).
    """

    def handler(ctx: PluginContext, args: str) -> CmdResult:
        data_dir = _folder_path(ctx, folder)
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
            # After the containment check: the added suffix keeps the path
            # in the same folder.
            path = default_file_name(path, ext)
            if not path.is_file():
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


def rename_file(data_dir: Path, old_name: str, new_name: str, ext: str) -> Path:
    """Rename ``old_name`` to ``new_name`` inside ``data_dir``; return the new path.

    The rules every frontend shares (the pickers dispatch the same
    command a CLI or MCP caller types):

    - both names stay inside the folder (no separators, no ``..``);
    - the old name may leave off ``ext`` (registry ``file-name-default-ext``);
    - rename changes the name, never the extension: the file keeps ``ext``
      (``ext="*"`` folders keep the old file's own suffix), a typed copy
      of it is dropped, and any other dot is part of the name, so ``v1.3``
      becomes ``v1.3.run``;
    - nothing is overwritten.

    Raises:
        ValueError: A rule above failed, or the file does not exist.
        OSError: The rename itself failed.
    """
    for name in (old_name, new_name):
        if any(char in name for char in "/\\") or name in (".", ".."):
            raise ValueError(f"Name may not contain path separators: {name}")
    old = default_file_name(data_dir / old_name, ext)
    if not old.is_file():
        raise ValueError(f"File not found: {old_name}")
    keep_ext = old.suffix if ext == "*" else ext
    if keep_ext and new_name.endswith(keep_ext):
        new_name = new_name[: -len(keep_ext)]
    reason = validate_file_stem(new_name)
    if reason:
        raise ValueError(reason)
    new = data_dir / f"{new_name}{keep_ext}"
    if new.exists():
        raise ValueError(f"File already exists: {new.name}")
    old.rename(new)
    return new


def _make_rename_handler(folder: str, ext: str):
    """Handler: ``/<folder>.rename <old> <new>`` via ``rename_file``."""

    def handler(ctx: PluginContext, args: str) -> CmdResult:
        data_dir = _folder_path(ctx, folder)
        old_name = str(ctx.arg("old"))
        # The file a bare old name means, for the message: it is gone after.
        shown = default_file_name(data_dir / old_name, ext).name
        try:
            new = rename_file(data_dir, old_name, ctx.arg("new"), ext)
        except ValueError as e:
            return CmdResult.fail(msg=str(e))
        except OSError as e:
            return CmdResult.fail(msg=f"Rename error: {e}")
        ctx.io.output(f"  Renamed {shown} -> {new.name}")
        return CmdResult.ok(value=new)

    return handler


def _spec_for(folder: str) -> FolderSpec | None:
    """Look up the FolderSpec for a folder name, or None."""
    for spec in FOLDERS:
        if spec.name == folder:
            return spec
    return None


def build_folder_subcommands(folder: str, noun: str = "file") -> dict[str, Command]:
    """Build the standard ``list/explore/show/dump/clear`` subcommand family.

    Driven by the ``FolderSpec`` for the given folder name.  Always
    includes ``list`` and ``explore``; adds ``show`` / ``dump`` /
    ``clear`` according to the spec's capability flags.  ``noun`` is
    what ``.list``'s title counts in (``proto/ - 6 scripts``).

    Caller merges the result into its plugin's ``sub_commands=`` dict:

        sub_commands={
            "send": ...,           # plugin-specific subcommands
            "run": ...,
            **build_folder_subcommands("proto", noun="script"),
        }
    """
    spec = _spec_for(folder)
    if spec is None:
        raise ValueError(f"Unknown folder: {folder!r}")
    pattern = spec.pattern

    subs: dict[str, Command] = {
        "list": Command(
            help=f"List files in {folder}/, newest first, with size and age.",
            handler=_make_list_handler(folder, pattern, noun),
            needs=CapabilitySet.CONFIG_LOADED,
        ),
        "explore": Command(
            help=f"Open {folder}/ in the system file explorer.",
            handler=_make_explore_handler(folder),
            needs=CapabilitySet.CONFIG_GUI_APPS,
        ),
        "rename": Command(
            help=f"Rename a file in {folder}/ (extension kept; never overwrites).",
            handler=_make_rename_handler(folder, spec.ext),
            needs=CapabilitySet.CONFIG_LOADED,
            files=folder,
            params=[
                ParamSpec("old", "str", positional=True, required=True,
                          help="current filename (extension optional)"),
                ParamSpec("new", "str", positional=True, required=True,
                          help="new name (the extension is kept)"),
            ],
        ),
    }
    if spec.showable:
        subs["show"] = Command(
            help=f"Open the newest file in {folder}/ with the system viewer.",
            handler=_make_show_handler(folder, pattern),
            needs=CapabilitySet.CONFIG_GUI_APPS,
        )
    if spec.dumpable:
        subs["dump"] = Command(
            args="{filename}",
            help=f"Print the newest (or named) file from {folder}/ to the terminal.",
            handler=_make_dump_handler(folder, pattern, spec.ext),
            needs=CapabilitySet.CONFIG_LOADED,
            files=folder,
        )
    if spec.clearable:
        subs["clear"] = Command(
            help=f"Delete all files in {folder}/.",
            handler=_make_clear_handler(folder, pattern),
            needs=CapabilitySet.CONFIG_LOADED,
        )
    return subs
