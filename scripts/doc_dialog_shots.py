"""Regenerate the modal-dialog screenshots in ``src/termapy/help/img``.

The ``doc_screenshots.run`` script captures everything a running script
can reach, but a modal picker replaces the input line, so the pickers
are shot here instead: boot the real ``SerialTerminal`` headless
(``app.run_test()``, the same route the Pilot tests use), open the
dialog the way a click would, and save the SVG.  Same 116x35 frame as
the rest of the doc set.

Everything is staged in a throwaway ``termapy_cfg`` so the shots are
reproducible: the demo scripts (real docstring summaries), three extra
configs with a macOS-length port and titles, and back-dated mtimes so
the UPDATED column shows more than "just now".

Run from the repo root::

    uv run python scripts/doc_dialog_shots.py

Writes:
    doc_21_script_picker.svg   the Run picker
    doc_37_config_picker.svg   the Config picker
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
IMG_DIR = REPO_ROOT / "src" / "termapy" / "help" / "img"
SIZE = (116, 35)

# Config picker rows: (name, port, baud, title, age in seconds).  A blank
# title shows the "name-only config" case; the macOS port shows the PORT
# column padding.
EXTRA_CONFIGS = [
    ("bench", "/dev/cu.usbserial-A50285BI", 115200, "Bench board", 3 * 3600),
    ("logger", "COM7", 9600, "Freezer logger", 2 * 86400),
    ("seven", "COM4", 115200, "", 40 * 86400),
]

# Run picker: back-date some demo scripts so the column has a spread.
SCRIPT_AGES = {
    "gps_demo.run": 10 * 60,
    "smoke_test.run": 3 * 3600,
    "status_check.run": 2 * 86400,
    "crc_tour.run": 40 * 86400,
}


def _backdate(path: Path, age_s: float) -> None:
    stamp = time.time() - age_s
    os.utime(path, (stamp, stamp))


def _stage(cfg_root: Path) -> str:
    """Populate ``cfg_root`` and return the demo config path to boot from."""
    from termapy.config import setup_demo_config
    from termapy.defaults import default_cfg

    demo_path = setup_demo_config(cfg_root)
    for name, age_s in SCRIPT_AGES.items():
        _backdate(demo_path.parent / "run" / name, age_s)
    _backdate(demo_path, 30 * 60)
    for name, port, baud, title, age_s in EXTRA_CONFIGS:
        folder = cfg_root / name
        folder.mkdir()
        cfg = default_cfg()
        cfg["title"] = title or name
        cfg["serial"]["port"] = port
        cfg["serial"]["baud_rate"] = baud
        path = folder / f"{name}.cfg"
        path.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
        _backdate(path, age_s)
    return str(demo_path)


async def _shoot(config_path: str) -> None:
    from termapy.app import SerialTerminal
    from termapy.config import load_config

    cfg = load_config(config_path)
    cfg["auto_connect"] = False  # the shots are about the dialogs, not the port
    app = SerialTerminal(cfg, config_path)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()

        app._btn_scripts()
        await pilot.pause()
        app.save_screenshot(filename="doc_21_script_picker.svg", path=str(IMG_DIR))
        await pilot.press("escape")
        await pilot.pause()

        app._btn_cfg()
        await pilot.pause()
        app.save_screenshot(filename="doc_37_config_picker.svg", path=str(IMG_DIR))
        await pilot.press("escape")
        await pilot.pause()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="termapy_docshots_") as tmp:
        cfg_root = Path(tmp) / "termapy_cfg"
        cfg_root.mkdir()
        # ConfigPicker lists cfg_dir(), which honors this variable.
        os.environ["TERMAPY_CFG_DIR"] = str(cfg_root)
        config_path = _stage(cfg_root)
        asyncio.run(_shoot(config_path))
    for name in ("doc_21_script_picker.svg", "doc_37_config_picker.svg"):
        print(f"wrote {IMG_DIR / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
