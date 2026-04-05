"""Tests for FakeSerial virtual filesystem — AT+FS commands and VFS state."""

from __future__ import annotations

import time

import pytest

from termapy.demo import FakeSerial


@pytest.fixture
def dev() -> FakeSerial:
    """Create a FakeSerial instance for testing."""
    fs = FakeSerial(baudrate=9600)
    fs.timeout = 0.5
    return fs


def _send_cmd(dev: FakeSerial, cmd: str) -> str:
    """Send an ASCII command and return the text response."""
    dev.write(cmd.encode() + b"\r")
    time.sleep(0.01)
    return dev.read(4096).decode(errors="replace")


# -- Default VFS state -----------------------------------------------------


class TestDefaultVfs:
    def test_default_files_present(self, dev: FakeSerial) -> None:
        """Fresh FakeSerial has pre-loaded VFS files."""
        vfs = dev.vfs
        assert "device_log.txt" in vfs  # text log file
        assert "config.dat" in vfs  # binary config
        assert "firmware_v1.bin" in vfs  # firmware blob

    def test_default_file_contents(self, dev: FakeSerial) -> None:
        """Default VFS files have expected deterministic content."""
        vfs = dev.vfs
        assert vfs["config.dat"] == bytes(range(64))  # 64 bytes 0-63
        assert vfs["firmware_v1.bin"] == bytes(i & 0xFF for i in range(2048))  # 2K
        assert b"Boot OK" in vfs["device_log.txt"]  # contains boot message

    def test_vfs_isolation(self) -> None:
        """Each FakeSerial instance gets its own VFS copy."""
        # Arrange
        dev1 = FakeSerial()
        dev2 = FakeSerial()

        # Act
        dev1._vfs["new_file.txt"] = b"hello"

        # Assert
        assert "new_file.txt" in dev1.vfs  # modified instance has file
        assert "new_file.txt" not in dev2.vfs  # other instance unaffected

    def test_vfs_property_returns_copy(self, dev: FakeSerial) -> None:
        """The vfs property returns a copy, not a reference."""
        # Arrange
        snapshot = dev.vfs

        # Act
        snapshot["injected.txt"] = b"sneaky"

        # Assert
        assert "injected.txt" not in dev.vfs  # original unmodified


# -- AT+FS.LIST -----------------------------------------------------------


class TestFsList:
    def test_list_shows_all_files(self, dev: FakeSerial) -> None:
        """AT+FS.LIST shows all default files."""
        actual = _send_cmd(dev, "AT+FS.LIST")
        assert "device_log.txt" in actual  # text file listed
        assert "config.dat" in actual  # config listed
        assert "firmware_v1.bin" in actual  # firmware listed

    def test_list_shows_sizes(self, dev: FakeSerial) -> None:
        """AT+FS.LIST shows byte counts."""
        actual = _send_cmd(dev, "AT+FS.LIST")
        assert "64 bytes" in actual  # config.dat size
        assert "2048 bytes" in actual  # firmware size

    def test_list_empty_vfs(self, dev: FakeSerial) -> None:
        """AT+FS.LIST on empty VFS shows (empty)."""
        # Arrange
        dev._vfs.clear()

        # Act
        actual = _send_cmd(dev, "AT+FS.LIST")

        # Assert
        assert "(empty)" in actual  # empty indicator

    def test_list_sorted(self, dev: FakeSerial) -> None:
        """AT+FS.LIST returns files in sorted order."""
        actual = _send_cmd(dev, "AT+FS.LIST")
        lines = [l.strip() for l in actual.strip().split("\r\n") if l.strip()]
        names = [l.split()[0] for l in lines]
        assert names == sorted(names)  # alphabetical order


# -- AT+FS.INFO -----------------------------------------------------------


class TestFsInfo:
    def test_info_count(self, dev: FakeSerial) -> None:
        """AT+FS.INFO shows correct file count."""
        actual = _send_cmd(dev, "AT+FS.INFO")
        assert "Files: 3" in actual  # 3 default files

    def test_info_total_size(self, dev: FakeSerial) -> None:
        """AT+FS.INFO shows correct total size."""
        actual = _send_cmd(dev, "AT+FS.INFO")
        expected_total = len(dev.vfs["device_log.txt"]) + 64 + 2048
        assert str(expected_total) in actual  # total bytes


# -- AT+FS.DELETE ----------------------------------------------------------


class TestFsDelete:
    def test_delete_removes_file(self, dev: FakeSerial) -> None:
        """AT+FS.DELETE removes a file from VFS."""
        actual = _send_cmd(dev, "AT+FS.DELETE config.dat")
        assert "OK" in actual  # success
        assert "config.dat" not in dev.vfs  # removed

    def test_delete_not_found(self, dev: FakeSerial) -> None:
        """AT+FS.DELETE with unknown file returns error."""
        actual = _send_cmd(dev, "AT+FS.DELETE nope.bin")
        assert "ERROR" in actual  # error
        assert "nope.bin" in actual  # includes filename

    def test_delete_no_arg(self, dev: FakeSerial) -> None:
        """AT+FS.DELETE with no filename returns usage error."""
        actual = _send_cmd(dev, "AT+FS.DELETE")
        assert "ERROR" in actual  # error

    def test_delete_preserves_case(self, dev: FakeSerial) -> None:
        """AT+FS.DELETE uses exact filename (case-sensitive)."""
        actual = _send_cmd(dev, "AT+FS.DELETE Config.dat")
        assert "ERROR" in actual  # wrong case, not found


# -- AT+FS unknown ---------------------------------------------------------


class TestFsUnknown:
    def test_unknown_fs_command(self, dev: FakeSerial) -> None:
        """Unknown AT+FS subcommand returns error."""
        actual = _send_cmd(dev, "AT+FS.RENAME foo bar")
        assert "ERROR" in actual  # not a valid command
