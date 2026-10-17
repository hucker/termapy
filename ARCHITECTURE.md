# Termapy architecture

For how termapy was built (and the role LLM tooling played), see [On AI assistance](src/termapy/help/on-ai-assistance.md).

## Core idea

Termapy is built on its own plugin system. Built-in commands (`/help`, `/cfg`, `/grep`, `/proto`, etc.) are regular plugins loaded from `builtins/commands/`. The same `Command` + `PluginContext` API that implements the core REPL is available to user plugins. Drop a `.py` file in a folder to add commands, override builtins, or build device-specific tools. No compilation or registration required.

## Core concepts

The whole system fits in your head once these pieces click. Each is detailed in its own section below; this list is the map.

- **Engines under thin frontends** — the logic lives in engines every frontend shares through `TerminalHost`: `SerialEngine` (the port, its reader thread and capture) and `ReplEngine` (dispatch and scripts). The TUI, the CLI and the MCP server each subclass `TerminalHost` and are wiring around the engines. Textual is confined to an exact list of UI modules, and a test fails when a core module imports it.
- **The port is a pyserial object** — everything above `SerialPort` sees a duck-typed pyserial `Serial` and cannot tell real hardware from a pyserial URL (`loop://`, `socket://`, `rfc2217://`) or a `FakeSerial` demo device. Port discovery is layered (an injected fleet, then `TERMAPY_DEMO_FLEET`, then the hardware), which is how the port stack is tested without hardware.
- **One RX path, one TX path** — bytes become lines once, in `SerialReader`, and the display, the session log, `/expect` and capture get the same lines in the same order. Every byte sent leaves through `TerminalHost._serial_write`, so a failed write is reported and the log records only what went out.
- **Threading** — `SerialEngine` owns the reader thread, and its hand-off to the frontend never blocks. In the TUI every command runs on a worker thread, never Textual's main thread, and the output helpers move themselves to the main thread.
- **Plugin system** — almost every command is a plugin (a `.py` file exporting a `COMMAND`). The same API builds the built-ins and any user/device plugin, so command functionality is added by dropping a file, not editing the core.
- **Hooks** — the small set of commands that need live frontend internals (Textual widgets, the screenshot surface, modals) are registered by the host instead of shipped as plugins. Everything else is a plugin.
- **PluginContext** — the stable API every handler receives: five handles — four capability domains (`io`, `serial`, `fs`, `ui`) plus the `internal` escape hatch — covering config, serial, filesystem, output/input, and global session state.
- **Capabilities & frontend projections** — each frontend (TUI / CLI / MCP) advertises a `CapabilitySet`; commands declare what they `need`. The dispatcher gates calls, so the three frontends are *projections* of one engine through different capability sets and an unavailable command fails cleanly instead of mysteriously. A command missing only session state (no config, no open port, no profile or symbol table, a capture already running) gets the state as its answer, not "not available in this environment".
- **Dispatch pipeline** — every input line follows one ordered path: *directive* → *transform* → REPL-vs-serial routing → *capability gate* → *handler* → **CmdResult**. New behavior attaches to a stage rather than becoming a special case.
- **CmdResult & output levels** — every handler returns one uniform `CmdResult` (success / value / error / timing). The active output level (silent / quiet / normal / verbose) decides which channels actually render, so a single handler is correct from a quiet script to a verbose TUI to an MCP caller that wants only `value`. An answer is data plus a view (`CmdResult.table` / `.record`): dispatch prints the view unless the caller wants the data.
- **Three kinds of text, kept apart** — device text (which may carry ANSI), Rich markup and plain text each cross into the others one way. Device ANSI renders as color in the TUI and is stripped from what the session log, `/expect` and capture see; a value going into markup is escaped (`escape_markup`); a Textual widget takes literal text; agents and the log get `markup_to_plain`.
- **Variables** — `$(NAME)` placeholders, expanded during the transform stage from three sources: user-assigned (`/var`, `$(NAME) = ...`), the loaded config, and the environment (`$(env.NAME)`).
- **Lifecycle events** — plugins react to app start/stop and script start/stop, so session-scoped state sets up and tears down without the engine knowing about it.
- **The config folder is the unit of state** — each project folder has its own `termapy_cfg/`. A config is a folder whose data folders appear at the first write and are pruned when empty; a read never writes; a global layer at the root supplies shared plugins and device files.
- **Config migration** — every config carries a schema version and is auto-upgraded on load through a chain of small migrators, so schema and command changes ship without breaking existing user configs.

## Module structure

<!-- module-tree:start (generated by scripts/sync_architecture.py from the module docstrings -- do not edit by hand) -->

```text
src/termapy/
├── builtins/               #              The plugins termapy ships: commands, CRC algorithms, the demo config, packet visualizers
│   ├── commands/           #   The command plugins, one module per command family (private _* helpers omitted)
│   │   ├── app.py          #     inspect app-wide state and config
│   │   ├── cap.py          #     unified data capture - text, binary, struct, hex
│   │   ├── cfg.py          #     show or change config values, project info, file listings
│   │   ├── cls.py          #     clear the terminal screen
│   │   ├── confirm.py      #     show a Yes/Cancel confirmation dialog
│   │   ├── credits.py      #     /credits -- print the acknowledgments page
│   │   ├── delay.py        #     wait for a duration, the one /delay in every frontend
│   │   ├── demo.py         #     `/demo` -- switch to the built-in demo device
│   │   ├── dev.py          #     /dev.* -- device files: import a vendor register description
│   │   ├── edit.py         #     open project files for editing
│   │   ├── env.py          #     expand $(env.NAME) placeholders in CLI commands
│   │   ├── exit.py         #     exit the application
│   │   ├── expect.py       #     wait for a device line, the one /expect in every frontend
│   │   ├── find.py         #     navigate matches in the scrollback
│   │   ├── grep.py         #     search the scrollback for matching lines
│   │   ├── help.py         #     forgiving /help with man-page detail view
│   │   ├── log.py          #     `/log.*` -- session-log commands
│   │   ├── mcp.py          #     /mcp.* commands -- MCP catalog and status
│   │   ├── mem.py          #     /mem.* -- device memory over the MEM spec or a template
│   │   ├── os_cmd.py       #     run a shell command
│   │   ├── ping.py         #     measure serial response time
│   │   ├── plugin.py       #     /plugin folder operations
│   │   ├── port.py         #     serial port control - list, connect, configure, signals
│   │   ├── print.py        #     print plain or Rich markup text to the terminal
│   │   ├── profile.py      #     /profile.* commands
│   │   ├── proto.py        #     binary protocol send/expect testing
│   │   ├── raw.py          #     /raw -- send text to the device exactly as given
│   │   ├── repeat.py       #     repeat a command multiple times
│   │   ├── run.py          #     `/run` -- execute .run scripts from any host
│   │   ├── search.py       #     /search -- Google-style deep command search
│   │   ├── seq.py          #     show or reset sequence counters
│   │   ├── show.py         #     show file contents
│   │   ├── ss.py           #     screenshot commands (ss.svg, ss.txt + folder ops)
│   │   ├── stop.py         #     abort a running script
│   │   ├── sym.py          #     /sym.* -- symbol table lookup, import, search
│   │   ├── term.py         #     /term.* -- terminal display / session toggles
│   │   ├── var.py          #     user-defined variables with $(NAME) syntax
│   │   └── xfer.py         #     file-transfer commands (settings + XMODEM + YMODEM)
│   ├── crc/                #   Built-in CRC plugins: simple byte-sum checksums beside the crcglot catalog
│   ├── demo/               #   The demo config the --demo flag installs: its cfg, scripts, proto files, plugins and profiles
│   └── viz/                #   Built-in packet visualizers for the proto debug screen (hex, text)
├── devices/                #              Device files -- the registers of a memory-mapped part, as data termapy owns
│   └── converters/         #   Device converters -- a vendor's register description in, a device document out
│       └── svd.py          #     CMSIS-SVD converter -- ARM's System View Description into a device document
├── dialogs/                # (2879 lines) Modal dialog screens for termapy
│   ├── _common.py          #   Shared constants and helpers for the dialog submodules
│   ├── cfg_confirm.py      #   CfgConfirm -- confirms a config value change (/cfg <key> <value> in the TUI)
│   ├── config_editor.py    #   ConfigEditor -- edit a config's JSON, validated before it is saved
│   ├── config_picker.py    #   ConfigPicker -- pick a config to load or edit, or create a new one
│   ├── confirm_dialog.py   #   ConfirmDialog -- the generic Yes/Cancel prompt (/confirm, delete confirmations)
│   ├── filename_dialog.py  #   FilenameDialog -- one-line filename prompt
│   ├── port_picker.py      #   PortPicker -- pick a serial port from a list that follows USB plug and unplug
│   ├── proto_editor.py     #   ProtoEditor -- the .pro protocol-script editor, with TOML highlighting
│   ├── proto_picker.py     #   ProtoPicker -- pick a .pro script to run or edit, or create one
│   ├── quick_setup.py      #   QuickSetup -- the first-run / new-config wizard: name, port and baud rate on one screen
│   ├── script_editor.py    #   ScriptEditor -- the .run script editor
│   ├── script_picker.py    #   ScriptPicker -- pick a .run script to run or edit, or create one
│   ├── set_var_dialog.py   #   SetVarDialog -- two-field name/value prompt for /var.set
│   └── update_available.py #   UpdateAvailableDialog -- reports a newer termapy on PyPI (Info / OK)
├── help/                   #              Markdown help pages, the source of the HTML help
├── html/                   #              Generated HTML help, what the wheel ships (rebuilt at release)
├── mcp/                    # (2178 lines) Termapy MCP (Model Context Protocol) server package
│   ├── catalog.py          #   Build JSON catalog/state snapshots from a running PluginContext
│   ├── prompts.py          #   MCP prompts: user-invocable conversation recipes
│   └── server.py           #   MCP stdio server: MCPHost + run_command tool + resources
├── plugins/                # (3647 lines) Plugin system for termapy - discovery, loading, and context API
│   ├── handles/            #   The handles a PluginContext carries: io, serial, fs, ui, and the internal escape hatch
│   ├── capabilities.py     #   Capability model: what a command needs and what a frontend provides, checked before dispatch
│   ├── command.py          #   Command, transform, directive, and lifecycle dataclasses
│   ├── context.py          #   PluginContext, InternalHandle, and PluginConfig
│   ├── loader.py           #   Plugin discovery and loading
│   ├── output_levels.py    #   Output-level vocabulary shared between `PluginContext` and `IOHandle`
│   ├── params.py           #   Declarative command parameters -- parse/coerce/validate before the handler
│   └── views.py            #   Views: how a typed answer looks in prose, kept apart from the answer itself
├── profile/                # (1300 lines) Device-profile schema, loader, validator, and type registry
│   ├── loader.py           #   Device profile: declarative description of a serial device
│   ├── matcher.py          #   Match a typed command line against a profile's command catalog
│   ├── schema.json         #   The JSON Schema a device profile is validated against (Draft 2020-12)
│   └── types.py            #   Profile-local user-defined types for the v2 device profile schema
├── protocol/               # (2762 lines) Binary-protocol toolkit: format-spec parser, CRC catalog, .pro runner
│   ├── core.py             #   Binary protocol utilities - hex parsing, framing, pattern matching, script parsing
│   ├── crc.py              #   CRC plugin loading and registry (termapy-side)
│   ├── runner.py           #   Headless protocol test runner with JSON result output
│   ├── type_codes.py       #   The format-spec type codes -- one table, every consumer iterates it
│   └── viz.py              #   Visualizer plugin discovery and loading
├── symbols/                # (1850 lines) Symbol tables -- named addresses for a firmware build
│   ├── converters/         #   Symbol converters -- vendor map text in, termapy symbols out
│   │   └── xc32.py         #     Microchip XC32 (GNU ld) linker-map converter -- the only home of XC32 knowledge
│   ├── address.py          #   The address grammar -- what consumes a symbol table
│   ├── format.py           #   Prose renderers and their structured `data=` twins, side by side
│   ├── provenance.py       #   Is the loaded symbol table still the one the map would produce?
│   ├── session.py          #   Session state for the loaded symbol table, and the auto-load rule
│   └── table.py            #   The symbol model and termapy's one symbol-file format
├── templates/              #              Desktop-shortcut templates and icons for /cfg.icon (Windows, macOS, Linux)
├── usb/                    # (3911 lines) USB lookup tables for serial-port debugging tools
│   ├── _vendors_full.py    #   USB Vendor ID -> canonical vendor name (generated)
│   ├── aliases.py          #   USB manufacturer descriptor -> short display alias
│   ├── chips.py            #   USB-serial chip identification by (vendor_id, product_id)
│   └── vendors.py          #   USB Vendor ID -> silicon-vendor name lookup
├── vendor/                 #              Vendored, unmaintained dependencies: pyserial, xmodem, ymodem (licenses in LICENSES.md)
├── widgets/                #              Custom Textual widgets for termapy's TUI
│   ├── checkbox.py         #   StrongCheckbox -- a Checkbox with a clearer on/off glyph
│   ├── status_bar.py       #   The transient status line beside the REPL input
│   └── suggester.py        #   CommandSuggester -- input type-ahead from REPL commands + history
├── app.py                  # (3940 lines) Textual TUI frontend: the app, its panes and buttons, and the wiring around the engines
├── app_dirs.py             #              User-level application directories and files for termapy
├── app_hooks.py            #              TUI-specific REPL hooks registered against the app's ReplEngine
├── capture.py              # (476 lines)  Capture engine - file capture state machine (no UI dependency)
├── capture_view.py         #              TUI capture-progress view: start/stop, status bar, and progress widget
├── cli.py                  # (1109 lines) CLI serial terminal - no Textual dependency
├── cli_flags.py            #              Print-and-exit handlers for termapy's CLI flags
├── cli_prompt.py           #              The CLI's interactive prompt: prompt_toolkit history, completion and session
├── config.py               # (1270 lines) Config file management - paths, defaults, loading, and serial port setup
├── config_resolve.py       #              Config-path resolution helpers
├── converters.py           #              Converter registries -- vendor text in, termapy data out, one record type
├── credits_data.py         #              The dependency and attribution registry -- one table, every consumer iterates it
├── defaults.py             # (593 lines)  Default config values and file templates
├── demo.py                 # (2089 lines) Simulated serial device for demo mode
├── demo_ndjson.py          #              Simulated NDJSON serial device for `DEMO_JSON` port name
├── demo_vt100.py           #              Simulated VT100/ANSI serial device for the `DEMO_VT100` port name
├── entry.py                #              Termapy CLI entry point
├── env_flags.py            #              Security-policy env-var gates, read once at process start
├── folder_ops.py           #              Shared folder-operation handlers for per-config data folders
├── folders.py              #              Folder and file constants -- single source of truth
├── help_dynamic.py         #              Helpers for building dynamic `long_help` callables
├── help_tooltip.py         #              The Help-button tooltip: version status + open-source attributions
├── history_nav.py          #              Command-history browsing cursor and file merge for the REPL input
├── info_views.py           #              Read-only info views for the TUI
├── install_info.py         #              Detect how termapy was installed, for upgrade-command hints
├── legacy.py               #              Legacy-command forwarding helper
├── markup.py               #              Rich markup: the one home for turning text into markup and back
├── memory.py               # (862 lines)  Device memory access -- bytes in, bytes out, over an injected exchange
├── memory_views.py         # (298 lines)  Typed views over device bytes -- layer 3 of the memory design
├── migration.py            # (750 lines)  Config schema versioning and migration chain
├── palette_provider.py     #              Textual command-provider that surfaces termapy's PALETTE_CMDS
├── pickers.py              #              Modal-picker callbacks for the TUI
├── port_control.py         # (2041 lines) Pure functions for serial port control - no Textual, no pyserial imports
├── port_format.py          #              One-line-per-port formatting for serial-port tables
├── proto_debug.py          # (1193 lines) Interactive protocol debug screen
├── repl.py                 # (2652 lines) REPL engine for termapy - plugin-based command dispatch and scripting
├── request_response.py     #              Request/response I/O pipeline for synchronous device exchanges
├── response_parsers.py     #              Pure response parsers for the profile response formats
├── run_docstring.py        #              Parse a `.run` script file's leading docstring block
├── run_legacy.py           #              Implementation of /run.legacy -- scan a .run script for legacy commands
├── scripting.py            # (693 lines)  Template expansion, script parsing, and shared utilities for termapy
├── serial_engine.py        # (773 lines)  SerialEngine - orchestrates serial port, reader, and capture
├── serial_port.py          # (618 lines)  Serial port I/O wrapper and reader data processor
├── terminal_host.py        #              Base class for the TUI, CLI and MCP frontends: builds the PluginContext, owns the one TX path
├── title_bar.py            #              Title-bar rendering for the TUI App
├── tree_render.py          #              Render a 2-level file tree with box-drawing connectors
├── update_check.py         #              Background PyPI update check for termapy
├── usb_tree.py             #              Enumerate the whole USB tree, not just the ports that carry a UART
├── variables.py            #              Variable engine: the `$(NAME)` namespace, storage, and expansion
└── vt100.py                #              --vt100 passthrough mode: a raw, Textual-free serial terminal
```

<!-- module-tree:end -->

**Engine in core, command surface in `builtins/`.** This split recurs, and the test for it
is "who calls the engine". `port_control.py`, `port_format.py`, and `usb_tree.py` are
engines; `builtins/commands/port.py` is the command surface built on them, and
`cli_flags.py` calls the same engines directly for `--ports` / `--usb`, so they stay in
core rather than moving into the plugin. `variables.py` is the same shape: it owns the
`$(NAME)` namespace that every frontend registers into at startup and that `repl.py`
expands on the dispatch path, while `builtins/commands/var.py` is just `/var` and the
`$(NAME) = value` directive. `symbols/` <- `builtins/commands/sym.py` is the third example:
the symbol table, its file format, the address grammar and the auto-load rule are core
(`ReplEngine.fire_lifecycle` loads the sidecar), and `/sym.*` is the thin command surface.
`memory.py` <- `builtins/commands/mem.py` is the fourth: the wire dialect, chunking and
continuity are core, `/mem.*` wires them to `ctx.serial` and renders.
Infrastructure core needs does not live under `builtins/`, however natural the matching
command feels.

`usb/` is a different kind of thing: a dependency-free table package kept separable for a
possible standalone release, which is why live platform probing (winreg, cfgmgr32, sysfs)
lives in `usb_tree.py` and not in it.

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
        "list": Command(help="List all config files.", handler=_handler_list),
        "icon": Command(
            help="Create a desktop launcher.",
            handler=_handler_icon,
            sub_commands={
                "remove": Command(help="Delete the launcher.", handler=...),
                "list": Command(help="List every launcher.", handler=...),
            },
        ),
    },
)
```

The subcommand tree is flattened at registration into dotted names (`cfg.auto`, `cfg.icon.remove`) that the dispatch system looks up directly. The `/help` command walks the tree to show hierarchical output.

### Hooks

A **hook** is a command the host (`app.py` / `cli.py` / `mcp/server.py`) registers directly via `repl.register_hook(...)` instead of shipping as a plugin file. Hooks exist for the small set of commands that need *live frontend internals* a plugin can't reach through `PluginContext` — mounting a Textual overlay, grabbing the screenshot surface, driving a modal dialog. Examples: `ss.svg`, `vt100`, `help.open`. A frontend never registers a second handler for a command another frontend has: it changes how a command works through a host method or a handle field (`_switch_to_cfg_path`, `ctx.internal.edit_file`), so `/cfg.load` and the `/edit` family are one builtin each (registry `one-handler-per-command`).

Hooks and plugins share one registry and one dispatch path; a hook is just a late, host-supplied entry that can override a plugin of the same name (see [Loading order](#loading-order-later-overrides-earlier)). The rule of thumb: if a command can be expressed through `PluginContext`, it's a plugin (large, portable set); if it genuinely needs Textual/host state, it's a hook (small, frontend-bound set).

### PluginContext

Every handler receives a `PluginContext`, the stable API boundary between plugins and the app. The context is a thin shell over five **handles** — four capability domains, plus `internal`, the privileged escape hatch that deliberately isn't a domain:

- **`ctx.io`** — text in/out: the level-gated `result` / `output` / `status` channels (and their Rich-markup variants, where any text interpolated into the markup goes through `escape_markup`) plus the always-works `notify` / `status_bar` / `log` fallbacks.
- **`ctx.serial`** — the serial connection: state (`is_connected`), lifecycle (`connect`, `disconnect`, `update_port`, `apply_port_effects`), I/O primitives (`write`, `read_raw`, `drain`, `wait_idle`, `rx_queue`), and passive `rx` / `tx` byte observers.
- **`ctx.fs`** — the filesystem layer: the config-dir folders (`ss_dir`, `scripts_dir`, `proto_dir`, `cap_dir`) and `open_file()`.
- **`ctx.ui`** — TUI-strict actions (`confirm`, `notify`, `clear_screen`, `exit_app`, `screenshot`); these raise `MissingCapability` in non-TUI frontends (CLI, MCP).
- **`ctx.internal`** — the intentional escape hatch: an internal, unstable interface (`InternalHandle`) for built-ins that need privileged frontend state (Textual, threads, pyserial handles) that can't be generified.

The remaining members (`ctx.cfg`, `ctx.config_path`, `ctx.prefix`, `ctx.dispatch`, `ctx.ns`, `ctx.plugin_cfg`, `ctx.arg`, `ctx.flag`, `ctx.wait_for_match`) sit directly on the context. The member-by-member reference is the [PluginContext API reference](src/termapy/help/writing-plugins.md#plugincontext-api-reference) in the plugin-writing guide, which ships in the app and is the one home for it; this document stops at the shape.

**A small, fixed set of visible names** on `ctx`, replacing the earlier sprawl of flat fields. The split is by responsibility, not by syntax: four handles each own one capability domain a reader can hold in their head; the fifth, `internal`, is the deliberate exception — the privileged escape hatch for built-ins that isn't a domain.

**Capability gating.** Some handle methods are gated on `CapabilitySet` flags. `ctx.ui.confirm()` requires `confirm_dialog`; `ctx.fs.open_file()` requires `gui_apps`; `ctx.wait_for_match()` requires `block_until`. Calling a gated method in an environment that didn't grant the capability raises `MissingCapability`, which the dispatcher converts to `CmdResult.fail`. Commands declare what they need with `Command(needs=CapabilitySet(...))`; the dispatcher refuses to invoke a handler whose `needs` aren't satisfied, so most capability mismatches fail loudly *before* the handler runs. Six capabilities are dynamic, set per dispatch by the engine rather than by the environment: `config_loaded` while a config is loaded, `serial_connected` while a port is open, `profile_loaded` while a device profile is loaded, `symbols_loaded` while a symbol table has entries, `capture_idle` while no capture runs, and `block_until` inside a script. A command missing only session state gets the state as its answer (`No config loaded.`, `Not connected.`, `No profile loaded.`, ..., from `capabilities.STATE_MESSAGES`, the one place each is spelled) rather than an environment refusal, on every route to a handler, typed `/raw` included, and whatever asks whether a command can ever run somewhere (`/help`'s AVAILABLE row, the MCP catalog) treats the dynamic ones as attainable (`capabilities.attainable`).

**Two-tier output.** `ctx.io.notify()` and `ctx.io.status_bar()` are the always-works fallbacks (toast in TUI, plain print in CLI). `ctx.ui.notify()` and `ctx.ui.status_bar()` are TUI-strict variants that require the matching capability. Plugin authors pick by intent: "just communicate" → `ctx.io`, "I need a real toast" → `ctx.ui`.

External plugins use `PluginContext` only. `ctx.internal` is the intentional escape hatch for built-ins; its surface may change. It actually serves **two jobs**:

1. **Frontend escape hatch** — slots that genuinely need Textual or threads. `confirm_save_cfg`, for instance, pops a TUI confirm modal — and is `None` in CLI/MCP, so the same `/cfg key value` falls back to `apply_cfg` and applies directly. Capture, proto-debug, the picker screens, and threaded script runs are here too.
2. **Engine forwarders** — slots like `dispatch` / `apply_cfg` / `in_script` that just reach the live `ReplEngine`. They're *injected rather than imported* because `repl.py` imports the plugins package, so a plugin importing `repl` would be circular — there's no other way for a Textual-free, repl-free plugin to reach the running engine.

The genuinely pure work (e.g. the JSON config write behind `confirm_save_cfg`) stays in pure modules like `config.py`; only the frontend-coupled and engine-reachable orchestration lives on the handle.

What remains on `ctx.internal` is the irreducible core, not the dregs of an unfinished cleanup. Members that had a real home already left: pure data (`prefix` → the `ctx.prefix` property, config-value coercion → `scripting`) and serial lifecycle (`connect` / `disconnect` / `update_port` / `apply_port_effects` / `rx_queue` → `ctx.serial`). The escape-hatch slots that stay are *per-command TUI screens* — `confirm_save_cfg` (the `CfgConfirm` old→new dialog), `open_proto_debug`, `open_picker`, `update_find_bar`. Each is used by a single command and is a no-op outside the TUI; promoting them to `ctx.ui` would clutter that clean, general API with one-off methods, so they live on the explicitly-unstable handle instead. The engine forwarders likewise can't move without breaking the `plugins ↔ repl` import cycle — the most they could become is one typed engine reference, which is a larger change than the cleanup warranted.

#### Namespaces (`ctx.ns()`)

`ctx.ns(name)` returns a session-scoped dict, created lazily on first access, shared across every call with the same name for the lifetime of the `PluginContext`. It is the supported way for both built-in and third-party plugins to keep per-session state - a sanctioned alternative to monkeypatching `ctx` or using module-level globals.

Namespaces are plain mutable `dict`s. They are not persisted (use `ctx.cfg` for that) and not isolated - any caller can read any namespace. The name is a collision-avoidance convention, not access control, which lets cooperating plugins share state on purpose (a "stats" plugin can walk every namespace and surface counters without the producers knowing it exists).

Built-ins use namespaces as worked examples of the pattern:

```text
ctx.ns("seq")              - sequence counters, mutated by {seqN+} template expansion
ctx.ns("active_profile")   - the loaded device profile (set by /profile.load)
ctx.ns("symbols")          - the loaded symbol table (auto-loaded from sym/<cfg>.symbols.json; set by /sym.import, /sym.load)
ctx.ns("flags")            - engine-owned toggles: echo, echo_repl, color, output_level, hex
```

The `flags` namespace is engine-reserved. Third-party plugins should use their own namespace name (conventionally the plugin name, e.g. `ctx.ns("myplugin")`). The engine's flag defaults are set once at context construction in `_build_context`; read sites access them with bare key lookups, so a missing key is a construction bug, not silent drift.

Contrast with `ctx.internal`: `InternalHandle` holds Textual, threading, and pyserial handles that genuinely cannot be generified. Anything that's just a dict or a flag lives in a namespace instead. Looking at the field list of each is the fastest way to see the distinction - `ctx.internal` is the escape hatch for privileged frontend state, `ns()` is the uniform state primitive for everything else.

#### Lifecycle hooks

Plugins that need setup, teardown, or per-script reset can export top-level lifecycle functions. There is no `Plugin` base class and no decorators - a plugin is a module that exports stuff, and lifecycle functions are just more stuff it can export.

```text
on_app_start(ctx)     - once after plugins load and ctx is wired, before first dispatch
on_app_stop(ctx)      - once during graceful shutdown (not guaranteed on crash)
on_script_start(ctx)  - when the outermost script begins (nested /run does NOT fire)
on_script_stop(ctx)   - when the outermost script ends, including on /stop or error
```

Script hooks fire only at the top level - nested `/run` inside a running script does not re-fire `on_script_start`. A plugin that clears state in `on_script_start` will not have its state wiped by inner scripts. Plugins that need per-file nesting can track depth themselves via `ctx.internal.in_script()`.

Hooks are stored in a flat list in load order (`ReplEngine._lifecycle_hooks`). `fire_lifecycle(name)` filters by name and calls matching handlers in registration order, catching exceptions per-hook so one bad plugin can't prevent later hooks from running. Errors surface through `ctx.status()`. Three core listeners run inside `fire_lifecycle` BEFORE the plugin hooks: the empty data-folder prune (`folders.prune_empty_folders`, on `on_app_start`, `on_config_load` and `on_app_stop`; see "Config and filesystem"), then on `on_app_start` and `on_config_load` external plugin resolution (`ReplEngine.resolve_plugins`: drop every folder-loaded registration, then load `termapy_cfg/plugin/` and `termapy_cfg/<name>/plugin/` again, so a folder plugin's own hook fires in that same pass), then the symbol auto-load (`symbols.session.autoload`). So every frontend resolves plugins and loads `sym/<cfg>.symbols.json` at the same moment -- `on_app_start` fires BEFORE the startup auto-connect in the TUI, CLI and MCP server alike, so an `on_connect` hook or `on_connect_cmd` already has the table -- and a plugin's own hook already sees `ctx.ns("symbols")`. None of the three is a plugin hook (which would drift per frontend) or a `set_context` / `replace_cfg` side effect (which double-loads on the CLI/MCP config switch that rebuilds the ctx).

Example use: the `seq` plugin (below) owns its counter state in `ctx.ns("seq")` and wires `on_script_start` to clear it, so scripts start with a clean counter set without `ReplEngine` knowing anything about sequence counters. This is the pattern to follow for any plugin with session-scoped state that needs lifecycle management.

### Loading order (later overrides earlier)

```text
1. builtins/commands/        - built-in command plugins (one module per command family)
2. termapy_cfg/plugin/       - user plugins (all configs on this machine)
3. termapy_cfg/<name>/plugin/ - per-config plugins (one config only)
4. App hooks (app.py/cli.py) - commands needing frontend access (ss.svg, vt100, etc.)
```

A user plugin with the same name as a built-in replaces it. App hooks override everything; they need direct access to frontend-specific features (Textual widgets in TUI, readline in CLI).

Layers 2 and 3 are resolved by the engine (`ReplEngine.resolve_plugins`) inside `fire_lifecycle` on `on_app_start` and `on_config_load`, identically in the TUI, CLI and MCP server, and rebuilt from scratch on every config switch, so a per-config transform, directive or hook goes away with its config. The order is enforced by the engine rather than by call sequence: a folder plugin whose name an app hook owns is skipped with a warning, however early the frontend registered the hook. `TERMAPY_TRUSTED_PLUGINS_ONLY=1` drops layers 2 and 3 in every frontend; the engine takes the policy as a constructor argument (`trusted_only`) with the frozen env flag as its default, and `global_root` names the folder whose `plugin/` child is layer 2 (None = `config.cfg_dir()`), so a test can drive the whole path against a temp folder.

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

### Dispatch pipeline

Every line — typed at the prompt or run from a `.run` script — flows through one ordered pipeline, so new behavior attaches to a stage instead of becoming a special case:

```text
raw line
  → Directive       intercept non-/command syntax (may rewrite / warn / error)
  → Transform       rewrite the command text; expand $(NAME) variables
  → Route           starts with the prefix? -> REPL command : serial send
  → Capability gate  command's `needs` vs the frontend's CapabilitySet
  → Handler         the plugin or hook function
  → CmdResult       success / value / error / elapsed
```

**`CmdResult` is the uniform contract.** Every handler returns one, and the active **output level** (`silent` / `quiet` / `normal` / `verbose`) decides which of its channels render. So the *same* handler is correct in a chatty TUI, a normal CLI, a quiet script that only consumes `value`, or an MCP call that treats `value` as structured data — handlers emit through `ctx.io` and never branch on "which frontend am I."

**Variables** resolve in the transform stage from three sources: user-assigned (`/var.set`, or the `$(NAME) = value` directive), the loaded **config**, and the **environment** (`$(env.NAME)`). Because expansion happens before routing, the same `$(NAME)` works in a REPL command and in a bare serial line. The runtime trace of this pipeline is in [Key data flows](#key-data-flows) → *Command dispatch* below.

### Plugin file convention

A plugin file may export any of: a `COMMAND`, a `TRANSFORM`, a `DIRECTIVE`, a symbol-map converter (the four top-level names `FORMAT` / `DESCRIPTION` / `DETECT` / `convert`, the same shape the built-in converters use), and/or top-level lifecycle functions (`on_app_start`, `on_app_stop`, `on_script_start`, `on_script_stop`). All are optional; the loader picks up whatever's there.

A converter is four names rather than a dataclass because it is one function plus two strings — `Command` earns its dataclass through validation and ~15 fields, and a wrapper here would exist only to be unpacked. Plugin converters live on the `ReplEngine` with a `source` label (not in the static `CONVERTERS` tuple), so a config switch drops them exactly like commands; `/sym.import` passes them to `find_converter`, which searches the built-in registry too. An empty `DETECT` means explicit-`format=` only — a board's own pipeline should not join format sniffing.

### Device files: the board's registers, not the build's

A linker map holds what the firmware defines, never the registers the silicon fixes, so those live in **device files** (`devices/`; `<cfg>/dev/*.device.json`, plus a global `termapy_cfg/dev/` that loads into every config, per-config overriding by `device` name). The symbol session (`symbols/session.py`) keeps the two inputs apart — `build` (the sidecar) and `devices` — and rebuilds one merged `table` from them through a single installer, so `/sym.import` replaces the build half and cannot drop the board half, and the sidecar never receives a device row. `Symbol` carries the three safety fields the memory commands act on (`rmw`, `access`, `read_effect`); everything a viewer shows (peripheral, description, reset, structured fields) stays on `devices.Register`. Loading a device file also changes what `/mem.*` will *read* at those addresses, because a register is an intrusive probe on live silicon where a variable is not: `devices.registers_in_range` answers the one question the gates ask — does this byte range touch a register — and `devices.distinct_spans` collapses the mode aliases SVD emits (a SERCOM's `CTRLA` is six definitions at one address, 410 such addresses on a PIC32CM5164LE00100, and counting entries rather than spans wrongly refused a single-register read on 47% of that part), so `/mem.dump` refuses a span covering two or more distinct registers, `/mem.str` refuses a scan that could reach any, a `wo` register refuses every reading path (including the read half of `/mem.or` and `/mem.not`), and an allowed peripheral read is logged like a write. Naming one register stays allowed; the *sweep* is what is refused. The gate is address containment rather than per-register knowledge on purpose — `access` is populated in every vendor SVD, but `readAction`, which would say which registers are destructive to read, is empty in all of them, so a safety list is not something termapy could honestly offer. There is deliberately no shipped catalog and no cfg key: a catalog would be a copy-from folder, never a load layer, and a relocatable part cannot load until its file says where it sits (`instances`). The user's own **library** is that copy-from pool made explicit — nothing in it loads. It has the same two layers as `plugin/` and `dev/`: the config's own `<cfg>/lib/` over the shared `termapy_cfg/lib/`, the per-config one winning a name clash (`devices.library_layers`), so a checked-in config folder carries its parts and its references resolve on any machine. Each layer is a `vendor/type/family` tree listed by `/dev.lib` (with its layer) and scanned header-only by `devices.scan_library`, which refuses a file whose `device` field disagrees with its filename. `/dev.import` writes the converted part to the config's own library by default (`to=global` shares it with every config), under `<vendor>/<category>/` with the vendor slugged from the file's own metadata, and drops a reference into `dev/`, so importing a 2 MB MCU leaves 61 bytes in `dev/` rather than a second copy; `/dev.use <part> {at=NAME@ADDR ...}` adds one that is already in the library. A `dev/` file reaches it with `{"ref": "<part>", "instances": [...]}`: the library part keeps the registers and stays pristine while the reference supplies only this board's placement, so one 2517-register MCU file serves every config that uses that chip and re-importing a newer conversion cannot lose a board's bases. A reference may override `instances` and nothing else, since one that could redefine registers would be a fork, and a **relocatable** part must state its own placement rather than inheriting the library part's — those are an example written by whoever prepared the part, and inheriting them would load every register at a plausible but wrong address, which is the same hazard that makes a bare relocatable file an error. `dev/` remains the bill of materials either way — folder presence is still the declaration, which is why this is a file and not the cfg key that was built and deleted during the original device work. The format normalizes whatever a vendor publishes the way `xc32.py` normalizes a linker map: `/dev.import` runs a device converter (`devices/converters/`, CMSIS-SVD built in) and writes the document it returns as `dev/<device>.device.json`, so a converted part and a hand-written one pass the same validation and the file on disk is the source of truth. Symbol and device converters share one record type and lookup (`converters.py`); a plugin file picks its kind with `KIND`, and the engine keys them by `(kind, format)` so the two never collide or cross over.

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

### Worked example: the `seq` plugin

`builtins/commands/seq.py` is a small real plugin that exercises both **session-global state** (a namespace) and **lifecycle events** in one file. It owns the `seq` namespace — the counters behind the `{seqN+}` script-template placeholders — and resets that state at the right session boundaries:

```python
from datetime import datetime
from termapy.plugins import CmdResult, Command, TextLine


def _now() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# --- lifecycle: set up / reset the shared state at session boundaries ---


def on_app_start(ctx):  # fires once, after plugins load
    ctx.ns("seq")["_start_time"] = _now()


def on_script_start(ctx):  # fires at each OUTERMOST /run (not nested)
    seq = ctx.ns("seq")  # the shared session dict
    seq.clear()  # fresh counters per top-level script run
    seq["_start_time"] = _now()


# --- command: read the shared state ---


def _handler(ctx, args) -> CmdResult:
    numbered = sorted((key, value) for key, value in ctx.ns("seq").items() if isinstance(key, int))
    counters = {f"seq{key}": value for key, value in numbered}
    if not counters:
        return CmdResult.record({}, [TextLine("No counters set.")], value="")
    rows = [(name, str(count)) for name, count in counters.items()]
    line = ", ".join(f"{name}={count}" for name, count in counters.items())
    return CmdResult.record(counters, rows, value=line)


COMMAND = Command(name="seq", help="Print sequence counters.", handler=_handler)
```

What it demonstrates:

- **Global/session state** lives in `ctx.ns("seq")` — a plain dict shared across every call for the life of the `PluginContext`. The `{seqN+}` transform writes counters here; `_handler` and the template engine read them. No module-level globals, no monkeypatching `ctx`.
- **Lifecycle** keeps that state honest: `on_app_start` seeds it once; `on_script_start` clears it at the top of each outermost script so runs don't bleed into each other — and since nested `/run` doesn't re-fire, inner scripts inherit the outer run's counters. The engine knows nothing about sequence counters; the plugin owns the policy.

- **A record answer**: the handler prints nothing.  `CmdResult.record` carries the counters as `data` (`{"seq1": 3}`) and the rows a person reads; dispatch prints the rows, or hands the record alone to `--json` or MCP (registry `record-one-format`).

Copy this shape for any plugin that needs per-session state with setup/reset semantics. (The full file adds a `_start_time` for the `{starttime}` placeholder, a `seq.reset` subcommand, and a dynamic `long_help` that reads the live state.)

## Rules

The rules the layers rely on are listed in [docs/rules.md](docs/rules.md), the rule registry, one row each: a stable id, whether a test enforces it (a guarantee) or review does (an assumption), and the test. The guards in `tests/test_architecture.py` lead their headers with the id, and a guard there keeps the table and the guards agreeing both ways. The decisions behind the rules, and the choices that are not rules at all, are in [docs/decisions.md](docs/decisions.md), dated and append-only.

## Layer diagram

```text
┌──────────────────────────────────────────────────┐
│  app.py - Textual App                            │
│  ┌─────────────┐ ┌──────────┐ ┌──────────────┐   │
│  │ Title Bar   │ │ RichLog  │ │ Bottom Bar   │   │
│  │ (X,Help,Cfg,│ │ (serial  │ │ (/, Input,   │   │
│  │  Run,Proto, │ │  output) │ │  <Rec>, find,│   │
│  │  Port,Status│ │          │ │  DTR/RTS,Cap)│   │
│  └─────────────┘ └──────────┘ └──────────────┘   │
│  ┌──────────────────────────────────────────┐    │
│  │ dialogs/ - Modal Screens (per-file pkg)  │    │
│  │ ConfigPicker, ConfigEditor, PortPicker,  │    │
│  │ ScriptPicker, ConfirmDialog, QuickSetup  │    │
│  └──────────────────────────────────────────┘    │
│  ┌──────────────────────────────────────────┐    │
│  │ proto_debug.py - Proto Debug Screen      │    │
│  │ Interactive send/expect with visualizers │    │
│  └──────────────────────────────────────────┘    │
│  ┌──────────────────────────────────────────┐    │
│  │ App Hooks - commands needing Textual     │    │
│  │ ss.svg, ss.txt, help.open, vt100         │    │
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
│  • handles/{io,serial,fs,ui,internal}.py:        │
│      capability-domain handles attached to ctx   │
│  • loader.py: load_plugins_from_dir, validation  │
│  • output_levels.py: silent/quiet/normal/verbose │
│  • views.py: Column, TableView (CmdResult.table) │
│    RecordView (CmdResult.record)                 │
├──────────────────────────────────────────────────┤
│  protocol/ - Protocol Engine (package)           │
│  • core.py: format-spec language (H, U, I, S,    │
│    F, B, CRC), apply_format, FrameCollector      │
│  • crc.py: crcglot catalog + plugin loading    │
│  • runner.py: .pro test-script execution         │
│  • viz.py: visualizer plugin loader              │
├──────────────────────────────────────────────────┤
│  config.py         - dirs, loading, validation   │
│  defaults.py       - DEFAULT_CFG, templates      │
│  migration.py      - schema migration chain      │
│  scripting.py      - pure functions, no state    │
│  demo.py           - simulated device for --demo │
│  mcp/              - MCP stdio server (sibling)  │
│  profile/          - v2 profile schema + loader  │
│  usb/              - USB lookup tables           │
└──────────────────────────────────────────────────┘
```

## Import cost: each path loads only what it shows

termapy is one package with several front doors: the TUI, the plain-text
CLI, the MCP server, VT100 passthrough, and the print-and-exit flags
(`--ports`, `--usb`, `--chips`, `--check`, `--version`). A process pays for
every module it imports before it does any work, and imports are
transitive: one top-level `import` in a widely shared module is paid by
every door that reaches it. So each path loads only what it shows (registry
`flag-paths-light`, decision 2026-10-17).

Three techniques, in order of preference:

1. **Import where used.** A library a module needs in one function is
   imported inside that function. `markup.py` imports Rich inside
   `escape_markup`, `markup_to_plain` and `json_markup`; `update_check`
   imports `urllib` inside the background fetch; `entry.py` reads
   `importlib.metadata` only in the `--version` action, not every time it
   builds the parser.
2. **Build on first use.** A module-level table built from a library is
   built by a `functools.cache` function on its first call instead, so
   importing the module does not import the library: `scripting`'s age and
   duration tiers from `frist`.
3. **Split the module.** When the library is needed to define classes
   (subclasses of prompt_toolkit's `History` and `Completer`), they move to
   their own module, imported only on the path that uses them.
   `cli_prompt.py` is the CLI's interactive prompt; `cli.py` imports it only
   when it builds a prompt, so `-e` and `--run` one-shots never load
   prompt_toolkit.

The usual trap is a package `__init__`. Importing
`termapy.plugins.capabilities` runs `termapy/plugins/__init__.py` first,
which imports the whole plugin API; that is how Rich reached `--ports`
(`port_control` → `plugins` → `markup` → Rich) though no flag code names
Rich.

What it bought: the print-and-exit flags start in half the time or less, a
CLI one-shot loads about a third fewer modules, and the TUI imports
noticeably faster. The measurements are in the decision entry.

**Enforced by `tests/test_cli.py::TestPathsStayLight`**: each path runs in
a subprocess and the test reads `sys.modules` afterwards, so a transitive
import is caught as surely as a direct one. The static complement is
`ui-layer-textual`, which pins the modules allowed to import Textual. Two
known exceptions: `--info` loads Rich, because it flattens the shared
`/port.chip` page to plain text with Rich's parser, and the TUI reads
`importlib.metadata` for the version it shows. To find a new cost, run
`python -X importtime -c "import termapy.app" 2> importtime.txt` and sort
by the cumulative column; the indentation shows which module pulled each
one in.

## CLI mode (`cli.py`)

`termapy --cli` runs a plain-text terminal without Textual. It shares the same `ReplEngine`, `SerialEngine`, `PluginContext`, and all built-in plugins. The difference is how the frontend wires `PluginContext` callbacks:

| Callback             | TUI (app.py)                 | CLI (cli.py)                  |
| -------------------- | ---------------------------- | ----------------------------- |
| `ctx.io._write()`    | `RichLog.write(Text(...))`   | `Rich Console.print()`        |
| `ctx.ui.confirm()`   | Modal dialog + `event.wait()`| `input()` prompt              |
| `ctx.fs.open_file()` | `open_with_system()`         | `open_with_system()`          |
| `ctx.internal.port`  | `self.ser` (via SerialEngine)| `engine.serial_port.port`     |
| `delay_progress`     | bottom bar / script overlay  | bar at the prompt only        |

CLI-specific features: prompt_toolkit tab completion and shared command history (`cli_prompt.py`, imported only when an interactive prompt is built, so `-e` and `--run` one-shots never load prompt_toolkit), `/term.color on|off` toggle. CLI limitations: no `/grep` (no scrollback buffer); `/edit.cfg` opens the system editor instead of the built-in modal editor.

## MCP mode (`mcp/server.py`)

`termapy --mcp` runs a third frontend (`MCPHost`): an MCP stdio server that exposes the same `ReplEngine`, `PluginContext`, and built-in plugins to an LLM client rather than to a human. Like CLI mode it has no Textual, so the `ctx.ui.*` handle raises `MissingCapability`; everything else (serial I/O, plugins, scripting) is the same engine the TUI uses. This is why MCP is a first-class audience for the project, not a bolt-on: an LLM drives the device through the identical command pipeline a support engineer types into.

A client discovers what a device can do from a JSON **catalog** (`mcp/catalog.py`) — the command list plus device-state resources — and invokes commands through a single `run_command` tool that dispatches them down the normal pipeline. When a v2 device **profile** is loaded (`profile/`), requests and responses are *shaped* by the profile (typed arguments in, structured fields out) instead of returned as raw text. Each cfg-with-profile is served by its own `--mcp` process; there is no hot-swap, so a client config lists one entry per device. The session log, `mcp/session.log`, is inspectable inline with `/mcp.log.dump`, or `/log.dump` over MCP: each frontend's session log is its own (`session_log_path()`; the TUI's `<name>.log`, none in the CLI). For the catalog schema, profile shaping, and the destructive-action approval gate, see `mcp/catalog.py`, `mcp/server.py`, and `help/authoring-profiles.md`.

## VT100 mode (`vt100.py`)

`termapy --vt100` is the one mode that does **not** go through `ReplEngine` / `PluginContext`. It is a raw passthrough for devices that emit terminal control beyond plain lines (cursor addressing, full-screen menus, `vi`/`top`, bootloader UIs): the surface is *already* a terminal, so the right move is to hand it the byte stream and let it emulate, rather than run an emulator. `run_vt100_mode` resolves the config (mirroring the CLI resolver), opens the raw pyserial object via the shared `open_serial` path (no `SerialEngine`, no `SerialReader`), and hands it to the vendored pyserial `Miniterm` (`vendor/serial/tools/miniterm.py`), which owns the read/write loop and enables VT processing on Windows 10+. It is Textual-free by design: dropping the TUI avoids stacking a third key-interception layer (VS Code → Textual → widget), which is why interactive VT100 is *not* built inside the TUI. miniterm is treated as a hidden implementation detail: `run_vt100_mode` replaces its banner with a termapy-branded line and disables its Ctrl-T settings menu (menu key set to an inert value, so Ctrl-T passes through to the device), so the passthrough reads as a native termapy view rather than "you dropped into pyserial." It also saves/restores `sys.stdout`/`sys.stderr` around the session, because miniterm's Windows console swaps them and never restores them -- required for re-entry across mode switches. `vt100` is a peer mode of `cli`/`tui` — selected by the `--vt100` flag or `"default_ui": "vt100"`, resolved in `entry.py`'s mode loop. It is also a reversible toggle from the TUI (`/vt100` for the current device, `/demo.vt100` for the demo), mirroring `/cli` <-> `/tui`: `app._switch_to_vt100()` sets `switch_to="vt100"` and exits; the mode loop tracks the previous mode and tells `run_vt100_mode` to return `"tui"` (so Ctrl-] comes back) when entered from the TUI, or `None` (quit) when launched via the flag. Driving such a device from an LLM is a separate, future concern (a headless emulator over MCP), deliberately not this passthrough.

To show the mode doing something a terminal-aware frontend uniquely can, `--vt100 --demo` resolves (in-memory, no disk write) to the reserved `DEMO_VT100` port, backed by `demo_vt100.py`'s `FakeSerialVT100`. It subclasses `FakeSerial` for the serial duck-type surface and overrides only the I/O: `write` decodes keystrokes (arrows/`jk`/Enter/`q`) into navigation, and `in_waiting` ticks a live redraw. Frames are absolutely positioned (`ESC[row;colH`) so they carry no `\r`/`\n` and survive the RX EOL transform.

## Key data flows

### Serial read (background thread)

```text
SerialEngine.read_loop() [background thread]
  → serial.read() → rx_queue (bounded; oldest chunk dropped when full)
  → SerialReader.process(data) → ReaderResult
    → binary capture active? → CaptureEngine.feed_bytes() → skip display
    → serial_claimed? → suppress display (the claimant reads rx_queue)
    → hex view on? → cut the raw chunk into hex rows (before decoding)
    → incremental decode → split per eol_rx → batch lines
  → callbacks (NONE may block on another thread):
               on_lines        → _rx_enqueue("lines")   ┐
               on_hex          → _rx_enqueue("hex")     │ (hex view: rows + lines)
               on_clear        → _rx_enqueue("clear")   │ ordered buffer +
               on_capture_done → _rx_enqueue("capdone") │ ONE post_message
               on_error        → _rx_enqueue("error")   │
               on_disconnect   → _rx_enqueue("disconnect")┘
                 → SerialRx → _on_serial_rx [main] → _write_batch
                   → RichLog (renders the newest max_lines) + session log
                   → _feed_rx_consumers → expect watcher + text capture
```

Every received line reaches the session log, the expect watcher and text
capture, in order, in every frontend (registry `rx-every-line-delivered`).
Only the TUI's render is capped, at what the pane keeps. The CLI and MCP
`on_lines` run on the reader thread and call the same
`TerminalHost._feed_rx_consumers` beside their own display and log.

The hex view (`/term.hex`) is display only. The reader asks the host's
`hex_view` once per chunk and, while it is on, calls `on_hex(rows, lines)`
instead of `on_lines`: every frontend shows the rows, built from the raw
bytes, and hands the decoded lines to the same consumers (registry
`hex-view-wire-bytes`). Deciding per chunk in the reader means a toggle can
neither show a chunk twice nor skip one.

### Command dispatch (user input or script)

```text
on_input_submitted() → _dispatch_on_thread_interactive()   (worker thread)
  → _dispatch_single() → repl.dispatch_full()
    → /raw <text>? → the /raw handler, text exactly as typed (no line ending)
    → run_directives() → rewrite/warn/error
    → starts with prefix? → apply transforms → repl.dispatch()
      → lookup dotted name → call handler(ctx, args)
    → else → apply serial transforms → repl.dispatch("term.send <text>")
```

Every byte a command sends leaves through `TerminalHost._serial_write`, the
one TX path in every frontend: a failed write raises to the command, which
reports it, and the TX observers and the session log hear only bytes that
went out (registry `tx-only-what-went-out`).

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
    → /run nested.run → inline recursive call (up to 5 deep)
    → every other line, /delay /expect /confirm included
      → dispatch callback → _dispatch_single
  → post ScriptProgress → update overlay label
  → post ScriptFinished → teardown overlay
  Input disabled during execution, Escape or Stop button aborts
```

## Config and filesystem

```text
termapy_cfg/
├── plugin/               # user plugins (all configs)
├── dev/                  # device files loaded by every config
├── lib/                  # shared device library: vendor/type/family tree, NOTHING loads
└── <name>/
    ├── <name>.cfg        # JSON config file
    ├── <name>.log        # session log
    ├── <name>.md         # info report (from /cfg.info)
    ├── <name>.history    # command history
    ├── plugin/           # per-config plugins
    ├── ss/               # screenshots (SVG + TXT)
    ├── run/              # .run script files
    ├── proto/            # .pro protocol test scripts
    ├── viz/              # per-config packet visualizers
    ├── cap/              # data capture output files
    ├── prof/             # /run.profile timing CSVs
    ├── sym/              # symbol tables (/sym.import)
    ├── dev/              # device files: the board's registers (*.device.json, or a ref)
    └── lib/              # this config's part library; checked in, the folder travels
```

That tree is the maximum, not the default. `prepare_cfg_folder()`, run when a config is loaded (`load_config`) or created, makes the config folder and its `.gitignore`, renames old folder names (`captures/` → `cap/`, `scripts/` → `run/`, `plugins/` → `plugin/`) and moves stem-named sidecars into their folder (`folders.SIDECARS`) — those are the one-time migrations. `cfg_data_dir()` is its read half: it returns the folder and writes nothing, as do `cfg_dir()` and `app_dirs`, so a listing, a viewer or an MCP query may ask about any config without changing it (registry `reads-never-write`). The data folders themselves follow a rule: **a data folder exists while something is in it.** `folders.ensure_folder` creates one at the write (or when a `.explore` command opens it for the user to drop a file in) and never at a read -- a missing folder lists as empty -- `folders.prune_empty_folders` removes empty ones from `ReplEngine.fire_lifecycle` at every config load and at app stop, and nothing else creates or removes one (`tests/test_architecture.py` fails a raw `mkdir` on a data folder). The global `termapy_cfg/plugin/` follows the same rule. `--demo` ships the four folders it populates (`run/ proto/ plugin/ sym/`). So a plain config is its `.cfg`, log, history, report and `.gitignore`, and `/cfg.info` lists only the folders that exist.

## Config migration

Every config carries a `config_version`. On load, `migrate_config()` (`migration.py`) runs the chain in `MIGRATIONS` from the file's version up to `CURRENT_CONFIG_VERSION` — one small function per step:

```text
config_version: N  ──migrators──▶  CURRENT_CONFIG_VERSION
   vN→vN+1, vN+1→vN+2, ...     (each step is one function in MIGRATIONS)
```

A migrator does whatever a schema change needs: rename a key, nest keys under a sub-dict (`port` → `serial.port`), rewrite a renamed command verb in the `*_on_connect_cmd` fields (`/color` → `/term.color`, `/ver` → `/app.ver`), move what a field held into a file (v30: a multi-command on-connect field becomes `run/<field>.run` and the field `/run <field>`), or retire a key. Two supporting tables back it: `DEPRECATED_CFG` (the reason each removed key went away, surfaced as a warning) and the legacy-command tables in `legacy.py` (so `/run.legacy` can rewrite old command names inside `.run` scripts).

The payoff — and the reason this is a core concept, not plumbing — is that schema and command changes ship freely: an old config opens, upgrades itself with a chatty per-step summary, and the user never hand-edits JSON. A config written by a *newer* termapy is detected and gets one clear upgrade hint instead of a wall of "unknown key" warnings.

## Threading model

```text
┌─────────────────────┐
│ Main thread         │  Textual event loop - all UI updates,
│ (async)             │  modals, button handlers, Message handlers
│                     │  (ScriptStarted, etc.).  Dispatches NOTHING:
│                     │  a typed line, a button or a palette entry
│                     │  only launches a worker.
├─────────────────────┤
│ reader thread       │  Long-lived; OWNED BY SerialEngine
│ engine.start_reader │  (start_reader / stop_reader), so teardown
│                     │  can Thread.join() it.  Callbacks hand off
│                     │  via _rx_enqueue and NEVER block on main.
├─────────────────────┤
│ _dispatch_on_thread │  One short-lived worker per typed line,
│ (+ _interactive,    │  button or palette entry; runs
│  _sequence_)        │  repl.dispatch_full INLINE on that thread,
│ @work(thread=True)  │  serialized by _acquire_dispatch_guard.
├─────────────────────┤
│ _run_script()       │  Short-lived per script; every line is
│ @work(thread=True)  │  dispatched on this thread.  /run and
│                     │  /run.profile are run here by the script
│                     │  runner itself -- they need the ScriptCtx,
│                     │  not a particular thread.  Nested /run is
│                     │  inline.
├─────────────────────┤
│ _run_reconnect()    │  Short-lived, retries the connection
│ _run_lines()        │  Short-lived, multi-line send with delays
│ _check_for_updates  │  Short-lived, version check
│ proto_debug:        │
│  _run_tests()       │  Short-lived, the debug screen's Run
│  _run_cmds()        │  Short-lived, setup/teardown commands
└─────────────────────┘
```

Only the reader is long-lived. Dispatch and scripts are each serialized: one
`dispatch_full` at a time (nested dispatch on the same thread is allowed) and
one outermost script; the reconnect and update-check workers run independently
of both.

That is enforced in two places, not assumed. `ReplEngine.start_script` holds
the thread id of the run in progress (`_script_owner`), so a nested `/run`
(same thread, inline via `_script_run`) is allowed while a second launch from
any other thread -- the picker, a button, another frontend -- is refused; the
rule lives in the engine because the CLI and MCP need it as much as the TUI
does, and `run_script` re-stamps the owner because the TUI launches on a
dispatch worker and then runs on a `@work` thread. `app._acquire_dispatch_guard`
serializes individual dispatches: non-blocking on the main thread, where
waiting for a guard a worker holds would deadlock against that worker's own
`call_from_thread`, and a bounded wait (`_DISPATCH_WAIT_S`) off it, so a
colliding line is delayed rather than dropped -- on a serial tool a refused
dispatch is a command that never reached the device, not a delayed one. After
the wait expires it still fails with "Busy" rather than queueing. The guard is
an `RLock`, so nested `ctx.dispatch` on the same thread is allowed.

Two different hand-off mechanisms, and the difference is load-bearing. Output
helpers called from a worker (`_status`, `_set_status_bar`,
`_write_output_markup`, `_on_main`, and the connection hooks `_on_connected`,
`_on_connect_failed`, `_on_disconnected` that `/port.connect` reaches) compare
`self._main_thread_id` and marshal via `call_from_thread`, which **blocks** the
caller until the event loop services it. The serial reader may not use it: blocking there deadlocks teardown
(`disconnect()` joins the reader from the very thread it would wait for) and
stalls the reader long enough for the driver to drop bytes
(`config.SERIAL_RX_BUFFER_BYTES` is how little room there is). RX and script
lifecycle therefore use `post_message`, which is fire-and-forget:
`app._rx_enqueue` appends to an ordered buffer and posts at most one wake-up.
Disconnect joins the reader for real; `READER_JOIN_TIMEOUT_S` is a safety net,
not a contract.

Cancellation is per run, never shared. The reader stops on the engine's
`stop_event`; each auto-reconnect run gets a fresh token from
`_auto_reconnect`. When the two shared one event, every connect attempt
(which stops the reader first) also ended the reconnect loop, and a connect
that then failed left nothing retrying. A run opens the port once, through
the same path as `SerialEngine.connect`, and the TUI finishes the connection
it hands back; the throwaway probe open it replaced put an extra DTR edge on
every reconnect.

## Built-in plugins

Every built-in command is one file in `builtins/commands/`, listed with its
`/command` and purpose in the [Module structure](#module-structure) tree
above — that tree is the single source of truth, so there's no second list
to drift out of sync. They are ordinary plugins: the same `Command` +
`PluginContext` API a user plugin uses. Hidden legacy aliases (e.g. `/echo`
→ `/term.echo`) are registered centrally from `legacy.py` rather than as
files (see [Hooks](#hooks) for the host-registered exceptions).

## Suppressions

28 lint/type/coverage pragmas in `src/termapy` (excluding vendor). Every one carries a specific rule code and a reason; `release_prep` hard-fails any newly-added suppression that lacks either. Run `python scripts/suppression_audit.py` for the current list, or `--since <tag>` to gate a diff.

## Test coverage

Extensively tested (run `uv run pytest`; the README's Test coverage section
carries the current count).  Every test module and what it covers, from its docstring:

<!-- test-table:start (generated by scripts/sync_architecture.py from the test module docstrings -- do not edit by hand) -->

| File                                | Covers                                                                                                                                             |
| ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| test_alias_chip_coverage.py         | Cross-module contract: every chip family has a matching mfg alias                                                                                  |
| test_app_config.py                  | Tests for app.py config utilities, custom buttons, and script editor                                                                               |
| test_app_dirs.py                    | Tests for app_dirs.app_state_dir and app_config_dir                                                                                                |
| test_app_pilot.py                   | Pilot-driven tests for the real SerialTerminal app                                                                                                 |
| test_app_pilot_targets.py           | app.py state transitions the August Pilot pass listed as next                                                                                      |
| test_app_pilot_views.py             | The capture overlay, the delay bar, and the read-only info views, driven in the real app                                                           |
| test_app_plugin.py                  | Tests for the /app builtin plugin                                                                                                                  |
| test_architecture.py                | Structural rules checked against the source tree itself                                                                                            |
| test_architecture_sync.py           | Guard ARCHITECTURE.md's generated module tree and test table against hand edits and drift                                                          |
| test_bare_forms.py                  | bare-declared: a bare call's behavior is a declaration the dispatcher honors                                                                       |
| test_builtins.py                    | Tests for built-in REPL commands dispatched through ReplEngine                                                                                     |
| test_cap_poll.py                    | `/cap.poll`: the scheduled polling loop, its files, its validation, and its stop                                                                   |
| test_capture.py                     | Tests for CaptureEngine - file capture state machine                                                                                               |
| test_capture_claim.py               | A capture owns its serial claim from start to stop, inside a script or not                                                                         |
| test_cfg_icon.py                    | Tests for /cfg.icon -- per-cfg desktop launcher creation                                                                                           |
| test_cfg_info_report.py             | The /cfg.info project report: its tails, and the exit hook that rewrites it                                                                        |
| test_cfg_load.py                    | End-to-end tests for /cfg.load                                                                                                                     |
| test_checkbox.py                    | Behavioral tests for StrongCheckbox -- the glyph flips with the value                                                                              |
| test_cli.py                         | Unit tests for CLITerminal - hooks, helpers, output, and dispatch                                                                                  |
| test_cli_exec.py                    | End-to-end tests for `--exec` / `-e` one-shot CLI mode                                                                                             |
| test_cli_gold.py                    | CLI gold-standard tests - run .run scripts and compare stdout to expected output                                                                   |
| test_cli_improvements.py            | The print-and-exit port flags: --ports / --chips JSON, the VID/PID/mfg/SN filters, and the driver column                                           |
| test_cli_prefix.py                  | End-to-end test: the REPL command prefix is actually configurable                                                                                  |
| test_cli_zero_config.py             | End-to-end tests for zero-config CLI mode                                                                                                          |
| test_commands_doc_sync.py           | Freshness guard: every command listed in help/commands.md must exist                                                                               |
| test_config_create.py               | Tests for creating a config: `config.new_config`, `config.create_config` and `--new`                                                               |
| test_config_reference_sync.py       | Guard the config field-reference table against drift                                                                                               |
| test_config_rename.py               | Tests for `config.rename_config`, `config.delete_config` and `config.validate_file_stem`                                                           |
| test_crc_builtins.py                | Tests for built-in CRC checksum modules (sum8, sum16)                                                                                              |
| test_crcglot_abi.py                 | Does crcglot's compiled extension run on THIS interpreter?                                                                                         |
| test_credits_data.py                | The dependency registry must agree with pyproject.toml, the vendored tree, and the surfaces that render it                                         |
| test_credits_sync.py                | Guard the markdown -> credits.py sync                                                                                                              |
| test_defaults.py                    | Tests for defaults.py - validation constants, resolve_color, preview helpers                                                                       |
| test_demo.py                        | Tests for FakeSerial demo device - ASCII, GPS/NMEA, Modbus RTU, and config setup                                                                   |
| test_demo_memory.py                 | The demo device's native memory: `MEM.R` / `MEM.W` / `MEM.INFO`                                                                                    |
| test_demo_ndjson.py                 | Tests for the DEMO_JSON NDJSON simulator                                                                                                           |
| test_demo_profiles.py               | Tests for the bundled DEMO profiles                                                                                                                |
| test_demo_random_packet.py          | Tests for the demo's AT+RND random-packet emitter                                                                                                  |
| test_demo_symbols.py                | Tests for the bundled demo symbol table (mirrors `test_demo_profiles.py`)                                                                          |
| test_demo_traffic.py                | The demo traffic plugin's /traffic.snoop, on the per-config plugin path --demo uses                                                                |
| test_demo_vt100.py                  | Tests for the DEMO_VT100 interactive demo device                                                                                                   |
| test_detect_gui_apps.py             | Tests for `termapy.plugins.detect_gui_apps`                                                                                                        |
| test_devices.py                     | Unit tests for device files: the format, instances, layering and merge                                                                             |
| test_devices_svd.py                 | The CMSIS-SVD converter, against a trimmed fixture that has every shape                                                                            |
| test_dialogs.py                     | Behavioral tests for the modal dialogs' result contracts                                                                                           |
| test_dialogs_config_editor.py       | The ConfigEditor dialog: what it shows, how it validates, what it dismisses with                                                                   |
| test_dialogs_quick_setup.py         | The QuickSetup dialog: name, baud entry, and the tuple it dismisses with                                                                           |
| test_dialogs_small.py               | The seven small dialogs: what each shows and what it dismisses with                                                                                |
| test_engine.py                      | Tests for ReplEngine internals: start_script, run_script, properties                                                                               |
| test_env_var.py                     | Tests for the env_var built-in transform and commands                                                                                              |
| test_env_var_gates.py               | Tests for the env-var security gates                                                                                                               |
| test_examples_load.py               | Guard: every plugin under examples/plugins/ must load cleanly                                                                                      |
| test_expect.py                      | Tests for the /expect and /expect.regex commands                                                                                                   |
| test_file_completion.py             | File-argument completion: the `Command.files` declaration and the engine's reader                                                                  |
| test_find.py                        | Tests for /find -- interactive scrollback match navigation                                                                                         |
| test_firmware_mem.py                | The drop-in MEM handler, `examples/firmware/termapy_mem.c`, run on the host                                                                        |
| test_folder_ops_dump.py             | Containment tests for the shared `.dump` folder handler                                                                                            |
| test_folder_ops_listing.py          | Tests for the shared listing producers in `folder_ops`                                                                                             |
| test_folders.py                     | Tests for termapy.folders -- folder/file constants                                                                                                 |
| test_format_tables_sync.py          | Guard the format-spec type tables against hand edits and decoder drift                                                                             |
| test_fs_sandbox.py                  | MCP filesystem/network sandbox: the fs chokepoint and its command gates                                                                            |
| test_handles.py                     | Tests for the namespaced PluginContext handles                                                                                                     |
| test_help_dynamic.py                | Tests for `help_dynamic` helpers                                                                                                                   |
| test_help_images.py                 | Verify help content: image references exist and tagged config examples stay current                                                                |
| test_help_tooltip.py                | Tests for the Help-button tooltip renderable                                                                                                       |
| test_hex_vocabulary.py              | Hex has one vocabulary: one reader for input, in any case; one case for output                                                                     |
| test_history_nav.py                 | Unit tests for history_nav: the Up/Down cursor and the file merge                                                                                  |
| test_hw_loopback.py                 | hw_loopback: tests that run only on the hardware loopback, the TX-RX-jumpered adapter                                                              |
| test_hw_loopback_gate.py            | The hw_loopback gate: a demanded run fails loud, and nothing runs on GitHub Actions                                                                |
| test_install_info.py                | Tests for `termapy.install_info.upgrade_command`                                                                                                   |
| test_lazy_folders.py                | The data-folder rule, driven end to end from a config with NO data folders                                                                         |
| test_legacy_migration.py            | Tests for the verbose/.quiet -> output-level migration                                                                                             |
| test_log_fingerprint.py             | `/log.fingerprint`: the session provenance report and each of its sections                                                                         |
| test_markup_escape.py               | markup-escape: text reaches the terminal as typed, through every markup path                                                                       |
| test_mcp_cfg_load.py                | Tests for /cfg.load via MCP frontend                                                                                                               |
| test_mcp_entry.py                   | Tests for the --mcp / --mcp-verbose entry-point wiring                                                                                             |
| test_mcp_lifecycle.py               | Tests for MCP lifecycle: profile-load rejection, on-connect fetch, banner watcher                                                                  |
| test_mcp_plugin.py                  | Tests for the /mcp.* REPL plugin                                                                                                                   |
| test_mcp_profile_executor.py        | Tests for the profile-aware MCP request/response executor                                                                                          |
| test_mcp_prompts.py                 | Tests for the MCP prompts surface                                                                                                                  |
| test_mcp_safety.py                  | `Command.safety`: termapy commands carry the profile's safety tiers                                                                                |
| test_mcp_server.py                  | Tests for the MCP server core: MCPHost + run_command + resources                                                                                   |
| test_mcp_stdio.py                   | End-to-end MCP over real stdio: the wire an actual client speaks                                                                                   |
| test_mcp_symbols.py                 | The symbol sidecar through the MCP frontend: auto-load and the data record                                                                         |
| test_mem_commands.py                | Behavior tests for the built-in `/mem.*` commands against the demo device                                                                          |
| test_memory_engine.py               | Unit tests for `termapy.memory` -- the bytes-only engine over the MEM spec                                                                         |
| test_memory_views.py                | Unit tests for `termapy.memory_views` -- scalars, chars, bit targets                                                                               |
| test_migration.py                   | Tests for config schema versioning and migration chain                                                                                             |
| test_observer_safety.py             | Exception-safety + behavior tests for `ctx.serial.rx_observer()` and `ctx.serial.tx_observer()` context managers                                   |
| test_output_level.py                | Tests for the output-level dial: levels, channel gating, suffix/flag dispatch                                                                      |
| test_palette.py                     | Tests for the command-palette additions that surface /find, /grep, /search                                                                         |
| test_params.py                      | Tests for declarative command parameters (plugins/params.py + dispatch)                                                                            |
| test_pickers.py                     | Tests for picker callbacks in `src/termapy/pickers.py`                                                                                             |
| test_plugin_resolve.py              | Tests for ReplEngine.resolve_plugins -- the one plugin-resolution step                                                                             |
| test_plugins.py                     | Tests for plugin loading, COMMAND dataclass, and PluginContext                                                                                     |
| test_port_commands.py               | The /port command family driven through the dispatcher on the CLI DEMO rig                                                                         |
| test_port_connect_cli.py            | End-to-end tests for the extended /port.connect command                                                                                            |
| test_port_control.py                | Tests for port_control.py - pure serial port control functions                                                                                     |
| test_port_noninvasive.py            | K1 regression: resolution and plain enumeration must never open a port                                                                             |
| test_port_serial_resolution.py      | End-to-end tests for USB serial-number resolution in port specs                                                                                    |
| test_post_dispatch_observer.py      | Tests for `ReplEngine.add_post_dispatch_observer` and friends                                                                                      |
| test_profile_memory_block.py        | The profile `memory` block: schema-known, lint-warned, never load-blocking                                                                         |
| test_profile_plugin.py              | Tests for /profile.load and /profile.info                                                                                                          |
| test_profile_schema.py              | Tests for termapy.profile and termapy.response_parsers                                                                                             |
| test_profile_types.py               | Tests for profile-local user-defined types (TypeRegistry + validators)                                                                             |
| test_proto_commands.py              | The non-CRC half of the /proto family driven through the dispatcher on the CLI DEMO rig                                                            |
| test_proto_crc_custom.py            | Tests for termapy's CLI handler around the CRC codegen surface                                                                                     |
| test_proto_crc_detect.py            | Tests for /proto.crc.detect -- generated from crcglot.VERBS                                                                                        |
| test_proto_crc_dispatch.py          | Smoke tests for the `/proto.crc.<lang>` REPL handlers                                                                                              |
| test_proto_crc_errors.py            | Regression test: /proto.crc.{c,python,rust} with an unknown algorithm                                                                              |
| test_proto_crc_find.py              | Tests for /proto.crc.find -- CRC algorithm identification                                                                                          |
| test_proto_crc_form.py              | Tests for /proto.crc.find `form=NAME` (crcglot 0.23+ payload forms)                                                                                |
| test_proto_crc_reverse.py           | End-to-end tests for /proto.crc.reverse and /proto.crc.find cmd=                                                                                   |
| test_proto_crc_styles_and_bundle.py | Tests for the `style=` kv arg and multi-algorithm bundling on `/proto.crc.<lang>`                                                                  |
| test_proto_crc_suggest.py           | Tests for the "did you mean" suggestion suffix on unknown algorithm names                                                                          |
| test_proto_crc_terminator.py        | The line ending on a cmd= capture is stripped only when it is one (/proto.crc.find and .reverse)                                                   |
| test_proto_debug_pilot.py           | The proto debug screen's Run and Stop buttons, driven headless with Pilot                                                                          |
| test_proto_run_command.py           | `/proto.run` writes `json_file`, returns the results as data, runs a test's own teardown, and stops on the engine's script-stop event              |
| test_proto_runner.py                | Tests for headless protocol test runner and JSON result output                                                                                     |
| test_proto_send_crc.py              | Tests for proto.send CRC-append feature                                                                                                            |
| test_protocol.py                    | Tests for termapy.protocol - hex utilities, framing, matching, script parsing                                                                      |
| test_protocol_word_fields.py        | Word-relative bit fields: `NAME:B.<bits>` binds to a register word, not to packet bytes                                                            |
| test_python_versions.py             | The supported-Python list must agree everywhere it is written down                                                                                 |
| test_repl_cfg.py                    | Tests for the /cfg and /cfg.auto REPL commands                                                                                                     |
| test_repl_coverage.py               | repl.py corners the engine tests miss -- covering suggest_command, edit_distance, ScriptCtx, directive dispatch paths, and script special commands |
| test_request_response.py            | Tests for `termapy.request_response.request_response`                                                                                              |
| test_resolve_config.py              | Tests for resolve_config - command-line config resolution chain                                                                                    |
| test_run_docstring.py               | Tests for the .run-file docstring parser (termapy.run_docstring)                                                                                   |
| test_run_record.py                  | Tests for /run.record -- session capture to a .run file                                                                                            |
| test_scripting.py                   | Tests for termapy.scripting - pure functions, no serial or Textual needed                                                                          |
| test_search_markup.py               | Regression tests for /search and cli output rendering markup-safety                                                                                |
| test_serial_engine.py               | Tests for SerialEngine - connection lifecycle and reader loop                                                                                      |
| test_serial_io_safety.py            | Exception-safety tests for the `ctx.serial.io()` context manager                                                                                   |
| test_serial_port.py                 | Tests for SerialPort and SerialReader                                                                                                              |
| test_shutdown_race.py               | Contract tests for the SHUTDOWN_RACE exception tuple                                                                                               |
| test_status_bar.py                  | Behavioral tests for the StatusBar widget                                                                                                          |
| test_suggester.py                   | Behavioral tests for CommandSuggester -- prefix type-ahead logic                                                                                   |
| test_suppression_audit.py           | Tests for scripts/suppression_audit.py -- the release-prep suppression gate                                                                        |
| test_sw_loopback.py                 | sw_loopback: the software loopback (pyserial's loop://) as a selectable virtual port                                                               |
| test_sw_loopback_crc_find.py        | sw_loopback: /proto.crc.find cmd= keeps a CRC byte of 0x0A, on pyserial's loop:// port                                                             |
| test_sw_loopback_io.py              | sw_loopback: end-to-end I/O tests using pyserial's `loop://` backend                                                                               |
| test_sym_commands.py                | Behavior tests for the built-in `/sym.*` commands and the symbol auto-load                                                                         |
| test_symbols_address.py             | Tests for `termapy.symbols.address` -- the address grammar                                                                                         |
| test_symbols_format.py              | Tests for `termapy.symbols.format` -- prose renderers and their `data=` twins                                                                      |
| test_symbols_provenance.py          | Unit tests for the symbol-table staleness verdict                                                                                                  |
| test_symbols_table.py               | Tests for `termapy.symbols.table` -- the model and the file format                                                                                 |
| test_symbols_xc32.py                | Tests for the xc32 converter and the converter registry                                                                                            |
| test_synopsis_validation.py         | Synopsis grammar validation: load-time chokepoint + registry conformance                                                                           |
| test_term_request.py                | Tests for /term.request toggle and the request_mode dispatcher fallthrough                                                                         |
| test_term_send.py                   | Tests for /term.send <text> and dispatch_full's rewrite of a bare device line to it                                                                |
| test_terminal_host.py               | Unit tests for TerminalHost - context builders, serial I/O, hooks                                                                                  |
| test_title_bar.py                   | Regression tests for title_bar.update_port with a read-only cfg                                                                                    |
| test_title_tooltip.py               | Tests for the title-bar tooltip formatters (pure string logic)                                                                                     |
| test_tree_render.py                 | Unit tests for `termapy.tree_render` (the `FileTree` renderer)                                                                                     |
| test_update_check.py                | Tests for update_check.check and its bail-out paths                                                                                                |
| test_usage_error.py                 | Dispatch-level tests for the UsageError mechanism                                                                                                  |
| test_usb_mfg.py                     | Tests for termapy.usb.aliases -- USB manufacturer alias folding                                                                                    |
| test_usb_serial_chips.py            | Tests for termapy.usb.chips -- VID:PID -> ChipInfo lookup                                                                                          |
| test_usb_tree.py                    | The USB tree: assembly, rendering, and the serial-port cross-reference                                                                             |
| test_usb_vendor.py                  | Tests for usb_vendor lookup + scripts/refresh_usb_ids.py transformer                                                                               |
| test_value_capture_e2e.py           | End-to-end check that handlers populate `CmdResult.value`                                                                                          |
| test_var.py                         | Tests for the user-defined variables plugin (var.py)                                                                                               |
| test_variable_isolation.py          | variables-isolated: a test cannot see the `$(NAME)` state an earlier test left behind                                                              |
| test_vfs.py                         | Tests for FakeSerial virtual filesystem - AT+FS commands and VFS state                                                                             |
| test_views.py                       | list-one-format: a list answer is records plus a view, and dispatch renders the view once                                                          |
| test_vt100.py                       | Tests for vt100.py - settings/transform mappings, config resolution, streams                                                                       |
| test_xmodem.py                      | Tests for XMODEM file transfer - QueueByteReader, FakeSerial responder, and protocol integration                                                   |
| test_ymodem.py                      | Tests for YMODEM file transfer - FakeSerial responder and ymodem library integration                                                               |

<!-- test-table:end -->

`app.py` is driven headless by `tests/test_app_pilot.py` (Textual Pilot) and the dialogs by `tests/test_dialogs.py`; `proto_debug.py` is tested manually. The serial engine, capture, reader, and dispatch layers are fully testable using `FakeSerial`.
