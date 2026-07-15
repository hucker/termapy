"""Synopsis grammar validation: load-time chokepoint + registry conformance.

Every command flows through ``PluginInfo.__post_init__``, which rejects a
malformed args synopsis at registration time -- so an f-string escape
artifact like ``"{{filename}}"`` (review finding K2) fails loud at boot
instead of rendering literal doubled braces in ``/help``.
"""

import json

import pytest

from termapy.plugins import (
    CmdResult,
    PluginInfo,
    builtins_dir,
    load_plugins_from_dir,
    validate_synopsis,
)
from termapy.repl import ReplEngine


def _noop(ctx, args):
    return CmdResult.ok(value="")


# -- validate_synopsis (pure unit) --------------------------------------------


class TestValidateSynopsis:
    @pytest.mark.parametrize(
        "args",
        [
            "",
            "{on|off}",
            "<name> <value>",
            "{name} {N}",
            "<file> fmt=<spec> records=<N> {mode=new|append} {cmd=... (must be last)}",
            '{algo{_le|_be}{_ascii}} <hex|"text"|~delay ...> {--dry-run}',
            "{width=8|16|32|64} {endian=be|le} bin=<hex>|asc=<text>",
            "{path|cmd=<command>}",
            "<filename|*> {--fix}",
        ],
    )
    def test_valid_synopses(self, args):
        # Act / Assert -- real shapes from the live registry all pass
        assert validate_synopsis(args) == "", f"expected valid: {args!r}"

    @pytest.mark.parametrize(
        ("args", "fragment"),
        [
            ("{{filename}}", "doubled"),  # the K2 artifact
            ("[N]", "square brackets"),
            ("[width=8|16] <x>", "square brackets"),
            ("{on | off}", "spaced alternation"),
            ("{unclosed", "unbalanced braces"),
            ("closed}", "unbalanced braces"),
            ("<a>>", "unbalanced angles"),
        ],
    )
    def test_invalid_synopses(self, args, fragment):
        # Act
        error = validate_synopsis(args)

        # Assert
        assert fragment in error, f"{args!r} should fail with {fragment!r}, got {error!r}"


# -- the chokepoint ------------------------------------------------------------


class TestChokepoint:
    def test_plugininfo_rejects_bad_synopsis(self):
        # Act / Assert -- construction is registration; it must fail loud
        with pytest.raises(ValueError, match="invalid args synopsis"):
            PluginInfo(name="bad", args="{{filename}}", help="h", handler=_noop)

    def test_error_names_the_command(self):
        # Act
        with pytest.raises(ValueError) as exc:
            PluginInfo(name="edit.run", args="[file]", help="h", handler=_noop)

        # Assert
        assert "/edit.run" in str(exc.value), "error names the offending command"

    def test_register_hook_rejects_bad_synopsis(self, tmp_path):
        # Arrange -- hooks build PluginInfo too (the K2 bug arrived this way)
        cfg = {"port": "COM4"}
        config_path = tmp_path / "sub" / "t.cfg"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps(cfg))
        for sub in ("plugin", "ss", "run"):
            (config_path.parent / sub).mkdir(exist_ok=True)
        eng = ReplEngine(cfg, str(config_path), lambda t, c=None: None)

        # Act / Assert
        with pytest.raises(ValueError, match="invalid args synopsis"):
            eng.register_hook("badhook", "{{filename}}", "help", _noop)


# -- registry conformance --------------------------------------------------------


class TestRegistryConformance:
    def test_all_builtin_synopses_valid(self, tmp_path):
        # Arrange -- constructing the engine registers every builtin, so
        # __post_init__ has already enforced the grammar; this walk documents
        # the invariant and pins the count above zero.
        cfg = {"port": "COM4"}
        config_path = tmp_path / "sub" / "t.cfg"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps(cfg))
        for sub in ("plugin", "ss", "run"):
            (config_path.parent / sub).mkdir(exist_ok=True)
        eng = ReplEngine(cfg, str(config_path), lambda t, c=None: None)

        # Act -- load the builtins dir directly: a synopsis rejection makes
        # the loader DROP the whole plugin file, so the registry walk alone
        # would pass vacuously over the missing commands.  errors == [] is
        # the real invariant.
        result = load_plugins_from_dir(builtins_dir(), source="built-in")
        bad = {
            name: validate_synopsis(info.args)
            for name, info in eng._plugins.items()
            if validate_synopsis(info.args)
        }

        # Assert
        assert result.errors == [], (
            f"builtin plugin files rejected at load: {result.errors}"
        )
        assert bad == {}, f"registered synopses violating the grammar: {bad}"
        assert len(eng._plugins) > 50, "registry populated (walk is not vacuous)"

    def test_demo_plugin_synopses_valid(self):
        # Arrange / Act -- the demo example plugins are the copy-me templates;
        # loading the dir constructs PluginInfo for each (validation fires).
        # builtins_dir() is .../builtins/commands; demo plugins are a sibling
        demo_plugin_dir = builtins_dir().parent / "demo" / "plugin"
        result = load_plugins_from_dir(demo_plugin_dir, source="demo")

        # Assert
        assert result.errors == [], (
            f"demo plugin files rejected at load: {result.errors}"
        )
        assert result.plugins, "demo plugin dir loaded (not vacuous)"
        bad = {
            p.name: validate_synopsis(p.args)
            for p in result.plugins
            if validate_synopsis(p.args)
        }
        assert bad == {}, f"demo plugin synopses violating the grammar: {bad}"
