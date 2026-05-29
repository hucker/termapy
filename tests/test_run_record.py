"""Tests for /run.record -- session capture to a .run file.

Covers:

- Start writes successful commands; failed dispatches and
  ``/run.record`` itself are skipped.
- Refuse-if-exists (mode="x" exclusive create).
- Refuse a second start while one is active.
- Stop without active recording is an error.
- Auto-`.run` suffix; reject wrong suffixes.
- Reject paths with separators (bare filename only).
- Flush after each write: kill the process and the recorded
  lines are still on disk.
"""

from __future__ import annotations

import json

import pytest

from termapy.builtins.commands import _run_record
from termapy.repl import ReplEngine


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Minimal engine + scripts_dir wired to the temp tree.

    Also clears the recorder's module-level state before AND after
    each test so a leaked observer from one test can't contaminate
    the next.  This matters because the recorder is a singleton --
    state lives at module scope.
    """
    cfg = {"port": "COM4", "baud_rate": 115200, "line_ending": "\r"}
    config_path = tmp_path / "cfg" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    scripts_dir = config_path.parent / "run"
    scripts_dir.mkdir()
    for sub in ("plugin", "ss"):
        (config_path.parent / sub).mkdir(exist_ok=True)

    output: list = []
    eng = ReplEngine(
        cfg, str(config_path), lambda t, c=None: output.append((t, c)),
    )
    flags = eng.ctx.ns("flags")
    flags["echo"] = True
    flags["output_level"] = "verbose"
    # The internal handle the recorder reaches for is on ctx.internal;
    # wire the observer pair to the underlying repl methods so the
    # builtin sees a configured host (TerminalHost normally does
    # this, but this test bypasses TerminalHost).
    eng.ctx.internal.add_post_dispatch_observer = eng.add_post_dispatch_observer
    eng.ctx.internal.remove_post_dispatch_observer = (
        eng.remove_post_dispatch_observer
    )
    # Tests dispatch via the engine, so scripts_dir on ctx.fs needs
    # to point at the real on-disk path; PluginContext defaults it
    # to Path('.').
    eng.ctx.fs.scripts_dir = scripts_dir

    # Ensure the recorder starts each test idle (defensive against
    # leaks from prior tests in the same session).
    if _run_record._active is not None:
        _run_record._active.file.close()
        _run_record._active = None

    yield eng, scripts_dir, output

    # Clean up after the test too -- the recorder is module-level
    # singleton state.
    if _run_record._active is not None:
        try:
            _run_record._active.file.close()
        except Exception:
            pass
        _run_record._active = None


class TestStartStop:
    def test_start_records_successful_dispatches(self, env):
        # Arrange
        eng, scripts_dir, _ = env

        # Act
        start = eng.dispatch("run.record session_a")
        eng.dispatch("app.ver")
        eng.dispatch("app.ver")
        stop = eng.dispatch("run.record")

        # Assert
        assert start.success, "start succeeded"
        assert stop.success, "stop succeeded"
        recorded = (scripts_dir / "session_a.run").read_text(encoding="utf-8")
        # Both /app.ver lines were dispatched successfully.  Bare
        # /run.record at start AND stop should NOT appear.
        assert recorded == "app.ver\napp.ver\n", (
            f"expected two app.ver lines, got: {recorded!r}"
        )

    def test_failed_dispatch_is_skipped(self, env):
        # Arrange
        eng, scripts_dir, _ = env

        # Act
        eng.dispatch("run.record session_b")
        eng.dispatch("definitely_not_a_command")  # unknown -> fail
        eng.dispatch("app.ver")  # success
        eng.dispatch("run.record")  # stop

        # Assert
        recorded = (scripts_dir / "session_b.run").read_text(encoding="utf-8")
        assert recorded == "app.ver\n", (
            "failed dispatch should be skipped; only successful /app.ver kept"
        )

    def test_run_record_itself_never_recorded(self, env):
        # Arrange
        eng, scripts_dir, _ = env

        # Act -- mix /run.record forms to verify the filter catches
        # all variants.
        eng.dispatch("run.record session_c")
        eng.dispatch("app.ver")
        eng.dispatch("  run.record   foo  ")  # weird whitespace
        eng.dispatch("app.ver")
        eng.dispatch("run.record")

        # Assert
        recorded = (scripts_dir / "session_c.run").read_text(encoding="utf-8")
        assert "run.record" not in recorded, (
            "recorder must filter out /run.record in every form"
        )
        assert recorded.count("app.ver\n") == 2, "both /app.ver lines preserved"

    def test_stop_message_reports_count_and_path(self, env):
        # Arrange
        eng, scripts_dir, output = env

        # Act
        eng.dispatch("run.record session_d")
        eng.dispatch("app.ver")
        eng.dispatch("app.ver")
        eng.dispatch("app.ver")
        eng.dispatch("run.record")

        # Assert
        texts = " ".join(t for t, _ in output)
        assert "Recorded 3 commands" in texts, (
            "stop message reports the count"
        )
        assert "session_d.run" in texts, "stop message includes the path"


class TestRefusal:
    def test_refuse_if_file_exists(self, env):
        # Arrange
        eng, scripts_dir, _ = env
        (scripts_dir / "existing.run").write_text("# pre-existing\n")

        # Act
        result = eng.dispatch("run.record existing")

        # Assert
        assert not result.success, "refuses to clobber existing file"
        assert "exists" in result.error.lower(), (
            f"error mentions 'exists', got: {result.error!r}"
        )
        # Recorder must not be active after refusal.
        assert _run_record.is_active() is False, (
            "no recording activated after refusal"
        )
        # Pre-existing file is untouched.
        assert (scripts_dir / "existing.run").read_text(
            encoding="utf-8",
        ) == "# pre-existing\n", "existing file content preserved"

    def test_refuse_second_start_while_active(self, env):
        # Arrange
        eng, scripts_dir, _ = env
        eng.dispatch("run.record first_session")

        # Act
        result = eng.dispatch("run.record second_session")

        # Assert
        assert not result.success, "second start refused"
        assert "already recording" in result.error.lower(), (
            "error mentions 'already recording'"
        )
        # Original recording still active.
        assert _run_record.is_active() is True, "first recording continues"

    def test_stop_without_active_recording_fails(self, env):
        # Arrange
        eng, _, _ = env

        # Act
        result = eng.dispatch("run.record")

        # Assert
        assert not result.success, "stop without active fails"
        assert "not recording" in result.error.lower(), (
            "error names the state"
        )


class TestFilenameHandling:
    def test_auto_appends_run_suffix(self, env):
        # Arrange
        eng, scripts_dir, _ = env

        # Act
        eng.dispatch("run.record sans_suffix")
        eng.dispatch("run.record")

        # Assert
        assert (scripts_dir / "sans_suffix.run").is_file(), (
            "filename without .run gets the suffix"
        )

    def test_rejects_wrong_suffix(self, env):
        # Arrange
        eng, scripts_dir, _ = env

        # Act
        result = eng.dispatch("run.record session.txt")

        # Assert
        assert not result.success, ".txt suffix rejected"
        assert ".run" in result.error.lower(), (
            "error explains the .run requirement"
        )
        assert not (scripts_dir / "session.txt").exists(), (
            "no file created on refusal"
        )

    def test_rejects_path_separators(self, env):
        # Arrange
        eng, _, _ = env

        # Act -- bare filename only; subfolder paths are out of scope.
        result = eng.dispatch("run.record sub/dir/foo")

        # Assert
        assert not result.success, "path with / refused"
        assert (
            "filename" in result.error.lower()
            or "path" in result.error.lower()
        ), "error explains why"


class TestDurability:
    def test_flush_after_each_write(self, env):
        # Arrange -- after recording two commands, the file on disk
        # contains both even though we haven't called stop.  This is
        # the "crash mid-recording leaves a usable file" guarantee.
        eng, scripts_dir, _ = env

        # Act
        eng.dispatch("run.record live_check")
        eng.dispatch("app.ver")
        eng.dispatch("app.ver")

        # Read the file mid-recording.  Without flush this would
        # likely be empty.
        on_disk = (scripts_dir / "live_check.run").read_text(
            encoding="utf-8",
        )

        # Cleanup (stop the recording so the fixture teardown is
        # clean) -- the assertion above is the real test.
        eng.dispatch("run.record")

        # Assert
        assert on_disk == "app.ver\napp.ver\n", (
            f"both lines flushed to disk before stop, got: {on_disk!r}"
        )


class TestIsActive:
    def test_is_active_false_when_idle(self, env):
        # Arrange / Act / Assert
        _eng, _, _ = env
        assert _run_record.is_active() is False, "no recording at fixture start"

    def test_is_active_true_during_recording(self, env):
        # Arrange
        eng, _, _ = env

        # Act
        eng.dispatch("run.record state_check")

        # Assert
        assert _run_record.is_active() is True, "active after start"
        eng.dispatch("run.record")  # stop, for teardown cleanliness
        assert _run_record.is_active() is False, "idle after stop"
