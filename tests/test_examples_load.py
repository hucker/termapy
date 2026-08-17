"""Guard: every plugin under examples/plugins/ must load cleanly.

These files exist to teach the plugin API to users.  A stale example
that fails to load is anti-marketing -- worse than no example.  This
test catches the regression class where the PluginContext API moves
(handles refactor, ctx.write -> ctx.io.write, etc.) and an example is
left referencing the old shape.

Each example file is loaded via the same ``load_plugins_from_dir``
machinery the engine uses.  Any file that ends up in
``result.errors`` (failed import / load) fails the test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from termapy.plugins.loader import load_plugins_from_dir

_REPO_ROOT = Path(__file__).resolve().parents[1]
_EXAMPLES_DIR = _REPO_ROOT / "examples" / "plugins"


def _example_files() -> list[Path]:
    """Every .py file under examples/plugins/ (skip dunder files)."""
    if not _EXAMPLES_DIR.exists():
        return []
    return sorted(
        p for p in _EXAMPLES_DIR.glob("*.py") if not p.name.startswith("_")
    )


def test_examples_dir_exists():
    # Arrange / Act / Assert
    assert _EXAMPLES_DIR.is_dir(), (
        f"examples/plugins/ missing at {_EXAMPLES_DIR} -- if you moved it, "
        "update _EXAMPLES_DIR in this test"
    )


def test_examples_dir_not_empty():
    # Arrange / Act
    files = _example_files()
    # Assert
    assert files, "no example plugins found -- expected at least one"


@pytest.mark.parametrize("plugin_path", _example_files(), ids=lambda p: p.name)
def test_example_loads_cleanly(plugin_path: Path):
    """Each example loads via the engine's standard loader without errors."""
    # Arrange / Act -- load the whole directory; per-file failures are
    # reported as filenames in result.errors.  Re-loading the directory
    # per parametrize is cheap (~ms each).
    result = load_plugins_from_dir(_EXAMPLES_DIR, source="examples")

    # Assert -- this example's filename must not be in the errors list.
    failed_names = {Path(name).name for name in result.errors}
    assert plugin_path.name not in failed_names, (
        f"example {plugin_path.name} failed to load (it's in "
        f"result.errors: {sorted(failed_names)})"
    )

    # And the example must register at least one plugin (the COMMAND
    # export).  We can't trivially map plugins back to source files
    # (PluginInfo doesn't carry source_path), so we just sanity-check
    # that the load produced some plugins -- a regression that broke
    # ALL examples would be caught either way.
    if not failed_names:
        assert len(result.plugins) > 0, (
            "load produced zero plugins -- examples directory may be empty"
        )


# ── Runtime smoke: dispatch each example with empty args ─────────────────────


def _example_root_command_names() -> list[str]:
    """Find the top-level command name each example registers."""
    result = load_plugins_from_dir(_EXAMPLES_DIR, source="examples")
    # Only top-level (no dot) commands -- subcommands need specific args
    # and aren't meaningful as a "smoke dispatch."
    return sorted(plugin.name for plugin in result.plugins if "." not in plugin.name)


@pytest.mark.parametrize("cmd_name", _example_root_command_names())
def test_example_dispatches_without_attribute_error(cmd_name, tmp_path):
    """Each example's top-level command must dispatch without AttributeError.

    AttributeError is the canonical "stale API" failure: ``ctx.write``
    (pre-handle API), ``ctx.add_rx_observer`` (pre-context-manager
    observer API), etc.  Load-time tests miss these because they only
    fire when the handler is invoked.

    The handler may legitimately return a CmdResult.fail (e.g. "not
    connected") -- that's fine; we only fail on AttributeError.
    """
    # Arrange -- build a real ReplEngine and graft in the example plugins.
    import json

    from termapy.repl import ReplEngine
    cfg = {"port": "DEMO", "baud_rate": 115200, "eol": "\r"}
    config_path = tmp_path / "test.cfg"
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run", "cap"):
        (config_path.parent / sub).mkdir(exist_ok=True)
    eng = ReplEngine(cfg, str(config_path), lambda t, c=None: None)
    eng.ctx.io._write_markup = lambda text: None
    # Graft in examples' plugin set so eng.dispatch can find them.
    load = load_plugins_from_dir(_EXAMPLES_DIR, source="examples")
    for plugin in load.plugins:
        eng._plugins[plugin.name] = plugin

    # Act -- dispatch with empty args.
    try:
        eng.dispatch(cmd_name)
    except AttributeError as e:
        pytest.fail(
            f"example /{cmd_name} raised AttributeError -- stale API call?\n"
            f"  {e}"
        )
