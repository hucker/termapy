"""MCP filesystem/network sandbox: the fs chokepoint and its command gates.

The MCP host confines a remote/automated peer to the config sandbox:
host-wide file paths and network 'ports' are refused unless the operator
opts in per-class (TERMAPY_MCP_FS_UNCONFINED / TERMAPY_MCP_NET_EGRESS).
Interactive hosts (CLI/TUI) grant both, so they are never sandboxed.

Two layers are tested here:
  1. FilesystemHandle.resolve / guard_external_path -- the chokepoint,
     unit-tested against a hand-built handle with confined vs unconfined
     capabilities.
  2. End-to-end command refusals through a sandboxed MCP host.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from termapy.plugins.capabilities import CapabilitySet, MissingCapability
from termapy.plugins.handles.fs import FilesystemHandle

# ── Layer 1: the chokepoint ─────────────────────────────────────────────────


def _handle(tmp_path, *, unconfined: bool) -> FilesystemHandle:
    cap = tmp_path / "cfg" / "cap"
    cap.mkdir(parents=True)
    return FilesystemHandle(
        cap_dir=cap,
        capabilities=CapabilitySet(filesystem_unconfined=unconfined),
    )


class TestResolveContainment:
    def test_plain_name_lands_in_folder(self, tmp_path):
        # Arrange
        fs = _handle(tmp_path, unconfined=False)
        # Act
        actual = fs.resolve("adc.csv", "cap")
        # Assert -- contained even under the sandbox.
        assert actual == (fs.cap_dir / "adc.csv").resolve(), (
            "a plain name resolves inside cap/"
        )

    def test_absolute_refused_under_sandbox(self, tmp_path):
        # Arrange
        fs = _handle(tmp_path, unconfined=False)
        # Act / Assert
        with pytest.raises(MissingCapability, match="escapes"):
            fs.resolve(str(tmp_path / "secret.txt"), "cap")

    def test_traversal_refused_under_sandbox(self, tmp_path):
        # Arrange
        fs = _handle(tmp_path, unconfined=False)
        # Act / Assert
        with pytest.raises(MissingCapability, match="escapes"):
            fs.resolve("../../secret.txt", "cap")

    def test_absolute_allowed_when_unconfined(self, tmp_path):
        # Arrange -- operator host (CLI/TUI) or opted-in MCP.
        fs = _handle(tmp_path, unconfined=True)
        target = tmp_path / "anywhere.txt"
        # Act
        actual = fs.resolve(str(target), "cap")
        # Assert
        assert actual == target.resolve(), "unconfined host keeps host-wide paths"

    def test_unknown_folder_raises_valueerror(self, tmp_path):
        # Arrange
        fs = _handle(tmp_path, unconfined=True)
        # Act / Assert
        with pytest.raises(ValueError, match="Unknown folder"):
            fs.resolve("x", "nope")


class TestGuardExternalPath:
    def test_plain_name_allowed(self, tmp_path):
        # Arrange / Act -- a bare config name is NOT external; must not raise.
        fs = _handle(tmp_path, unconfined=False)
        fs.guard_external_path("mydevice", "Config")
        # Assert -- no exception is the assertion (name-based load survives).

    def test_absolute_refused_under_sandbox(self, tmp_path):
        # Arrange
        fs = _handle(tmp_path, unconfined=False)
        # Act / Assert
        with pytest.raises(MissingCapability, match="outside the config sandbox"):
            fs.guard_external_path(str(tmp_path / "creds.json"), "Config")

    def test_traversal_refused_under_sandbox(self, tmp_path):
        # Arrange
        fs = _handle(tmp_path, unconfined=False)
        # Act / Assert
        with pytest.raises(MissingCapability):
            fs.guard_external_path("../../etc/passwd", "Profile path")

    def test_absolute_allowed_when_unconfined(self, tmp_path):
        # Arrange / Act -- operator host: no raise.
        fs = _handle(tmp_path, unconfined=True)
        fs.guard_external_path(str(tmp_path / "creds.json"), "Config")
        # Assert -- no exception.


# ── Layer 2: end-to-end through a sandboxed MCP host ────────────────────────

pytest.importorskip("mcp", reason="mcp SDK not installed; install with [mcp] extra")

from termapy.defaults import DEFAULT_CFG  # noqa: E402
from termapy.mcp.server import MCPHost  # noqa: E402


def _mcp_host(tmp_path):
    """A sandboxed MCP host (no opt-in flags set)."""
    cfg = dict(DEFAULT_CFG)
    cfg["port"] = ""
    config_path = tmp_path / "cfg" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run", "cap"):
        (config_path.parent / sub).mkdir(exist_ok=True)
    return MCPHost(cfg, str(config_path), verbose=False)


class TestMcpHostSandboxed:
    def test_host_is_sandboxed_by_default(self, tmp_path):
        # Arrange / Act
        host = _mcp_host(tmp_path)
        # Assert -- both restrictive caps off by default.
        assert host.ctx.capabilities.filesystem_unconfined is False, (
            "MCP filesystem is sandboxed by default"
        )
        assert host.ctx.capabilities.network_egress is False, (
            "MCP network egress is off by default"
        )
        # And the fs handle snapshot agrees (sync_capabilities ran).
        assert host.ctx.fs.capabilities.filesystem_unconfined is False, (
            "fs handle snapshot reflects the sandbox"
        )

    def test_cfg_auto_log_file_absolute_refused(self, tmp_path):
        # Arrange -- the CRITICAL arbitrary-read setup step.
        host = _mcp_host(tmp_path)
        # Act
        result = host.repl.dispatch(rf"cfg.auto log_file {tmp_path}\steal.txt")
        # Assert
        assert not result.success, "path-valued cfg key refused outside sandbox"
        assert "sandbox" in result.error, "refusal cites the sandbox"

    def test_cfg_auto_nonpath_key_still_works(self, tmp_path):
        # Arrange -- a normal cfg edit must NOT be gated.
        host = _mcp_host(tmp_path)
        # Act
        result = host.repl.dispatch("cfg.auto default_response_timeout_ms 5000")
        # Assert
        assert result.success, "non-path cfg keys are unaffected by the sandbox"

    def test_cfg_load_absolute_refused_but_name_ok(self, tmp_path):
        # Arrange
        host = _mcp_host(tmp_path)
        # Act -- absolute path refused...
        abs_result = host.repl.dispatch(rf"cfg.load {tmp_path}\creds.json")
        # ...bare name is not sandbox-refused (fails later for "not found").
        name_result = host.repl.dispatch("cfg.load somedevice")
        # Assert
        assert not abs_result.success, "absolute cfg.load path refused"
        assert "sandbox" in abs_result.error, "refusal cites the sandbox"
        assert "sandbox" not in (name_result.error or ""), (
            "name-based cfg.load (MCP hot-swap) is not sandbox-gated"
        )

    def test_crc_file_write_refused(self, tmp_path):
        # Arrange
        host = _mcp_host(tmp_path)
        # Act -- codegen to disk is a host-fs write.
        result = host.repl.dispatch("proto.crc.python crc32 file=evil")
        # Assert
        assert not result.success, "crc file= write refused under sandbox"
        assert "sandbox" in result.error, "refusal cites the sandbox"

    def test_crc_inline_still_works(self, tmp_path):
        # Arrange -- inline codegen (no file=) is not a host write.
        host = _mcp_host(tmp_path)
        # Act
        result = host.repl.dispatch("proto.crc.python crc32")
        # Assert
        assert result.success, "inline crc codegen is unaffected by the sandbox"

    def test_port_connect_socket_url_refused(self, tmp_path):
        # Arrange
        host = _mcp_host(tmp_path)
        # Act
        result = host.repl.dispatch("port.connect socket://127.0.0.1:9")
        # Assert
        assert not result.success, "network 'port' refused under sandbox"
        assert "sandbox" in result.error, "refusal cites the sandbox"

    def test_cap_poll_absolute_file_refused(self, tmp_path):
        # Arrange
        host = _mcp_host(tmp_path)
        # Act -- regex=.* keeps the file; file= is an absolute escape.
        result = host.repl.dispatch(
            rf"cap.poll count=1 file={tmp_path}\out.csv regex=.* cmd=/print x"
        )
        # Assert
        assert not result.success, "cap.poll absolute file= refused under sandbox"
        assert "escapes" in result.error or "sandbox" in result.error, (
            "refusal cites containment"
        )

    def test_crc_calc_absolute_path_refused(self, tmp_path):
        # Arrange -- crc.calc reads a file when its data arg is a path,
        # an existence/size/CRC oracle.
        host = _mcp_host(tmp_path)
        # Act
        result = host.repl.dispatch(rf"proto.crc.calc crc32 {tmp_path}\secret")
        # Assert
        assert not result.success, "crc.calc file-read refused under sandbox"
        assert "sandbox" in result.error, "refusal cites the sandbox"

    def test_crc_calc_literal_data_still_works(self, tmp_path):
        # Arrange -- literal hex bytes are not a path; the guard is a no-op.
        host = _mcp_host(tmp_path)
        # Act
        result = host.repl.dispatch("proto.crc.calc crc32 01 02 03 04")
        # Assert
        assert result.success, "literal-data crc.calc is unaffected by the sandbox"

    def test_cfg_icon_filtered_from_catalog(self, tmp_path):
        # Arrange -- icon spawns a subprocess / writes a desktop launcher;
        # gated on gui_apps, which MCP lacks, so it drops out of the catalog.
        from termapy.mcp.catalog import build_catalog

        host = _mcp_host(tmp_path)
        # Act
        names = {command["name"] for command in build_catalog(host.ctx)["commands"]}
        # Assert
        assert "/cfg.icon" not in names, (
            "cfg.icon is gated out of the MCP catalog (needs gui_apps)"
        )

    def test_cfg_icon_dispatch_refused(self, tmp_path):
        # Arrange
        host = _mcp_host(tmp_path)
        # Act
        result = host.repl.dispatch("cfg.icon")
        # Assert -- the dispatch capability gate refuses it (not "unknown").
        assert not result.success, "cfg.icon refused at dispatch under MCP"
        assert "unknown" not in result.error.lower(), (
            "refused for capability, not command-not-found"
        )


class TestMcpHostOptedIn:
    @pytest.fixture
    def unconfined_host(self, tmp_path, monkeypatch):
        # Arrange -- operator set TERMAPY_MCP_FS_UNCONFINED=1 before launch.
        monkeypatch.setattr("termapy.env_flags.MCP_FS_UNCONFINED", True)
        monkeypatch.setattr("termapy.env_flags.MCP_NET_EGRESS", True)
        return _mcp_host(tmp_path)

    def test_optin_grants_filesystem(self, unconfined_host):
        # Assert -- caps + handle snapshot both reflect the opt-in.
        assert unconfined_host.ctx.capabilities.filesystem_unconfined is True, (
            "TERMAPY_MCP_FS_UNCONFINED grants the capability"
        )
        assert unconfined_host.ctx.fs.capabilities.filesystem_unconfined is True, (
            "fs handle snapshot reflects the opt-in"
        )

    def test_optin_allows_absolute_cfg_auto(self, unconfined_host, tmp_path):
        # Act -- the same call that was refused under the sandbox.
        result = unconfined_host.repl.dispatch(
            rf"cfg.auto log_file {tmp_path}\ok.log"
        )
        # Assert
        assert result.success, "opt-in restores host-wide path-valued cfg keys"


class TestMcpRunConfined:
    """/run was the last file-taking command reachable under the sandbox
    that bypassed the fs chokepoint: it probed cwd-relative and absolute
    paths before the scripts dir. A confined caller now resolves scripts
    ONLY inside the per-config run/ dir (same policy as ctx.fs.resolve)."""

    def test_run_absolute_path_refused(self, tmp_path):
        # Arrange -- a script OUTSIDE the sandbox
        host = _mcp_host(tmp_path)
        evil = tmp_path / "evil.run"
        evil.write_text("/print pwned\n", encoding="utf-8")

        # Act -- start_script is the resolution seam (synchronous)
        path, result = host.repl.start_script(str(evil))

        # Assert
        assert path is None, "absolute path never resolves under sandbox"
        assert "sandbox" in result.error, "refusal cites the sandbox"

    def test_run_traversal_refused(self, tmp_path):
        # Arrange
        host = _mcp_host(tmp_path)
        evil = tmp_path / "evil.run"
        evil.write_text("/print pwned\n", encoding="utf-8")

        # Act -- ../ from the run dir points at the out-of-sandbox file
        path, result = host.repl.start_script("../../evil.run")

        # Assert
        assert path is None, "traversal never resolves under sandbox"
        assert "sandbox" in result.error, "refusal cites the sandbox"

    def test_run_in_sandbox_script_resolves(self, tmp_path):
        # Arrange -- a legitimate script inside the config's run/ dir
        host = _mcp_host(tmp_path)
        run_dir = Path(host.repl.ctx.config_path).parent / "run"
        (run_dir / "ok.run").write_text("/print hello\n", encoding="utf-8")

        # Act -- bare name (no suffix) resolves inside the sandbox
        path, result = host.repl.start_script("ok")

        # Assert
        assert result.success is True, "in-sandbox script accepted"
        assert path == run_dir / "ok.run", "resolved inside the run/ sandbox"
