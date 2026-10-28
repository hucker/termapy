"""Regenerate the ``/help`` screenshots in ``src/termapy/help/img``.

``doc_screenshots.run`` shoots everything a running script can reach, but
it needs a live TUI: ``/ss.svg`` declares ``screen_capture``, so the CLI
refuses it.  The help pages are shot here instead, the same way the
dialog shots are -- boot the real ``SerialTerminal`` headless
(``app.run_test()``, the route the Pilot tests use) and save the SVG --
so a landscape change can be re-shot without a person at a terminal.

Same 116x35 frame as the rest of the doc set, over a throwaway demo
config so the shots are reproducible.

Run from the repo root::

    uv run python scripts/doc_help_shots.py

Writes:
    doc_02_help.svg            the grouped landscape
    doc_02b_help_group.svg     one folded group, expanded
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
IMG_DIR = REPO_ROOT / "src" / "termapy" / "help" / "img"
SIZE = (116, 35)

# (command to run, file to write).  Each is dispatched as the user would
# type it, then shot once the output has settled.
#
# Bare ``help`` is taller than the frame, and the pane holds the BOTTOM of
# what it printed, which is the directives -- not the grouping the shot is
# of.  ``help.group`` is the same page's point in one screen: the groups,
# each with the commands it folds.
SHOTS = [
    ("help.group", "doc_02_help.svg"),
    ("help.group devices", "doc_02b_help_group.svg"),
]


async def _shoot(config_path: str) -> None:
    from termapy.app import SerialTerminal
    from termapy.config import load_config

    cfg = load_config(config_path)
    # The shots are about the landscape, not the port: a connect banner
    # would scroll the page and make the frame depend on timing.
    cfg["auto_connect"] = False
    app = SerialTerminal(cfg, config_path)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        for command, filename in SHOTS:
            app._dispatch_on_thread(app._repl_line(command))
            # The worker dispatches off the main thread; let it finish and
            # the output widget repaint before the frame is captured.
            for _ in range(20):
                await pilot.pause()
            app.save_screenshot(filename=filename, path=str(IMG_DIR))
            app._dispatch_on_thread(app._repl_line("cls"))
            for _ in range(10):
                await pilot.pause()


def main() -> int:
    from termapy.config import setup_demo_config

    with tempfile.TemporaryDirectory(prefix="termapy_helpshots_") as tmp:
        cfg_root = Path(tmp) / "termapy_cfg"
        cfg_root.mkdir()
        os.environ["TERMAPY_CFG_DIR"] = str(cfg_root)
        config_path = str(setup_demo_config(cfg_root))
        asyncio.run(_shoot(config_path))
    for _, filename in SHOTS:
        print(f"wrote {IMG_DIR / filename}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
