"""Tests for the DEMO_VT100 interactive demo device.

Drives ``FakeSerialVT100`` directly (no miniterm): write keystroke bytes,
read the resulting frame, strip ANSI, and assert on the visible text. The
input-driven transitions (menu navigation, opening a screen, adjusting the
slider, the reboot confirm) are immediate and deterministic; the time-driven
animations (gauge wiggle, progress fill, boot scroll) are not asserted here.
"""

from __future__ import annotations

import re

from termapy.config import open_serial
from termapy.defaults import default_cfg
from termapy.demo_vt100 import FakeSerialVT100

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

_DOWN = b"\x1b[B"
_RIGHT = b"\x1b[C"


def _frame(fake: FakeSerialVT100) -> str:
    """Read one pending frame and return it with ANSI escapes stripped."""
    n = fake.in_waiting
    return _ANSI.sub("", fake.read(n).decode("utf-8", "replace")) if n else ""


def _open_item(fake: FakeSerialVT100, index: int) -> str:
    """From a fresh device, move down to *index* and open it. Returns its frame."""
    _frame(fake)  # consume the initial menu
    for _ in range(index):
        fake.write(_DOWN)
    fake.write(b"\r")
    return _frame(fake)


def test_initial_frame_is_menu():
    # Arrange
    fake = FakeSerialVT100()

    # Act
    text = _frame(fake)

    # Assert
    assert "Bassomatic v77" in text, "menu shows the device title"
    assert "> Status" in text, "first item is selected by default"
    assert "Reboot" in text, "all menu items render"


def test_down_moves_selection():
    # Arrange
    fake = FakeSerialVT100()
    _frame(fake)

    # Act
    fake.write(_DOWN)
    text = _frame(fake)

    # Assert
    assert "> Motor control" in text, "selection moved to the second item"
    assert "> Status" not in text, "the previous item is no longer selected"


def test_up_wraps_to_last_item():
    # Arrange
    fake = FakeSerialVT100()
    _frame(fake)

    # Act
    fake.write(b"k")  # vim-style up from the first item
    text = _frame(fake)

    # Assert
    assert "> Reboot" in text, "up from the first item wraps to the last"


def test_status_opens_gauges():
    # Act
    text = _open_item(FakeSerialVT100(), 0)

    # Assert
    assert "Temp" in text, "status shows the temperature gauge"
    assert "GPS: FIX" in text, "status shows device state"


def test_motor_slider_adjusts():
    # Arrange
    fake = FakeSerialVT100()
    opened = _open_item(fake, 1)

    # Act
    fake.write(_RIGHT)
    after = _frame(fake)

    # Assert
    assert "40%" in opened, "slider opens at the default value"
    assert "45%" in after, "Right arrow increases the slider value"


def test_calibration_shows_progress_bar():
    # Act
    text = _open_item(FakeSerialVT100(), 2)

    # Assert
    assert "Calibration" in text, "calibration screen title renders"
    assert "0%" in text, "progress starts at zero"


def test_network_shows_table():
    # Act
    text = _open_item(FakeSerialVT100(), 3)

    # Assert
    assert "eth0" in text, "network table lists interfaces"
    assert "DOWN" in text, "network table shows a down link"


def test_reboot_confirm_then_cancel():
    # Arrange
    fake = FakeSerialVT100()
    confirm = _open_item(fake, 4)

    # Act
    fake.write(b"n")  # decline
    text = _frame(fake)

    # Assert
    assert "Reboot the device now?" in confirm, "reboot asks for confirmation"
    assert "> Reboot" in text, "declining returns to the menu"


def test_reboot_yes_starts_boot():
    # Arrange
    fake = FakeSerialVT100()
    _open_item(fake, 4)

    # Act
    fake.write(b"y")  # confirm
    text = _frame(fake)

    # Assert
    assert "Rebooting" in text, "confirming starts the boot sequence"


def test_port_name_is_demo_vt100():
    # Arrange
    fake = FakeSerialVT100()

    # Assert
    actual = fake.name
    assert actual == "DEMO_VT100", "name mirrors the reserved port"


def test_open_serial_resolves_vt100_device():
    # Arrange
    cfg = default_cfg()
    cfg["serial"]["port"] = "DEMO_VT100"

    # Act
    port = open_serial(cfg)

    # Assert
    assert isinstance(port, FakeSerialVT100), "DEMO_VT100 resolves to the VT100 fake"
    port.close()
