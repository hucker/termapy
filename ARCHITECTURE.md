# Termapy architecture

For how termapy was built (and the role LLM tooling played), see [On AI assistance](src/termapy/help/on-ai-assistance.md).

## Core idea

Termapy is built on its own plugin system. Built-in commands (`/help`, `/cfg`, `/grep`, `/proto`, etc.) are regular plugins loaded from `builtins/commands/`. The same `Command` + `PluginContext` API that implements the core REPL is available to user plugins. Drop a `.py` file in a folder to add commands, override builtins, or build device-specific tools. No compilation or registration required.

## Module structure

```text
src/termapy/
├── builtins/
│   ├── commands/           #              36 built-in REPL command plugins
│   ├── crc/                #              Built-in CRC plugins (sum8, sum16)
│   ├── demo/               #              Demo config, scripts, proto files, plugins
│   └── viz/                #              Built-in packet visualizers (hex, text)
├── dialogs/                # (2045 lines) Modal screens - one file per dialog
│   ├── _common.py          #   Shared CSS, dismiss bindings, port-row helper
│   ├── cfg_confirm.py      #   CfgConfirm
│   ├── config_editor.py    #   ConfigEditor - the cfg-dict editor (~461 lines, the big one)
│   ├── config_picker.py    #   ConfigPicker
│   ├── confirm_dialog.py   #   ConfirmDialog - Yes/Cancel
│   ├── name_picker.py      #   NamePicker
│   ├── port_picker.py      #   PortPicker - serial port selection
│   ├── proto_editor.py     #   ProtoEditor - .pro file editor
│   ├── proto_picker.py     #   ProtoPicker
│   ├── quick_setup.py      #   QuickSetup - first-run / new-config wizard
│   ├── script_editor.py    #   ScriptEditor - .run file editor
│   ├── script_picker.py    #   ScriptPicker
│   ├── update_available.py #   UpdateAvailableDialog
│   └── welcome_dialog.py   #   WelcomeDialog
├── help/                   #              Markdown help pages (source for HTML build)
├── html/                   #              Generated HTML help
├── mcp/                    # (1912 lines) MCP stdio server
│   ├── catalog.py          #   JSON catalog + device-state resources
│   ├── prompts.py          #   MCP prompts (draft_profile, etc.)
│   └── server.py           #   MCPHost - run_command, async events, lifecycle
├── plugins/                # (2235 lines) Plugin system - capability-handle architecture
│   ├── handles/            #   IOHandle, SerialHandle, FilesystemHandle, UIHandle, EngineHandle
│   ├── capabilities.py     #   CapabilitySet, MissingCapability
│   ├── command.py          #   Command, CmdResult, Transform, Directive
│   ├── context.py          #   PluginContext dataclass + ns/plugin_cfg/dispatch
│   ├── loader.py           #   Plugin discovery, COMMAND validation
│   └── output_levels.py    #   Silent/quiet/normal/verbose constants + ordering
├── profile/                # (1099 lines) v2 device-profile schema, loader, type registry
│   ├── loader.py           #   load/save/validate + Profile dataclass + transport apply
│   ├── matcher.py          #   match_profile_command, template_to_regex
│   ├── schema.json         #   Canonical JSON Schema (Draft 2020-12)
│   └── types.py            #   TypeRegistry, TypeDef (enum/range/pattern/format_spec/...)
├── protocol/               # (2783 lines) Binary-protocol toolkit (library-shaped, no Textual)
│   ├── core.py             #   Format-spec parser, apply_format, FrameCollector
│   ├── crc.py              #   CRC catalogue (64 algorithms) + registry
│   ├── crc_codegen.py      #   C / Python / Rust CRC codegen
│   ├── runner.py           #   .pro file execution
│   └── viz.py              #   Visualizer plugin loader
├── usb/                    # (3913 lines) USB lookup tables (library-shaped)
│   ├── _vendors_full.py    #   Generated USB-IF table (~3,400 entries, fallback)
│   ├── aliases.py          #   Manufacturer-string -> short display alias
│   ├── chips.py            #   (VID, PID) -> ChipInfo (model, speed, max baud)
│   └── vendors.py          #   VID -> canonical vendor name (curated short forms)
├── app.py                  # (3144 lines) Textual TUI - UI, modals, app hooks
├── capture.py              # (336 lines)  Capture state machine - text, binary, format spec
├── cli.py                  # (1042 lines) Plain-text CLI frontend - CLITerminal + _run_cli_mode
├── config.py               # (741 lines)  Config dirs, loading, validation, migration trigger
├── defaults.py             # (538 lines)  DEFAULT_CFG, templates, CONFIG_FIELD_HELP
├── demo.py                 # (1690 lines) Simulated device for --demo mode (FakeSerial)
├── demo_ndjson.py          # (379 lines)  NDJSON simulator variant (DEMO_JSON port)
├── entry.py                #              CLI argument parsing and mode dispatch (Textual-free)
├── help_dynamic.py         # (258 lines)  Reusable helpers for callable long_help
├── migration.py            # (451 lines)  Config schema migration chain (v17)
├── port_control.py         # (1452 lines) Pure serial port control functions - no Textual
├── proto_debug.py          # (1178 lines) Interactive protocol debug screen (Textual)
├── repl.py                 # (1748 lines) REPL engine - dispatch, scripting, transforms
├── scripting.py            # (278 lines)  Pure functions - templates, duration parsing, ANSI
├── serial_engine.py        # (566 lines)  Serial connection lifecycle, reader loop orchestrator
├── serial_port.py          # (306 lines)  Serial I/O wrapper + SerialReader data processor
└── terminal_host.py        # (649 lines)  Shared base for TUI and CLI - builds PluginContext
```

## The plugin system

The plugin system is the central abstraction. Everything flows through it.

### Command

A `Command` declares a REPL command: its name, args, help text, handler function, and optional subcommands:

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

Every handler receives a `PluginContext`, the stable API boundary between plugins and the app. The context is a thin shell over five **capability handles**, each owning one domain:

```text
ctx.cfg, ctx.config_path                      # plain config (read-only mapping + path)
ctx.io.result(), ctx.io.output(), ctx.io.status()        # level-gated text channels
ctx.io.result_markup(), ctx.io.output_markup(),          # level-gated Rich-markup variants
    ctx.io.status_markup()
ctx.io.notify(), ctx.io.status_bar()          # always-works fallbacks (no capability gate)
ctx.io.log()                                  # session log
ctx.serial.is_connected, ctx.serial.port      # serial state
ctx.serial.write(), ctx.serial.read_raw()     # serial I/O primitives
ctx.serial.drain(), ctx.serial.wait_idle()
ctx.serial.io() (context manager)             # acquire serial for synchronous read
ctx.serial.rx_observer(), ctx.serial.tx_observer()  # passive byte taps
ctx.fs.ss_dir, ctx.fs.scripts_dir, ctx.fs.proto_dir, ctx.fs.cap_dir
ctx.fs.open_file()                            # gated by gui_apps capability
ctx.ui.confirm(), ctx.ui.notify()             # TUI-strict; raise MissingCapability in CLI
ctx.ui.clear_screen(), ctx.ui.exit_app(), ctx.ui.screenshot()
ctx.engine                                    # internal/unstable SPI for built-ins
ctx.dispatch(cmd)                             # re-route a command through the pipeline
ctx.wait_for_match(predicate, timeout)        # block until serial matches (gated)
ctx.ns(name)                                  # session-scoped state dict (see below)
ctx.plugin_cfg(name)                          # per-plugin persistent config
```

**12 visible names** on `ctx`, down from ~50 flat fields. The split is by responsibility, not by syntax: each handle owns one capability domain a reader can hold in their head.

**Capability gating.** Some handle methods are gated on `CapabilitySet` flags. `ctx.ui.confirm()` requires `confirm_dialog`; `ctx.fs.open_file()` requires `gui_apps`; `ctx.wait_for_match()` requires `block_until`. Calling a gated method in an environment that didn't grant the capability raises `MissingCapability`, which the dispatcher converts to `CmdResult.fail`. Commands declare what they need with `Command(needs=CapabilitySet(...))`; the dispatcher refuses to invoke a handler whose `needs` aren't satisfied, so most capability mismatches fail loudly *before* the handler runs.

**Two-tier output.** `ctx.io.notify()` and `ctx.io.status_bar()` are the always-works fallbacks (toast in TUI, plain print in CLI). `ctx.ui.notify()` and `ctx.ui.status_bar()` are TUI-strict variants that require the matching capability. Plugin authors pick by intent: "just communicate" → `ctx.io`, "I need a real toast" → `ctx.ui`.

External plugins use `PluginContext` only. `ctx.engine` is the intentional escape hatch for built-ins that need internal SPI (capture, proto debug, modem transfers); its surface may change.

#### Namespaces (`ctx.ns()`)

`ctx.ns(name)` returns a session-scoped dict, created lazily on first access, shared across every call with the same name for the lifetime of the `PluginContext`. It is the supported way for both built-in and third-party plugins to keep per-session state - a sanctioned alternative to monkeypatching `ctx` or using module-level globals.

Namespaces are plain mutable `dict`s. They are not persisted (use `ctx.cfg` for that) and not isolated - any caller can read any namespace. The name is a collision-avoidance convention, not access control, which lets cooperating plugins share state on purpose (a "stats" plugin can walk every namespace and surface counters without the producers knowing it exists).

Built-ins use namespaces as worked examples of the pattern:

```text
ctx.ns("seq")              - sequence counters, mutated by {seqN+} template expansion
ctx.ns("target_commands")  - device commands imported via /include
ctx.ns("flags")            - engine-owned toggles: echo, verbose, hex_mode
```

The `flags` namespace is engine-reserved. Third-party plugins should use their own namespace name (conventionally the plugin name, e.g. `ctx.ns("myplugin")`). The engine's flag defaults are set once at context construction in `_build_context`; read sites access them with bare key lookups, so a missing key is a construction bug, not silent drift.

Contrast with `ctx.engine`: `EngineAPI` holds Textual, threading, and pyserial handles that genuinely cannot be generified. Anything that's just a dict or a flag lives in a namespace instead. Looking at the field list of each is the fastest way to see the distinction - `engine` is the escape hatch for privileged frontend state, `ns()` is the uniform state primitive for everything else.

#### Lifecycle hooks

Plugins that need setup, teardown, or per-script reset can export top-level lifecycle functions. There is no `Plugin` base class and no decorators - a plugin is a module that exports stuff, and lifecycle functions are just more stuff it can export.

```text
on_app_start(ctx)     - once after plugins load and ctx is wired, before first dispatch
on_app_stop(ctx)      - once during graceful shutdown (not guaranteed on crash)
on_script_start(ctx)  - when the outermost script begins (nested /run does NOT fire)
on_script_stop(ctx)   - when the outermost script ends, including on /stop or error
```

Script hooks fire only at the top level - nested `/run` inside a running script does not re-fire `on_script_start`. A plugin that clears state in `on_script_start` will not have its state wiped by inner scripts. Plugins that need per-file nesting can track depth themselves via `ctx.engine.in_script()`.

Hooks are stored in a flat list in load order (`ReplEngine._lifecycle_hooks`). `fire_lifecycle(name)` filters by name and calls matching handlers in registration order, catching exceptions per-hook so one bad plugin can't prevent later hooks from running. Errors surface through `ctx.status()`.

Example use: the `seq` plugin (below) owns its counter state in `ctx.ns("seq")` and wires `on_script_start` to clear it, so scripts start with a clean counter set without `ReplEngine` knowing anything about sequence counters. This is the pattern to follow for any plugin with session-scoped state that needs lifecycle management.

### Loading order (later overrides earlier)

```text
1. builtins/commands/         - 37 built-in commands (shipped with termapy)
2. termapy_cfg/plugins/      - user plugins (all configs on this machine)
3. termapy_cfg/<name>/plugins/ - per-config plugins (one config only)
4. App hooks (app.py/cli.py) - commands needing frontend access (ss, run, delay, etc.)
```

A user plugin with the same name as a built-in replaces it. App hooks override everything; they need direct access to frontend-specific features (Textual widgets in TUI, readline in CLI).

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

A `Directive` intercepts raw input lines **before** REPL/serial routing, before transforms, before prefix checking. Used for syntax that doesn't fit the `/command` pattern. Returns a `DirectiveResult` with an action (`rewrite`, `warn`, `error`, or `none`):

```python
DIRECTIVE = Directive(
    name="var_assign",
    help="Assign user variables with $(NAME) = value syntax.",
    pattern="$(NAME) = value",
    handler=_directive_var_assign,  # returns DirectiveResult
)
```

Currently the only directive is `var_assign` which rewrites `$(PORT) = COM7` into `var.set PORT COM7`. The directive system exists so this logic lives in the plugin rather than as a hardcoded special case in app.py.

### Plugin file convention

A plugin file may export any of: a `COMMAND`, a `TRANSFORM`, a `DIRECTIVE`, and/or top-level lifecycle functions (`on_app_start`, `on_app_stop`, `on_script_start`, `on_script_stop`). All are optional; the loader picks up whatever's there.

```python
def _handler(ctx: PluginContext, args: str) -> None:
    ctx.io.result("Hello!")

def on_app_start(ctx: PluginContext) -> None:
    ctx.ns("hello")["greeting"] = "Hello!"

# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(name="hello", args="{name}", help="Say hello.", handler=_handler)
```

"Must be at end of file" means after all handler functions it references.

There is deliberately no `Plugin` base class. A plugin is a module that exports stuff; the loader finds what's there. This keeps the mental model one sentence long and avoids the inheritance, decorator, and metaclass traps that creep into most plugin systems. If a plugin needs internal organization, it can use a class *inside* the module - the module boundary is the plugin boundary.

## Layer diagram

```text
┌──────────────────────────────────────────────────┐
│  app.py - Textual App                            │
│  ┌─────────────┐ ┌──────────┐ ┌──────────────┐   │
│  │ Title Bar   │ │ RichLog  │ │ Bottom Bar   │   │
│  │ (?,#,Cfg,   │ │ (serial  │ │ (Input, SS,  │   │
│  │  Port,      │ │  output) │ │  Scripts,Cap,│   │
│  │  Status)    │ │          │ │  Proto,Exit) │   │
│  └─────────────┘ └──────────┘ └──────────────┘   │
│  ┌──────────────────────────────────────────┐    │
│  │ dialogs/ - Modal Screens (per-file pkg)  │    │
│  │ ConfigPicker, ConfigEditor, PortPicker,  │    │
│  │ ScriptPicker, NamePicker, ConfirmDialog  │    │
│  └──────────────────────────────────────────┘    │
│  ┌──────────────────────────────────────────┐    │
│  │ proto_debug.py - Proto Debug Screen      │    │
│  │ Interactive send/expect with visualizers │    │
│  └──────────────────────────────────────────┘    │
│  ┌──────────────────────────────────────────┐    │
│  │ App Hooks - commands needing Textual     │    │
│  │ ss, run, delay, cfg.load, edit, help.open│    │
│  └──────────────────────────────────────────┘    │
├──────────────────────────────────────────────────┤
│  serial_engine.py - SerialEngine                 │
│  • Owns SerialPort, SerialReader, CaptureEngine  │
│  • connect() / disconnect() / read_loop()        │
│  • Callback-driven - no Textual dependency       │
├──────────────────────────────────────────────────┤
│  serial_port.py - SerialPort + SerialReader      │
│  • SerialPort: write, read_raw, drain, idle wait │
│  • SerialReader: bytes → lines, EOL, ANSI, clear │
│  • Works with real serial.Serial or FakeSerial   │
├──────────────────────────────────────────────────┤
│  capture.py - CaptureEngine                      │
│  • start/stop/feed_bytes/feed_text/get_progress  │
│  • Format spec decoding, CSV writing, echo       │
│  • No Textual dependency - fully testable        │
├──────────────────────────────────────────────────┤
│  repl.py - ReplEngine                            │
│  • dispatch_full() - full command routing        │
│  • dispatch() - REPL command → plugin handler    │
│  • Script runner with nested /run support        │
│  • fire_lifecycle() - run on_*_start/stop hooks  │
├──────────────────────────────────────────────────┤
│  plugins/ - Plugin System (package)              │
│  • command.py: Command, CmdResult, Transform,    │
│      Directive, DirectiveResult, LifecycleHook   │
│  • context.py: PluginContext + ns/plugin_cfg/    │
│      dispatch/wait_for_match                     │
│  • capabilities.py: CapabilitySet,               │
│      MissingCapability                           │
│  • handles/{io,serial,fs,ui,engine}.py:          │
│      capability-domain handles attached to ctx   │
│  • loader.py: load_plugins_from_dir, validation  │
│  • output_levels.py: silent/quiet/normal/verbose │
├──────────────────────────────────────────────────┤
│  protocol/ - Protocol Engine (package)           │
│  • core.py: format-spec language (H, U, I, S,    │
│    F, B, CRC), apply_format, FrameCollector      │
│  • crc.py: 64 CRC algorithms + plugin loading    │
│  • crc_codegen.py: C / Python / Rust codegen     │
│  • runner.py: .pro test-script execution         │
│  • viz.py: visualizer plugin loader              │
├──────────────────────────────────────────────────┤
│  config.py         - dirs, loading, validation   │
│  defaults.py       - DEFAULT_CFG, templates      │
│  migration.py      - schema migration v1→v17     │
│  scripting.py      - pure functions, no state    │
│  demo.py           - simulated device for --demo │
│  mcp/              - MCP stdio server (sibling)  │
│  profile/          - v2 profile schema + loader  │
│  usb/              - USB lookup tables           │
└──────────────────────────────────────────────────┘
```

## CLI mode (`cli.py`)

`termapy --cli` runs a plain-text terminal without Textual. It shares the same `ReplEngine`, `SerialEngine`, `PluginContext`, and all built-in plugins. The difference is how the frontend wires `PluginContext` callbacks:

| Callback             | TUI (app.py)                 | CLI (cli.py)                  |
| -------------------- | ---------------------------- | ----------------------------- |
| `ctx.io._write()`    | `RichLog.write(Text(...))`   | `Rich Console.print()`        |
| `ctx.ui.confirm()`   | Modal dialog + `event.wait()`| `input()` prompt              |
| `ctx.fs.open_file()` | `open_with_system()`         | `open_with_system()`          |
| `ctx.serial.port`    | `self.ser` (via SerialEngine)| `engine.serial_port.port`     |
| `/delay`             | `set_timer()` (non-blocking) | `time.sleep()` + progress bar |

CLI-specific features: readline tab completion, shared command history, `/color on|off` toggle. CLI limitations: no `/grep` (no scrollback buffer), no `/edit.cfg` (no config editor modal).

## Key data flows

### Serial read (background thread)

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

### Command dispatch (user input or script)

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

### Binary capture flow

```text
/cap.struct → CaptureEngine.start(path, mode, target, columns, ...)
  → SerialReader feeds bytes via CaptureEngine.feed_bytes()
  → on each record: apply format spec → write CSV row
  → on target reached: CaptureEngine.stop() → CaptureResult
  cmd= sends device trigger after capture starts + drain
```

### Script execution

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

## Config and filesystem

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

## Threading model

```text
┌─────────────────────┐
│ Main thread         │  Textual event loop - all UI updates
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

## Built-in plugins (36 files)

| Plugin             | Command       | Purpose                                            |
| ------------------ | ------------- | -------------------------------------------------- |
| app.py             | /app          | App-wide state and config (state.json, config.json)|
| cap.py             | /cap          | Unified data capture (text, bin, struct, hex)      |
| cfg.py             | /cfg          | Config values, info, explore, per-folder file ops  |
| cls.py             | /cls          | Clear terminal                                     |
| confirm.py         | /confirm      | Yes/Cancel dialog (scripts)                        |
| credits.py         | /credits      | Third-party attributions                           |
| echo.py            | /echo         | Toggle command echo                                |
| edit.py            | /edit         | Open project files (scripts, proto, plugins, cfg)  |
| env_var.py         | /env          | Environment variable management                    |
| eol.py             | /eol          | Toggle line ending markers                         |
| exit.py            | /exit         | Quit the app                                       |
| grep.py            | /grep         | Search scrollback (TUI only)                       |
| help.py            | /help         | Colorized command listing and help                 |
| line_no.py         | /line_no      | Toggle line numbers in terminal output             |
| mcp.py             | /mcp          | MCP catalog and status                             |
| os_cmd.py          | /os           | Run shell commands                                 |
| ping.py            | /ping         | Send command, measure response time                |
| plugin_folder.py   | /plugin       | Plugin-folder tools (list, explore, show, dump)    |
| port.py            | /port         | Serial port control                                |
| print.py           | /print        | Print to terminal                                  |
| profile_cmd.py     | /profile      | Device profile commands (MCP profile schema)       |
| proto.py           | /proto        | Binary protocol tools                              |
| repeat.py          | /repeat       | Repeat a command N times with optional delay       |
| run_edit.py        | /run.edit     | Open .run scripts in system editor                 |
| search.py          | /search       | Deep-search every command's metadata               |
| seq.py             | /seq          | Sequence counters                                  |
| show.py            | /show         | Display files                                      |
| ss.py              | /ss           | Screenshots (TUI only, stub in CLI)                |
| stop.py            | /stop         | Abort running script                               |
| term.py            | /term         | Terminal display / session toggles                 |
| var.py             | /var          | User variables                                     |
| ver.py             | /ver          | Show termapy version                               |
| verbose.py         | /verbose      | Output-level toggle (silent/quiet/normal/verbose)  |
| xfer.py            | /xfer         | File transfer settings                             |
| xmodem_xfer.py     | /xmodem       | XMODEM file transfer                               |
| ymodem_xfer.py     | /ymodem       | YMODEM file transfer (batch, 1K blocks)            |

## Test coverage

87 test files, 2561 tests:

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
| test_vfs.py            | Demo VFS: file list, info, delete, isolation   |
| test_xmodem.py         | XMODEM transfer, QueueByteReader, FakeSerial   |
| test_crc_builtins.py   | sum8/sum16 checksum modules                    |
| test_ymodem.py         | YMODEM transfer, batch send, FakeSerial        |

`app.py`, `proto_debug.py`, and `dialogs/` are not unit tested; UI is tested manually. The serial engine, capture, reader, and dispatch layers are fully testable using `FakeSerial`.
