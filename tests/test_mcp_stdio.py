"""End-to-end MCP over real stdio: the wire an actual client speaks.

Everything else in the MCP suite calls ``run_command_async`` directly on
an in-process host; this file is the one place the SDK wiring layer --
``run_mcp_stdio``, the tool/resource decorators, the stdio transport --
is exercised the way Claude Desktop or Claude Code exercises it: a
subprocess, JSON-RPC 2.0 frames on stdin/stdout, nothing shared.

One test, one conversation: spawning the server is the expensive part,
so the whole agent loop runs in a single session -- initialize, discover
the tool, run a termapy command, drive a plain device through request
mode, read a resource.  Port enumeration is pinned to the demo fleet via
``TERMAPY_DEMO_FLEET`` (the env layer exists precisely because injection
cannot reach through a process boundary).
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="mcp SDK not installed; install with [mcp] extra")

pytestmark = pytest.mark.slow  # subprocess spawn + real stdio round-trips

from termapy.defaults import DEFAULT_CFG  # noqa: E402


class _StdioClient:
    """A minimal JSON-RPC 2.0 client over a subprocess's stdio."""

    def __init__(
        self, args: list[str], *, project: Path | None = None, cwd: Path | None = None
    ) -> None:
        import os

        env = dict(os.environ)
        # Deterministic ports: the subprocess enumerates the demo fleet,
        # not this machine's hardware.
        env["TERMAPY_DEMO_FLEET"] = "1"
        # What finds the config is what this test sets, not what the test run inherited.
        env.pop("CLAUDE_PROJECT_DIR", None)
        env.pop("TERMAPY_CFG_DIR", None)
        if project is not None:
            env["CLAUDE_PROJECT_DIR"] = str(project)
        argv = ["termapy", "--mcp", *args]
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                f"import sys; sys.argv = {argv!r}; from termapy.entry import main; main()",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=cwd,
        )
        self._lines: queue.Queue[str] = queue.Queue()
        self._id = 0
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        stdout = self.proc.stdout
        assert stdout is not None, "opened with stdout=PIPE"
        for line in stdout:
            self._lines.put(line)

    def rpc(
        self,
        method: str,
        params: dict | None = None,
        *,
        notify: bool = False,
        timeout: float = 20.0,
    ):
        """Send one request (or notification) and return the response."""
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self._id += 1
            msg["id"] = self._id
        stdin = self.proc.stdin
        assert stdin is not None, "opened with stdin=PIPE"
        stdin.write(json.dumps(msg) + "\n")
        stdin.flush()
        if notify:
            return None
        while True:
            line = self._lines.get(timeout=timeout)
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue  # non-frame noise is not ours to fail on
            if obj.get("id") == self._id:
                return obj

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def _run_command(client: _StdioClient, command: str) -> dict:
    """tools/call run_command and parse the envelope out of the reply."""
    reply = client.rpc(
        "tools/call",
        {
            "name": "run_command",
            "arguments": {"command": command, "output": "normal", "timeout_s": 5},
        },
    )
    content = reply["result"]["content"][0]["text"]
    return json.loads(content)


ENVELOPE_KEYS = {
    "cmd",
    "success",
    "error",
    "value",
    "data",
    "elapsed_s",
    "output_lines",
    "captured_artifacts",
    "async_events",
}


class TestMcpStdioEndToEnd:
    def test_full_agent_conversation(self, tmp_path):
        """A real client over stdio can handshake, discover, run commands and read state."""
        # Arrange -- a demo-device cfg the server can auto-connect to.
        cfg = json.loads(json.dumps(DEFAULT_CFG))
        cfg["serial"]["port"] = "DEMO"
        cfg["auto_connect"] = True
        cfg["tui_on_connect_cmd"] = ""
        cfg["cli_on_connect_cmd"] = ""
        slot = tmp_path / "probe"
        slot.mkdir()
        cfg_file = slot / "probe.cfg"
        cfg_file.write_text(json.dumps(cfg))
        for sub in ("plugin", "ss", "run", "cap"):
            (slot / sub).mkdir()
        client = _StdioClient([str(cfg_file), "--cfg-dir", str(tmp_path)])

        try:
            # Act / Assert -- one conversation, the way an agent has it.

            # 1. Handshake.
            init = client.rpc(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest-stdio", "version": "0"},
                },
            )
            server_name = init["result"]["serverInfo"]["name"]
            assert server_name == "termapy", "server identifies itself"
            client.rpc("notifications/initialized", {}, notify=True)

            # 2. Discovery.
            tools = client.rpc("tools/list", {})
            tool_names = [tool["name"] for tool in tools["result"]["tools"]]
            assert "run_command" in tool_names, "the one tool is published"

            # 3. A termapy command: structured records over the wire.
            envelope = _run_command(client, "/port.list")
            assert set(envelope.keys()) == ENVELOPE_KEYS, "wire envelope is the fixed nine keys"
            devices = [record["device"] for record in envelope["data"]]
            assert devices == ["COM3", "COM4", "COM7"], (
                "demo fleet records in data (TERMAPY_DEMO_FLEET reached "
                "through the process boundary)"
            )

            # 4. The plain-device loop: request mode drives a text device.
            bare = _run_command(client, "AT")
            assert bare["success"] is True, "bare send succeeds"
            assert bare["value"] == "", (
                "without request mode a bare device command is "
                "fire-and-forget: the agent learns nothing"
            )
            on = _run_command(client, "/term.request on")
            assert on["value"] is True, (
                "session flipped to request mode; a switch is a boolean on the wire"
            )
            at = _run_command(client, "AT")
            assert at["success"] is True, "request/response exchange succeeds"
            assert at["value"] == "OK", (
                "plain text device answers in value -- the agent now has the reply"
            )
            assert at["data"] is None, "text reply carries no structure"

            # 5. A resource read.
            res = client.rpc("resources/read", {"uri": "termapy://device_state.json"})
            state = json.loads(res["result"]["contents"][0]["text"])
            assert state["last_command"]["cmd"] == "AT", "device_state tracks the conversation"
        finally:
            client.close()


def _write_config(root: Path, name: str, port: str) -> None:
    """A config on disk: ``root/<name>/<name>.cfg``."""
    cfg = json.loads(json.dumps(DEFAULT_CFG))
    cfg["serial"]["port"] = port
    path = root / name / f"{name}.cfg"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(cfg))


def _instructions(client: _StdioClient) -> str:
    """Handshake, and return what the server told the agent on connecting."""
    init = client.rpc(
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest-stdio", "version": "0"},
        },
    )
    client.rpc("notifications/initialized", {}, notify=True)
    return init["result"]["instructions"]


class TestFindsItsConfig:
    @pytest.mark.rule("mcp-finds-its-config")
    def test_two_bare_servers_are_two_free_slots(self, tmp_path):
        """termapy-a and termapy-b debug two ports at once: each holds nothing until told, then its own device."""
        # Arrange -- the two configs open different simulated devices.
        root = tmp_path / "termapy_cfg"
        for name, port in (("left", "DEMO"), ("right", "DEMO_JSON")):
            _write_config(root, name, port)
            path = root / name / f"{name}.cfg"
            path.write_text(json.dumps({**json.loads(path.read_text()), "auto_connect": True}))
        first = _StdioClient([], project=tmp_path)
        second = _StdioClient([], project=tmp_path)

        # Act / Assert
        try:
            assert _instructions(first).startswith("No config is loaded."), "holds no port yet"
            assert _instructions(second).startswith("No config is loaded."), "nor does the other"
            assert _run_command(first, "/cfg.load left")["success"] is True
            assert _run_command(second, "/cfg.load right")["success"] is True
            assert _run_command(first, "/port.info")["value"] == "DEMO", "each has its own"
            assert _run_command(second, "/port.info")["value"] == "DEMO_JSON", "and keeps it"
            assert _run_command(first, "/cfg.load right")["success"] is True, "and can move on"
            assert _run_command(second, "/port.info")["value"] == "DEMO_JSON", "untouched by that"
        finally:
            first.close()
            second.close()

    @pytest.mark.rule("mcp-finds-its-config")
    def test_a_missing_named_config_stops_there(self, tmp_path):
        """A name given at launch is the only choice, so a typo exits instead of landing on another device's config."""
        # Arrange -- an mcp_* it could wrongly fall to.
        root = tmp_path / "termapy_cfg"
        _write_config(root, "mcp_board", "DEMO")
        client = _StdioClient(["mcp_bord"], project=tmp_path)

        # Act
        returncode = client.proc.wait(timeout=20)
        stderr = client.proc.stderr
        assert stderr is not None, "opened with stderr=PIPE"
        message = stderr.read()

        # Assert
        assert returncode == 1, "the client shows a failed server"
        assert "No config matching 'mcp_bord'" in message, message
        assert "did you mean mcp_board?" in message, "and the likely name, as every typed name gets"

    @pytest.mark.rule("mcp-finds-its-config")
    def test_a_config_named_by_path_brings_its_folder(self, tmp_path):
        """The command line outranks the search, so the folder is the named config's, not the project's."""
        # Arrange -- the project has its own configs, which must not be offered.
        project = tmp_path / "project"
        _write_config(project / "termapy_cfg", "mcp_here", "DEMO")
        other = tmp_path / "other" / "termapy_cfg"
        _write_config(other, "left", "")
        _write_config(other, "right", "")
        client = _StdioClient([str(other / "left" / "left.cfg")], project=project)

        # Act / Assert
        try:
            told = _instructions(client)
            assert told.startswith("Active config: left."), told
            listed = _run_command(client, "/cfg.list")
            names = [record["name"] for record in listed["data"]]
            assert sorted(names) == ["left", "right"], "its siblings, not the search's"
        finally:
            client.close()

    @pytest.mark.rule("mcp-finds-its-config")
    def test_enabled_in_a_project_it_finds_the_config_there(self, tmp_path):
        """Bare --mcp reads CLAUDE_PROJECT_DIR over the working folder, walks the chain and tells the agent."""
        # Arrange -- the folder is at the second place searched, and the
        # working folder is somewhere else entirely.
        project = tmp_path / "project"
        root = project / "src" / "termapy_cfg"
        _write_config(root, "mcp_a", "DEMO_FAIL")
        _write_config(root, "mcp_b", "DEMO")
        _write_config(root, "other", "")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        client = _StdioClient([], project=project, cwd=elsewhere)

        # Act / Assert
        try:
            told = _instructions(client)
            assert told.startswith("Active config: mcp_b. Skipped mcp_a: Cannot open DEMO_FAIL"), (
                told
            )
            assert "mcp_a, mcp_b, other" in told, "and what else it may load"
            loaded = _run_command(client, "/cfg.load other")
            assert loaded["success"] is True, loaded["error"]
            refused = _run_command(client, "/cfg.load src/termapy_cfg/mcp_b/mcp_b.cfg")
            assert refused["success"] is False, "a bare name, never a path"
        finally:
            client.close()

    @pytest.mark.rule("mcp-finds-its-config")
    def test_a_project_with_no_folder_stays_out_of_the_os_folder(self, tmp_path):
        """No folder means no configs, so a user-scope server sees nothing of the user's other projects."""
        # Arrange
        project = tmp_path / "project"
        project.mkdir()
        client = _StdioClient([], project=project, cwd=tmp_path)

        # Act / Assert
        try:
            told = _instructions(client)
            assert told.startswith("No config is loaded. No configs in "), told
            listed = _run_command(client, "/cfg.list")
            assert listed["data"] == [], "and the OS config folder's are not offered"
            assert not (project / "termapy_cfg").exists(), "looking created nothing"
        finally:
            client.close()

    @pytest.mark.rule("mcp-finds-its-config")
    def test_several_with_no_mcp_name_starts_with_none_and_lists_them(self, tmp_path):
        """Rather than guess at a device, it starts with none and the agent picks from the list."""
        # Arrange
        root = tmp_path / "termapy_cfg"
        _write_config(root, "demo", "DEMO")
        _write_config(root, "pico", "")
        client = _StdioClient([], project=tmp_path)

        # Act / Assert
        try:
            told = _instructions(client)
            assert told.startswith("No config is loaded. 2 configs and none named mcp_*"), told
            assert "demo, pico" in told
            first_try = _run_command(client, "/run.list")
            assert "demo, pico" in first_try["error"], "the first refusal repeats the choices"
            loaded = _run_command(client, "/cfg.load demo")
            assert loaded["success"] is True, loaded["error"]
        finally:
            client.close()

    @pytest.mark.rule("mcp-finds-its-config")
    def test_a_config_named_at_launch_wins(self, tmp_path):
        """The explicit path: --mcp <name> needs no absolute path, and never walks."""
        # Arrange
        root = tmp_path / "termapy_cfg"
        _write_config(root, "mcp_a", "DEMO")
        _write_config(root, "left", "DEMO_FAIL")
        client = _StdioClient(["left"], project=tmp_path)

        # Act / Assert
        try:
            told = _instructions(client)
            assert told.startswith("Active config: left."), told
            assert "Skipped" not in told, (
                "a named config is where it starts, its port failing or not"
            )
            assert "mcp_a" in told, "and the agent is told what else it can load"
            moved = _run_command(client, "/cfg.load mcp_a")
            assert moved["success"] is True, moved["error"]
        finally:
            client.close()
