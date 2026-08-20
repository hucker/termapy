"""K1 regression: resolution and plain enumeration must never open a port.

The in-use probe (``_check_in_use``) opens each port, and pyserial asserts
DTR/RTS on open -- which resets Arduino/ESP32 auto-reset boards.  So
``resolve_port`` / ``resolve_port_trace`` / ``list_ports`` / the ``--ports``
table must gather with ``fast=True`` and never reach the probe.  Only
explicit monitoring surfaces (``fast=False``) may probe.

The invariant is **opens no port**, not "identity fields only".  Driver and
location come from sysfs / the registry, which open nothing, and the
surfaces that display those columns ask for them via ``enrich=True`` while
staying fast.  ``TestEnrichmentIsSeparateFromProbing`` pins that split --
conflating the two is what emptied those columns for eight weeks.

These tests fake ``comports`` so a fixed two-port bus is enumerated, and
replace the probe and lookup functions with recorders -- real recorders, not
a mock framework -- so a call is both observable and cannot touch the real
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


@pytest.fixture
def enriched(monkeypatch, probed):
    """Record the platform-metadata lookups; run neither of them.

    Same recorder pattern as ``probed``, and patching is what makes the
    call observable at all -- the real lookups read this machine's sysfs
    or registry, so what they return depends on the host, while *whether
    they ran* is the property under test.  Records ``(function, device)``
    because the Linux and Windows lookups are called back to back for the
    same port and only one of them does anything on a given host.
    """
    calls: list[tuple[str, str]] = []

    def _linux(facts, device):
        calls.append(("linux", device))

    def _windows(facts, device):
        calls.append(("windows", device))

    monkeypatch.setattr(port_control, "_gather_linux_extras", _linux)
    monkeypatch.setattr(port_control, "_gather_windows_extras", _windows)
    return calls


def _devices(calls) -> list[str]:
    """The ports an ``enriched`` recorder saw, once each."""
    return sorted({device for _, device in calls})


class TestEnrichmentIsSeparateFromProbing:
    """Driver and location are sysfs / registry reads -- they open nothing.

    They were nonetheless gated behind ``fast`` alongside the port-opening
    probe from 2026-07-04 (e07cd27, the K1 fix) until this commit, which
    silently emptied the LOCATION and DRIVER columns of the ``--ports``
    table, ``/port.list`` and the port picker: all three display those
    columns and all three gather fast.  These tests pin the two costs as
    separate knobs so they cannot be re-fused.
    """

    def test_the_listing_enriches_without_probing(self, probed, enriched):
        # Act
        port_control.list_ports()

        # Assert -- both halves matter: the columns get filled, and the
        # K1 invariant that made them fast in the first place still holds.
        actual = _devices(enriched)
        expected = ["COM3", "COM7"]
        assert actual == expected, (
            f"/port.list shows LOCATION and DRIVER, so it must look them up; "
            f"enriched {actual}"
        )
        assert probed == [], f"and it still must not open a port; probed {probed}"

    def test_the_ports_table_enriches_without_probing(self, probed, enriched):
        # Arrange
        import argparse
        import io
        from contextlib import redirect_stdout

        from termapy import cli_flags

        args = argparse.Namespace(
            ports="*", json=False, vid=None, pid=None, mfg=None, sn=None
        )

        # Act
        with pytest.raises(SystemExit), redirect_stdout(io.StringIO()):
            cli_flags.run_ports(args)

        # Assert
        actual = _devices(enriched)
        expected = ["COM3", "COM7"]
        assert actual == expected, f"--ports fills its own columns; enriched {actual}"
        assert probed == [], f"--ports still opens nothing; probed {probed}"

    def test_resolution_does_not_enrich(self, enriched):
        # Act
        port_control.resolve_port("COM7")

        # Assert -- resolution runs on every connect and every tick of the
        # 2.5 s reconnect loop, and reads only device + serial.  Paying for
        # a registry walk there would be a cost with no reader.
        assert enriched == [], (
            f"resolution reads identity only and must not look up metadata; "
            f"enriched {_devices(enriched)}"
        )

    def test_a_plain_fast_gather_does_not_enrich(self, enriched):
        # Act -- the default: what --watch polls with.
        port_control._gather_all_chip_facts(fast=True)

        # Assert
        assert enriched == [], (
            f"enrichment is opt-in, so a timer-driven poll doesn't pay for it; "
            f"enriched {_devices(enriched)}"
        )

    def test_the_probing_path_still_enriches(self, enriched):
        # Act -- fast=False means the caller already accepted the far larger
        # probe cost, so metadata comes along as it always has.
        port_control.gather_chip_facts("COM7")

        # Assert
        actual = _devices(enriched)
        expected = ["COM7"]
        assert actual == expected, (
            f"an explicit gather still returns a fully populated record; got {actual}"
        )
