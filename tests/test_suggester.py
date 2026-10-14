"""Behavioral tests for CommandSuggester -- prefix type-ahead logic.

Pure logic: instantiate, ``update()``, ``get_suggestion()`` -- no widget
tree, no app, not even Pilot.  This matching contract had zero coverage
when the class lived inside app.py.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from termapy.repl import ReplEngine
from termapy.widgets import CommandSuggester


def _suggest(sugg: CommandSuggester, value: str) -> str | None:
    return asyncio.run(sugg.get_suggestion(value))


@pytest.fixture
def file_suggester(tmp_path):
    """A suggester wired to a real engine whose run/ holds Welcome.run."""
    config_path = tmp_path / "proj" / "proj.cfg"
    (config_path.parent / "run").mkdir(parents=True)
    config_path.write_text(json.dumps({}), encoding="utf-8")
    (config_path.parent / "run" / "Welcome.run").write_text("", encoding="utf-8")
    eng = ReplEngine({}, str(config_path), lambda text, color=None: None)
    return CommandSuggester(complete_file=eng.complete_file_arg)


def test_matches_first_repl_command_by_prefix():
    """Among several REPL commands sharing a prefix, the first in list order is suggested."""
    s = CommandSuggester()
    s.update(commands=["/help", "/cfg", "/clear"], history=[])
    assert _suggest(s, "/c") == "/cfg", "first matching REPL command wins"


def test_matching_is_case_insensitive():
    """Lowercase typing still completes an uppercase device command from history."""
    s = CommandSuggester()
    s.update(commands=[], history=["AT+CSQ"])
    assert _suggest(s, "at+") == "AT+CSQ", "match ignores case"


def test_device_history_is_suggested():
    """Device commands from history are offered alongside REPL commands."""
    s = CommandSuggester()
    s.update(commands=["/help"], history=["ATZ"])
    assert _suggest(s, "at") == "ATZ", "non-prefix history is offered"


def test_prefix_history_is_not_suggested():
    """REPL lines in history are never offered; REPL completions come only from the command list."""
    # A non-default prefix, so the filter must honor the prefix it is given.
    s = CommandSuggester()
    s.update(commands=["!print"], history=["!print hello"], prefix="!")
    assert _suggest(s, "!print h") is None, "prefix history excluded from suggestions"


def test_no_match_returns_none():
    """With no candidate for the typed text, no suggestion is shown."""
    s = CommandSuggester()
    s.update(commands=["/help"], history=[])
    assert _suggest(s, "zzz") is None, "no candidate -> None"


def test_declared_file_argument_suggests_the_file(file_suggester):
    """A typed partial after /run suggests the matching run/ file, in its on-disk case."""
    # Textual hands get_suggestion the casefolded value; accepting the
    # suggestion replaces the line, so the name arrives as spelled on disk.
    file_suggester.update(commands=["/run", "/run {filename}"], history=[])
    assert _suggest(file_suggester, "/run w") == "/run Welcome.run", (
        "the file wins over the static signature once the argument is started"
    )


def test_bare_file_argument_keeps_the_signature(file_suggester):
    """With nothing typed after /run, the signature ghost still shows what /run takes."""
    file_suggester.update(commands=["/run", "/run {filename}"], history=[])
    assert _suggest(file_suggester, "/run ") == "/run {filename}", (
        "no file is guessed before the user starts the argument"
    )


def test_undeclared_command_uses_the_static_list(file_suggester):
    """A command with no files= declaration falls through to the ordinary list."""
    file_suggester.update(commands=["/print hello"], history=[])
    assert _suggest(file_suggester, "/print h") == "/print hello", (
        "the file completer steps aside for commands that take no file"
    )
