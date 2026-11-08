"""``/proto.run`` writes ``json_file``, returns the results as data, runs a
test's own teardown, and stops on the engine's script-stop event.

Runs on the ``CLITerminal`` DEMO rig (``conftest.py``'s ``cli``): a real
engine and the demo device, no mocks.  The stop tests
set the event from a timer while the run is in progress, which is the
only way to request a stop that the prompt-time clear does not erase.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest


def _pro(folder: Path, name: str, text: str) -> Path:
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


_AT_OK = """\
name = "AT Test"
frame_gap = "100ms"

[[test]]
name = "AT basic"
send = '"AT\\r"'
expect = '"OK\\r\\n"'
"""


class TestResultsDocument:
    def test_json_file_is_written_beside_the_script(self, cli, tmp_path):
        """A script-level json_file lands in test/ next to the script, named by the template, holding the shared document."""
        # Arrange
        script = _pro(tmp_path, "at.pro", 'json_file = "{proto_name}_out.json"\n' + _AT_OK)

        # Act
        result = cli.repl.dispatch_full(f"/proto.run {script}")

        # Assert
        out_path = tmp_path / "test" / "at_out.json"
        assert result.success is True, result.error
        assert out_path.exists(), "written where termapy --proto writes"
        document = json.loads(out_path.read_text(encoding="utf-8"))
        assert document["summary"] == {
            "total": 1,
            "passed": 1,
            "failed": 0,
            "elapsed_ms": document["summary"]["elapsed_ms"],
        }
        assert document["meta"]["script"] == "at.pro", "the same meta the headless runner records"
        assert document["tests"][0]["name"] == "AT basic"
        assert document["source"].startswith("json_file"), "the script text rides along"

    @pytest.mark.parametrize("name", ["../up.json", "..\\up.json", "C:x.json"])
    def test_json_file_cannot_leave_the_results_folder(self, cli, tmp_path, capsys, name):
        """proto-results-in-folder: for a person too, a script cannot choose the folder, and nothing is sent first."""
        # Arrange
        script = _pro(tmp_path, "at.pro", f"json_file = {json.dumps(name)}\n" + _AT_OK)
        capsys.readouterr()

        # Act
        result = cli.repl.dispatch_full(f"/proto.run {script}")

        # Assert
        assert "Invalid results file name" in result.error, result.error
        assert "[PROTO]" not in capsys.readouterr().out, "no test ran"
        assert not (tmp_path / "test").exists(), "nothing was written"

    def test_a_person_may_run_a_fixed_name_twice(self, cli, tmp_path):
        """A person's re-run replaces the last results: only an MCP client is refused that."""
        # Arrange
        script = _pro(tmp_path, "at.pro", 'json_file = "out.json"\n' + _AT_OK)

        # Act
        first = cli.repl.dispatch_full(f"/proto.run {script}")
        second = cli.repl.dispatch_full(f"/proto.run {script}")

        # Assert
        assert first.success is True, first.error
        assert second.success is True, second.error

    def test_no_json_file_writes_nothing(self, cli, tmp_path):
        """Without json_file a plain /proto.run leaves no file behind."""
        script = _pro(tmp_path, "at.pro", _AT_OK)
        cli.repl.dispatch_full(f"/proto.run {script}")
        assert not (tmp_path / "test").exists(), "no results folder without the key"

    def test_json_flag_returns_the_document(self, cli, tmp_path, capsys):
        """/proto.run --json returns the results document as data; the run's narration is progress, kept by the level."""
        # Arrange
        script = _pro(tmp_path, "at.pro", _AT_OK)
        capsys.readouterr()

        # Act
        result = cli.repl.dispatch_full(f"/proto.run {script} --json")
        envelope = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

        # Assert
        assert result.success is True, result.error
        assert result.data["summary"]["passed"] == 1, "the structured twin of the PASS line"
        assert result.data["tests"][0]["actual_text"] == "OK\\r\\n"
        assert any("[PROTO]" in line for line in envelope["output_lines"]), (
            "at normal the narration is in the envelope's output_lines, nowhere else"
        )

    def test_quiet_json_is_the_document_alone(self, cli, tmp_path, capsys):
        """/proto.run --json --quiet, MCP's default level: the document and no narration."""
        script = _pro(tmp_path, "at.pro", _AT_OK)
        capsys.readouterr()
        cli.repl.dispatch_full(f"/proto.run {script} --json --quiet")
        envelope = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert envelope["output_lines"] == [], "quiet drops the progress lines"
        assert envelope["data"]["summary"]["passed"] == 1

    def test_prose_run_carries_the_document_too(self, cli, tmp_path):
        """A plain /proto.run has the same data as a --json one: the path is the value, the document rides as data."""
        script = _pro(tmp_path, "at.pro", _AT_OK)
        result = cli.repl.dispatch_full(f"/proto.run {script}")
        assert result.data["summary"]["passed"] == 1, "the structured twin always rides"
        assert result.value == str(script), "the path, as a scriptable string"


class TestTeardown:
    def test_per_test_teardown_runs(self, cli, tmp_path, capsys):
        """A test's own teardown commands are sent after its expect, as the headless runner does."""
        # Arrange
        script = _pro(tmp_path, "at.pro", _AT_OK + 'teardown = ["ATI"]\n')
        capsys.readouterr()

        # Act
        cli.repl.dispatch_full(f"/proto.run {script}")

        # Assert
        out = capsys.readouterr().out
        assert out.index("PASS") < out.index("CMD: ATI"), "teardown follows the test's result"


class TestStop:
    def test_stale_stop_is_cleared_at_the_prompt(self, cli, tmp_path):
        """Typed at the prompt, /proto.run owns the stop event: a stale Escape does not end the run before it starts."""
        # Arrange
        script = _pro(tmp_path, "at.pro", _AT_OK)
        cli.repl._script_stop.set()

        # Act
        result = cli.repl.dispatch_full(f"/proto.run {script} --json")

        # Assert
        assert result.data["summary"]["passed"] == 1, "the run happened"

    @pytest.mark.slow
    def test_stop_ends_a_toml_run_after_the_current_test(self, cli, tmp_path, capsys):
        """A stop raised mid-run ends the script after the test in flight, skips the teardown, and says so."""
        # Arrange - ten tests, each at least a frame gap long
        tests = "".join(
            f'[[test]]\nname = "T{i}"\nsend = \'"AT\\r"\'\nexpect = \'"OK\\r\\n"\'\n'
            for i in range(10)
        )
        script = _pro(tmp_path, "many.pro", 'frame_gap = "100ms"\nteardown = ["ATI"]\n' + tests)
        capsys.readouterr()
        threading.Timer(0.15, cli.repl._script_stop.set).start()

        # Act
        t0 = time.monotonic()
        result = cli.repl.dispatch_full(f"/proto.run {script}")
        elapsed_s = time.monotonic() - t0

        # Assert
        out = capsys.readouterr().out
        assert result.success is True, result.error
        assert out.count("[PROTO]") < 10, "not every test ran"
        assert "Run interrupted after" in out, "the stop is reported"
        assert "CMD: ATI" not in out, "script-level teardown skipped after a stop"
        assert elapsed_s < 0.8, "ten tests would take a second or more"

    @pytest.mark.slow
    def test_stop_wakes_a_flat_delay(self, cli, tmp_path, capsys):
        """A flat-format delay step waits on the stop event, so a stop ends it at once instead of sleeping it out."""
        # Arrange
        script = _pro(tmp_path, "flat.pro", 'delay: 5s\nsend: "AT\\r"\nexpect: "OK\\r\\n"\n')
        capsys.readouterr()
        threading.Timer(0.1, cli.repl._script_stop.set).start()

        # Act
        t0 = time.monotonic()
        cli.repl.dispatch_full(f"/proto.run {script}")
        elapsed_s = time.monotonic() - t0

        # Assert
        assert elapsed_s < 2.0, "the 5 s delay was cut short"
        assert "Run interrupted." in capsys.readouterr().out
