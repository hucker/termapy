"""Tests for plugin loading and PACKAGE namespacing."""

import pytest

from terapy.plugins import PluginInfo, load_plugins_from_dir


@pytest.fixture
def plugin_dir(tmp_path):
    """Create a temp directory for plugin files."""
    return tmp_path / "plugins"


def _write_plugin(folder, filename, content):
    folder.mkdir(exist_ok=True)
    (folder / filename).write_text(content, encoding="utf-8")


class TestLoadPlugins:
    def test_loads_valid_plugin(self, plugin_dir):
        _write_plugin(plugin_dir, "hello.py", '''
NAME = "hello"
ARGS = "{name}"
HELP = "Say hello."

def handler(ctx, args):
    pass
''')
        plugins = load_plugins_from_dir(plugin_dir, "test")
        assert len(plugins) == 1
        assert plugins[0].name == "hello"
        assert plugins[0].args == "{name}"
        assert plugins[0].help == "Say hello."
        assert plugins[0].source == "test"

    def test_skips_files_without_name(self, plugin_dir):
        _write_plugin(plugin_dir, "bad.py", '''
ARGS = ""
HELP = "Missing NAME."
def handler(ctx, args): pass
''')
        plugins = load_plugins_from_dir(plugin_dir, "test")
        assert len(plugins) == 0

    def test_skips_files_without_handler(self, plugin_dir):
        _write_plugin(plugin_dir, "bad.py", '''
NAME = "bad"
ARGS = ""
HELP = "Missing handler."
''')
        plugins = load_plugins_from_dir(plugin_dir, "test")
        assert len(plugins) == 0

    def test_skips_underscore_files(self, plugin_dir):
        _write_plugin(plugin_dir, "_private.py", '''
NAME = "private"
ARGS = ""
HELP = "Should be skipped."
def handler(ctx, args): pass
''')
        _write_plugin(plugin_dir, "__init__.py", "")
        plugins = load_plugins_from_dir(plugin_dir, "test")
        assert len(plugins) == 0

    def test_nonexistent_dir_returns_empty(self, tmp_path):
        plugins = load_plugins_from_dir(tmp_path / "nope", "test")
        assert plugins == []

    def test_multiple_plugins_sorted(self, plugin_dir):
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
        plugins = load_plugins_from_dir(plugin_dir, "test")
        assert [p.name for p in plugins] == ["alpha", "beta"]

    def test_broken_plugin_skipped(self, plugin_dir):
        _write_plugin(plugin_dir, "broken.py", "raise RuntimeError('boom')")
        _write_plugin(plugin_dir, "good.py", '''
NAME = "good"
ARGS = ""
HELP = "Works."
def handler(ctx, args): pass
''')
        plugins = load_plugins_from_dir(plugin_dir, "test")
        assert len(plugins) == 1
        assert plugins[0].name == "good"


class TestPackageNamespacing:
    def test_package_prefix(self, plugin_dir):
        _write_plugin(plugin_dir, "flash.py", '''
PACKAGE = "acme"
NAME = "flash"
ARGS = "<firmware>"
HELP = "Flash firmware."
def handler(ctx, args): pass
''')
        plugins = load_plugins_from_dir(plugin_dir, "test")
        assert len(plugins) == 1
        assert plugins[0].name == "acme.flash"

    def test_no_package_no_prefix(self, plugin_dir):
        _write_plugin(plugin_dir, "simple.py", '''
NAME = "simple"
ARGS = ""
HELP = "No package."
def handler(ctx, args): pass
''')
        plugins = load_plugins_from_dir(plugin_dir, "test")
        assert plugins[0].name == "simple"

    def test_package_name_lowercased(self, plugin_dir):
        _write_plugin(plugin_dir, "cmd.py", '''
PACKAGE = "MyPkg"
NAME = "CMD"
ARGS = ""
HELP = "Test."
def handler(ctx, args): pass
''')
        plugins = load_plugins_from_dir(plugin_dir, "test")
        assert plugins[0].name == "mypkg.cmd"

    def test_defaults_for_missing_args_help(self, plugin_dir):
        _write_plugin(plugin_dir, "bare.py", '''
NAME = "bare"
def handler(ctx, args): pass
''')
        plugins = load_plugins_from_dir(plugin_dir, "test")
        assert plugins[0].args == ""
        assert plugins[0].help == ""
