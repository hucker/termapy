"""The load report: what the last config load put in the session, one row per file.

Every config load (``on_app_start`` / ``on_config_load``) runs the same
loaders in ``ReplEngine.fire_lifecycle``: the plugin folders, the symbol
sidecar and the device files.  Each loader returns its rows, built here
by :func:`load_record`; the engine keeps them in a :class:`LoadReport`,
prints them once as a table, and ``/cfg.loaded`` shows them again, the
same records as JSON for ``--json`` and MCP.  The MCP frontend adds the
profile it auto-loads on connect (``mcp/server.py``); a later
``/sym.import``, ``/dev.import`` or ``/profile.load`` is that command's
own answer and does not change the report::

    Loaded - 3 files
    KIND     NAME      LAYER   FILE                     COUNT  NOTE
    -------  --------  ------  -----------------------  -----  ------------------------------
    plugin   hello     global  plugin/hello.py              1  commands hello
    symbols  demo.elf  demo    sym/demo.symbols.json       15  elf, 12 typed
    device   ADC       demo    dev/adc.device.json          9  ref acme-adc

A record's keys are the same for every kind:

- ``kind``: :data:`PLUGIN`, :data:`SYMBOLS`, :data:`DEVICE` or :data:`PROFILE`
- ``name``: what loaded (the plugin module, the build, the device, the profile)
- ``layer``: ``global`` or the config's name
- ``file``: the file, relative to that layer's folder
- ``path``: the same file in full, ``""`` when there is none (a device
  error names its file only in ``error``)
- ``count``: what it registered (commands and hooks, symbols, registers,
  profile commands)
- ``detail``: the kind's own facts, a fixed set of keys per kind
- ``error``: why it did not load; ``""`` when it did

A failure is printed where it happens, as it always was (an error is
never silent), and is also a row here, so an agent that never saw the
startup output learns what failed.  The rows are reported, the table is
not printed, for ``--run`` / ``--exec``.  Under ``quiet_start`` the table
goes to the session log and the screen names only the files that describe
the target (:func:`startup_lines`).  Registry ``load-report-one-table``.
Pure: no I/O, no Textual, no pyserial.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from termapy.folders import PLUGIN as PLUGIN_FOLDER
from termapy.plugins import CmdResult, Column

if TYPE_CHECKING:
    from termapy.plugins import ModuleLoad

Record = dict[str, Any]

PLUGIN: Final[str] = "plugin"
SYMBOLS: Final[str] = "symbols"
DEVICE: Final[str] = "device"
PROFILE: Final[str] = "profile"


def load_record(
    kind: str,
    *,
    name: str = "",
    layer: str = "",
    file: str = "",
    path: str = "",
    count: int = 0,
    detail: dict[str, Any] | None = None,
    error: str = "",
) -> Record:
    """One row of the report, every key present (see the module docstring)."""
    return {
        "kind": kind,
        "name": name,
        "layer": layer,
        "file": file,
        "path": path,
        "count": count,
        "detail": detail or {},
        "error": error,
    }


def plugin_record(
    module: ModuleLoad, layer: str, folder: Path, commands: list[str], hooks: list[str]
) -> Record:
    """A plugin file's row: what it registered, by kind.

    Args:
        module: The file's load result.
        layer: ``global`` or the config's name.
        folder: The ``plugin/`` folder the file is in.
        commands: The command names actually registered -- a name the
            frontend owns is skipped, so it is not ``module.plugins``.
        hooks: The lifecycle hooks actually registered -- a folder
            plugin's ``on_app_prestart`` is skipped, for the same reason.
    """
    detail = {
        "commands": commands,
        "transforms": [transform.name for transform in module.transforms],
        "directives": [directive.name for directive in module.directives],
        "converters": [converter.format for converter in module.converters],
        "hooks": hooks,
    }
    if module.error:
        error = module.error
    elif not module.exported:
        error = "nothing exported"
    else:
        error = ""
    return load_record(
        PLUGIN,
        name=Path(module.file).stem,
        layer=layer,
        file=Path(PLUGIN_FOLDER, module.file).as_posix(),
        path=str((folder / module.file).resolve()),
        count=sum(len(names) for names in detail.values()),
        detail=detail,
        error=error,
    )


def profile_record(profile: dict[str, Any], *, file: str, path: str, layer: str) -> Record:
    """An auto-loaded profile's row: the device it describes, its command count.

    Args:
        profile: The installed profile (``ctx.ns("active_profile")``).
        file: The profile file, relative to the config folder when inside it.
        path: The same file in full.
        layer: The config's name.
    """
    device = profile.get("device")
    return load_record(
        PROFILE,
        name=str(device.get("name", "")) if isinstance(device, dict) else "",
        layer=layer,
        file=file,
        path=path,
        count=len(profile.get("commands") or {}),
        detail={"revision": str(profile.get("profile_revision") or "")},
    )


# What a quiet startup names on screen: the files that describe the
# target, its memory map and its registers.  Plugins are code; their rows
# stay in the log and /cfg.loaded.
_STARTUP_LABELS: Final[dict[str, str]] = {SYMBOLS: "Symbols:", DEVICE: "Device: "}


def startup_lines(records: Iterable[Record]) -> list[str]:
    """The screen's lines for a quiet startup: one per symbol table and device file.

    Each names the file in full, which the TUI makes clickable; one that
    failed is left out here, since its error was printed where it failed.
    """
    return [
        f"{_STARTUP_LABELS[record['kind']]} {record['path']}"
        for record in records
        if record["kind"] in _STARTUP_LABELS and record["path"] and not record["error"]
    ]


class LoadReport:
    """The session's load rows: replaced by each config load.

    Readers get a copy and writers rebind the list, so a dispatch thread
    reading ``/cfg.loaded`` while MCP's connect adds the profile never
    sees a list mid-change.
    """

    def __init__(self) -> None:
        """Start empty: nothing has loaded."""
        self._records: list[Record] = []

    @property
    def records(self) -> list[Record]:
        """The rows, in load order."""
        return list(self._records)

    def replace(self, records: Iterable[Record]) -> None:
        """A config load: its rows are the whole report."""
        self._records = list(records)

    def put(self, kind: str, records: Iterable[Record]) -> None:
        """Replace one kind's rows (MCP's profile on connect); none drops them."""
        kept = [record for record in self._records if record["kind"] != kind]
        self._records = [*kept, *records]


def _plugin_note(detail: dict[str, Any]) -> str:
    return "; ".join(f"{group} {', '.join(names)}" for group, names in detail.items() if names)


def _symbols_note(detail: dict[str, Any]) -> str:
    # Only a positive stale verdict speaks, as on /sym.info: "unknown" is
    # the normal state of a hand-written table, not a warning.
    parts = [detail["converter"]] if detail["converter"] else []
    parts.append(f"{detail['typed']} typed")
    if detail["status"] == "stale":
        parts.append("STALE")
    return ", ".join(parts)


def _device_note(detail: dict[str, Any]) -> str:
    return f"ref {detail['ref']}" if detail["ref"] else ""


def _profile_note(detail: dict[str, Any]) -> str:
    return f"rev {detail['revision']}" if detail["revision"] else ""


_NOTES: Final[dict[str, Callable[[dict[str, Any]], str]]] = {
    PLUGIN: _plugin_note,
    SYMBOLS: _symbols_note,
    DEVICE: _device_note,
    PROFILE: _profile_note,
}


def _note(record: Record) -> str:
    if record["error"]:
        return f"error: {record['error']}"
    return _NOTES[record["kind"]](record["detail"])


COLUMNS: Final[tuple[Column, ...]] = (
    Column("KIND", lambda record: record["kind"]),
    Column("NAME", lambda record: record["name"]),
    Column("LAYER", lambda record: record["layer"]),
    Column("FILE", lambda record: record["file"]),
    Column("COUNT", lambda record: "" if record["error"] else str(record["count"]), right=True),
    Column("NOTE", _note),
)


def diag_line(record: Record) -> str:
    """A row as one line of the diagnostic log, the table's facts in its order.

    ``plugin hello (global plugin/hello.py): 1, commands hello``;
    a failed row ends with its ``error:`` note.  The callers that add rows
    write it (``diag_log``), so this module stays free of I/O.
    """
    location = f"{record['layer']} {record['file']}".strip()
    count = "" if record["error"] else f"{record['count']}, "
    return f"{record['kind']} {record['name']} ({location}): {count}{_note(record)}".rstrip(", ")


def result(records: list[Record]) -> CmdResult:
    """The report as a list answer: what startup prints and ``/cfg.loaded`` returns.

    ``value`` is the files that loaded, one per line.
    """
    files = "\n".join(record["file"] for record in records if not record["error"])
    return CmdResult.table("Loaded", "file", COLUMNS, records, value=files)
