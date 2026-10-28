"""Regenerate the documentation screenshots in ``src/termapy/help/img``.

One command for every shot that can be taken without a person at a
terminal::

    uv run python scripts/doc_shots.py            # all of them
    uv run python scripts/doc_shots.py doc_08     # just the ones matching

Why a script and not ``doc_screenshots.run``: ``/ss.svg`` declares
``screen_capture``, so only a live TUI can take a shot, and the .run file
therefore needed a person to start termapy, connect to the demo device
and run it.  That cost is why ten images went unchanged from 2026-04-03
to 2026-10-28 while the toolbar's ``?`` button became ``Help`` -- the
docs showed a build that no longer existed.  Here the real
``SerialTerminal`` is booted headless (``app.run_test()``, the route the
Pilot tests use) over a throwaway demo config, so the shots are
reproducible and a stale one is a command away from being fixed.

Driving the app directly also reaches three frames the .run file could
not: while a script runs, the script-progress bar owns the toolbar, so
the idle toolbar and the capture overlay never showed.  Here nothing is
running, and they do.

``tests/test_doc_shots.py`` fails when a committed image shows a toolbar
that no longer matches the app's own buttons -- the staleness that went
seven months unnoticed.

Frame is 116x35, the doc set's size.  The pickers stay in
``doc_dialog_shots.py``: a modal replaces the input line, so they are
opened by calling the button handler rather than dispatching a command.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
IMG_DIR = REPO_ROOT / "src" / "termapy" / "help" / "img"
SIZE = (116, 35)

# Where the demo config is staged while the shots are taken.  It is in
# the frame (/var and /cfg.info print it), so it is a short neutral path
# on every machine, not a temp dir and not the repo.  Made and removed
# by this script; nothing is left behind.
STAGE_ROOT = Path.home().anchor + "termapy-docs"

# The synthetic three-port fleet, so doc_14 shows the same ports on every
# machine instead of whatever is plugged into the one taking the shot.
DEMO_FLEET = "1"

# Each shot: the file, and the REPL lines that set it up.  The last line
# is the subject; the ones before it are the state it needs.  A shot is
# taken against a cleared pane, so no entry needs its own /cls.
#
# Device commands (no prefix) go to the demo device; everything else is a
# REPL command, written WITHOUT the prefix -- _repl_line adds the config's
# own, so these keep working under a custom cmd_prefix.
SHOTS: list[tuple[str, list[str]]] = [
    # The hero: a realistic session with variety in it.
    ("doc_01_main_tui", ["@AT+INFO", "@AT+TEMP", "temp_plot 20", "@AT+LED on", "@AT+STATUS"]),
    ("doc_02_help", ["help.group"]),
    ("doc_02b_help_group", ["help.group devices"]),
    ("doc_03_help_proto", ["help proto"]),
    ("doc_04_proto_send", ['proto.send "rev\\r"']),
    ("doc_05_proto_crc", ["proto.send crc16-modbus 01 03 00 00 00 0A"]),
    ("doc_06_proto_delay", ['proto.send 00 ~25ms "AT+INFO\\r"']),
    ("doc_07_crc_python", ["proto.crc.python crc16-modbus"]),
    ("doc_08_variables", ["var"]),
    ("doc_09_target_help", ["help.target"]),
    # config.md captions this "Config info display"; the old shot was a
    # verbose load report, which is a different thing and did not match.
    ("doc_10_config_info", ["cfg.info"]),
    ("doc_11_proto_run", ["proto.run modbus_inline.pro"]),
    (
        "doc_12_cap_struct",
        [
            "cap.struct doc_cap_001.csv fmt=Label:S1-10 Counter:U11 Val16:U13-12 "
            "Val32:U17-14 Temp:F21-18 records=10 echo=on cmd=AT+BINDUMP 10"
        ],
    ),
    # 13 and 16 could not be taken by the .run file: a running script owns
    # the toolbar, so the capture overlay and the idle toolbar never showed.
    ("doc_13_cap_progress", ["cap.text doc_progress.txt timeout=15s"]),
    ("doc_14_port_list", ["port.list"]),
    # The XMODEM pull: the device sends, termapy receives.  The receive
    # blocks until the transfer finishes, so it needs a longer settle.
    (
        "doc_15_xfer",
        ["@AT+FS.LIST", "@AT+XMODEM=SEND device_log.txt", "xfer.xmodem.recv doc_xfer_001.bin"],
    ),
    ("doc_16_toolbar", []),
    ("doc_17_proto_debug", ["proto.debug modbus_inline.pro"]),
]

# Shots that leave the app somewhere a later shot cannot start from: a
# full-screen view, or a capture still running.  Each is followed by the
# lines that put the app back at an idle prompt.
AFTER: dict[str, list[str]] = {
    "doc_13_cap_progress": ["cap.stop"],
    "doc_17_proto_debug": ["@\x1b"],  # Escape leaves the debug screen
}

# What a shot that is NOT taken here needs, for the summary at the end.
MANUAL = {
    "doc_20_palette": "Ctrl+P, the command palette over a real session",
    "doc_22_script_editor": "the script editor, opened from the Run picker",
    "doc_23_config_editor": "the config editor, opened from the Cfg picker",
    "doc_24_cmd_picker": "the / popup, which needs a keypress in the input line",
}


async def _settle(pilot, ticks: int = 25) -> None:
    """Let a worker finish and the pane repaint before the frame is taken."""
    for _ in range(ticks):
        await pilot.pause()


async def _shoot(config_path: str, wanted: list[str]) -> list[str]:
    from termapy.app import SerialTerminal
    from termapy.config import load_config

    cfg = load_config(config_path)
    app = SerialTerminal(cfg, config_path)
    written: list[str] = []
    async with app.run_test(size=SIZE) as pilot:
        await _settle(pilot, 40)  # boot, auto-connect to the demo device
        for name, lines in SHOTS:
            if wanted and not any(token in name for token in wanted):
                continue
            app._dispatch_on_thread(app._repl_line("cls"))
            await _settle(pilot, 10)
            for line in lines:
                # "@" marks a line for the device, which goes as typed.
                app._dispatch_on_thread(line[1:] if line.startswith("@") else app._repl_line(line))
                await _settle(pilot)
            await _settle(pilot)
            app.save_screenshot(filename=f"{name}.svg", path=str(IMG_DIR))
            written.append(name)
            for line in AFTER.get(name, []):
                app._dispatch_on_thread(line[1:] if line.startswith("@") else app._repl_line(line))
                await _settle(pilot)
    return written


def main() -> int:
    from termapy.config import setup_demo_config

    wanted = sys.argv[1:]
    os.environ["TERMAPY_DEMO_FLEET"] = DEMO_FLEET
    # /var, /cfg.info and the load report print the config's full path, so
    # the staging folder is IN the shot.  It is a fixed, neutral location
    # rather than a temp dir or the repo: a published image should not
    # carry the username of whoever regenerated it, and two people
    # regenerating should get the same SVG.  Removed again at the end.
    home = Path(STAGE_ROOT)
    if home.exists():
        shutil.rmtree(home)
    cfg_root = home / "termapy_cfg"
    cfg_root.mkdir(parents=True)
    try:
        os.environ["TERMAPY_CFG_DIR"] = str(cfg_root)
        config_path = str(setup_demo_config(cfg_root))
        written = asyncio.run(_shoot(config_path, wanted))
    finally:
        shutil.rmtree(home, ignore_errors=True)
    for name in written:
        print(f"wrote {IMG_DIR / name}.svg")
    if not wanted:
        print(f"\n{len(written)} shot(s) written; still taken by hand:")
        for name, why in sorted(MANUAL.items()):
            print(f"  {name:24} {why}")
        print("  the two pickers         scripts/doc_dialog_shots.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
