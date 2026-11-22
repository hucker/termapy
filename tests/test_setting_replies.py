"""A setting command answers with its value and nothing else (registry ``setting-reply-is-value``).

Every setting-style command is driven on the DEMO rig, bare and with a
value, and the one line it prints must be ``scripting.value_text`` of the
value it returns: no ``name = value``, no prose, no note, so what a person
reads is what ``$(X) <- cmd`` captures.  The list is what the 2026-11-14
audit ran; a new setting command joins it.
"""

from __future__ import annotations

import pytest

from termapy.scripting import value_text

pytestmark = pytest.mark.rule("setting-reply-is-value")

# Each line leaves the rig as it found it, or the next line restores it, so
# the order matters only within a pair.
SETTING_LINES = [
    "/term.echo",
    "/term.echo on",
    "/term.echo off",
    "/term.hex",
    "/term.hex on",
    "/term.hex off",
    "/proto.hex",
    "/proto.hex on",
    "/proto.hex off",
    "/term.eol",
    "/term.eol cr",
    "/term.eol.rx",
    "/term.eol.rx auto",
    "/term.eol.markers",
    "/term.eol.markers on",
    "/term.eol.markers off",
    "/term.json_fmt.indent",
    "/term.json_fmt.indent 4",
    "/term.json_fmt.indent 2",
    "/term.json_fmt.highlight",
    "/term.encoding",
    "/term.encoding utf-8",
    "/term.output",
    "/term.request",
    "/term.send_bare_enter",
    "/term.control_keys",
    "/term.timestamps",
    "/term.timestamps on",
    "/term.timestamps off",
    "/find.max_count",
    "/find.max_count 300",
    "/xfer.root",
    "/cfg echo",
    "/cfg echo on",
    "/cfg echo off",
    "/cfg max_lines",
    "/cfg max_lines 500",
    "/port.baud_rate",
    "/port.baud_rate 115200",
    "/port.baud_rate cycle",
    "/port.baud_rate 115200",
    "/port.byte_size",
    "/port.byte_size 8",
    "/port.byte_size cycle",
    "/port.byte_size 8",
    "/port.parity",
    "/port.parity N",
    "/port.parity cycle",
    "/port.parity N",
    "/port.stop_bits",
    "/port.stop_bits 1",
    "/port.stop_bits cycle",
    "/port.stop_bits 1",
    "/port.flow_control",
    "/port.flow_control none",
    "/port.flow_control cycle",
    "/port.flow_control none",
    "/port.dtr",
    "/port.dtr 1",
    "/port.dtr toggle",
    "/port.dtr toggle",
    "/port.rts",
    "/port.rts 0",
    "/port.rts toggle",
    "/port.rts toggle",
    "/port.mode",
    "/port.mode 115200 8N1",
]


@pytest.mark.parametrize("line", SETTING_LINES)
def test_a_setting_command_answers_with_its_value(cli, capsys, line):
    """The printed line is value_text of the value, so a person reads what a script captures."""
    capsys.readouterr()
    result = cli.repl.dispatch_full(line)
    printed = capsys.readouterr().out.strip()
    assert result.success, f"{line}: {result.error}"
    assert printed == value_text(result.value), (
        f"{line} printed {printed!r} but its value is {result.value!r}"
    )
    # A record page's rows are indented where a result line is not (the
    # TUI showed "  115200" beside "1"), so the answer goes on the result
    # line and never as a one-row page.
    assert result.view is None, f"{line} answered as a page, not a result line"


def test_a_disconnected_query_answers_the_config_value_alone(cli, capsys):
    """Off the port a /port.* query answers the config's value with no note beside it."""
    cli._disconnect()
    for line, expected in (("/port.baud_rate", 115200), ("/port.flow_control", "none")):
        capsys.readouterr()
        result = cli.repl.dispatch_full(line)
        assert (capsys.readouterr().out.strip(), result.value) == (str(expected), expected), (
            f"{line} answers the config's value, nothing beside it"
        )
