"""Smoke tests — verify the app starts and shuts down without crashing.

Uses Textual's run_test() to launch the full app through on_mount,
then exits. Catches import errors, missing attributes, and
initialization failures that unit tests miss.
"""

import json

import pytest

from termapy.app import SerialTerminal
from termapy.config import load_config, setup_demo_config
from termapy.defaults import DEFAULT_CFG


@pytest.fixture
def demo_cfg(tmp_path):
    """Set up a demo config in a temp directory and return (cfg, path)."""
    config_path = setup_demo_config(tmp_path, force=True)
    cfg = load_config(str(config_path))
    return cfg, str(config_path)


@pytest.fixture
def empty_cfg(tmp_path):
    """Set up a minimal config in a temp directory and return (cfg, path)."""
    cfg_dir = tmp_path / "test"
    cfg_dir.mkdir()
    config_path = cfg_dir / "test.cfg"
    config_path.write_text(json.dumps(DEFAULT_CFG), encoding="utf-8")
    for sub in ("plugins", "ss", "scripts", "proto", "viz", "cap"):
        (cfg_dir / sub).mkdir()
    cfg = load_config(str(config_path))
    return cfg, str(config_path)


class TestSmoke:
    """Smoke tests — app starts and shuts down cleanly."""

    @pytest.mark.asyncio
    async def test_startup_with_demo_config(self, demo_cfg):
        """App starts with demo config without crashing."""
        cfg, path = demo_cfg
        app = SerialTerminal(cfg, config_path=path)
        async with app.run_test() as pilot:
            await pilot.press("ctrl+q")

    @pytest.mark.asyncio
    async def test_startup_with_empty_config(self, empty_cfg):
        """App starts with a minimal config without crashing."""
        cfg, path = empty_cfg
        app = SerialTerminal(cfg, config_path=path)
        async with app.run_test() as pilot:
            await pilot.press("ctrl+q")

    @pytest.mark.asyncio
    async def test_startup_with_no_config(self):
        """App starts with no config path without crashing."""
        cfg = dict(DEFAULT_CFG)
        app = SerialTerminal(cfg, config_path="")
        async with app.run_test() as pilot:
            await pilot.press("ctrl+q")
