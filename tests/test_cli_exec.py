"""End-to-end tests for ``--exec`` / ``-e`` one-shot CLI mode.

These are subprocess-spawning tests (mirroring ``test_cli_gold.py`` and
``test_cli_prefix.py``) because the behavior we care about -- exit
code, stdout shape, autorun suppression -- only manifests through the
real entry point.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from termapy.plugins.capabilities import STATE_MESSAGES

pytestmark = pytest.mark.slow  # subprocess spawn + full app boot


def _run(*extra_args: str, cfg_dir: str) -> subprocess.CompletedProcess:
    """Spawn ``termapy --cli --demo`` with extra args.  Captures both
    streams and returns the CompletedProcess for the caller to inspect
    ``returncode``, ``stdout``, ``stderr``."""
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            f"sys.argv = ['termapy', '--cli', '--demo', '--no-color', "
            f"'--cfg-dir', {cfg_dir!r}, "
            + ", ".join(repr(extra_arg) for extra_arg in extra_args)
            + "]; from termapy.entry import main; main()",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


def _run_on_absent_port(*extra_args: str, cfg_dir: str) -> subprocess.CompletedProcess:
    """Spawn ``termapy probe --cli`` on a config whose port, COM99, is not on the machine."""
    from termapy.config import create_config, new_config

    create_config(Path(cfg_dir) / "probe" / "probe.cfg", new_config("probe", port="COM99"))
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            f"sys.argv = ['termapy', 'probe', '--cli', '--no-color', "
            f"'--cfg-dir', {cfg_dir!r}, "
            + ", ".join(repr(extra_arg) for extra_arg in extra_args)
            + "]; from termapy.entry import main; main()",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestOneShotConnectsOnNeed:
    """A one-shot opens the port only when its command uses it (registry oneshot-connects-on-need)."""

    @pytest.mark.rule("oneshot-connects-on-need")
    def test_a_one_shot_that_needs_no_port_runs_without_the_device(self, tmp_path):
        """With the board absent, a command that does not use the port runs and exits 0."""
        # Act
        result = _run_on_absent_port("-e", "/print", "no port needed", cfg_dir=str(tmp_path))

        # Assert
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert result.stdout.strip() == "no port needed", "the command ran, nothing else printed"
        assert "Cannot open" not in result.stdout + result.stderr, "no connect was attempted"

    @pytest.mark.rule("oneshot-connects-on-need")
    def test_a_one_shot_device_line_still_needs_the_port(self, tmp_path):
        """A bare device line sends bytes, so the one-shot connects first and fails on the absent port."""
        # Act
        result = _run_on_absent_port("-e", "AT", cfg_dir=str(tmp_path))

        # Assert
        assert result.returncode == 1, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "Cannot open COM99" in result.stdout + result.stderr, "the port error, as before"

    @pytest.mark.rule("oneshot-connects-on-need")
    def test_a_one_shot_command_that_declares_the_port_still_connects(self, tmp_path):
        """A command whose declaration says serial_connected connects first, /term.raw among them."""
        # Act
        result = _run_on_absent_port("-e", "/term.raw", "AT", cfg_dir=str(tmp_path))

        # Assert
        assert result.returncode == 1, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "Cannot open COM99" in result.stdout + result.stderr, (
            "the declaration asked for the port"
        )

    @pytest.mark.rule("uses-port-declared")
    def test_a_one_shot_connects_for_a_command_that_may_use_the_port(self, tmp_path):
        """A command declaring uses_port gets the port opened first, so its own check never answers Not connected."""
        # Act -- what detection names is the demo's business, the connect is ours
        result = _run("-e", "/proto.crc.find", "cmd=AT+PKT", cfg_dir=str(tmp_path))

        # Assert
        assert STATE_MESSAGES["serial_connected"] not in result.stdout + result.stderr, (
            f"the one-shot opened the port first; stdout={result.stdout!r}"
        )


class TestCliExec:
    """``--exec``/``-e`` runs one command, prints to stdout, exits."""

    def test_exec_success_exits_zero(self, tmp_path):
        """A successful one-shot command exits 0 and prints its output to stdout."""
        # Arrange / Act -- /help port is a deterministic local command
        # with multi-line output.  Always succeeds.
        result = _run("--exec", "/help port", cfg_dir=str(tmp_path))

        # Assert
        actual_code = result.returncode
        expected_code = 0
        assert actual_code == expected_code, (
            f"--exec /help port should exit 0, got {actual_code}; stderr={result.stderr!r}"
        )
        assert "port" in result.stdout.lower(), (
            f"stdout should mention port help, got {result.stdout!r}"
        )

    def test_exec_unknown_command_exits_one(self, tmp_path):
        """A CmdResult.fail from the one-shot command becomes exit 1 for shell callers."""
        # Arrange / Act -- a command that doesn't exist.  Dispatch
        # returns CmdResult.fail, exec mode maps that to exit code 1.
        result = _run("--exec", "/notacommand", cfg_dir=str(tmp_path))

        # Assert
        actual_code = result.returncode
        expected_code = 1
        assert actual_code == expected_code, (
            f"--exec on unknown command should exit 1, got {actual_code}; "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )

    def test_exec_and_run_are_mutually_exclusive(self, tmp_path):
        """--run with --exec is refused up front, with an error naming the mutual exclusion."""
        # Arrange / Act -- argparse-level mutual exclusion enforced
        # in entry.py.  Should reject before doing anything.
        result = _run(
            "--run",
            "any.run",
            "--exec",
            "AT+VER",
            cfg_dir=str(tmp_path),
        )

        # Assert
        actual_code = result.returncode
        assert actual_code != 0, f"--run + --exec must reject, got exit 0; stderr={result.stderr!r}"
        combined = (result.stdout + result.stderr).lower()
        assert "mutually exclusive" in combined, (
            f"error message should call out mutual exclusion, got "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )

    def test_the_rest_of_the_line_is_the_command(self, tmp_path):
        """The words after -e are the command, joined with spaces, so a one-liner needs no quotes."""
        # Act
        result = _run("-e", "/print", "hello", "wide", "world", cfg_dir=str(tmp_path))

        # Assert
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert result.stdout.strip() == "hello wide world", "the words joined with single spaces"

    def test_quoted_and_unquoted_run_the_same_command(self, tmp_path):
        """One quoted word and the same text as separate words are the same command."""
        # Act
        quoted = _run("-e", "/print hello wide world", cfg_dir=str(tmp_path))
        unquoted = _run("-e", "/print", "hello", "wide", "world", cfg_dir=str(tmp_path))

        # Assert
        assert quoted.stdout == unquoted.stdout, "the join makes the two spellings one command"

    def test_a_flag_after_exec_belongs_to_the_command(self, tmp_path):
        """Everything after -e is the command, a termapy flag included, so termapy's flags go before it."""
        # Act -- argparse's own --version would print the version and exit
        result = _run("-e", "/print", "after", "--version", cfg_dir=str(tmp_path))

        # Assert
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        assert result.stdout.strip() == "after --version", "the flag reached the command as text"

    def test_an_empty_exec_is_refused(self, tmp_path):
        """-e with nothing after it is a usage error that says what -e takes."""
        # Act
        result = _run("-e", cfg_dir=str(tmp_path))

        # Assert
        assert result.returncode == 2, f"a usage error; stderr={result.stderr!r}"
        assert "needs a command" in result.stderr, "says what is missing"

    @pytest.mark.rule("json-flag-reaches-the-command")
    def test_json_before_exec_is_the_commands_json(self, tmp_path):
        """--json before -e is handed to the command, so the one-shot prints the command's envelope."""
        # Act
        before = _run("--json", "-e", "/print", "hi", cfg_dir=str(tmp_path))
        after = _run("-e", "/print", "hi", "--json", cfg_dir=str(tmp_path))

        # Assert
        assert before.returncode == 0, f"stderr={before.stderr!r}"
        envelope = json.loads(before.stdout)
        assert (envelope["cmd"], envelope["value"]) == ("/print hi", "hi"), "the command's envelope"
        twin = json.loads(after.stdout)
        envelope.pop("elapsed_s"), twin.pop("elapsed_s")
        assert envelope == twin, "the two spellings are one command, timing aside"

    @pytest.mark.rule("json-flag-reaches-the-command")
    def test_json_before_exec_reaches_a_bare_device_line(self, tmp_path):
        """A bare device line takes --json as a typed line does: the exchange comes back as the envelope."""
        # Act
        result = _run("--json", "-e", "AT+INFO", cfg_dir=str(tmp_path))

        # Assert
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        envelope = json.loads(result.stdout.strip().splitlines()[-1])
        assert envelope["cmd"] == "AT+INFO", "the device line is the request"
        assert envelope["success"] and envelope["value"], "the reply is the value"

    @pytest.mark.rule("envelope-is-the-whole-answer")
    def test_a_one_shot_device_line_prints_the_envelope_alone(self, tmp_path):
        """--json -e on a device line prints one envelope and nothing beside it, no request echo."""
        # Act
        result = _run("--json", "-e", "AT+INFO", cfg_dir=str(tmp_path))

        # Assert
        assert result.returncode == 0, f"stderr={result.stderr!r}"
        envelope = json.loads(result.stdout)
        assert envelope["cmd"] == "AT+INFO", "all of stdout is the one envelope"

    def test_a_second_exec_is_refused(self, tmp_path):
        """-e twice is refused, where argparse used to run only the last one silently."""
        # Act
        result = _run("-e", "/print a", "-e", "/print b", cfg_dir=str(tmp_path))

        # Assert
        assert result.returncode == 2, f"a usage error; stderr={result.stderr!r}"
        assert "takes one command" in result.stderr, "names the rule"
        assert "--run" in result.stderr, "and the way to run several"
        assert result.stdout.strip() == "", "neither command ran"

    def test_a_line_break_in_exec_is_refused(self, tmp_path):
        """A line break inside -e is refused rather than run as one mangled command."""
        # Act
        result = _run("-e", "/print a\n/print b", cfg_dir=str(tmp_path))

        # Assert
        assert result.returncode == 2, f"a usage error; stderr={result.stderr!r}"
        assert "without line breaks" in result.stderr, "names what is wrong"
