# Termapy Architecture

## Module Structure

```
src/termapy/
├── __init__.py          # Entry point — exports run()
├── app.py               # (1960 lines) Textual TUI app, serial I/O, UI
├── repl.py              # (222 lines) REPL engine — command dispatch, scripting
├── plugins.py           # (140 lines) Plugin system — discovery, loading, context API
├── scripting.py         # (76 lines)  Pure functions — template expansion, duration parsing
├── help.md              # In-app help guide (bundled in pip installs)
└── builtins/            # Built-in REPL commands (10 plugins)
    ├── cfg.py           # !!cfg — view/change config
    ├── cfg_auto.py      # !!cfg_auto — set config without confirmation
    ├── echo.py          # !!echo — toggle command echo
    ├── help.py          # !!help — list commands
    ├── os_cmd.py        # !!os — run shell commands
    ├── print.py         # !!print — print to terminal
    ├── seq.py           # !!seq — sequence counters
    ├── show.py          # !!show — display files
    └── stop.py          # !!stop — abort scripts
```

## Layer Diagram

```
┌─────────────────────────────────────────────┐
│  app.py — Textual App (TermapyApp)          │
│  ┌────────────┐ ┌──────────┐ ┌───────────┐ │
│  │ Title Bar  │ │ RichLog  │ │ Bottom Bar│ │
│  │ (Cfg,Port, │ │ (output) │ │ (Input,   │ │
│  │  Status)   │ │          │ │  Buttons) │ │
│  └────────────┘ └──────────┘ └───────────┘ │
│  ┌────────────────────────────────────────┐ │
│  │ Modal Screens                          │ │
│  │ ConfigPicker, ScriptPicker,            │ │
│  │ ScriptEditor, JsonEditor, PortPicker   │ │
│  └────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────┐ │
│  │ Serial I/O (pyserial)                  │ │
│  │ read_serial() — background thread      │ │
│  │ open_serial() / _try_open_port()       │ │
│  └────────────────────────────────────────┘ │
├─────────────────────────────────────────────┤
│  repl.py — ReplEngine                       │
│  • Command dispatch (name → plugin handler) │
│  • Script runner (parse → execute lines)    │
│  • State: seq counters, echo, in_script     │
├─────────────────────────────────────────────┤
│  plugins.py — Plugin System                 │
│  • PluginContext — stable API for plugins   │
│  • PluginInfo — metadata + handler          │
│  • EngineAPI — internals for built-ins only │
│  • load_plugins_from_dir() — file discovery │
├─────────────────────────────────────────────┤
│  scripting.py — Pure Functions              │
│  • expand_template() — {seq}, {datetime}    │
│  • parse_duration() — "500ms" → 0.5         │
│  • parse_script_lines() — classify lines    │
└─────────────────────────────────────────────┘
```

## Key Data Flow

### Serial Read Path (background thread)

```
serial.read() → decode(encoding) → buffer
  → detect ANSI clear screen → clear RichLog
  → split on \n → batch lines
  → call_from_thread(_write_output_batch) → RichLog.write(Text.from_ansi())
  → call_from_thread(_write_log_batch) → strip ANSI → log file
```

### Command Send Path (user input)

```
Input.on_submit → _execute_command()
  → starts with prefix? → repl.dispatch() → plugin.handler(ctx, args)
  → else → ser.write(cmd + line_ending) → optional echo to RichLog
  → _wait_for_idle() between multi-line commands
```

### Plugin Loading Order (later overrides earlier)

```
1. builtins/                      — shipped with termapy
2. termapy_cfg/plugins/           — global (all configs)
3. termapy_cfg/<name>/plugins/    — per-config
4. App hooks                      — registered by app.py for Textual-coupled commands
                                    (connect, disconnect, port, ss_svg, ss_txt, etc.)
```

## Design Principles

- **app.py is the monolith** — all UI, serial I/O, config management, and modal screens live here. It's the only file that imports Textual.
- **plugins.py has zero dependencies** on Textual or pyserial — it's pure Python dataclasses and importlib.
- **scripting.py is pure functions** — no state, no I/O, fully testable.
- **repl.py bridges plugins and app** — owns command dispatch and script execution, but delegates UI actions back to app.py through `PluginContext` callbacks.
- **Plugin API boundary** — external plugins interact only through `PluginContext` (write, serial_write, cfg, etc.). `EngineAPI` exists for built-ins but is marked unstable.

## Config & Filesystem

```
termapy_cfg/
├── plugins/              # global plugins
└── <name>/
    ├── <name>.json       # config file
    ├── <name>.txt        # session log
    ├── .cmd_history.txt  # command history (last 10)
    ├── plugins/          # per-config plugins
    ├── ss/               # screenshots (SVG + TXT)
    └── scripts/          # .run script files
```

`cfg_data_dir()` auto-creates `plugins/`, `ss/`, `scripts/` subdirs whenever a config path is accessed, so the rest of the code can assume they exist.

## Test Coverage

7 test files covering the non-UI layers:

| File | Coverage |
|------|----------|
| `test_engine.py` | ReplEngine dispatch, scripting, config |
| `test_app_config.py` | Config utilities, custom buttons, templates |
| `test_scripting.py` | Template expansion, duration parsing, script parsing |
| `test_plugins.py` | Plugin loading, context API |
| `test_builtins.py` | Built-in command handlers |
| `test_repl_cfg.py` | Config change mechanics |
| `conftest.py` | Shared fixtures |

The UI layer (app.py modals, widget interactions) is not unit tested — it relies on manual testing.
