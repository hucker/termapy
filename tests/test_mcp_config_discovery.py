"""How an MCP server finds its project's config folder and its config at launch.

The rules are Chuck's: the first of three folders, then the only config, then
the only ``mcp_*``, then a chain over several ``mcp_*``; otherwise none, and the
agent is told what it can load.  Any config in the folder can be loaded by bare
name, and nothing else can.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="mcp SDK not installed; install with [mcp] extra")

import termapy  # noqa: E402
from termapy.config import (  # noqa: E402
    CONFIG_GLOB,
    config_files,
    config_names,
    is_config_file,
    open_serial,
)
from termapy.config_resolve import choose_mcp_configs, find_config, find_mcp_root  # noqa: E402
from termapy.defaults import DEFAULT_CFG, default_cfg  # noqa: E402
from termapy.mcp.server import (  # noqa: E402
    MCPHost,
    _start_instructions,
    _start_on_first_open,
)
from termapy.plugins.capabilities import STATE_MESSAGES  # noqa: E402


def _make_config(root: Path, name: str, port: str = "DEMO") -> Path:
    """A config on disk: ``root/<name>/<name>.cfg``."""
    serial = {**DEFAULT_CFG["serial"], "port": port}
    path = root / name / f"{name}.cfg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**DEFAULT_CFG, "serial": serial}), encoding="utf-8")
    return path


def _names(paths: list[Path]) -> list[str]:
    """The config names of ``paths``, as a reader compares them."""
    return [path.stem for path in paths]


def _spells_config_glob(source: str) -> bool:
    """Whether Python ``source`` holds the config glob as a string literal."""
    return any(
        isinstance(node, ast.Constant) and node.value == CONFIG_GLOB
        for node in ast.walk(ast.parse(source))
    )


class TestWhatIsAConfig:
    @pytest.mark.rule("config-files-one-reader")
    def test_only_config_spells_the_config_glob(self):
        """A second */*.cfg would count stray files as configs again, so only config.py holds it."""
        # Arrange
        package = Path(termapy.__file__).parent
        sources = [path for path in package.rglob("*.py") if "vendor" not in path.parts]

        # Act
        spelled = [
            path.relative_to(package).as_posix()
            for path in sources
            if _spells_config_glob(path.read_text(encoding="utf-8"))
        ]

        # Assert
        assert spelled == ["config.py"], "read configs through config.config_files"

    @pytest.mark.rule("config-files-one-reader")
    def test_the_glob_guard_fires_on_a_probe(self):
        """The walk catches the literal, and not a comment that names it."""
        assert _spells_config_glob('paths = root.glob("*/*.cfg")') is True
        assert _spells_config_glob('# root.glob("*/*.cfg")\npaths = []') is False

    @pytest.mark.rule("config-files-one-reader")
    def test_another_cfg_in_a_folder_is_not_a_config(self, tmp_path):
        """demo/.target_menu.cfg is not a config, so it cannot make one config look like two."""
        # Arrange
        _make_config(tmp_path, "demo")
        (tmp_path / "demo" / ".target_menu.cfg").write_text("{}", encoding="utf-8")

        # Act
        actual = _names(config_files(tmp_path))

        # Assert
        assert actual == ["demo"]

    @pytest.mark.rule("config-files-one-reader")
    def test_a_lone_config_with_a_stray_still_loads_by_itself(self, tmp_path, monkeypatch):
        """find_config counts by the same reader, so the stray no longer forces a picker."""
        # Arrange
        path = _make_config(tmp_path, "demo")
        (tmp_path / "demo" / ".target_menu.cfg").write_text("{}", encoding="utf-8")
        monkeypatch.setenv("TERMAPY_CFG_DIR", str(tmp_path))

        # Act
        actual = find_config()

        # Assert
        assert actual == (str(path), False)

    def test_a_missing_folder_has_no_configs(self, tmp_path):
        """A project with no config folder answers with an empty list, not an error."""
        assert config_files(tmp_path / "nowhere") == []

    @pytest.mark.rule("config-files-one-reader")
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("demo/demo.cfg", True),
            ("Demo/demo.cfg", True),
            ("demo/.target_menu.cfg", False),
            ("demo/other.cfg", False),
        ],
    )
    def test_the_name_and_its_folder_agree(self, path, expected):
        """A config is <name>/<name>.cfg, case ignored as the disk ignores it."""
        assert is_config_file(Path(path)) is expected


class TestWhichFolder:
    @pytest.mark.rule("mcp-finds-its-config")
    @pytest.mark.parametrize(
        ("present", "expected"),
        [
            (["termapy_cfg"], "termapy_cfg"),
            (["src/termapy_cfg"], "src/termapy_cfg"),
            (["source/termapy_cfg"], "source/termapy_cfg"),
            (["termapy_cfg", "src/termapy_cfg"], "termapy_cfg"),
            (["src/termapy_cfg", "source/termapy_cfg"], "src/termapy_cfg"),
        ],
    )
    def test_the_first_folder_found_wins(self, tmp_path, present, expected):
        """./termapy_cfg, then src, then source, so a stale src/termapy_cfg cannot shadow the real one."""
        # Arrange
        for folder in present:
            (tmp_path / folder).mkdir(parents=True)

        # Act
        actual = find_mcp_root(tmp_path)

        # Assert
        assert actual is not None
        assert actual.resolve() == (tmp_path / expected).resolve()

    @pytest.mark.rule("mcp-finds-its-config")
    def test_no_folder_is_none_and_never_the_os_folder(self, tmp_path):
        """A server enabled for an unrelated project finds nothing rather than the user's other configs."""
        assert find_mcp_root(tmp_path) is None


class TestWhichConfig:
    @pytest.mark.rule("mcp-finds-its-config")
    @pytest.mark.parametrize(
        ("configs", "expected"),
        [
            (["only"], ["only"]),
            (["mcp_only"], ["mcp_only"]),
            (["demo", "mcp_board", "pico"], ["mcp_board"]),
            (["demo", "mcp_b", "mcp_a"], ["mcp_a", "mcp_b"]),
            (["demo", "pico"], []),
            ([], []),
        ],
    )
    def test_the_rules_in_order(self, tmp_path, configs, expected):
        """The only config, else every mcp_* in name order, else none so the agent chooses."""
        # Arrange
        for name in configs:
            _make_config(tmp_path, name)

        # Act
        paths, _ = choose_mcp_configs(tmp_path)

        # Assert
        assert _names(paths) == expected

    def test_an_empty_choice_says_why(self, tmp_path):
        """With several and no mcp_*, the reason says so, naming how to make one the default."""
        # Arrange
        _make_config(tmp_path, "demo")
        _make_config(tmp_path, "pico")

        # Act
        _, reason = choose_mcp_configs(tmp_path)

        # Assert
        assert reason == "2 configs and none named mcp_*, so none was chosen."


@pytest.fixture
def root(tmp_path, monkeypatch) -> Path:
    """A project's config folder, pinned so every lookup sees it."""
    folder = tmp_path / "termapy_cfg"
    folder.mkdir()
    monkeypatch.setenv("TERMAPY_CFG_DIR", str(folder))
    return folder


def _bare_host(tmp_path: Path) -> MCPHost:
    """A host with no config yet, as a chain starts."""
    made = MCPHost(default_cfg(), "", verbose=False)
    made.repl.global_root = tmp_path
    made._fire_app_start()
    return made


def _stem(host: MCPHost) -> str:
    """The active config's name."""
    return Path(host.config_path).stem


class TestTheChain:
    @pytest.mark.rule("mcp-finds-its-config")
    def test_a_failed_open_moves_on(self, root, tmp_path):
        """mcp_a's port will not open, so the server stays on mcp_b, connected, and says why."""
        # Arrange
        paths = [
            str(_make_config(root, "mcp_a", port="DEMO_FAIL")),
            str(_make_config(root, "mcp_b")),
        ]
        host = _bare_host(tmp_path)

        # Act
        note = _start_on_first_open(host, paths)

        # Assert
        try:
            assert _stem(host) == "mcp_b"
            assert host.engine.is_connected, "it stayed on the one that opened"
            assert note.startswith("Skipped mcp_a: Cannot open DEMO_FAIL"), note
        finally:
            host._disconnect()

    @pytest.mark.rule("mcp-finds-its-config")
    def test_the_first_that_opens_ends_the_walk(self, root, tmp_path):
        """When the first opens, the second is never tried and there is nothing to report."""
        # Arrange
        paths = [str(_make_config(root, "mcp_a")), str(_make_config(root, "mcp_b"))]
        host = _bare_host(tmp_path)

        # Act
        note = _start_on_first_open(host, paths)

        # Assert
        try:
            assert _stem(host) == "mcp_a"
            assert note == "", "nothing was skipped"
        finally:
            host._disconnect()

    @pytest.mark.rule("mcp-finds-its-config")
    def test_all_failing_lands_on_the_first_not_connected(self, root, tmp_path):
        """With no port open it stays on the first and gives every reason, so /port.connect can retry."""
        # Arrange
        paths = [
            str(_make_config(root, "mcp_a", port="DEMO_FAIL")),
            str(_make_config(root, "mcp_b", port="DEMO_FAIL")),
        ]
        host = _bare_host(tmp_path)

        # Act
        note = _start_on_first_open(host, paths)

        # Assert
        assert _stem(host) == "mcp_a"
        assert not host.engine.is_connected
        assert "mcp_a: Cannot open" in note, note
        assert "mcp_b: Cannot open" in note, note

    def test_a_broken_config_is_skipped_too(self, root, tmp_path):
        """A config that will not parse is one more failure, not a crash at launch."""
        # Arrange
        broken = _make_config(root, "mcp_a")
        broken.write_text("{ not json", encoding="utf-8")
        paths = [str(broken), str(_make_config(root, "mcp_b"))]
        host = _bare_host(tmp_path)

        # Act
        note = _start_on_first_open(host, paths)

        # Assert
        try:
            assert _stem(host) == "mcp_b"
            assert note.startswith("Skipped mcp_a:"), note
        finally:
            host._disconnect()

    def test_walking_twice_lands_the_same_way(self, root, tmp_path):
        """A second walk on the same host starts clean: the same failure, the same config, connected once."""
        # Arrange
        paths = [
            str(_make_config(root, "mcp_a", port="DEMO_FAIL")),
            str(_make_config(root, "mcp_b")),
        ]
        host = _bare_host(tmp_path)

        # Act
        first = _start_on_first_open(host, paths)
        host._disconnect()
        second = _start_on_first_open(host, paths)

        # Assert
        try:
            assert first == second
            assert _stem(host) == "mcp_b"
            assert host.engine.is_connected
        finally:
            host._disconnect()


def _call(host: MCPHost, line: str) -> dict:
    """One ``run_command`` call, as the tool makes it."""
    return asyncio.run(host.run_command_async(line, "normal", 8.0))


class TestLoadingByName:
    @pytest.fixture
    def host(self, root, tmp_path):
        """A host on one config, with a sibling to load, and a stray file in the working folder."""
        active = _make_config(root, "alpha", port="")
        _make_config(root, "beta", port="")
        made = MCPHost(json.loads(active.read_text(encoding="utf-8")), str(active), verbose=False)
        made.repl.global_root = tmp_path
        made._fire_app_start()
        return made

    @pytest.mark.rule("mcp-finds-its-config")
    @pytest.mark.parametrize("name", ["beta", "beta.cfg"])
    def test_any_config_in_the_folder_loads_by_name(self, host, name):
        """The folder's configs belong to the project, so an agent may load any of them."""
        # Act
        result = _call(host, f"/cfg.load {name}")

        # Assert
        assert result["success"] is True, result["error"]
        assert _stem(host) == "beta"

    def test_a_load_whose_port_fails_says_so(self, root, host):
        """A config can load with its port held by the other server: the load is true, and the reply says the port did not open."""
        # Arrange
        path = _make_config(root, "held", port="DEMO_FAIL")
        cfg = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**cfg, "auto_connect": True}), encoding="utf-8")

        # Act
        result = _call(host, "/cfg.load held")

        # Assert
        assert result["success"] is True, "the config is loaded"
        lines = [line["text"] for line in result["output_lines"]]
        assert any(line.startswith("Cannot open DEMO_FAIL") for line in lines), lines
        assert not host.engine.is_connected

    def test_loading_there_and_back(self, host):
        """Switching twice lands where it started: a load is not a one-way door."""
        # Act
        _call(host, "/cfg.load beta")
        result = _call(host, "/cfg.load alpha")

        # Assert
        assert result["success"] is True, result["error"]
        assert _stem(host) == "alpha"

    @pytest.mark.rule("mcp-finds-its-config")
    def test_a_stray_file_in_the_working_folder_is_not_a_config(self, host, tmp_path):
        """/cfg.load plain.json names no config, so the file is left byte for byte."""
        # Arrange
        stray = tmp_path / "plain.json"
        stray.write_text('{"name": "not a config"}\n', encoding="utf-8")

        # Act
        with contextlib.chdir(tmp_path):
            result = _call(host, "/cfg.load plain.json")

        # Assert
        assert result["success"] is False
        assert "No config matching" in result["error"], result["error"]
        assert stray.read_text(encoding="utf-8") == '{"name": "not a config"}\n', "untouched"
        assert _stem(host) == "alpha", "still on the config it had"

    @pytest.mark.rule("mcp-finds-its-config")
    def test_a_path_to_another_folder_is_not_a_config(self, host, tmp_path):
        """A config outside the folder is refused, and its plugins never run."""
        # Arrange
        marker = tmp_path / "plugin_ran.txt"
        elsewhere = tmp_path / "elsewhere"
        (elsewhere / "plugin").mkdir(parents=True)
        (elsewhere / "elsewhere.cfg").write_text(json.dumps(DEFAULT_CFG), encoding="utf-8")
        (elsewhere / "plugin" / "loaded.py").write_text(
            f"from pathlib import Path\nPath(r'{marker}').write_text('ran')\n", encoding="utf-8"
        )

        # Act
        with contextlib.chdir(tmp_path):
            result = _call(host, "/cfg.load elsewhere/elsewhere.cfg")

        # Assert
        assert result["success"] is False
        assert not marker.exists(), "that folder's plugin did not run"


class TestItsOwnProfileLoads:
    @pytest.fixture
    def board(self, root):
        """A config with its profile beside it, as a real device has, connecting on load."""
        path = _make_config(root, "board")
        cfg = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**cfg, "auto_connect": True}), encoding="utf-8")
        demo = Path(termapy.__file__).parent / "builtins" / "demo" / "demo_legacy.profile.json"
        shutil.copy(demo, path.parent / "board.profile.json")
        return path

    @pytest.mark.rule("mcp-finds-its-config")
    def test_loading_by_name_loads_its_profile(self, board, tmp_path):
        """Config paths are absolute, and the path guard must not refuse the server's own profile: its tiers are the safety gate."""
        # Arrange
        host = _bare_host(tmp_path)

        # Act
        result = _call(host, "/cfg.load board")

        # Assert
        try:
            lines = [line["text"] for line in result["output_lines"]]
            assert not any("outside the config sandbox" in line for line in lines), lines
            assert host.ctx.ns("active_profile"), "the profile is installed"
        finally:
            host._disconnect()

    def test_connecting_again_loads_it_again(self, board, tmp_path):
        """A disconnect clears the profile, so a reconnect must load it again, the second time as the first."""
        # Arrange
        host = _bare_host(tmp_path)
        _call(host, "/cfg.load board")

        # Act
        _call(host, "/port.disconnect")
        cleared = dict(host.ctx.ns("active_profile"))
        _call(host, "/port.connect")

        # Assert
        try:
            assert not cleared, "the disconnect cleared it"
            assert host.ctx.ns("active_profile"), "and the reconnect loaded it"
        finally:
            host._disconnect()

    def test_a_config_named_at_launch_loads_its_profile(self, board):
        """The launch path takes a full path too, and that form loads the same profile."""
        # Arrange
        host = MCPHost(json.loads(board.read_text(encoding="utf-8")), str(board), verbose=False)
        host.repl.global_root = board.parent
        host._fire_app_start()

        # Act
        host._connect()

        # Assert
        try:
            assert host.ctx.ns("active_profile"), "the profile is installed"
        finally:
            host._disconnect()


class TestAnAgentCanLoadAnyConfig:
    @pytest.fixture
    def started(self, root, tmp_path):
        """A host on one config, with a sibling an agent may load."""
        active = _make_config(root, "left", port="")
        _make_config(root, "right", port="")
        made = MCPHost(json.loads(active.read_text(encoding="utf-8")), str(active), verbose=False)
        made.repl.global_root = tmp_path
        made._fire_app_start()
        return made

    @pytest.mark.rule("mcp-finds-its-config")
    def test_an_agent_can_load_another(self, started):
        """An agent moves to a sibling config and back, however the server started."""
        # Act
        there = _call(started, "/cfg.load right")
        there_stem = _stem(started)
        back = _call(started, "/cfg.load left")

        # Assert
        assert there["success"] is True, there["error"]
        assert there_stem == "right"
        assert back["success"] is True, back["error"]
        assert _stem(started) == "left"

    @pytest.mark.parametrize("name", ["left", "LEFT", "left.cfg"])
    def test_reloading_it_is_still_a_load(self, started, name):
        """Loading the config it already has is a fresh start on it, twice, in any spelling of its name."""
        # Act
        first = _call(started, f"/cfg.load {name}")
        second = _call(started, f"/cfg.load {name}")

        # Assert
        assert first["success"] is True, first["error"]
        assert second["success"] is True, second["error"]
        assert _stem(started) == "left"

    @pytest.mark.rule("mcp-finds-its-config")
    def test_a_reload_picks_up_an_edit(self, started):
        """A config edited on disk is what an agent gets by loading it again."""
        # Arrange
        path = Path(started.config_path)
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["title"] = "edited"
        path.write_text(json.dumps(cfg), encoding="utf-8")

        # Act
        result = _call(started, "/cfg.load left")

        # Assert
        assert result["success"] is True, result["error"]
        assert started.cfg["title"] == "edited", "the file on disk, not the one it started with"

    def test_it_offers_the_others(self, started):
        """What an agent hears lists every config it can load, the one it is on included."""
        # Act
        actual = _start_instructions(started)

        # Assert
        assert actual.startswith("Active config: left. "), actual
        assert "right" in actual, "the sibling is on offer"
        assert "/cfg.load <name>" in actual, "and it says how"


class TestTheAgentIsTold:
    @pytest.mark.rule("mcp-finds-its-config")
    def test_no_config_loaded_lists_the_choices(self, root, tmp_path):
        """The reply an agent first hits names what it can load, so it need not ask."""
        # Arrange
        _make_config(root, "demo", port="")
        _make_config(root, "pico", port="")
        host = _bare_host(tmp_path)

        # Act
        result = _call(host, "/run.list")

        # Assert
        expected = f"{STATE_MESSAGES['config_loaded']} Configs in this project: demo, pico."
        assert result["error"].startswith(expected), result["error"]
        assert "/cfg.load <name>" in result["error"], "and how to load one"

    def test_with_nothing_to_load_the_message_stands_alone(self, root, tmp_path):
        """A project with no configs gets the plain state message, with no empty list trailing it."""
        # Arrange
        host = _bare_host(tmp_path)

        # Act
        result = _call(host, "/run.list")

        # Assert
        assert result["error"] == STATE_MESSAGES["config_loaded"]

    def test_the_instructions_name_the_config_and_the_choices(self, root, tmp_path):
        """What an agent hears on connecting says what it has and what it may load."""
        # Arrange
        active = _make_config(root, "alpha", port="")
        _make_config(root, "beta", port="")
        host = MCPHost(json.loads(active.read_text(encoding="utf-8")), str(active), verbose=False)
        host.start_note = "Skipped mcp_a: busy."

        # Act
        actual = _start_instructions(host)

        # Assert
        assert actual.startswith(
            "Active config: alpha. Skipped mcp_a: busy. Configs in this project:"
        )

    def test_the_instructions_say_when_there_is_none(self, root, tmp_path):
        """With no config, the first thing an agent hears says so, with the reason."""
        # Arrange
        host = _bare_host(tmp_path)
        host.start_note = "No configs in the folder."

        # Act
        actual = _start_instructions(host)

        # Assert
        assert actual == "No config is loaded. No configs in the folder."


class TestAnExclusiveOpen:
    @pytest.mark.rule("port-open-exclusive")
    def test_a_second_open_of_a_held_port_fails(self):
        """A second termapy cannot share a port, so a chain moves on instead of splitting its bytes."""
        import serial

        if sys.platform == "win32":
            pytest.skip("Windows refuses a second open by itself; hw_loopback covers it")

        # Arrange
        master, slave = os.openpty()
        cfg = {"serial": {**DEFAULT_CFG["serial"], "port": os.ttyname(slave)}}
        first = open_serial(cfg)

        # Act / Assert
        try:
            with pytest.raises(serial.SerialException):
                open_serial(cfg)
        finally:
            first.close()
            os.close(slave)
            os.close(master)

    @pytest.mark.rule("port-open-exclusive")
    @pytest.mark.sw_loopback
    def test_a_url_port_still_opens(self):
        """The flag reaches pyserial's URL handlers too, and loop:// must still open, twice."""
        # Arrange
        cfg = {"serial": {**DEFAULT_CFG["serial"], "port": "loop://"}}

        # Act
        first = open_serial(cfg)
        second = open_serial(cfg)

        # Assert
        try:
            assert first.is_open
            assert second.is_open
        finally:
            first.close()
            second.close()

    @pytest.mark.rule("config-files-one-reader")
    def test_config_names_reads_the_one_reader(self, root):
        """The did-you-mean's names exclude the stray, as every other list does."""
        # Arrange
        _make_config(root, "demo")
        (root / "demo" / ".target_menu.cfg").write_text("{}", encoding="utf-8")

        # Act
        actual = config_names()

        # Assert
        assert actual == ["demo"]
