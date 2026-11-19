"""The QuickSetup dialog: name, baud entry, and the tuple it dismisses with.

Pushed into a bare host app and driven with Pilot.  The port list comes
from pyserial's real ``comports()``, so nothing here depends on which
ports the machine has: submits go through Enter in the name field (the
Connect button is disabled on a machine with no ports) and the port
element of the result is only checked for type.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager

import pytest
from textual.app import App
from textual.widgets import Button, Input, OptionList

from termapy.dialogs.quick_setup import QuickSetup
from termapy.port_control import loopback_port_facts
from termapy.widgets import StrongCheckbox


class _Host(App):
    """Bare host: no widgets of its own, just a stage for modals."""


# The loopback's port name, read from the one place that owns it rather
# than spelled here: the literal is what the sw-loopback-labeled guard
# looks for, and this test asserts on a ROW ID, it opens no port.
_LOOPBACK = loopback_port_facts().device

# The dialog is taller than the 80x24 headless default; its buttons sit
# below the visible region there and a click on them is out of bounds.
_SIZE = (120, 50)


def _run(scenario) -> None:
    asyncio.run(scenario())


async def _open(app: App, pilot):
    """Push the dialog and return it with the list its dismiss value lands in."""
    results: list[tuple | None] = []
    app.push_screen(QuickSetup("Probe"), callback=results.append)
    await pilot.pause()
    screen = app.screen
    assert isinstance(screen, QuickSetup), "the dialog is on top"
    return screen, results


async def _submit_name(pilot, screen: QuickSetup, name: str) -> None:
    """Type ``name`` and press Enter in the name field."""
    name_input = screen.query_one("#qs-name", Input)
    name_input.value = name
    name_input.focus()
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()


class TestSubmit:
    def test_enter_returns_connect_with_the_standard_baud(self, tmp_path):
        """Enter in the name field dismisses with ("connect", name, port, 115200, False, False)."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                screen, results = await _open(app, pilot)
                await _submit_name(pilot, screen, "probe_dev")
                assert len(results) == 1 and results[0] is not None, "dismissed once"
                action, name, port, baud, custom, add_icon = results[0]
                assert (action, name, baud, custom, add_icon) == (
                    "connect",
                    "probe_dev",
                    115200,
                    False,
                    False,
                )
                assert isinstance(port, str), "whatever this machine has, or empty"

        _run(scenario)

    def test_name_is_reduced_to_its_stem(self, tmp_path):
        """A name typed with an extension or path comes back as the bare stem."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                screen, results = await _open(app, pilot)
                await _submit_name(pilot, screen, "my_device.cfg")
                assert results[0] is not None and results[0][1] == "my_device"

        _run(scenario)

    def test_submit_needs_a_name(self, tmp_path):
        """Enter on an empty name neither dismisses nor closes the dialog."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                screen, results = await _open(app, pilot)
                await _submit_name(pilot, screen, "   ")
                assert results == [], "not dismissed"
                assert app.screen is screen, "still open"

        _run(scenario)

    def test_a_picked_standard_baud_comes_back(self, tmp_path):
        """Highlighting another standard rate in the list returns that rate."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                screen, results = await _open(app, pilot)
                baud_list = screen.query_one("#qs-baud-list", OptionList)
                baud_list.highlighted = screen._COMMON_BAUDS.index(9600)
                await _submit_name(pilot, screen, "slow")
                assert results[0] is not None and results[0][3:5] == (9600, False)

        _run(scenario)

    def test_cancel_and_escape_dismiss_none(self, tmp_path):
        """Cancel and Escape both dismiss with None."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                _, results = await _open(app, pilot)
                await pilot.click("#qs-cancel")
                await pilot.pause()
                _, results_escape = await _open(app, pilot)
                await pilot.press("escape")
                await pilot.pause()
                assert (results, results_escape) == ([None], [None])

        _run(scenario)


def _focused_id(app: App) -> str | None:
    """The id of the widget holding focus on the top screen."""
    widget = app.screen.focused
    return widget.id if widget is not None else None


class TestNameFocus:
    """The cursor goes to the name box whenever the name is what the dialog still needs."""

    @pytest.mark.parametrize("button", ["#qs-connect", "#qs-advanced"])
    def test_a_missing_name_puts_the_cursor_in_the_name(self, button):
        """A click asking for the name warns and leaves focus in the name box, not on the list or button."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                # Arrange
                screen, results = await _open(app, pilot)
                screen.query_one("#qs-port-list", OptionList).focus()
                await pilot.pause()

                # Act
                await pilot.click(button)
                await pilot.pause()

                # Assert
                notes = [str(note.message) for note in app._notifications]
                assert (results, notes) == ([], ["Enter a config name"]), "warned, not dismissed"
                assert _focused_id(app) == "qs-name", "typing goes into the name now"

        _run(scenario)

    @pytest.mark.parametrize(
        ("list_id", "offset"), [("#qs-port-list", (5, 3)), ("#qs-baud-list", (5, 1))]
    )
    def test_a_pick_with_no_name_moves_the_cursor_to_the_name(self, list_id, offset):
        """Clicking a row with the name empty keeps the row and puts the cursor in the name box."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                # Arrange
                screen, _ = await _open(app, pilot)
                option_list = screen.query_one(list_id, OptionList)

                # Act
                await pilot.click(list_id, offset=offset)
                await pilot.pause()

                # Assert
                assert _focused_id(app) == "qs-name", "the name is what is owed"
                assert option_list.highlighted is not None, "the picked row stays picked"

        _run(scenario)

    def test_a_pick_after_naming_stays_in_the_list(self):
        """With a name typed, clicking a port leaves focus in the list: nothing is owed."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                # Arrange
                screen, _ = await _open(app, pilot)
                screen.query_one("#qs-name", Input).value = "bench"

                # Act
                await pilot.click("#qs-port-list", offset=(5, 3))
                await pilot.pause()

                # Assert
                assert _focused_id(app) == "qs-port-list", (
                    "the list keeps the focus it was clicked into"
                )

        _run(scenario)

    def test_arrow_keys_stay_in_the_list(self):
        """Down in the port list only highlights, so a keyboard user can still move through it."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                # Arrange
                screen, _ = await _open(app, pilot)
                screen.query_one("#qs-port-list", OptionList).focus()
                await pilot.pause()

                # Act
                await pilot.press("down")
                await pilot.pause()

                # Assert
                assert _focused_id(app) == "qs-port-list", "a highlight is not a pick"

        _run(scenario)


class TestCustomBaud:
    def test_the_toggle_swaps_list_for_input_and_back(self, tmp_path):
        """The baud button flips between the standard list and a custom entry field, relabeling itself."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                screen, _ = await _open(app, pilot)
                button = screen.query_one("#qs-standard-baud", Button)
                baud_list = screen.query_one("#qs-baud-list", OptionList)
                baud_input = screen.query_one("#qs-baud-input", Input)

                # Act
                await pilot.click("#qs-standard-baud")
                await pilot.pause()
                custom_state = (
                    str(button.label),
                    baud_list.has_class("qs-hidden"),
                    baud_input.has_class("qs-hidden"),
                )
                # Textual drops a click on a button still showing its 0.2 s
                # active effect; wait it out before clicking the same one again.
                for _ in range(50):
                    if not button.has_class("-active"):
                        break
                    await pilot.pause(0.02)
                await pilot.click("#qs-standard-baud")
                await pilot.pause()
                standard_state = (
                    str(button.label),
                    baud_list.has_class("qs-hidden"),
                    baud_input.has_class("qs-hidden"),
                )

                # Assert
                assert custom_state == ("Standard Rates", True, False), "list hidden, input shown"
                assert standard_state == ("Custom Rate...", False, True), "and back"

        _run(scenario)

    def test_baud_has_a_heading_and_its_toggle_sits_under_it(self):
        """Baud Rate is a heading like Config Name and Serial Port, and the toggle sits under the baud boxes, not in the heading's place."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                # Arrange / Act
                screen, _ = await _open(app, pilot)
                children = list(screen.query_one("#qs-dialog").children)
                ids = [child.id for child in children]
                headings = [
                    str(child.render()) for child in children if child.has_class("qs-label")
                ]

                # Assert
                assert headings == ["Config Name:", "Serial Port:", "Baud Rate:"], "three alike"
                assert ids.index("qs-standard-baud") > ids.index("qs-baud-input"), (
                    "the toggle comes after the boxes it swaps"
                )

        _run(scenario)

    @pytest.mark.rule("dialog-focus-visible")
    def test_the_focused_box_is_the_one_that_looks_it(self):
        """Of the name box and the two lists, only the focused one wears the bright border."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                # Arrange
                screen, _ = await _open(app, pilot)
                screen.query_one("#qs-port-list", OptionList).focus()
                await pilot.pause()

                # Act
                borders = {
                    name: screen.query_one(f"#{name}").styles.border_top
                    for name in ("qs-name", "qs-port-list", "qs-baud-list")
                }

                # Assert
                assert borders["qs-name"] == borders["qs-baud-list"], "the two at rest look alike"
                assert borders["qs-port-list"] != borders["qs-name"], "the focused one stands out"

        _run(scenario)

    @pytest.mark.rule("baud-standard-unless-custom")
    def test_custom_entry_is_validated(self, tmp_path):
        """An empty, non-numeric, or sub-300 custom rate keeps the dialog open; a valid one comes back flagged custom."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                screen, results = await _open(app, pilot)
                await pilot.click("#qs-standard-baud")
                await pilot.pause()
                baud_input = screen.query_one("#qs-baud-input", Input)

                # Act / Assert -- each rejected entry leaves the dialog open
                for bad in ("", "fast", "100"):
                    baud_input.value = bad
                    await _submit_name(pilot, screen, "custom_dev")
                    assert results == [], f"{bad!r} must not submit"
                baud_input.value = "250000"
                await _submit_name(pilot, screen, "custom_dev")
                assert results[0] is not None and results[0][3:5] == (250000, True), (
                    "custom rate, flagged custom"
                )

        _run(scenario)


class TestAdvanced:
    def test_advanced_returns_the_advanced_action_with_the_icon_choice(self, tmp_path):
        """Advanced dismisses with action "advanced" and carries the add-icon checkbox through."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                screen, results = await _open(app, pilot)
                screen.query_one("#qs-name", Input).value = "adv_dev"
                screen.query_one("#qs-add-icon", StrongCheckbox).value = True
                await pilot.pause()

                # Act
                await pilot.click("#qs-advanced")
                await pilot.pause()

                # Assert
                assert len(results) == 1 and results[0] is not None, "dismissed once"
                action, name, _, baud, custom, add_icon = results[0]
                assert (action, name, baud, custom, add_icon) == (
                    "advanced",
                    "adv_dev",
                    115200,
                    False,
                    True,
                )

        _run(scenario)

    def test_advanced_needs_a_name(self, tmp_path):
        """Advanced without a name neither dismisses nor closes the dialog."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                screen, results = await _open(app, pilot)
                await pilot.click("#qs-advanced")
                await pilot.pause()
                assert results == [] and app.screen is screen

        _run(scenario)


@contextmanager
def _no_ports():
    """``comports()`` finds nothing, as on a machine with no adapter attached.

    QuickSetup calls ``comports`` itself and takes no ``source=``, so
    this is the documented exception: a test ABOUT enumeration replaces
    that layer wholesale (see tests/test_port_noninvasive.py, which
    patches it for the same reason).  Without it the no-hardware branch
    cannot run on a developer machine with adapters plugged in -- which
    is exactly how the loopback bug survived: the old test asserted the
    wrong behavior in a branch that never executed here.
    """
    import serial.tools.list_ports as list_ports

    real = list_ports.comports
    # A test double for a precisely typed function.
    list_ports.comports = lambda *a, **kw: []  # ty: ignore[invalid-assignment]
    try:
        yield
    finally:
        list_ports.comports = real


class TestPortList:
    def test_the_list_offers_the_loopback_when_no_hardware_is_attached(self, tmp_path):
        """With no adapter, the loopback is highlighted and Connect is live.

        ``_populate_port_option_list`` always appends ``loop://``, a real
        and selectable port, so the list is never empty.  Every guard
        here used to ask ``if self._ports`` -- the raw comports() result,
        which excludes it -- leaving that row on screen, unhighlighted,
        beside a disabled "No Ports" button: the list said pick this and
        the button said there was nothing to pick.
        """

        async def scenario():
            app = _Host()
            with _no_ports():
                async with app.run_test(size=_SIZE) as pilot:
                    # Arrange / Act
                    screen, _ = await _open(app, pilot)
                    await pilot.pause()
                    connect = screen.query_one("#qs-connect", Button)
                    port_list = screen.query_one("#qs-port-list", OptionList)
                    row = port_list.get_option_at_index(2)

                    # Assert
                    assert screen._ports == [], "no hardware ports, by construction"
                    assert row.id == _LOOPBACK and not row.disabled, (
                        "the loopback is a real row the user can choose"
                    )
                    assert port_list.highlighted == 2, (
                        "and it is highlighted, so Enter and Connect act on something"
                    )
                    assert (str(connect.label), connect.disabled) == ("Connect", False), (
                        "the button agrees with the list: there IS a port to connect to"
                    )

        _run(scenario)

    def test_submitting_with_no_hardware_yields_the_loopback(self, tmp_path):
        """The config a user gets on a bare machine names loop://, not an empty port."""

        async def scenario():
            app = _Host()
            with _no_ports():
                async with app.run_test(size=_SIZE) as pilot:
                    # Arrange
                    screen, results = await _open(app, pilot)
                    screen.query_one("#qs-name", Input).value = "bench"
                    await pilot.pause()

                    # Act
                    await pilot.click("#qs-connect")
                    await pilot.pause()

                    # Assert
                    assert results and results[0] is not None, "the dialog submitted"
                    assert results[0][2] == _LOOPBACK, (
                        f"the port is the loopback the user picked, not blank; got {results[0]!r}"
                    )

        _run(scenario)

    def test_with_hardware_the_first_real_port_is_highlighted(self, tmp_path):
        """With adapters attached the list highlights a hardware port and Connect is live."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                # Arrange / Act
                screen, _ = await _open(app, pilot)
                await pilot.pause()
                connect = screen.query_one("#qs-connect", Button)
                port_list = screen.query_one("#qs-port-list", OptionList)

                # Assert -- true whatever this machine has: the list always
                # offers the loopback, so there is always a row to pick.
                assert port_list.highlighted == 2, "past the header and separator rows"
                assert (str(connect.label), connect.disabled) == ("Connect", False), (
                    "Connect is live because the list always has at least the loopback"
                )

        _run(scenario)
