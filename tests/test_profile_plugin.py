"""Tests for /profile.load and /profile.info (Phase 4).

Phase 0 added /profile.validate; Phase 4 added /profile.load (sets
the active_profile namespace) and /profile.info (displays the
metadata).

Transport-apply on load is deferred to Phase 6 (needs SerialEngine
runtime baud changes + lifecycle hooks).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="mcp SDK not installed; install with [mcp] extra")

from termapy.defaults import DEFAULT_CFG  # noqa: E402
from termapy.mcp.server import MCPHost  # noqa: E402


FIXTURES = Path(__file__).parent / "fixtures" / "profiles"


@pytest.fixture
def env(tmp_path):
    """Build an MCPHost so ctx.engine is wired (catalog + plugins available)."""
    cfg = dict(DEFAULT_CFG)
    cfg["port"] = ""
    config_path = tmp_path / "cfg" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run", "cap"):
        (config_path.parent / sub).mkdir(exist_ok=True)
    host = MCPHost(cfg, str(config_path), verbose=False)
    host.ctx.ns("flags")["output_level"] = "verbose"
    output: list = []
    orig_write = host.ctx.io._write

    def captured_write(text, color=""):
        output.append((text, color))
        orig_write(text, color)

    host.ctx.io._write = captured_write
    return host.repl, host.ctx, output


# ── /profile.load ───────────────────────────────────────────────────────────


class TestProfileLoad:
    def test_loads_reference_profile(self, env):
        # Arrange / Act
        eng, ctx, _output = env
        path = FIXTURES / "at_modem.profile.json"
        result = eng.dispatch(f"profile.load {path}")
        # Assert
        assert result.success is True, "load succeeds"
        active = ctx.ns("active_profile")
        assert active.get("profile_revision") == "1.0.0", "revision recorded"
        assert "AT" in active.get("commands", {}), "commands populated"

    def test_load_missing_file_fails(self, env):
        # Arrange / Act
        eng, _ctx, _output = env
        result = eng.dispatch("profile.load /no/such/path.json")
        # Assert
        assert result.success is False, "missing file fails"
        assert "not found" in result.error.lower(), "error names not found"

    def test_load_no_args_with_nothing_loaded_fails(self, env):
        # Arrange -- no profile loaded yet; no-args reload has no source.
        eng, _ctx, _output = env
        # Act
        result = eng.dispatch("profile.load")
        # Assert -- new contract: no-args reloads current source; with
        # nothing loaded, fails with a "nothing to reload" message.
        assert result.success is False, "no source to reload fails"
        assert "reload" in result.error.lower() or "path" in result.error.lower(), (
            "error explains the no-source case"
        )

    def test_load_invalid_json_fails(self, env, tmp_path):
        # Arrange — write a malformed profile
        bad = tmp_path / "bad.profile.json"
        bad.write_text("{ this is not valid", encoding="utf-8")
        eng, _ctx, _output = env
        # Act
        result = eng.dispatch(f"profile.load {bad}")
        # Assert
        assert result.success is False, "malformed parse fails"
        assert "parse" in result.error.lower(), "error names parse"

    def test_load_schema_invalid_refuses(self, env, tmp_path):
        # Arrange — schema-invalid profile
        bad = tmp_path / "schema_bad.profile.json"
        bad.write_text(
            json.dumps(
                {
                    "profile_version": 2,
                    "commands": {"X": {"help": "h", "safety": "made-up"}},
                }
            ),
            encoding="utf-8",
        )
        eng, ctx, _output = env
        # Act
        result = eng.dispatch(f"profile.load {bad}")
        # Assert
        assert result.success is False, "schema error refuses load"
        assert ctx.ns("active_profile") == {}, "active profile not set on failure"

    def test_load_replaces_previous_active_profile(self, env):
        # Arrange — load one, then another
        eng, ctx, _output = env
        eng.dispatch(f"profile.load {FIXTURES / 'at_modem.profile.json'}")
        rev1 = ctx.ns("active_profile").get("profile_revision")
        # Act — load a second profile
        eng.dispatch(f"profile.load {FIXTURES / 'register_psu.profile.json'}")
        rev2 = ctx.ns("active_profile").get("profile_revision")
        # Assert
        assert rev1 != rev2, "active profile replaced"
        assert rev2 == "0.3.0", "second profile's revision in effect"


# ── /profile.info ──────────────────────────────────────────────────────────


class TestProfileInfo:
    def test_info_with_no_loaded_profile(self, env):
        # Arrange / Act
        eng, _ctx, output = env
        result = eng.dispatch("profile.info")
        # Assert
        assert result.success is True, "info still succeeds with no profile"
        full = " ".join(t for t, _ in output)
        assert "no profile" in full.lower(), "informs user no profile loaded"

    def test_info_after_load_shows_metadata(self, env):
        # Arrange
        eng, _ctx, output = env
        eng.dispatch(f"profile.load {FIXTURES / 'at_modem.profile.json'}")
        output.clear()
        # Act
        eng.dispatch("profile.info")
        full = " ".join(t for t, _ in output)
        # Assert
        assert "1.0.0" in full, "shows revision"
        assert "Generic AT Modem" in full, "shows device name"
        assert "115200" in full, "shows baud"

    def test_info_value_is_command_count(self, env):
        # Arrange
        eng, _ctx, _output = env
        eng.dispatch(f"profile.load {FIXTURES / 'at_modem.profile.json'}")
        # Act
        result = eng.dispatch("profile.info")
        # Assert
        assert int(result.value) == 5, "value is the command count"


# ── /profile.load no-args reload ───────────────────────────────────────────


class TestProfileReload:
    def test_no_args_reloads_file_source(self, env, tmp_path):
        # Arrange -- write a profile, load it.
        eng, ctx, _output = env
        path = tmp_path / "p.profile.json"
        path.write_text(json.dumps({
            "profile_version": 2,
            "profile_revision": "1.0.0",
            "commands": {"X": {"help": "x"}},
        }), encoding="utf-8")
        eng.dispatch(f"profile.load {path}")
        # Mutate the file on disk; bare /profile.load should pick it up.
        path.write_text(json.dumps({
            "profile_version": 2,
            "profile_revision": "1.1.0",
            "commands": {"X": {"help": "x"}, "Y": {"help": "y"}},
        }), encoding="utf-8")
        # Act
        result = eng.dispatch("profile.load")
        # Assert
        assert result.success is True, "no-args reload from file succeeds"
        active = ctx.ns("active_profile")
        assert active.get("profile_revision") == "1.1.0", "fresh contents loaded"
        assert "Y" in active.get("commands", {}), "new command picked up"

    def test_no_args_with_no_source_fails(self, env):
        # Arrange / Act -- nothing loaded yet.
        eng, _ctx, _output = env
        result = eng.dispatch("profile.load")
        # Assert
        assert result.success is False, "no source -> fail"
        assert "reload" in result.error.lower() or "path" in result.error.lower(), (
            "error explains the no-source case"
        )


# ── /profile.save ──────────────────────────────────────────────────────────


class TestProfileSave:
    def test_save_to_explicit_path(self, env, tmp_path):
        # Arrange -- load a profile, save it elsewhere.
        eng, _ctx, _output = env
        eng.dispatch(f"profile.load {FIXTURES / 'at_modem.profile.json'}")
        out = tmp_path / "saved.profile.json"
        # Act
        result = eng.dispatch(f"profile.save {out}")
        # Assert
        assert result.success is True, "save succeeds"
        assert out.exists(), "file written"
        loaded = json.loads(out.read_text(encoding="utf-8"))
        assert loaded.get("profile_revision") == "1.0.0", (
            "saved file round-trips"
        )
        assert "__source_path" not in loaded, (
            "internal fields stripped from saved profile"
        )

    def test_save_no_args_uses_default_path(self, env):
        # Arrange -- the env fixture's cfg is at <tmp>/cfg/test.cfg, so
        # the default save path is <tmp>/cfg/test.profile.json.
        eng, ctx, _output = env
        eng.dispatch(f"profile.load {FIXTURES / 'at_modem.profile.json'}")
        # Act
        result = eng.dispatch("profile.save")
        # Assert
        assert result.success is True, "default-path save succeeds"
        expected = Path(ctx.config_path).parent / "test.profile.json"
        assert expected.exists(), f"saved at default path {expected}"

    def test_save_without_active_profile_fails(self, env):
        # Arrange / Act -- nothing loaded.
        eng, _ctx, _output = env
        result = eng.dispatch("profile.save")
        # Assert
        assert result.success is False, "no profile -> fail"
        assert "no profile" in result.error.lower(), "error message clear"

    def test_save_warns_when_all_commands_disabled(self, env, tmp_path):
        # Arrange -- write a profile where every command has enabled=false.
        eng, _ctx, output = env
        draft = tmp_path / "draft.profile.json"
        draft.write_text(json.dumps({
            "profile_version": 2,
            "commands": {
                "A": {"help": "a", "enabled": False},
                "B": {"help": "b", "enabled": False},
            },
        }), encoding="utf-8")
        eng.dispatch(f"profile.load {draft}")
        out = tmp_path / "saved.profile.json"
        output.clear()
        # Act
        result = eng.dispatch(f"profile.save {out}")
        # Assert
        assert result.success is True, "save still succeeds"
        full = " ".join(t for t, _ in output)
        assert "enabled=false" in full.lower() or "nothing will dispatch" in full.lower(), (
            "warning surfaced about all-disabled state"
        )


# ── /profile.unload ────────────────────────────────────────────────────────


class TestProfileUnload:
    def test_unload_clears_active_profile(self, env):
        # Arrange
        eng, ctx, _output = env
        eng.dispatch(f"profile.load {FIXTURES / 'at_modem.profile.json'}")
        assert ctx.ns("active_profile"), "precondition: profile loaded"
        # Act
        result = eng.dispatch("profile.unload")
        # Assert
        assert result.success is True, "unload succeeds"
        assert ctx.ns("active_profile") == {}, "namespace cleared"

    def test_unload_with_no_profile_is_noop(self, env):
        # Arrange / Act -- nothing loaded.
        eng, _ctx, _output = env
        result = eng.dispatch("profile.unload")
        # Assert -- harmless, returns ok.
        assert result.success is True, "no-op succeeds"


# ── /profile.load cmd=<command> (device fetch) ─────────────────────────────


class _NullContext:
    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _mock_device_json(env, payload: dict) -> None:
    """Patch the device-fetch path so /profile.load cmd= returns ``payload``.

    Mirrors test_include's `_run_fetch` helper but for the new
    profile_cmd._read_profile_json hook and the connected-serial stubs.
    """
    from termapy.builtins.commands import profile_cmd

    eng, ctx, _output = env
    profile_cmd._read_profile_json = lambda c, t: payload  # type: ignore[assignment]
    ctx.serial.is_connected = lambda: True
    ctx.serial.io = lambda: _NullContext()
    ctx.serial.drain = lambda: 0
    ctx.serial.send = lambda text: None


class TestProfileLoadFromDevice:
    def test_fetch_installs_full_profile(self, env):
        # Arrange -- simulate a device dumping a v2 profile.
        _mock_device_json(env, {
            "profile_version": 2,
            "profile_revision": "1.0.0",
            "transport": {"protocol": "text", "baud_rate": 115200},
            "types": {"onoff": {"kind": "enum", "values": ["on", "off"]}},
            "commands": {"AT": {"help": "Connection test."}},
        })
        eng, ctx, _output = env
        # Act
        result = eng.dispatch("profile.load cmd=AT+HELP.JSON")
        # Assert -- full profile installed, not just commands.
        assert result.success is True, "device-fetch succeeds"
        active = ctx.ns("active_profile")
        assert active.get("profile_revision") == "1.0.0", "revision installed"
        assert active.get("transport", {}).get("baud_rate") == 115200, (
            "transport block installed (the gap /include couldn't bridge)"
        )
        assert "onoff" in active.get("types", {}), (
            "top-level types block installed"
        )
        assert "AT" in active.get("commands", {}), "commands installed"
        assert active.get("__source_cmd") == "AT+HELP.JSON", (
            "source command recorded for no-args reload"
        )

    def test_fetch_when_disconnected_fails(self, env):
        # Arrange -- explicitly NOT mocking is_connected; default False.
        eng, _ctx, _output = env
        # Act
        result = eng.dispatch("profile.load cmd=AT+HELP.JSON")
        # Assert
        assert result.success is False, "disconnected fetch fails"
        assert "not connected" in result.error.lower(), "error clear"

    def test_fetch_missing_cmd_value_fails(self, env):
        # Arrange / Act -- cmd= with no value.
        eng, _ctx, _output = env
        result = eng.dispatch("profile.load cmd=")
        # Assert
        assert result.success is False, "empty cmd fails"
        assert "command" in result.error.lower(), "error clear"

    def test_fetch_schema_invalid_payload_refuses(self, env):
        # Arrange -- device replies with something that fails the schema.
        _mock_device_json(env, {
            "profile_version": 99,
            "commands": {"X": {"help": "x"}},
        })
        eng, ctx, _output = env
        # Act
        result = eng.dispatch("profile.load cmd=AT+HELP.JSON")
        # Assert
        assert result.success is False, "bad payload refused"
        assert ctx.ns("active_profile") == {}, (
            "active profile untouched on failure"
        )

    def test_fetch_then_no_args_reload_refetches(self, env):
        # Arrange -- first fetch, then mutate payload, then no-args reload.
        from termapy.builtins.commands import profile_cmd

        seq = iter([
            {
                "profile_version": 2, "profile_revision": "1.0.0",
                "commands": {"X": {"help": "x"}},
            },
            {
                "profile_version": 2, "profile_revision": "1.1.0",
                "commands": {"X": {"help": "x"}, "Y": {"help": "y"}},
            },
        ])
        _mock_device_json(env, next(seq))
        eng, ctx, _output = env
        eng.dispatch("profile.load cmd=AT+HELP.JSON")
        # Swap in the second payload; reload should re-run the cmd path.
        profile_cmd._read_profile_json = lambda c, t: next(seq)
        # Act
        result = eng.dispatch("profile.load")
        # Assert
        assert result.success is True, "reload from cmd source succeeds"
        active = ctx.ns("active_profile")
        assert active.get("profile_revision") == "1.1.0", (
            "no-args reload re-ran the device fetch and picked up new payload"
        )


# ── Transport divergence visibility ────────────────────────────────────────


def _divergent_profile(tmp_path):
    """Write a v2 profile whose transport differs from DEFAULT_CFG."""
    p = tmp_path / "div.profile.json"
    p.write_text(json.dumps({
        "profile_version": 2,
        "profile_revision": "1.0.0",
        "transport": {
            "protocol": "text",
            "baud_rate": 9600,             # DEFAULT_CFG has 115200
            "line_ending_send": "\n",      # DEFAULT_CFG has "\r"
        },
        "commands": {"AT": {"help": "Connection test."}},
    }), encoding="utf-8")
    return p


class TestProfileLoadDivergenceWarning:
    """On /profile.load, surface every cfg field the profile overrode
    so the engineer sees what changed instead of having to spot it
    later via /cfg.dump."""

    def test_warning_lists_each_diverging_field(self, env, tmp_path):
        # Arrange -- cfg has baud=115200, line_ending="\r" (defaults);
        # profile asks for 9600 and "\n".
        eng, ctx, output = env
        path = _divergent_profile(tmp_path)
        assert ctx.cfg.get("baud_rate") == 115200, "precondition: default baud"
        output.clear()
        # Act
        eng.dispatch(f"profile.load {path}")
        full = " ".join(t for t, _ in output)
        # Assert -- warning mentions baud_rate AND line_ending, with
        # both old and new values surfaced for each.
        assert "overrode" in full.lower(), "override warning fired"
        assert "baud_rate" in full, "baud_rate row present"
        assert "115200" in full and "9600" in full, (
            "both old and new baud values shown"
        )
        assert "line_ending" in full, "line_ending row present"

    def test_no_warning_when_profile_matches_cfg(self, env, tmp_path):
        # Arrange -- profile carries the SAME values as DEFAULT_CFG.
        eng, _ctx, output = env
        p = tmp_path / "match.profile.json"
        p.write_text(json.dumps({
            "profile_version": 2,
            "transport": {
                "baud_rate": 115200,         # DEFAULT_CFG default
                "line_ending_send": "\r",    # DEFAULT_CFG default
            },
            "commands": {"AT": {"help": "Connection test."}},
        }), encoding="utf-8")
        output.clear()
        # Act
        eng.dispatch(f"profile.load {p}")
        full = " ".join(t for t, _ in output)
        # Assert -- no override warning (the values agree).
        assert "override" not in full.lower(), (
            "no warning when profile == cfg"
        )


class TestProfileInfoDivergence:
    """After /profile.load, an engineer may /cfg something serial-level
    away from the profile.  /profile.info should surface the gap so the
    engineer remembers they're off-contract."""

    # Distinct phrase the section uses; doesn't appear anywhere else in
    # the codebase so we can grep for it without false positives from
    # temp-path names or test-method names.
    _SECTION_MARKER = "differs from profile"

    def test_divergence_section_shown_when_cfg_drifted(self, env, tmp_path):
        # Arrange -- load a profile, then change baud via the cfg engine.
        eng, ctx, output = env
        path = _divergent_profile(tmp_path)
        eng.dispatch(f"profile.load {path}")
        # Move cfg.baud away from the profile's 9600.
        ctx.engine.apply_cfg("baud_rate", 38400)
        output.clear()
        # Act
        eng.dispatch("profile.info")
        full = " ".join(t for t, _ in output)
        # Assert
        assert self._SECTION_MARKER in full, (
            "divergence section header surfaced"
        )
        assert "baud_rate" in full, "diverging field named"
        assert "9600" in full and "38400" in full, (
            "both profile and cfg values shown"
        )

    def test_no_divergence_section_when_cfg_matches(self, env, tmp_path):
        # Arrange -- load a profile; do not mutate cfg after.
        eng, _ctx, output = env
        path = _divergent_profile(tmp_path)
        eng.dispatch(f"profile.load {path}")
        output.clear()
        # Act
        eng.dispatch("profile.info")
        full = " ".join(t for t, _ in output)
        # Assert -- no divergence section (cfg was just mutated by the
        # load itself, so cfg == profile transport now).
        assert self._SECTION_MARKER not in full, (
            "no divergence section when cfg matches profile"
        )
