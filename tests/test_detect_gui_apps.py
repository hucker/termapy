"""Tests for ``termapy.plugins.detect_gui_apps``.

The detector is a heuristic over env vars and ``sys.platform``.  These
tests fully isolate it via ``monkeypatch.setenv`` / ``delenv`` and a
patched ``sys.platform`` so the real environment can't influence them.
"""

from __future__ import annotations

import sys

import pytest

from termapy.plugins import detect_gui_apps

# Env vars touched by the detector.  Each test starts from a clean slate.
_ENV_VARS = (
    "TERMAPY_GUI",
    "SSH_CONNECTION",
    "SSH_TTY",
    "DISPLAY",
    "WAYLAND_DISPLAY",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip every var the detector reads so each test starts clean."""
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _set_platform(monkeypatch, platform: str) -> None:
    monkeypatch.setattr(sys, "platform", platform)


# ── Override ─────────────────────────────────────────────────────────────────


def test_override_true_wins_even_in_ssh(monkeypatch):
    # Arrange — SSH session, no display: would normally be False
    monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 22 5.6.7.8 22")
    monkeypatch.setenv("TERMAPY_GUI", "1")
    _set_platform(monkeypatch, "linux")

    # Act
    actual = detect_gui_apps()

    # Assert
    assert actual is True, "TERMAPY_GUI=1 must override SSH-no-display detection"


def test_override_false_wins_on_native_linux(monkeypatch):
    # Arrange — native Linux with DISPLAY: would normally be True
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("TERMAPY_GUI", "0")
    _set_platform(monkeypatch, "linux")

    # Act
    actual = detect_gui_apps()

    # Assert
    assert actual is False, "TERMAPY_GUI=0 must override native-with-DISPLAY"


@pytest.mark.parametrize("value", ["yes", "true", "on"])
def test_override_truthy_aliases(monkeypatch, value):
    # Arrange
    monkeypatch.setenv("TERMAPY_GUI", value)
    _set_platform(monkeypatch, "linux")

    # Act
    actual = detect_gui_apps()

    # Assert
    assert actual is True, f"TERMAPY_GUI={value!r} should be truthy"


@pytest.mark.parametrize("value", ["no", "false", "off"])
def test_override_falsy_aliases(monkeypatch, value):
    # Arrange
    monkeypatch.setenv("TERMAPY_GUI", value)
    monkeypatch.setenv("DISPLAY", ":0")
    _set_platform(monkeypatch, "linux")

    # Act
    actual = detect_gui_apps()

    # Assert
    assert actual is False, f"TERMAPY_GUI={value!r} should be falsy"


# ── SSH ──────────────────────────────────────────────────────────────────────


def test_ssh_connection_no_display_returns_false(monkeypatch):
    # Arrange
    monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 22 5.6.7.8 22")
    _set_platform(monkeypatch, "linux")

    # Act
    actual = detect_gui_apps()

    # Assert
    assert actual is False, "SSH without DISPLAY -> no GUI apps"


def test_ssh_with_x11_forwarding_returns_true(monkeypatch):
    # Arrange — SSH session with X11 forwarding sets DISPLAY too
    monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 22 5.6.7.8 22")
    monkeypatch.setenv("DISPLAY", "localhost:10.0")
    _set_platform(monkeypatch, "linux")

    # Act
    actual = detect_gui_apps()

    # Assert
    assert actual is True, "SSH + X11 forwarding -> GUI apps available"


def test_ssh_tty_alone_treated_as_ssh(monkeypatch):
    # Arrange — some setups set SSH_TTY but not SSH_CONNECTION
    monkeypatch.setenv("SSH_TTY", "/dev/pts/0")
    _set_platform(monkeypatch, "linux")

    # Act
    actual = detect_gui_apps()

    # Assert
    assert actual is False, "SSH_TTY alone is enough to detect SSH"


# ── Native platforms ─────────────────────────────────────────────────────────


def test_linux_with_display_returns_true(monkeypatch):
    # Arrange
    monkeypatch.setenv("DISPLAY", ":0")
    _set_platform(monkeypatch, "linux")

    # Act
    actual = detect_gui_apps()

    # Assert
    assert actual is True, "native Linux + DISPLAY -> GUI apps available"


def test_linux_with_wayland_returns_true(monkeypatch):
    # Arrange
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    _set_platform(monkeypatch, "linux")

    # Act
    actual = detect_gui_apps()

    # Assert
    assert actual is True, "native Linux + WAYLAND_DISPLAY -> GUI apps available"


def test_linux_no_display_returns_false(monkeypatch):
    # Arrange — headless Linux box (CI, container, server, ...)
    _set_platform(monkeypatch, "linux")

    # Act
    actual = detect_gui_apps()

    # Assert
    assert actual is False, "headless Linux -> no GUI apps"


def test_macos_with_display_returns_true(monkeypatch):
    # Arrange
    monkeypatch.setenv("DISPLAY", ":0")
    _set_platform(monkeypatch, "darwin")

    # Act
    actual = detect_gui_apps()

    # Assert
    assert actual is True, "macOS + DISPLAY -> GUI apps available"


def test_windows_returns_true(monkeypatch):
    # Arrange — assume native Windows always has GUI; WSL caller can override
    _set_platform(monkeypatch, "win32")

    # Act
    actual = detect_gui_apps()

    # Assert
    assert actual is True, "Windows defaults to GUI apps available"


def test_unknown_platform_returns_false(monkeypatch):
    # Arrange — some hypothetical unknown platform; fail safe
    _set_platform(monkeypatch, "freebsd")

    # Act
    actual = detect_gui_apps()

    # Assert
    assert actual is False, "unknown platform -> conservative no"
