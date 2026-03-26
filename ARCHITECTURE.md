# Termapy Architecture

## Core Idea

Termapy is built on its own plugin system. Built-in commands (`/help`, `/cfg`, `/grep`, `/proto`, etc.) are regular plugins loaded from `builtins/plugins/`. The same `Command` + `PluginContext` API that implements the core REPL is available to user plugins. Drop a `.py` file in a folder to add commands, override builtins, or build device-specific tools — no compilation or registration required.

## Module Structure

```text
src/termapy/
├── app.py               # (3126 lines) Textual TUI — UI, modals, app hooks
├── cli.py               # (581 lines)  Plain-text CLI frontend — CLITerminal class
├── serial_engine.py     # (215 lines)  Serial connection lifecycle, reader loop orchestrator
├── serial_port.py       # (280 lines)  Serial I/O wrapper + SerialReader data processor
├── capture.py           # (336 lines)  Capture state machine — text, binary, format spec
├── dialogs.py           # (1227 lines) Modal screens — config editor, pickers, confirm
├── proto_debug.py       # (1161 lines) Interactive protocol debug screen
├── protocol.py          # (1770 lines) Protocol parsing, format specs, CRC, visualizers
├── demo.py              # (896 lines)  Simulated device for --demo mode (FakeSerial)
├── repl.py              # (661 lines)  REPL engine — dispatch, scripting, transforms
├── plugins.py           # (572 lines)  Plugin system — Command, PluginContext, loading
├── config.py            # (440 lines)  Config dirs, loading, validation, migration trigger
├── port_control.py      # (247 lines)  Pure serial port control functions — no Textual
├── proto_runner.py      # (284 lines)  Protocol test script runner
├── scripting.py         # (136 lines)  Pure functions — templates, duration parsing, ANSI
├── migration.py         # (130 lines)  Config schema migration chain (v1->v8)
├── defaults.py          # (377 lines)  DEFAULT_CFG, templates
├── help/                #              Markdown help pages (source for MkDocs)
├── html/                #              Generated HTML help (MkDocs Material output)
├── builtins/
│   ├── plugins/         #              22 built-in REPL command plugins
│   ├── viz/             #              Built-in packet visualizers (hex, text)
│   ├── crc/             #              Built-in CRC plugins (sum8, sum16)
│   └── demo/            #              Demo config, scripts, proto files, plugins
└── help.md              #              Legacy single-page help (bundled)
```

## The Plugin System

The plugin system is the central abstraction. Everything flows through it.

### Command

A `Command` declares a REPL command — its name, args, help text, handler function, and optional subcommands:

```python
COMMAND = Command(
    name="cfg",
    args="{key {value}}",
    help="Show or change config values.",
    handler=_handler,
    sub_commands={
        "auto": Command(args="<key> <value>", help="Set immediately.", handler=_handler_auto),
        "configs": Command(help="List all config files.", handler=_handler_configs),
        "ss": Command(help="List ss/ files.", handler=_handler_ss,
            sub_commands={
                "explore": Command(help="Open ss/ in explorer.", handler=...),
                "clear": Command(help="Delete all ss/ files.", handler=...),
            }),
    },
)
```

The subcommand tree is flattened at registration into dotted names (`cfg.auto`, `cfg.ss.explore`) that the dispatch system looks up directly. The `/help` command walks the tree to show hierarchical output.

### PluginContext

Every handler receives a `PluginContext` — the stable API boundary between plugins and the app:

```text
Output:          ctx.write(), ctx.write_markup(), ctx.notify()
Config:          ctx.cfg, ctx.config_path
Serial port:     ctx.port() — raw pyserial object (or None)
                 ctx.is_connected()
Serial I/O:     ctx.serial_write(), ctx.serial_read_raw(), ctx.serial_drain()
                 ctx.serial_wait_idle(), ctx.serial_io() (context manager)
Filesystem:      ctx.ss_dir, ctx.scripts_dir, ctx.proto_dir, ctx.cap_dir
Interaction:     ctx.confirm(), ctx.clear_screen(), ctx.open_file()
Dispatch:        ctx.dispatch() — route a command through the full pipeline
Engine:          ctx.engine — internal/unstable API for built-ins
```

External plugins use `PluginContext` only. `EngineAPI` is internal and may change.

### Loading Order (later overrides earlier)

```text
1. builtins/plugins/         — 22 built-in commands (shipped with termapy)
2. termapy_cfg/plugins/      — user plugins (all configs on this machine)
3. termapy_cfg/<name>/plugins/ — per-config plugins (one config only)
4. App hooks (app.py/cli.py) — commands needing frontend access (ss, run, delay, etc.)
```

A user plugin with the same name as a built-in replaces it. App hooks override everything — they need direct access to frontend-specific features (Textual widgets in TUI, readline in CLI).

### Transforms

A `Transform` rewrites command text after the REPL/serial routing decision. Separate chains for REPL commands and serial commands. Used by the `var` plugin to expand `$(NAME)` placeholders and by `env` to expand `$(env.NAME)`:

```python
TRANSFORM = Transform(
    name="var",
    help="Expand $(NAME) placeholders from user-defined variables.",
    repl=expand_vars,
    serial=expand_vars,
)
```

### Directives

A `Directive` intercepts raw input lines **before** REPL/serial routing — before transforms, before prefix checking. Used for syntax that doesn't fit the `/command` pattern. Returns a `DirectiveResult` with an action (`rewrite`, `warn`, `error`, or `none`):

```python
DIRECTIVE = Directive(
    name="var_assign",
    help="Assign user variables with $(NAME) = value syntax.",
    pattern="$(NAME) = value",
    handler=_directive_var_assign,  # returns DirectiveResult
)
```

Currently the only directive is `var_assign` which rewrites `$(PORT) = COM7` into `var.set PORT COM7`. The directive system exists so this logic lives in the plugin rather than as a hardcoded special case in app.py.

### Plugin File Convention

A plugin file exports `COMMAND`, `TRANSFORM`, and/or `DIRECTIVE` at module level:

```python
def _handler(ctx: PluginContext, args: str) -> None:
    ctx.write("Hello!")

# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(name="hello", args="{name}", help="Say hello.", handler=_handler)
```

"Must be at end of file" means after all handler functions it references.

## Layer Diagram

```text
┌──────────────────────────────────────────────────┐
│  app.py — Textual App                            │
│  ┌─────────────┐ ┌──────────┐ ┌──────────────┐   │
│  │ Title Bar   │ │ RichLog  │ │ Bottom Bar   │   │
│  │ (?,#,Cfg,   │ │ (serial  │ │ (Input, SS,  │   │
│  │  Port,      │ │  output) │ │  Scripts,Cap,│   │
│  │  Status)    │ │          │ │  Proto,Exit) │   │
│  └─────────────┘ └──────────┘ └──────────────┘   │
│  ┌──────────────────────────────────────────┐    │
│  │ dialogs.py — Modal Screens               │    │
│  │ ConfigPicker, ConfigEditor, PortPicker,  │    │
│  │ ScriptPicker, NamePicker, ConfirmDialog  │    │
│  └──────────────────────────────────────────┘    │
│  ┌──────────────────────────────────────────┐    │
│  │ proto_debug.py — Proto Debug Screen      │    │
│  │ Interactive send/expect with visualizers │    │
│  └──────────────────────────────────────────┘    │
│  ┌──────────────────────────────────────────┐    │
│  │ App Hooks — commands needing Textual     │    │
│  │ ss, run, delay, cfg.load, edit, help.open│    │
│  └──────────────────────────────────────────┘    │
├──────────────────────────────────────────────────┤
│  serial_engine.py — SerialEngine                 │
│  • Owns SerialPort, SerialReader, CaptureEngine  │
│  • connect() / disconnect() / read_loop()        │
│  • Callback-driven — no Textual dependency       │
├──────────────────────────────────────────────────┤
│  serial_port.py — SerialPort + SerialReader      │
│  • SerialPort: write, read_raw, drain, idle wait │
│  • SerialReader: bytes → lines, EOL, ANSI, clear │
│  • Works with real serial.Serial or FakeSerial   │
├──────────────────────────────────────────────────┤
│  capture.py — CaptureEngine                      │
│  • start/stop/feed_bytes/feed_text/get_progress  │
│  • Format spec decoding, CSV writing, echo       │
│  • No Textual dependency — fully testable        │
├──────────────────────────────────────────────────┤
│  repl.py — ReplEngine                            │
│  • dispatch_full() — full command routing        │
│  • dispatch() — REPL command → plugin handler    │
│  • Script runner with nested /run support        │
│  • State: seq counters, echo, variables          │
├──────────────────────────────────────────────────┤
│  plugins.py — Plugin System                      │
│  • Command — declares name, args, handler, subs  │
│  • Transform — post-routing text rewriters       │
│  • Directive / DirectiveResult — pre-routing     │
│  • PluginContext — stable API for all plugins    │
│  • PluginInfo — flattened metadata + handler     │
│  • EngineAPI — internal API for built-ins        │
│  • load_plugins_from_dir() — file discovery      │
├──────────────────────────────────────────────────┤
│  protocol.py — Protocol Engine                   │
│  • Format spec language (H, U, I, S, F, B, CRC)  │
│  • ProtoScript / TestCase — test data model      │
│  • 62 CRC algorithms + plugin CRC loading        │
│  • Visualizer loading and column rendering       │
│  • diff_bytes() / diff_columns() — comparison    │
├──────────────────────────────────────────────────┤
│  config.py         — dirs, loading, validation   │
│  defaults.py       — DEFAULT_CFG, templates      │
│  migration.py      — schema migration v1→v8      │
│  scripting.py      — pure functions, no state    │
│  demo.py           — simulated device for --demo │
│  proto_runner.py   — protocol test execution     │
└──────────────────────────────────────────────────┘
```

## CLI Mode (`cli.py`)

`termapy --cli` runs a plain-text terminal without Textual. It shares the same `ReplEngine`, `SerialEngine`, `PluginContext`, and all built-in plugins. The difference is how the frontend wires `PluginContext` callbacks:

| Callback          | TUI (app.py)                 | CLI (cli.py)                  |
| ----------------- | ---------------------------- | ----------------------------- |
| `ctx.write()`     | `RichLog.write(Text(...))`   | `Rich Console.print()`        |
| `ctx.confirm()`   | Modal dialog + `event.wait()`| `input()` prompt              |
| `ctx.open_file()` | `open_with_system()`         | `open_with_system()`          |
| `ctx.port()`      | `self.ser` (via SerialEngine)| `engine.serial_port.port`     |
| `/delay`          | `set_timer()` (non-blocking) | `time.sleep()` + progress bar |

CLI-specific features: readline tab completion, shared command history, `/color on|off` toggle. CLI limitations: no `/grep` (no scrollback buffer), no `/edit.cfg` (no config editor modal).

## Key Data Flows

### Serial Read (background thread)

```text
SerialEngine.read_loop() [background thread]
  → serial.read() → rx_queue.put(data)
  → SerialReader.process(data) → ReaderResult
    → binary capture active? → CaptureEngine.feed_bytes() → skip display
    → proto_active? → suppress display
    → decode(encoding) → split on \n → batch lines
  → callbacks: on_lines → call_from_thread → RichLog
               on_clear → clear screen
               on_capture_done → stop capture
               on_error → status message
```

### Command Dispatch (user input or script)

```text
Input.on_submit → _execute_command()
  → split on \n (multi-command)
  → _dispatch_single() → repl.dispatch_full()
    → /raw? → serial_write_raw (bypass everything)
    → run_directives() → rewrite/warn/error
    → starts with prefix? → apply transforms → repl.dispatch()
      → lookup dotted name → call handler(ctx, args)
    → else → apply serial transforms → serial_write(encoded bytes)
```

### Binary Capture Flow

```text
/cap.struct → CaptureEngine.start(path, mode, target, columns, ...)
  → SerialReader feeds bytes via CaptureEngine.feed_bytes()
  → on each record: apply format spec → write CSV row
  → on target reached: CaptureEngine.stop() → CaptureResult
  cmd= sends device trigger after capture starts + drain
```

### Script Execution

```text
/run script.run → _run_script [background thread]
  → post ScriptStarted → mount overlay
  → repl.run_script() processes lines:
    → /delay → Event.wait (stop-aware)
    → /run nested.run → inline recursive call (up to 5 deep)
    → /confirm → dialog via call_from_thread
    → other → dispatch callback → _dispatch_single
  → post ScriptProgress → update overlay label
  → post ScriptFinished → teardown overlay
  Input disabled during execution, Escape or Stop button aborts
```

## Config & Filesystem

```text
termapy_cfg/
├── plugins/              # user plugins (all configs)
└── <name>/
    ├── <name>.cfg        # JSON config file
    ├── <name>.log        # session log
    ├── <name>.md         # info report (from /cfg.info)
    ├── .cmd_history.txt  # command history
    ├── plugins/          # per-config plugins
    ├── ss/               # screenshots (SVG + TXT)
    ├── scripts/          # .run script files
    ├── proto/            # .pro protocol test scripts
    ├── viz/              # per-config packet visualizers
    └── cap/              # data capture output files
```

`cfg_data_dir()` auto-creates all subdirs on access. Old `captures/` folders are auto-renamed to `cap/`.

## Threading Model

```text
┌─────────────────────┐
│ Main thread         │  Textual event loop — all UI updates
│ (async)             │  dispatch, modals, button handlers,
│                     │  Message handlers (ScriptStarted, etc.)
├─────────────────────┤
│ _run_reader()       │  Long-lived background thread
│ @work(thread=True)  │  Calls SerialEngine.read_loop()
│                     │  Callbacks post to main via call_from_thread
├─────────────────────┤
│ _run_script()       │  Short-lived per script/command
│ @work(thread=True)  │  Blocking commands (/delay, /confirm)
│                     │  must run here, not on main thread
│                     │  Nested /run executes inline (same thread)
├─────────────────────┤
│ _auto_reconnect()   │  Short-lived, retries connection
│ _send_test()        │  Short-lived, protocol test case
│ _run_cmds()         │  Short-lived, setup/teardown commands
└─────────────────────┘
```

At most two workers run concurrently: the serial reader plus one command/script/test worker. `call_from_thread` posts UI updates back to the main thread. `post_message` is used for script lifecycle events (thread-safe).

## Built-in Plugins (22 files)

| Plugin       | Command            | Purpose                                           |
| ------------ | ------------------ | ------------------------------------------------- |
| cap.py       | /cap               | Unified data capture (text, bin, struct, hex)     |
| cfg.py       | /cfg               | Config values, info, explore, per-folder file ops |
| cls.py       | /cls               | Clear terminal                                    |
| confirm.py   | /confirm           | Yes/Cancel dialog (scripts)                       |
| echo.py      | /echo              | Toggle command echo                               |
| edit.py      | /edit              | Open project files (scripts, proto, plugins, cfg) |
| env_var.py   | /env               | Environment variable management                   |
| eol.py       | /show_line_endings | Toggle line ending markers                        |
| exit.py      | /exit              | Quit the app                                      |
| grep.py      | /grep              | Search scrollback (TUI only)                      |
| help.py      | /help              | Colorized command listing and help                |
| os_cmd.py    | /os                | Run shell commands                                |
| port.py      | /port              | Serial port control (17 subcommands)              |
| print.py     | /print             | Print to terminal                                 |
| proto.py     | /proto             | Binary protocol tools                             |
| run_edit.py  | /run.edit          | Open .run scripts in system editor                |
| seq.py       | /seq               | Sequence counters                                 |
| show.py      | /show              | Display files                                     |
| ss.py        | /ss                | Screenshots (TUI only, stub in CLI)               |
| stop.py      | /stop              | Abort running script                              |
| var.py       | /var               | User variables                                    |
| ver.py       | /ver               | Show termapy version                              |

## Test Coverage

19 test files, 854 tests, 60% overall coverage:

| File                   | Covers                                         |
| ---------------------- | ---------------------------------------------- |
| test_protocol.py       | Format specs, CRC, visualizers, diff           |
| test_engine.py         | ReplEngine dispatch, dispatch_full, scripting  |
| test_capture.py        | CaptureEngine lifecycle, text/bin/hex, progress|
| test_serial_port.py    | SerialPort I/O, SerialReader data processing   |
| test_serial_engine.py  | SerialEngine connect/disconnect, read_loop     |
| test_app_config.py     | Config utilities, custom buttons, templates    |
| test_scripting.py      | Template expansion, duration parsing           |
| test_plugins.py        | Plugin loading, context API                    |
| test_builtins.py       | Built-in command handlers                      |
| test_repl_cfg.py       | Config change mechanics                        |
| test_migration.py      | Config schema migration                        |
| test_demo.py           | Demo device simulation (FakeSerial)            |
| test_var.py            | User variable system                           |
| test_env_var.py        | Environment variable commands                  |
| test_port_control.py   | Serial port control pure functions             |
| test_proto_runner.py   | Protocol test runner                           |
| test_proto_send_crc.py | CRC in proto.send                              |
| test_resolve_config.py | Config resolution chain (16 tests)             |
| test_cli_gold.py       | CLI gold-standard integration test             |

`app.py`, `proto_debug.py`, and `dialogs.py` are not unit tested — UI is tested manually. The serial engine, capture, reader, and dispatch layers are fully testable using `FakeSerial`.
