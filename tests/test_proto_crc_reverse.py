"""End-to-end tests for /proto.crc.reverse and /proto.crc.find cmd=.

These tests drive the full ``termapy --cli --demo`` stack: the engine
opens the demo's ``FakeSerial`` port, the read loop pulls bytes off
the demo's output buffer, the handler's ``cmd=`` mode writes a
trigger, captures the response, and feeds it into either
``crcglot.detect`` (find) or ``crcglot.reverse_packets`` (reverse).

Slow because each test boots a subprocess of termapy.  Marked
accordingly so the fast suite skips them.

Covers:

* ``/proto.crc.find cmd=AT+RND`` resolves to one of the curated
  catalog algorithms the demo emits.  cmd= sends the bare trigger
  verbatim -- the configured line ending is auto-appended -- so there
  is no quoting and no explicit ``\\r``.
* ``/proto.crc.reverse cmd=AT+RND.CUSTOM count=13 crc_bytes=2``
  recovers a polynomial that matches the demo's
  ``AT+RND.CUSTOM.REVEAL`` output, and lists every equivalent
  (init, xorout) labeling.
* ``$(rev) <- /proto.crc.reverse ...`` capture pipeline -- the
  returned value drops straight into a subsequent
  ``/proto.crc.c $(rev)`` codegen invocation.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from termapy.defaults import DEFAULT_CFG


def _run_cli(
    tmp_path: Path, script_lines: list[str]
) -> subprocess.CompletedProcess[str]:
    """Invoke termapy --cli against a throwaway config and script."""
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    default_serial = DEFAULT_CFG["serial"]
    assert isinstance(default_serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {
        **DEFAULT_CFG,
        "serial": {**default_serial, "port": "DEMO"},
        "auto_connect": True,
    }
    (proj_dir / "proj.cfg").write_text(json.dumps(cfg, indent=4))
    script_path = tmp_path / "tour.run"
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
        timeout=60,
    )


@pytest.mark.slow
class TestFindCmdMode:
    """``cmd=`` mode on /proto.crc.find captures + identifies."""

    def test_cmd_at_rnd_resolves_to_curated_algorithm(self, tmp_path):
        # Act
        result = _run_cli(tmp_path, ["/proto.crc.find cmd=AT+RND"])

        # Assert -- one of the curated algorithms appears.  Don't pin
        # which one (the demo picks randomly).
        assert result.returncode == 0, (
            f"/proto.crc.find cmd= must exit 0, got {result.returncode}. "
            f"stderr: {result.stderr!r}"
        )
        curated = (
            "crc8", "crc8-maxim",
            "crc16-modbus", "crc16-xmodem",
            "crc32", "crc32-bzip2",
        )
        assert any(name in result.stdout for name in curated), (
            f"/proto.crc.find cmd=AT+RND should report one of "
            f"{curated}; stdout: {result.stdout!r}"
        )


@pytest.mark.slow
class TestReverseCmdMode:
    """``cmd=`` mode on /proto.crc.reverse captures + recovers."""

    def test_count_13_recovers_secret_polynomial(self, tmp_path):
        # Arrange -- 13 packets at deterministic length cycle is enough
        # for crcglot.reverse_packets to recover the secret polynomial
        # (0x1A2B) reliably; the REVEAL'd init/xorout may differ from
        # the recovered ones (equivalent (init, xorout) labeling) but
        # the polynomial is uniquely determined.
        lines = [
            "/proto.crc.reverse cmd=AT+RND.CUSTOM count=13 crc_bytes=2",
            "AT+RND.CUSTOM.REVEAL",
        ]

        # Act
        result = _run_cli(tmp_path, lines)

        # Assert
        assert result.returncode == 0, (
            f"reverse must exit 0, got {result.returncode}. "
            f"stderr: {result.stderr!r}"
        )
        # The recovered polynomial matches the secret poly=0x1A2B (the
        # rest of the params -- init/xorout -- may differ via the (x+1)
        # equivalence class; that's correct + documented).
        assert "0x1A2B" in result.stdout, (
            f"reverse should recover poly=0x1A2B; stdout: {result.stdout!r}"
        )
        assert "width=16" in result.stdout, (
            f"reverse should recover width=16; stdout: {result.stdout!r}"
        )
        # Fix: reverse lists EVERY (init, xorout) labeling, not just the
        # first.  The demo's poly carries a 1-bit (x+1) ambiguity, so two
        # labelings are recovered and both must be printed.
        assert result.stdout.count("init=0x") >= 2, (
            f"reverse should list all equivalent labelings, not just one; "
            f"stdout: {result.stdout!r}"
        )
        # The REVEAL line confirms the demo's secret matches what we
        # expected the test to drive against.
        assert "poly=0x1A2B" in result.stdout, (
            f"REVEAL should print the demo's secret poly; "
            f"stdout: {result.stdout!r}"
        )

    def test_reverse_value_pipes_into_codegen(self, tmp_path):
        # Arrange -- the headline pipeline: capture recovered params via
        # $(rev), pass straight to /proto.crc.c.  Generated source must
        # contain the recovered polynomial (or its byte-reversed form
        # depending on how crcglot renders the table) -- check for the
        # secret poly value either way.
        lines = [
            "$(rev) <- /proto.crc.reverse cmd=AT+RND.CUSTOM count=13 crc_bytes=2",
            "/proto.crc.c $(rev)",
        ]

        # Act
        result = _run_cli(tmp_path, lines)

        # Assert
        assert result.returncode == 0, (
            f"pipeline must exit 0, got {result.returncode}. "
            f"stderr: {result.stderr!r}"
        )
        # The codegen output should mention `recovered` (the name the
        # /proto.crc.reverse handler assigns) somewhere in the emitted C
        # source -- it appears in struct names, function names, etc.
        assert "recovered" in result.stdout, (
            f"codegen for $(rev) should use the 'recovered' name; "
            f"stdout: {result.stdout!r}"
        )


# ── $(*NAME) dereference on explicit packets ────────────────────────────────
#
# Fast, in-process: these exercise the argument path only, so they need no
# subprocess and are NOT marked slow (unlike the cmd= capture tests above,
# which drive a real demo serial port).


class TestReverseDeref:
    """/proto.crc.reverse resolves $(*NAME) per packet token.

    The command parses its own arguments (its cmd= value may contain spaces
    and is followed by more keywords -- the documented ``params`` boundary in
    CLAUDE.md), so it opts into the dereference primitive explicitly rather
    than inheriting it from the param binder.
    """

    P1 = "010203AA55"
    P2 = "040506BB66"
    P3 = "0708CCAA"

    def _engine(self):
        from termapy.repl import ReplEngine
        engine = ReplEngine({}, "", lambda t, c=None: None)
        engine.ctx.dispatch = engine.dispatch
        return engine

    def test_references_match_literals(self):
        # Arrange -- the same three packets, expressed both ways
        from termapy.builtins.commands import var
        engine = self._engine()
        var._VARS.update({"p1": self.P1, "p2": self.P2, "p3": self.P3})

        # Act
        literal = engine.dispatch(
            f"proto.crc.reverse crc_bytes=2 {self.P1} {self.P2} {self.P3}"
        )
        referenced = engine.dispatch(
            "proto.crc.reverse crc_bytes=2 $(*p1) $(*p2) $(*p3)"
        )

        # Assert -- identical outcome; the reference form is a pure substitution
        actual = (referenced.success, referenced.error)
        expected = (literal.success, literal.error)
        assert actual == expected, (
            "dereferenced packets must reduce to exactly the literal packets"
        )
        var._VARS.clear()

    def test_value_with_spaces_stays_one_packet(self):
        # Arrange -- the case the primitive exists for.  Spliced with $(NAME)
        # these would fork into 14 one-byte tokens; dereferenced they are three
        # packets, so the result must match the unspaced literals.
        from termapy.builtins.commands import var
        engine = self._engine()
        var._VARS.update({
            "s1": "01 02 03 AA 55",
            "s2": "04 05 06 BB 66",
            "s3": "07 08 CC AA",
        })

        # Act
        literal = engine.dispatch(
            f"proto.crc.reverse crc_bytes=2 {self.P1} {self.P2} {self.P3}"
        )
        spaced = engine.dispatch(
            "proto.crc.reverse crc_bytes=2 $(*s1) $(*s2) $(*s3)"
        )

        # Assert
        actual = (spaced.success, spaced.error)
        expected = (literal.success, literal.error)
        assert actual == expected, (
            "a dereferenced value containing spaces must bind as ONE packet"
        )
        var._VARS.clear()

    def test_unknown_reference_errors(self):
        # Act
        result = self._engine().dispatch(
            f"proto.crc.reverse crc_bytes=2 $(*nope) {self.P2}"
        )

        # Assert -- names the missing variable rather than failing later as
        # "Invalid hex bytes", which is what the literal text would produce
        assert not result.success, "an undefined reference fails"
        assert "unknown variable: 'nope'" in result.error, (
            "the failure names the missing variable"
        )

    def test_embedded_reference_errors(self):
        # Act
        result = self._engine().dispatch(
            f"proto.crc.reverse crc_bytes=2 x$(*p1)y {self.P2}"
        )

        # Assert
        assert not result.success, "a partial reference fails"
        assert "invalid reference" in result.error, (
            "a reference embedded in a larger token is a mistake, not a literal"
        )

    def test_literal_hex_still_rejected_when_malformed(self):
        # Act -- the deref pass must not swallow the plain-hex error path
        result = self._engine().dispatch(
            f"proto.crc.reverse crc_bytes=2 ZZZZ {self.P2}"
        )

        # Assert
        assert not result.success, "non-hex packet fails"
        assert "Invalid hex bytes" in result.error, (
            "a malformed literal still reports the hex error"
        )
