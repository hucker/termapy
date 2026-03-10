"""Tests for plugin loading, PACKAGE namespacing, and PluginContext."""

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
NAME = "hello"
ARGS = "{name}"
HELP = "Say hello."

def handler(ctx, args):
    pass
''')

        # Act
        actual = load_plugins_from_dir(plugin_dir, "test")

        # Assert
        assert len(actual) == 1  # one plugin loaded
        assert actual[0].name == "hello"  # correct name
        assert actual[0].args == "{name}"  # correct args
        assert actual[0].help == "Say hello."  # correct help text
        assert actual[0].source == "test"  # source tag preserved

    def test_skips_files_without_name(self, plugin_dir):
        _write_plugin(plugin_dir, "bad.py", '''
ARGS = ""
HELP = "Missing NAME."
def handler(ctx, args): pass
''')
        actual = load_plugins_from_dir(plugin_dir, "test")
        assert len(actual) == 0  # skipped — no NAME

    def test_skips_files_without_handler(self, plugin_dir):
        _write_plugin(plugin_dir, "bad.py", '''
NAME = "bad"
ARGS = ""
HELP = "Missing handler."
''')
        actual = load_plugins_from_dir(plugin_dir, "test")
        assert len(actual) == 0  # skipped — no handler

    def test_skips_underscore_files(self, plugin_dir):
        _write_plugin(plugin_dir, "_private.py", '''
NAME = "private"
ARGS = ""
HELP = "Should be skipped."
def handler(ctx, args): pass
''')
        _write_plugin(plugin_dir, "__init__.py", "")
        actual = load_plugins_from_dir(plugin_dir, "test")
        assert len(actual) == 0  # underscore-prefixed files skipped

    def test_nonexistent_dir_returns_empty(self, tmp_path):
        actual = load_plugins_from_dir(tmp_path / "nope", "test")
        assert actual == []  # no directory = empty list

    def test_multiple_plugins_sorted(self, plugin_dir):
        # Arrange
        _write_plugin(plugin_dir, "beta.py", '''
NAME = "beta"
ARGS = ""
HELP = "B"
def handler(ctx, args): pass
''')
        _write_plugin(plugin_dir, "alpha.py", '''
NAME = "alpha"
ARGS = ""
HELP = "A"
def handler(ctx, args): pass
''')

        # Act
        actual = load_plugins_from_dir(plugin_dir, "test")

        # Assert
        actual_names = [p.name for p in actual]
        expected_names = ["alpha", "beta"]
        assert actual_names == expected_names  # loaded in alphabetical order

    def test_broken_plugin_skipped(self, plugin_dir):
        # Arrange
        _write_plugin(plugin_dir, "broken.py", "raise RuntimeError('boom')")
        _write_plugin(plugin_dir, "good.py", '''
NAME = "good"
ARGS = ""
HELP = "Works."
def handler(ctx, args): pass
''')

        # Act
        actual = load_plugins_from_dir(plugin_dir, "test")

        # Assert
        assert len(actual) == 1  # broken plugin skipped
        assert actual[0].name == "good"  # good plugin still loaded


class TestPackageNamespacing:
    def test_package_prefix(self, plugin_dir):
        _write_plugin(plugin_dir, "flash.py", '''
PACKAGE = "acme"
NAME = "flash"
ARGS = "<firmware>"
HELP = "Flash firmware."
def handler(ctx, args): pass
''')
        actual = load_plugins_from_dir(plugin_dir, "test")
        assert len(actual) == 1  # one plugin loaded
        assert actual[0].name == "acme.flash"  # package.name format

    def test_no_package_no_prefix(self, plugin_dir):
        _write_plugin(plugin_dir, "simple.py", '''
NAME = "simple"
ARGS = ""
HELP = "No package."
def handler(ctx, args): pass
''')
        actual = load_plugins_from_dir(plugin_dir, "test")
        assert actual[0].name == "simple"  # no prefix without PACKAGE

    def test_package_name_lowercased(self, plugin_dir):
        _write_plugin(plugin_dir, "cmd.py", '''
PACKAGE = "MyPkg"
NAME = "CMD"
ARGS = ""
HELP = "Test."
def handler(ctx, args): pass
''')
        actual = load_plugins_from_dir(plugin_dir, "test")
        assert actual[0].name == "mypkg.cmd"  # both package and name lowercased

    def test_defaults_for_missing_args_help(self, plugin_dir):
        _write_plugin(plugin_dir, "bare.py", '''
NAME = "bare"
def handler(ctx, args): pass
''')
        actual = load_plugins_from_dir(plugin_dir, "test")
        assert actual[0].args == ""  # missing ARGS defaults to ""
        assert actual[0].help == ""  # missing HELP defaults to ""


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
