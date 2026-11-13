"""Quick open: ``termapy PORT [SETTING ...]`` opens the port's own config, made at first use and kept.

``config.quick_config`` is the config (one per port, ``quick-<port>``),
``cli_flags.run_quick`` decides that a positional is a port, and
``entry._resolve_port_positional`` hands it the positionals no config
claims.  The in-process tests point the config folder at ``tmp_path``
through ``TERMAPY_CFG_DIR`` and hand a port list in through ``source=``;
the subprocess tests at the end drive ``main()`` the way a user types it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

import pytest

from termapy.cli_flags import run_quick
from termapy.config import quick_config, quick_config_path
from termapy.port_control import ChipFacts


class _UsageError(Exception):
    """What the test's ``usage_error`` raises instead of exiting 2."""


def _usage_error(message: str) -> NoReturn:
    """Stand in for ``parser.error``: raise, so the test sees the message."""
    raise _UsageError(message)


def _fleet(*ports: tuple[str, str]) -> list[ChipFacts]:
    """A port list of ``(device, serial number)`` pairs."""
    return [ChipFacts(device=device, serial=serial) for device, serial in ports]


@pytest.fixture
def cfg_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The config folder for one test: quick configs land here, as --cfg-dir would put them."""
    monkeypatch.setenv("TERMAPY_CFG_DIR", str(tmp_path))
    return tmp_path


def _saved(path: Path) -> dict:
    """The config file as written."""
    return json.loads(path.read_text(encoding="utf-8"))


class TestQuickConfigPath:
    """A quick config is named ``quick-`` and the port, with what a file name cannot hold made a dash."""

    @pytest.mark.parametrize(
        ("port", "expected"),
        [
            ("COM3", "quick-COM3"),
            ("socket://localhost:7000", "quick-socket-localhost-7000"),
            ("/dev/ttyUSB0", "quick-dev-ttyUSB0"),
            ("/dev/tty.usbserial-A10K", "quick-dev-tty.usbserial-A10K"),
            ("58E6C5716A840000", "quick-58E6C5716A840000"),
        ],
    )
    def test_the_name_follows_the_port(self, cfg_root, port, expected):
        """Each port spelling gets a name a folder can have, in the config folder's <name>/<name>.cfg layout."""
        # Act
        actual = quick_config_path(port)

        # Assert
        assert actual == cfg_root / expected / f"{expected}.cfg"


class TestQuickConfig:
    """``config.quick_config``: created once, laid over by later settings, left alone without them."""

    def test_the_first_open_creates_it_connecting_on_load(self, cfg_root):
        """A new port's config has the settings typed after it and opens the port when it loads."""
        # Act
        path, verb = quick_config("COM3", "9600 E71")

        # Assert
        serial_cfg = _saved(path)["serial"]
        assert verb == "Created"
        assert path == cfg_root / "quick-COM3" / "quick-COM3.cfg"
        assert (serial_cfg["port"], serial_cfg["baud_rate"]) == ("COM3", 9600)
        assert (serial_cfg["parity"], serial_cfg["byte_size"]) == ("E", 7)
        assert _saved(path)["auto_connect"] is True, "typing a port asks to connect to it"

    def test_a_second_open_without_settings_changes_nothing(self, cfg_root):
        """Twice in a row: the second open is 'Using' and the file stays byte for byte as it was."""
        # Arrange
        path, _ = quick_config("COM3", "9600")
        before = path.read_bytes()

        # Act
        again, verb = quick_config("COM3", "")

        # Assert
        assert (again, verb) == (path, "Using")
        assert path.read_bytes() == before, "an open with no settings must not rewrite the config"

    @pytest.mark.rule("config-created-once")
    def test_later_settings_are_laid_over_and_kept(self, cfg_root):
        """A new baud replaces the saved one; the frame given the first time stays."""
        # Arrange
        quick_config("COM3", "9600 E71")

        # Act
        path, verb = quick_config("COM3", "19200")

        # Assert
        serial_cfg = _saved(path)["serial"]
        assert verb == "Updated"
        assert serial_cfg["baud_rate"] == 19200, (
            "the new rate is what the port opens with next time"
        )
        assert serial_cfg["parity"] == "E", "a setting not typed again is kept"

    def test_two_ports_share_nothing(self, cfg_root):
        """Two ports in one session folder: two configs, so two termapys on two ports never share a file."""
        # Act
        first, _ = quick_config("COM3", "9600")
        second, _ = quick_config("COM5", "115200")

        # Assert
        assert first.parent != second.parent, "each port has its own folder"
        assert (_saved(first)["serial"]["baud_rate"], _saved(second)["serial"]["baud_rate"]) == (
            9600,
            115200,
        )

    def test_many_opens_stay_one_config(self, cfg_root):
        """A sustained run of opens with changing settings leaves one folder holding one config."""
        # Act
        for index in range(30):
            quick_config("COM3", "9600" if index % 2 else "115200")

        # Assert
        folders = sorted(child.name for child in cfg_root.iterdir())
        assert folders == ["quick-COM3"], "every open reuses the port's one config"
        assert len(list((cfg_root / "quick-COM3").glob("*.cfg"))) == 1

    def test_an_update_keeps_an_env_template_unexpanded(self, cfg_root):
        """Settings laid over a config that names $(env.X) save the template, never its value."""
        # Arrange -- the user put a template in the file by hand
        path, _ = quick_config("COM3", "")
        data = _saved(path)
        data["title"] = "$(env.TERMAPY_QUICK_TEST_TITLE)"
        path.write_text(json.dumps(data), encoding="utf-8")

        # Act
        quick_config("COM3", "9600")

        # Assert
        assert _saved(path)["title"] == "$(env.TERMAPY_QUICK_TEST_TITLE)", (
            "saving must not bake an expanded value into the file"
        )

    def test_a_word_that_is_no_setting_is_refused(self, cfg_root):
        """An unknown word names itself and the settings there are, and writes nothing."""
        # Act
        with pytest.raises(ValueError, match="Unknown setting: fast") as raised:
            quick_config("COM3", "fast")

        # Assert
        assert "cr|lf|crlf" in str(raised.value), "the refusal lists what a setting can be"
        assert not quick_config_path("COM3").exists(), "a refused open creates no config"

    def test_a_nonstandard_baud_needs_custom(self, cfg_root):
        """A rate off the standard list is refused unless custom=true asks for it, as /port.connect does."""
        # Act
        with pytest.raises(ValueError, match="9460"):
            quick_config("COM3", "9460")
        path, _ = quick_config("COM3", "9460 custom=true")

        # Assert
        serial_cfg = _saved(path)["serial"]
        assert (serial_cfg["baud_rate"], serial_cfg["custom_baud"]) == (9460, True)


class TestRunQuick:
    """``cli_flags.run_quick``: which positional is a port, and how the config names it."""

    def test_a_present_port_opens_its_config(self, cfg_root):
        """A device name the system lists opens quick-<device>."""
        # Act
        path = run_quick(
            "COM3", ["9600"], usage_error=_usage_error, source=lambda: _fleet(("COM3", "A1"))
        )

        # Assert
        assert path == cfg_root / "quick-COM3" / "quick-COM3.cfg"
        assert _saved(path)["serial"]["port"] == "COM3"

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows device names ignore case")
    def test_a_device_name_takes_the_systems_spelling(self, cfg_root):
        """com3 on Windows is the COM3 the system lists, so both spellings open one config."""
        # Act
        path = run_quick(
            "com3", [], usage_error=_usage_error, source=lambda: _fleet(("COM3", "A1"))
        )

        # Assert
        assert path.name == "quick-COM3.cfg"
        assert _saved(path)["serial"]["port"] == "COM3", "the config names the port as listed"

    def test_a_serial_number_opens_a_config_named_for_it(self, cfg_root):
        """A USB serial number is a port, and its config is named for it, so it follows the board between COM numbers."""
        # Act
        path = run_quick(
            "58E6C5716A840000",
            ["115200"],
            usage_error=_usage_error,
            source=lambda: _fleet(("COM9", "58E6C5716A840000")),
        )

        # Assert
        assert path.name == "quick-58E6C5716A840000.cfg"
        assert _saved(path)["serial"]["port"] == "58E6C5716A840000", (
            "the config connects by serial number, wherever the board enumerates"
        )

    def test_a_url_is_a_port_without_opening_it(self, cfg_root):
        """A pyserial URL counts as a port whatever is listed; nothing is opened to decide."""
        # Act
        path = run_quick("socket://localhost:7000", [], usage_error=_usage_error, source=list)

        # Assert
        assert path.name == "quick-socket-localhost-7000.cfg"

    def test_an_unplugged_port_opens_the_config_it_had(self, cfg_root):
        """A port no longer listed still opens when a quick session made its config before."""
        # Arrange
        made = run_quick(
            "COM7", ["9600"], usage_error=_usage_error, source=lambda: _fleet(("COM7", ""))
        )

        # Act
        path = run_quick("COM7", [], usage_error=_usage_error, source=list)

        # Assert
        assert path == made, "the adapter's config opens and waits for it, as a project config does"

    def test_an_unknown_name_exits_naming_what_it_meant(self, cfg_root, capsys):
        """No config, no port, no quick config: exit 1 with the did-you-mean over configs and ports."""
        # Act
        with pytest.raises(SystemExit) as raised:
            run_quick("COM33", [], usage_error=_usage_error, source=lambda: _fleet(("COM3", "")))

        # Assert
        assert raised.value.code == 1
        assert "Unknown config or port: COM33 -- did you mean COM3?" in capsys.readouterr().err
        assert list(cfg_root.iterdir()) == [], "an unknown name creates nothing"

    def test_a_bad_setting_is_a_usage_error(self, cfg_root):
        """A word that is no setting goes to usage_error, which exits 2 in main()."""
        # Act / Assert
        with pytest.raises(_UsageError, match="Unknown setting: fast"):
            run_quick(
                "COM3", ["fast"], usage_error=_usage_error, source=lambda: _fleet(("COM3", ""))
            )


def _termapy(cwd: Path, *argv: str, env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    """Run ``termapy <argv>`` through main() in ``cwd``, with no TERMAPY_CFG_DIR unless ``env_extra`` sets one."""
    env = {key: value for key, value in os.environ.items() if key != "TERMAPY_CFG_DIR"}
    env.update(env_extra)
    return subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys; sys.argv = ['termapy', *{list(argv)!r}]; "
            "from termapy.entry import main; main()",
        ],
        cwd=cwd,
        env=env,
        input="",
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.slow
class TestCommandLine:
    """``main()`` end to end, ``--check`` standing in for the frontend as TestNewFlag's tests do."""

    @pytest.mark.rule("quick-open-outside-project")
    def test_quick_open_outside_project(self, tmp_path):
        """A port typed inside a project makes its config in the user folder, never ./termapy_cfg."""
        # Arrange -- a project folder, and a user folder the OS lookup lands in on every platform
        project = tmp_path / "project"
        (project / "termapy_cfg").mkdir(parents=True)
        home = tmp_path / "home"
        home.mkdir()
        user_folder = {"APPDATA": str(home), "XDG_CONFIG_HOME": str(home), "HOME": str(home)}

        # Act
        result = _termapy(
            project, "socket://localhost:7000", "9600", "--check", env_extra=user_folder
        )

        # Assert
        made = list(home.rglob("quick-*.cfg"))
        assert result.returncode == 0, result.stderr
        assert [path.name for path in made] == ["quick-socket-localhost-7000.cfg"]
        assert list((project / "termapy_cfg").glob("*/*.cfg")) == [], "the project gains no config"

    def test_a_flag_may_sit_between_the_settings(self, tmp_path):
        """termapy PORT 9600 --check E71: the words on both sides of the flag are settings."""
        # Act
        result = _termapy(
            tmp_path,
            "socket://localhost:7000",
            "9600",
            "--check",
            "E71",
            env_extra={"TERMAPY_CFG_DIR": str(tmp_path)},
        )

        # Assert
        serial_cfg = _saved(
            tmp_path / "quick-socket-localhost-7000" / "quick-socket-localhost-7000.cfg"
        )["serial"]
        assert result.returncode == 0, result.stderr
        assert (serial_cfg["baud_rate"], serial_cfg["parity"]) == (9600, "E")

    def test_settings_after_a_config_are_refused(self, tmp_path):
        """A config wins its name, and settings follow a port only: exit 2, the config untouched."""
        # Arrange
        _termapy(
            tmp_path,
            "--new",
            "dev",
            "COM9",
            "--check",
            env_extra={"TERMAPY_CFG_DIR": str(tmp_path)},
        )
        cfg_path = tmp_path / "dev" / "dev.cfg"
        before = cfg_path.read_bytes()

        # Act
        result = _termapy(
            tmp_path, "dev", "9600", "--check", env_extra={"TERMAPY_CFG_DIR": str(tmp_path)}
        )

        # Assert
        assert result.returncode == 2
        assert "settings follow a port, not a config: 9600" in result.stderr
        assert cfg_path.read_bytes() == before

    def test_mcp_never_opens_a_port_for_its_positional(self, tmp_path):
        """--mcp looks a name up among its project's configs only: a port positional makes no quick config."""
        # Act
        result = _termapy(
            tmp_path,
            "--mcp",
            "socket://localhost:7000",
            env_extra={"TERMAPY_CFG_DIR": str(tmp_path)},
        )

        # Assert
        assert result.returncode != 0, "a name no config has stops the server, as before"
        assert "No config matching" in result.stderr, "the refusal is MCP's own"
        assert list(tmp_path.glob("quick-*")) == [], "no quick config is made under --mcp"

    @pytest.mark.sw_loopback
    def test_a_quick_session_connects_with_its_settings(self, tmp_path):
        """termapy loop:// 9600 --cli -e: the one-shot runs on the port at the baud typed."""
        # Act
        result = _termapy(
            tmp_path,
            "loop://",
            "9600",
            "--cli",
            "-e",
            "/port.baud_rate",
            env_extra={"TERMAPY_CFG_DIR": str(tmp_path)},
        )

        # Assert
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "9600", "the session opened the port at the typed rate"
        assert "Created" in result.stderr, "the status line names the config it made"
