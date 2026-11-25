"""A network port can be typed and tested: /port.test, the port dialogs' Network Port... field, and local --serve rows.

Nothing finds a port on another machine, so its ``rfc2217://`` or
``socket://`` URL is typed.  ``/port.test`` opens a port and closes it again
to see that it answers; the Select Serial Port dialog and the New Config
wizard take a typed URL and run that test through the host; a port a local
``termapy --serve`` shares is offered as a row by its URL.
"""

from __future__ import annotations

import asyncio
import os
import socket

import pytest
from pilot_wait import until
from textual.app import App
from textual.widgets import Input, Static

from termapy import breadcrumbs
from termapy.dialogs import PortPicker
from termapy.dialogs._common import NETWORK_PORT_FORM
from termapy.dialogs.quick_setup import QuickSetup
from termapy.plugins import CmdResult
from termapy.port_allow import PortAllowlist, PortPolicy
from termapy.port_control import served_port_facts

pytestmark = pytest.mark.sw_loopback

URL = "rfc2217://192.168.1.57:2217"

# Wide and tall enough for both dialogs' button rows.
_SIZE = (160, 50)


class _Host(App):
    """Bare host: no widgets of its own, just a stage for modals."""


class _Answers:
    """A host's ``test_port``: answers from a list, keeping each URL it was asked about."""

    def __init__(self, *results: CmdResult) -> None:
        self._results = list(results)
        self.asked: list[str] = []

    def __call__(self, url: str) -> CmdResult:
        """The next answer, for ``url``."""
        self.asked.append(url)
        return self._results.pop(0)


def _run(scenario) -> None:
    asyncio.run(scenario())


def _closed_tcp_port() -> int:
    """A local TCP port nothing listens on: bound, read and let go."""
    with socket.create_server(("127.0.0.1", 0)) as server:
        return server.getsockname()[1]


def _note(screen) -> str:
    """The line under the network field."""
    return str(screen.query_one("#net-note", Static).render())


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    """A private app state folder, so a test sees only the breadcrumbs it writes."""
    folder = tmp_path / "state"
    monkeypatch.setenv("TERMAPY_STATE_DIR", str(folder))
    return folder


class TestPortTest:
    """/port.test opens a port with the session's settings and closes it again."""

    def test_a_port_that_answers_answers_each_time(self, cli):
        """loop:// answers twice running: the test closes the port it opened."""
        # Act
        first = cli.repl.dispatch_full("/port.test loop://")
        second = cli.repl.dispatch_full("/port.test loop://")

        # Assert
        assert (first.success, first.value) == (True, True), first.error
        assert (second.success, second.value) == (True, True), "the first test let the port go"
        assert isinstance(first.data, dict) and first.data["port"] == "loop://", (
            "data names the port tested"
        )

    def test_the_sessions_own_port_is_refused(self, cli):
        """The port this session has open answers already, and a second open would fail."""
        # Act
        actual = cli.repl.dispatch_full("/port.test DEMO")

        # Assert
        assert (actual.success, actual.error) == (False, "Open in this session.")

    def test_a_port_that_does_not_answer_fails_with_the_reason(self, cli):
        """A socket:// port nothing listens on fails in plain words and leaves the session's port open."""
        # Arrange
        port = _closed_tcp_port()

        # Act
        actual = cli.repl.dispatch_full(f"/port.test socket://127.0.0.1:{port}")

        # Assert
        assert (actual.success, actual.error) == (
            False,
            f"Nothing answers at 127.0.0.1:{port}: no server listens there",
        ), "the refusal named for what it is"
        assert cli.repl.ctx.serial.is_connected(), "the session's own port is untouched"

    def test_wait_for_data_answers_outside_the_tui(self, cli):
        """ctx.serial.wait_for_data waits for the reply in the CLI too, where it once always said no."""
        # Arrange
        serial = cli.repl.ctx.serial
        serial.drain()

        # Act
        serial.send("AT")
        actual = serial.wait_for_data(2000)

        # Assert
        assert actual is True, "the DEMO's reply arrived and the handle saw it"


class TestServedRows:
    """A port a local termapy --serve shares is a picker row, named by its URL."""

    def test_a_served_port_is_a_row_by_its_url(self, state_dir):
        """The server's breadcrumb gives one row per served port: the URL, and which port it is."""
        # Arrange
        breadcrumbs.leave("serve", port="COM4", open_port="COM4", address=[URL])
        expected = [(URL, f"termapy serve COM4 (PID {os.getpid()})")]

        # Act
        actual = [(fact.device, fact.description) for fact in served_port_facts()]

        # Assert
        assert actual == expected

    def test_a_url_the_policy_refuses_is_left_out(self, state_dir):
        """A served URL the port policy does not name is not offered, as a refused device is not."""
        # Arrange
        breadcrumbs.leave("serve", port="COM4", open_port="COM4", address=[URL])
        policy = PortPolicy(allowlist=PortAllowlist.parse("COM3"))

        # Act
        actual = served_port_facts(policy=policy)

        # Assert
        assert actual == [], "the list names no network endpoint"


class TestThePickersNetworkField:
    """Select Serial Port: Network Port... swaps the list for a typed URL with Test and Use."""

    def test_use_dismisses_with_the_typed_url(self):
        """A typed rfc2217:// URL and Use: the dialog dismisses with the URL, as a picked row does."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                # Arrange
                results: list = []
                app.push_screen(PortPicker(), callback=results.append)
                await pilot.pause()
                await pilot.click("#port-network")
                app.screen.query_one("#net-url", Input).value = URL

                # Act
                await pilot.click("#port-use")
                await pilot.pause()

                # Assert
                assert results == [URL], "the URL goes where a picked port goes"

        _run(scenario)

    def test_a_url_that_is_no_network_port_stays_in_the_dialog(self):
        """A URL with no port, or not a network one, is refused where it was typed, saying what one looks like."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                # Arrange
                results: list = []
                app.push_screen(PortPicker(), callback=results.append)
                await pilot.pause()
                await pilot.click("#port-network")
                field = app.screen.query_one("#net-url", Input)

                for typed in ("rfc2217://192.168.1.57", "loop://"):
                    # Act
                    field.value = typed
                    await pilot.click("#port-use")
                    await pilot.pause()

                    # Assert
                    assert results == [], f"{typed} did not close the dialog"
                    assert _note(app.screen) == NETWORK_PORT_FORM, "the form a network port takes"

        _run(scenario)

    def test_test_shows_the_answer_and_then_the_failure(self):
        """Test asks the host's test_port off the main thread and shows each answer under the field."""

        async def scenario():
            app = _Host()
            answers = _Answers(
                CmdResult.ok(value=True, data={"port": URL, "seconds": 0.4}),
                CmdResult.fail(msg="No connection could be made"),
            )
            async with app.run_test(size=_SIZE) as pilot:
                # Arrange
                app.push_screen(PortPicker(test_port=answers))
                await pilot.pause()
                await pilot.click("#port-network")
                app.screen.query_one("#net-url", Input).value = URL
                screen = app.screen

                # Act
                await pilot.click("#net-test")
                answered = await until(pilot, lambda: "answers" in _note(screen))
                await pilot.pause(0.3)  # the button's active effect, before a second click
                await pilot.click("#net-test")
                failed = await until(pilot, lambda: "No connection" in _note(screen))

                # Assert
                assert answered, f"the first answer shows; the note read {_note(screen)!r}"
                assert failed, f"the failure's reason shows; the note read {_note(screen)!r}"
                assert answers.asked == [URL, URL], "each Test asked about the typed URL"

        _run(scenario)

    def test_with_no_test_port_there_is_no_test_button(self):
        """A dialog built without a host's test_port offers no Test it could not answer."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                # Act
                app.push_screen(PortPicker())
                await pilot.pause()

                # Assert
                assert not app.screen.query("#net-test"), "no Test button"

        _run(scenario)


class TestTheWizardsNetworkField:
    """New Config: the same Network Port... field gives the config its port."""

    def test_the_config_takes_the_typed_network_port(self):
        """A typed URL in place of the port list is the new config's port."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=(120, 50)) as pilot:
                # Arrange
                results: list = []
                app.push_screen(QuickSetup("Probe"), callback=results.append)
                await pilot.pause()
                app.screen.query_one("#qs-name", Input).value = "bench"
                await pilot.click("#qs-network")
                app.screen.query_one("#net-url", Input).value = URL

                # Act
                await pilot.click("#qs-connect")
                await pilot.pause()

                # Assert
                assert results and results[0] is not None, "the dialog submitted"
                assert results[0][2] == URL, f"the config's port is the URL; got {results[0]!r}"

        _run(scenario)
