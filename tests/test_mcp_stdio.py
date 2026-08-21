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

    def __init__(self, cfg_file: Path, cfg_dir: Path) -> None:
        import os

        env = dict(os.environ)
        # Deterministic ports: the subprocess enumerates the demo fleet,
        # not this machine's hardware.
        env["TERMAPY_DEMO_FLEET"] = "1"
        self.proc = subprocess.Popen(
            [sys.executable, "-c",
             "import sys; sys.argv = ['termapy', '--mcp', "
             f"{str(cfg_file)!r}, '--cfg-dir', {str(cfg_dir)!r}]; "
             "from termapy.entry import main; main()"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env,
        )
        self._lines: queue.Queue[str] = queue.Queue()
        self._id = 0
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        for line in self.proc.stdout:
            self._lines.put(line)

    def rpc(self, method: str, params: dict | None = None, *,
            notify: bool = False, timeout: float = 20.0):
        """Send one request (or notification) and return the response."""
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self._id += 1
            msg["id"] = self._id
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
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
    reply = client.rpc("tools/call", {
        "name": "run_command",
        "arguments": {"command": command, "output": "normal", "timeout_s": 5},
    })
    content = reply["result"]["content"][0]["text"]
    return json.loads(content)


ENVELOPE_KEYS = {
    "cmd", "success", "error", "value", "data", "elapsed_s",
    "output_lines", "captured_artifacts", "async_events",
}


class TestMcpStdioEndToEnd:
    def test_full_agent_conversation(self, tmp_path):
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
        client = _StdioClient(cfg_file, tmp_path)

        try:
            # Act / Assert -- one conversation, the way an agent has it.

            # 1. Handshake.
            init = client.rpc("initialize", {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest-stdio", "version": "0"},
            })
            server_name = init["result"]["serverInfo"]["name"]
            assert server_name == "termapy", "server identifies itself"
            client.rpc("notifications/initialized", {}, notify=True)

            # 2. Discovery.
            tools = client.rpc("tools/list", {})
            tool_names = [tool["name"] for tool in tools["result"]["tools"]]
            assert "run_command" in tool_names, "the one tool is published"

            # 3. A termapy command: structured records over the wire.
            envelope = _run_command(client, "/port.list")
            assert set(envelope.keys()) == ENVELOPE_KEYS, (
                "wire envelope is the fixed nine keys"
            )
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
            assert on["value"] == "on", "session flipped to request mode"
            at = _run_command(client, "AT")
            assert at["success"] is True, "request/response exchange succeeds"
            assert at["value"] == "OK", (
                "plain text device answers in value -- the agent now has "
                "the reply"
            )
            assert at["data"] is None, "text reply carries no structure"

            # 5. A resource read.
            res = client.rpc(
                "resources/read", {"uri": "termapy://device_state.json"}
            )
            state = json.loads(res["result"]["contents"][0]["text"])
            assert state["last_command"]["cmd"] == "AT", (
                "device_state tracks the conversation"
            )
        finally:
            client.close()
