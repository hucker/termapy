"""The ``/firmware`` command and the table behind it: every shipped target is listed, copyable and printable.

``termapy.firmware.TARGETS`` is the one registry of the device-side sources
termapy ships.  These tests pin that each row's files exist in the package,
that the resolver's three failures name their choices, that a copy is byte
for byte and never overwrites, and that the command's verbs answer through
dispatch the way the CLI and MCP reach them.  The C itself is compiled by
``test_firmware_mem.py`` and ``test_firmware_json.py``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from termapy.firmware import (
    TARGETS,
    Target,
    copy_target,
    find_target,
    source_path,
    source_text,
)
from termapy.plugins import CapabilitySet
from termapy.repl import ReplEngine

HELP_PAGE = Path(__file__).parent.parent / "src" / "termapy" / "help" / "firmware.md"


@dataclass
class Env:
    """A bare engine and the lines it printed."""

    engine: ReplEngine
    output: list[str]


@pytest.fixture
def env(tmp_path) -> Env:
    """A bare engine granted what ``/firmware.copy`` declares (host-wide file access)."""
    cfg = {"port": "COM4", "baud_rate": 115200}
    config_path = tmp_path / "cfg" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    output: list[str] = []
    engine = ReplEngine(cfg, str(config_path), lambda text, color=None: output.append(text))
    engine.ctx.ns("flags")["output_level"] = "verbose"
    engine.ctx.capabilities = CapabilitySet(filesystem_unconfined=True)
    return Env(engine, output)


class TestTable:
    def test_every_target_ships_its_files(self):
        """Each row of TARGETS names files that exist in the package, non-empty: a row is never a promise."""
        for target in TARGETS:
            for name in target.files:
                assert source_path(target, name).is_file(), f"{target.name}: {name} is missing"
                assert source_text(target, name), f"{target.name}: {name} is empty"

    def test_find_target_defaults_the_only_language(self):
        """A target shipped in one language needs no lang=."""
        actual = find_target("mem")
        assert actual.language == "c", "the only language is the default"

    def test_find_target_demands_a_language_when_several(self):
        """With two languages for one name, leaving lang= off names the choices instead of guessing."""
        table = (
            Target("mem", "c", ("termapy_mem.c",)),
            Target("mem", "rust", ("termapy_mem.rs",)),
        )
        with pytest.raises(ValueError, match=re.escape("Specify a language: lang=c|rust")):
            find_target("mem", targets=table)
        assert find_target("mem", "rust", targets=table).language == "rust"

    def test_find_target_rejects_an_unknown_name_and_language(self):
        """The two lookup failures list what exists, in the standard phrasing."""
        with pytest.raises(ValueError, match=re.escape("Unknown target: nosuch (use json, mem)")):
            find_target("nosuch")
        with pytest.raises(ValueError, match=re.escape("Unknown language: zz (use c)")):
            find_target("mem", "zz")

    def test_copy_is_byte_for_byte_and_refuses_a_second_copy(self, tmp_path):
        """A copy reproduces the shipped bytes exactly, and an existing file stops the whole copy."""
        target = find_target("mem")

        # Act
        written = copy_target(target, tmp_path / "fw")

        # Assert
        assert [path.name for path in written] == list(target.files), "every file, in order"
        for path, name in zip(written, target.files, strict=True):
            assert path.read_bytes() == source_path(target, name).read_bytes(), name
        with pytest.raises(
            FileExistsError, match=re.escape(str(tmp_path / "fw" / "termapy_mem.c"))
        ):
            copy_target(target, tmp_path / "fw")

    def test_help_page_table_lists_every_target(self):
        """help/firmware.md's table is TARGETS: a row added to one appears in the other."""
        page = HELP_PAGE.read_text(encoding="utf-8")
        rows = re.findall(r"^\| `(\w+)`\s+\| \w+\s+\| (.+?)\s+\|", page, flags=re.MULTILINE)
        documented = {name: tuple(re.findall(r"`([^`]+)`", files)) for name, files in rows}
        expected = {target.name: target.files for target in TARGETS}
        assert documented == expected, "the help table and TARGETS name the same files"


class TestCommands:
    def test_firmware_list_is_the_table(self, env: Env):
        """/firmware.list answers with one record per target, files and size as real values."""
        result = env.engine.dispatch("firmware.list")
        assert result.success, result.error
        names = [(record["name"], record["language"]) for record in result.data]
        assert names == [(target.name, target.language) for target in TARGETS]
        assert all(record["bytes"] > 0 for record in result.data), "size is the files' bytes"

    def test_bare_firmware_lists(self, env: Env):
        """Bare /firmware runs its declared bare_sub, the listing."""
        bare = env.engine.dispatch("firmware")
        listed = env.engine.dispatch("firmware.list")
        assert bare.data == listed.data, "bare and .list are one answer"

    def test_firmware_copy_writes_the_target_and_refuses_twice(self, env: Env, tmp_path):
        """/firmware.copy writes the files, reports them, and the second copy fails on the first file."""
        folder = tmp_path / "out"

        # Act
        first = env.engine.dispatch(f"firmware.copy mem folder={folder}")
        second = env.engine.dispatch(f"firmware.copy mem folder={folder}")

        # Assert
        assert first.success, first.error
        assert (folder / "termapy_mem.c").is_file() and (folder / "termapy_mem.h").is_file()
        assert first.data == {
            "files": [str(folder / "termapy_mem.c"), str(folder / "termapy_mem.h")]
        }, "the paths written are the data"
        assert any(
            line.startswith("Copied termapy_mem.c, termapy_mem.h to ") for line in env.output
        )
        assert not second.success, "an existing copy is never overwritten"
        assert second.error.startswith("File exists: "), second.error

    def test_firmware_copy_unknown_target_fails(self, env: Env, tmp_path):
        """An unknown name fails before anything is written, listing the targets."""
        result = env.engine.dispatch(f"firmware.copy nosuch folder={tmp_path / 'out'}")
        assert not result.success
        assert result.error == "Unknown target: nosuch (use json, mem)"
        assert not (tmp_path / "out").exists(), "nothing is created for an unknown target"

    def test_firmware_dump_prints_one_file(self, env: Env):
        """/firmware.dump <name> <file> prints the file and makes its text the value, with the facts as data."""
        expected = source_text(find_target("mem"), "termapy_mem.h")

        result = env.engine.dispatch("firmware.dump mem termapy_mem.h")

        assert result.success, result.error
        assert result.value == expected, "the file's text is the scriptable value"
        assert result.data["file"] == "termapy_mem.h"
        assert result.data["bytes"] == len(expected.encode("utf-8")), "bytes is the UTF-8 size"
        assert env.output[-1] == expected.splitlines()[-1], "every line reached the terminal"

    def test_firmware_dump_needs_a_file_name_for_a_pair(self, env: Env):
        """A target shipping two files will not guess which one to print."""
        result = env.engine.dispatch("firmware.dump mem")
        assert not result.success
        assert result.error == "Specify a file: termapy_mem.c, termapy_mem.h"

    def test_firmware_dump_rejects_a_file_the_target_lacks(self, env: Env):
        """A file name outside the target's row is a lookup failure that lists the row."""
        result = env.engine.dispatch("firmware.dump mem nosuch.h")
        assert not result.success
        assert result.error == "Unknown file: nosuch.h (use termapy_mem.c, termapy_mem.h)"

    def test_firmware_show_is_gated_on_gui_apps(self, env: Env):
        """/firmware.show opens a viewer, so without gui_apps the gate answers, not the handler."""
        result = env.engine.dispatch("firmware.show mem")
        assert not result.success, "no desktop, no viewer"
