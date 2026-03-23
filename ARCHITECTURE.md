# Termapy Architecture

## Core Idea

Termapy is built on its own plugin system. Built-in commands (`/help`, `/cfg`, `/grep`, `/proto`, etc.) are regular plugins loaded from `builtins/plugins/`. The same `Command` + `PluginContext` API that implements the core REPL is available to user plugins. Drop a `.py` file in a folder to add commands, override builtins, or build device-specific tools — no compilation or registration required.

## Module Structure

```text
src/termapy/
├── app.py               # (3150 lines) Textual TUI — UI, serial I/O, modals, app hooks
├── dialogs.py           # (890 lines)  Modal screens — config editor, pickers, confirm
├── proto_debug.py       # (1160 lines) Interactive protocol debug screen
├── protocol.py          # (1770 lines) Protocol parsing, format specs, CRC, visualizers
├── demo.py              # (910 lines)  Simulated device for --demo mode
├── repl.py              # (405 lines)  REPL engine — dispatch, scripting, history
├── plugins.py           # (484 lines)  Plugin system — Command, PluginContext, loading
├── config.py            # (406 lines)  Config dirs, loading, validation, migration trigger
├── port_control.py      # (230 lines)  Pure serial port control functions — no Textual
├── proto_runner.py      # (284 lines)  Protocol test script runner
├── scripting.py         # (154 lines)  Pure functions — templates, duration parsing
├── migration.py         # (130 lines)  Config schema migration chain (v1→v8)
├── defaults.py          # (114 lines)  DEFAULT_CFG, templates
├── help/                #              Markdown help pages (source for MkDocs)
├── html/                #              Generated HTML help (MkDocs Material output)
├── builtins/
│   ├── plugins/         #              20 built-in REPL command plugins
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
Serial I/O:     ctx.serial_write(), ctx.serial_read_raw(), ctx.serial_drain()
                 ctx.serial_wait_idle(), ctx.serial_io() (context manager)
                 ctx.is_connected()
Filesystem:      ctx.ss_dir, ctx.scripts_dir, ctx.proto_dir, ctx.cap_dir
Interaction:     ctx.confirm(), ctx.clear_screen()
Dispatch:        ctx.dispatch() — route a command through the full pipeline
Engine:          ctx.engine — internal/unstable API for built-ins
```

External plugins use `PluginContext` only. `EngineAPI` is internal and may change.

### Loading Order (later overrides earlier)

```text
1. builtins/plugins/         — 20 built-in commands (shipped with termapy)
2. termapy_cfg/plugins/      — user plugins (all configs on this machine)
3. termapy_cfg/<name>/plugins/ — per-config plugins (one config only)
4. App hooks (app.py)        — commands needing Textual access (connect, ss, run, port, etc.)
```

A user plugin with the same name as a built-in replaces it. App hooks override everything — they need direct access to Textual widgets and serial state.

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
│  │ port, ss, run, cfg.load, edit, help.open │    │
│  └──────────────────────────────────────────┘    │
│  ┌──────────────────────────────────────────┐    │
│  │ Serial I/O (pyserial)                    │    │
│  │ read_serial() — background thread        │    │
│  │ _raw_rx_queue — inter-thread byte queue  │    │
│  └──────────────────────────────────────────┘    │
├──────────────────────────────────────────────────┤
│  repl.py — ReplEngine                            │
│  • Command dispatch (dotted name → handler)      │
│  • Script runner (background thread)             │
│  • State: seq counters, echo, variables          │
│  • History management                            │
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

## Key Data Flows

### Serial Read (background thread)

```text
serial.read() → _raw_rx_queue
  → decode(encoding) → split on \n → batch lines
  → call_from_thread → RichLog.write(Text.from_ansi())
  → strip ANSI → log file
  (if capture active: raw bytes also written to capture file)
```

### Command Dispatch (user input or script)

```text
Input.on_submit → _execute_command()
  → split on \n (multi-command)
  → /raw? → send verbatim to serial (bypass everything)
  → run_directives() → rewrite? dispatch rewritten command
                      → warn/error? show message, stop
  → starts with prefix? → repl.dispatch(name, args)
      → apply REPL transforms (variable expansion, etc.)
      → lookup dotted name in plugin registry
      → call handler(ctx, args)
  → else → apply serial transforms → ser.write(cmd + line_ending)
  → optional echo to RichLog
```

### Binary Capture Flow

```text
/bin_cap → start_capture(path, mode, target_bytes, columns, ...)
  → serial read thread feeds raw bytes to capture buffer
  → on each record: apply format spec → write CSV row
  → on target reached: close file, show summary
  cmd= sends device trigger after capture starts + drain
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
│ (async)             │  dispatch, modals, button handlers
├─────────────────────┤
│ read_serial()       │  Long-lived background thread
│ @work(thread=True)  │  Reads serial data, posts to RichLog
├─────────────────────┤
│ _run_script()       │  Short-lived per script/command
│ @work(thread=True)  │  Blocking commands (/delay, /confirm)
│                     │  must run here, not on main thread
├─────────────────────┤
│ _auto_reconnect()   │  Short-lived, retries connection
│ _send_test()        │  Short-lived, protocol test case
│ _run_cmds()         │  Short-lived, setup/teardown commands
└─────────────────────┘
```

At most two workers run concurrently: the serial reader plus one command/script/test worker. `call_from_thread` posts UI updates back to the main thread.

## Built-in Plugins (20 files)

| Plugin      | Command            | Purpose                                           |
| ----------- | ------------------ | ------------------------------------------------- |
| bin_cap.py  | /bin_cap           | Binary data capture to CSV/file                   |
| cfg.py      | /cfg               | Config values, info, explore, per-folder file ops |
| cls.py      | /cls               | Clear terminal                                    |
| confirm.py  | /confirm           | Yes/Cancel dialog (scripts)                       |
| echo.py     | /echo              | Toggle command echo                               |
| env_var.py  | /env               | Environment variable management                   |
| eol.py      | /show_line_endings | Toggle line ending markers                        |
| exit.py     | /exit              | Quit the app                                      |
| grep.py     | /grep              | Search scrollback                                 |
| help.py     | /help              | Colorized command listing and help                |
| os_cmd.py   | /os                | Run shell commands                                |
| print.py    | /print             | Print to terminal                                 |
| proto.py    | /proto             | Binary protocol tools                             |
| seq.py      | /seq               | Sequence counters                                 |
| show.py     | /show              | Display files                                     |
| ss.py       | /ss                | Screenshots (placeholder, hooks override)         |
| stop.py     | /stop              | Abort running script                              |
| text_cap.py | /text_cap          | Timed text capture                                |
| timeit.py   | /timeit            | Time a command                                    |
| var.py      | /var               | User variables                                    |

## Test Coverage

14 test files, 733 tests covering non-UI layers:

| File                   | Covers                                      |
| ---------------------- | ------------------------------------------- |
| test_protocol.py       | Format specs, CRC, visualizers, diff        |
| test_engine.py         | ReplEngine dispatch, scripting, config      |
| test_app_config.py     | Config utilities, custom buttons, templates |
| test_scripting.py      | Template expansion, duration parsing        |
| test_plugins.py        | Plugin loading, context API                 |
| test_builtins.py       | Built-in command handlers                   |
| test_repl_cfg.py       | Config change mechanics                     |
| test_migration.py      | Config schema migration                     |
| test_demo.py           | Demo device simulation                      |
| test_var.py            | User variable system                        |
| test_env_var.py        | Environment variable commands               |
| test_port_control.py   | Serial port control pure functions          |
| test_proto_runner.py   | Protocol test runner                        |
| test_proto_send_crc.py | CRC in proto.send                           |

`app.py`, `proto_debug.py`, and `dialogs.py` are not unit tested — UI is tested manually.
