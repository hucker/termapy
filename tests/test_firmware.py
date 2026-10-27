"""The ``/firmware`` command and the tables behind it: every shipped target is listed, copyable and printable.

``termapy.firmware.LANGUAGES`` says what a module consists of per language
and ``TARGETS`` is one row per module and language; together they are the
registry of the device-side sources termapy ships.  These tests pin that a
row's files follow from its language, that each file exists in the package
and is plain ASCII, that the resolver's failures name their choices, that a
copy is byte for byte and never overwrites, and that the command's verbs
answer through dispatch the way the CLI and MCP reach them.  The C itself is
compiled by ``test_firmware_mem.py`` and ``test_firmware_json.py``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from termapy.boards import BOARDS, firmware_paths
from termapy.firmware import (
    TARGETS,
    Target,
    copy_targets,
    find_target,
    select_targets,
    source_path,
    source_text,
)
from termapy.plugins import CapabilitySet
from termapy.repl import ReplEngine

HELP_PAGE = Path(__file__).parent.parent / "src" / "termapy" / "help" / "firmware.md"

# A table with two languages for one module, which the shipped one does not have yet.
TWO_LANGUAGES = (
    Target("mem", "c"),
    Target("mem", "rust", files=("termapy_mem.rs",)),
)


@dataclass
class Env:
    """A bare engine and the lines it printed."""

    engine: ReplEngine
    output: list[str]


@pytest.fixture
def env(tmp_path) -> Env:
    """A bare engine with a loaded config and host-wide file access, as the TUI and CLI have."""
    cfg = {"port": "COM4", "baud_rate": 115200}
    config_path = tmp_path / "cfg" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    output: list[str] = []
    engine = ReplEngine(cfg, str(config_path), lambda text, color=None: output.append(text))
    engine.ctx.ns("flags")["output_level"] = "verbose"
    engine.ctx.capabilities = CapabilitySet(filesystem_unconfined=True)
    engine.ctx.sync_capabilities()
    engine.ctx.config_path = str(config_path)  # a loaded config, as a frontend sets it
    return Env(engine, output)


def _non_ascii(path: Path) -> list[str]:
    """The code points above ASCII in ``path``, as ``U+XXXX``, sorted and unique."""
    return sorted(
        {f"U+{ord(char):04X}" for char in path.read_text(encoding="utf-8") if ord(char) > 0x7F}
    )


class TestTable:
    def test_a_module_s_files_follow_its_language(self):
        """A C module is termapy_<name>.c and .h from LANGUAGES; a row with its own file list keeps it."""
        assert Target("mem", "c").sources == ("termapy_mem.c", "termapy_mem.h")
        assert Target("pico", "c", files=("CMakeLists.txt", "main.c")).sources == (
            "CMakeLists.txt",
            "main.c",
        ), "an explicit list is a board project's, not derived"

    def test_every_target_ships_its_files(self):
        """Each row of TARGETS names files that exist in the package, non-empty: a row is never a promise."""
        for target in TARGETS:
            for name in target.sources:
                assert source_path(target, name).is_file(), f"{target.name}: {name} is missing"
                assert source_text(target, name), f"{target.name}: {name} is empty"

    def test_shipped_sources_are_ascii(self):
        """firmware-ascii-only: Every device-side file /firmware or /board ships is pure ASCII; a rule is --.

        The sources are compiled by toolchains, run on boards, read in build
        logs and editors that mishandle UTF-8, and travel in a JSON envelope
        where each non-ASCII character is six characters of ``\\uXXXX``.
        Until 2026-10-25 the four modules shipped 867 box-drawing rule
        characters.
        """
        shipped = {
            f"{target.name}/{name}": source_path(target, name)
            for target in TARGETS
            for name in target.sources
        }
        shipped |= {
            f"board {board.name}/{path.name}": path
            for board in BOARDS
            for path in firmware_paths(board)
        }
        offenders = {name: chars for name, path in shipped.items() if (chars := _non_ascii(path))}
        assert offenders == {}, f"non-ASCII in shipped sources (write -- for a rule): {offenders}"

    def test_ascii_guard_fires_on_a_probe(self, tmp_path):
        """The scan names a box-drawing rule and an accented letter, and passes a hyphen rule."""
        probe = tmp_path / "probe.c"
        probe.write_text("/* ── rule ── */ café\n", encoding="utf-8")
        assert _non_ascii(probe) == ["U+00E9", "U+2500"], "each code point once, sorted"
        probe.write_text("/* -- rule -- */ cafe\n", encoding="utf-8")
        assert _non_ascii(probe) == [], "a hyphen rule is ASCII"

    def test_select_targets_without_a_name_is_the_whole_language(self):
        """No name and one shipped language: every module, in table order; a name narrows to one."""
        assert select_targets(None) == list(TARGETS), "the whole C kit"
        assert select_targets("mem") == [TARGETS[0]]
        assert find_target("mem").language == "c", "the only language is the default"

    def test_select_targets_demands_a_language_when_several(self):
        """With two languages, leaving lang= off names the choices instead of guessing, with or without a name."""
        with pytest.raises(ValueError, match=re.escape("Specify a language: lang=c|rust")):
            select_targets(None, targets=TWO_LANGUAGES)
        with pytest.raises(ValueError, match=re.escape("Specify a language: lang=c|rust")):
            find_target("mem", targets=TWO_LANGUAGES)
        assert select_targets(None, "rust", TWO_LANGUAGES) == [TWO_LANGUAGES[1]]
        assert find_target("mem", "rust", TWO_LANGUAGES).sources == ("termapy_mem.rs",)

    def test_select_targets_rejects_an_unknown_name_and_language(self):
        """The two lookup failures list what exists, in the standard phrasing."""
        with pytest.raises(ValueError, match=re.escape("Unknown target: nosuch (use json, mem)")):
            select_targets("nosuch")
        with pytest.raises(ValueError, match=re.escape("Unknown language: zz (use c)")):
            select_targets("mem", "zz")
        with pytest.raises(ValueError, match=re.escape("Unknown language: zz (use c)")):
            select_targets(None, "zz")

    def test_copy_is_byte_for_byte_and_refuses_before_writing(self, tmp_path):
        """A copy reproduces the shipped bytes exactly; one existing file stops the whole kit, untouched."""
        mem, json_target = TARGETS
        folder = tmp_path / "fw"

        # Act -- one module first, then the whole kit over it
        written = copy_targets([json_target], folder)

        # Assert
        assert [path.name for path in written] == list(json_target.sources), "every file, in order"
        for path, name in zip(written, json_target.sources, strict=True):
            assert path.read_bytes() == source_path(json_target, name).read_bytes(), name
        with pytest.raises(FileExistsError, match=re.escape(str(folder / "termapy_json.c"))):
            copy_targets([mem, json_target], folder)
        assert not (folder / "termapy_mem.c").exists(), "nothing of the kit is written on a refusal"

    def test_help_page_table_lists_every_target(self):
        """help/firmware.md's table is TARGETS: a row added to one appears in the other."""
        page = HELP_PAGE.read_text(encoding="utf-8")
        rows = re.findall(r"^\| `(\w+)`\s+\| \w+\s+\| (.+?)\s+\|", page, flags=re.MULTILINE)
        documented = {name: tuple(re.findall(r"`([^`]+)`", files)) for name, files in rows}
        expected = {target.name: target.sources for target in TARGETS}
        assert documented == expected, "the help table and TARGETS name the same files"


class TestCommands:
    def test_firmware_list_is_the_table(self, env: Env):
        """/firmware.list answers with one record per target, files and size as real values."""
        result = env.engine.dispatch("firmware.list")
        assert result.success, result.error
        names = [(record["name"], record["language"]) for record in result.data]
        assert names == [(target.name, target.language) for target in TARGETS]
        assert result.data[0]["files"] == ["termapy_mem.c", "termapy_mem.h"], (
            "files from the language"
        )
        assert all(record["bytes"] > 0 for record in result.data), "size is the files' bytes"

    def test_bare_firmware_lists(self, env: Env):
        """Bare /firmware runs its declared bare_sub, the listing."""
        bare = env.engine.dispatch("firmware")
        listed = env.engine.dispatch("firmware.list")
        assert bare.data == listed.data, "bare and .list are one answer"

    def test_firmware_copy_without_a_name_writes_the_whole_kit(self, env: Env, tmp_path):
        """/firmware.copy alone writes every module of the one shipped language; lang=c says the same."""
        folder = tmp_path / "kit"

        result = env.engine.dispatch(f"firmware.copy folder={folder}")

        assert result.success, result.error
        expected = ["termapy_mem.c", "termapy_mem.h", "termapy_json.c", "termapy_json.h"]
        assert [Path(path).name for path in result.data["files"]] == expected
        assert all((folder / name).is_file() for name in expected), "all four files landed"
        named = env.engine.dispatch(f"firmware.copy lang=c folder={tmp_path / 'kit2'}")
        assert named.success and len(named.data["files"]) == 4, "lang=c is the same kit"

    def test_firmware_copy_writes_one_module_and_refuses_twice(self, env: Env, tmp_path):
        """/firmware.copy <name> writes that module's files, reports them, and a second copy fails on the first file."""
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

    def test_firmware_copy_unknown_target_or_language_fails(self, env: Env, tmp_path):
        """An unknown name or language fails before anything is written, listing the choices."""
        bad_name = env.engine.dispatch(f"firmware.copy nosuch folder={tmp_path / 'out'}")
        bad_lang = env.engine.dispatch(f"firmware.copy lang=rust folder={tmp_path / 'out'}")
        assert bad_name.error == "Unknown target: nosuch (use json, mem)"
        assert bad_lang.error == "Unknown language: rust (use c)"
        assert not (tmp_path / "out").exists(), "nothing is created for a bad request"

    def test_firmware_copy_defaults_to_the_config_folder(self, env: Env, tmp_path):
        """With no folder= the files go in the config's firmware/ folder, never the working directory."""
        # Act
        result = env.engine.dispatch("firmware.copy mem")

        # Assert
        folder = (tmp_path / "cfg" / "firmware").resolve()
        assert result.success, result.error
        assert (folder / "termapy_mem.c").is_file(), "the config's firmware/ holds the copy"
        assert any(
            line == f"Copied termapy_mem.c, termapy_mem.h to {folder}" for line in env.output
        )
        assert not Path("termapy_mem.c").exists(), "the working directory is never written"

    def test_firmware_copy_relative_folder_is_under_the_config(self, env: Env, tmp_path):
        """A relative folder= lands under the config's folder, not the process working directory."""
        result = env.engine.dispatch("firmware.copy json folder=fw")
        assert result.success, result.error
        assert (tmp_path / "cfg" / "fw" / "termapy_json.c").is_file()

    def test_firmware_copy_outside_the_sandbox_needs_host_access(self, env: Env, tmp_path):
        """Without host-wide file access (MCP's default), an absolute or climbing folder is refused, nothing written."""
        # Arrange -- the MCP host's sandbox: no filesystem_unconfined
        env.engine.ctx.capabilities = CapabilitySet()
        env.engine.ctx.sync_capabilities()

        # Act
        absolute = env.engine.dispatch(f"firmware.copy mem folder={tmp_path / 'elsewhere'}")
        climbing = env.engine.dispatch("firmware.copy mem folder=../up")
        inside = env.engine.dispatch("firmware.copy mem")

        # Assert
        assert not absolute.success and "outside the config sandbox" in absolute.error
        assert not climbing.success and "outside the config sandbox" in climbing.error
        assert not (tmp_path / "elsewhere").exists() and not (tmp_path / "up").exists()
        assert inside.success, "the config's own firmware/ is inside the sandbox"

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
        """A module shipping two files will not guess which one to print."""
        result = env.engine.dispatch("firmware.dump mem")
        assert not result.success
        assert result.error == "Specify a file: termapy_mem.c, termapy_mem.h"

    def test_firmware_dump_rejects_a_file_the_target_lacks(self, env: Env):
        """A file name outside the module's files is a lookup failure that lists them."""
        result = env.engine.dispatch("firmware.dump mem nosuch.h")
        assert not result.success
        assert result.error == "Unknown file: nosuch.h (use termapy_mem.c, termapy_mem.h)"

    def test_firmware_show_is_gated_on_gui_apps(self, env: Env):
        """/firmware.show opens a viewer, so without gui_apps the gate answers, not the handler."""
        result = env.engine.dispatch("firmware.show mem")
        assert not result.success, "no desktop, no viewer"
