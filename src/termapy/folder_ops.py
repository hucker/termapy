"""Shared folder-operation handlers for per-config data folders.

Every top-level plugin that owns a data folder (run/, proto/, plugin/,
ss/, cap/, and prof/ as ``/run.profile``) exposes the same uniform
subcommand family:

  - ``list``    -- list files in the folder
  - ``explore`` -- open the folder in the system file explorer
  - ``show``    -- open the newest (or named) file in the system viewer
                   (showable)
  - ``dump``    -- print the newest (or named) file to the terminal
                   (dumpable)
  - ``rename``  -- rename a file, keeping its extension
  - ``delete``  -- delete one named file, or in a folder of termapy's own
                   output every file a pattern matches (generated)

Which of ``show`` and ``dump`` exist, and whether ``delete`` takes a
pattern, is driven by the ``showable / dumpable / generated`` flags on
each ``FolderSpec`` in ``termapy.folders`` -- no per-plugin decision, no
duplication.  viz/ has no command family (the proto debug screen loads
it), and a config folder is not a data folder: ``/cfg`` has its own verbs.

These factories used to live in ``cfg.py``.  They moved here so that
``ss.py``, ``cap.py``, ``proto.py``, and the ``/run.*`` hook registrations
can all reuse the same handler bodies instead of re-implementing the
folder operations per plugin.

The edit leaves live here too: ``edit_command(folder)`` builds
``/edit.<folder>`` and its noun-first twin ``/<folder>.edit`` as one
declaration, ``edit_folder_command`` adds that leaf's ``.list`` and
``.explore``, and ``edit_cfg_command`` is ``/edit.cfg`` / ``/cfg.edit``.
``dump_text_slice`` is the body of ``/log.dump`` and ``/log.diag.dump``.
A helper two plugins import is core, never another plugin's export
(registry ``plugins-import-published-only``), so the run, proto, plugin,
cfg, log and mcp plugins reach these from here, not from ``edit.py`` and
``log.py`` as they did until 2026-10-24.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from termapy.config import validate_file_stem
from termapy.folders import (
    FOLDER_EXTS,
    FOLDERS,
    PROTO,
    RUN,
    FolderSpec,
    default_file_name,
    ensure_folder,
    file_suggestion,
    inside,
)
from termapy.help_dynamic import compose, folder_line
from termapy.matching import is_glob, matches
from termapy.plugins import CapabilitySet, CmdResult, Command, UsageError
from termapy.plugins.command import PICKERS
from termapy.plugins.params import ParamSpec
from termapy.plugins.views import Column
from termapy.scripting import format_age, format_size, select_lines

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


def write_destination(ctx: PluginContext, given: str, default: str) -> Path:
    """The folder a command writes into: everything for a config goes in its folder.

    With ``given`` empty, the config's ``default`` data folder, created
    through ``ensure_folder``.  A relative ``given`` is under the config's
    folder, never the process working directory, which is usually the
    user's project root (decision 2026-10-26: a write never pollutes it).
    An absolute ``given``, or one climbing out with ``..``, is honored only
    where the host allows paths outside the config sandbox
    (``ctx.fs.guard_external_path``), so an MCP client stays confined, and
    a named code folder is refused to one outright
    (``ctx.fs.guard_code_folder``).  The command declares ``config_loaded``.

    Args:
        ctx: The plugin context, for the config and the sandbox guards.
        given: The folder the user named, ``""`` for none.
        default: The ``folders.FOLDERS`` data folder used when none is named.

    Returns:
        The absolute folder; a named one may not exist yet.

    Raises:
        MissingCapability: ``given`` leaves the config sandbox or names a code
            folder, and the host does not allow it.
    """
    base = Path(ctx.config_path).parent
    if not given:
        return ensure_folder(base / default).resolve()
    ctx.fs.guard_external_path(given, "Folder")
    path = Path(given)
    folder = (path if path.is_absolute() else base / path).resolve()
    ctx.fs.guard_code_folder(folder)
    return folder


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


def dump_text_slice(ctx: PluginContext, path: Path, args: str) -> CmdResult:
    """Print a text file: all lines, last N (N>0), or first N (N<0).

    The shared body of ``/log.dump`` and ``/log.diag.dump`` -- each does its
    own path resolution + sandbox guard, then hands the resolved path
    here.  Returns the line count via ``value=`` for scripting.

    Args:
        ctx: Plugin context for output.
        path: Resolved, already-authorized file path.
        args: Raw arg string -- empty (all), or a signed integer count.
    """
    n: int | None = None
    arg = args.strip()
    if arg:
        try:
            n = int(arg)
        except ValueError:
            raise UsageError(f"Invalid count: {arg!r}  (N>0 last N, N<0 first N)") from None
        if n == 0:
            return CmdResult.fail(msg="Invalid line count: 0")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        return CmdResult.fail(msg=f"Read error: {e}")
    lines = select_lines(lines, n)
    for line in lines:
        ctx.io.output(line)
    return CmdResult.ok(value=str(len(lines)))


def _make_list_handler(
    folder: str, pattern: str, noun: str = "file", show: str = ""
) -> Callable[[PluginContext, str], CmdResult]:
    """Handler: list files in the folder, newest first, with size and age.

    With ``show`` (``ss.show``), a click on a name opens that file in the
    system viewer (registry ``links-are-data``).
    """

    def handler(ctx: PluginContext, args: str) -> CmdResult:
        data_dir = _folder_path(ctx, folder)
        run = (lambda record: f"{show} {record['name']}") if show else None
        return file_table(f"{folder}/", noun, list_entries(data_dir, pattern), run=run)

    # The /help.dev text of each command built here (registry handler-docstring).
    handler.__doc__ = (
        f"List the {noun}s in the config's {folder}/ folder, newest first, with size and age."
    )
    return handler


def _make_explore_handler(
    folder: str, *, cwd_fallback: bool = False
) -> Callable[[PluginContext, str], CmdResult]:
    """Handler: open the folder in the system file explorer.

    Creates the folder first so the user has somewhere to drop a file:
    opening it is the one read that counts as a write.  ``cwd_fallback``
    is ``/edit.<folder>.explore``'s: it declares no config, and opens the
    cwd when none is loaded.
    """

    def handler(ctx: PluginContext, args: str) -> CmdResult:
        data_dir = _edit_folder_dir(folder, ctx) if cwd_fallback else _folder_path(ctx, folder)
        ctx.fs.open_file(ensure_folder(data_dir))
        return CmdResult.ok(value=data_dir)

    # The /help.dev text of each command built here (registry handler-docstring).
    handler.__doc__ = (
        f"Open the config's {folder}/ folder in the system file explorer, creating it first."
    )
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
    # folders.inside keeps the target in the folder, as it does for the MCP
    # capture resource.  /show is the command for reading files outside
    # the config dir, and it is capability-gated.
    path = inside(data_dir, name)
    if path is None:
        return CmdResult.fail(msg=f"Path escapes {folder}/: {name}")
    # After the containment check: the added suffix keeps the path in
    # the same folder.
    path = default_file_name(path, ext)
    if not path.is_file():
        hint = file_suggestion(data_dir, name, ext)
        return CmdResult.fail(msg=f"File not found: {name}{hint}")
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


def _make_show_handler(
    folder: str, pattern: str, ext: str = "*"
) -> Callable[[PluginContext, str], CmdResult]:
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

    # The /help.dev text of each command built here (registry handler-docstring).
    handler.__doc__ = f"Open the newest {pattern} file in the config's {folder}/ folder, or the one named, with the system viewer."
    return handler


def _make_dump_handler(
    folder: str, pattern: str, ext: str = "*"
) -> Callable[[PluginContext, str], CmdResult]:
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

    # The /help.dev text of each command built here (registry handler-docstring).
    handler.__doc__ = (
        f"Print the newest {pattern} file in the config's {folder}/ folder, or the one named."
    )
    return handler


def _make_delete_handler(
    folder: str, pattern: str, ext: str, generated: bool
) -> Callable[[PluginContext, str], CmdResult]:
    """Handler: delete one named file from the folder, or every file a pattern matches.

    A pattern (registry ``name-one-matcher``; ``*`` is every file) works
    only in a folder of termapy's own output (``generated``); a folder of
    what someone wrote deletes one named file at a time.  Where there is
    someone to ask (the TUI and CLI) a pattern asks first, and so does a
    file someone wrote; one generated file goes without a question.  An MCP
    client never reaches this (registry ``mcp-no-file-management``).  Finding
    nothing to delete fails, for a pattern as for a name.  The value is the
    count.
    """

    def handler(ctx: PluginContext, args: str) -> CmdResult:
        name = args.strip()
        if not name:
            raise UsageError()
        if is_glob(name):
            if not generated:
                return CmdResult.fail(
                    msg=f"Invalid name: {name} ({folder}/ deletes one named file at a time)"
                )
            files = [
                file
                for file in list_entries(_folder_path(ctx, folder), pattern)
                if matches(file.name, name)
            ]
            if not files:
                return CmdResult.fail(msg=f"No files in {folder}/ match: {name}")
            shown = f"{len(files)} file" if len(files) == 1 else f"{len(files)} files"
            question = f"Delete {shown} from {folder}/ ({name})? This cannot be undone."
        else:
            path = _named_file(ctx, folder, ext, name)
            if isinstance(path, CmdResult):
                return path
            files, shown = [path], path.name
            question = "" if generated else f"Delete {shown} from {folder}/? This cannot be undone."
        if question and ctx.capabilities.confirm_dialog and not ctx.ui.confirm(question):
            return CmdResult.fail(msg=f"Nothing deleted from {folder}/")
        try:
            for file in files:
                file.unlink()
        except OSError as e:
            return CmdResult.fail(msg=f"Delete failed: {e}")
        finally:
            ctx.internal.folder_changed(folder)
        ctx.io.output(f"  Deleted {shown} from {folder}/.")
        return CmdResult.ok(value=str(len(files)))

    # The /help.dev text of each command built here (registry handler-docstring).
    handler.__doc__ = f"Delete one named file from the config's {folder}/ folder" + (
        ", or every file a pattern matches." if generated else "."
    )
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
        raise ValueError(f"File not found: {old_name}{file_suggestion(data_dir, old_name, ext)}")
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


def _make_rename_handler(folder: str, ext: str) -> Callable[[PluginContext, str], CmdResult]:
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

    # The /help.dev text of each command built here (registry handler-docstring).
    handler.__doc__ = f"Rename a file in the config's {folder}/ folder, old name then new; the {ext} extension is kept."
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
            shows=True,
            state=True,
            needs=CapabilitySet.CONFIG_LOADED,
        ),
        "explore": Command(
            help=f"Open {folder}/ in the system file explorer.",
            handler=_make_explore_handler(folder),
            shows=True,
            needs=CapabilitySet.CONFIG_GUI_APPS,
        ),
        "rename": Command(
            help=f"Rename a file in {folder}/ (extension kept; never overwrites).",
            handler=_make_rename_handler(folder, spec.ext),
            needs=CapabilitySet.MANAGE_FILES,
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
            shows=True,
            needs=CapabilitySet.CONFIG_GUI_APPS,
            files=folder,
        )
    if spec.dumpable:
        subs["dump"] = Command(
            args="{filename}",
            help=f"Print the newest (or named) file from {folder}/ to the terminal.",
            handler=_make_dump_handler(folder, pattern, spec.ext),
            shows=True,
            needs=CapabilitySet.CONFIG_LOADED,
            files=folder,
        )
    subs["delete"] = Command(
        # Hand-rolled like .show and .dump: a positional param's synopsis is
        # always <name>, and a generated folder's takes a pattern too
        # (CLAUDE.md, "Declarative Command Parameters").
        args="<name|pattern>" if spec.generated else "<name>",
        help=(
            f"Delete a file from {folder}/; a pattern (* for all) deletes its matches, after a yes."
            if spec.generated
            else f"Delete a file from {folder}/, after a yes."
        ),
        handler=_make_delete_handler(folder, pattern, spec.ext, spec.generated),
        needs=CapabilitySet.MANAGE_FILES,
        files=folder,
        safety="destructive",
    )
    return subs


# ── The edit leaves ──────────────────────────────────────────────────────────
#
# ``/edit.<folder>`` and its noun-first twin ``/<folder>.edit`` are one
# declaration, built here, so the two spellings cannot drift; the run,
# proto, plugin and cfg plugins mount the twins.  The handlers decide
# WHICH file; ``ctx.internal.edit_file`` is how the frontend opens it: the
# TUI's own editor for the config, a .run or a .pro file (over SSH too),
# the system editor otherwise.

# Extensions the TUI opens in its own editor (``app._edit_file``); the rest
# go to the system editor.
_TERMAPY_EDITS = frozenset({FOLDER_EXTS[RUN], FOLDER_EXTS[PROTO]})

# The word ``folder_line`` counts files in, where "file" would read wrong.
_NOUNS = {"run": "script", "proto": "script"}


def _edit_folder_dir(folder: str, ctx: PluginContext) -> Path:
    """The per-config data folder; the cwd when no config is loaded."""
    return Path(ctx.config_path).parent / folder if ctx.config_path else Path(".")


def _folder_long_help(folder: str, text: str = "") -> Callable[[PluginContext], str]:
    """Dynamic long_help: the live file count ("42 scripts in run/"), then ``text``."""
    noun = _NOUNS.get(folder)

    def long_help(ctx: PluginContext) -> str:
        return compose(folder_line(ctx, folder, noun=noun), text)

    return long_help


def _make_edit_handler(folder: str, ext: str) -> Callable[[PluginContext, str], CmdResult]:
    """Handler: open a file by name, or list the folder when no name is given.

    Returns the opened file path as ``CmdResult.value``; bare, the
    ``/<folder>.list`` result (a picker host never gets here bare -- the
    dispatcher opens the picker first).
    """

    def handler(ctx: PluginContext, args: str) -> CmdResult:
        name = args.strip()
        if not name:
            return _make_forward_list_handler(folder)(ctx, args)
        # The extension is optional (registry ``file-name-default-ext``).
        folder_dir = _edit_folder_dir(folder, ctx)
        path = default_file_name(folder_dir / name, ext)
        if not path.is_file():
            hint = file_suggestion(folder_dir, name, ext)
            return CmdResult.fail(msg=f"File not found: {name}{hint}")
        ctx.internal.edit_file(path)
        return CmdResult.ok(value=path)

    # The /help.dev text of each command built here (registry handler-docstring).
    handler.__doc__ = f"Open a {ext} file from the config's {folder}/ folder in the editor, or list the folder when no name is given."
    return handler


def _make_forward_list_handler(folder: str) -> Callable[[PluginContext, str], CmdResult]:
    """``/edit.<folder>.list`` is ``/<folder>.list``: one listing per folder.

    Forwarded through the engine, so the listing (format, order, data
    records, ``/run.list``'s SUMMARY) is the folder command's own.
    """

    def handler(ctx: PluginContext, args: str) -> CmdResult:
        return ctx.internal.dispatch(f"{folder}.list")

    # The /help.dev text of each command built here (registry handler-docstring).
    handler.__doc__ = f"The same listing as {folder}.list, so there is one listing per folder."
    return handler


# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).


def _handler_edit_cfg(ctx: PluginContext, args: str) -> CmdResult:
    """``/edit.cfg`` and ``/cfg.edit``: open the loaded config."""
    ctx.internal.edit_file(Path(ctx.config_path))
    return CmdResult.ok(value=Path(ctx.config_path))


def edit_command(folder: str) -> Command:
    """``/edit.<folder>`` and its noun-first twin ``/<folder>.edit``.

    One builder for both spellings, so they cannot drift: open an existing
    file from ``folder`` by name, or list the folder when bare (the TUI
    opens the folder's picker instead).  The TUI has its own editor for
    .run and .pro files, so those need ``file_editor``; a plugin's .py
    opens only in the system editor, which needs a desktop.

    Args:
        folder: A ``folders.FOLDERS`` name holding editable text files.
    """
    spec = _spec_for(folder)
    if spec is None:
        raise ValueError(f"Unknown folder: {folder!r}")
    # A folder with a picker opens it on a bare call in the TUI (SYNOPSIS
    # says so); the listing is what a host without pickers falls back to.
    picker = folder if folder in PICKERS else ""
    bare = (
        f"Without a picker (CLI, MCP), bare lists {folder}/."
        if picker
        else f"Bare lists {folder}/."
    )
    own_editor = spec.ext in _TERMAPY_EDITS
    where = (
        "termapy's editor in the TUI, the system editor in the CLI"
        if own_editor
        else "the system editor"
    )
    text = (
        f"With a name, opens that existing file from {folder}/ in {where} "
        f"(the {spec.ext} is optional); a missing name fails.  {bare}  "
        f"/{folder}.edit and {{prefix}}edit.{folder} are the same command."
    )
    return Command(
        args="{filename}",
        help=f"Open a {spec.ext} file for editing.",
        long_help=_folder_long_help(folder, text),
        handler=_make_edit_handler(folder, spec.ext),
        shows=True,
        needs=CapabilitySet.FILE_EDITOR if own_editor else CapabilitySet.GUI_APPS,
        files=folder,
        picker=picker,
    )


def edit_cfg_command() -> Command:
    """``/edit.cfg`` and its noun-first twin ``/cfg.edit``: open the current config."""
    return Command(
        help="Edit the current config file.",
        long_help=(
            "Opens the loaded config in termapy's config editor in the TUI, "
            "as F10 does, and in the system editor in the CLI.  {prefix}cfg.edit "
            "and {prefix}edit.cfg are the same command."
        ),
        handler=_handler_edit_cfg,
        shows=True,
        needs=CapabilitySet(file_editor=True, config_loaded=True),
    )


def edit_folder_command(folder: str) -> Command:
    """``/edit.<folder>``: the shared edit leaf plus ``.list`` and ``.explore``."""
    spec = _spec_for(folder)
    if spec is None:
        raise ValueError(f"Unknown folder: {folder!r}")
    long_help = _folder_long_help(folder)  # the subs: the count line alone
    return replace(
        edit_command(folder),
        intro=f"Open a {spec.ext} file from {folder}/ in an editor, list them, or open the folder.",
        sub_commands={
            "list": Command(
                help=f"List {spec.ext} files.",
                long_help=long_help,
                handler=_make_forward_list_handler(folder),
                shows=True,
            ),
            "explore": Command(
                help="Open folder in file explorer.",
                long_help=long_help,
                handler=_make_explore_handler(folder, cwd_fallback=True),
                shows=True,
                needs=CapabilitySet.GUI_APPS,
            ),
        },
    )
