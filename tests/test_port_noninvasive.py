"""K1 regression: resolution and plain enumeration must never open a port.

The in-use probe (``_check_in_use``) opens each port, and pyserial asserts
DTR/RTS on open -- which resets Arduino/ESP32 auto-reset boards.  So
``resolve_port`` / ``resolve_port_trace`` / ``list_ports`` / the ``--ports``
table must gather with ``fast=True`` (identity fields only) and never reach
the probe.  Only explicit monitoring surfaces (``fast=False``) may probe.

These tests fake ``comports`` so a fixed two-port bus is enumerated, and
replace the probe functions with recorders -- a real recorder, not a mock
framework -- so a probe call is both observable and cannot open the real
COM7 on the dev machine.
"""

from __future__ import annotations

import pytest

from termapy import port_control


class _FakePort:
    """Minimal stand-in for pyserial ListPortInfo (identity fields only)."""

    def __init__(self, device, serial_number=None, vid=None, pid=None):
        self.device = device
        self.description = f"{device} device"
        self.manufacturer = "TestMfg"
        self.product = None
        self.serial_number = serial_number
        self.location = None
        self.interface = None
        self.vid = vid
        self.pid = pid


@pytest.fixture
def probed(monkeypatch):
    """Enumerate two fake ports; record any probe call, open nothing real.

    Patches ``comports`` rather than passing ``source=``.  That is
    deliberate and must stay: a substitute fleet replaces the whole
    enumeration layer, so ``_check_in_use`` and ``_check_permissions``
    would never run and these tests would prove nothing about probing.
    Faking the OS boundary is the only way to watch what happens above it.
    """
    ports = [
        _FakePort("COM3", serial_number="A1B2C3D4", vid=0x0403, pid=0x6001),
        _FakePort("COM7", serial_number="BG03U7VTA", vid=0x0403, pid=0x6001),
    ]
    monkeypatch.setattr("serial.tools.list_ports.comports", lambda: list(ports))
    calls: list[str] = []
    # Recorders stand in for the real probes so a call is observable AND no
    # real port (COM7 exists on this machine) is opened during the test.
    monkeypatch.setattr(
        port_control,
        "_check_in_use",
        lambda device, connected_port="": (calls.append(device) or "no"),
    )
    monkeypatch.setattr(port_control, "_check_permissions", lambda device: "ok")
    return calls


class TestResolutionNeverProbes:
    def test_resolve_by_name_opens_no_port(self, probed):
        # Act
        actual = port_control.resolve_port("COM7")

        # Assert
        assert actual == "COM7", f"resolves the literal name, got {actual!r}"
        assert probed == [], f"resolve_port must not probe any port; probed {probed}"

    def test_resolve_by_serial_opens_no_port(self, probed):
        # Act
        actual = port_control.resolve_port("A1B2C3D4")

        # Assert
        assert actual == "COM3", f"resolves the SN to its device, got {actual!r}"
        assert probed == [], f"SN resolution must not probe; probed {probed}"

    def test_resolve_trace_opens_no_port(self, probed):
        # Act
        port_control.resolve_port_trace("A1B2C3D4|COM7")

        # Assert
        assert probed == [], f"resolution trace must not probe; probed {probed}"

    def test_list_ports_opens_no_port(self, probed):
        # Act
        port_control.list_ports()

        # Assert
        assert probed == [], f"/port.list table must not probe; probed {probed}"


class TestExplicitSurfaceStillProbes:
    def test_fast_gather_skips_probe(self, probed):
        # Act
        facts = port_control.gather_chip_facts("COM7", fast=True)

        # Assert
        assert facts is not None and facts.in_use is None, (
            "fast gather returns the port with in_use unset"
        )
        assert probed == [], f"fast gather must not probe; probed {probed}"

    def test_explicit_gather_still_probes(self, probed):
        # Act -- an explicit single-port lookup (fast defaults False) is a
        # legitimate monitoring surface and must still probe the target.
        facts = port_control.gather_chip_facts("COM7")

        # Assert
        assert facts is not None, "explicit gather finds the port"
        actual = probed
        expected = ["COM7"]
        assert actual == expected, f"explicit gather probes only the target, got {actual}"
