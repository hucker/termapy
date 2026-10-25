"""The device-side sources termapy ships, one command away: the ``/firmware`` registry.

A device speaks termapy's MEM wire spec or its JSON reply envelope by
compiling a drop-in module into its monitor.  Those modules ship in the
wheel, here, so a user types ``/firmware.copy mem`` instead of finding the
repository, and an agent over MCP fetches the matching source with
``/firmware.dump``.  Everything shipped is a row of one table, ``TARGETS``,
and every surface iterates it: the listing, ``.copy``, ``.show``, ``.dump``,
the firmware help page and ``tests/test_firmware.py``, which checks that each
row's files exist in the package.  A new target -- a second language, a
board example -- is a row and a folder, never a branch in a handler.

Rows are addressed by ``name`` (``mem``, ``json``; a board example would be
named for the board) and ``language``.  The sources live one folder per
language (``c/`` today), read through ``importlib.resources`` so an
installed wheel and a checkout behave the same.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

NAMES: dict[str, str] = {
    "mem": "the MEM wire spec: read, write and dump memory and registers by address",
    "json": "the JSON-RPC reply envelope: one typed answer per command, beside the text mode",
}

LANGUAGES: dict[str, str] = {
    "c": "C99, no dependency beyond the standard headers",
}


@dataclass(frozen=True)
class Target:
    """One shipped source set.

    Attributes:
        name: What the user asks for (``mem``, ``json``).
        language: The ``LANGUAGES`` key; also the folder the files sit in.
        files: The file names, the main source first (``.show`` opens it).
        guide: The help page that walks through integrating it, or ``""``.
        platform: The board a complete example targets; ``""`` for a
            portable module.
    """

    name: str
    language: str
    files: tuple[str, ...]
    guide: str = ""
    platform: str = ""


TARGETS: tuple[Target, ...] = (
    Target("mem", "c", ("termapy_mem.c", "termapy_mem.h")),
    Target("json", "c", ("termapy_json.c", "termapy_json.h"), guide="firmware-json"),
)


def find_target(
    name: str, language: str | None = None, targets: tuple[Target, ...] = TARGETS
) -> Target:
    """The row ``name`` and ``language`` address.

    The language may be left off when the name ships in one language; with
    several it must be given, and the error names them.

    Args:
        name: A target name (``mem``).
        language: A ``LANGUAGES`` key, or None for the only one.
        targets: The table; a test passes its own.

    Returns:
        The target.

    Raises:
        ValueError: ``Unknown target: X``, ``Unknown language: X`` or
            ``Specify a language: lang=c|rust``, each listing the choices.
    """
    rows = [target for target in targets if target.name == name]
    if not rows:
        known = ", ".join(sorted({target.name for target in targets}))
        raise ValueError(f"Unknown target: {name} (use {known})")
    if language is None:
        if len(rows) == 1:
            return rows[0]
        choices = "|".join(sorted(target.language for target in rows))
        raise ValueError(f"Specify a language: lang={choices}")
    for target in rows:
        if target.language == language:
            return target
    choices = ", ".join(sorted(target.language for target in rows))
    raise ValueError(f"Unknown language: {language} (use {choices})")


def source_path(target: Target, name: str) -> Path:
    """Where ``name``, one of the target's files, sits in the installed package.

    Raises:
        ValueError: ``Unknown file: X``, listing the target's files.
    """
    if name not in target.files:
        raise ValueError(f"Unknown file: {name} (use {', '.join(target.files)})")
    return Path(str(files(__name__) / target.language / name))


def source_text(target: Target, name: str) -> str:
    """The text of ``name``, one of the target's files."""
    return source_path(target, name).read_text(encoding="utf-8")


def source_size(target: Target) -> int:
    """The target's files' total size in UTF-8 bytes with ``\\n`` line ends, for the listing.

    Measured from the text, not the file, so a CRLF checkout and an LF one
    report the same size and the gold output holds on every platform.
    """
    return sum(len(source_text(target, name).encode("utf-8")) for name in target.files)


def copy_target(target: Target, folder: Path) -> list[Path]:
    """Write the target's files into ``folder``, byte for byte, creating the folder.

    Refuses before writing anything if a file is already there: a copy the
    user has edited is never overwritten.

    Args:
        target: The row to copy.
        folder: Where to write; created if missing.  The user's own folder,
            not a data folder, so a raw ``mkdir`` is right here.

    Returns:
        The paths written, in the target's file order.

    Raises:
        FileExistsError: Its message is the path that already exists.
        OSError: The folder could not be created or a file not written.
    """
    folder.mkdir(parents=True, exist_ok=True)
    for name in target.files:
        if (folder / name).exists():
            raise FileExistsError(str(folder / name))
    written: list[Path] = []
    for name in target.files:
        destination = folder / name
        destination.write_bytes(source_path(target, name).read_bytes())
        written.append(destination)
    return written
