"""Tests for the verbose/.quiet -> output-level migration.

Covers both the runtime forwarders (so old scripts keep running) and
the ``/run.legacy`` scanner (so users can rewrite to canonical names).
"""

from __future__ import annotations

import json

import pytest

from termapy.legacy import LEGACY_COMMANDS, LEGACY_REWRITES
from termapy.plugins import CapabilitySet
from termapy.repl import ReplEngine
from termapy.run_legacy import _scan_line

# Importing termapy.legacy (above) runs the module-level
# LEGACY_COMMANDS / LEGACY_REWRITES registrations.  In production the
# runtime forwarders are registered by ReplEngine._register_legacy_forwarders.


# ── Scanner: simple renames ───────────────────────────────────────────────────


class TestScanSimpleRenames:
    def test_echo_quiet_to_silent(self):
        # Arrange
        line = "/echo.quiet on"

        # Act
        new_line, hits = _scan_line(line, "/")

        # Assert
        actual = new_line
        expected = "/echo.silent on"
        assert actual == expected, "echo.quiet rewritten to echo.silent"
        assert len(hits) == 1, "one hit recorded"

    def test_term_echo_quiet_to_silent(self):
        # Arrange
        line = "/term.echo.quiet off"

        # Act
        new_line, hits = _scan_line(line, "/")

        # Assert
        actual = new_line
        expected = "/term.echo.silent off"
        assert actual == expected, "term.echo.quiet rewritten"

    def test_indent_preserved(self):
        # Arrange -- some scripts indent commands.
        line = "    /echo.quiet on"

        # Act
        new_line, _ = _scan_line(line, "/")

        # Assert
        actual = new_line
        expected = "    /echo.silent on"
        assert actual == expected, "indent preserved"

    def test_non_command_line_untouched(self):
        # Arrange -- serial line, not a REPL command.
        line = "AT+INFO"

        # Act
        new_line, hits = _scan_line(line, "/")

        # Assert
        assert new_line == line, "device commands pass through"
        assert hits == [], "no hits"

    def test_unknown_command_untouched(self):
        # Arrange
        line = "/unknown_cmd arg"

        # Act
        new_line, hits = _scan_line(line, "/")

        # Assert
        assert new_line == line, "non-legacy commands untouched"
        assert hits == [], "no hits"


# ── Scanner: args-aware rewrites ──────────────────────────────────────────────


class TestScanArgsAware:
    def test_verbose_on(self):
        # Arrange
        line = "/verbose on"

        # Act
        new_line, hits = _scan_line(line, "/")

        # Assert
        actual = new_line
        expected = "/term.output verbose"
        assert actual == expected, "verbose on -> term.output verbose"
        assert len(hits) == 1, "one hit"

    def test_verbose_off(self):
        # Arrange
        line = "/verbose off"

        # Act
        new_line, _ = _scan_line(line, "/")

        # Assert
        actual = new_line
        expected = "/term.output normal"
        assert actual == expected, "verbose off -> term.output normal"

    def test_term_verbose_on(self):
        # Arrange
        line = "/term.verbose on"

        # Act
        new_line, _ = _scan_line(line, "/")

        # Assert
        actual = new_line
        expected = "/term.output verbose"
        assert actual == expected, "term.verbose on -> term.output verbose"

    def test_term_verbose_quiet_on(self):
        # Arrange -- old "set silently" idiom; .quiet meant silent.
        line = "/term.verbose.quiet on"

        # Act
        new_line, _ = _scan_line(line, "/")

        # Assert
        actual = new_line
        expected = "/term.output.silent verbose"
        assert actual == expected, ".quiet old idiom rewritten to .silent"

    def test_args_aware_wins_over_simple(self):
        # Arrange -- both LEGACY_COMMANDS["verbose"] and a regex match.
        # The regex (longer) should win.
        line = "/verbose on"

        # Act
        new_line, _ = _scan_line(line, "/")

        # Assert -- if simple match had won we'd see "term.output on";
        # the regex rewrites the args too.
        actual = new_line
        expected = "/term.output verbose"
        assert actual == expected, "args-aware wins over name-only"

    def test_bare_verbose_falls_back_to_simple(self):
        # Arrange -- no args, no regex match; LEGACY_COMMANDS catches it.
        line = "/verbose"

        # Act
        new_line, _ = _scan_line(line, "/")

        # Assert
        actual = new_line
        expected = "/term.output"
        assert actual == expected, "bare verbose -> bare term.output"


# ── Tables actually populated ────────────────────────────────────────────────


class TestLegacyTablesPopulated:
    """The forwarder modules must register their entries on import."""

    def test_verbose_in_simple_table(self):
        actual = "verbose" in LEGACY_COMMANDS
        assert actual, "verbose plugin populates LEGACY_COMMANDS"

    def test_echo_quiet_in_simple_table(self):
        actual = LEGACY_COMMANDS.get("echo.quiet")
        expected = "echo.silent"
        assert actual == expected, "echo.quiet -> echo.silent registered"

    def test_rewrites_populated(self):
        # Arrange
        all_repls = [r for _, r in LEGACY_REWRITES]

        # Assert -- spot-check that the verbose rewrites landed.
        assert "term.output verbose" in all_repls, "verbose on rewrite present"
        assert "term.output normal" in all_repls, "verbose off rewrite present"


# ── Runtime forwarder: /verbose dispatches to /term.output ────────────────────


@pytest.fixture
def engine_with_term(tmp_path):
    """ReplEngine with builtin plugins loaded so /term.output works."""
    cfg = {"port": "COM4", "baud_rate": 115200, "line_ending": "\r"}
    config_path = tmp_path / "sub" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run"):
        (config_path.parent / sub).mkdir(exist_ok=True)
    writes: list[tuple[str, str | None]] = []
    eng = ReplEngine(
        cfg, str(config_path), lambda t, c=None: writes.append((t, c))
    )
    # ReplEngine.__init__ already loads builtins; wire ctx.internal.dispatch
    # the way TerminalHost would, so legacy forwarders that delegate via
    # ctx.internal.dispatch reach the REPL pipeline instead of the no-op
    # lambda default.
    eng.ctx.internal.dispatch = eng.dispatch
    # Real CLI/TUI hosts advertise interactive + gui_apps; legacy aliases
    # under test (/verbose, /term.verbose) declare needs.interactive=True.
    # Mirror the host capability set so the dispatcher allows them through.
    eng.ctx.capabilities = CapabilitySet(interactive=True, gui_apps=True)
    flags = eng.ctx.ns("flags")
    flags["echo"] = True
    flags["output_level"] = "normal"
    flags["hex_mode"] = False
    return eng, writes


class TestVerboseRuntimeForward:
    def test_verbose_on_sets_level_to_verbose(self, engine_with_term):
        # Arrange
        eng, _ = engine_with_term

        # Act
        eng.dispatch("verbose on")

        # Assert
        actual = eng.ctx.ns("flags")["output_level"]
        expected = "verbose"
        assert actual == expected, "verbose on -> output_level verbose"

    def test_verbose_off_sets_level_to_normal(self, engine_with_term):
        # Arrange -- start at verbose, then turn off.
        eng, _ = engine_with_term
        eng.ctx.ns("flags")["output_level"] = "verbose"

        # Act
        eng.dispatch("verbose off")

        # Assert
        actual = eng.ctx.ns("flags")["output_level"]
        expected = "normal"
        assert actual == expected, "verbose off -> output_level normal"

    def test_verbose_emits_deprecation_note_once(self, engine_with_term):
        # Arrange
        eng, writes = engine_with_term

        # Act -- two invocations.
        eng.dispatch("verbose on")
        first_count = sum(
            1 for t, _ in writes if "legacy" in t and "verbose" in t
        )
        writes.clear()
        eng.dispatch("verbose off")
        second_count = sum(
            1 for t, _ in writes if "legacy" in t and "verbose" in t
        )

        # Assert -- the warning fires once per session, not per call.
        assert first_count == 1, "first /verbose call warns"
        assert second_count == 0, "second /verbose call stays silent"

    def test_term_verbose_on_sets_level(self, engine_with_term):
        # Arrange
        eng, _ = engine_with_term

        # Act
        eng.dispatch("term.verbose on")

        # Assert
        actual = eng.ctx.ns("flags")["output_level"]
        expected = "verbose"
        assert actual == expected, "term.verbose on -> verbose level"


class TestSimpleForwarderRuntime:
    """The central make_forwarder aliases reach their /term.* target.

    Covers the plain name-forward forwarders (echo, show_line_endings)
    that moved out of per-file plugins into legacy.LEGACY_FORWARDERS;
    /verbose has its own arg-translating coverage above.
    """

    def test_echo_off_forwards_to_term_echo(self, engine_with_term):
        # Arrange
        eng, _ = engine_with_term
        eng.ctx.ns("flags")["echo"] = True

        # Act -- /echo forwards to /term.echo, which sets flags["echo"].
        eng.dispatch("echo off")

        # Assert
        actual = eng.ctx.ns("flags")["echo"]
        assert actual is False, "/echo off -> /term.echo set flags['echo'] False"

    def test_show_line_endings_on_forwards_to_term(self, engine_with_term):
        # Arrange
        eng, _ = engine_with_term

        # Act -- /show_line_endings forwards to /term.line_endings.
        result = eng.dispatch("show_line_endings on")

        # Assert -- the forwarded result carries the toggle's reported state,
        # proving the forward reached /term.line_endings and it ran.
        assert result.success, "/show_line_endings forwards successfully"
        assert result.value == "on", (
            "/show_line_endings on -> /term.line_endings reported 'on'"
        )

    def test_forwarder_emits_deprecation_note_once(self, engine_with_term):
        # Arrange
        eng, writes = engine_with_term

        # Act -- two invocations of the same legacy alias.
        eng.dispatch("echo off")
        first_count = sum(1 for t, _ in writes if "legacy" in t and "echo" in t)
        writes.clear()
        eng.dispatch("echo on")
        second_count = sum(1 for t, _ in writes if "legacy" in t and "echo" in t)

        # Assert -- one note per session, not per call.
        assert first_count == 1, "first /echo call warns"
        assert second_count == 0, "second /echo call stays silent"
