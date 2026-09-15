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

from termapy.folders import SYMBOLS_SUFFIX
from termapy.plugins import CapabilitySet, InternalHandle, IOHandle, PluginContext
from termapy.plugins.command import LifecycleHook
from termapy.repl import ReplEngine
from termapy.symbols import SYMBOLS_NS, get_table
from termapy.symbols.converters import FORMATS

DEMO_SYMBOLS = (
    Path(__file__).parent.parent / "src" / "termapy" / "builtins" / "demo" / f"demo{SYMBOLS_SUFFIX}"
)
XC32_MAP = Path(__file__).parent / "fixtures" / "maps" / "xc32_sample.map"
XC32_COUNT = 17  # symbols in the fixture (see test_symbols_xc32.py)
DEMO_COUNT = 13


def _build(tmp_path: Path, *, unconfined: bool = True, oneshot: bool = False):
    """A ReplEngine + PluginContext over ``<tmp>/rig/rig.cfg`` capturing output.

    Args:
        tmp_path: pytest temp folder; the cfg lives in ``rig/`` under it.
        unconfined: The ``filesystem_unconfined`` capability (False = MCP sandbox).
        oneshot: What ``ctx.is_oneshot()`` reports (True = ``--run`` / ``--exec``).
    """
    cfg = {
        "serial": {
            "port": "COM4", "baud_rate": 115200, "custom_baud": False, "byte_size": 8,
            "parity": "N", "stop_bits": 1, "flow_control": "none",
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


# ── Auto-load ───────────────────────────────────────────────────────────────


class TestAutoload:

    def test_sidecar_present_loads_on_app_start(self, sym_env):
        # Arrange
        engine, config_path, output = sym_env
        _install_sidecar(config_path)

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        table = get_table(engine.ctx)
        assert table is not None, "table installed in the namespace"
        assert len(table) == DEMO_COUNT, "every demo symbol loaded"
        assert (f"Loaded {DEMO_COUNT} symbols (rig{SYMBOLS_SUFFIX})", "dim") in output, (
            "interactive sessions see the load line on the output channel"
        )

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

    def test_oneshot_loads_silently(self, tmp_path):
        # Arrange -- --run / --exec stdout must carry only the user's output
        engine, config_path, output = _build(tmp_path, oneshot=True)
        _install_sidecar(config_path)

        # Act
        engine.fire_lifecycle("on_app_start")

        # Assert
        assert get_table(engine.ctx) is not None, "the table still loads"
        assert not any(text.startswith("Loaded ") for text in _texts(output)), "but says nothing"

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
        assert result.value == str(XC32_COUNT), "value is the count"
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
        assert result.data is not None and result.data["format"] == "xc32", "data carries the format"
        assert result.data["count"] == XC32_COUNT, "data carries the count"

    def test_explicit_format(self, sym_env):
        # Arrange
        engine, _, _ = sym_env

        # Act
        result = engine.dispatch(f"sym.import {XC32_MAP} format=xc32")

        # Assert
        assert result.success, result.error
        assert result.value == str(XC32_COUNT), "explicit format= works"

    def test_unknown_format_rejected_by_dispatcher(self, sym_env):
        # Arrange
        engine, _, _ = sym_env

        # Act
        result = engine.dispatch(f"sym.import {XC32_MAP} format=nope")

        # Assert
        assert not result.success, "enum param refuses an unknown format"
        assert "nope" in result.error and "xc32" in result.error, "the message lists the choices"

    def test_unrecognized_map(self, sym_env, tmp_path):
        # Arrange
        engine, _, _ = sym_env
        notes = tmp_path / "notes.txt"
        notes.write_text("just some notes\n", encoding="utf-8")

        # Act
        result = engine.dispatch(f"sym.import {notes}")

        # Assert
        expected = f"Unknown map format: notes.txt (formats: {', '.join(FORMATS)})"
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
        assert result.value == str(XC32_COUNT), "an in-folder name is found beside the cfg"
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
        assert result.value == str(XC32_COUNT), "the whole path binds; the keyword after it still parses"

    def test_missing_map(self, sym_env):
        # Arrange
        engine, _, _ = sym_env

        # Act
        result = engine.dispatch("sym.import nosuch.map")

        # Assert
        assert result.error == "Map file not found: nosuch.map"

    def test_no_config(self, sym_env):
        # Arrange
        engine, _, _ = sym_env
        engine.ctx.config_path = ""

        # Act
        result = engine.dispatch(f"sym.import {XC32_MAP}")

        # Assert
        assert result.error == "No config loaded."

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
        assert result.value == "1", "the new table replaces the old wholesale"
        doc = json.loads(sidecar.read_text(encoding="utf-8"))
        assert len(doc["symbols"]) == 1, "the file is overwritten"
        assert doc["source"] == str(small), "provenance follows the new import"


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
        assert result.value == str(DEMO_COUNT), "value is the count"
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

    def test_explicit_path(self, sym_env):
        # Arrange
        engine, _, output = sym_env

        # Act
        result = engine.dispatch(f"sym.load {DEMO_SYMBOLS}")

        # Assert
        assert result.success, result.error
        assert result.value == str(DEMO_COUNT), "an ad-hoc file loads"
        table = get_table(engine.ctx)
        assert table is not None, "installed"
        assert table.path == DEMO_SYMBOLS, "path recorded"
        # _display_path's out-of-folder branch: the full path, not a bare name.
        info = engine.dispatch("sym.info")
        rows = [text for text, color in output if color == "markup"]
        assert any(str(DEMO_SYMBOLS) in row for row in rows), "file row shows the full path out-of-folder"
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
        assert result.value == str(DEMO_COUNT), "a bare name resolves into sym/, not against the CWD"

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
        assert first.value == str(DEMO_COUNT), "value is the count that was unloaded"
        assert (f"Unloaded symbols ({DEMO_COUNT}).", "green") in output
        assert get_table(engine.ctx) is None, "namespace cleared"
        assert second.success, second.error
        assert second.value == "0", "a second unload is a no-op, not an error"
        assert ("No symbols loaded.", "yellow") in output
        assert (config_path.parent / "sym" / f"rig{SYMBOLS_SUFFIX}").exists(), "the file is untouched"


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
        aliased.write_text(json.dumps({
            "symbols_version": 1,
            "symbols": [
                {"name": "main", "addr": "0x2000", "size": 442, "section": "text"},
                {"name": "_start", "addr": "0x2000", "size": 0, "section": "global"},
            ],
        }), encoding="utf-8")
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
        assert engine.dispatch("sym gTemp").error == "No symbols loaded."

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

    def test_prose_branch(self, sym_env):
        # Arrange
        engine, config_path, output = sym_env
        _load(engine, config_path)

        # Act
        result = engine.dispatch("sym.search g*")

        # Assert
        assert result.value == "gTemp", "first match's name"
        assert result.data is None, "prose branch builds no records"
        lines = [text for text in _texts(output) if text.startswith("  0x0000100")]
        assert lines == [
            "  0x00001000  gTemp  [bss 2 bytes sensor.c]",
            "  0x00001002  gPressure  [bss 4 bytes sensor.c]",
            "  0x00001008  gFlags  [data 4 bytes main.c]",
        ], "one line per match, address order"

    def test_data_branch(self, sym_env):
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
        assert result.value == "gTemp", "value is unchanged by the data branch"
        assert result.data is not None and [rec["name"] for rec in result.data] == [
            "gTemp", "gPressure", "gFlags",
        ], "records in address order"
        assert len(output) == before, "the data branch prints nothing"

    def test_no_match(self, sym_env):
        # Arrange
        engine, config_path, output = sym_env
        _load(engine, config_path)

        # Act
        result = engine.dispatch("sym.search zzz")

        # Assert
        assert result.success, result.error
        assert result.value == "", "empty value = nothing matched"
        assert result.data == [], "empty record list, not None"
        assert ("No symbols matching 'zzz'", "yellow") in output

    def test_no_table(self, sym_env):
        # Arrange
        engine, _, _ = sym_env

        # Act / Assert
        assert engine.dispatch("sym.search main").error == "No symbols loaded."

    def test_bare_is_usage(self, sym_env):
        # Arrange
        engine, _, _ = sym_env

        # Act / Assert
        assert engine.dispatch("sym.search").error == "Usage: /sym.search <pattern>"


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
        assert result.value == str(DEMO_COUNT)
        assert result.data is not None and result.data["sections"] == table.stats()
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
        assert (
            "No symbols loaded.  /sym.import <map> or /sym.load {path} to load one.", "yellow",
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
        after = _texts(output)[len(before):]

        # Assert
        assert any("Current symbols = none" in text for text in before), "no table -> none"
        assert any(f"Current symbols = {DEMO_COUNT} (sym/rig{SYMBOLS_SUFFIX})" in text for text in after), (
            "loaded -> count and the config-relative sidecar path"
        )


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
        assert bare.value == str(DEMO_COUNT), "the sidecar is always in-sandbox"


class TestValueContract:

    @pytest.mark.parametrize(
        "line",
        [
            "sym main", "sym 1000", "sym.search g*", "sym.search zzz", "sym.info",
            "sym.unload", "sym.load",
        ],
    )
    def test_every_command_returns_a_str_value(self, sym_env, line):
        # Arrange
        engine, config_path, _ = sym_env
        _load(engine, config_path)

        # Act
        result = engine.dispatch(line)

        # Assert
        assert result.success, result.error
        assert isinstance(result.value, str), f"{line}: value must be a str for $(X) <- capture"
