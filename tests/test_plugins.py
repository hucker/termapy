"""Tests for plugin loading, COMMAND dataclass, and PluginContext."""

import pytest

from termapy.plugins import PluginContext, PluginInfo, load_plugins_from_dir


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
        assert len(result.plugins) == 1  # one plugin loaded
        assert result.plugins[0].name == "hello"  # correct name
        assert result.plugins[0].args == "{name}"  # correct args
        assert result.plugins[0].help == "Say hello."  # correct help text
        assert result.plugins[0].source == "test"  # source tag preserved

    def test_skips_files_without_command(self, plugin_dir):
        _write_plugin(plugin_dir, "bad.py", '''
def handler(ctx, args): pass
''')
        result = load_plugins_from_dir(plugin_dir, "test")
        assert len(result.plugins) == 0  # skipped — no COMMAND
        assert result.skipped == ["bad.py"]  # file name reported

    def test_skips_files_without_name(self, plugin_dir):
        _write_plugin(plugin_dir, "bad.py", '''
from termapy.plugins import Command
COMMAND = Command(help="Missing name.")
''')
        result = load_plugins_from_dir(plugin_dir, "test")
        assert len(result.plugins) == 0  # skipped — no name in COMMAND
        assert result.skipped == ["bad.py"]  # file name reported

    def test_skips_underscore_files(self, plugin_dir):
        _write_plugin(plugin_dir, "_private.py", '''
from termapy.plugins import Command
def _handler(ctx, args): pass
COMMAND = Command(name="private", help="Should be skipped.", handler=_handler)
''')
        _write_plugin(plugin_dir, "__init__.py", "")
        result = load_plugins_from_dir(plugin_dir, "test")
        assert len(result.plugins) == 0  # underscore-prefixed files skipped

    def test_nonexistent_dir_returns_empty(self, tmp_path):
        result = load_plugins_from_dir(tmp_path / "nope", "test")
        assert result.plugins == []  # no directory = empty list

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
        assert actual_names == expected_names  # loaded in alphabetical order

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
        assert len(result.plugins) == 1  # broken plugin skipped
        assert result.plugins[0].name == "good"  # good plugin still loaded
        assert len(result.errors) == 1  # error reported
        assert "broken.py" in result.errors[0]  # error mentions file


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
        assert "tool" in names  # root node registered
        assert "tool.run" in names  # sub_command registered with dot
        assert "tool.status" in names  # sub_command registered with dot
        assert len(result.plugins) == 3  # root + 2 subcommands

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
        assert root.children == ["tool.alpha", "tool.beta"]  # children tracked

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
        assert "tool" in names  # root
        assert "tool.sub" in names  # interior
        assert "tool.sub.leaf" in names  # leaf
        assert len(result.plugins) == 3  # root + interior + leaf

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
        assert root.handler is not None  # synthetic handler created
        assert callable(root.handler)  # handler is callable

    def test_defaults_for_missing_fields(self, plugin_dir):
        _write_plugin(plugin_dir, "bare.py", '''
from termapy.plugins import Command
def _handler(ctx, args): pass
COMMAND = Command(name="bare", help="Bare.", handler=_handler)
''')
        result = load_plugins_from_dir(plugin_dir, "test")
        assert result.plugins[0].args == ""  # missing args defaults to ""
        assert result.plugins[0].long_help == ""  # missing long_help defaults to ""
        assert result.plugins[0].children == []  # no children for leaf


# -- serial_io context manager ------------------------------------------------


class TestSerialIo:
    def test_calls_claim_and_release(self):
        # Arrange
        calls = []
        ctx = PluginContext(
            write=lambda t, c=None: None,
            serial_claim=lambda: calls.append("claim"),
            serial_release=lambda: calls.append("release"),
        )

        # Act
        with ctx.serial_io():
            calls.append("body")

        # Assert
        assert calls == ["claim", "body", "release"]  # claim before, release after

    def test_releases_on_exception(self):
        # Arrange
        calls = []
        ctx = PluginContext(
            write=lambda t, c=None: None,
            serial_claim=lambda: calls.append("claim"),
            serial_release=lambda: calls.append("release"),
        )

        # Act
        with pytest.raises(ValueError):
            with ctx.serial_io():
                raise ValueError("boom")

        # Assert
        assert "release" in calls  # release called despite exception
