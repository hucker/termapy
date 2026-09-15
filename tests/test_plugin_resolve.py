"""Tests for ReplEngine.resolve_plugins -- the one plugin-resolution step.

Every frontend fires ``on_app_start`` / ``on_config_load``; the engine
resolves the global and per-config ``plugin/`` folders there.  These tests
drive that path through ``fire_lifecycle`` on an engine whose global root
is a temp folder: the checkout has a real ``termapy_cfg/plugin/``, so the
default root would load the developer's own plugins.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from termapy.plugins import (
    CapabilitySet,
    CmdResult,
    InternalHandle,
    IOHandle,
    PluginContext,
)
from termapy.repl import ReplEngine

# A minimal COMMAND plugin whose handler returns a recognizable value.
_COMMAND_PLUGIN = '''
from termapy.plugins import CmdResult, Command


def _handler(ctx, args):
    return CmdResult.ok(value="__TOKEN__")


COMMAND = Command(name="__NAME__", args="", help="test", handler=_handler)
'''

# A plugin exporting every kind: command, transform, directive, two hooks.
_FULL_PLUGIN = '''
from termapy.plugins import CmdResult, Command, Directive, Transform


def _handler(ctx, args):
    return CmdResult.ok(value="full")


def _rewrite(line):
    return line


def _directive(line):
    return None


def on_app_start(ctx):
    ctx.ns("probe")["started"] = True


def on_config_load(ctx):
    fired = ctx.ns("probe").setdefault("fired", [])
    fired.append("__NAME__")


COMMAND = Command(name="__NAME__", args="", help="test", handler=_handler)
TRANSFORM = Transform(name="__NAME___x", help="test", repl=_rewrite)
DIRECTIVE = Directive(name="__NAME___d", help="test", handler=_directive)
'''


def _command_plugin(name: str, token: str = "x") -> str:
    return _COMMAND_PLUGIN.replace("__NAME__", name).replace("__TOKEN__", token)


def _full_plugin(name: str) -> str:
    return _FULL_PLUGIN.replace("__NAME__", name)


def _write_cfg(root: Path, name: str) -> Path:
    """``<root>/<name>/<name>.cfg`` with a minimal serial block."""
    cfg = {
        "serial": {
            "port": "", "baud_rate": 115200, "custom_baud": False, "byte_size": 8,
            "parity": "N", "stop_bits": 1, "flow_control": "none",
        },
        "echo": False,
        "eol": "\r",
    }
    path = root / name / f"{name}.cfg"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def _write_plugin(folder: Path, filename: str, content: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_text(content, encoding="utf-8")


def _build(
    root: Path, config_path: str, *, trusted_only: bool = False, oneshot: bool = False,
):
    """An engine + context whose global root is ``root``; output captured as (text, color)."""
    cfg = json.loads(Path(config_path).read_text(encoding="utf-8")) if config_path else {}
    output: list[tuple[str, str | None]] = []

    def write(text, color=None):
        output.append((text, color))

    engine = ReplEngine(
        cfg, config_path, write, trusted_only=trusted_only, global_root=root,
    )
    internal = InternalHandle(
        plugins=engine._plugins,
        in_script=lambda: engine.in_script,
        script_stop=lambda: engine._script_stop.set(),
        apply_cfg=engine._apply_cfg,
        dispatch=engine.dispatch,
    )
    ctx = PluginContext(
        cfg=cfg,
        config_path=config_path,
        internal=internal,
        io=IOHandle(_write=write, _write_markup=lambda text: output.append((text, "markup"))),
        capabilities=CapabilitySet(filesystem_unconfined=True),
        dispatch=engine.dispatch,
        is_oneshot=lambda: oneshot,
    )
    engine.set_context(ctx)
    flags = ctx.ns("flags")
    flags["echo"] = True
    flags["output_level"] = "normal"
    flags["hex"] = False
    return engine, output


@pytest.fixture
def rig(tmp_path):
    """``(root, config_path)`` for ``<tmp>/root/rig/rig.cfg``; global layer is ``<tmp>/root/plugin``."""
    root = tmp_path / "root"
    return root, _write_cfg(root, "rig")


def _load_lines(output) -> list[str]:
    return [text for text, _ in output if text.startswith("Loaded ")]


class TestLayers:

    def test_on_app_start_loads_global_then_config(self, rig):
        # Arrange
        root, config_path = rig
        _write_plugin(root / "plugin", "hello.py", _command_plugin("hello"))
        _write_plugin(config_path.parent / "plugin", "hi.py", _command_plugin("hi"))
        engine, output = _build(root, str(config_path))

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        assert engine._plugins["hello"].source == "global", "the global layer loaded"
        assert engine._plugins["hi"].source == "rig", "the per-config layer is labelled by cfg stem"
        expected = [
            "Loaded 1 plugin(s) from global: hello",
            "Loaded 1 plugin(s) from rig: hi",
        ]
        assert _load_lines(output) == expected, "one load line per layer, global first"

    def test_config_layer_overrides_global(self, rig):
        # Arrange
        root, config_path = rig
        _write_plugin(root / "plugin", "same.py", _command_plugin("same", token="global"))
        _write_plugin(config_path.parent / "plugin", "same.py", _command_plugin("same", token="rig"))
        engine, _ = _build(root, str(config_path))

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        assert engine._plugins["same"].source == "rig", "the per-config plugin wins by name"
        assert engine.dispatch("same").value == "rig", "and its handler is the one installed"

    def test_zero_config_loads_global_only(self, tmp_path):
        # Arrange
        root = tmp_path / "root"
        _write_plugin(root / "plugin", "hello.py", _command_plugin("hello"))
        engine, output = _build(root, "")

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        assert "hello" in engine._plugins, "global plugins load without a config"
        errors = [text for text, _ in output if text.startswith("Plugin error")]
        assert errors == [], "a missing per-config layer is not an error"

    def test_external_hook_fires_in_same_pass(self, rig):
        # Arrange
        root, config_path = rig
        _write_plugin(config_path.parent / "plugin", "full.py", _full_plugin("full"))
        engine, _ = _build(root, str(config_path))

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        assert engine.ctx.ns("probe").get("started") is True, (
            "a folder plugin's on_app_start fires in the resolution pass that loaded it"
        )


class TestProtection:

    def test_app_hook_is_never_overridden(self, rig):
        # Arrange
        root, config_path = rig
        _write_plugin(
            config_path.parent / "plugin", "hello.py", _command_plugin("hello", token="plugin"),
        )
        engine, output = _build(root, str(config_path))
        engine.register_hook(
            "hello", "", "app-owned", lambda ctx, args: CmdResult.ok(value="app"), source="app",
        )

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        assert engine._plugins["hello"].source == "app", "the app hook keeps the name"
        assert engine.dispatch("hello").value == "app", "and its handler"
        expected = ("Skipped /hello from rig - an app command has that name", "yellow")
        assert expected in output, "the collision is reported, never silent"


class TestConfigSwitch:

    def test_config_switch_drops_and_rebuilds(self, tmp_path):
        # Arrange -- rig and other each carry a full plugin
        root = tmp_path / "root"
        rig_cfg = _write_cfg(root, "rig")
        other_cfg = _write_cfg(root, "other")
        _write_plugin(rig_cfg.parent / "plugin", "rigfull.py", _full_plugin("rigfull"))
        _write_plugin(other_cfg.parent / "plugin", "otherfull.py", _full_plugin("otherfull"))
        engine, output = _build(root, str(rig_cfg))
        engine.fire_lifecycle("on_app_start")
        assert "rigfull" in engine._plugins, "precondition: the rig plugin loaded"

        # Act -- the CLI/MCP switch shape: replace the cfg, re-point the ctx, fire
        engine.replace_cfg(json.loads(other_cfg.read_text(encoding="utf-8")), str(other_cfg))
        engine.ctx.config_path = str(other_cfg)
        engine.fire_lifecycle("on_config_load")

        # Assert
        assert "rigfull" not in engine._plugins, "the old config's plugin is gone"
        assert engine._plugins["otherfull"].source == "other", "the new config's plugin is in"
        assert all(info.source != "rig" for info in engine._transform_infos), (
            "the old config's transform is dropped"
        )
        assert all(info.source != "rig" for info in engine._directives), (
            "the old config's directive is dropped"
        )
        assert all(hook.source != "rig" for hook in engine._lifecycle_hooks), (
            "the old config's hooks are dropped"
        )
        actual = len(engine._repl_transforms)
        expected = sum(1 for info in engine._transform_infos if info.repl)
        assert actual == expected, "bare transform callables are rebuilt from the surviving infos"
        assert engine.ctx.ns("probe")["fired"] == ["otherfull"], (
            "only the new config's on_config_load fired on the switch"
        )
        assert ("Unloaded 1 plugin(s): rigfull", "dim") in output, "the drop is reported"

    def test_list_identity_preserved(self, rig):
        # Arrange -- InternalHandle aliases these containers
        root, config_path = rig
        _write_plugin(config_path.parent / "plugin", "full.py", _full_plugin("full"))
        engine, _ = _build(root, str(config_path))
        plugins, directives, hooks = engine._plugins, engine._directives, engine._lifecycle_hooks

        # Act
        engine.fire_lifecycle("on_app_start")
        engine.fire_lifecycle("on_config_load")

        # Assert
        assert engine._plugins is plugins, "the plugin registry is filtered in place"
        assert engine._directives is directives, "the directive list is filtered in place"
        assert engine._lifecycle_hooks is hooks, "the hook list is filtered in place"

    def test_resolve_is_idempotent(self, rig):
        # Arrange
        root, config_path = rig
        _write_plugin(config_path.parent / "plugin", "full.py", _full_plugin("full"))
        engine, _ = _build(root, str(config_path))
        engine.fire_lifecycle("on_app_start")
        before = (
            len(engine._plugins), len(engine._transform_infos),
            len(engine._directives), len(engine._lifecycle_hooks),
        )

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        after = (
            len(engine._plugins), len(engine._transform_infos),
            len(engine._directives), len(engine._lifecycle_hooks),
        )
        assert after == before, "a second resolution replaces, never accumulates"


class TestGateAndReporting:

    def test_trusted_only_loads_builtins_only(self, rig):
        # Arrange
        root, config_path = rig
        _write_plugin(root / "plugin", "hello.py", _command_plugin("hello"))
        _write_plugin(config_path.parent / "plugin", "hi.py", _command_plugin("hi"))
        engine, output = _build(root, str(config_path), trusted_only=True)

        # Act
        engine.fire_lifecycle("on_app_start")
        engine.fire_lifecycle("on_config_load")

        # Assert
        assert "hello" not in engine._plugins and "hi" not in engine._plugins, (
            "neither folder is read under the trusted-only policy"
        )
        notices = [text for text, _ in output if text.startswith("TERMAPY_TRUSTED_PLUGINS_ONLY")]
        assert len(notices) == 1, "the notice shows once per engine, not once per resolution"

    def test_oneshot_suppresses_load_lines_not_errors(self, rig):
        # Arrange
        root, config_path = rig
        _write_plugin(config_path.parent / "plugin", "good.py", _command_plugin("good"))
        _write_plugin(config_path.parent / "plugin", "broken.py", "raise RuntimeError('boom')\n")
        engine, output = _build(root, str(config_path), oneshot=True)

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        assert "good" in engine._plugins, "the good sibling still loads"
        assert _load_lines(output) == [], "no load line under --run / --exec"
        assert ("Plugin error: broken.py: boom", "red") in output, "an error is never silent"

    def test_skipped_file_reported(self, rig):
        # Arrange
        root, config_path = rig
        _write_plugin(config_path.parent / "plugin", "noop.py", "X = 1\n")
        engine, output = _build(root, str(config_path))

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        expected = ("Skipped noop.py - nothing exported (see plugin docs)", "yellow")
        assert expected in output, "a file with nothing to register is reported"
