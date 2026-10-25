"""The device-side sources termapy ships, one command away: the ``/firmware`` registry.

A device speaks termapy's MEM wire spec or its JSON reply envelope by
compiling a drop-in module into its monitor.  Those modules ship in the
wheel, here, so a user types ``/firmware.copy`` instead of finding the
repository, and an agent over MCP fetches the matching source with
``/firmware.dump``.  Two tables drive every surface -- the listing,
``.copy``, ``.show``, ``.dump``, the firmware help page and
``tests/test_firmware.py``:

- ``LANGUAGES`` says what one module consists of in each language: in C a
  module is a ``.c`` and its ``.h``; a language with one file per module
  lists one extension.
- ``TARGETS`` is one row per module and language (``mem`` in C, ``json`` in
  C).  A row's files are derived, ``termapy_<name>`` with each of the
  language's extensions; a row that is not a module (a board project)
  lists its files explicitly instead.

A new language is a ``LANGUAGES`` entry, a folder (``c/``, ``rust/``) and
a row per module; a board example is a row with its own file list.  Never
a branch in a handler.  The sources are read through ``importlib.resources``
so an installed wheel and a checkout behave the same.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

NAMES: dict[str, str] = {
    "mem": "the MEM wire spec: read, write and dump memory and registers by address",
    "json": "the JSON-RPC reply envelope: one typed answer per command, beside the text mode",
}


@dataclass(frozen=True)
class Language:
    """One language the sources ship in.

    Attributes:
        description: One line for the listing and the help page.
        extensions: What one module consists of, main source first: C is a
            ``.c`` and its ``.h``; a language with one file per module
            lists one.
    """

    description: str
    extensions: tuple[str, ...]


LANGUAGES: dict[str, Language] = {
    "c": Language("C99, no dependency beyond the standard headers", (".c", ".h")),
}


@dataclass(frozen=True)
class Target:
    """One shipped source set.

    Attributes:
        name: What the user asks for (``mem``, ``json``).
        language: The ``LANGUAGES`` key; also the folder the files sit in.
        files: An explicit file list, main source first, for a row that
            is not a module (a board project); empty for a module, whose
            files follow from its name and language (:attr:`sources`).
        guide: The help page that walks through integrating it, or ``""``.
        platform: The board a complete example targets; ``""`` for a
            portable module.
    """

    name: str
    language: str
    files: tuple[str, ...] = ()
    guide: str = ""
    platform: str = ""

    @property
    def sources(self) -> tuple[str, ...]:
        """The files this row ships: the explicit list, or ``termapy_<name>`` per extension."""
        if self.files:
            return self.files
        extensions = LANGUAGES[self.language].extensions
        return tuple(f"termapy_{self.name}{extension}" for extension in extensions)


TARGETS: tuple[Target, ...] = (
    Target("mem", "c"),
    Target("json", "c", guide="firmware-json"),
)


def select_targets(
    name: str | None, language: str | None = None, targets: Sequence[Target] = TARGETS
) -> list[Target]:
    """The rows a name and a language address: one module, or every module of a language.

    Without a name, every module of the language; without a language, the
    only language the rows ship in, or a demand to name one.

    Args:
        name: A target name (``mem``), or None for every target.
        language: A ``LANGUAGES`` key, or None for the only one.
        targets: The table; a test passes its own.

    Returns:
        The matching rows, in table order; never empty.

    Raises:
        ValueError: ``Unknown target: X``, ``Unknown language: X`` or
            ``Specify a language: lang=c|rust``, each listing the choices.
    """
    rows = list(targets)
    if name is not None:
        rows = [target for target in rows if target.name == name]
        if not rows:
            known = ", ".join(sorted({target.name for target in targets}))
            raise ValueError(f"Unknown target: {name} (use {known})")
    languages = sorted({target.language for target in rows})
    if language is None:
        if len(languages) == 1:
            return rows
        raise ValueError(f"Specify a language: lang={'|'.join(languages)}")
    chosen = [target for target in rows if target.language == language]
    if not chosen:
        raise ValueError(f"Unknown language: {language} (use {', '.join(languages)})")
    return chosen


def find_target(
    name: str, language: str | None = None, targets: Sequence[Target] = TARGETS
) -> Target:
    """The one row ``name`` and ``language`` address (what ``.show`` and ``.dump`` need).

    Raises:
        ValueError: As :func:`select_targets`.
    """
    rows = select_targets(name, language, targets)
    assert len(rows) == 1, f"{name}/{language} names {len(rows)} rows; (name, language) is a key"
    return rows[0]


def source_path(target: Target, name: str) -> Path:
    """Where ``name``, one of the target's files, sits in the installed package.

    Raises:
        ValueError: ``Unknown file: X``, listing the target's files.
    """
    if name not in target.sources:
        raise ValueError(f"Unknown file: {name} (use {', '.join(target.sources)})")
    return Path(str(files(__name__) / target.language / name))


def source_text(target: Target, name: str) -> str:
    """The text of ``name``, one of the target's files."""
    return source_path(target, name).read_text(encoding="utf-8")


def source_size(target: Target) -> int:
    """The target's files' total size in UTF-8 bytes with ``\\n`` line ends, for the listing.

    Measured from the text, not the file, so a CRLF checkout and an LF one
    report the same size and the gold output holds on every platform.
    """
    return sum(len(source_text(target, name).encode("utf-8")) for name in target.sources)


def copy_targets(targets: Sequence[Target], folder: Path) -> list[Path]:
    """Write every file of ``targets`` into ``folder``, byte for byte, creating the folder.

    Refuses before writing anything if any file is already there: a copy the
    user has edited is never overwritten, and a half-written kit never
    exists.

    Args:
        targets: The rows to copy, as :func:`select_targets` returns them.
        folder: Where to write; created if missing.  The user's own folder,
            not a data folder, so a raw ``mkdir`` is right here.

    Returns:
        The paths written, in table then file order.

    Raises:
        FileExistsError: Its message is the path that already exists.
        OSError: The folder could not be created or a file not written.
    """
    folder.mkdir(parents=True, exist_ok=True)
    pairs = [(target, name) for target in targets for name in target.sources]
    for _, name in pairs:
        if (folder / name).exists():
            raise FileExistsError(str(folder / name))
    written: list[Path] = []
    for target, name in pairs:
        destination = folder / name
        destination.write_bytes(source_path(target, name).read_bytes())
        written.append(destination)
    return written
