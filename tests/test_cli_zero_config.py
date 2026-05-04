"""End-to-end tests for zero-config CLI mode.

``termapy --cli`` with no config file arg and no auto-detectable config
previously printed ``"no config found"`` and exited 1.  It now enters a
"zero-config" REPL: prints a welcome banner listing available ports,
shows the defaults the user would get, and hints at
``/port.connect <name>``.  The user can then open any port without ever
having written a ``.cfg`` file.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow  # subprocess-spawning zero-config CLI tests


def _run_zero_config(
    script_lines: list[str] | None = None,
    args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke termapy --cli in a tmp cwd with no config present.

    Uses an explicit empty ``--cfg-dir`` pointing at a just-made temp
    dir, which guarantees ``_find_config()`` returns None and triggers
    the zero-config path.  The subprocess gets empty stdin so the REPL
    exits immediately after the welcome banner (EOF on interactive
    prompt terminates the loop).
    """
    tmp = tempfile.mkdtemp()
    empty_cfg = os.path.join(tmp, "empty_cfg_dir")
    os.makedirs(empty_cfg)

    script_path = ""
    extra_flags = ""
    if script_lines is not None:
        sp = Path(tmp) / "zero.run"
        sp.write_text("\n".join(script_lines) + "\n")
        script_path = str(sp)
        extra_flags = f", '--run', {script_path!r}"

    cli_argv = [
        "'termapy'", "'--cli'",
        "'--cfg-dir'", f"{empty_cfg!r}",
        "'--no-color'", "'--term-width'", "'120'",
    ]
    if args:
        cli_argv.extend(repr(a) for a in args)

    return subprocess.run(
        [
            sys.executable, "-c",
            f"import sys; sys.argv = [{', '.join(cli_argv)}{extra_flags}]; "
            "from termapy.entry import main; main()",
        ],
        input="",
        capture_output=True,
        text=True,
        timeout=15,
        cwd=tmp,
    )


class TestZeroConfigWelcomeBanner:
    """The welcome banner prints when no config is found + --cli is used."""

    def test_banner_shows_defaults_and_hint(self):
        # Act
        result = _run_zero_config()

        # Assert -- the key pieces of the banner are all present, in
        # whatever order the frontend emits them.
        assert result.returncode == 0, (
            f"exit cleanly on EOF, got {result.returncode}. "
            f"stderr: {result.stderr!r}"
        )
        out = result.stdout
        assert "No config found" in out, (
            f"welcome banner must say so; stdout: {out!r}"
        )
        assert "Available ports:" in out, (
            f"must list ports; stdout: {out!r}"
        )
        assert "115200 N81 cr noecho" in out, (
            f"must state the defaults; stdout: {out!r}"
        )
        assert "/port.connect" in out, (
            f"must hint at /port.connect; stdout: {out!r}"
        )

    def test_banner_mentions_help(self):
        # Act
        result = _run_zero_config()

        # Assert
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert "/help" in result.stdout, (
            f"should mention /help; stdout: {result.stdout!r}"
        )


class TestZeroConfigRunPreserved:
    """--run without an inferrable config still errors (not a zero-config case).

    Zero-config mode is explicitly only for interactive use.  Scripts
    passed via --run still go through the existing
    _infer_config_from_run_file path; this test verifies that path
    wasn't accidentally broken.
    """

    def test_run_without_config_still_errors(self):
        # Arrange -- --run with a script that has no nearby .cfg file.
        tmp = tempfile.mkdtemp()
        empty_cfg = os.path.join(tmp, "empty_cfg_dir")
        os.makedirs(empty_cfg)
        script_path = Path(tmp) / "lonely.run"
        script_path.write_text("/echo unreachable\n")

        # Act
        result = subprocess.run(
            [
                sys.executable, "-c",
                "import sys; sys.argv = ["
                "'termapy', '--cli', "
                f"'--cfg-dir', {empty_cfg!r}, "
                f"'--run', {str(script_path)!r}, "
                "'--no-color', '--term-width', '120']; "
                "from termapy.entry import main; main()",
            ],
            input="",
            capture_output=True,
            text=True,
            timeout=15,
            cwd=tmp,
        )

        # Assert -- should error (can't infer config), NOT silently
        # fall into zero-config mode.
        assert result.returncode != 0, (
            f"--run without config must error, not zero-config into REPL. "
            f"stdout: {result.stdout!r} stderr: {result.stderr!r}"
        )
        assert "cannot infer config" in result.stderr.lower() or (
            "unreachable" not in result.stdout
        ), (
            f"should tell user the inference failed, NOT run the script. "
            f"stderr: {result.stderr!r}"
        )
