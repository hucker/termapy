"""End-to-end test: the REPL command prefix is actually configurable.

Termapy's command prefix defaults to ``/``.  The ``chore/slash-fix``
branch consolidated the literal ``"/"`` behind ``DEFAULT_CMD_PREFIX``
and a ``cmd_prefix(cfg)`` helper so the default lives in exactly one
place.  This test proves the plumbing actually works -- a config
with ``cmd_prefix: "!"`` routes commands typed with ``!`` to the
right handlers, ignores the usual ``/`` as REPL syntax, and displays
``!`` where the prompt / echo would have shown ``/``.

The test builds a minimal config in a tmp dir, writes a short .run
script that exercises a representative slice of commands, runs
termapy in CLI mode, and asserts on substrings in stdout.  It does
NOT try to diff against a gold file -- that's the
``test_cli_gold`` test's job for the standard ``/`` prefix, and
reproducing the transform for every ``/`` in the 630-line gold
output is more maintenance than signal.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from termapy.defaults import DEFAULT_CFG


def _run_cli(
    tmp_path: Path,
    cfg_overrides: dict,
    script_lines: list[str],
) -> subprocess.CompletedProcess[str]:
    """Build a config + .run script in tmp_path, invoke the CLI, return the result.

    Mirrors the subprocess shape used in test_cli_gold so the two
    share an isolation strategy -- each test gets its own tmp_path
    and cfg dir, nothing touches the developer's real config tree.
    """
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    # Build a cfg with DEMO port wired up so ``AT`` gets an ``OK``
    # response without real hardware.  DEFAULT_CFG has ``port=""``
    # so setdefault() won't help -- we put the DEMO defaults first
    # and let cfg_overrides from the caller win by merging last.
    cfg = {
        **DEFAULT_CFG,
        "port": "DEMO",
        "auto_connect": True,
        **cfg_overrides,
    }
    (proj_dir / "proj.cfg").write_text(json.dumps(cfg, indent=4))

    script_path = tmp_path / "prefix_test.run"
    script_path.write_text("\n".join(script_lines) + "\n")

    return subprocess.run(
        [
            sys.executable, "-c",
            "import sys; "
            f"sys.argv = ['termapy', 'proj', '--cli', "
            f"'--cfg-dir', {str(tmp_path)!r}, "
            f"'--run', {str(script_path)!r}, "
            f"'--no-color', '--term-width', '120']; "
            "from termapy.entry import main; main()",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestPrefixDispatchShapes:
    """Prove every argument shape dispatches under a non-default prefix.

    One test per argument shape.  The dispatch line in repl.py is
    the same for all of them, so if ``!print hello`` works, all the
    other commands' dispatch works too -- but running a handful
    confirms the argument tokenising, subcommand splitting, and
    device-vs-REPL fallback are all prefix-aware.
    """

    def test_bare_command_dispatches(self, tmp_path):
        # Arrange -- `!ver` is the shortest possible REPL invocation.
        result = _run_cli(
            tmp_path,
            cfg_overrides={"cmd_prefix": "!"},
            script_lines=["!ver"],
        )

        # Assert
        actual = result.stdout
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "termapy v" in actual, \
            f"!ver didn't dispatch the version handler: {actual!r}"

    def test_positional_string_dispatches(self, tmp_path):
        # Arrange -- single-positional print, most common REPL shape.
        result = _run_cli(
            tmp_path,
            cfg_overrides={"cmd_prefix": "!"},
            script_lines=["!print hello from bang"],
        )

        # Assert
        actual = result.stdout
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "hello from bang" in actual, \
            f"!print didn't dispatch: {actual!r}"

    def test_cfg_read_dispatches(self, tmp_path):
        # Arrange -- read-only handler that takes one positional arg.
        result = _run_cli(
            tmp_path,
            cfg_overrides={"cmd_prefix": "!"},
            script_lines=["!cfg cmd_prefix"],
        )

        # Assert -- /cfg with a key prints the current value.
        actual = result.stdout
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "!" in actual, f"cfg didn't report the prefix value: {actual!r}"

    def test_dotted_subcommand_dispatches(self, tmp_path):
        # Arrange -- `.auto` is a subcommand under /cfg; the dot-split
        # happens after the prefix strip, so this proves the subcommand
        # tree is reached even with a non-default prefix.
        result = _run_cli(
            tmp_path,
            cfg_overrides={"cmd_prefix": "!"},
            script_lines=[
                "!cfg.auto echo_input false",
                "!cfg echo_input",
            ],
        )

        # Assert -- the set took effect (value reads back as False).
        actual = result.stdout
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "False" in actual, \
            f"!cfg.auto set didn't take effect: {actual!r}"

    def test_device_command_unprefixed_still_reaches_serial(self, tmp_path):
        # Arrange -- no REPL prefix at all; the line must be sent to
        # the demo device, which responds with OK.
        result = _run_cli(
            tmp_path,
            cfg_overrides={"cmd_prefix": "!"},
            script_lines=["AT"],
        )

        # Assert
        actual = result.stdout
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "OK" in actual, \
            f"AT device command didn't reach DEMO: {actual!r}"

    def test_default_slash_is_not_a_repl_prefix_when_cmd_prefix_is_bang(
        self, tmp_path
    ):
        # Arrange -- with cmd_prefix="!", the literal `/print hello`
        # must NOT dispatch the print handler.  It's sent to the
        # demo device instead (where it's meaningless).  Absence of
        # a bare "hello" line in stdout proves the handler didn't fire.
        result = _run_cli(
            tmp_path,
            cfg_overrides={"cmd_prefix": "!"},
            script_lines=["/print hello"],
        )

        # Assert
        actual = result.stdout
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "hello\n" not in actual, (
            f"/print dispatched even though cmd_prefix='!'; the "
            f"prefix override didn't take effect. stdout: {actual!r}"
        )


class TestPrefixOutputFlowThrough:
    """Commands whose output REFERENCES other commands should render
    those references with the live prefix, not a hardcoded ``/``.

    This is the class of bug a dispatch-only test can't catch: a
    plugin that builds help text like ``f"See also /cfg"`` instead
    of ``f"See also {prefix}cfg"`` would ship `/cfg` to a user
    running with `cmd_prefix='!'`, which reads wrong and doesn't
    actually work when they paste it back in.

    These tests are narrow: they assert the LIVE prefix appears
    (positive) and specific patterns known to be cross-references
    using the DEFAULT prefix do NOT appear (negative).  False
    positives from URLs, file paths, and user-data fields are
    avoided by checking for ``SPACE + "/" + known_cmd`` rather
    than any "/".
    """

    def test_help_listing_uses_live_prefix_for_command_names(self, tmp_path):
        # Arrange -- `!help` lists every command.  The left column
        # should read `!cmd`, never `/cmd`.
        result = _run_cli(
            tmp_path,
            cfg_overrides={"cmd_prefix": "!"},
            script_lines=["!help"],
        )

        # Assert
        actual = result.stdout
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # Positive: live prefix appears in the listing.
        assert "!help" in actual, "own command listed with live prefix"
        assert "!cfg" in actual, "cfg listed with live prefix"
        assert "!print" in actual, "print listed with live prefix"
        # Negative: the two-space-indent pattern that starts every
        # listing line must NOT begin with a `/`-prefixed command.
        # A "  /cfg " on a listing line would be the smoking-gun
        # shape for a hardcoded prefix in the help-listing renderer.
        for cmd in ("help", "cfg", "print", "ver", "echo"):
            assert f"  /{cmd} " not in actual and f"  /{cmd}\n" not in actual, (
                f"/help listing contains hardcoded '/{cmd}' in the "
                f"command-name column: the listing renderer isn't "
                f"using the live prefix.  stdout: {actual!r}"
            )

    def test_help_detail_cross_references_use_live_prefix(self, tmp_path):
        # Arrange -- drill into a single command's help.  The help
        # text (right column / long_help) commonly cross-references
        # other commands.  Catch the case where those references
        # are hardcoded `/`.
        result = _run_cli(
            tmp_path,
            cfg_overrides={"cmd_prefix": "!"},
            script_lines=["!help cfg"],
        )

        # Assert
        actual = result.stdout
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # The help output for !cfg references !cfg.auto in its prose.
        # If it reads `/cfg.auto` instead, the plugin author hardcoded
        # the prefix in their help text.  We check for the smoking-gun
        # phrase: whitespace + "/cfg.auto".
        assert " /cfg.auto" not in actual, (
            f"!help cfg contains a hardcoded '/cfg.auto' reference -- "
            f"plugin help text should use the live prefix.  stdout: {actual!r}"
        )

    def test_search_results_use_live_prefix(self, tmp_path):
        # Arrange -- `!search` finds commands matching a query and
        # prints them.  Results should read `!cmd`, not `/cmd`.
        result = _run_cli(
            tmp_path,
            cfg_overrides={"cmd_prefix": "!"},
            script_lines=["!search print"],
        )

        # Assert
        actual = result.stdout
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # Positive: `!print` appears (the main match).
        assert "!print" in actual, f"search didn't find !print: {actual!r}"
        # Negative: no `/print` in search results either.  If the
        # search renderer composes its output with a hardcoded `/`,
        # `/print` shows up instead.
        assert " /print" not in actual and "/print\n" not in actual, (
            f"!search results contain '/print' -- the search renderer "
            f"isn't using the live prefix.  stdout: {actual!r}"
        )

    def test_cfg_dump_reports_live_prefix_value(self, tmp_path):
        # Arrange -- `!cfg` (bare) dumps the full config JSON.  The
        # `cmd_prefix` field should show `!`.  We don't check for
        # absence of `/` in the output because the dump is JSON and
        # includes user-authored custom_buttons command strings
        # (e.g. `"/cfg.info"`) which are data, not prefix literals.
        result = _run_cli(
            tmp_path,
            cfg_overrides={"cmd_prefix": "!"},
            script_lines=["!cfg"],
        )

        # Assert
        actual = result.stdout
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert '"cmd_prefix": "!"' in actual, (
            f"!cfg dump doesn't reflect the configured cmd_prefix: {actual!r}"
        )


class TestPrefixExclamation:
    """Run a handful of commands with cmd_prefix='!' and assert they dispatch."""

    def test_print_dispatches_with_bang_prefix(self, tmp_path):
        # Arrange - a config whose prefix is '!' instead of '/'
        result = _run_cli(
            tmp_path,
            cfg_overrides={"cmd_prefix": "!"},
            script_lines=["!print hello from bang"],
        )

        # Assert - /print handler fired and wrote the message
        actual = result.stdout
        assert result.returncode == 0, f"cli exited non-zero, stderr: {result.stderr}"
        assert "hello from bang" in actual, \
            f"!print didn't dispatch, stdout: {actual!r}"

    def test_slash_is_not_dispatched_when_prefix_is_bang(self, tmp_path):
        # Arrange - typing '/print foo' with a '!' prefix config should
        # send "/print foo" to the device as a raw serial command,
        # NOT dispatch to the /print REPL handler.  The demo device
        # doesn't understand "/print foo" so there's no "hello"
        # anywhere in the output.
        result = _run_cli(
            tmp_path,
            cfg_overrides={"cmd_prefix": "!"},
            script_lines=["/print hello"],
        )

        # Assert
        actual = result.stdout
        assert result.returncode == 0, f"cli exited non-zero, stderr: {result.stderr}"
        # The REPL-handler echo would produce the bare message "hello"
        # on its own line.  If we see that, the handler ran, which
        # means the prefix override didn't work.
        assert "hello\n" not in actual, (
            f"/print dispatched even though cmd_prefix='!'; "
            f"the prefix override didn't take effect. stdout: {actual!r}"
        )

    def test_cfg_read_with_bang_prefix(self, tmp_path):
        # Arrange - read back the cmd_prefix value to prove /cfg
        # routes through the new prefix.
        result = _run_cli(
            tmp_path,
            cfg_overrides={"cmd_prefix": "!"},
            script_lines=["!cfg cmd_prefix"],
        )

        # Assert - the reported value should include the '!' setting.
        actual = result.stdout
        assert result.returncode == 0, f"cli exited non-zero, stderr: {result.stderr}"
        # !cfg cmd_prefix prints the single value.
        assert "!" in actual, f"cfg didn't report the prefix value: {actual!r}"

    def test_device_command_still_works(self, tmp_path):
        # Arrange - device commands (no prefix) should go through
        # regardless of what the REPL prefix is.
        result = _run_cli(
            tmp_path,
            cfg_overrides={"cmd_prefix": "!"},
            script_lines=["AT"],
        )

        # Assert - demo device responds to AT with OK.
        actual = result.stdout
        assert result.returncode == 0, f"cli exited non-zero, stderr: {result.stderr}"
        assert "OK" in actual, f"AT command didn't reach demo device: {actual!r}"

    def test_mixed_script_runs_end_to_end(self, tmp_path):
        # Arrange - a tiny script that mixes REPL commands (!) and
        # device commands, exercising the dispatch boundary both ways.
        result = _run_cli(
            tmp_path,
            cfg_overrides={"cmd_prefix": "!"},
            script_lines=[
                "!print one",
                "AT",
                "!print two",
                "AT+PROD-ID",
                "!print three",
            ],
        )

        # Assert - all three prints ran and both AT commands got
        # responses.  We don't assert ordering beyond "everything
        # happened" because /print goes to the output log and AT
        # responses come from the reader thread; the exact
        # interleaving isn't the test's concern.
        actual = result.stdout
        assert result.returncode == 0, f"cli exited non-zero, stderr: {result.stderr}"
        assert "one" in actual, "first !print dispatched"
        assert "two" in actual, "second !print dispatched"
        assert "three" in actual, "third !print dispatched"
        assert "OK" in actual, "AT got response"


class TestPrefixDefault:
    """Sanity check: the default '/' prefix still works end-to-end.

    Guards against an accidental change that breaks the default
    while fixing the override path.  A short positive test so the
    suite doesn't silently pass when ! works but / regresses.
    """

    def test_slash_print_dispatches_by_default(self, tmp_path):
        # Arrange - no override; use the project default.
        result = _run_cli(
            tmp_path,
            cfg_overrides={},
            script_lines=["/print hello default"],
        )

        # Assert
        actual = result.stdout
        assert result.returncode == 0, f"cli exited non-zero, stderr: {result.stderr}"
        assert "hello default" in actual, \
            f"default '/' prefix regressed: {actual!r}"
