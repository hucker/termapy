"""Shared folder-operation handlers for per-config data folders.

Every top-level plugin that owns a data folder (run/, proto/, ss/,
cap/, prof/, viz/, plugin/, or the cfg config dir itself) exposes
the same uniform subcommand family:

  - ``list``    -- list files in the folder
  - ``explore`` -- open the folder in the system file explorer
  - ``show``    -- open the newest file in the system viewer (showable)
  - ``dump``    -- print the newest (or named) file to the terminal
                   (dumpable)
  - ``delete``  -- delete one named file, or every file with ``*``
                   (deletable)

Which of ``show``, ``dump``, ``delete`` are exposed is driven by the
``showable / dumpable / deletable`` flags on each ``FolderSpec`` in
``termapy.folders`` -- no per-plugin decision, no duplication.

These factories used to live in ``cfg.py``.  They moved here so that
``ss.py``, ``cap.py``, ``proto.py``, and the ``/run.*`` hook registrations
can all reuse the same handler bodies instead of re-implementing the
folder operations per plugin.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from termapy.config import validate_file_stem
from termapy.folders import FOLDERS, FolderSpec, default_file_name, ensure_folder
from termapy.plugins import CapabilitySet, CmdResult, Command, UsageError
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
        file for file in path.glob(pattern) if file.is_file() and not file.name.startswith(".")
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
    return "?" if record["age_s"] is None else format_size(record["bytes"], column=True)


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
        rows.append((file.name, format_size(st.st_size, column=True), format_age(st.st_mtime)))
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
    run: Callable[[dict[str, Any]], str] | None = None,
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
        run: The command a click on a file's NAME runs, from its record
            (``run <name>``); None leaves the names plain text.
    """
    records = []
    for file in files:
        record = file_record(file)
        for _, value_of, key in extra:
            record[key] = value_of(file)
        records.append(record)
    name = FILE_COLUMNS[0] if run is None else replace(FILE_COLUMNS[0], run=run)
    columns = (name, *FILE_COLUMNS[1:], *(column for column, _, _ in extra))
    names = "\n".join(file.name for file in files)
    return CmdResult.table(title, noun, columns, records, value=names)


def _make_list_handler(folder: str, pattern: str, noun: str = "file", show: str = ""):
    """Handler: list files in the folder, newest first, with size and age.

    With ``show`` (``ss.show``), a click on a name opens that file in the
    system viewer (registry ``links-are-data``).
    """

    def handler(ctx: PluginContext, args: str) -> CmdResult:
        data_dir = _folder_path(ctx, folder)
        run = (lambda record: f"{show} {record['name']}") if show else None
        return file_table(f"{folder}/", noun, list_entries(data_dir, pattern), run=run)

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


def _named_file(ctx: PluginContext, folder: str, ext: str, name: str) -> Path | CmdResult:
    """The file ``name`` names in the folder, or the failure that answers it.

    The one lookup of ``.show``, ``.dump`` and ``.delete``.  The name may
    leave off the folder's ``ext`` (registry ``file-name-default-ext``); a
    name that escapes the folder or names nothing is a failure.
    """
    data_dir = _folder_path(ctx, folder)
    # Containment: these commands touch files within the folder only.
    # ``data_dir / name`` with an absolute name (or ``../``) would
    # otherwise escape to any path on disk -- an arbitrary-file read or
    # delete for an MCP client (/cap.dump C:\\Users\\me\\.secrets).
    # Resolve and verify the target stays inside the folder, the same
    # guard the MCP capture resource uses.  /show is the command for
    # reading files outside the config dir, and it is capability-gated.
    path = (data_dir / name).resolve()
    try:
        inside = path.is_relative_to(data_dir.resolve())
    except OSError:
        inside = False
    if not inside:
        return CmdResult.fail(msg=f"Path escapes {folder}/: {name}")
    # After the containment check: the added suffix keeps the path in
    # the same folder.
    path = default_file_name(path, ext)
    if not path.is_file():
        return CmdResult.fail(msg=f"File not found: {name}")
    return path


def _named_or_newest(
    ctx: PluginContext, folder: str, pattern: str, ext: str, name: str
) -> Path | CmdResult:
    """The file ``name`` names in the folder, or its newest file when ``name`` is empty.

    Anything but a file is the handler's answer: ``_named_file``'s failure
    for a name, an empty success when there is no file.
    """
    if name:
        return _named_file(ctx, folder, ext, name)
    data_dir = _folder_path(ctx, folder)
    files: list[Path] = []
    if data_dir.exists():
        if pattern == "*":
            files = [file for file in data_dir.glob(pattern) if file.is_file()]
        else:
            files = list(data_dir.glob(pattern))
    if not files:
        ctx.io.output(f"  {folder}/ is empty.")
        return CmdResult.ok(value="")
    return max(files, key=lambda f: f.stat().st_mtime)


def _make_show_handler(folder: str, pattern: str, ext: str = "*"):
    """Handler: open the newest (or named) file in the folder with the system viewer."""

    def handler(ctx: PluginContext, args: str) -> CmdResult:
        path = _named_or_newest(ctx, folder, pattern, ext, args.strip())
        if isinstance(path, CmdResult):
            return path
        ctx.io.output(f"Opening {path.name}")
        # Through the fs handle, as .explore does: it carries the gui_apps
        # gate, and a test hands it a recorder instead of a viewer.
        ctx.fs.open_file(str(path))
        return CmdResult.ok(value=path)

    return handler


def _make_dump_handler(folder: str, pattern: str, ext: str = "*"):
    """Handler: print the newest (or named) file to the terminal."""

    def handler(ctx: PluginContext, args: str) -> CmdResult:
        path = _named_or_newest(ctx, folder, pattern, ext, args.strip())
        if isinstance(path, CmdResult):
            return path
        try:
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                ctx.io.output(line)
        except OSError as e:
            return CmdResult.fail(msg=f"Read error: {e}")
        # Return the dumped contents so scripts can capture / grep them.
        return CmdResult.ok(value=text)

    return handler


def _make_delete_handler(folder: str, pattern: str, ext: str):
    """Handler: delete one named file from the folder, or every file with ``*``.

    ``*`` asks first where there is someone to ask (the TUI and CLI, as
    ``/cfg.delete`` does); MCP has asked already, through its destructive
    gate.  One named file goes without a question.  The value is the count
    deleted, for either form.
    """

    def handler(ctx: PluginContext, args: str) -> CmdResult:
        name = args.strip()
        if not name:
            raise UsageError()
        if name == "*":
            files = list_entries(_folder_path(ctx, folder), pattern)
            if not files:
                ctx.io.output(f"  {folder}/ is already empty.")
                return CmdResult.ok(value="0")
            shown = f"{len(files)} file" if len(files) == 1 else f"{len(files)} files"
            question = f"Delete every file in {folder}/ ({shown})? This cannot be undone."
            if ctx.capabilities.confirm_dialog and not ctx.ui.confirm(question):
                return CmdResult.fail(msg=f"Nothing deleted from {folder}/")
        else:
            path = _named_file(ctx, folder, ext, name)
            if isinstance(path, CmdResult):
                return path
            files, shown = [path], path.name
        for file in files:
            try:
                file.unlink()
            except OSError as e:
                return CmdResult.fail(msg=f"Delete failed: {e}")
        ctx.io.output(f"  Deleted {shown} from {folder}/.")
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


def build_folder_subcommands(
    folder: str, noun: str = "file", command: str = ""
) -> dict[str, Command]:
    """Build the standard ``list/explore/show/dump/clear`` subcommand family.

    Driven by the ``FolderSpec`` for the given folder name.  Always
    includes ``list`` and ``explore``; adds ``show`` / ``dump`` /
    ``clear`` according to the spec's capability flags.  ``noun`` is
    what ``.list``'s title counts in (``proto/ - 6 scripts``).
    ``command`` is the command the family hangs under when it is not the
    folder's name (``prof/`` is ``/run.profile``): a listed name links
    to ``<command>.show <name>``.

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
    show = f"{command or folder}.show" if spec.showable else ""

    subs: dict[str, Command] = {
        "list": Command(
            help=f"List files in {folder}/, newest first, with size and age.",
            handler=_make_list_handler(folder, pattern, noun, show),
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
                ParamSpec(
                    "old",
                    "str",
                    positional=True,
                    required=True,
                    help="current filename (extension optional)",
                ),
                ParamSpec(
                    "new",
                    "str",
                    positional=True,
                    required=True,
                    help="new name (the extension is kept)",
                ),
            ],
        ),
    }
    if spec.showable:
        subs["show"] = Command(
            args="{filename}",
            help=f"Open the newest (or named) file in {folder}/ with the system viewer.",
            handler=_make_show_handler(folder, pattern, spec.ext),
            needs=CapabilitySet.CONFIG_GUI_APPS,
            files=folder,
        )
    if spec.dumpable:
        subs["dump"] = Command(
            args="{filename}",
            help=f"Print the newest (or named) file from {folder}/ to the terminal.",
            handler=_make_dump_handler(folder, pattern, spec.ext),
            needs=CapabilitySet.CONFIG_LOADED,
            files=folder,
        )
    if spec.deletable:
        subs["delete"] = Command(
            # Hand-rolled like .show and .dump: a positional param's synopsis
            # is always <name>, and this one takes * too (CLAUDE.md,
            # "Declarative Command Parameters").
            args="<name|*>",
            help=f"Delete a file from {folder}/; * deletes them all, after a yes.",
            handler=_make_delete_handler(folder, pattern, spec.ext),
            needs=CapabilitySet.CONFIG_LOADED,
            files=folder,
            safety="destructive",
        )
    return subs
