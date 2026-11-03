"""A config load ends with what a fresh start on that config has, whatever ran before.

The guard compares the two across every frontend, one process per scenario so
module-level state cannot carry between scenarios, only within one.  The rest
pins what the load keeps: what the user asked for at launch and a host's own
rules, a script's echo, a connection the config asks for, and a port change,
which is not a load.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from importlib import resources
from pathlib import Path

import pytest
from pilot_wait import until
from textual.widgets import Button

import termapy.builtins
from termapy import variables
from termapy.config import load_config
from termapy.defaults import DEFAULT_CFG
from termapy.pickers import on_config_result, on_script_picked

BUILTINS_FOLDER = Path(termapy.builtins.__file__).parent
FRONTENDS = ("cli", "tui", "mcp")

# What differs between two runs without bearing on what the session does next.
NOT_STATE = {
    "ctx._namespaces": "compared entry by entry, and an empty namespace holds nothing",
    "engine._cfg": "the config, compared key by key",
    "engine._last_rx_monotonic": "a clock",
    "host._history_mtime_seen": "the time of a history file an earlier scenario wrote",
    "host._main_thread_id": "a thread id",
    "host._session": "the CLI prompt, built when it first prompts",
    "repl._cfg_data": "the config, compared key by key",
}

A_ONLY_PLUGIN = """
from termapy.plugins import CmdResult, Command


def _handler(ctx, args):
    return CmdResult.ok(value="a")


COMMAND = Command(name="a_only", help="Only in config A.", handler=_handler)
"""


def _config_paths(root: Path) -> dict[str, Path]:
    """Where the two configs of a scenario live."""
    return {name: root / name / f"{name}.cfg" for name in ("a", "b")}


def _write_configs(root: Path) -> None:
    """Config A differs from B in every session flag, and has a plugin B does not."""
    serial = dict(DEFAULT_CFG["serial"], port="DEMO")
    settings = {
        "a": dict(
            DEFAULT_CFG,
            title="A",
            serial=dict(serial, baud_rate=9600),
            auto_connect=False,
            echo=True,
            hex=True,
            line_no=True,
            color=False,
            output_level="verbose",
            timestamps=True,
        ),
        "b": dict(DEFAULT_CFG, title="B", serial=serial, auto_connect=False),
    }
    for name, path in _config_paths(root).items():
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(settings[name]), encoding="utf-8")
    plugin_folder = root / "a" / "plugin"
    plugin_folder.mkdir()
    (plugin_folder / "a_only.py").write_text(A_ONLY_PLUGIN, encoding="utf-8")


def _use_everything(repl, recording: str = "") -> None:
    """Change what a user can: every setting command by its declaration, then the rest."""
    lines = []
    for command, plugin in sorted(repl._plugins.items()):
        if plugin.state and plugin.args.endswith("toggle}"):
            lines.append(f"{command} toggle")
        elif plugin.state and plugin.args.endswith("cycle}"):
            lines.append(f"{command} cycle")
    profile = resources.files("termapy.boards") / "pico" / "profile.json"
    lines += [
        "term.request err=ZZZ",
        "find.max_count 7",
        "find abc",
        "var.set LEFTOVER 1",
        "env.set LEFTOVER 1",
        "print {seq1+}",
        "print {seq1+}",
        "xfer.root .",
        f"profile.load {profile}",
    ]
    if recording:
        lines.append(f"run.record {recording}")
    for line in lines:
        repl.dispatch(line, report=False)


def _digest(items: list[str]) -> str:
    """A short stable name for a list, the same in every process."""
    return hashlib.sha256("|".join(items).encode()).hexdigest()[:12]


def _summary(value: object) -> str:
    """A value as text that two processes can compare: no addresses, no object identity."""
    if isinstance(value, (bool, int, float, str, type(None))):
        return repr(value)[:80]
    if isinstance(value, dict):
        names = sorted(str(key) for key in value)
        return f"dict {len(names)} {_digest(names)}"
    if isinstance(value, (list, tuple, set, frozenset)):
        items = sorted(
            repr(item) if isinstance(item, (bool, int, float, str)) else type(item).__name__
            for item in value
        )
        return f"{type(value).__name__} {len(items)} {_digest(items)}"
    return f"<{type(value).__name__}>"


def _facts(host) -> dict[str, str]:
    """All the state a load could leave behind, each fact named by where it lives."""
    repl = host.repl
    facts: dict[str, str] = {}
    for space_name, space in repl.ctx._namespaces.items():
        for key, value in space.items():
            stamped = str(key).startswith("_start")
            facts[f"ns {space_name}.{key}"] = "(stamped)" if stamped else _summary(value)
    for group, names in variables.snapshot(repl.ctx).items():
        if group == "datetime":
            facts["vars datetime"] = _summary(sorted(names))
            continue
        for name, value in names.items():
            facts[f"vars {group}.{name}"] = _summary(value)
    for key, value in repl._cfg_data.items():
        if not key.startswith("_"):
            facts[f"cfg {key}"] = json.dumps(value, sort_keys=True, default=str)
    for module_name, module in list(sys.modules.items()):
        # A folder plugin of the config being left stays imported but unregistered,
        # so only the built-ins, which outlive every load, can carry state across one.
        file = getattr(module, "__file__", None)
        if file and Path(file).is_relative_to(BUILTINS_FOLDER):
            for name, value in vars(module).items():
                skipped = callable(value) or isinstance(value, type(sys)) or name.startswith("__")
                if not skipped:
                    facts[f"module {module_name}.{name}"] = _summary(value)
    textual_names = _textual_app_names()
    owners = {"repl": repl, "engine": host.engine, "ctx": repl.ctx, "capture": host.capture}
    for prefix, owner in owners.items():
        for name, value in vars(owner).items():
            facts[f"{prefix}.{name}"] = _summary(value)
    for name, value in vars(host).items():
        if name not in textual_names:
            facts[f"host.{name}"] = _summary(value)
    facts["commands"] = " ".join(sorted(repl._plugins))
    return facts


def _textual_app_names() -> set[str]:
    """Textual's own attributes on the TUI host: its state is not termapy's to reset."""
    if "textual.app" not in sys.modules:
        return set()
    from textual.app import App

    return set(vars(App()))


def _build_headless(frontend: str, config_path: Path):
    """A started CLI or MCP host, through the start each frontend really makes."""
    cfg = load_config(str(config_path))
    if frontend == "cli":
        from termapy.cli import CLITerminal

        host = CLITerminal(cfg, str(config_path), no_color=False, term_width=120)
    else:
        from termapy.mcp.server import MCPHost

        host = MCPHost(cfg, str(config_path), verbose=False)
    host._fire_app_start()
    return host


def _drive(host, scenario: str, paths: dict[str, Path]) -> dict[str, dict[str, str] | None]:
    """One scenario on a started host."""
    before = None
    if scenario in ("used", "used_twice"):
        _use_everything(host.repl, recording=f"from_a_{scenario}")
        before = _facts(host)
    if scenario != "fresh":
        result = host._switch_to_cfg_path(str(paths["b"]))
        assert result.success, result.error
    if scenario == "used_twice":
        _use_everything(host.repl)
        result = host._switch_to_cfg_path(str(paths["b"]))
        assert result.success, result.error
    return {"before": before, "after": _facts(host)}


def _run_scenario(frontend: str, scenario: str, root: Path) -> dict[str, dict[str, str] | None]:
    """A scenario in this process: the child side of the guard."""
    paths = _config_paths(root)
    start = paths["b"] if scenario == "fresh" else paths["a"]
    if frontend != "tui":
        return _drive(_build_headless(frontend, start), scenario, paths)

    from termapy.app import SerialTerminal

    cfg = load_config(str(start))
    cfg["_config_path"] = str(start)
    app = SerialTerminal(cfg, str(start))

    async def body() -> dict[str, dict[str, str] | None]:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            outcome = _drive(app, scenario, paths)
            await pilot.pause()
            outcome["after"] = _facts(app)
            return outcome

    return asyncio.run(body())


def _differences(fresh: dict[str, str], arrived: dict[str, str]) -> dict[str, tuple[str, str]]:
    """Facts that differ between the two, each as (fresh, arrived); NOT_STATE is not state."""
    return {
        key: (fresh.get(key, "(absent)"), arrived.get(key, "(absent)"))
        for key in sorted(fresh.keys() | arrived.keys())
        if key not in NOT_STATE and fresh.get(key) != arrived.get(key)
    }


def _in_a_process(frontend: str, scenario: str, root: Path) -> dict:
    """Run one scenario in its own process and return what it reports."""
    out = root / f"{frontend}_{scenario}.json"
    run = subprocess.run(
        [sys.executable, __file__, frontend, scenario, str(root), str(out)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        cwd=root,
    )
    assert out.exists(), f"{frontend} {scenario} reported nothing: {run.stderr[-2000:]}"
    return json.loads(out.read_text(encoding="utf-8"))


class TestArrivingEqualsAFreshStart:
    """The rule itself, in each frontend, whatever came before."""

    @pytest.mark.slow
    @pytest.mark.parametrize("frontend", FRONTENDS)
    def test_a_load_equals_a_fresh_start(self, frontend, tmp_path):
        """config-load-deterministic: B reached from A, used or not, is B started fresh."""
        # Arrange
        _write_configs(tmp_path)
        fresh = _in_a_process(frontend, "fresh", tmp_path)["after"]

        for scenario in ("clean", "used", "used_twice"):
            # Act
            outcome = _in_a_process(frontend, scenario, tmp_path)

            # Assert
            actual = _differences(fresh, outcome["after"])
            assert actual == {}, f"{frontend}, {scenario}: state survived the load: {actual}"
            if outcome["before"] is not None:
                changed = _differences(fresh, outcome["before"])
                assert "vars user.LEFTOVER" in changed, "the session to throw away really differed"

    def test_a_difference_is_reported_by_name(self):
        """config-load-deterministic: the comparison names a changed, a new and a lost fact."""
        # Arrange
        fresh = {"same": "1", "changed": "1", "lost": "1"}
        arrived = {"same": "1", "changed": "2", "new": "1"}

        # Act
        actual = _differences(fresh, arrived)

        # Assert
        expected = {
            "changed": ("1", "2"),
            "lost": ("1", "(absent)"),
            "new": ("(absent)", "1"),
        }
        assert actual == expected, "every kind of leak is seen, and a matching fact is not"

    def test_what_is_not_state_is_not_compared(self):
        """config-load-deterministic: a clock differing between two runs is not a leak."""
        # Arrange
        key = "engine._last_rx_monotonic"

        # Act
        actual = _differences({key: "1"}, {key: "2"})

        # Assert
        assert actual == {}, "NOT_STATE entries stay out of the comparison"


@pytest.fixture
def configs(tmp_path) -> dict[str, Path]:
    """The two configs on disk."""
    _write_configs(tmp_path)
    return _config_paths(tmp_path)


@pytest.fixture
def cli(configs):
    """A CLI started on A, moved to B at the end so nothing it set outlives the test."""
    host = _build_headless("cli", configs["a"])
    yield host
    host._switch_to_cfg_path(str(configs["b"]))


@pytest.fixture(autouse=True)
def _no_module_state_left_behind():
    """A test that ends mid-session leaves the built-ins as it found them."""
    yield
    variables.clear_start_time_vars()


def _flags(host) -> dict:
    """The session flags of a host."""
    return host.repl.ctx.ns("flags")


class TestWhatALoadForgets:
    """Each thing a user can leave behind, one at a time, in the CLI's in-process path."""

    def test_user_variables_go(self, cli, configs):
        """config-load-deterministic: a /var.set from the config being left is gone."""
        # Arrange
        cli.repl.dispatch("var.set LEFTOVER 1")

        # Act
        cli._switch_to_cfg_path(str(configs["b"]))

        # Assert
        assert "LEFTOVER" not in variables.user_vars(cli.repl.ctx), (
            "the variable is undefined after the load"
        )

    def test_env_set_goes(self, cli, configs):
        """config-load-deterministic: /env.set is undone, and a variable from the process stays."""
        # Arrange
        cli.repl.dispatch("env.set LEFTOVER 1")
        before = cli.repl.dispatch("env.list LEFTOVER").data
        assert before == {"LEFTOVER": "1"}, "precondition: the snapshot has it"

        # Act
        cli._switch_to_cfg_path(str(configs["b"]))

        # Assert
        env = cli.repl.dispatch("env.list").data
        assert "LEFTOVER" not in env, "what /env.set added is gone"
        assert env == dict(os.environ), "the snapshot is the process environment again"

    def test_find_limit_goes_back(self, cli, configs):
        """config-load-deterministic: /find.max_count is the default again, with no hook: it is in ctx.ns()."""
        # Arrange
        expected = cli.repl.dispatch("find.max_count").value
        cli.repl.dispatch("find.max_count 7")
        assert cli.repl.dispatch("find.max_count").value == "7", "precondition: the limit moved"

        # Act
        cli._switch_to_cfg_path(str(configs["b"]))

        # Assert
        actual = cli.repl.dispatch("find.max_count").value
        assert actual == expected

    def test_counters_go_and_the_clock_restarts(self, cli, configs):
        """config-load-deterministic: {seq} starts over and {starttime} is stamped, as on a fresh start."""
        # Arrange
        cli.repl.dispatch("print {seq1+}")
        assert cli.repl.ctx.ns("seq")[1] == 1, "precondition: the counter moved"

        # Act
        cli._switch_to_cfg_path(str(configs["b"]))

        # Assert
        seq = cli.repl.ctx.ns("seq")
        assert 1 not in seq, "the counter is gone"
        assert seq["_start_time"], "the clock a fresh start sets is set"

    def test_a_recording_stops_and_another_can_start(self, cli, configs):
        """config-load-deterministic: a load ends a recording, twice over, with its file closed."""
        for name in ("first", "second"):
            # Arrange
            started = cli.repl.dispatch(f"run.record {name}", report=False)
            assert started.success, started.error

            # Act
            cli._switch_to_cfg_path(str(configs["b"]))

            # Assert
            record = sys.modules["termapy.builtins.commands._run_record"]
            assert record.is_active() is False, f"{name}: the load ended the recording"
            assert cli.repl._post_dispatch_observers == [], f"{name}: nothing still listens"

    def test_a_capture_stops(self, cli, configs, tmp_path):
        """config-load-deterministic: a capture running on the config being left stops and frees the port."""
        # Arrange
        target = tmp_path / "capture.txt"
        started = cli.capture.start(path=target, file_mode="w", mode="text", duration=60.0)
        assert started is True, "precondition: the capture runs"

        # Act
        cli._switch_to_cfg_path(str(configs["b"]))

        # Assert
        assert cli.capture.active is False, "the load stopped it"
        assert cli.engine.serial_claimed is False, "and the port is free"

    def test_a_kept_namespace_is_still_the_session_namespace(self, cli):
        """A plugin holding the dict ns() gave it sees the reset, and writes to the live namespace."""
        # Arrange
        ctx = cli.repl.ctx
        kept = ctx.ns("plugin_state")
        kept["old"] = 1

        # Act
        ctx.reset_namespaces()
        kept["new"] = 2

        # Assert
        actual = ctx.ns("plugin_state")
        assert actual is kept, "the same dict"
        assert actual == {"new": 2}, "emptied, then written"


class TestWhatALoadKeeps:
    """Startup flags and a host's rules hit after the load; the config decides the rest."""

    def _on_b(self, frontend: str, configs, **settings):
        """A host on A, with B on disk holding these settings."""
        path = configs["b"]
        cfg = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps(dict(cfg, **settings)), encoding="utf-8")
        return _build_headless(frontend, configs["a"])

    def test_the_config_decides_with_no_flag_given(self, configs):
        """config-load-deterministic: with no flag at launch, color and level are the file's."""
        # Arrange
        host = self._on_b("cli", configs, color=False, output_level="verbose", echo=True)

        # Act
        host._switch_to_cfg_path(str(configs["b"]))

        # Assert
        flags = _flags(host)
        actual = (flags["color"], flags["output_level"], flags["echo"])
        assert actual == (False, "verbose", True), "every session flag follows the file"

    def test_a_fresh_start_reads_the_same_keys(self, configs):
        """config-load-deterministic: starting on the config and loading it read the keys alike."""
        # Arrange
        self._on_b("cli", configs, color=False, output_level="silent")

        # Act
        host = _build_headless("cli", configs["b"])

        # Assert
        flags = _flags(host)
        assert (flags["color"], flags["output_level"]) == (False, "silent")

    def test_no_color_and_a_level_at_launch_outlast_the_config(self, configs):
        """config-load-deterministic: what the user asked for at launch is laid over every load."""
        # Arrange
        from termapy.cli import CLITerminal

        self._on_b("cli", configs, color=True, output_level="verbose")
        cfg = load_config(str(configs["a"]))
        host = CLITerminal(cfg, str(configs["a"]), no_color=True, output_level="quiet")
        host._fire_app_start()

        # Act
        host._switch_to_cfg_path(str(configs["b"]))

        # Assert
        flags = _flags(host)
        assert (flags["color"], flags["output_level"]) == (False, "quiet")

    def test_mcp_keeps_its_rules_over_any_config(self, configs):
        """config-load-deterministic: an agent's JSON stays uncolored, quiet and without echo."""
        # Arrange
        host = self._on_b("mcp", configs, color=True, output_level="verbose", echo=True)

        # Act
        host._switch_to_cfg_path(str(configs["b"]))

        # Assert
        flags = _flags(host)
        actual = (flags["color"], flags["output_level"], flags["echo"])
        assert actual == (False, "quiet", False)


class TestCfgMovesTheFlag:
    """A key that starts a session flag is what its setter reports, whichever way it changed."""

    @pytest.mark.parametrize(
        ("key", "text", "expected"),
        [
            ("echo", "on", True),
            ("hex", "on", True),
            ("line_no", "on", True),
            ("color", "off", False),
            ("output_level", "quiet", "quiet"),
        ],
    )
    def test_cfg_auto_moves_the_flag(self, cli, key, text, expected):
        """cfg-keys-start-flags: /cfg.auto changes the session flag, not only the config dict."""
        # Act
        result = cli.repl.dispatch(f"cfg.auto {key} {text}", report=False)

        # Assert
        assert result.success, result.error
        assert _flags(cli)[key] == expected, "the flag is what the session acts on"
        assert cli.repl._cfg_data[key] == expected, "and the config agrees"

    def test_the_setter_answers_what_cfg_auto_set(self, cli):
        """cfg-keys-start-flags: /term.echo reports the value /cfg.auto gave the key, twice over."""
        for text in ("on", "off"):
            # Act
            cli.repl.dispatch(f"cfg.auto echo {text}", report=False)

            # Assert
            assert cli.repl.dispatch("term.echo", report=False).value == text

    def test_a_port_effect_moves_the_flag(self, cli):
        """cfg-keys-start-flags: /port.connect's echo option moves the flag too."""
        # Arrange
        _flags(cli)["echo"] = False

        # Act
        cli._apply_port_effects({"cfg_update": {"echo": True}})

        # Assert
        assert _flags(cli)["echo"] is True
        assert cli.repl._cfg_data["echo"] is True, "and the config agrees"

    def test_a_key_with_no_flag_makes_none(self, cli):
        """cfg-keys-start-flags: only the keys the table marks reach the flags namespace."""
        # Act
        cli.repl.store_cfg("timestamps", True)

        # Assert
        assert "timestamps" not in _flags(cli)
        assert cli.repl._cfg_data["timestamps"] is True


def _boot(configs, start: str, **overrides):
    """The real TUI on one of the configs, with these settings over it."""
    from termapy.app import SerialTerminal

    path = str(configs[start])
    cfg = dict(load_config(path), **overrides)
    cfg["_config_path"] = path
    return SerialTerminal(cfg, path)


async def _submit(pilot, text: str) -> None:
    """Type a line at the prompt and press Enter."""
    from textual.widgets import Input

    field = pilot.app.query_one("#cmd", Input)
    field.value = text
    field.focus()
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()


class TestTheConnection:
    """A load connects only if the config says so; an editor Save and a port change keep the link."""

    def _connected_then(self, configs, act, **b_settings) -> dict:
        """Boot connected on A, run ``act``, and report what the session looks like after."""
        path = configs["b"]
        cfg = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps(dict(cfg, **b_settings)), encoding="utf-8")
        app = _boot(configs, "a", auto_connect=True)

        async def scenario() -> dict:
            async with app.run_test(size=(120, 40)) as pilot:
                assert await until(pilot, lambda: app.is_connected), "precondition: connected"
                app.repl.dispatch("var.set LEFTOVER 1")
                app.repl.dispatch("find.max_count 7")
                app.repl.ctx.ns("seq")[1] = 5
                act(app)
                await until(pilot, lambda: app.is_connected, seconds=0.5)
                return {
                    "connected": app.is_connected,
                    "variable": variables.user_vars(app.repl.ctx).get("LEFTOVER"),
                    "find limit": app.repl.dispatch("find.max_count").value,
                    "counter": app.repl.ctx.ns("seq").get(1),
                }

        return asyncio.run(scenario())

    def test_a_load_drops_the_link_when_the_config_does_not_connect(self, configs):
        """config-load-deterministic: a connected A does not connect a B that says auto_connect false."""
        # Arrange / Act
        actual = self._connected_then(
            configs,
            lambda app: app._switch_config(load_config(str(configs["b"])), str(configs["b"])),
            auto_connect=False,
        )

        # Assert
        assert actual["connected"] is False, "a fresh start on B is not connected"
        assert actual["variable"] is None, "and the session reset"

    def test_a_load_connects_when_the_config_says_to(self, configs):
        """config-load-deterministic: auto_connect true opens the port, as a fresh start does."""
        # Arrange / Act
        actual = self._connected_then(
            configs,
            lambda app: app._switch_config(load_config(str(configs["b"])), str(configs["b"])),
            auto_connect=True,
        )

        # Assert
        assert actual["connected"] is True
        assert actual["variable"] is None, "and the session reset"

    def test_saving_in_the_editor_keeps_the_link(self, configs):
        """A Save is a load that keeps a connected port: an edit is no request to drop the link."""
        # Arrange / Act
        actual = self._connected_then(
            configs,
            lambda app: on_config_result(app, (load_config(str(configs["b"])), str(configs["b"]))),
            auto_connect=False,
        )

        # Assert
        assert actual["connected"] is True, "still linked"
        assert actual["variable"] is None, "and the session reset"

    def test_a_port_change_is_not_a_load(self, configs):
        """A port change keeps the session whole and the link, and fires no on_config_load."""
        # Arrange / Act
        actual = self._connected_then(configs, lambda app: app._update_port("DEMO"))

        # Assert
        expected = {"connected": True, "variable": "1", "find limit": "7", "counter": 5}
        assert actual == expected, "connected, and nothing was reset"


class TestTheScreen:
    """What a load must leave right on screen, and in a script that calls it."""

    def test_the_find_bar_closes_and_the_record_button_goes_idle(self, configs):
        """config-load-deterministic: neither the find bar nor the Record button outlives the session."""
        # Arrange
        app = _boot(configs, "a", auto_connect=False)

        async def scenario() -> tuple:
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                app.repl.dispatch("print findable")
                await pilot.pause()
                app.repl.dispatch("find findable")
                app.repl.dispatch("run.record screen")
                app._refresh_record_button()
                await pilot.pause()
                find_bar = app.query_one("#find-bar")
                record = sys.modules["termapy.builtins.commands._run_record"]
                assert find_bar.display, "precondition: the bar is up"
                assert record.is_active(), "precondition: recording"
                recording_label = str(app.query_one("#btn-record", Button).label)

                # Act
                app._switch_config(load_config(str(configs["b"])), str(configs["b"]))
                await pilot.pause()

                # Assert
                label = str(app.query_one("#btn-record", Button).label)
                return find_bar.display, record.is_active(), recording_label, label

        bar_up, recording, label_during, label_after = asyncio.run(scenario())
        assert bar_up is False, "the bar closed"
        assert recording is False, "the recording ended"
        assert label_after != label_during, "the button shows it"

    def test_a_script_that_loads_keeps_echo_off(self, configs):
        """config-load-deterministic: /cfg.load inside a script keeps its lines quiet, twice in a row."""
        # Arrange
        target = configs["b"].as_posix()
        for name in ("a", "b"):
            folder = configs[name].parent / "run"
            folder.mkdir(exist_ok=True)
            lines = [f"/cfg.load {target}", f"/print after-load-{name}", ""]
            (folder / "loader.run").write_text(chr(10).join(lines), encoding="utf-8")
        # Normal, not A's verbose: a script started at verbose prints each line it runs.
        app = _boot(configs, "a", auto_connect=False, output_level="normal")

        async def scenario() -> list:
            seen = []
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                for name in ("a", "b"):
                    # Act
                    await _submit(pilot, "/run loader")
                    await until(pilot, lambda n=name: f"after-load-{n}" in app._get_screen_text())
                    await until(pilot, lambda: not app.repl.in_script)
                    await pilot.pause()
                    seen.append(
                        (name, app._get_screen_text(), app.repl.ctx.ns("flags")["echo_repl"])
                    )
            return seen

        # Assert
        for name, text, echo_repl in asyncio.run(scenario()):
            assert f"after-load-{name}" in text, f"{name}: the script ran on past the load"
            assert f"/print after-load-{name}" not in text, f"{name}: its lines stayed without echo"
            assert echo_repl is True, f"{name}: its end restores the TUI default"


class TestAReplayThatLoads:
    """/run.last forces echo on and puts it back after, and a load in the replay must win."""

    def test_the_tui_ends_where_a_fresh_start_would(self, configs):
        """config-load-deterministic: a replayed /cfg.load ends with the TUI's echo, not the one the replay saved."""
        # Arrange
        app = _boot(configs, "a", auto_connect=False)

        async def scenario() -> tuple[bool, str]:
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                await _submit(pilot, f"/cfg.load {configs['b'].as_posix()}")
                await until(pilot, lambda: app.config_path == str(configs["b"]))
                await app.workers.wait_for_complete()
                await _submit(pilot, f"/cfg.load {configs['a'].as_posix()}")
                await until(pilot, lambda: app.config_path == str(configs["a"]))
                await app.workers.wait_for_complete()
                app.repl.dispatch("term.echo_repl off")
                assert _flags(app)["echo_repl"] is False, "precondition: the user turned it off"

                # Act -- the newest line typed on A is the load of B.
                await _submit(pilot, "/run.last 1")
                await until(pilot, lambda: app.config_path == str(configs["b"]))
                await app.workers.wait_for_complete()
                await pilot.pause()
                return _flags(app)["echo_repl"], app.config_path

        # Assert
        echo_repl, path = asyncio.run(scenario())
        assert path == str(configs["b"]), "the replay ran the load"
        assert echo_repl is True, (
            "the new config starts with the TUI's default, as a fresh start does"
        )

    def test_the_cli_ends_where_a_fresh_start_would(self, cli, configs):
        """config-load-deterministic: the same in the CLI, whose load builds a new context under the replay."""
        # Arrange
        cli._dispatch_typed(f"/cfg.load {configs['b'].as_posix()}")
        cli._dispatch_typed(f"/cfg.load {configs['a'].as_posix()}")
        cli.repl.dispatch("term.echo_repl on")
        assert _flags(cli)["echo_repl"] is True, "precondition: the user turned it on"

        # Act
        result = cli._dispatch_typed("/run.last 1")

        # Assert
        assert result.success, result.error
        assert cli.config_path == str(configs["b"]), "the replay ran the load"
        assert _flags(cli)["echo_repl"] is False, "the new config starts with the CLI's default"


class TestAScriptStartsOneWay:
    """A script is not a load: every launch starts it the same way, and it resets nothing."""

    def _script(self, configs, name: str, line: str) -> str:
        """A one-line script in config A's run folder; returns its path."""
        folder = configs["a"].parent / "run"
        folder.mkdir(exist_ok=True)
        script = folder / f"{name}.run"
        script.write_text(line + chr(10), encoding="utf-8")
        return str(script)

    @pytest.mark.parametrize("launch", ["typed", "picked"])
    def test_every_launch_starts_the_same(self, configs, launch):
        """script-start-one-way: typed or picked, twice over, a script sees the variables and its start time."""
        # Arrange
        script = self._script(configs, "show", "/print kept=$(KEPT) when=$(SESSION_DATE)")
        app = _boot(configs, "a", auto_connect=False, output_level="normal")

        async def scenario() -> list[str]:
            seen = []
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                app.repl.dispatch("var.set KEPT 1")
                for _ in range(2):
                    # Act
                    app._clear_output()
                    if launch == "typed":
                        await _submit(pilot, "/run show")
                    else:
                        on_script_picked(app, ("run", script))
                    await until(pilot, lambda: "kept=" in app._get_screen_text())
                    await until(pilot, lambda: not app.repl.in_script)
                    seen.append(app._get_screen_text())
            return seen

        # Assert
        for text in asyncio.run(scenario()):
            assert "kept=1" in text, f"{launch}: the session's variable reached the script"
            assert "when=20" in text, f"{launch}: the start time resolved"

    def test_a_script_leaves_its_changes_behind(self, cli, configs):
        """script-start-one-way: no restore at the end; what a script set stays, its echo hold aside."""
        # Arrange
        script = self._script(configs, "setup", "/var.set LEFT 1")

        # Act
        path, _ = cli.repl.start_script(script)
        assert path, "precondition: the script starts"
        cli.repl.run_script(path)

        # Assert
        assert variables.user_vars(cli.repl.ctx).get("LEFT") == "1", "what it set stays"

    def test_a_script_gets_a_known_start_by_asking(self, cli, configs):
        """script-start-one-way: /cfg.load as a first line drops what the session held."""
        # Arrange
        target = configs["b"].as_posix()
        lines = chr(10).join([f"/cfg.load {target}", "/var.set AFTER 1"])
        script = self._script(configs, "known", lines)
        cli.repl.dispatch("var.set BEFORE 1")

        # Act
        path, _ = cli.repl.start_script(script)
        assert path, "precondition: the script starts"
        cli.repl.run_script(path)

        # Assert
        actual = sorted(variables.user_vars(cli.repl.ctx))
        assert actual == ["AFTER"], "the load took what came before; the script's own line stands"


if __name__ == "__main__":
    _frontend, _scenario, _root, _out = sys.argv[1:5]
    _report = _run_scenario(_frontend, _scenario, Path(_root))
    Path(_out).write_text(json.dumps(_report), encoding="utf-8")
