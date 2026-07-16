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
    s = CommandSuggester()
    s.update(commands=["/help", "/cfg", "/clear"], history=[])
    assert _suggest(s, "/c") == "/cfg", "first matching REPL command wins"


def test_matching_is_case_insensitive():
    s = CommandSuggester()
    s.update(commands=[], history=["AT+CSQ"])
    assert _suggest(s, "at+") == "AT+CSQ", "match ignores case"


def test_device_history_is_suggested():
    s = CommandSuggester()
    s.update(commands=["/help"], history=["ATZ"])
    assert _suggest(s, "at") == "ATZ", "non-prefix history is offered"


def test_prefix_history_is_not_double_counted():
    # A REPL command already lives in `commands`; the same entry appearing in
    # history (it starts with the prefix) must be dropped, not listed twice.
    s = CommandSuggester()
    s.update(commands=["/status"], history=["/status"], prefix="/")
    assert s._suggestions == ["/status"], "prefix-history excluded from device list"


def test_no_match_returns_none():
    s = CommandSuggester()
    s.update(commands=["/help"], history=[])
    assert _suggest(s, "zzz") is None, "no candidate -> None"
