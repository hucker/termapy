"""Behavioral tests for CommandSuggester -- prefix type-ahead logic.

Pure logic: instantiate, ``update()``, ``get_suggestion()`` -- no widget
tree, no app, not even Pilot.  This matching contract had zero coverage
when the class lived inside app.py.
"""

from __future__ import annotations

import asyncio

from termapy.widgets import CommandSuggester


def _suggest(sugg: CommandSuggester, value: str) -> str | None:
    return asyncio.run(sugg.get_suggestion(value))


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


def test_prefix_history_is_not_double_counted():
    """A REPL command found in history is not added again as a device suggestion."""
    # A REPL command already lives in `commands`; the same entry appearing in
    # history (it starts with the prefix) must be dropped, not listed twice.
    s = CommandSuggester()
    s.update(commands=["/status"], history=["/status"], prefix="/")
    assert s._suggestions == ["/status"], "prefix-history excluded from device list"


def test_no_match_returns_none():
    """With no candidate for the typed text, no suggestion is shown."""
    s = CommandSuggester()
    s.update(commands=["/help"], history=[])
    assert _suggest(s, "zzz") is None, "no candidate -> None"
