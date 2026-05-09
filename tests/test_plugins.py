"""Tests for plugin loading, COMMAND dataclass, and PluginContext."""

import pytest

from termapy.plugins import (
    CapabilitySet,
    PluginConfig,
    PluginContext,
    load_plugins_from_dir,
)


class TestCapabilitySet:
    """Algebra tests for the capability declaration dataclass."""

    def test_empty_satisfies_empty(self):
        """A command with no needs runs in any environment."""
        # Arrange
        needs = CapabilitySet()
        env = CapabilitySet()

        # Act
        actual = needs.satisfied_by(env)

        # Assert
        assert actual is True, "empty needs always satisfied"

    def test_needs_satisfied_when_env_provides(self):
        """Single need met by an env that provides it."""
        # Arrange
        needs = CapabilitySet(block_until=True)
        env = CapabilitySet(block_until=True, confirm_dialog=True)

        # Act
        actual = needs.satisfied_by(env)

        # Assert
        assert actual is True, "superset env satisfies needs"

    def test_needs_unsatisfied_when_env_missing_it(self):
        """Need unmet when env lacks the capability."""
        # Arrange
        needs = CapabilitySet(block_until=True)
        env = CapabilitySet(ui_notify=True)

        # Act
        actual = needs.satisfied_by(env)

        # Assert
        assert actual is False, "disjoint env does not satisfy"

    def test_missing_from_lists_unmet_fields(self):
        """missing_from reports field names in declaration order."""
        # Arrange - needs two things; env provides one of them
        needs = CapabilitySet(block_until=True, ui_notify=True)
        env = CapabilitySet(block_until=True)

        # Act
        actual = needs.missing_from(env)

        # Assert
        expected = ["ui_notify"]
        assert actual == expected, f"{actual} == {expected}"

    def test_missing_from_empty_when_fully_satisfied(self):
        """missing_from returns an empty list when all needs met."""
        # Arrange
        needs = CapabilitySet(block_until=True)
        env = CapabilitySet(block_until=True)

        # Act
        actual = needs.missing_from(env)

        # Assert
        expected = []
        assert actual == expected, f"{actual} == {expected}"

    def test_union_combines_both_sides(self):
        """union() produces a set containing every field set in either input."""
        # Arrange
        a = CapabilitySet(block_until=True)
        b = CapabilitySet(ui_notify=True)

        # Act
        actual = a.union(b)

        # Assert
        expected = CapabilitySet(block_until=True, ui_notify=True)
        assert actual == expected, f"{actual} == {expected}"

    def test_frozen_prevents_mutation(self):
        """CapabilitySet is immutable so environments can't mutate each other's caps."""
        # Arrange
        caps = CapabilitySet(block_until=True)

        # Act / Assert
        with pytest.raises(Exception):
            caps.block_until = False  # ty: ignore[invalid-assignment]

    def test_typo_raises_at_construction(self):
        """Typos in field names fail loudly at construction time."""
        # Act / Assert - a misspelled field is not silently ignored.
        with pytest.raises(TypeError):
            CapabilitySet(bloc_until=True)  # ty: ignore[unknown-argument]

    def test_baseline_defaults_true(self):
        """Baseline capabilities default True so every environment has them."""
        # Arrange
        caps = CapabilitySet()

        # Assert - the four baseline capabilities are on by default
        assert caps.terminal_output is True, "terminal_output baseline"
        assert caps.serial_io is True, "serial_io baseline"
        assert caps.dispatch is True, "dispatch baseline"
        assert caps.config_read is True, "config_read baseline"

    def test_restrictive_defaults_false(self):
        """Restrictive capabilities default False so commands opt in."""
        # Arrange
        caps = CapabilitySet()

        # Assert - restrictive fields are off by default
        assert caps.block_until is False, "block_until default off"
        assert caps.confirm_dialog is False, "confirm_dialog default off"
        assert caps.ui_notify is False, "ui_notify default off"
        assert caps.screen_capture is False, "screen_capture default off"
        assert caps.serial_connected is False, "serial_connected default off"

    def test_restricted_env_fails_baseline_command(self):
        """A restricted env that disables a baseline capability gates
        commands that rely on the default ``CapabilitySet()`` (baseline True).
        """
        # Arrange - a hypothetical sandbox with serial_io disabled.
        restricted = CapabilitySet(serial_io=False)
        # A command that implicitly needs serial_io (via the baseline
        # default True) doesn't declare it, but still has it True.
        default_needs = CapabilitySet()

        # Act
        missing = default_needs.missing_from(restricted)

        # Assert - serial_io surfaces as missing.
        assert "serial_io" in missing, "baseline gap detected"


@pytest.fixture
def plugin_dir(tmp_path):
    """Create a temp directory for plugin files."""
    return tmp_path / "plugins"


def _write_plugin(folder, filename, content):
    """Helper to write a plugin file into a directory."""
    folder.mkdir(exist_ok=True)
    (folder / filename).write_text(content, encoding="utf-8")


class TestLoadPlugins:
    def test_loads_valid_plugin(self, plugin_dir):
        # Arrange
        _write_plugin(plugin_dir, "hello.py", '''
from termapy.plugins import Command

def _handler(ctx, args):
    pass

COMMAND = Command(
    name="hello",
    args="{name}",
    help="Say hello.",
    handler=_handler,
)
''')

        # Act
        result = load_plugins_from_dir(plugin_dir, "test")

        # Assert
        assert len(result.plugins) == 1, "one plugin loaded"
        assert result.plugins[0].name == "hello", "correct name"
        assert result.plugins[0].args == "{name}", "correct args"
        assert result.plugins[0].help == "Say hello.", "correct help text"
        assert result.plugins[0].source == "test", "source tag preserved"

    def test_skips_files_without_command(self, plugin_dir):
        _write_plugin(plugin_dir, "bad.py", '''
def handler(ctx, args): pass
''')
        result = load_plugins_from_dir(plugin_dir, "test")
        assert len(result.plugins) == 0, "skipped - no COMMAND"
        assert result.skipped == ["bad.py"], "file name reported"

    def test_skips_files_without_name(self, plugin_dir):
        _write_plugin(plugin_dir, "bad.py", '''
from termapy.plugins import Command
COMMAND = Command(help="Missing name.")
''')
        result = load_plugins_from_dir(plugin_dir, "test")
        assert len(result.plugins) == 0, "skipped - no name in COMMAND"
        assert result.skipped == ["bad.py"], "file name reported"

    def test_skips_underscore_files(self, plugin_dir):
        _write_plugin(plugin_dir, "_private.py", '''
from termapy.plugins import Command
def _handler(ctx, args): pass
COMMAND = Command(name="private", help="Should be skipped.", handler=_handler)
''')
        _write_plugin(plugin_dir, "__init__.py", "")
        result = load_plugins_from_dir(plugin_dir, "test")
        assert len(result.plugins) == 0, "underscore-prefixed files skipped"

    def test_nonexistent_dir_returns_empty(self, tmp_path):
        result = load_plugins_from_dir(tmp_path / "nope", "test")
        assert result.plugins == [], "no directory = empty list"

    def test_multiple_plugins_sorted(self, plugin_dir):
        # Arrange
        _write_plugin(plugin_dir, "beta.py", '''
from termapy.plugins import Command
def _handler(ctx, args): pass
COMMAND = Command(name="beta", help="B", handler=_handler)
''')
        _write_plugin(plugin_dir, "alpha.py", '''
from termapy.plugins import Command
def _handler(ctx, args): pass
COMMAND = Command(name="alpha", help="A", handler=_handler)
''')

        # Act
        result = load_plugins_from_dir(plugin_dir, "test")

        # Assert
        actual_names = [p.name for p in result.plugins]
        expected_names = ["alpha", "beta"]
        assert actual_names == expected_names, "loaded in alphabetical order"

    def test_broken_plugin_reports_error(self, plugin_dir):
        # Arrange
        _write_plugin(plugin_dir, "broken.py", "raise RuntimeError('boom')")
        _write_plugin(plugin_dir, "good.py", '''
from termapy.plugins import Command
def _handler(ctx, args): pass
COMMAND = Command(name="good", help="Works.", handler=_handler)
''')

        # Act
        result = load_plugins_from_dir(plugin_dir, "test")

        # Assert
        assert len(result.plugins) == 1, "broken plugin skipped"
        assert result.plugins[0].name == "good", "good plugin still loaded"
        assert len(result.errors) == 1, "error reported"
        assert "broken.py" in result.errors[0], "error mentions file"


class TestSubCommands:
    def test_flattens_sub_commands(self, plugin_dir):
        # Arrange
        _write_plugin(plugin_dir, "tool.py", '''
from termapy.plugins import Command

def _run(ctx, args): pass
def _status(ctx, args): pass

COMMAND = Command(
    name="tool",
    help="A tool.",
    sub_commands={
        "run": Command(args="<file>", help="Run a file.", handler=_run),
        "status": Command(help="Show status.", handler=_status),
    },
)
''')

        # Act
        result = load_plugins_from_dir(plugin_dir, "test")

        # Assert
        names = [p.name for p in result.plugins]
        assert "tool" in names, "root node registered"
        assert "tool.run" in names, "sub_command registered with dot"
        assert "tool.status" in names, "sub_command registered with dot"
        assert len(result.plugins) == 3, "root + 2 subcommands"

    def test_root_has_children(self, plugin_dir):
        # Arrange
        _write_plugin(plugin_dir, "tool.py", '''
from termapy.plugins import Command

def _a(ctx, args): pass
def _b(ctx, args): pass

COMMAND = Command(
    name="tool",
    help="A tool.",
    sub_commands={
        "alpha": Command(help="First.", handler=_a),
        "beta": Command(help="Second.", handler=_b),
    },
)
''')

        # Act
        result = load_plugins_from_dir(plugin_dir, "test")
        root = [p for p in result.plugins if p.name == "tool"][0]

        # Assert
        assert root.children == ["tool.alpha", "tool.beta"], "children tracked"

    def test_nested_sub_commands(self, plugin_dir):
        # Arrange
        _write_plugin(plugin_dir, "tool.py", '''
from termapy.plugins import Command

def _leaf(ctx, args): pass

COMMAND = Command(
    name="tool",
    help="A tool.",
    sub_commands={
        "sub": Command(
            help="Sub group.",
            sub_commands={
                "leaf": Command(help="A leaf.", handler=_leaf),
            },
        ),
    },
)
''')

        # Act
        result = load_plugins_from_dir(plugin_dir, "test")
        names = [p.name for p in result.plugins]

        # Assert
        assert "tool" in names, "root"
        assert "tool.sub" in names, "interior"
        assert "tool.sub.leaf" in names, "leaf"
        assert len(result.plugins) == 3, "root + interior + leaf"

    def test_flatten_preserves_callable_long_help(self, plugin_dir):
        """A callable long_help survives the Command -> PluginInfo copy.

        Guards against a future refactor that might coerce long_help to str
        at flatten time. Dynamic help is a contract with the renderer; the
        callable must reach it unchanged.
        """
        # Arrange
        _write_plugin(plugin_dir, "dyn.py", '''
from termapy.plugins import Command

def _handler(ctx, args): pass
def _dyn(ctx): return "live text"

COMMAND = Command(
    name="dyn",
    help="Dynamic help test.",
    long_help=_dyn,
    handler=_handler,
)
''')

        # Act
        result = load_plugins_from_dir(plugin_dir, "test")
        info = [p for p in result.plugins if p.name == "dyn"][0]

        # Assert - long_help is still callable (same identity isn't possible
        # across module loads, but we can check it's callable and returns our text)
        assert callable(info.long_help), "callable survived flatten"
        assert info.long_help(None) == "live text", "callable still works"

    def test_interior_gets_synthetic_handler(self, plugin_dir):
        # Arrange
        _write_plugin(plugin_dir, "tool.py", '''
from termapy.plugins import Command

def _leaf(ctx, args): pass

COMMAND = Command(
    name="tool",
    help="A tool.",
    sub_commands={
        "leaf": Command(help="A leaf.", handler=_leaf),
    },
)
''')

        # Act
        result = load_plugins_from_dir(plugin_dir, "test")
        root = [p for p in result.plugins if p.name == "tool"][0]

        # Assert
        assert root.handler is not None, "synthetic handler created"
        assert callable(root.handler), "handler is callable"

    def test_defaults_for_missing_fields(self, plugin_dir):
        _write_plugin(plugin_dir, "bare.py", '''
from termapy.plugins import Command
def _handler(ctx, args): pass
COMMAND = Command(name="bare", help="Bare.", handler=_handler)
''')
        result = load_plugins_from_dir(plugin_dir, "test")
        assert result.plugins[0].args == "", "missing args defaults to empty"
        assert result.plugins[0].long_help == "", "missing long_help defaults to empty"
        assert result.plugins[0].children == [], "no children for leaf"


class TestTransformLoading:
    def test_transform_loaded(self, plugin_dir):
        # Arrange
        _write_plugin(plugin_dir, "xform.py", '''
from termapy.plugins import Transform
def _repl_xf(s): return s.upper()
def _serial_xf(s): return s.lower()
TRANSFORM = Transform(
    name="vars", help="Expand variables.",
    repl=_repl_xf, serial=_serial_xf,
)
''')

        # Act
        result = load_plugins_from_dir(plugin_dir, "test")

        # Assert
        assert len(result.transforms) == 1, "one transform loaded"
        assert result.transforms[0].name == "vars", "correct name"
        assert result.transforms[0].repl("hello") == "HELLO", "repl works"
        assert result.transforms[0].serial("HELLO") == "hello", "serial works"
        assert result.transforms[0].source == "test", "source propagated"

    def test_transform_only_file_not_skipped(self, plugin_dir):
        # Arrange
        _write_plugin(plugin_dir, "xonly.py", '''
from termapy.plugins import Transform
TRANSFORM = Transform(name="xonly", help="Transform only.", repl=lambda s: s)
''')

        # Act
        result = load_plugins_from_dir(plugin_dir, "test")

        # Assert
        assert len(result.transforms) == 1, "transform loaded"
        assert len(result.plugins) == 0, "no commands"
        assert len(result.skipped) == 0, "not counted as skipped"

    def test_file_with_both_command_and_transform(self, plugin_dir):
        # Arrange
        _write_plugin(plugin_dir, "both.py", '''
from termapy.plugins import Command, Transform
def _handler(ctx, args): pass
COMMAND = Command(name="both", help="A command.", handler=_handler)
TRANSFORM = Transform(name="both_xf", help="A transform.", serial=lambda s: s)
''')

        # Act
        result = load_plugins_from_dir(plugin_dir, "test")

        # Assert
        assert len(result.plugins) == 1, "command loaded"
        assert len(result.transforms) == 1, "transform loaded"

    def test_no_transforms_by_default(self, plugin_dir):
        # Arrange
        _write_plugin(plugin_dir, "plain.py", '''
from termapy.plugins import Command
def _handler(ctx, args): pass
COMMAND = Command(name="plain", help="No transforms.", handler=_handler)
''')

        # Act
        result = load_plugins_from_dir(plugin_dir, "test")

        # Assert
        assert len(result.transforms) == 0, "no transforms loaded"


# -- serial_io context manager ------------------------------------------------


class TestSerialIo:
    def test_calls_claim_and_release(self):
        # Arrange
        from termapy.plugins import IOHandle, SerialHandle
        calls = []
        ctx = PluginContext(
            io=IOHandle(write=lambda t, c=None: None),
            serial=SerialHandle(
                claim=lambda: calls.append("claim"),
                release=lambda: calls.append("release"),
            ),
        )

        # Act
        with ctx.serial.io():
            calls.append("body")

        # Assert
        assert calls == ["claim", "body", "release"], "claim before, release after"

    def test_releases_on_exception(self):
        # Arrange
        from termapy.plugins import IOHandle, SerialHandle
        calls = []
        ctx = PluginContext(
            io=IOHandle(write=lambda t, c=None: None),
            serial=SerialHandle(
                claim=lambda: calls.append("claim"),
                release=lambda: calls.append("release"),
            ),
        )

        # Act
        with pytest.raises(ValueError):
            with ctx.serial.io():
                raise ValueError("boom")

        # Assert
        assert "release" in calls, "release called despite exception"


class TestNamespaces:
    """Tests for ctx.ns() - the session-scoped namespace primitive."""

    def _ctx(self):
        from termapy.plugins import IOHandle
        return PluginContext(io=IOHandle(write=lambda t, c=None: None))

    def test_lazy_creation_returns_empty_dict(self):
        # Arrange
        ctx = self._ctx()

        # Act
        actual = ctx.ns("fresh")

        # Assert
        expected: dict = {}
        assert actual == expected, "new namespace should be an empty dict"

    def test_mutation_visible_on_next_access(self):
        # Arrange
        ctx = self._ctx()
        ctx.ns("seq")["counter"] = 42

        # Act
        actual = ctx.ns("seq")["counter"]

        # Assert
        expected = 42
        assert actual == expected, "mutation should persist across ns() calls"

    def test_same_name_returns_same_dict(self):
        # Arrange
        ctx = self._ctx()
        first = ctx.ns("flags")

        # Act
        second = ctx.ns("flags")

        # Assert
        assert first is second, "ns() must return the same dict for the same name"

    def test_different_names_are_independent(self):
        # Arrange
        ctx = self._ctx()

        # Act
        ctx.ns("a")["x"] = 1
        ctx.ns("b")["x"] = 2

        # Assert
        actual_a = ctx.ns("a")["x"]
        actual_b = ctx.ns("b")["x"]
        assert actual_a == 1, "namespace a should not see b's writes"
        assert actual_b == 2, "namespace b should not see a's writes"

    def test_namespaces_isolated_per_context(self):
        # Arrange
        ctx1 = self._ctx()
        ctx2 = self._ctx()
        ctx1.ns("seq")["counter"] = 99

        # Act
        actual = ctx2.ns("seq")

        # Assert
        expected: dict = {}
        assert actual == expected, "each PluginContext has its own namespace registry"

    def test_dict_like_api_works(self):
        # Arrange
        ctx = self._ctx()
        ns = ctx.ns("myplugin")

        # Act
        ns["a"] = 1
        ns.setdefault("b", 2)
        ns.update({"c": 3})

        # Assert
        actual = dict(ns)
        expected = {"a": 1, "b": 2, "c": 3}
        assert actual == expected, "namespace should support full dict interface"

    def test_clear_empties_in_place(self):
        # Arrange
        ctx = self._ctx()
        ns = ctx.ns("seq")
        ns["x"] = 1
        ns["y"] = 2

        # Act
        ns.clear()

        # Assert
        actual = ctx.ns("seq")
        expected: dict = {}
        assert actual == expected, "clear() should empty the namespace"
        assert actual is ns, "clear() preserves identity so cached refs stay valid"


class TestLifecycleHookDiscovery:
    """Tests for loader discovery of on_app_start / on_script_start / etc."""

    def test_discovers_on_app_start(self, plugin_dir):
        # Arrange
        _write_plugin(plugin_dir, "hooked.py", '''
from termapy.plugins import Command

def _handler(ctx, args):
    pass

def on_app_start(ctx):
    ctx.ns("hooked")["started"] = True

COMMAND = Command(name="hooked", help="x", handler=_handler)
''')

        # Act
        result = load_plugins_from_dir(plugin_dir, "test")

        # Assert
        names = [h.name for h in result.lifecycle_hooks]
        assert "on_app_start" in names, "on_app_start hook should be discovered"
        assert len(result.lifecycle_hooks) == 1, "only one hook exported"
        assert result.lifecycle_hooks[0].plugin == "hooked", "plugin stem recorded"

    def test_discovers_all_four_hooks(self, plugin_dir):
        # Arrange
        _write_plugin(plugin_dir, "fully_hooked.py", '''
from termapy.plugins import Command

def _handler(ctx, args):
    pass

def on_app_start(ctx): pass
def on_app_stop(ctx): pass
def on_script_start(ctx): pass
def on_script_stop(ctx): pass

COMMAND = Command(name="fully_hooked", help="x", handler=_handler)
''')

        # Act
        result = load_plugins_from_dir(plugin_dir, "test")

        # Assert
        actual = sorted(h.name for h in result.lifecycle_hooks)
        expected = ["on_app_start", "on_app_stop", "on_script_start", "on_script_stop"]
        assert actual == expected, "all four lifecycle hooks discovered"

    def test_non_hook_functions_ignored(self, plugin_dir):
        # Arrange
        _write_plugin(plugin_dir, "noisy.py", '''
from termapy.plugins import Command

def _handler(ctx, args):
    pass

def on_something_else(ctx):
    """Not a recognized hook name."""
    pass

def helper():
    pass

COMMAND = Command(name="noisy", help="x", handler=_handler)
''')

        # Act
        result = load_plugins_from_dir(plugin_dir, "test")

        # Assert
        assert result.lifecycle_hooks == [], "unknown function names are not hooks"

    def test_hook_only_plugin_not_skipped(self, plugin_dir):
        """A file that exports only a hook (no COMMAND) is still a valid plugin."""
        # Arrange
        _write_plugin(plugin_dir, "hook_only.py", '''
def on_app_start(ctx):
    pass
''')

        # Act
        result = load_plugins_from_dir(plugin_dir, "test")

        # Assert
        assert len(result.lifecycle_hooks) == 1, "hook-only plugin is loaded"
        assert "hook_only.py" not in result.skipped, "not reported as skipped"


class TestFireLifecycle:
    """Tests for ReplEngine.fire_lifecycle - ordering, filtering, isolation."""

    def _engine(self, tmp_path):
        from termapy.repl import ReplEngine
        cfg_path = tmp_path / "test.cfg"
        cfg_path.write_text("{}", encoding="utf-8")
        return ReplEngine({}, str(cfg_path), lambda t, c=None: None)

    def test_fire_calls_matching_hooks_only(self, tmp_path):
        # Arrange
        from termapy.plugins import LifecycleHook
        calls = []
        eng = self._engine(tmp_path)
        eng._lifecycle_hooks = []  # clear builtin hooks for isolation
        eng.register_lifecycle_hook(LifecycleHook(
            name="on_app_start",
            handler=lambda ctx: calls.append("start"),
        ))
        eng.register_lifecycle_hook(LifecycleHook(
            name="on_app_stop",
            handler=lambda ctx: calls.append("stop"),
        ))

        # Act
        eng.fire_lifecycle("on_app_start")

        # Assert
        assert calls == ["start"], "only the named hook fires"

    def test_fire_preserves_registration_order(self, tmp_path):
        # Arrange
        from termapy.plugins import LifecycleHook
        calls = []
        eng = self._engine(tmp_path)
        eng._lifecycle_hooks = []
        for label in ("a", "b", "c"):
            eng.register_lifecycle_hook(LifecycleHook(
                name="on_app_start",
                handler=lambda ctx, lbl=label: calls.append(lbl),
            ))

        # Act
        eng.fire_lifecycle("on_app_start")

        # Assert
        actual = calls
        expected = ["a", "b", "c"]
        assert actual == expected, "hooks fire in registration order"

    def test_exception_in_one_hook_does_not_block_others(self, tmp_path):
        # Arrange
        from termapy.plugins import LifecycleHook
        calls = []
        eng = self._engine(tmp_path)
        eng._lifecycle_hooks = []

        def bad_hook(ctx):
            raise RuntimeError("boom")

        eng.register_lifecycle_hook(LifecycleHook(
            name="on_app_start",
            handler=bad_hook,
            plugin="bad",
        ))
        eng.register_lifecycle_hook(LifecycleHook(
            name="on_app_start",
            handler=lambda ctx: calls.append("survivor"),
        ))

        # Act
        eng.fire_lifecycle("on_app_start")

        # Assert
        assert calls == ["survivor"], "later hooks run despite earlier exception"

    def test_fire_with_no_matching_hooks_is_noop(self, tmp_path):
        # Arrange
        eng = self._engine(tmp_path)
        eng._lifecycle_hooks = []

        # Act + Assert - just must not raise
        eng.fire_lifecycle("on_app_stop")


# ── PluginConfig ────────────────────────────────────────────────────────────


class TestPluginConfig:

    def test_get_set_save_reload(self, tmp_path):
        # Arrange
        path = tmp_path / "test.cfg"
        cfg = PluginConfig(path)

        # Act
        cfg["key1"] = "value1"
        cfg["key2"] = 42
        cfg.save()

        # Assert - reload from disk
        cfg2 = PluginConfig(path)
        assert cfg2["key1"] == "value1", "should persist string value"
        assert cfg2["key2"] == 42, "should persist int value"

    def test_get_default(self, tmp_path):
        # Arrange
        cfg = PluginConfig(tmp_path / "missing.cfg")

        # Act / Assert
        assert cfg.get("nope", "fallback") == "fallback", "should return default for missing key"

    def test_missing_file_returns_empty(self, tmp_path):
        # Arrange
        cfg = PluginConfig(tmp_path / "missing.cfg")

        # Assert
        assert len(cfg) == 0, "missing file should yield empty config"

    def test_pop(self, tmp_path):
        # Arrange
        path = tmp_path / "test.cfg"
        cfg = PluginConfig(path)
        cfg["key"] = "value"
        cfg.save()

        # Act
        cfg2 = PluginConfig(path)
        actual = cfg2.pop("key", None)
        cfg2.save()

        # Assert
        assert actual == "value", "pop should return the value"
        cfg3 = PluginConfig(path)
        assert "key" not in cfg3, "key should be gone after pop + save"

    def test_contains(self, tmp_path):
        # Arrange
        cfg = PluginConfig(tmp_path / "test.cfg")
        cfg["present"] = True

        # Assert
        assert "present" in cfg, "should report key as present"
        assert "absent" not in cfg, "should report missing key as absent"

    def test_del(self, tmp_path):
        # Arrange
        cfg = PluginConfig(tmp_path / "test.cfg")
        cfg["key"] = "value"

        # Act
        del cfg["key"]

        # Assert
        assert "key" not in cfg, "key should be gone after del"

    def test_creates_parent_dirs(self, tmp_path):
        # Arrange
        path = tmp_path / "deep" / "nested" / "test.cfg"
        cfg = PluginConfig(path)
        cfg["key"] = "value"

        # Act
        cfg.save()

        # Assert
        assert path.exists(), "save should create parent directories"

    def test_corrupt_file_returns_empty(self, tmp_path):
        # Arrange
        path = tmp_path / "bad.cfg"
        path.write_text("not json{{{", encoding="utf-8")

        # Act
        cfg = PluginConfig(path)

        # Assert
        assert len(cfg) == 0, "corrupt JSON should yield empty config"

    def test_items(self, tmp_path):
        # Arrange
        cfg = PluginConfig(tmp_path / "test.cfg")
        cfg["a"] = 1
        cfg["b"] = 2

        # Act
        actual = dict(cfg.items())

        # Assert
        assert actual == {"a": 1, "b": 2}, "items() should return all key-value pairs"


class TestPluginContextPluginCfg:

    def test_returns_plugin_config(self, tmp_path):
        # Arrange
        cfg_path = tmp_path / "test.cfg"
        cfg_path.write_text("{}", encoding="utf-8")
        from termapy.plugins import IOHandle
        ctx = PluginContext(io=IOHandle(write=lambda *a, **kw: None), config_path=str(cfg_path))

        # Act
        pcfg = ctx.plugin_cfg("myplugin")

        # Assert
        expected = tmp_path / "plugin" / "myplugin.cfg"
        assert pcfg.path == expected, "should resolve to <config_dir>/plugin/<name>.cfg"

    def test_caches_across_calls(self, tmp_path):
        # Arrange
        cfg_path = tmp_path / "test.cfg"
        cfg_path.write_text("{}", encoding="utf-8")
        from termapy.plugins import IOHandle
        ctx = PluginContext(io=IOHandle(write=lambda *a, **kw: None), config_path=str(cfg_path))

        # Act
        pcfg1 = ctx.plugin_cfg("myplugin")
        pcfg2 = ctx.plugin_cfg("myplugin")

        # Assert
        assert pcfg1 is pcfg2, "should return the same instance on repeated calls"

    def test_raises_without_config_path(self):
        # Arrange
        from termapy.plugins import IOHandle
        ctx = PluginContext(io=IOHandle(write=lambda *a, **kw: None), config_path="")

        # Act / Assert
        import pytest
        with pytest.raises(RuntimeError, match="no config loaded"):
            ctx.plugin_cfg("myplugin")
