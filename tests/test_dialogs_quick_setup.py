"""The QuickSetup dialog: name, baud entry, and the tuple it dismisses with.

Pushed into a bare host app and driven with Pilot.  The port list comes
from pyserial's real ``comports()``, so nothing here depends on which
ports the machine has: submits go through Enter in the name field (the
Connect button is disabled on a machine with no ports) and the port
element of the result is only checked for type.
"""

from __future__ import annotations

import asyncio

from textual.app import App
from textual.widgets import Button, Input, OptionList

from termapy.dialogs.quick_setup import QuickSetup
from termapy.widgets import StrongCheckbox


class _Host(App):
    """Bare host: no widgets of its own, just a stage for modals."""


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
                assert custom_state == ("Custom Baud Rate", True, False), "list hidden, input shown"
                assert standard_state == ("Standard Baud Rates", False, True), "and back"

        _run(scenario)

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


class TestPortList:
    def test_connect_button_tells_the_truth_about_ports(self, tmp_path):
        """With ports the list highlights the first real row and Connect is live; without, Connect reads No Ports and is disabled."""

        async def scenario():
            app = _Host()
            async with app.run_test(size=_SIZE) as pilot:
                screen, _ = await _open(app, pilot)
                await pilot.pause()
                connect = screen.query_one("#qs-connect", Button)
                port_list = screen.query_one("#qs-port-list", OptionList)
                if screen._ports:
                    assert port_list.highlighted == 2, "past the header and separator rows"
                    assert (str(connect.label), connect.disabled) == ("Connect", False)
                else:
                    assert (str(connect.label), connect.disabled) == ("No Ports", True)

        _run(scenario)
