"""Tests for the Help-button tooltip renderable.

Pure Rich (no Textual, no app): build it, render to a string, assert the
content.  Covers the version-status line and the crcglot/reveng credit
added this cycle -- previously verified only by an ad-hoc smoke.

The version-status line reads the cached PyPI check via ``cached_status``,
which honors ``TERMAPY_STATE_DIR``; each test seeds that state file
directly (the same seam ``test_update_check.py`` uses), so no mocking.
"""

from __future__ import annotations

import json

from rich.console import Console

from termapy.help_tooltip import build_help_tooltip


def _render(renderable) -> str:
    console = Console(width=80)
    with console.capture() as cap:
        console.print(renderable)
    return cap.get()


def _seed_state(tmp_path, monkeypatch, latest_seen: str | None) -> None:
    monkeypatch.setenv("TERMAPY_STATE_DIR", str(tmp_path))
    if latest_seen is not None:
        (tmp_path / "state.json").write_text(
            json.dumps({"update_check": {"latest_seen": latest_seen}})
        )


def test_shows_up_to_date_when_current(tmp_path, monkeypatch):
    _seed_state(tmp_path, monkeypatch, latest_seen="1.0.0")
    out = _render(build_help_tooltip("1.0.0"))
    assert "You have the latest version." in out, "current version -> reassurance"
    assert "Update available" not in out, "no update prompt when current"


def test_shows_update_available_when_behind(tmp_path, monkeypatch):
    _seed_state(tmp_path, monkeypatch, latest_seen="2.0.0")
    out = _render(build_help_tooltip("1.0.0"))
    assert "Update available: v2.0.0" in out, "behind -> shows the newer version"
    assert "uv tool upgrade termapy" in out, "tells the user how to upgrade"


def test_no_status_line_when_never_checked(tmp_path, monkeypatch):
    _seed_state(tmp_path, monkeypatch, latest_seen=None)  # no state file
    out = _render(build_help_tooltip("1.0.0"))
    assert "You have the latest version." not in out, "silent when never checked"
    assert "Update available" not in out, "silent when never checked"


def test_credits_crcglot_alongside_reveng(tmp_path, monkeypatch):
    _seed_state(tmp_path, monkeypatch, latest_seen="1.0.0")
    out = _render(build_help_tooltip("1.0.0"))
    assert "crcglot" in out, "crcglot (the CRC engine) is credited"
    assert "reveng" in out, "reveng (its algorithm source) is still credited"


def test_header_shows_version_and_hotkey_hint(tmp_path, monkeypatch):
    _seed_state(tmp_path, monkeypatch, latest_seen="1.0.0")
    out = _render(build_help_tooltip("1.2.3", hint="F1"))
    assert "Termapy v1.2.3" in out, "version in the header"
    assert "(F1)" in out, "hotkey hint rendered when provided"
