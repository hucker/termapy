"""What an MCP client can never do to the host: no shell, no rename or delete, no code written.

No flag widens any of it (decision 2026-11-03).  These are limits on termapy's
own commands; a client that can write files some other way is outside them.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import termapy
from termapy.builtins.commands.proto import _write_crc_codegen_files
from termapy.cli import CLITerminal
from termapy.defaults import DEFAULT_CFG
from termapy.legacy import LEGACY_COMMANDS
from termapy.mcp.server import MCPHost
from termapy.plugins.capabilities import ENVIRONMENTS, CapabilitySet, MissingCapability
from termapy.plugins.handles.fs import FilesystemHandle

# Every route to /os a client could try.  {} is the shell command.
_OS_ROUTES = {
    "direct": "/os {}",
    "level_suffix": "/os.silent {}",
    "upper_case": "/OS {}",
    "flag": "/os --quiet {}",
    "repeat": "/repeat count=1 cmd=/os {}",
    "var_capture": "$(X) <- /os {}",
    "cap_text": "/cap.text a.txt timeout=100ms cmd=/os {}",
    "cap_poll": "/cap.poll count=1 regex=. cmd=/os {}",
    "run_profile_cmd": "/run.profile.cmd /os {}",
    "profile_load": "/profile.load cmd=/os {}",
    "script": "/run s.run",
    "name_in_a_variable": "/$(C) {}",
    "plugin_dispatch": "/disp {}",
}

# Runs in its own process, so TERMAPY_OS_CMD_ENABLED is read as it is at a real
# start.  A marker on disk proves the shell ran, whatever the reply says.
_OS_PROBE = r"""
import asyncio, json, sys
from pathlib import Path
from termapy import env_flags
from termapy.cli import CLITerminal
from termapy.defaults import DEFAULT_CFG
from termapy.mcp.server import MCPHost

root, routes = Path(sys.argv[1]), json.loads(sys.argv[2])
assert env_flags.OS_CMD_ENABLED, "the flag must be on for this to prove anything"
marks = root / "marks"
marks.mkdir()
serial = {**DEFAULT_CFG["serial"], "port": "DEMO"}


def shell(tag):
    return f"echo x>{marks / (tag + '.txt')}"


def make(name, **keys):
    path = root / "termapy_cfg" / name / f"{name}.cfg"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({**DEFAULT_CFG, "serial": serial, "echo": False, **keys}))
    return path


control = make("control")
cli = CLITerminal(json.loads(control.read_text()), str(control), no_color=True, term_width=120)
cli.repl.global_root = root
cli.repl.dispatch_full("/os " + shell("control"))

path = make(
    "p",
    on_connect_cmd="/os " + shell("on_connect_cmd"),
    mcp_on_connect_cmd="/os " + shell("mcp_on_connect_cmd"),
)
(path.parent / "run").mkdir()
(path.parent / "run" / "s.run").write_text("/os " + shell("script") + "\n")
(path.parent / "plugin").mkdir()
(path.parent / "plugin" / "disp.py").write_text(
    "from termapy.plugins import Command\n\n\n"
    "def _handler(ctx, args):\n"
    '    return ctx.internal.dispatch("os " + args)\n\n\n'
    'COMMAND = Command(name="disp", args="<x>", help="probe", handler=_handler)\n'
)
host = MCPHost(json.loads(path.read_text()), str(path), verbose=False)
host.repl.global_root = root
host._fire_app_start()


def call(line, confirm=False):
    return asyncio.run(host.run_command_async(line, "normal", 8.0, confirm=confirm))


call("/port.connect")  # also fires the two on-connect keys
call("/var.set C os")
for tag, template in routes.items():
    call(template.format(shell(tag)), confirm=True)
    call("/cap.stop")
print(json.dumps(sorted(mark.stem for mark in marks.iterdir())))
"""


@pytest.fixture(scope="module")
def shells_that_ran(tmp_path_factory) -> list[str]:
    """The markers left by every route to /os, tried with TERMAPY_OS_CMD_ENABLED on."""
    root = tmp_path_factory.mktemp("os_routes")
    probe = root / "probe.py"
    probe.write_text(_OS_PROBE, encoding="utf-8")
    env = {**os.environ, "TERMAPY_OS_CMD_ENABLED": "1"}
    result = subprocess.run(
        [sys.executable, str(probe), str(root), json.dumps(_OS_ROUTES)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=root,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.splitlines()[-1])


_MINE = "# mine\n"  # what each file starts as: valid Python, so the plugin load stays clean
_LEGACY_LINE = f"/{next(iter(LEGACY_COMMANDS))}\n"  # a line /run.legacy --fix would rewrite
_TRAFFIC_PLUGIN = Path(termapy.__file__).parent / "builtins" / "demo" / "plugin" / "traffic.py"


@pytest.fixture
def host(tmp_path) -> Iterator[MCPHost]:
    """An MCP host over a config holding a plugin, a script, a capture and a sibling config."""
    serial = {**DEFAULT_CFG["serial"], "port": "DEMO"}
    cfg = {**DEFAULT_CFG, "serial": serial, "echo": False}
    root = tmp_path / "termapy_cfg"
    path = root / "p" / "p.cfg"
    (root / "other").mkdir(parents=True)
    (root / "other" / "other.cfg").write_text(json.dumps(cfg))
    path.parent.mkdir()
    path.write_text(json.dumps(cfg))
    for folder, name in (
        ("plugin", "keep.py"),
        ("viz", "keep.py"),
        ("run", "keep.run"),
        ("cap", "keep.txt"),
    ):
        (path.parent / folder).mkdir()
        (path.parent / folder / name).write_text(_MINE, encoding="utf-8")
    made = MCPHost(cfg, str(path), verbose=False)
    made.repl.global_root = tmp_path
    made._fire_app_start()
    yield made
    _call(made, "/port.disconnect")  # a test that connected leaves no reader running


def _call(host: MCPHost, line: str, *, confirm: bool = False) -> dict:
    """One ``run_command`` call, as the tool makes it."""
    return asyncio.run(host.run_command_async(line, "normal", 8.0, confirm=confirm))


def _folder(host: MCPHost) -> Path:
    """The active config's folder."""
    return Path(host.ctx.config_path).parent


class TestNoShell:
    @pytest.mark.slow
    def test_the_probe_can_see_a_shell(self, shells_that_ran):
        """os-never-under-mcp: /os does run in the CLI with the flag on, so a clean MCP result means something."""
        assert "control" in shells_that_ran

    @pytest.mark.slow
    @pytest.mark.parametrize("route", [*_OS_ROUTES, "on_connect_cmd", "mcp_on_connect_cmd"])
    def test_no_route_reaches_the_shell(self, shells_that_ran, route):
        """os-never-under-mcp: with TERMAPY_OS_CMD_ENABLED on and confirm=true, no route to /os runs a shell."""
        assert route not in shells_that_ran, f"{route} reached the shell under MCP"

    def test_the_mcp_host_is_never_interactive(self, host):
        """os-never-under-mcp: every gate here rests on this, so granting MCP interactive must be a loud decision."""
        assert host.ctx.capabilities.interactive is False, "the running host"
        assert ENVIRONMENTS["MCP"].interactive is False, "the table /help and the catalog read"

    def test_a_refusal_names_the_reason(self, host):
        """/os under MCP answers with the capability it lacks, not the flag, because the flag would not help."""
        # Act
        result = _call(host, "/os echo hi")

        # Assert
        assert result["success"] is False
        assert "interactive" in result["error"], result["error"]
        assert "TERMAPY_OS_CMD_ENABLED" not in result["error"], "no hint that a flag could open it"


def _file_management(plugins: dict) -> dict:
    """The commands whose verb renames or deletes, by name."""
    return {
        name: plugin
        for name, plugin in plugins.items()
        if name.rsplit(".", 1)[-1] in ("rename", "delete")
    }


def _not_guarded(plugins: dict) -> list[str]:
    """The rename and delete commands a host with no person could run."""
    return sorted(
        name for name, plugin in _file_management(plugins).items() if not plugin.needs.interactive
    )


class TestNoRenameOrDelete:
    def test_every_rename_and_delete_needs_a_person(self, host):
        """mcp-no-file-management: a command whose verb is rename or delete declares interactive, in every family."""
        # Act
        plugins = host.repl._plugins

        # Assert
        assert len(_file_management(plugins)) >= 15, "the walk found the commands it is about"
        assert _not_guarded(plugins) == [], "declare needs=CapabilitySet.MANAGE_FILES on these"

    def test_the_file_management_guard_fires_on_a_probe(self):
        """mcp-no-file-management: the walk really catches a rename or delete with no such need."""
        # Arrange
        loose = SimpleNamespace(needs=CapabilitySet.CONFIG_LOADED)
        held = SimpleNamespace(needs=CapabilitySet.MANAGE_FILES)
        plugins = {"x.delete": loose, "y.rename": loose, "z.delete": held, "x.list": loose}

        # Act
        actual = _not_guarded(plugins)

        # Assert
        assert actual == ["x.delete", "y.rename"], "the loose two, and neither of the others"

    @pytest.mark.parametrize(
        ("line", "survivor"),
        [
            ("/plugin.delete keep", "plugin/keep.py"),
            ("/plugin.rename keep gone", "plugin/keep.py"),
            ("/run.delete keep", "run/keep.run"),
            ("/run.rename keep gone", "run/keep.run"),
            ("/cap.delete keep.txt", "cap/keep.txt"),
            ("/cap.delete *", "cap/keep.txt"),
            ("/cap.rename keep gone", "cap/keep.txt"),
            ("/repeat count=1 cmd=/plugin.delete keep", "plugin/keep.py"),
            ("$(Y) <- /cap.delete keep.txt", "cap/keep.txt"),
        ],
    )
    @pytest.mark.parametrize("confirm", [False, True])
    def test_a_file_stays_put(self, host, line, survivor, confirm):
        """mcp-no-file-management: confirm=true unlocks nothing and no nesting form reaches one."""
        # Act
        _call(host, line, confirm=confirm)

        # Assert
        file = _folder(host) / survivor
        assert file.read_text(encoding="utf-8") == _MINE, f"{line} touched {survivor}"
        assert not (file.parent / f"gone{file.suffix}").exists(), "and nothing new took its place"

    @pytest.mark.parametrize("line", ["/cfg.delete other", "/cfg.rename other gone"])
    def test_another_config_stays_put(self, host, line):
        """mcp-no-file-management: a whole config is the worst case, so confirm=true moves nothing there too."""
        # Act
        result = _call(host, line, confirm=True)

        # Assert
        assert "interactive" in result["error"], result["error"]
        assert (_folder(host).parent / "other" / "other.cfg").exists(), "the folder is still there"


class TestNoCodeWritten:
    @pytest.mark.parametrize(
        "line",
        [
            "/proto.crc.python crc16-xmodem file=plugin/x",
            "/proto.crc.python crc16-xmodem file=viz/x",
            "/proto.crc.python crc16-xmodem file=PLUGIN/x",
            "/proto.crc.python crc16-xmodem file=./plugin/x",
            "/firmware.copy json folder=plugin",
            "/firmware.copy json folder=viz",
            "/board.firmware pico folder=plugin",
            "/proto.crc.python crc16-xmodem file=plugin/sub/x",
            "/firmware.copy json folder=plugin/sub",
            "/firmware.copy json folder=viz/sub/deeper",
            "/firmware.copy json folder=plugin.",
        ],
    )
    def test_a_writer_cannot_land_in_a_code_folder(self, host, line):
        """mcp-no-code-writes: every route that names a folder is refused, in any spelling of its name."""
        # Arrange
        before = {path.name for path in _folder(host).rglob("*")}

        # Act
        result = _call(host, line)

        # Assert
        assert result["success"] is False, line
        assert "holds code" in result["error"], result["error"]
        assert {path.name for path in _folder(host).rglob("*")} == before, "and nothing was written"

    @pytest.mark.parametrize("name", ["plugin.", "plugin..", "plugin::$INDEX_ALLOCATION"])
    @pytest.mark.parametrize(
        "line", ["/firmware.copy mem folder={}", "/board.firmware pico folder={}"]
    )
    def test_a_spelling_windows_folds_is_refused_with_the_folder_absent(self, host, line, name):
        """mcp-no-code-writes: with plugin/ absent no OS normalizes the name first, and Windows would make plugin/ from it."""
        # Arrange
        shutil.rmtree(_folder(host) / "plugin")

        # Act
        result = _call(host, line.format(name))

        # Assert
        assert "holds code" in result["error"], result["error"]
        assert not (_folder(host) / "plugin").exists(), "and the folder was not created"

    @pytest.mark.parametrize("route", ["/port {}", "/cfg.auto port {}"])
    def test_a_spy_log_cannot_leave_cap(self, host, route):
        """spy-log-in-cap: a spy:// port can reach neither a code folder nor anywhere else, by any route."""
        # Arrange
        victim = _folder(host) / "plugin" / "keep.py"

        for name in ("../plugin/keep.py", str(victim)):
            url = f"spy://NO-SUCH-PORT?file={name}"

            # Act
            _call(host, route.format(url))
            _call(host, "/port.connect")

            # Assert
            assert victim.read_text(encoding="utf-8") == _MINE, f"{name} left the file alone"

    def test_a_saved_profile_cannot_land_in_a_code_folder(self, host):
        """mcp-no-code-writes: /profile.save measures from the working folder, so the guard reads the real path."""
        # Arrange
        demo = Path(termapy.__file__).parent / "builtins" / "demo" / "demo_legacy.profile.json"
        shutil.copy(demo, _folder(host) / "x.profile.json")
        assert _call(host, "/profile.load x.profile.json")["success"] is True, "a profile to save"

        # Act
        with contextlib.chdir(_folder(host)):
            result = _call(host, "/profile.save plugin/x.py")

        # Assert
        assert "holds code" in result["error"], result["error"]
        assert not (_folder(host) / "plugin" / "x.py").exists(), "nothing was written"

    def test_the_sandbox_flag_does_not_lift_it(self, host):
        """mcp-no-code-writes: TERMAPY_MCP_FS_UNCONFINED widens paths, never this, so ../plugin stays refused."""
        # Arrange
        host.ctx.capabilities = replace(host.ctx.capabilities, filesystem_unconfined=True)
        host.ctx.sync_capabilities()
        assert _call(host, "/port.connect")["success"] is True, "a capture needs a port"

        # Act
        result = _call(host, "/cap.text ../plugin/x.py timeout=100ms")

        # Assert
        assert "holds code" in result["error"], result["error"]
        assert not (_folder(host) / "plugin" / "x.py").exists(), "nothing was written"

    def test_a_person_still_can(self, tmp_path):
        """mcp-no-code-writes: the TUI and CLI keep it, because the operator is the caller on their own machine."""
        # Arrange
        path = tmp_path / "c" / "c.cfg"
        path.parent.mkdir()
        (path.parent / "plugin").mkdir()
        path.write_text(json.dumps(DEFAULT_CFG))
        cli = CLITerminal(dict(DEFAULT_CFG), str(path), no_color=True, term_width=120)
        cli.repl.global_root = tmp_path

        # Act
        result = cli.repl.dispatch_full("/firmware.copy json folder=plugin")

        # Assert
        assert result.success is True, result.error
        assert (path.parent / "plugin" / "termapy_json.c").exists(), "the file landed"


class TestReplacing:
    def test_a_capture_replaces_inside_the_folder(self, host):
        """mcp-no-code-writes: the config's folder is the agent's to change, so reusing a name starts it over, twice."""
        # Arrange
        assert _call(host, "/port.connect")["success"] is True, "a capture needs a port"

        # Act
        first = _call(host, "/cap.text keep.txt timeout=100ms")
        _call(host, "/cap.stop")
        second = _call(host, "/cap.text keep.txt timeout=100ms")
        _call(host, "/cap.stop")

        # Assert
        assert first["success"] is True, first["error"]
        assert second["success"] is True, second["error"]
        capture = _folder(host) / "cap" / "keep.txt"
        assert capture.read_text(encoding="utf-8") != _MINE, "the old text is gone"

    def test_a_saved_profile_replaces_inside_the_folder(self, host):
        """mcp-no-code-writes: an agent can update its config's profile, by the bare save and by a name."""
        # Arrange
        demo = Path(termapy.__file__).parent / "builtins" / "demo" / "demo_legacy.profile.json"
        shutil.copy(demo, _folder(host) / "x.profile.json")
        assert _call(host, "/profile.load x.profile.json")["success"] is True, "a profile to save"

        # Act
        bare_first = _call(host, "/profile.save")
        bare_second = _call(host, "/profile.save")
        with contextlib.chdir(_folder(host)):
            named = _call(host, "/profile.save cap/keep.txt")

        # Assert
        assert bare_first["success"] is True, bare_first["error"]
        assert bare_second["success"] is True, bare_second["error"]
        assert named["success"] is True, named["error"]
        capture = _folder(host) / "cap" / "keep.txt"
        assert capture.read_text(encoding="utf-8") != _MINE, "the named save landed"

    @pytest.mark.parametrize(
        "line",
        [
            "/cap.text {victim} timeout=100ms",
            "/proto.crc.python crc16-xmodem file={stem}",
            "/run.legacy {victim} --fix",
            "/profile.save {victim}",
        ],
    )
    def test_a_file_outside_the_folder_is_not_replaced(self, host, tmp_path, line):
        """mcp-no-code-writes: even with host-wide paths on, the one way to aim outside, a file there is never replaced."""
        # Arrange
        host.ctx.capabilities = replace(host.ctx.capabilities, filesystem_unconfined=True)
        host.ctx.sync_capabilities()
        demo = Path(termapy.__file__).parent / "builtins" / "demo" / "demo_legacy.profile.json"
        shutil.copy(demo, _folder(host) / "x.profile.json")
        assert _call(host, "/profile.load x.profile.json")["success"] is True, "a profile to save"
        assert _call(host, "/port.connect")["success"] is True, "a capture needs a port"
        victim = tmp_path / "victim.py"
        victim.write_text(_LEGACY_LINE, encoding="utf-8")

        # Act
        result = _call(host, line.format(victim=victim, stem=victim.with_suffix("")))

        # Assert
        assert "File exists: victim.py" in result["error"], result["error"]
        assert victim.read_text(encoding="utf-8") == _LEGACY_LINE, "the file is untouched"

    def test_another_configs_file_is_outside_too(self, host):
        """mcp-no-code-writes: a sibling config is not this config's to change, whatever folder it shares."""
        # Arrange
        host.ctx.capabilities = replace(host.ctx.capabilities, filesystem_unconfined=True)
        host.ctx.sync_capabilities()
        assert _call(host, "/port.connect")["success"] is True, "a capture needs a port"
        sibling = _folder(host).parent / "other" / "other.cfg"
        before = sibling.read_text(encoding="utf-8")

        # Act
        result = _call(host, "/cap.text ../../other/other.cfg timeout=100ms")

        # Assert
        assert "File exists: other.cfg" in result["error"], result["error"]
        assert sibling.read_text(encoding="utf-8") == before, "the sibling's config is untouched"

    def test_a_capture_may_still_append(self, host):
        """mcp-no-code-writes: an append destroys nothing, so a client can add to its own capture."""
        # Arrange
        assert _call(host, "/port.connect")["success"] is True, "a capture needs a port"

        # Act
        result = _call(host, "/cap.text keep.txt timeout=100ms mode=append")

        # Assert
        assert result["success"] is True, result["error"]

    def test_generated_code_replaces_inside_the_folder(self, host):
        """mcp-no-code-writes: a second run of one name writes over the first, in firmware/."""
        # Act
        first = _call(host, "/proto.crc.python crc16-xmodem file=gen")
        second = _call(host, "/proto.crc.python crc16-xmodem file=gen")

        # Assert
        assert first["success"] is True, first["error"]
        assert second["success"] is True, second["error"]

    def test_a_pair_is_refused_whole(self, tmp_path):
        """A refused second file leaves no half of a C pair, because every file is asked about first."""

        # Arrange
        def guard(path: Path) -> None:
            if path.suffix == ".c":
                raise MissingCapability("File exists: x.c")

        # Act / Assert
        with pytest.raises(MissingCapability):
            _write_crc_codegen_files(("header", "source"), "c", "x", tmp_path, guard)
        assert list(tmp_path.iterdir()) == [], "the header was not written either"


_PROTO_TEST = """
frame_gap = "50ms"

[[test]]
name = "t"
send = '"AT\\r"'
expect = '"OK\\r\\n"'
"""


def _script(folder: Path, json_file: str) -> Path:
    """A one-test ``.pro`` in ``folder`` naming where its results go."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "s.pro"
    path.write_text(f"json_file = {json.dumps(json_file)}\n{_PROTO_TEST}", encoding="utf-8")
    return path


class TestProtoRunResults:
    @pytest.fixture
    def connected(self, host) -> MCPHost:
        """The host with the DEMO port open, so a script could really run."""
        assert _call(host, "/port.connect")["success"] is True, "a script needs a port"
        return host

    @pytest.mark.parametrize(
        "name",
        ["../../plugin/x.py", "..\\..\\plugin\\x.py", "C:x.json", "x.json:s", ".."],
    )
    def test_a_script_cannot_choose_the_folder(self, connected, name):
        """proto-results-in-folder: a json_file holding a folder is refused before the script runs at all."""
        # Arrange
        _script(_folder(connected) / "proto", name)

        # Act
        result = _call(connected, "/proto.run s")

        # Assert
        assert "Invalid results file name" in result["error"], result["error"]
        assert result["data"] is None, "no test ran, so no bytes went to the device"
        assert not (_folder(connected) / "proto" / "test").exists(), "and nothing was written"
        assert not (_folder(connected) / "plugin" / "x.py").exists(), "least of all there"

    def test_a_script_cannot_write_by_absolute_path(self, connected, tmp_path):
        """proto-results-in-folder: an absolute json_file leaves proto/test/, so the file outside stays as it was."""
        # Arrange
        victim = tmp_path / "victim.json"
        victim.write_text(_MINE, encoding="utf-8")
        _script(_folder(connected) / "proto", str(victim))

        # Act
        result = _call(connected, "/proto.run s")

        # Assert
        assert "Invalid results file name" in result["error"], result["error"]
        assert victim.read_text(encoding="utf-8") == _MINE, "the file outside is untouched"

    def test_results_may_go_in_a_subfolder(self, connected):
        """proto-results-in-folder: a json_file below proto/test/ lands there, for an agent as for a person."""
        # Arrange
        _script(_folder(connected) / "proto", "nightly/out.json")

        # Act
        result = _call(connected, "/proto.run s")

        # Assert
        assert result["success"] is True, result["error"]
        written = _folder(connected) / "proto" / "test" / "nightly" / "out.json"
        assert written.exists(), "in proto/test/nightly/"

    def test_a_run_replaces_an_earlier_results_file(self, connected):
        """mcp-no-code-writes: json_file naming an existing file in proto/test/ writes over it."""
        # Arrange
        earlier = _folder(connected) / "proto" / "test" / "fixed.json"
        earlier.parent.mkdir(parents=True)
        earlier.write_text(_MINE, encoding="utf-8")
        _script(_folder(connected) / "proto", "fixed.json")

        # Act
        result = _call(connected, "/proto.run s")

        # Assert
        assert result["success"] is True, result["error"]
        document = json.loads(earlier.read_text(encoding="utf-8"))
        assert document["summary"]["passed"] == 1, "the results replaced the old text"

    def test_a_fixed_name_runs_twice(self, connected):
        """mcp-no-code-writes: an agent can re-run a script with a fixed json_file, and gets the results both times."""
        # Arrange
        _script(_folder(connected) / "proto", "{proto_name}_out.json")

        # Act
        first = _call(connected, "/proto.run s")
        second = _call(connected, "/proto.run s")

        # Assert
        assert first["success"] is True, first["error"]
        assert second["success"] is True, second["error"]
        assert second["data"]["summary"]["passed"] == 1, "the agent gets the results as data"
        written = _folder(connected) / "proto" / "test" / "s_out.json"
        assert json.loads(written.read_text(encoding="utf-8"))["summary"]["passed"] == 1, "on disk"

    def test_results_beside_an_outside_script_are_not_replaced(self, connected, tmp_path):
        """mcp-no-code-writes: with host-wide paths on, results go beside the script, and a file there stays."""
        # Arrange
        connected.ctx.capabilities = replace(connected.ctx.capabilities, filesystem_unconfined=True)
        connected.ctx.sync_capabilities()
        script = _script(tmp_path / "outside", "beside.json")
        earlier = tmp_path / "outside" / "test" / "beside.json"
        earlier.parent.mkdir()
        earlier.write_text(_MINE, encoding="utf-8")

        # Act
        result = _call(connected, f"/proto.run {script}")

        # Assert
        assert "File exists: beside.json" in result["error"], result["error"]
        assert earlier.read_text(encoding="utf-8") == _MINE, "the file outside is untouched"

    @pytest.mark.parametrize("where", ["outside", "plugin"])
    def test_results_stay_in_the_configs_proto_test(self, connected, tmp_path, where):
        """writes-in-cfg-folder: wherever the script came from, a confined host's results land in proto/test/."""
        # Arrange
        folder = tmp_path / "outside" if where == "outside" else _folder(connected) / "plugin"
        script = _script(folder, "beside.json")

        # Act
        result = _call(connected, f"/proto.run {script}")

        # Assert
        assert result["success"] is True, result["error"]
        assert not (folder / "test").exists(), "nothing beside the script"
        expected = _folder(connected) / "proto" / "test" / "beside.json"
        assert expected.exists(), "in the config's own folder"

    def test_the_sandbox_flag_does_not_open_a_code_folder(self, connected):
        """mcp-no-code-writes: with host-wide paths on, results go beside the script again, and plugin/ stays refused."""
        # Arrange
        connected.ctx.capabilities = replace(connected.ctx.capabilities, filesystem_unconfined=True)
        connected.ctx.sync_capabilities()
        script = _script(_folder(connected) / "plugin", "beside.json")

        # Act
        result = _call(connected, f"/proto.run {script}")

        # Assert
        assert "holds code" in result["error"], result["error"]
        assert not (_folder(connected) / "plugin" / "test").exists(), "nothing was written"


class TestRunLegacy:
    @pytest.fixture
    def old(self, host) -> Path:
        """A script in run/ naming a renamed command, so --fix has a line to rewrite."""
        path = _folder(host) / "run" / "old.run"
        path.write_text(_LEGACY_LINE, encoding="utf-8")
        return path

    @pytest.mark.parametrize("name", ["old", "old*"])
    def test_a_fix_rewrites_a_script_in_run(self, host, old, name):
        """mcp-no-code-writes: run/ is the config's own, so --fix works there, and a second report finds nothing."""
        # Act
        report = _call(host, f"/run.legacy {name}")
        fix = _call(host, f"/run.legacy {name} --fix")
        again = _call(host, f"/run.legacy {name}")

        # Assert
        assert report["value"] == "1", "the report found the line"
        assert fix["success"] is True, fix["error"]
        assert old.read_text(encoding="utf-8") != _LEGACY_LINE, "the script was rewritten"
        assert again["value"] == "0", "nothing left to fix"

    def test_fixing_a_clean_script_is_not_a_failure(self, host):
        """mcp-no-code-writes: a script with no hits writes nothing, and says so."""
        # Arrange
        (_folder(host) / "run" / "clean.run").write_text("/print clean\n", encoding="utf-8")

        # Act
        result = _call(host, "/run.legacy clean --fix")

        # Assert
        assert result["success"] is True, result["error"]
        assert result["value"] == "0", "no hits"

    @pytest.mark.parametrize("fix", ["", " --fix"])
    def test_a_name_cannot_leave_run(self, host, tmp_path, fix):
        """mcp-no-code-writes: neither a climb into plugin/ nor an absolute path reaches a file, to read or to rewrite."""
        # Arrange
        in_plugin = _folder(host) / "plugin" / "victim.py"
        outside = tmp_path / "victim.txt"
        for path in (in_plugin, outside):
            path.write_text(_LEGACY_LINE, encoding="utf-8")

        for name, path in (("../plugin/victim.py", in_plugin), (str(outside), outside)):
            # Act
            result = _call(host, f"/run.legacy {name}{fix}")

            # Assert
            assert "sandbox" in result["error"], result["error"]
            assert "run/" in result["error"], "the refusal names the folder as it is on disk"
            assert path.read_text(encoding="utf-8") == _LEGACY_LINE, "the file is untouched"

    def test_the_sandbox_flag_does_not_lift_it(self, host, tmp_path):
        """mcp-no-code-writes: with host-wide paths on, --fix still rewrites nothing in plugin/ or outside the folder."""
        # Arrange
        host.ctx.capabilities = replace(host.ctx.capabilities, filesystem_unconfined=True)
        host.ctx.sync_capabilities()
        in_plugin = _folder(host) / "plugin" / "victim.py"
        outside = tmp_path / "victim.txt"
        for path in (in_plugin, outside):
            path.write_text(_LEGACY_LINE, encoding="utf-8")

        # Act
        code = _call(host, "/run.legacy ../plugin/victim.py --fix")
        away = _call(host, f"/run.legacy {outside} --fix")

        # Assert
        assert "holds code" in code["error"], code["error"]
        assert "File exists: victim.txt" in away["error"], away["error"]
        assert in_plugin.read_text(encoding="utf-8") == _LEGACY_LINE, "the plugin is untouched"
        assert outside.read_text(encoding="utf-8") == _LEGACY_LINE, "the file outside is untouched"

    def test_a_person_can_still_fix_a_script(self, tmp_path):
        """mcp-no-code-writes: the TUI and CLI keep --fix, and the working folder as a place to find the script."""
        # Arrange
        path = tmp_path / "c" / "c.cfg"
        path.parent.mkdir()
        path.write_text(json.dumps(DEFAULT_CFG))
        cli = CLITerminal(dict(DEFAULT_CFG), str(path), no_color=True, term_width=120)
        cli.repl.global_root = tmp_path
        here = tmp_path / "here"
        here.mkdir()
        script = here / "old.run"
        script.write_text(_LEGACY_LINE, encoding="utf-8")

        # Act
        with contextlib.chdir(here):
            result = cli.repl.dispatch_full("/run.legacy old --fix")

        # Assert
        assert result.success is True, result.error
        assert script.read_text(encoding="utf-8") != _LEGACY_LINE, "the script was rewritten"


class TestDemoTraffic:
    @pytest.fixture
    def traffic(self, host) -> MCPHost:
        """The host with the demo traffic plugin loaded and the port open, as a copied plugin would be."""
        shutil.copy(_TRAFFIC_PLUGIN, _folder(host) / "plugin" / "traffic.py")
        host._fire_app_start()  # resolves the plugin folders again, picking it up
        assert _call(host, "/port.connect")["success"] is True, "a hexdump needs a port"
        return host

    def test_a_hexdump_lands_in_cap(self, traffic):
        """writes-in-cfg-folder: the plugin authors copy writes a new relative name under cap/."""
        # Act
        result = _call(traffic, "/traffic.hexdump dump.txt 50ms")

        # Assert
        assert result["success"] is True, result["error"]
        assert (_folder(traffic) / "cap" / "dump.txt").exists(), "in the config's cap/"

    def test_a_hexdump_replaces_inside_the_folder(self, traffic):
        """mcp-no-code-writes: the plugin authors copy reuses a name in cap/ the way a capture does."""
        # Act
        result = _call(traffic, "/traffic.hexdump keep.txt 50ms")

        # Assert
        assert result["success"] is True, result["error"]
        capture = _folder(traffic) / "cap" / "keep.txt"
        assert capture.read_text(encoding="utf-8") != _MINE, "the old text is gone"

    def test_a_hexdump_cannot_reach_a_code_folder(self, traffic):
        """mcp-no-code-writes: a climb into plugin/ is refused, so the plugin cannot overwrite itself."""
        # Arrange
        plugin = _folder(traffic) / "plugin" / "keep.py"

        # Act
        result = _call(traffic, "/traffic.hexdump ../plugin/keep.py 50ms")

        # Assert
        assert "sandbox" in result["error"], result["error"]
        assert plugin.read_text(encoding="utf-8") == _MINE, "the file is untouched"


class TestTheGuards:
    def test_a_person_passes_both(self, tmp_path):
        """A host with a person is the caller on their own machine, so neither guard refuses."""
        # Arrange
        handle = FilesystemHandle(capabilities=CapabilitySet.INTERACTIVE)
        existing = tmp_path / "plugin" / "x.py"
        existing.parent.mkdir()
        existing.write_text("x", encoding="utf-8")

        # Act / Assert
        handle.guard_code_folder(existing.parent)
        handle.guard_replace(existing)

    @pytest.mark.parametrize("name", ["plugin", "viz", "PLUGIN", "Viz"])
    def test_a_code_folder_is_refused_in_any_case(self, tmp_path, name):
        """Windows and macOS read Plugin/ as plugin/, so the case must not matter."""
        # Arrange
        handle = FilesystemHandle(capabilities=CapabilitySet())

        # Act / Assert
        with pytest.raises(MissingCapability, match="holds code"):
            handle.guard_code_folder(tmp_path / name)

    def test_replacing_inside_the_config_folder_passes(self, tmp_path):
        """guard_replace lets a file in the config's folder be written over, at any depth."""
        # Arrange
        root = tmp_path / "termapy_cfg"
        handle = FilesystemHandle(capabilities=CapabilitySet(), cap_dir=root / "c" / "cap")
        deep = root / "c" / "proto" / "test" / "x.json"
        deep.parent.mkdir(parents=True)
        deep.write_text("x", encoding="utf-8")

        # Act / Assert
        handle.guard_replace(deep)

    @pytest.mark.parametrize("name", ["other/x.cfg", "x.json", "../x.json"])
    def test_replacing_outside_the_config_folder_is_refused(self, tmp_path, name):
        """guard_replace refuses a sibling config's file, a file at the root, and anything above it."""
        # Arrange
        root = tmp_path / "project" / "termapy_cfg"
        handle = FilesystemHandle(capabilities=CapabilitySet(), cap_dir=root / "c" / "cap")
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

        # Act / Assert
        with pytest.raises(MissingCapability, match="File exists"):
            handle.guard_replace(path)

    def test_with_no_config_nothing_is_inside(self, tmp_path):
        """With no config the data folders are the working folder, which must not count as the config's own."""
        # Arrange
        handle = FilesystemHandle(capabilities=CapabilitySet())
        path = tmp_path / "x.txt"
        path.write_text("x", encoding="utf-8")

        # Act / Assert
        with contextlib.chdir(tmp_path), pytest.raises(MissingCapability, match="File exists"):
            handle.guard_replace(path)

    @pytest.mark.parametrize("name", ["plugin.", "viz..", "plugin. ", "PLUGIN::$INDEX_ALLOCATION"])
    def test_a_spelling_windows_folds_is_refused(self, tmp_path, name):
        """Windows makes plugin/ from each of these, and resolve() keeps them as typed while the folder is absent."""
        # Arrange
        handle = FilesystemHandle(capabilities=CapabilitySet())

        # Act / Assert
        with pytest.raises(MissingCapability, match="holds code"):
            handle.guard_code_folder(tmp_path / name)

    @pytest.mark.parametrize("below", ["c/plugin/sub", "c/viz/a/b", "plugin/sub", "c/plugin./sub"])
    def test_a_folder_below_a_code_folder_is_refused(self, tmp_path, below):
        """plugin/sub/ is still in plugin/, in a config's layer and in the global one at the config root."""
        # Arrange
        root = tmp_path / "termapy_cfg"
        handle = FilesystemHandle(capabilities=CapabilitySet(), cap_dir=root / "c" / "cap")

        # Act / Assert
        with pytest.raises(MissingCapability, match="holds code"):
            handle.guard_code_folder(root / below)

    def test_a_project_inside_a_folder_named_plugin_is_ordinary(self, tmp_path):
        """A folder named plugin above the config root is where a project lives, so its captures still work."""
        # Arrange
        root = tmp_path / "plugin" / "viz" / "termapy_cfg"
        handle = FilesystemHandle(capabilities=CapabilitySet(), cap_dir=root / "c" / "cap")

        # Act / Assert
        handle.guard_code_folder(root / "c" / "cap")
        handle.guard_code_folder(root / "c" / "firmware")

    def test_another_folder_passes(self, tmp_path):
        """Only the code folders are refused, so a capture or firmware folder is ordinary."""
        # Arrange
        handle = FilesystemHandle(capabilities=CapabilitySet())

        # Act / Assert
        handle.guard_code_folder(tmp_path / "firmware")
        handle.guard_replace(tmp_path / "firmware" / "new.c")
