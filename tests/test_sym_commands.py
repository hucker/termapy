"""Behavior tests for the built-in ``/sym.*`` commands and the symbol auto-load.

Everything runs through the real dispatcher against a ``ReplEngine`` with a
temp config (the ``tests/test_builtins.py`` ``repl_env`` shape); no mocking.
The demo table (``builtins/demo/demo.symbols.json``) is copied beside the
config as its sidecar, so the counts here match the CLI gold.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from termapy.devices import library_layers
from termapy.folders import LIB, SYMBOLS_SUFFIX
from termapy.plugins import CapabilitySet, InternalHandle, IOHandle, PluginContext
from termapy.plugins.capabilities import STATE_MESSAGES
from termapy.plugins.command import LifecycleHook
from termapy.repl import ReplEngine
from termapy.symbols import SYMBOLS_NS, get_table, reload_devices
from termapy.symbols.converters import FORMATS

DEMO_SYMBOLS = (
    Path(__file__).parent.parent / "src" / "termapy" / "builtins" / "demo" / f"demo{SYMBOLS_SUFFIX}"
)
XC32_MAP = Path(__file__).parent / "fixtures" / "maps" / "xc32_sample.map"
XC32_COUNT = 17  # symbols in the fixture (see test_symbols_xc32.py)
DEMO_COUNT = 17


def _build(tmp_path: Path, *, unconfined: bool = True, oneshot: bool = False):
    """A ReplEngine + PluginContext over ``<tmp>/rig/rig.cfg`` capturing output.

    Args:
        tmp_path: pytest temp folder; the cfg lives in ``rig/`` under it.
        unconfined: The ``filesystem_unconfined`` capability (False = MCP sandbox).
        oneshot: What ``ctx.is_oneshot()`` reports (True = ``--run`` / ``--exec``).
    """
    cfg = {
        "serial": {
            "port": "COM4",
            "baud_rate": 115200,
            "custom_baud": False,
            "byte_size": 8,
            "parity": "N",
            "stop_bits": 1,
            "flow_control": "none",
        },
        "echo": False,
        "eol": "\r",
    }
    config_path = tmp_path / "rig" / "rig.cfg"
    config_path.parent.mkdir()
    (config_path.parent / "sym").mkdir()
    config_path.write_text(json.dumps(cfg, indent=4))
    output: list[tuple[str, str | None]] = []

    def write(text, color=None):
        output.append((text, color))

    def write_markup(text):
        output.append((text, "markup"))

    # global_root: the checkout has a real termapy_cfg/plugin/; keep it out.
    engine = ReplEngine(cfg, str(config_path), write, global_root=tmp_path)
    internal_handle = InternalHandle(
        plugins=engine._plugins,
        # Aliases the engine's list, as TerminalHost does: converters a
        # plugin folder loads must reach /sym.import and /dev.import.
        converters=engine.converters,
        # /dev.import re-scans dev/ through this; the test's global layer
        # is tmp_path/dev, never the checkout's termapy_cfg/dev.
        reload_devices=lambda: reload_devices(engine.ctx, str(config_path), tmp_path),
        # /dev.lib resolves the libraries through this; without it the
        # handler would read the CHECKOUT's termapy_cfg/lib, which is
        # exactly the leak the temp global_root exists to prevent.
        library_layers=lambda: library_layers(str(config_path), tmp_path),
        in_script=lambda: engine.in_script,
        script_stop=lambda: engine._script_stop.set(),
        apply_cfg=engine._apply_cfg,
        dispatch=engine.dispatch,
    )
    ctx = PluginContext(
        cfg=cfg,
        config_path=str(config_path),
        internal=internal_handle,
        io=IOHandle(_write=write, _write_markup=write_markup),
        capabilities=CapabilitySet(filesystem_unconfined=unconfined),
        dispatch=engine.dispatch,
        is_oneshot=lambda: oneshot,
    )
    engine.set_context(ctx)
    flags = ctx.ns("flags")
    flags["echo"] = True
    flags["output_level"] = "verbose"
    flags["hex"] = False
    return engine, config_path, output


@pytest.fixture
def sym_env(tmp_path):
    """Engine over rig.cfg with NO sidecar; tests install one when they need it."""
    return _build(tmp_path)


def _install_sidecar(config_path: Path) -> Path:
    """Copy the demo table into sym/ as ``rig.symbols.json``."""
    sidecar = config_path.parent / "sym" / f"rig{SYMBOLS_SUFFIX}"
    shutil.copyfile(DEMO_SYMBOLS, sidecar)
    return sidecar


def _load(engine: ReplEngine, config_path: Path) -> Path:
    """Install the sidecar and load it through the real auto-load path."""
    sidecar = _install_sidecar(config_path)
    engine.fire_lifecycle("on_app_start")
    return sidecar


def _texts(output) -> list[str]:
    return [text for text, _ in output]


def _part_names(result) -> list[str]:
    """The part names a library listing answers with: its records' ``device`` keys, in table order."""
    return [record["device"] for record in result.data]


# ── Auto-load ───────────────────────────────────────────────────────────────


class TestAutoload:
    def test_sidecar_present_loads_on_app_start(self, sym_env):
        """The sidecar loads on app start and is the load report's symbols row."""
        # Arrange
        engine, config_path, output = sym_env
        _install_sidecar(config_path)
        engine.ctx.cfg["quiet_start"] = False  # the printed table, below

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        table = get_table(engine.ctx)
        assert table is not None, "table installed in the namespace"
        assert len(table) == DEMO_COUNT, "every demo symbol loaded"
        (actual,) = engine.load_report.records
        expected = {
            "kind": "symbols",
            "name": "demo firmware (hand-written)",
            "layer": "rig",
            "file": f"sym/rig{SYMBOLS_SUFFIX}",
            "path": str((config_path.parent / "sym" / f"rig{SYMBOLS_SUFFIX}").resolve()),
            "count": DEMO_COUNT,
            "detail": {
                "source": "demo firmware (hand-written)",
                "converter": "",
                "typed": sum(1 for symbol in table.symbols if symbol.type),
                "status": "unknown",
            },
            "error": "",
        }
        assert actual == expected, "named for its build, counted, with no verdict to give"
        assert "Loaded - 1 file" in _texts(output), "interactive sessions see the report"

    def test_absent_sidecar_is_silent(self, sym_env):
        # Arrange
        engine, _, output = sym_env

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        assert "table" not in engine.ctx.ns(SYMBOLS_NS), "nothing loaded"
        assert not any("ymbols" in text for text in _texts(output)), "no line at all"

    def test_corrupt_sidecar_reports_and_loads_nothing(self, sym_env):
        # Arrange
        engine, config_path, output = sym_env
        sidecar = config_path.parent / "sym" / f"rig{SYMBOLS_SUFFIX}"
        sidecar.write_text('{"symbols_version": 2, "symbols": []}', encoding="utf-8")

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        assert get_table(engine.ctx) is None, "a broken file loads nothing"
        errors = [(text, color) for text, color in output if text.startswith("Symbols: ")]
        assert errors == [
            (f"Symbols: rig{SYMBOLS_SUFFIX}: symbols_version: expected 1, got 2", "yellow"),
        ], "the error is reported once, field-qualified, and never raised"
        (row,) = engine.load_report.records
        actual = (row["kind"], row["file"], row["error"])
        expected = ("symbols", f"sym/rig{SYMBOLS_SUFFIX}", "symbols_version: expected 1, got 2")
        assert actual == expected, "and it is the report's symbols row, with the reason"

    def test_oneshot_loads_silently(self, tmp_path):
        # Arrange -- --run / --exec stdout must carry only the user's output
        engine, config_path, output = _build(tmp_path, oneshot=True)
        _install_sidecar(config_path)

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        assert get_table(engine.ctx) is not None, "the table still loads"
        assert not any(text.startswith("Loaded ") for text in _texts(output)), "but says nothing"
        assert len(engine.load_report.records) == 1, "the report still has its row"

    def test_config_switch_without_sidecar_clears_table(self, sym_env, tmp_path):
        # Arrange -- the TUI keeps its ctx across a switch, so the seam must clear
        engine, config_path, _ = sym_env
        _load(engine, config_path)
        other = tmp_path / "other" / "other.cfg"
        other.parent.mkdir()
        other.write_text(json.dumps(dict(engine.cfg)))

        # Act
        engine.replace_cfg(dict(engine.cfg), str(other))
        engine.fire_lifecycle("on_config_load")

        # Assert
        assert get_table(engine.ctx) is None, "the previous build's names are dropped"

    def test_on_connect_does_not_load(self, sym_env):
        # Arrange -- symbols belong to the config, not the port
        engine, config_path, _ = sym_env
        _install_sidecar(config_path)

        # Act
        engine.fire_lifecycle("on_connect")

        # Assert
        assert get_table(engine.ctx) is None, "only on_app_start / on_config_load auto-load"

    def test_plugin_hook_sees_table_already_installed(self, sym_env):
        # Arrange -- core runs before plugin hooks
        engine, config_path, _ = sym_env
        _install_sidecar(config_path)
        seen: list[int] = []

        def hook(ctx):
            table = get_table(ctx)
            seen.append(len(table) if table is not None else -1)

        # source="app": a hook the host registers itself.  Resolution drops
        # every folder-sourced hook before this pass, so a made-up label
        # would be swept away with them.
        engine.register_lifecycle_hook(
            LifecycleHook(name="on_config_load", handler=hook, source="app", plugin="probe"),
        )

        # Act
        engine.fire_lifecycle("on_config_load")

        # Assert
        assert seen == [DEMO_COUNT], "a plugin's on_config_load hook already sees the table"


# ── /sym.import ─────────────────────────────────────────────────────────────


class TestSymImport:
    def test_import_writes_sidecar_and_installs(self, sym_env):
        # Arrange
        engine, config_path, output = sym_env
        sidecar = config_path.parent / "sym" / f"rig{SYMBOLS_SUFFIX}"

        # Act
        result = engine.dispatch(f"sym.import {XC32_MAP}")

        # Assert
        assert result.success, result.error
        assert result.value == XC32_COUNT, "value is the count"
        doc = json.loads(sidecar.read_text(encoding="utf-8"))
        assert doc["symbols_version"] == 1, "the sidecar is a version-1 file"
        assert doc["source"] == str(XC32_MAP), "source is the resolved map path"
        assert len(doc["symbols"]) == XC32_COUNT, "every converted symbol written"
        table = get_table(engine.ctx)
        assert table is not None and table.path == sidecar, "installed with its path"
        assert (
            f"Imported {XC32_COUNT} symbols from xc32_sample.map (xc32) -> rig{SYMBOLS_SUFFIX}",
            "green",
        ) in output, "result line names count, format and file"
        assert ("  duplicate names need name@file: sState", "dim") in output, (
            "the bench user learns the name@file form before hitting the error"
        )
        assert result.data is not None and result.data["format"] == "xc32", (
            "data carries the format"
        )
        assert result.data["count"] == XC32_COUNT, "data carries the count"

    def test_import_writes_regions_and_section_access(self, sym_env):
        """The map's memory regions reach the sidecar; code and constants load read-only."""
        # Arrange
        engine, config_path, _ = sym_env
        sidecar = config_path.parent / "sym" / f"rig{SYMBOLS_SUFFIX}"

        # Act
        engine.dispatch(f"sym.import {XC32_MAP}")

        # Assert
        doc = json.loads(sidecar.read_text(encoding="utf-8"))
        assert doc["regions"] == [
            {"name": "rom", "start": "0x00000000", "size": 524288, "access": "ro"},
            {"name": "ram", "start": "0x20000000", "size": 65536, "access": "rw"},
        ], "the Memory Configuration table, *default* left out"
        assert not any("access" in row for row in doc["symbols"]), (
            "every access is its section's, so the file states none"
        )
        table = get_table(engine.ctx)
        assert table is not None, "a table is loaded"
        access = {symbol.name: symbol.access for symbol in table.symbols}
        assert (access["main"], access["sCmdTableA"], access["sDispatch"]) == ("ro", "ro", "rw"), (
            "code and constants are read-only, a bss variable read-write"
        )

    def test_explicit_format(self, sym_env):
        # Arrange
        engine, _, _ = sym_env

        # Act
        result = engine.dispatch(f"sym.import {XC32_MAP} format=xc32")

        # Assert
        assert result.success, result.error
        assert result.value == XC32_COUNT, "explicit format= works"

    def test_unknown_format_rejected(self, sym_env):
        """An unknown ``format=`` names the token typed, not the map file.

        Validated by the handler, not an enum param: plugin folders add
        converters at runtime, so the valid set isn't known at import.
        """
        # Arrange
        engine, _, _ = sym_env

        # Act
        result = engine.dispatch(f"sym.import {XC32_MAP} format=nope")

        # Assert
        assert not result.success, "an unknown format= is refused"
        assert "nope" in result.error, "the message names the format the user typed"
        assert "xc32" in result.error, "the message lists the choices"

    def test_unrecognized_map(self, sym_env, tmp_path):
        # Arrange
        engine, _, _ = sym_env
        notes = tmp_path / "notes.txt"
        notes.write_text("just some notes\n", encoding="utf-8")

        # Act
        result = engine.dispatch(f"sym.import {notes}")

        # Assert
        expected = f"Unknown build format: notes.txt (formats: {', '.join(FORMATS)})"
        assert result.error == expected, "the message lists every registered format"

    def test_no_symbols_found_writes_nothing(self, sym_env, tmp_path):
        # Arrange
        engine, config_path, _ = sym_env
        junk = tmp_path / "junk.txt"
        junk.write_text("garbage\n", encoding="utf-8")

        # Act
        result = engine.dispatch(f"sym.import {junk} format=xc32")

        # Assert
        assert result.error == "No symbols found in junk.txt (xc32)"
        assert not (config_path.parent / "sym" / f"rig{SYMBOLS_SUFFIX}").exists(), "no file written"

    def test_relative_path_is_cfg_relative(self, sym_env):
        # Arrange -- the CWD is the repo root, never the temp cfg folder
        engine, config_path, _ = sym_env
        shutil.copyfile(XC32_MAP, config_path.parent / "mem.map")

        # Act
        result = engine.dispatch("sym.import mem.map")

        # Assert
        assert result.success, result.error
        assert result.value == XC32_COUNT, "an in-folder name is found beside the cfg"
        assert result.data is not None, "table record"
        assert result.data["source"] == str(config_path.parent / "mem.map"), (
            "provenance records the anchored path"
        )

    def test_path_with_spaces(self, sym_env, tmp_path):
        # Arrange -- a Windows-style folder name; the file param is rest=True
        spaced = tmp_path / "my maps" / "mem.map"
        spaced.parent.mkdir()
        shutil.copyfile(XC32_MAP, spaced)
        engine, _, _ = sym_env

        # Act
        result = engine.dispatch(f"sym.import {spaced} format=xc32")

        # Assert
        assert result.success, result.error
        assert result.value == XC32_COUNT, "the whole path binds; the keyword after it still parses"

    def test_missing_map(self, sym_env):
        # Arrange
        engine, _, _ = sym_env

        # Act
        result = engine.dispatch("sym.import nosuch.map")

        # Assert
        assert result.error.startswith("Build file not found: nosuch.map"), (
            "names the file; the anchor note follows (TestNotFoundNamesTheFolder)"
        )

    def test_no_config(self, sym_env):
        # Arrange
        engine, _, _ = sym_env
        engine.ctx.config_path = ""

        # Act
        result = engine.dispatch(f"sym.import {XC32_MAP}")

        # Assert
        assert result.error == STATE_MESSAGES["config_loaded"]

    def test_second_import_overwrites(self, sym_env, tmp_path):
        # Arrange
        engine, config_path, _ = sym_env
        sidecar = config_path.parent / "sym" / f"rig{SYMBOLS_SUFFIX}"
        engine.dispatch(f"sym.import {XC32_MAP}")
        small = tmp_path / "small.map"
        small.write_text(
            "Microchip PIC32 Memory-Usage Report\n"
            ".text.main                 0x10ffa          0x1ba         442\n",
            encoding="utf-8",
        )

        # Act
        result = engine.dispatch(f"sym.import {small}")

        # Assert
        assert result.value == 1, "the new table replaces the old wholesale"
        doc = json.loads(sidecar.read_text(encoding="utf-8"))
        assert len(doc["symbols"]) == 1, "the file is overwritten"
        assert doc["source"] == str(small), "provenance follows the new import"


# ── Staleness ───────────────────────────────────────────────────────────────


class TestStaleness:
    """/sym.import records provenance; load and /sym.info report on it."""

    def test_import_records_recipe_and_witness(self, sym_env, tmp_path):
        # Arrange
        engine, config_path, _ = sym_env
        map_copy = tmp_path / "mem.map"
        map_copy.write_bytes(XC32_MAP.read_bytes())

        # Act
        engine.dispatch(f"sym.import {map_copy}")

        # Assert
        sidecar = config_path.parent / "sym" / f"rig{SYMBOLS_SUFFIX}"
        doc = json.loads(sidecar.read_text(encoding="utf-8"))
        assert doc["recipe"] == {"converter": "xc32"}, "the converter that ran is recorded"
        assert doc["witness"]["size"] == map_copy.stat().st_size, (
            "the witness records the map as it was at import"
        )

    def test_rebuilt_map_warns_at_load_with_the_fix(self, sym_env, tmp_path):
        """The memo's rule: report at load, never regenerate."""
        # Arrange
        engine, config_path, output = sym_env
        map_copy = tmp_path / "mem.map"
        map_copy.write_bytes(XC32_MAP.read_bytes())
        engine.dispatch(f"sym.import {map_copy}")
        map_copy.write_text("rebuilt, and shorter", encoding="utf-8")
        output.clear()

        # Act
        engine.fire_lifecycle("on_config_load")

        # Assert
        warnings = [text for text, color in output if color == "yellow"]
        assert any("rebuilt" in text for text in warnings), "the rebuild is reported"
        assert any("sym.import" in text for text in warnings), (
            "the warning names the command that fixes it"
        )

    def test_load_does_not_regenerate(self, sym_env, tmp_path):
        """A stale table stays as imported; only the user rebuilds it."""
        # Arrange
        engine, config_path, _ = sym_env
        map_copy = tmp_path / "mem.map"
        map_copy.write_bytes(XC32_MAP.read_bytes())
        engine.dispatch(f"sym.import {map_copy}")
        sidecar = config_path.parent / "sym" / f"rig{SYMBOLS_SUFFIX}"
        before = sidecar.read_text(encoding="utf-8")
        map_copy.write_text("rebuilt", encoding="utf-8")

        # Act
        engine.fire_lifecycle("on_config_load")

        # Assert
        assert sidecar.read_text(encoding="utf-8") == before, (
            "loading a stale table must never rewrite the sidecar"
        )

    def test_fresh_import_is_silent(self, sym_env, tmp_path):
        """No warning when nothing moved -- the common case stays quiet."""
        # Arrange
        engine, config_path, output = sym_env
        map_copy = tmp_path / "mem.map"
        map_copy.write_bytes(XC32_MAP.read_bytes())
        engine.dispatch(f"sym.import {map_copy}")
        output.clear()

        # Act
        engine.fire_lifecycle("on_config_load")

        # Assert
        assert not [text for text, color in output if color == "yellow"], (
            "an up-to-date table says nothing"
        )

    def test_hand_written_table_never_warns(self, sym_env):
        """The demo table's shape: unknown is not stale, and must stay quiet."""
        # Arrange
        engine, config_path, output = sym_env
        _install_sidecar(config_path)

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        assert not [text for text, color in output if color == "yellow"], (
            "a table with no witness is not reported as stale"
        )

    def test_info_reports_status_and_fix(self, sym_env, tmp_path):
        # Arrange
        engine, config_path, output = sym_env
        map_copy = tmp_path / "mem.map"
        map_copy.write_bytes(XC32_MAP.read_bytes())
        engine.dispatch(f"sym.import {map_copy}")
        map_copy.write_text("rebuilt", encoding="utf-8")
        engine.fire_lifecycle("on_config_load")
        output.clear()

        # Act
        result = engine.dispatch("sym.info")

        # Assert
        rendered = " ".join(_texts(output))
        assert "STALE" in rendered, "the prose page shows the status"
        assert result.data["status"] == "stale", "the record carries the verdict"
        assert result.data["fixable"] is True, "and whether termapy can fix it"
        assert "format=xc32" in result.data["rebuild_command"], (
            "the structured surface names the rebuild, for the agent that can run it"
        )

    def test_info_stays_quiet_when_in_sync(self, sym_env, tmp_path):
        # Arrange
        engine, config_path, output = sym_env
        map_copy = tmp_path / "mem.map"
        map_copy.write_bytes(XC32_MAP.read_bytes())
        engine.dispatch(f"sym.import {map_copy}")
        output.clear()

        # Act
        result = engine.dispatch("sym.info")

        # Assert
        assert "STALE" not in " ".join(_texts(output)), "no status row when in sync"
        assert result.data["status"] == "in_sync", "the record still says so explicitly"


# ── Device files ────────────────────────────────────────────────────────────


def _install_device(folder: Path, name: str, registers: list[dict], **extra) -> Path:
    """Write ``<folder>/<name>.device.json`` describing one part."""
    folder.mkdir(parents=True, exist_ok=True)
    doc = {"device_version": 1, "device": name, "registers": registers} | extra
    file = folder / f"{name}.device.json"
    file.write_text(json.dumps(doc), encoding="utf-8")
    return file


_PART = [
    {"name": "REG_A", "addr": "0x50000000", "size": 4},
    {"name": "REG_B", "addr": "0x50000004", "size": 4, "access": "ro"},
]


class TestDevices:
    """Device files in dev/ are the board's registers, merged over the build."""

    def test_device_registers_merge_with_the_sidecar(self, sym_env):
        # Arrange
        engine, config_path, _ = sym_env
        _install_sidecar(config_path)
        _install_device(config_path.parent / "dev", "part", _PART)

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        table = get_table(engine.ctx)
        assert table is not None, "a table is loaded"
        assert len(table) == DEMO_COUNT + 2, "build symbols plus the part's registers"
        result = engine.dispatch("sym REG_A")
        assert result.value == "0x50000000", "a register resolves like any symbol"

    def test_each_device_file_is_a_report_row(self, sym_env):
        """A device file's row names the part, its layer, its file and its registers."""
        # Arrange
        engine, config_path, _ = sym_env
        _install_device(config_path.parent / "dev", "part", _PART)

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        (actual,) = engine.load_report.records
        expected = {
            "kind": "device",
            "name": "part",
            "layer": "rig",
            "file": "dev/part.device.json",
            "path": str((config_path.parent / "dev" / "part.device.json").resolve()),
            "count": 2,
            "detail": {"ref": ""},
            "error": "",
        }
        assert actual == expected, "a file holding its own registers names no ref"

    def test_quiet_start_names_the_files_in_full(self, sym_env):
        """quiet_start: the screen names the sidecar and each device file by full path, no table."""
        # Arrange
        engine, config_path, output = sym_env
        _install_sidecar(config_path)
        _install_device(config_path.parent / "dev", "part", _PART)
        engine.ctx.cfg["quiet_start"] = True

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        sidecar = (config_path.parent / "sym" / f"rig{SYMBOLS_SUFFIX}").resolve()
        device = (config_path.parent / "dev" / "part.device.json").resolve()
        assert (f"Symbols: {sidecar}", "green") in output, "the memory map, clickable in the TUI"
        assert (f"Device:  {device}", "green") in output, "and the register map"
        assert not any(text.startswith("Loaded - ") for text in _texts(output)), (
            "the table itself is the log's"
        )

    def test_reference_row_names_the_library_part(self, sym_env, tmp_path):
        """A reference file's row says which library part it placed."""
        # Arrange
        engine, config_path, _ = sym_env
        _install_library(tmp_path, "vendor", "fixedpart")
        dev = config_path.parent / "dev"
        dev.mkdir()
        (dev / "board.device.json").write_text(
            json.dumps({"device_version": 1, "ref": "fixedpart"}), encoding="utf-8"
        )

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        (actual,) = engine.load_report.records
        assert actual["file"] == "dev/board.device.json", "the row is the board's file"
        assert actual["detail"] == {"ref": "fixedpart"}, "and names the part it placed"

    def test_broken_device_file_is_an_error_row(self, sym_env):
        """A device file that does not load is a row carrying the loader's reason."""
        # Arrange
        engine, config_path, _ = sym_env
        dev = config_path.parent / "dev"
        dev.mkdir()
        (dev / "bad.device.json").write_text("{", encoding="utf-8")

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        (actual,) = engine.load_report.records
        assert actual["kind"] == "device", "a device row"
        assert actual["error"].startswith("bad.device.json: "), "the loader's own message"

    def test_devices_load_with_no_sidecar(self, sym_env):
        """A board whose firmware map you lack still has registers."""
        # Arrange
        engine, config_path, _ = sym_env
        _install_device(config_path.parent / "dev", "part", _PART)

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        table = get_table(engine.ctx)
        assert table is not None and len(table) == 2, "a devices-only table"
        assert table.source == "", "it derives from no build, so no provenance"

    def test_reimport_keeps_the_device_registers(self, sym_env, tmp_path):
        """The bug that shaped session.py: an import must not drop the board."""
        # Arrange
        engine, config_path, _ = sym_env
        _install_device(config_path.parent / "dev", "part", _PART)
        engine.fire_lifecycle("on_app_start")
        map_copy = tmp_path / "mem.map"
        map_copy.write_bytes(XC32_MAP.read_bytes())

        # Act
        engine.dispatch(f"sym.import {map_copy}")

        # Assert
        table = get_table(engine.ctx)
        assert table is not None, "a table is loaded"
        assert table.by_name("REG_A"), "the register survived the import"
        assert len(table) == XC32_COUNT + 2, "build symbols replaced, registers kept"

    def test_sidecar_never_receives_device_rows(self, sym_env, tmp_path):
        """Registers come from their own files; the build's file stays the build's."""
        # Arrange
        engine, config_path, _ = sym_env
        _install_device(config_path.parent / "dev", "part", _PART)
        engine.fire_lifecycle("on_app_start")
        map_copy = tmp_path / "mem.map"
        map_copy.write_bytes(XC32_MAP.read_bytes())

        # Act
        engine.dispatch(f"sym.import {map_copy}")

        # Assert
        sidecar = config_path.parent / "sym" / f"rig{SYMBOLS_SUFFIX}"
        doc = json.loads(sidecar.read_text(encoding="utf-8"))
        assert len(doc["symbols"]) == XC32_COUNT, "only the converter's rows were written"

    def test_load_keeps_the_device_registers(self, sym_env):
        # Arrange
        engine, config_path, _ = sym_env
        _install_sidecar(config_path)
        _install_device(config_path.parent / "dev", "part", _PART)
        engine.fire_lifecycle("on_app_start")

        # Act
        engine.dispatch("sym.load")

        # Assert
        table = get_table(engine.ctx)
        assert table is not None and len(table) == DEMO_COUNT + 2, "reload merges again"

    def test_unload_keeps_the_board_and_says_so(self, sym_env):
        # Arrange
        engine, config_path, output = sym_env
        _install_sidecar(config_path)
        _install_device(config_path.parent / "dev", "part", _PART)
        engine.fire_lifecycle("on_app_start")
        output.clear()

        # Act
        result = engine.dispatch("sym.unload")

        # Assert
        assert result.value == DEMO_COUNT, "the value is the build count removed"
        table = get_table(engine.ctx)
        assert table is not None and len(table) == 2, "the registers remain"
        assert any("2 device registers remain" in text for text in _texts(output)), (
            "unload leaving symbols behind must not read as a failure"
        )

    def test_info_lists_devices_in_prose_and_data(self, sym_env):
        # Arrange
        engine, config_path, output = sym_env
        _install_device(config_path.parent / "dev", "part", _PART, description="A part")
        engine.fire_lifecycle("on_app_start")
        output.clear()

        # Act
        result = engine.dispatch("sym.info")

        # Assert
        assert any("part" in text and "2 registers" in text for text in _texts(output)), (
            "the prose page has a device row"
        )
        assert result.data["devices"][0]["device"] == "part", "the record lists it"
        assert result.data["devices"][0]["registers"] == 2, "with its register count"

    def test_broken_device_file_is_reported_and_skipped(self, sym_env):
        # Arrange
        engine, config_path, output = sym_env
        _install_sidecar(config_path)
        folder = config_path.parent / "dev"
        folder.mkdir()
        (folder / "bad.device.json").write_text("{not json", encoding="utf-8")

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        assert any(text.startswith("Device: bad.device.json:") for text in _texts(output)), (
            "the file and the problem are named"
        )
        table = get_table(engine.ctx)
        assert table is not None and len(table) == DEMO_COUNT, "the sidecar still loaded"

    def test_global_layer_loads_and_per_config_overrides(self, sym_env, tmp_path):
        """termapy_cfg/dev/ is every board's; <cfg>/dev/ wins by device name."""
        # Arrange
        engine, config_path, _ = sym_env
        _install_device(tmp_path / "dev", "part", _PART)
        _install_device(
            config_path.parent / "dev",
            "part",
            [{"name": "REG_A", "addr": "0x60000000", "size": 4}],
        )

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        table = get_table(engine.ctx)
        assert table is not None, "a table is loaded"
        assert table.by_name("REG_A")[0].addr == 0x60000000, "the per-config file won"
        assert not table.by_name("REG_B"), "the global file was replaced whole, not merged"

    def test_config_switch_drops_the_previous_board(self, sym_env):
        # Arrange
        engine, config_path, _ = sym_env
        file = _install_device(config_path.parent / "dev", "part", _PART)
        engine.fire_lifecycle("on_app_start")
        assert get_table(engine.ctx) is not None, "loaded to begin with"

        # Act
        file.unlink()
        engine.fire_lifecycle("on_config_load")

        # Assert
        assert get_table(engine.ctx) is None, "no build, no board: nothing loaded"

    def test_build_symbol_shadowing_a_register_warns(self, sym_env):
        # Arrange
        engine, config_path, output = sym_env
        _install_sidecar(config_path)
        _install_device(
            config_path.parent / "dev",
            "part",
            [{"name": "gTemp", "addr": "0x50000000", "size": 2}],
        )

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        table = get_table(engine.ctx)
        assert table is not None, "a table is loaded"
        assert table.by_name("gTemp")[0].addr == 0x1000, "the build wins"
        assert any("gTemp shadows" in text for text in _texts(output)), "and it is reported"


# ── /dev.import ─────────────────────────────────────────────────────────────


SVD_SAMPLE = Path(__file__).parent / "fixtures" / "devices" / "svd_sample.svd"
SVD_COUNT = 16  # see test_devices_svd.py

_DEVICE_CONVERTER = """
KIND = "device"
FORMAT = "acme"
DESCRIPTION = "Acme register dump"
DETECT = ()


def convert(text):
    return {"device_version": 1, "device": "acmepart",
            "registers": [{"name": "ACME_CTRL", "addr": "0x70000000", "size": 4}]}
"""


class TestDevImport:
    """/dev.import converts a vendor file into dev/ and loads it at once."""

    def test_import_writes_the_file_and_loads_it(self, sym_env):
        # Arrange
        engine, config_path, _ = sym_env

        # Act
        result = engine.dispatch(f"dev.import {SVD_SAMPLE}")

        # Assert
        assert result.success, result.error
        assert result.value == SVD_COUNT, "the register count is the value"
        dest = config_path.parent / "dev" / "atsample1.device.json"
        assert dest.is_file(), "written as dev/<device>.device.json"
        assert engine.dispatch("sym PORT_GROUP1_DIR").value == "0x40003080", (
            "a register resolves in the same session, no config reload"
        )
        assert result.data["device"] == "atsample1" and result.data["format"] == "svd", (
            "the record names the device and the converter"
        )

    def test_explicit_format_and_unknown_format(self, sym_env):
        # Arrange
        engine, _, _ = sym_env

        # Act
        explicit = engine.dispatch(f"dev.import {SVD_SAMPLE} format=svd")
        unknown = engine.dispatch(f"dev.import {SVD_SAMPLE} format=nope")

        # Assert
        assert explicit.success, explicit.error
        assert not unknown.success, "an unknown format= is refused"
        assert "nope" in unknown.error and "svd" in unknown.error, "names the token and the choices"

    def test_missing_and_unrecognized_files(self, sym_env, tmp_path):
        # Arrange
        engine, _, _ = sym_env
        notes = tmp_path / "notes.txt"
        notes.write_text("just some notes\n", encoding="utf-8")

        # Act
        missing = engine.dispatch("dev.import nosuch.svd")
        junk = engine.dispatch(f"dev.import {notes}")

        # Assert
        assert missing.error.startswith("Source file not found: nosuch.svd"), (
            "names the file; the anchor note follows (TestNotFoundNamesTheFolder)"
        )
        assert junk.error == "Unknown device format: notes.txt (formats: svd)"

    def test_reimport_overwrites(self, sym_env):
        # Arrange
        engine, config_path, _ = sym_env
        engine.dispatch(f"dev.import {SVD_SAMPLE}")

        # Act
        result = engine.dispatch(f"dev.import {SVD_SAMPLE}")

        # Assert
        assert result.success, result.error
        table = get_table(engine.ctx)
        assert table is not None and len(table) == SVD_COUNT, "same registers, not doubled"

    def test_refuses_a_second_file_for_the_same_device(self, sym_env):
        # Arrange
        engine, config_path, _ = sym_env
        _install_device(
            config_path.parent / "dev",
            "mine",
            [
                {"name": "X", "addr": 0, "size": 4},
            ],
            device="atsample1",
        )  # a differently named file claiming the same device
        engine.fire_lifecycle("on_app_start")

        # Act
        result = engine.dispatch(f"dev.import {SVD_SAMPLE}")

        # Assert
        assert not result.success, "two files for one device is the instances mistake"
        assert "already defined by mine.device.json" in result.error

    def test_plugin_device_converter_serves_dev_import_only(self, sym_env):
        """KIND = "device" reaches /dev.import and is invisible to /sym.import."""
        # Arrange
        engine, config_path, _ = sym_env
        _install_converter(config_path, _DEVICE_CONVERTER, name="acme")
        engine.fire_lifecycle("on_app_start")
        anything = config_path.parent / "part.txt"
        anything.write_text("opaque vendor dump", encoding="utf-8")

        # Act
        imported = engine.dispatch(f"dev.import {anything} format=acme")
        as_map = engine.dispatch(f"sym.import {anything} format=acme")

        # Assert
        assert imported.success, imported.error
        assert engine.dispatch("sym ACME_CTRL").value == "0x70000000", "the plugin converter ran"
        assert not as_map.success and "acme" not in as_map.error.split("formats:")[1], (
            "/sym.import neither accepts nor lists a device converter"
        )


# ── Converter plugins ───────────────────────────────────────────────────────


# A converter plugin: the four names a built-in converter exports.  This is
# the pipeline shape the feature exists for -- reuse a built-in, drop what
# the board doesn't want, add the typed rows a linker map cannot express.
_PIPELINE_CONVERTER = """
from termapy.symbols.converters import xc32
from termapy.symbols.table import Symbol

FORMAT = "myboard"
DESCRIPTION = "xc32 map, no code symbols, plus a typed SFR"
DETECT = ()

_SFRS = [Symbol("U1MODE", 0xBF806000, 4, "sfr", type="u32", rmw=False)]


def convert(text):
    rows = [s for s in xc32.convert(text) if s.section != "text"]
    return rows + _SFRS
"""

_BROKEN_CONVERTER = """
FORMAT = "boom"
DESCRIPTION = "raises on purpose"
DETECT = ()


def convert(text):
    raise ValueError("bad regex")
"""

# FORMAT present, convert() missing -- an authoring error, not a plugin
# that simply has no converter.
_MALFORMED_CONVERTER = """
FORMAT = "halfdone"
DESCRIPTION = "no convert()"
DETECT = ()
"""


def _install_converter(config_path: Path, source: str, name: str = "conv") -> Path:
    """Drop a converter plugin into the config's ``plugin/`` folder."""
    folder = config_path.parent / "plugin"
    folder.mkdir(exist_ok=True)
    file = folder / f"{name}.py"
    file.write_text(source, encoding="utf-8")
    return file


class TestBareImport:
    """Bare /sym.import re-runs the loaded table's own recipe and source."""

    def test_bare_reimports_the_recorded_source(self, sym_env, tmp_path):
        """The rebuild step after a compile, without retyping a long path."""
        # Arrange
        engine, config_path, _ = sym_env
        map_copy = tmp_path / "mem.map"
        map_copy.write_bytes(XC32_MAP.read_bytes())
        engine.dispatch(f"sym.import {map_copy}")

        # Act
        result = engine.dispatch("sym.import")

        # Assert
        assert result.success, result.error
        assert result.value == XC32_COUNT, "the same map was converted again"
        assert result.data["source"] == str(map_copy), "against the recorded source"

    def test_bare_reuses_the_recorded_converter(self, sym_env, tmp_path):
        """A plugin converter must survive the re-import, not fall back to sniffing."""
        # Arrange
        engine, config_path, _ = sym_env
        _install_converter(config_path, _PIPELINE_CONVERTER)
        engine.fire_lifecycle("on_app_start")
        map_copy = tmp_path / "mem.map"
        map_copy.write_bytes(XC32_MAP.read_bytes())
        engine.dispatch(f"sym.import {map_copy} format=myboard")

        # Act
        engine.dispatch("sym.import")

        # Assert
        table = get_table(engine.ctx)
        assert table is not None, "a table is loaded"
        assert "text" not in {symbol.section for symbol in table.symbols}, (
            "the recorded plugin converter ran again, not the sniffed built-in"
        )

    def test_explicit_format_overrides_the_recipe(self, sym_env, tmp_path):
        """Re-importing with a different converter switches the pipeline."""
        # Arrange
        engine, config_path, _ = sym_env
        _install_converter(config_path, _PIPELINE_CONVERTER)
        engine.fire_lifecycle("on_app_start")
        map_copy = tmp_path / "mem.map"
        map_copy.write_bytes(XC32_MAP.read_bytes())
        engine.dispatch(f"sym.import {map_copy} format=myboard")

        # Act
        engine.dispatch("sym.import format=xc32")

        # Assert
        table = get_table(engine.ctx)
        assert table is not None, "a table is loaded"
        assert "text" in {symbol.section for symbol in table.symbols}, (
            "an explicit format= beats the recorded recipe"
        )

    def test_table_without_a_recipe_still_reimports(self, sym_env, tmp_path):
        """A sidecar written before recipes existed: source alone is enough.

        This is how such a table EARNS a recipe -- the re-import sniffs
        the map exactly as the original import did, then records it.
        """
        # Arrange
        engine, config_path, _ = sym_env
        map_copy = tmp_path / "mem.map"
        map_copy.write_bytes(XC32_MAP.read_bytes())
        sidecar = config_path.parent / "sym" / f"rig{SYMBOLS_SUFFIX}"
        doc = json.loads(DEMO_SYMBOLS.read_text(encoding="utf-8"))
        doc["source"] = str(map_copy)  # a real map, but no recipe/witness
        sidecar.write_text(json.dumps(doc), encoding="utf-8")
        engine.fire_lifecycle("on_app_start")

        # Act
        result = engine.dispatch("sym.import")

        # Assert
        assert result.success, result.error
        assert result.data["recipe"] == {"converter": "xc32"}, (
            "the re-import gives the old table the provenance it lacked"
        )

    def test_bare_without_a_table_is_a_usage_error(self, sym_env):
        # Arrange
        engine, _, _ = sym_env

        # Act
        result = engine.dispatch("sym.import")

        # Assert
        assert not result.success, "there is nothing to re-import"
        assert "sym.import" in result.error, "the usage line names the command"


class TestConverterPlugins:
    """A plugin folder may add a symbol-map converter (four top-level names)."""

    def test_plugin_converter_runs_as_a_pipeline(self, sym_env):
        """The motivating case: filter the map, add rows it cannot express."""
        # Arrange
        engine, config_path, _ = sym_env
        _install_converter(config_path, _PIPELINE_CONVERTER)
        engine.fire_lifecycle("on_app_start")

        # Act
        result = engine.dispatch(f"sym.import {XC32_MAP} format=myboard")

        # Assert
        assert result.success, result.error
        table = get_table(engine.ctx)
        assert table is not None, "a table is loaded"
        sections = {symbol.section for symbol in table.symbols}
        assert "text" not in sections, "the converter dropped code symbols"
        sfr = table.by_name("U1MODE")
        assert len(sfr) == 1, "the converter added a row no linker map carries"
        assert sfr[0].type == "u32", "a full Symbol survives: type is kept"
        assert sfr[0].rmw is False, "a full Symbol survives: rmw is kept"

    def test_plugin_converter_is_not_sniffed_by_default(self, sym_env):
        """Empty DETECT keeps a board pipeline out of format detection."""
        # Arrange
        engine, config_path, _ = sym_env
        _install_converter(config_path, _PIPELINE_CONVERTER)
        engine.fire_lifecycle("on_app_start")

        # Act
        result = engine.dispatch(f"sym.import {XC32_MAP}")

        # Assert
        assert result.success, result.error
        table = get_table(engine.ctx)
        assert table is not None, "a table is loaded"
        assert "text" in {symbol.section for symbol in table.symbols}, (
            "the sniffed built-in ran, not the explicit-only plugin converter"
        )

    def test_unknown_format_lists_plugin_converters(self, sym_env):
        # Arrange
        engine, config_path, _ = sym_env
        _install_converter(config_path, _PIPELINE_CONVERTER)
        engine.fire_lifecycle("on_app_start")

        # Act
        result = engine.dispatch(f"sym.import {XC32_MAP} format=nope")

        # Assert
        assert not result.success, "an unknown format is still refused"
        assert "myboard" in result.error, "the plugin's format is offered too"

    def test_converter_exception_names_the_converter(self, sym_env):
        """Third-party code on the dispatch path must not crash the app."""
        # Arrange
        engine, config_path, _ = sym_env
        _install_converter(config_path, _BROKEN_CONVERTER)
        engine.fire_lifecycle("on_app_start")

        # Act
        result = engine.dispatch(f"sym.import {XC32_MAP} format=boom")

        # Assert
        assert not result.success, "the raising converter fails the command"
        assert "boom" in result.error, "the message names the converter that raised"
        assert "bad regex" in result.error, "the underlying reason is kept"

    def test_malformed_converter_is_reported_not_silent(self, sym_env):
        """FORMAT without convert() is an authoring error, reported at load."""
        # Arrange
        engine, config_path, output = sym_env
        _install_converter(config_path, _MALFORMED_CONVERTER)

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        errors = [text for text in _texts(output) if "Plugin error" in text]
        assert errors, "a malformed converter export is reported"
        assert any("halfdone" in text for text in errors), (
            "the report names the offending converter"
        )

    def test_converters_are_dropped_on_config_switch(self, sym_env):
        """A folder converter must not outlive the config that loaded it."""
        # Arrange
        engine, config_path, _ = sym_env
        plugin_file = _install_converter(config_path, _PIPELINE_CONVERTER)
        engine.fire_lifecycle("on_app_start")
        assert engine.converters, "loaded to begin with"

        # Act
        plugin_file.unlink()
        engine.fire_lifecycle("on_config_load")

        # Assert
        assert engine.converters == [], "the converter went away with its folder"
        result = engine.dispatch(f"sym.import {XC32_MAP} format=myboard")
        assert not result.success, "its format no longer resolves"

    def test_converter_only_plugin_is_not_skipped(self, sym_env):
        """A file exporting only a converter is a real plugin, not a no-op."""
        # Arrange
        engine, config_path, output = sym_env
        _install_converter(config_path, _PIPELINE_CONVERTER)

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        assert not [text for text in _texts(output) if "Skipped" in text], (
            "a converter-only file is not reported as exporting nothing"
        )
        assert [spec.format for spec in engine.converters] == ["myboard"], (
            "the converter registered"
        )


# ── /sym.load / /sym.unload ─────────────────────────────────────────────────


class TestSymLoadUnload:
    def test_bare_load_reads_sidecar(self, sym_env):
        # Arrange
        engine, config_path, output = sym_env
        _install_sidecar(config_path)

        # Act
        result = engine.dispatch("sym.load")

        # Assert
        assert result.success, result.error
        assert result.value == DEMO_COUNT, "value is the count"
        assert (f"Loaded {DEMO_COUNT} symbols (rig{SYMBOLS_SUFFIX})", "green") in output
        assert result.data is not None and result.data["count"] == DEMO_COUNT, "table record"

    def test_bare_load_without_sidecar(self, sym_env):
        # Arrange
        engine, config_path, _ = sym_env

        # Act
        result = engine.dispatch("sym.load")

        # Assert
        expected = f"Symbols not found: {config_path.parent / 'sym' / ('rig' + SYMBOLS_SUFFIX)}"
        assert result.error == expected

    @pytest.mark.parametrize("line", ["sym.load", f"sym.load {DEMO_SYMBOLS}"])
    def test_without_a_config_both_forms_are_the_gates_answer(self, sym_env, line):
        """state-answered-by-gate: /sym.load declares config_loaded, so bare and explicit forms both need one.

        Until 2026-10-16 an explicit path loaded with no config, and the bare
        form answered "No config loaded." itself, inferred from sidecar_path
        returning None.
        """
        # Arrange
        engine, _, _ = sym_env
        engine.ctx.config_path = ""

        # Act
        result = engine.dispatch(line)

        # Assert
        assert (result.success, result.error) == (False, STATE_MESSAGES["config_loaded"])

    def test_explicit_path(self, sym_env):
        # Arrange
        engine, _, output = sym_env

        # Act
        result = engine.dispatch(f"sym.load {DEMO_SYMBOLS}")

        # Assert
        assert result.success, result.error
        assert result.value == DEMO_COUNT, "an ad-hoc file loads"
        table = get_table(engine.ctx)
        assert table is not None, "installed"
        assert table.path == DEMO_SYMBOLS, "path recorded"
        # _display_path's out-of-folder branch: the full path, not a bare name.
        info = engine.dispatch("sym.info")
        rows = [text for text, color in output if color == "markup"]
        assert any(str(DEMO_SYMBOLS) in row for row in rows), (
            "file row shows the full path out-of-folder"
        )
        assert info.data is not None, "info record"
        assert info.data["path"] == str(DEMO_SYMBOLS), "data carries the same path"

    def test_relative_path_is_cfg_relative(self, sym_env):
        # Arrange -- the help page promises in-folder names work; CWD is the repo root
        engine, config_path, _ = sym_env
        _install_sidecar(config_path)

        # Act
        result = engine.dispatch(f"sym.load rig{SYMBOLS_SUFFIX}")

        # Assert
        assert result.success, result.error
        assert result.value == DEMO_COUNT, "a bare name resolves into sym/, not against the CWD"

    def test_load_takes_a_bare_stem_in_sym(self, sym_env):
        """file-name-default-ext: /sym.load rig finds sym/rig.symbols.json; the suffix is optional."""
        # Arrange
        engine, config_path, _ = sym_env
        _install_sidecar(config_path)

        # Act
        result = engine.dispatch("sym.load rig")

        # Assert
        assert result.success, result.error
        assert result.value == DEMO_COUNT, "the stem named sym/rig.symbols.json"

    def test_bad_json(self, sym_env, tmp_path):
        # Arrange
        engine, _, _ = sym_env
        bad = tmp_path / "bad.symbols.json"
        bad.write_text("{nope", encoding="utf-8")

        # Act
        result = engine.dispatch(f"sym.load {bad}")

        # Assert
        assert result.error.startswith("Parse error: "), result.error

    def test_wrong_version(self, sym_env, tmp_path):
        # Arrange
        engine, _, _ = sym_env
        bad = tmp_path / "v2.symbols.json"
        bad.write_text('{"symbols_version": 2, "symbols": []}', encoding="utf-8")

        # Act
        result = engine.dispatch(f"sym.load {bad}")

        # Assert
        assert result.error == "Parse error: symbols_version: expected 1, got 2"

    def test_unload_twice(self, sym_env):
        # Arrange
        engine, config_path, output = sym_env
        _load(engine, config_path)

        # Act
        first = engine.dispatch("sym.unload")
        second = engine.dispatch("sym.unload")

        # Assert
        assert first.value == DEMO_COUNT, "value is the count that was unloaded"
        assert (f"Unloaded symbols ({DEMO_COUNT}).", "green") in output
        assert get_table(engine.ctx) is None, "namespace cleared"
        assert second.success, second.error
        assert second.value == "0", "a second unload is a no-op, not an error"
        assert (STATE_MESSAGES["symbols_loaded"], "yellow") in output
        assert (config_path.parent / "sym" / f"rig{SYMBOLS_SUFFIX}").exists(), (
            "the file is untouched"
        )


# ── /sym ────────────────────────────────────────────────────────────────────


class TestSymRoot:
    def test_address_to_symbolic(self, sym_env):
        # Arrange
        engine, config_path, output = sym_env
        _load(engine, config_path)

        # Act
        result = engine.dispatch("sym 0x2010")

        # Assert
        assert result.value == "main+0x10", "numeric target -> symbolic name"
        assert ("  0x00002010  main+0x10  [code 442 bytes main.c]", "green") in output

    def test_name_to_address(self, sym_env):
        # Arrange
        engine, config_path, _ = sym_env
        _load(engine, config_path)

        # Act / Assert
        assert engine.dispatch("sym gTemp").value == "0x00001000", "symbolic target -> hex address"

    def test_decimal_literal(self, sym_env):
        # Arrange
        engine, config_path, _ = sym_env
        _load(engine, config_path)

        # Act / Assert
        assert engine.dispatch("sym 4096").value == "gTemp", "4096 is 0x1000, which is gTemp"

    def test_decimal_is_not_hex(self, sym_env):
        # Arrange
        engine, config_path, output = sym_env
        _load(engine, config_path)

        # Act
        result = engine.dispatch("sym 1000")

        # Assert
        assert result.success, result.error
        assert result.value == "", "lookup ran, nothing there"
        assert ("0x000003E8  -- no symbol", "yellow") in output, "printed so the mistake is visible"
        assert result.data is not None and result.data["symbol"] is None, "miss record"

    def test_name_plus_offset(self, sym_env):
        # Arrange
        engine, config_path, _ = sym_env
        _load(engine, config_path)

        # Act / Assert
        assert engine.dispatch("sym main+0x10").value == "0x00002010"
        assert engine.dispatch("sym count.12+4").value == "0x00001010", "dotted name with offset"

    def test_ambiguous(self, sym_env):
        # Arrange
        engine, config_path, _ = sym_env
        _load(engine, config_path)

        # Act
        result = engine.dispatch("sym tick")

        # Assert
        assert result.error == (
            "Ambiguous symbol: tick (0x00001010 mon.c, 0x00001014 adc.c; "
            "use tick@mon.c or the address)"
        )
        assert engine.dispatch("sym tick@adc.c").value == "0x00001014", "qualified form resolves"

    def test_named_alias_is_shown_not_the_sized_neighbor(self, sym_env, tmp_path):
        # Arrange -- two symbols at one address: the sized one is what lookup lands on
        engine, config_path, output = sym_env
        aliased = tmp_path / "aliased.symbols.json"
        aliased.write_text(
            json.dumps(
                {
                    "symbols_version": 1,
                    "symbols": [
                        {"name": "main", "addr": "0x2000", "size": 442, "section": "text"},
                        {"name": "_start", "addr": "0x2000", "size": 0, "section": "global"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        engine.dispatch(f"sym.load {aliased}")

        # Act
        named = engine.dispatch("sym _start")
        offset = engine.dispatch("sym _start+4")

        # Assert
        assert named.value == "0x00002000", "the alias resolves to the shared address"
        assert ("  0x00002000  _start  [global]", "green") in output, "what was NAMED is shown"
        assert offset.data is not None, "record on the offset form"
        assert offset.data["symbol"]["name"] == "main", (
            "with an offset the containing range is the question, so lookup answers"
        )

    def test_offset_below_zero_is_an_error(self, sym_env):
        # Arrange
        engine, config_path, _ = sym_env
        _load(engine, config_path)

        # Act / Assert
        assert engine.dispatch("sym main-0x3000").error == "Invalid address: main-0x3000"

    def test_reserved_suffix_refused(self, sym_env):
        # Arrange
        engine, config_path, _ = sym_env
        _load(engine, config_path)

        # Act / Assert
        assert engine.dispatch("sym U1MODE.ON").error == "Unsupported address suffix: .ON"

    def test_bare_is_usage(self, sym_env):
        # Arrange
        engine, _, _ = sym_env

        # Act
        result = engine.dispatch("sym")

        # Assert
        assert result.error == "Usage: /sym <addr|name>", "dispatcher renders the synopsis"

    def test_name_without_table(self, sym_env):
        # Arrange
        engine, _, _ = sym_env

        # Act / Assert
        assert engine.dispatch("sym gTemp").error == STATE_MESSAGES["symbols_loaded"]

    def test_literal_without_table(self, sym_env):
        # Arrange
        engine, _, _ = sym_env

        # Act
        result = engine.dispatch("sym 0x1000")

        # Assert
        assert result.success, result.error
        assert result.value == "", "a literal parses with no table"

    def test_data_has_fixed_keys_on_hit_and_miss(self, sym_env):
        # Arrange
        engine, config_path, _ = sym_env
        _load(engine, config_path)

        # Act
        hit = engine.dispatch("sym main").data
        miss = engine.dispatch("sym 0x9000").data

        # Assert
        expected = {"query", "addr", "addr_hex", "symbolic", "symbol", "offset", "suffix"}
        assert hit is not None and set(hit) == expected, "hit record keys"
        assert miss is not None and set(miss) == expected, "miss record keys"
        assert hit["symbol"]["name"] == "main", "hit names the symbol"
        assert miss["symbol"] is None, "miss carries null, not a missing key"


# ── /sym.search / /sym.info ─────────────────────────────────────────────────


class TestSymSearch:
    def test_the_matches_are_a_table(self, sym_env):
        """list-one-format: one row per match in address order, every fact of a symbol line a column, the value the count."""
        # Arrange
        engine, config_path, output = sym_env
        _load(engine, config_path)
        output.clear()

        # Act
        result = engine.dispatch("sym.search g*")
        texts = _texts(output)

        # Assert
        assert result.value == 3, "the match count"
        assert [rec["name"] for rec in result.data] == ["gTemp", "gPressure", "gFlags"], (
            "records in address order, whoever asked"
        )
        assert texts[0] == "Symbols matching g* - 3 symbols", "the table's title"
        assert texts[1].split() == ["ADDRESS", "NAME", "SECTION", "SIZE", "FILE"], (
            "the line's facts, a column each; NOTE hides when no match has one"
        )
        assert [line.split()[:5] for line in texts[3:]] == [
            ["0x00001000", "gTemp", "bss", "2", "sensor.c"],
            ["0x00001002", "gPressure", "bss", "4", "sensor.c"],
            ["0x00001008", "gFlags", "data", "4", "main.c"],
        ], "one row per match"

    def test_a_data_caller_gets_the_same_records(self, sym_env):
        """A caller that wants data gets the records alone; dispatch prints no table for it."""
        # Arrange
        engine, config_path, output = sym_env
        _load(engine, config_path)
        before = len(output)
        engine.ctx.wants_data = True

        # Act
        try:
            result = engine.dispatch("sym.search g*")
        finally:
            engine.ctx.wants_data = False

        # Assert
        assert [rec["name"] for rec in result.data] == ["gTemp", "gPressure", "gFlags"]
        assert len(output) == before, "nothing printed for a data caller"

    def test_no_match_is_the_empty_table(self, sym_env):
        """No match succeeds with no records, a count of none and the table's own empty line."""
        # Arrange
        engine, config_path, output = sym_env
        _load(engine, config_path)
        output.clear()

        # Act
        result = engine.dispatch("sym.search zzz")

        # Assert
        assert result.success, result.error
        assert (result.value, result.data) == (0, []), "none matched"
        assert _texts(output) == ["Symbols matching zzz - empty"]

    def test_the_note_is_the_one_phrasing_of_what_a_symbol_stands_for(self):
        """symbol_note feeds the NOTE column and a symbol line's tail alike: alias, pointer target, value, or nothing."""
        from termapy.symbols.format import symbol_note

        base = {"alias_of": "", "value": None, "pointee": ""}
        assert symbol_note({**base, "alias_of": "PORTC_DIR"}) == "= PORTC_DIR", (
            "an alias names its register"
        )
        assert symbol_note({**base, "value": 4}) == "= 4", "a constant shows its value"
        assert symbol_note({**base, "value": 0x50000000, "pointee": "u32"}) == "-> 0x50000000", (
            "a pointer shows where it points"
        )
        assert symbol_note(base) == "", "a plain symbol has no note"

    def test_no_table(self, sym_env):
        # Arrange
        engine, _, _ = sym_env

        # Act / Assert
        assert engine.dispatch("sym.search main").error == STATE_MESSAGES["symbols_loaded"]

    def test_bare_is_usage(self, sym_env):
        """With a table loaded, a bare search is a usage error."""
        # Arrange
        engine, config_path, _ = sym_env
        _load(engine, config_path)

        # Act / Assert
        assert engine.dispatch("sym.search").error == "Usage: /sym.search <pattern>"

    def test_no_table_is_the_gates_answer_before_the_arguments(self, sym_env):
        """state-answered-by-gate: with no table the gate answers, before a bare search's usage error."""
        # Arrange
        engine, _, _ = sym_env

        # Act / Assert
        assert engine.dispatch("sym.search").error == STATE_MESSAGES["symbols_loaded"], (
            "the missing state first, as config and port do"
        )


class TestSymInfo:
    def test_loaded(self, sym_env):
        # Arrange
        engine, config_path, output = sym_env
        _load(engine, config_path)
        table = get_table(engine.ctx)
        assert table is not None

        # Act
        result = engine.dispatch("sym.info")

        # Assert
        assert result.value == DEMO_COUNT
        assert result.data is not None and result.data["sections"] == table.stats()
        assert result.data["loaded"] is True, "the loaded record says so, as the empty one says not"
        rows = [text for text, color in output if color == "markup"]
        assert any(f"sym/rig{SYMBOLS_SUFFIX}" in row for row in rows), "file row is config-relative"
        assert not any(str(config_path.parent) in row for row in rows), "no absolute path in-folder"

    def test_none_loaded(self, sym_env):
        # Arrange
        engine, _, output = sym_env

        # Act
        result = engine.dispatch("sym.info")

        # Assert
        assert result.success, result.error
        assert result.value == "", "no table = empty value, not an error"
        assert result.data == {"loaded": False}, "an agent reads the state, not the prose"
        assert (
            f"{STATE_MESSAGES['symbols_loaded']}  /sym.import <map> or /sym.load {{path}} to load one.",
            "yellow",
        ) in output


class TestSymHelp:
    def test_long_help_reports_the_loaded_state(self, sym_env):
        # Arrange
        engine, config_path, output = sym_env

        # Act
        engine.dispatch("help sym")
        before = list(_texts(output))
        _load(engine, config_path)
        engine.dispatch("help sym")
        after = _texts(output)[len(before) :]

        # Assert
        assert any("Current symbols = none" in text for text in before), "no table -> none"
        assert any(
            f"Current symbols = {DEMO_COUNT} (sym/rig{SYMBOLS_SUFFIX})" in text for text in after
        ), "loaded -> count and the config-relative sidecar path"


# ── Sandbox and the value contract ──────────────────────────────────────────


class TestSandbox:
    def test_absolute_paths_refused_when_confined(self, tmp_path):
        # Arrange -- an MCP host without TERMAPY_MCP_FS_UNCONFINED
        engine, config_path, _ = _build(tmp_path, unconfined=False)
        _install_sidecar(config_path)

        # Act
        imported = engine.dispatch(f"sym.import {XC32_MAP}")
        loaded = engine.dispatch(f"sym.load {DEMO_SYMBOLS}")
        bare = engine.dispatch("sym.load")

        # Assert
        assert "outside the config sandbox" in imported.error, "/sym.import <abs> refused"
        assert "outside the config sandbox" in loaded.error, "/sym.load <abs> refused"
        assert bare.success, bare.error
        assert bare.value == DEMO_COUNT, "the sidecar is always in-sandbox"


class TestValueContract:
    @pytest.mark.parametrize(
        "line",
        [
            "sym main",
            "sym 1000",
            "sym.search g*",
            "sym.search zzz",
            "sym.info",
            "sym.unload",
            "sym.load",
        ],
    )
    def test_every_command_returns_a_scalar_value(self, sym_env, line):
        """value-typed: every /sym command answers a scalar, text or a count, that $(X) <- spells for a variable."""
        # Arrange
        engine, config_path, _ = sym_env
        _load(engine, config_path)

        # Act
        result = engine.dispatch(line)

        # Assert
        assert result.success, result.error
        assert isinstance(result.value, (str, int)), f"{line}: value must be a scalar"


# ── /dev.list ───────────────────────────────────────────────────────────────


class TestDevList:
    """What THIS config loaded -- the 'installed' half of the two listings."""

    def test_no_devices_says_so(self, sym_env):
        """list-one-format: no devices is the shared empty table, not a hand-written line."""
        # Arrange
        engine, _, output = sym_env
        engine.fire_lifecycle("on_app_start")
        output.clear()

        # Act
        result = engine.dispatch("dev.list")

        # Assert
        assert result.success, result.error
        assert result.value == 0, "nothing loaded, none counted"
        assert _texts(output) == ["devices - empty"], "the table's own empty answer"

    def test_lists_a_loaded_device_with_its_layer(self, sym_env):
        # Arrange
        engine, config_path, _ = sym_env
        _install_device(config_path.parent / "dev", "part", _PART)
        engine.fire_lifecycle("on_app_start")

        # Act
        result = engine.dispatch("dev.list")

        # Assert
        assert result.success, result.error
        assert [record["device"] for record in result.data] == ["part"], "the device is a record"
        assert result.value == 1, "the value is the count, for capture"

    def test_a_relocatable_part_shows_its_placement(self, sym_env):
        """Placement is what distinguishes two copies of one chip."""
        # Arrange
        engine, config_path, output = sym_env
        _install_device(
            config_path.parent / "dev",
            "fpga",
            [{"name": "STATUS", "addr": "0x00", "size": 4}],
            relocatable=True,
            instances=[
                {"name": "FPGA0", "base": "0x60000000"},
                {"name": "FPGA1", "base": "0x60010000"},
            ],
        )
        engine.fire_lifecycle("on_app_start")
        output.clear()  # drop the load lines

        # Act
        engine.dispatch("dev.list")

        # Assert
        out = "\n".join(_texts(output))
        assert "FPGA0@0x60000000" in out, "each instance is named with its base"
        assert "FPGA1@0x60010000" in out, "both copies, so a collision is visible"

    def test_a_fixed_address_part_has_no_placement(self, sym_env):
        """A part at fixed addresses has an empty PLACEMENT cell, so its row ends at the layer."""
        # Arrange
        engine, config_path, output = sym_env
        _install_device(config_path.parent / "dev", "part", _PART)
        engine.fire_lifecycle("on_app_start")
        output.clear()

        # Act
        engine.dispatch("dev.list")

        # Assert
        lines = _texts(output)
        assert lines[0] == "devices - 1 device", "the shared table's title line"
        row = next(line for line in lines if line.startswith("part "))
        assert row.endswith("rig"), "nothing trails the layer when unplaced"

    def test_data_carries_the_records_not_prose(self, sym_env):
        # Arrange
        engine, config_path, _ = sym_env
        _install_device(config_path.parent / "dev", "part", _PART)
        engine.fire_lifecycle("on_app_start")

        # Act
        result = engine.dispatch("dev.list --json")

        # Assert
        assert isinstance(result.data, list), "a list of records, one per device"
        assert result.data[0]["device"] == "part", "named"
        assert result.data[0]["registers"] == 2, "with its register count"
        assert result.data[0]["path"].endswith("part.device.json"), "and where it came from"

    def test_global_and_per_config_layers_are_distinguished(self, sym_env, tmp_path):
        """A part every config loads and a part this board has are different facts."""
        # Arrange
        engine, config_path, output = sym_env
        _install_device(
            tmp_path / "dev", "globalpart", [{"name": "G_REG", "addr": "0x70000000", "size": 4}]
        )
        _install_device(
            config_path.parent / "dev",
            "localpart",
            [{"name": "L_REG", "addr": "0x80000000", "size": 4}],
        )
        engine.fire_lifecycle("on_app_start")
        output.clear()

        # Act
        engine.dispatch("dev.list")

        # Assert
        out = "\n".join(_texts(output))
        assert "global" in out, "the global layer is named as such"
        assert config_path.stem in out, "the per-config layer carries the config name"


class TestDevRegs:
    """One loaded device's registers, read from its file and never from the chip."""

    _REGS = [
        {
            "name": "CTRL",
            "addr": "0x50000000",
            "size": 4,
            "peripheral": "ADC",
            "description": "Control",
            "reset": "0x00000001",
            "fields": [{"name": "EN", "bit": 0}],
        },
        {
            "name": "DATA",
            "addr": "0x50000004",
            "size": 4,
            "access": "ro",
            "read_effect": True,
            "peripheral": "ADC",
            "description": "Result FIFO",
        },
        {
            "name": "INTFLAG",
            "addr": "0x50000008",
            "size": 1,
            "rmw": False,
            "peripheral": "ADC",
            "description": "Flags; write 1 to clear",
        },
        {"name": "UART_DATA", "addr": "0x50001000", "size": 4, "peripheral": "UART"},
    ]

    def _engine(self, sym_env):
        """sym_env with the hazard part installed and loaded, its load lines dropped."""
        engine, config_path, output = sym_env
        _install_device(config_path.parent / "dev", "hazard", self._REGS)
        engine.fire_lifecycle("on_app_start")
        output.clear()
        return engine, output

    def test_lists_every_register_in_file_order(self, sym_env):
        """The records are the registers in file order and the value their count; the title names the device."""
        # Arrange
        engine, output = self._engine(sym_env)
        # Act
        result = engine.dispatch("dev.regs hazard")
        # Assert
        assert result.success, result.error
        assert [r["name"] for r in result.data] == ["CTRL", "DATA", "INTFLAG", "UART_DATA"]
        assert result.value == 4, "the value counts the registers"
        assert _texts(output)[0] == "hazard - 4 registers", "the shared table's title line"

    def test_flags_show_the_hazards(self, sym_env):
        """FLAGS names read_effect and rmw: false as the file spells them, and nothing else."""
        # Arrange
        engine, output = self._engine(sym_env)
        # Act
        engine.dispatch("dev.regs hazard")
        # Assert
        rows = {line.split()[1]: line for line in _texts(output)[3:]}
        assert "read_effect" in rows["DATA"], "a read that pops the FIFO is flagged"
        assert "rmw: false" in rows["INTFLAG"], "a write-1-to-clear register is flagged"
        assert "read_effect" not in rows["CTRL"] and "rmw" not in rows["CTRL"], (
            "a plain register carries no flag"
        )

    def test_the_flag_cell_is_drawn_in_yellow(self, sym_env):
        """The hazard stands out where the frontend can color it; the plain text is unchanged."""
        # Arrange
        engine, _ = self._engine(sym_env)
        pieces: list = []
        engine.ctx.io._write_segments = lambda segment, color: pieces.extend(segment)
        # Act
        engine.dispatch("dev.regs hazard DATA")
        # Assert
        flagged = [piece for piece in pieces if piece.text == "read_effect"]
        assert flagged and flagged[0].style == "yellow", "the FLAGS cell carries its style"

    def test_a_pattern_matches_register_or_peripheral(self, sym_env):
        """name-one-matcher: plain text contains, over the name and the peripheral."""
        # Arrange
        engine, _ = self._engine(sym_env)
        # Act
        by_peripheral = engine.dispatch("dev.regs hazard UART")
        by_glob = engine.dispatch("dev.regs hazard *DATA")
        nothing = engine.dispatch("dev.regs hazard zzz*")
        # Assert
        assert [r["name"] for r in by_peripheral.data] == ["UART_DATA"], (
            "the peripheral picked its block"
        )
        assert [r["name"] for r in by_glob.data] == ["DATA", "UART_DATA"], "an anchored glob"
        assert nothing.success and nothing.value == 0, "no match is an empty answer"

    def test_data_carries_the_safety_facts_and_fields(self, sym_env):
        """list-one-format: the records hold the safety trio, reset and fields for an agent."""
        # Arrange
        engine, _ = self._engine(sym_env)
        # Act
        result = engine.dispatch("dev.regs hazard --json")
        # Assert
        by_name = {record["name"]: record for record in result.data}
        assert by_name["DATA"]["read_effect"] is True, "the read hazard is data"
        assert by_name["INTFLAG"]["rmw"] is False, "so is the write hazard"
        assert by_name["CTRL"]["reset"] == 1, "the reset value, as a number"
        assert by_name["CTRL"]["fields"][0]["name"] == "EN", "and the bit fields"
        assert by_name["CTRL"]["addr_hex"] == "0x50000000", "hex beside the integer"

    def test_runs_with_no_port_open(self, sym_env):
        """The listing reads the file, never the chip: it needs no connection at all."""
        # Arrange: sym_env has no serial port, so any device read would be refused
        engine, _ = self._engine(sym_env)
        # Act
        result = engine.dispatch("dev.regs hazard")
        # Assert
        assert result.success, "listing a read_effect register touched nothing"

    def test_an_unknown_device_fails_and_points_at_the_list(self, sym_env):
        """A device that is not loaded fails with the standard phrasing and a way forward."""
        # Arrange
        engine, _ = self._engine(sym_env)
        # Act
        result = engine.dispatch("dev.regs nosuch")
        # Assert
        assert result.success is False, "nothing to list is a failure"
        assert result.error.startswith("Unknown device: nosuch"), result.error
        assert "dev.list" in result.error, "and it says how to see what is loaded"


# -- /dev.lib ----------------------------------------------------------------


def _install_library(root: Path, category: str, name: str, **extra) -> Path:
    """Write a library part under ``root/<category>/<name>.device.json``."""
    folder = root / "lib" / category if category else root / "lib"
    folder.mkdir(parents=True, exist_ok=True)
    doc = {
        "device_version": 1,
        "device": name,
        "registers": [{"name": "R", "addr": "0x90000000", "size": 4}],
    } | extra
    file = folder / f"{name}.device.json"
    file.write_text(json.dumps(doc), encoding="utf-8")
    return file


class TestDevLib:
    """What the LIBRARY holds -- the 'available' half, and never loaded."""

    def test_empty_library_says_so(self, sym_env):
        # Arrange
        engine, _, _ = sym_env

        # Act
        result = engine.dispatch("dev.lib")

        # Assert
        assert result.success, result.error
        assert result.value == 0, "nothing available, none counted"

    def test_lists_parts_with_their_category(self, sym_env, tmp_path):
        # Arrange
        engine, _, output = sym_env
        _install_library(tmp_path, "microchip/mcu", "somepart")
        output.clear()

        # Act
        result = engine.dispatch("dev.lib")

        # Assert
        assert _part_names(result) == ["somepart"], "the part is a record"
        assert result.value == 1, "the value is the count"
        assert "microchip/mcu" in "\n".join(_texts(output)), "category is shown"

    def test_listing_does_not_load_anything(self, sym_env, tmp_path):
        """The library is a pool; a thousand parts must not reach the table."""
        # Arrange
        engine, _, _ = sym_env
        _install_library(tmp_path, "microchip/mcu", "somepart")
        engine.fire_lifecycle("on_app_start")

        # Act
        engine.dispatch("dev.lib")

        # Assert
        assert engine.dispatch("dev.list").value == 0, "nothing became installed"
        assert engine.dispatch("sym R").success is False, "its registers do not resolve"

    def test_a_substring_filters_by_name_or_category(self, sym_env, tmp_path):
        # Arrange
        engine, _, _ = sym_env
        _install_library(tmp_path, "microchip/mcu", "alpha")
        _install_library(tmp_path, "lattice/fpga", "beta")

        # Act
        by_name = engine.dispatch("dev.lib alpha")
        by_category = engine.dispatch("dev.lib lattice")

        # Assert
        assert _part_names(by_name) == ["alpha"], "matched on the part name"
        assert _part_names(by_category) == ["beta"], "matched on the vendor folder"

    def test_a_glob_filters(self, sym_env, tmp_path):
        # Arrange
        engine, _, _ = sym_env
        _install_library(tmp_path, "m", "pic32cm1216")
        _install_library(tmp_path, "m", "pic32cm2532")
        _install_library(tmp_path, "m", "other")

        # Act
        result = engine.dispatch("dev.lib pic32cm*")

        # Assert
        assert _part_names(result) == ["pic32cm1216", "pic32cm2532"], (
            "the wildcard matched both families and excluded the rest"
        )

    def test_a_filter_matching_nothing_is_an_empty_answer(self, sym_env, tmp_path):
        """name-one-matcher: a pattern no part matches succeeds with no names."""
        # Arrange
        engine, _, output = sym_env
        _install_library(tmp_path, "m", "alpha")
        output.clear()

        # Act
        result = engine.dispatch("dev.lib zzz*")

        # Assert
        assert result.success, result.error
        assert result.value == 0, "no part matched"
        assert _texts(output) == ["library 'zzz*' - empty"], "the title names the filter"

    def test_a_loaded_part_is_marked(self, sym_env, config_dev_part, tmp_path):
        """The listing answers 'do I already have this?' in the same glance."""
        # Arrange
        engine, output = config_dev_part
        _install_library(tmp_path, "vendor", "part")
        engine.fire_lifecycle("on_app_start")
        output.clear()

        # Act
        result = engine.dispatch("dev.lib --json")
        engine.dispatch("dev.lib")

        # Assert
        assert result.data[0]["loaded"] is True, "the record says this config has the part"
        row = next(line for line in _texts(output) if line.startswith("part "))
        assert "yes" in row.split(), "and its LOADED cell shows it"

    def test_data_is_records_not_prose(self, sym_env, tmp_path):
        # Arrange
        engine, _, _ = sym_env
        _install_library(tmp_path, "microchip/mcu", "somepart", vendor="microchip")

        # Act
        result = engine.dispatch("dev.lib --json")

        # Assert
        assert isinstance(result.data, list), "one record per part"
        assert result.data[0]["device"] == "somepart"
        assert result.data[0]["category"] == "microchip/mcu", "browsable location"
        assert result.data[0]["relative"].endswith("somepart.device.json"), (
            "a path relative to the library root, for a follow-up command"
        )


@pytest.fixture
def config_dev_part(sym_env, tmp_path):
    """sym_env with a device named 'part' installed in the config's dev/."""
    engine, config_path, output = sym_env
    _install_device(config_path.parent / "dev", "part", _PART)
    return engine, output


# -- /dev.use, and where /dev.import writes ----------------------------------


class TestDevUse:
    """Adding a library part to THIS config, with this board's addresses."""

    def test_adds_a_fixed_address_part(self, sym_env, tmp_path):
        # Arrange
        engine, config_path, _ = sym_env
        _install_library(tmp_path, "vendor", "fixedpart")

        # Act
        result = engine.dispatch("dev.use fixedpart")

        # Assert
        assert result.success, result.error
        ref = config_path.parent / "dev" / "fixedpart.device.json"
        assert ref.is_file(), "a reference file landed in dev/"
        assert json.loads(ref.read_text(encoding="utf-8")) == {
            "device_version": 1,
            "ref": "fixedpart",
        }, "it references, it does not copy"

    def test_the_reference_is_tiny_next_to_the_part(self, sym_env, tmp_path):
        """The whole point: the part is stored once, the config says it uses it."""
        # Arrange
        engine, config_path, _ = sym_env
        part = _install_library(
            tmp_path,
            "vendor",
            "bigpart",
            registers=[
                {"name": f"REG{i}", "addr": f"0x{0x40000000 + i * 4:X}", "size": 4}
                for i in range(200)
            ],
        )

        # Act
        engine.dispatch("dev.use bigpart")

        # Assert
        ref = config_path.parent / "dev" / "bigpart.device.json"
        assert ref.stat().st_size < part.stat().st_size / 10, (
            "a reference is a fraction of the part it points at"
        )

    def test_placement_is_written_and_used(self, sym_env, tmp_path):
        # Arrange
        engine, config_path, _ = sym_env
        _install_library(
            tmp_path,
            "vendor",
            "icepart",
            relocatable=True,
            registers=[{"name": "STATUS", "addr": "0x00", "size": 4}],
        )

        # Act
        result = engine.dispatch("dev.use icepart at=FPGA0@0x70000000 FPGA1@0x70001000")

        # Assert
        assert result.success, result.error
        assert engine.dispatch("sym FPGA1_STATUS").value == "0x70001000", (
            "the second copy resolves at the base this board gave"
        )

    def test_a_relocatable_part_refuses_without_placement(self, sym_env, tmp_path):
        """And must not inherit the library's example addresses."""
        # Arrange
        engine, config_path, _ = sym_env
        _install_library(
            tmp_path,
            "vendor",
            "icepart",
            relocatable=True,
            instances=[{"name": "EXAMPLE", "base": "0x10000000"}],
            registers=[{"name": "STATUS", "addr": "0x00", "size": 4}],
        )

        # Act
        result = engine.dispatch("dev.use icepart")

        # Assert
        assert not result.success, "a plausible wrong address is the hazard"
        assert "needs placement" in result.error, "and the fix is spelled out"

    def test_a_refused_placement_leaves_no_file_behind(self, sym_env, tmp_path):
        """Otherwise every later load would report the same error."""
        # Arrange
        engine, config_path, _ = sym_env
        _install_library(
            tmp_path,
            "vendor",
            "icepart",
            relocatable=True,
            registers=[{"name": "STATUS", "addr": "0x00", "size": 4}],
        )

        # Act
        engine.dispatch("dev.use icepart")

        # Assert
        ref = config_path.parent / "dev" / "icepart.device.json"
        assert not ref.exists(), "the half-written reference was cleaned up"

    def test_a_part_that_will_not_load_reports_the_loaders_error(self, sym_env, tmp_path):
        """Not every failed load is a missing placement; say what the loader said."""
        # Arrange -- two fixed parts owning the same register name
        engine, config_path, _ = sym_env
        _install_library(tmp_path, "vendor", "one")
        _install_library(tmp_path, "vendor", "two")
        assert engine.dispatch("dev.use one").success, "the first owner loads"

        # Act
        result = engine.dispatch("dev.use two")

        # Assert
        assert not result.success, "R is already owned by one"
        assert "two not added: register R is also defined by one.device.json" in result.error, (
            "the loader's own verdict, not a placement guess"
        )
        assert "needs placement" not in result.error, "a fixed part is never told to place itself"
        assert not (config_path.parent / "dev" / "two.device.json").exists(), (
            "and the reference that would not load was removed"
        )

    def test_an_unknown_part_points_at_the_listing(self, sym_env):
        # Arrange
        engine, _, _ = sym_env

        # Act
        result = engine.dispatch("dev.use nosuch")

        # Assert
        assert not result.success
        assert "Unknown library part: nosuch" in result.error, (
            "the standard Unknown <thing> phrasing"
        )
        assert "dev.lib" in result.error, "and says how to find the real names"

    @pytest.mark.parametrize("token", ["FPGA0", "@0x60000000", "FPGA0@zz"])
    def test_a_malformed_placement_is_named(self, sym_env, tmp_path, token):
        # Arrange
        engine, _, _ = sym_env
        _install_library(
            tmp_path,
            "vendor",
            "icepart",
            relocatable=True,
            registers=[{"name": "STATUS", "addr": "0x00", "size": 4}],
        )

        # Act
        result = engine.dispatch(f"dev.use icepart at={token}")

        # Assert
        assert not result.success, f"{token}: not a NAME@ADDRESS"
        assert "Invalid" in result.error, "and the token is named"


class TestImportWritesToLibrary:
    """A converted part is a fact about silicon, so it is stored once."""

    def test_import_writes_the_library_and_a_reference(self, sym_env, tmp_path):
        # Arrange
        engine, config_path, _ = sym_env

        # Act
        result = engine.dispatch(f"dev.import {SVD_SAMPLE}")

        # Assert
        assert result.success, result.error
        library = config_path.parent / LIB
        parts = list(library.rglob("*.device.json"))
        assert len(parts) == 1, "the part landed in THIS config's library"
        assert not (tmp_path / LIB).exists(), "and not in the shared one"
        ref = config_path.parent / "dev" / "atsample1.device.json"
        assert json.loads(ref.read_text(encoding="utf-8"))["ref"] == "atsample1", (
            "and the config references it rather than copying it"
        )

    def test_the_registers_resolve_through_the_reference(self, sym_env):
        # Arrange
        engine, _, _ = sym_env

        # Act
        engine.dispatch(f"dev.import {SVD_SAMPLE}")

        # Assert
        assert engine.dispatch("sym PORT_GROUP1_DIR").value == "0x40003080", (
            "a referenced register resolves like any other"
        )

    def test_a_category_nests_the_part(self, sym_env):
        # Arrange
        engine, config_path, _ = sym_env

        # Act
        engine.dispatch(f"dev.import {SVD_SAMPLE} category=mcu/sam")

        # Assert
        parts = list((config_path.parent / LIB).rglob("*.device.json"))
        assert "mcu/sam" in parts[0].as_posix(), "filed where the user said"

    def test_use_off_leaves_the_config_alone(self, sym_env):
        """Import as a librarian, without adding it to the board in front of you."""
        # Arrange
        engine, _, _ = sym_env

        # Act
        result = engine.dispatch(f"dev.import {SVD_SAMPLE} use=off")

        # Assert
        assert result.success, result.error
        assert engine.dispatch("dev.list").value == 0, "nothing was added here"
        assert _part_names(engine.dispatch("dev.lib")) == ["atsample1"], "but it is available"


# -- "not found" says where it looked ----------------------------------------


class TestNotFoundNamesTheFolder:
    """A relative path is measured from the CONFIG folder, not the shell's cwd.

    Without that in the message a user retypes variations of the same
    relative path while the command keeps looking somewhere else -- which
    is exactly what happened in use.
    """

    @pytest.mark.parametrize(
        "line, subject",
        [
            ("dev.import ../part.svd", "Source file"),
            ("sym.import nope.map", "Build file"),
        ],
    )
    def test_a_relative_path_names_the_anchor(self, sym_env, line, subject):
        # Arrange
        engine, config_path, _ = sym_env

        # Act
        result = engine.dispatch(line)

        # Assert
        assert not result.success, "the file really is missing"
        assert f"{subject} not found" in result.error, "the standard phrasing"
        assert config_path.parent.name in result.error, (
            "and the folder the relative path was measured from"
        )

    def test_an_absolute_path_gets_the_short_form(self, sym_env, tmp_path):
        """It is unambiguous already; an anchor note would be noise."""
        # Arrange
        engine, _, _ = sym_env
        missing = tmp_path / "nowhere" / "part.svd"

        # Act
        result = engine.dispatch(f"dev.import {missing}")

        # Assert
        assert not result.success
        assert "relative path is measured" not in result.error, (
            "nothing to explain about an absolute path"
        )


# -- The per-config library layer -------------------------------------------


def _install_config_library(config_path: Path, category: str, name: str, **extra) -> Path:
    """Write a part under the CONFIG's own lib/ (the per-config layer)."""
    folder = config_path.parent / LIB / category if category else config_path.parent / LIB
    folder.mkdir(parents=True, exist_ok=True)
    doc = {
        "device_version": 1,
        "device": name,
        "registers": [{"name": "R", "addr": "0x90000000", "size": 4}],
    } | extra
    file = folder / f"{name}.device.json"
    file.write_text(json.dumps(doc), encoding="utf-8")
    return file


class TestLibraryLayersCommands:
    """The config's own lib/ over the shared one, through the commands."""

    def test_lib_lists_both_layers_with_their_layer(self, sym_env, tmp_path):
        # Arrange
        engine, config_path, output = sym_env
        _install_library(tmp_path, "vendor", "sharedpart")
        _install_config_library(config_path, "vendor", "localpart")
        output.clear()

        # Act
        result = engine.dispatch("dev.lib")

        # Assert
        assert _part_names(result) == ["localpart", "sharedpart"], "both offered"
        text = "\n".join(_texts(output))
        assert "global" in text and "rig" in text, "each part says which library holds it"

    def test_the_per_config_part_shadows_the_shared_one(self, sym_env, tmp_path):
        # Arrange
        engine, config_path, _ = sym_env
        _install_library(tmp_path, "vendor", "twin")
        _install_config_library(config_path, "vendor", "twin")

        # Act
        result = engine.dispatch("dev.lib --json")

        # Assert
        assert [record["layer"] for record in result.data] == ["rig"], "one entry, the config's"

    def test_use_resolves_a_per_config_part(self, sym_env):
        # Arrange
        engine, config_path, _ = sym_env
        _install_config_library(config_path, "vendor", "localpart")

        # Act
        result = engine.dispatch("dev.use localpart")

        # Assert
        assert result.success, result.error
        assert engine.dispatch("sym R").value == "0x90000000", "its register resolves"

    def test_import_to_global_lands_in_the_shared_library(self, sym_env, tmp_path):
        # Arrange
        engine, config_path, _ = sym_env

        # Act
        result = engine.dispatch(f"dev.import {SVD_SAMPLE} to=global")

        # Assert
        assert result.success, result.error
        assert list((tmp_path / LIB).rglob("*.device.json")), "in termapy_cfg/lib/"
        assert not (config_path.parent / LIB).exists(), "and not in the config's"
        assert result.data["layer"] == "global", "the record says which"

    def test_import_names_an_unknown_target(self, sym_env):
        # Arrange
        engine, _, _ = sym_env

        # Act
        result = engine.dispatch(f"dev.import {SVD_SAMPLE} to=elsewhere")

        # Assert
        assert not result.success
        assert "Invalid to: elsewhere" in result.error, "and names the choices"

    def test_an_empty_per_config_lib_is_pruned_at_load(self, sym_env):
        """lib/ is a data folder now: it exists while something is in it."""
        # Arrange
        engine, config_path, _ = sym_env
        (config_path.parent / LIB).mkdir()

        # Act
        engine.fire_lifecycle("on_config_load")

        # Assert
        assert not (config_path.parent / LIB).exists(), "empty, so gone"


# A converter that also declares regions, and one whose regions is not a
# function -- the optional export follows the same fail-loud rule.
_REGIONS_CONVERTER = """
from termapy.symbols import Layout, Region
from termapy.symbols.converters import xc32

FORMAT = "withregions"
DESCRIPTION = "xc32 map with a hand-declared FRAM region"
DETECT = ()

convert = xc32.convert


def layout(text):
    return Layout(regions=(Region("fram", 0x60000000, 0x8000, "rw"),))
"""

_BAD_REGIONS_CONVERTER = """
FORMAT = "badregions"
DESCRIPTION = "layout is a list, not a function"
DETECT = ()
layout = []


def convert(text):
    return []
"""


class TestConverterRegions:
    def test_a_plugin_converter_may_declare_regions(self, sym_env):
        """The optional layout(text) export supplies the table's regions."""
        # Arrange
        engine, config_path, _ = sym_env
        _install_converter(config_path, _REGIONS_CONVERTER)
        engine.fire_lifecycle("on_app_start")

        # Act
        result = engine.dispatch(f"sym.import {XC32_MAP} format=withregions")

        # Assert
        assert result.success, result.error
        table = get_table(engine.ctx)
        assert table is not None and [region.name for region in table.regions] == ["fram"]

    def test_a_converter_without_regions_gives_none(self, sym_env):
        """A converter that does not export layout imports a table with no regions."""
        # Arrange
        engine, config_path, _ = sym_env
        _install_converter(config_path, _PIPELINE_CONVERTER)
        engine.fire_lifecycle("on_app_start")

        # Act
        engine.dispatch(f"sym.import {XC32_MAP} format=myboard")

        # Assert
        table = get_table(engine.ctx)
        assert table is not None and table.regions == [], "regions is optional"

    def test_a_layout_that_is_not_a_function_is_reported(self, sym_env):
        """A non-callable layout export is an authoring error, reported at load."""
        # Arrange
        engine, config_path, output = sym_env
        _install_converter(config_path, _BAD_REGIONS_CONVERTER)

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        errors = [text for text in _texts(output) if "Plugin error" in text]
        assert any("badregions" in text and "layout" in text for text in errors), (
            "the report names the converter and the export"
        )


# ── ELF import ──────────────────────────────────────────────────────────────

ELF_DIR = Path(__file__).parent / "fixtures" / "elf"
XC32_ELF = ELF_DIR / "elf_xc32_cm23.elf"
CLANG_ELF = ELF_DIR / "elf_clang_ppc64.elf"


class TestElfImport:
    def _doc(self, config_path: Path) -> dict:
        sidecar = config_path.parent / "sym" / f"rig{SYMBOLS_SUFFIX}"
        return json.loads(sidecar.read_text(encoding="utf-8"))

    def test_an_elf_imports_with_its_types(self, sym_env):
        """The ELF is recognized by its signature, and its debug info types the sidecar."""
        # Arrange
        engine, config_path, _ = sym_env

        # Act
        result = engine.dispatch(f"sym.import {XC32_ELF}")

        # Assert
        assert result.success, result.error
        doc = self._doc(config_path)
        types = {row["name"]: row.get("type", "") for row in doc["symbols"]}
        assert (types["gI16"], types["gF32"], types["main"]) == ("i16", "f32", ""), (
            "a signed short, a float, and code with no value type"
        )
        assert doc["recipe"] == {"converter": "elf"}, "the converter that ran is recorded"
        assert doc["witness"]["size"] == XC32_ELF.stat().st_size, "the ELF is the witnessed source"

    def test_the_header_sets_width_and_byte_order(self, sym_env):
        """A 64-bit big-endian build's table says so, from the ELF header."""
        # Arrange
        engine, _, _ = sym_env

        # Act
        engine.dispatch(f"sym.import {CLANG_ELF}")

        # Assert
        table = get_table(engine.ctx)
        assert table is not None and (table.address_bits, table.endian) == (64, "be")

    def test_regions_are_the_loadable_segments(self, sym_env):
        """The RAM segment is the last region, read-write, named after its first section."""
        # Arrange
        engine, config_path, _ = sym_env

        # Act
        engine.dispatch(f"sym.import {XC32_ELF}")

        # Assert
        regions = self._doc(config_path)["regions"]
        assert regions[-1] == {"name": "bss", "start": "0x20000000", "size": 188, "access": "rw"}

    def test_bare_import_reruns_the_elf(self, sym_env, tmp_path):
        """After a rebuild, bare /sym.import reads the same ELF through the same converter."""
        # Arrange
        engine, config_path, _ = sym_env
        build = tmp_path / "default.elf"
        build.write_bytes(XC32_ELF.read_bytes())
        engine.dispatch(f"sym.import {build}")

        # Act
        result = engine.dispatch("sym.import")

        # Assert
        assert result.success, result.error
        assert self._doc(config_path)["recipe"] == {"converter": "elf"}, "the same pipeline again"

    def test_format_elf_names_the_converter(self, sym_env):
        """format=elf picks the converter explicitly, as format=xc32 does for a map."""
        # Arrange
        engine, _, _ = sym_env

        # Act
        result = engine.dispatch(f"sym.import {XC32_ELF} format=elf")

        # Assert
        assert result.success, result.error


# A binary plugin format: MAGIC makes it read bytes and be sniffed by them.
_BINARY_CONVERTER = """
from termapy.symbols import Symbol

FORMAT = "rawbin"
DESCRIPTION = "four bytes of signature, four of address"
DETECT = ()
MAGIC = b"RAWB"


def convert(data):
    if not isinstance(data, bytes):
        return []
    return [Symbol("head", int.from_bytes(data[4:8], "little"), 4, "data")]
"""

_TEXT_MAGIC_CONVERTER = """
FORMAT = "textmagic"
DESCRIPTION = "MAGIC must be bytes"
DETECT = ()
MAGIC = "RAWB"


def convert(data):
    return []
"""


class TestBinaryConverters:
    def test_a_plugin_with_magic_reads_bytes_and_is_sniffed_by_them(self, sym_env, tmp_path):
        """converter-magic-first: a plugin's signature selects it and hands it the bytes."""
        # Arrange
        engine, config_path, _ = sym_env
        _install_converter(config_path, _BINARY_CONVERTER)
        engine.fire_lifecycle("on_app_start")
        build = tmp_path / "build.bin"
        build.write_bytes(b"RAWB" + (0x1234).to_bytes(4, "little"))

        # Act
        result = engine.dispatch(f"sym.import {build}")

        # Assert
        assert result.success, result.error
        table = get_table(engine.ctx)
        assert table is not None and [(s.name, s.addr) for s in table.symbols] == [("head", 0x1234)]

    def test_magic_that_is_not_bytes_is_reported(self, sym_env):
        """MAGIC = "RAWB" (a str) is an authoring error, reported at load."""
        # Arrange
        engine, config_path, output = sym_env
        _install_converter(config_path, _TEXT_MAGIC_CONVERTER)

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        errors = [text for text in _texts(output) if "Plugin error" in text]
        assert any("textmagic" in text and "MAGIC" in text for text in errors), (
            "the report names the converter and the export"
        )


class TestBlockAliases:
    """A pointer constant in the build names a device block: the merged view carries the aliases."""

    def test_a_pointer_constant_names_the_blocks_registers(self, sym_env):
        """alias-from-pointer-constant: BUS -> the part's base; /sym resolves an alias, /sym.info lists the block."""
        # Arrange -- a sidecar holding one pointer constant to _PART's base
        engine, config_path, output = sym_env
        sidecar = config_path.parent / "sym" / f"rig{SYMBOLS_SUFFIX}"
        constant = {
            "name": "BUS",
            "addr": "0x3000",
            "size": 4,
            "section": "rodata",
            "type": "u32",
            "value": 0x50000000,
            "pointee": "struct",
        }
        sidecar.write_text(
            json.dumps({"symbols_version": 1, "symbols": [constant]}), encoding="utf-8"
        )
        _install_device(config_path.parent / "dev", "part", _PART)

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        table = get_table(engine.ctx)
        assert table is not None, "a table is loaded"
        assert [s.name for s in table.symbols if s.alias_of] == ["BUS_REG_A", "BUS_REG_B"]
        assert engine.dispatch("sym BUS_REG_B").value == "0x50000004", (
            "an alias resolves like the register it names"
        )
        engine.dispatch("sym BUS")
        assert any("BUS  [const 4 bytes]  -> 0x50000000" in line for line in _texts(output)), (
            "the pointer constant shows the address it holds"
        )
        info = engine.dispatch("sym.info")
        assert info.data["aliases"] == [
            {
                "name": "BUS",
                "block": "part",
                "device": "part",
                "base": 0x50000000,
                "base_hex": "0x50000000",
                "count": 2,
            }
        ], "the record names the block once, not per register"
        assert any("BUS -> part@0x50000000  (2 registers)" in line for line in _texts(output)), (
            "and the prose has the same row"
        )

    def test_a_skipped_alias_is_said_at_load(self, sym_env):
        """alias-from-pointer-constant: a name the build already owns keeps it, and the load says so."""
        # Arrange
        engine, config_path, output = sym_env
        sidecar = config_path.parent / "sym" / f"rig{SYMBOLS_SUFFIX}"
        symbols = [
            {
                "name": "BUS",
                "addr": "0x3000",
                "size": 4,
                "section": "rodata",
                "type": "u32",
                "value": 0x50000000,
                "pointee": "struct",
            },
            {"name": "BUS_REG_A", "addr": "0x1000", "size": 4, "section": "bss"},
        ]
        sidecar.write_text(json.dumps({"symbols_version": 1, "symbols": symbols}), encoding="utf-8")
        _install_device(config_path.parent / "dev", "part", _PART)

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        assert "Symbols: BUS: BUS_REG_A is taken; no alias for REG_A" in _texts(output)
        assert engine.dispatch("sym BUS_REG_A").value == "0x00001000", (
            "the build's own symbol stays"
        )
