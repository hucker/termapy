"""A port's state color: green held by this termapy session, yellow by another termapy session.

Only termapy sessions are seen, the same on every OS: each running termapy
names the port it holds in its breadcrumb, and reading those opens no
port.  A port held by any other program, or free, stays plain, so plain
never claims a port is free.  The color is one function
(``port_control.port_state_style``) read by ``/port.list``, the port
picker, ``/port.usb`` and ``termapy --ports``.  These tests hand the
deciders their breadcrumbs, so no port or process is involved.
"""

from __future__ import annotations

import pytest

from termapy.breadcrumbs import Crumb
from termapy.port_control import THIS_SESSION, ChipFacts, port_state_style, session_holding

OWN_PID = 1000


def _crumb(pid: int, open_port: str, frontend: str = "mcp") -> Crumb:
    """A running termapy's breadcrumb holding ``open_port``."""
    return Crumb(
        pid=pid, token="t", started="2026-11-12T10:00:00", frontend=frontend, open_port=open_port
    )


class TestWhichSession:
    """The session holding a port comes from the breadcrumbs; no breadcrumb means none is known."""

    def test_this_processs_breadcrumb_is_this_session(self):
        """The port this process's own breadcrumb holds is this session's."""
        actual = session_holding("COM7", [_crumb(OWN_PID, "COM7")], OWN_PID)
        assert actual == THIS_SESSION

    def test_another_termapy_is_named_by_frontend_and_pid(self):
        """A port another termapy holds names that session; the port spelling matches as the OS opens it."""
        actual = session_holding("COM7", [_crumb(17140, "com7")], OWN_PID, hides_host=False)
        assert actual == "termapy mcp, PID 17140"

    def test_an_mcp_client_is_told_only_that_a_session_holds_it(self):
        """Without the host view another session is unnamed: no PID, no frontend."""
        actual = session_holding("COM7", [_crumb(17140, "COM7")], OWN_PID, hides_host=True)
        assert actual == "another termapy session"

    def test_a_port_no_session_holds_has_none(self):
        """No breadcrumb claims it: None, which says nothing about other programs."""
        assert session_holding("COM7", [_crumb(17140, "COM3")], OWN_PID) is None


class TestTheColor:
    """One mapping from session to color, read by every surface."""

    @pytest.mark.parametrize(
        ("session", "expected"),
        [(THIS_SESSION, "green"), ("termapy mcp, PID 17140", "yellow"), (None, "")],
    )
    def test_session_to_color(self, session, expected):
        """This session is green, another termapy session yellow, no session plain."""
        assert port_state_style(session) == expected

    def test_list_colors_the_port_cell(self):
        """/port.list draws a port's name in its session color and leaves the other cells alone."""
        # Arrange
        from termapy.plugins.views import TableView
        from termapy.port_format import PORT_TABLE_COLUMNS, facts_to_json_record

        records = [
            facts_to_json_record(ChipFacts(device="COM3", session=THIS_SESSION)),
            facts_to_json_record(ChipFacts(device="COM4")),
        ]
        view = TableView("Ports", "port", PORT_TABLE_COLUMNS, tuple(records))

        # Act
        lines = view.segment_lines(200)

        # Assert
        styles = {
            piece.text.strip(): piece.style
            for pieces, _ in lines
            for piece in pieces
            if piece.text.strip() in ("COM3", "COM4")
        }
        assert styles == {"COM3": "green", "COM4": ""}

    @pytest.mark.parametrize(
        ("facts", "expected"),
        [
            (
                ChipFacts(device="COM7", session="termapy tui, PID 22056"),
                (True, "termapy tui, PID 22056"),
            ),
            (ChipFacts(device="COM7"), (None, None)),
            (ChipFacts(device="COM7", in_use="no"), (False, None)),
            (ChipFacts(device="COM7", in_use="yes (python (PID 8842))"), (True, None)),
        ],
    )
    def test_the_record_says_what_was_checked(self, facts, expected):
        """in_use is True or False only when something checked, None when nothing did; session names a termapy holder."""
        from termapy.port_format import facts_to_json_record

        record = facts_to_json_record(facts)
        assert (record["in_use"], record["session"]) == expected

    @pytest.mark.parametrize(
        ("session", "expected"),
        [(THIS_SESSION, "bold green"), ("termapy cli, PID 9", "bold yellow"), (None, "bold")],
    )
    def test_the_usb_tree_colors_the_port_name(self, session, expected):
        """/port.usb draws -> COMx bold, in the port's session color when it has one."""
        # Arrange
        from termapy.usb_tree import UsbRecord, build_tree, render_tree_pieces, tag_serial_ports

        roots = build_tree([UsbRecord("1"), UsbRecord("1-1", description="FTDI")])
        tag_serial_ports(roots, [ChipFacts(device="COM3", location="1-1", session=session)])

        # Act
        lines = render_tree_pieces(roots)

        # Assert
        names = [piece for line in lines for piece in line if piece.text == "-> COM3"]
        assert [piece.style for piece in names] == [expected]

    def test_ports_colors_each_rows_name(self):
        """termapy --ports at a terminal colors each row's port name; the text is the plain table's."""
        # Arrange
        from termapy.cli_flags import port_rows_styled
        from termapy.port_format import format_table

        facts = [
            ChipFacts(device="COM3", session="termapy mcp, PID 17140"),
            ChipFacts(device="COM4"),
        ]
        lines = format_table(facts, 200)

        # Act
        styled = port_rows_styled(lines, facts)

        # Assert
        assert ["".join(piece.text for piece in line) for line in styled] == lines, "same text"
        first_pieces = [line[0] for line in styled[2:]]
        assert [(piece.text, piece.style) for piece in first_pieces] == [
            ("COM3", "yellow"),
            (lines[3], ""),
        ], "COM3 held by another session, COM4 plain"

    def test_the_picker_colors_only_session_held_ports(self):
        """The picker's color map holds each session-held port's color and leaves the rest out."""
        from termapy.dialogs._common import _port_colors

        facts = [
            ChipFacts(device="COM3", session=THIS_SESSION),
            ChipFacts(device="COM4"),
            ChipFacts(device="COM5", session="termapy cli, PID 9"),
        ]
        assert _port_colors(facts) == {"COM3": "green", "COM5": "yellow"}
