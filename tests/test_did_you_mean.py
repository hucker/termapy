"""did-you-mean-one: every name typed from memory answers a typo with the names it most likely meant.

One function picks the names (``matching.closest``) and one phrases them
(``matching.did_you_mean``).  These run each place a name is typed from
memory -- a command, a help page, a config key and a config name, a
script and a data-folder file, a symbol or register, a loaded device and
a library part -- against a real demo config, so a site that loses its
suggestion fails here.  The algorithm itself is ``tests/test_matching.py``.
"""

# cspell:ignore hlep baudrat welcom zzzzzzzz dmeo legcy notse firmwar cgf -- the typos these tests make on purpose

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from termapy.cli import CLITerminal
from termapy.config import setup_demo_config
from termapy.folders import SYMBOLS_SUFFIX, file_suggestion
from termapy.plugins.views import Link, error_pieces

_PART = {
    "device_version": 1,
    "device": "acme-adc16",
    "registers": [{"name": "STATUS", "addr": "0x60000000", "size": 4}],
}


@pytest.fixture
def demo(tmp_path, monkeypatch):
    """A CLI host on a fresh demo config, with one part in the shared library."""
    cfg_root = tmp_path / "termapy_cfg"
    config_path = setup_demo_config(cfg_root)
    # /cfg.load lists the configs under cfg_dir(), which reads this variable
    # before the working directory: the test's tree, never the user's.
    monkeypatch.setenv("TERMAPY_CFG_DIR", str(cfg_root))
    library = cfg_root / "lib" / "acme"
    library.mkdir(parents=True)
    (library / "acme-adc16.device.json").write_text(json.dumps(_PART), encoding="utf-8")
    # A vendor file beside the config, the kind of long name that gets mistyped
    (config_path.parent / "PIC32CM5164LE00100.svd").write_text("<device/>", encoding="utf-8")
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    terminal = CLITerminal(cfg, str(config_path), no_color=True, term_width=120)
    terminal.repl.global_root = cfg_root  # the shared layer is this tree's, not the checkout's
    terminal.repl.fire_lifecycle("on_app_start")  # loads the demo's symbols and devices
    return terminal


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("/hlep", "Unknown command: hlep -- did you mean /help"),
        ("/help hlep", "No command matches 'hlep' -- did you mean help"),
        ("/cfg baudrat", "Unknown config key: baudrat -- did you mean baud_rate?"),
        ("/cfg.load dem", "No config matching 'dem' -- did you mean demo?"),
        ("/run welcom", "File not found: welcom -- did you mean welcome?"),
        ("/run.help welcom", "Script not found: welcom -- did you mean welcome?"),
        ("/run.dump welcom.rnu", "File not found: welcom.rnu -- did you mean welcome.run?"),
        ("/sym u1_mode", "Unknown symbol: u1_mode -- did you mean U1_MODE"),
        ("/dev.regs demo_uar", "Unknown device: demo_uar -- did you mean demo_uart?"),
        ("/dev.use acme-adc61", "Unknown library part: acme-adc61 -- did you mean acme-adc16?"),
        ("/dev.import PIC32CM5164LE0010.svd", "-- did you mean PIC32CM5164LE00100.svd?"),
        ("/sym.load dmeo", "Symbols not found: dmeo -- did you mean demo?"),
        ("/profile.load demo_legcy", "-- did you mean demo_legacy?"),
        ("/cfg.delete dem", "Config not found: dem -- did you mean demo?"),
        ("/cfg.file demo.cgf", "File not found: demo.cgf -- did you mean demo.cfg?"),
        ("/run.rename welcom hello", "File not found: welcom -- did you mean welcome?"),
    ],
)
def test_a_typo_names_what_was_meant(demo, line, expected):
    """did-you-mean-one: each site fails, and its error names the name that was meant."""
    # Act
    result = demo.repl.dispatch_full(line)
    # Assert
    assert result.success is False, "a name that names nothing is still a failure"
    assert expected in result.error, result.error


@pytest.mark.parametrize(
    ("line", "kept"),
    [
        ("/dev.use zzzzzzzz", "(try /dev.lib to see what there is)"),
        ("/dev.regs zzzzzzzz", "(try /dev.list to see what is loaded)"),
        ("/cfg.load zzzzzzzz", "Try /cfg.list to list available."),
        ("/help zzzzzzzz", "Try /search zzzzzzzz for a deeper search."),
    ],
)
def test_nothing_close_keeps_the_way_to_look(demo, line, kept):
    """did-you-mean-one: with no near name, the error still says where to look, and suggests nothing."""
    result = demo.repl.dispatch_full(line)
    assert kept in result.error, result.error
    assert "did you mean" not in result.error, "no far-fetched suggestion"


def test_a_part_name_given_to_import_points_at_use(demo):
    """/dev.import of a library part's name says the part is converted and names the command that adds it."""
    result = demo.repl.dispatch_full("/dev.import acme-adc16")
    assert result.success is False, "there is no such file to convert"
    assert result.error.endswith("-- did you mean /dev.use acme-adc16?"), result.error


def test_a_proto_file_typo_names_the_file(demo):
    """/proto.run looks in the proto folder after the name as typed, and its miss names the file meant."""
    # Arrange: /proto.run needs an open port, so the gate does not answer first
    assert demo._connect() is True, "the DEMO port opens"
    try:
        # Act
        result = demo.repl.dispatch_full("/proto.run at_tst")
    finally:
        demo._disconnect()
    # Assert
    assert result.success is False, "there is no at_tst.pro"
    assert result.error.endswith("-- did you mean at_test?"), result.error


# -- clickable fixes (registry links-are-data) ---------------------------------


# help and dev.lib only show something; run and dev.use act.
_SHOWS = {"help", "dev.lib"}


def _links(text: str, line: str) -> list[tuple[str, Link]]:
    """The linked pieces of an error line, each with what a click does."""
    pieces = error_pieces(
        text,
        line,
        prefix="/",
        commands={"help", "dev.lib", "dev.use", "run"},
        click_runs=lambda command_line: command_line.split()[0] in _SHOWS,
    )
    return [(piece.text, piece.link) for piece in pieces or () if piece.link is not None]


@pytest.mark.parametrize(
    ("text", "line", "expected"),
    [
        # a name: replaces the word it corrects, the rest of the line kept
        (
            "Error: File not found: welcom -- did you mean welcome?",
            "run welcom",
            [("welcome", Link("run welcome", run=False))],
        ),
        # an offered command replaces the command typed and keeps its arguments;
        # /help only shows a page, so a click runs it
        (
            "Error: Unknown command: held -- did you mean /help?",
            "held me",
            [("/help", Link("help me", run=True))],
        ),
        # an offered command line stands as it is
        (
            "Error: Source file not found: x -- did you mean /dev.use x?",
            "dev.import x",
            [("/dev.use x", Link("dev.use x", run=False))],
        ),
        # a command the text names links to it, and runs because it only lists
        (
            "Error: Unknown library part: x (try /dev.lib to see what there is)",
            "dev.use x",
            [("/dev.lib", Link("dev.lib", run=True))],
        ),
    ],
)
def test_an_errors_fixes_are_links(text, line, expected):
    """links-are-data: a fix runs on a click when its command only shows; one that acts fills the input."""
    assert _links(text, line) == expected


def test_the_pieces_are_the_line_unchanged():
    """links-are-data: the linked line has exactly the characters of the plain one."""
    text = "Error: Unknown symbol: u1_mode -- did you mean U1_MODE, U1MODE?"
    pieces = error_pieces(text, "sym u1_mode", prefix="/", commands=set())
    assert pieces is not None, "two names to link"
    assert "".join(piece.text for piece in pieces) == text, "clicking adds links, never text"


def test_an_error_with_nothing_to_click_stays_plain():
    """links-are-data: no suggestion and no command named means no pieces, so the plain line prints."""
    text = "Error: Invalid address: zz (a path like cfg/demo is not a command)"
    assert error_pieces(text, "mem.read zz", prefix="/", commands={"demo"}) is None


def test_a_frontend_that_can_click_gets_the_fix_as_a_link(demo):
    """A typo at a real site reaches a clicking frontend as a red line whose name fills in the fixed command."""
    # Arrange: a frontend that can click, as the TUI is
    lines: list[tuple[list, str]] = []
    demo.repl.ctx.io._write_segments = lambda pieces, color: lines.append((list(pieces), color))
    # Act
    demo.repl.dispatch_full("/dev.regs demo_uar")
    # Assert
    pieces, color = lines[-1]
    assert color == "red", "still the error line"
    links = [piece.link for piece in pieces if piece.link is not None]
    assert links == [Link("dev.regs demo_uart", run=True)], (
        "/dev.regs only lists, so the click runs the fixed command"
    )


def test_an_acting_command_waits_for_enter(demo):
    """A fix whose command acts (/dev.use writes a file) fills the input line; a click never runs it."""
    lines: list[tuple[list, str]] = []
    demo.repl.ctx.io._write_segments = lambda pieces, color: lines.append((list(pieces), color))
    demo.repl.dispatch_full("/dev.use acme-adc61")
    links = [piece.link for piece in lines[-1][0] if piece.link is not None]
    assert links == [Link("dev.use acme-adc16", run=False)], "the fixed command waits for Enter"


def test_shows_is_for_commands_that_change_nothing():
    """links-are-data: shows= on a command whose safety says it acts fails where it is declared."""
    from termapy.plugins import CmdResult, Command

    with pytest.raises(ValueError, match="shows= is for a command that changes nothing"):
        Command(
            help="x",
            name="x",
            handler=lambda _ctx, _args: CmdResult.ok(value=""),
            shows=True,
            safety="destructive",
        )


# -- file names and paths -----------------------------------------------------


def test_a_path_keeps_what_was_typed_around_the_name(tmp_path):
    """did-you-mean-one: only the last part is matched, and the suggestion is the whole path as typed."""
    (tmp_path / "PIC32CM5164LE00100.svd").write_text("", encoding="utf-8")
    typed = str(tmp_path / "PIC32CM5164LE0010.svd")
    expected = f" -- did you mean {tmp_path / 'PIC32CM5164LE00100.svd'}?"
    assert file_suggestion(tmp_path, typed) == expected, "a click fills in the full path"


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("welcom", " -- did you mean welcome?"),  # a bare name finds the stem
        ("welcom.run", " -- did you mean welcome.run?"),  # a full name finds the name
    ],
)
def test_a_name_is_matched_in_the_form_it_was_typed(tmp_path, typed, expected):
    """did-you-mean-one: with or without its extension, the suggestion reads as the name was typed."""
    (tmp_path / "welcome.run").write_text("", encoding="utf-8")
    assert file_suggestion(tmp_path, typed, ".run") == expected


def test_a_suffix_offers_only_that_kind_of_file(tmp_path):
    """did-you-mean-one: the name without a compound suffix matches, and other kinds are never offered."""
    (tmp_path / "demo.symbols.json").write_text("", encoding="utf-8")
    (tmp_path / "deme.txt").write_text("", encoding="utf-8")
    (tmp_path / ".demo").write_text("", encoding="utf-8")
    actual = file_suggestion(tmp_path, "dmeo", SYMBOLS_SUFFIX)
    assert actual == " -- did you mean demo?", (
        "the symbols file, once; not the .txt, not the dotfile"
    )


def test_each_file_is_offered_once(tmp_path):
    """did-you-mean-one: a file near in two forms (name and stem) is one suggestion, not two."""
    (tmp_path / "abc.y").write_text("", encoding="utf-8")
    assert file_suggestion(tmp_path, "abc.x") == " -- did you mean abc.y?"


def test_a_folder_that_is_not_there_suggests_nothing(tmp_path):
    """did-you-mean-one: a missing folder is no suggestion, never an error."""
    assert file_suggestion(tmp_path / "nowhere", "welcom") == ""


def test_an_absolute_path_to_import_names_the_file_meant(demo, tmp_path):
    """/dev.import with a mistyped absolute path offers the file beside it, as a whole path."""
    # Arrange: a vendor pack somewhere outside the config
    pack = tmp_path / "pack" / "svd"
    pack.mkdir(parents=True)
    (pack / "PIC32CM5164LE00100.svd").write_text("<device/>", encoding="utf-8")
    # Act
    result = demo.repl.dispatch_full(f"/dev.import {pack / 'PIC32CM5164LE0010.svd'}")
    # Assert
    assert result.error.endswith(f"-- did you mean {pack / 'PIC32CM5164LE00100.svd'}?"), (
        result.error
    )


def test_the_desktop_file_commands_name_the_file_meant(demo, tmp_path):
    """/edit and /show, which need a desktop, suggest like every other file command; /show now fails."""
    # Arrange: a desktop, as the TUI on a workstation has
    caps = replace(demo.repl.ctx.capabilities, gui_apps=True, file_editor=True)
    demo.repl.ctx.capabilities = caps
    (tmp_path / "notes.txt").write_text("", encoding="utf-8")
    # Act
    edited = demo.repl.dispatch_full("/edit welcom.run")
    shown = demo.repl.dispatch_full(f"/show {tmp_path / 'notse.txt'}")
    # Assert
    assert edited.error.endswith("-- did you mean welcome.run?"), edited.error
    assert shown.success is False, "a missing file is a failure, not a red line beside ok"
    assert shown.error.endswith(f"-- did you mean {tmp_path / 'notes.txt'}?"), shown.error


def test_a_transfer_names_the_file_meant(demo):
    """/xfer.xmodem.send looks in the transfer folder, and its miss names the file meant."""
    # Arrange: a file in cap/, the default transfer root
    cap = demo.repl.ctx.fs.cap_dir
    cap.mkdir(parents=True, exist_ok=True)
    (cap / "firmware.bin").write_bytes(b"firmware")
    assert demo._connect() is True, "the DEMO port opens"
    try:
        # Act
        result = demo.repl.dispatch_full("/xfer.xmodem.send firmwar.bin")
    finally:
        demo._disconnect()
    # Assert
    assert result.error.endswith("-- did you mean firmware.bin?"), result.error
