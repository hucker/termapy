"""What a click on a file does, declared once per folder, and read alike by every .list table and /cfg.info's tree."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from termapy.folder_ops import (
    file_click,
    folder_links,
    folder_list_command,
    print_click,
    root_file_click,
)
from termapy.folders import FILE_PRINT, FILE_PRINT_MAX_BYTES, FOLDERS, ROOT_FILE_OPEN
from termapy.tree_render import FileTree


@pytest.mark.rule("folder-links-declared")
def test_every_declared_click_is_a_command(cli):
    """A folder's family has a .list, its open and edit and every root file's opener name registered commands; a folder with no family declares none."""
    # Arrange
    plugins = cli.repl.ctx.internal.plugins

    # Act
    wrong = []
    for spec in FOLDERS:
        if not spec.family:
            if spec.open_command or spec.edit:
                wrong.append(f"{spec.name}: a click with no family")
            continue
        for command in (f"{spec.family}.list", spec.open_command, spec.edit):
            if command and command not in plugins:
                wrong.append(f"{spec.name}: {command}")
    wrong += [
        f"root {suffix}: {command}"
        for suffix, command in {**ROOT_FILE_OPEN, "any": FILE_PRINT}.items()
        if command not in plugins
    ]

    # Assert
    assert wrong == [], "every declared click names a command that exists"


@pytest.mark.rule("folder-links-declared")
@pytest.mark.parametrize("spec", FOLDERS, ids=lambda spec: spec.name)
def test_a_list_and_the_tree_click_alike(spec):
    """folder_links, which a .list table takes, and file_click, which the tree takes, give one answer."""
    # Arrange
    links = folder_links(spec.name)
    record = {"name": "a.txt"}

    # Act
    actual = tuple(link(record) if link else "" for link in (links["run"], links["edit"]))

    # Assert
    assert actual == file_click(spec.name, "a.txt")


def test_a_run_file_runs_and_edits():
    """A .run file's name runs it and its edit opens it, as /run.list always did; the folder lists."""
    actual = (file_click("run", "boot.run"), folder_list_command("run"))
    expected = (("run boot.run", "run.edit boot.run"), "run.list")
    assert actual == expected


def test_a_symbols_file_loads_and_every_folder_lists():
    """A click on a table in sym/ loads it, a visualizer opens in the viewer with edit beside it, and lib/ lists as /dev.lib.list."""
    actual = (
        file_click("sym", "demo.symbols.json"),
        file_click("viz", "mine.py"),
        file_click("lib", "part.device.json"),
        folder_list_command("sym"),
        folder_list_command("lib"),
    )
    expected = (
        ("sym.load demo.symbols.json", ""),
        ("proto.viz.show mine.py", "proto.viz.edit mine.py"),
        ("dev.lib.show part.device.json", ""),
        "sym.list",
        "dev.lib.list",
    )
    assert actual == expected


def test_a_folder_the_table_lacks_declares_no_click():
    """A name that is no data folder has no click and no list: nothing is guessed."""
    actual = (file_click("nosuch", "a.txt"), folder_list_command("nosuch"))
    assert actual == (("", ""), "")


def test_a_root_file_opens_by_its_suffix():
    """At a config's root its config, report and log open; any other file prints in the log window."""
    actual = [
        root_file_click("demo", name)
        for name in ("demo.cfg", "demo.md", "demo.log", "demo.mcp.log", "other.cfg")
    ]
    expected = ["cfg.edit", "edit.info", "edit.log", "cfg.file demo.mcp.log", "cfg.file other.cfg"]
    assert actual == expected


def test_a_big_file_opens_in_the_viewer():
    """Over FILE_PRINT_MAX_BYTES a printed click opens the file with --display instead: a root file, or any file nothing declares."""
    big = FILE_PRINT_MAX_BYTES + 1
    actual = [
        print_click("dev/chip.device.json", big),
        print_click("dev/chip.device.json", 100),
        root_file_click("demo", "notes.json", big),
        root_file_click("demo", "notes.json", 100),
    ]
    expected = [
        "cfg.file --display dev/chip.device.json",
        "cfg.file dev/chip.device.json",
        "cfg.file --display notes.json",
        "cfg.file notes.json",
    ]
    assert actual == expected


def test_a_tree_line_links_what_click_names():
    """FileTree.pieces makes each name a link to what click returns, with a dim edit beside a file that has one."""
    # Arrange
    tree = FileTree([("run/", ["boot.run"])])

    def click(folder: str, name: str) -> tuple[str, str]:
        return file_click(folder, name) if folder else (folder_list_command(name[:-1]), "")

    # Act
    lines = tree.pieces(click)

    # Assert
    actual = [[piece.link.command for piece in line if piece.link] for line in lines]
    expected = [["run.list"], ["run boot.run", "run.edit boot.run"]]
    assert actual == expected


class TestCfgInfo:
    @pytest.mark.rule("folder-links-declared")
    def test_json_is_the_files_by_folder(self, cli, capsys):
        """/cfg.info --json answers the files by folder, the root's apart with its report and no dotfile, no drawn tree."""
        # Arrange
        folder = Path(cli.ctx.config_path).parent
        (folder / "run").mkdir()
        (folder / "run" / "boot.run").write_text("# Boot\n", encoding="utf-8")
        (folder / ".hidden").write_text("x", encoding="utf-8")
        capsys.readouterr()

        # Act
        cli.repl.dispatch_full("/cfg.info --json")
        envelope = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

        # Assert
        data = envelope["data"]
        root = [record["name"] for record in data["files"]]
        actual = (
            root[0],
            "rig.md" in root,
            ".hidden" in root,
            [record["name"] for record in data["folders"]["run"]],
            envelope["value"] == data["report"],
            envelope["output_lines"],
        )
        expected = ("rig.cfg", True, False, ["boot.run"], True, [])
        assert actual == expected, "each file once, as data, the tree not repeated as text"
        assert set(data["folders"]["run"][0]) == {"name", "bytes", "mtime", "age_s"}, (
            "the record every .list answers"
        )
