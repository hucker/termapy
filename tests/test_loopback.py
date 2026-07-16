"""The pyserial loopback (loop://) as a selectable virtual port.

loop:// echoes writes straight back -- a hardware-free way to exercise the
real serial read/write path (CI or by hand).  It's offered as a picker row
with honest facts (name + description, no invented VID/PID/serial) and
opens through the existing serial_for_url path.  Unlike DEMO it does not
simulate a *responding* device -- it's the raw round-trip.
"""

from __future__ import annotations

import asyncio

from textual.app import App
from textual.widgets import OptionList

from termapy.dialogs import PortPicker
from termapy.port_control import LOOPBACK_PORT, loopback_port_facts


def test_loopback_facts_are_honest():
    facts = loopback_port_facts()
    assert facts.device == "loop://", "device is the real pyserial URL"
    assert "loopback" in facts.description.lower(), "described as a loopback"
    # No invented identity -- a loopback has no USB attributes.
    assert facts.vid_pid is None, "no fake VID:PID"
    assert facts.serial is None, "no fake serial number"
    assert facts.manufacturer is None, "no fake manufacturer"
    assert facts.model is None, "no fake chip model"


def test_loopback_opens_and_echoes():
    from termapy.config import open_serial

    cfg = {
        "serial": {
            "port": LOOPBACK_PORT, "baud_rate": 115200, "byte_size": 8,
            "parity": "N", "stop_bits": 1, "flow_control": "none",
        }
    }
    s = open_serial(cfg)
    try:
        s.write(b"PING")
        assert s.read(4) == b"PING", "loopback echoes writes back"
    finally:
        s.close()


class _Host(App):
    """Bare host: a stage for pushing the picker modal."""


def test_picker_offers_loopback_and_selects_it():
    async def scenario():
        app = _Host()
        async with app.run_test() as pilot:
            results: list[str | None] = []
            app.push_screen(PortPicker(), callback=results.append)
            await pilot.pause()
            ol = app.screen.query_one("#port-list", OptionList)
            loop_idx = next(
                (
                    i
                    for i in range(ol.option_count)
                    if ol.get_option_at_index(i).id == "loop://"
                ),
                None,
            )
            assert loop_idx is not None, "loopback row offered in the picker"
            ol.focus()
            ol.highlighted = loop_idx
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert results == ["loop://"], "selecting it returns the loop:// spec"

    asyncio.run(scenario())
