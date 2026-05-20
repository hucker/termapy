"""Tests for ``termapy.install_info.upgrade_command``.

The detection inspects ``sys.executable`` for path fragments
characteristic of each install layout.  Each test patches
``sys.executable`` to a representative path for one layout and
asserts the right upgrade command is returned.
"""

from __future__ import annotations

import pytest

from termapy import install_info


@pytest.mark.parametrize(
    "exe_path, expected",
    [
        # uv tool: cross-platform path styles.
        (
            r"C:\Users\bob\AppData\Local\uv\tools\termapy\Scripts\python.exe",
            "uv tool upgrade termapy",
        ),
        (
            "/home/bob/.local/share/uv/tools/termapy/bin/python",
            "uv tool upgrade termapy",
        ),
        # pipx layouts.
        (
            r"C:\Users\bob\.local\pipx\venvs\termapy\Scripts\python.exe",
            "pipx upgrade termapy",
        ),
        (
            "/home/bob/.local/pipx/venvs/termapy/bin/python",
            "pipx upgrade termapy",
        ),
        # Dev tree (editable install in a project's .venv).
        (
            r"C:\Users\bob\src\termapy\.venv\Scripts\python.exe",
            "git pull && uv pip install -e .",
        ),
        (
            "/home/bob/src/termapy/.venv/bin/python",
            "git pull && uv pip install -e .",
        ),
    ],
)
def test_upgrade_command_detects_install_layout(
    exe_path, expected, monkeypatch,
):
    # Arrange
    monkeypatch.setattr(install_info.sys, "executable", exe_path)

    # Act
    actual = install_info.upgrade_command()

    # Assert
    assert actual == expected, (
        f"path {exe_path!r} should map to {expected!r}, got {actual!r}"
    )


def test_upgrade_command_falls_back_to_pip(monkeypatch):
    # Arrange -- a path that matches none of the recognised
    # layouts (typical of a plain pip install into a system
    # interpreter or a bare uv pip install).
    monkeypatch.setattr(
        install_info.sys, "executable",
        r"C:\Python313\python.exe",
    )

    # Act
    actual = install_info.upgrade_command()

    # Assert -- pip is the broadly-correct last resort.
    assert actual == "pip install -U termapy", (
        "unrecognised layouts fall back to pip"
    )
