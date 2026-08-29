# Termapy architecture

For how termapy was built (and the role LLM tooling played), see [On AI assistance](src/termapy/help/on-ai-assistance.md).

## Core idea

Termapy is built on its own plugin system. Built-in commands (`/help`, `/cfg`, `/grep`, `/proto`, etc.) are regular plugins loaded from `builtins/commands/`. The same `Command` + `PluginContext` API that implements the core REPL is available to user plugins. Drop a `.py` file in a folder to add commands, override builtins, or build device-specific tools. No compilation or registration required.

## Core concepts

The whole system fits in your head once these pieces click. Each is detailed in its own section below; this list is the map.

- **Plugin system** — almost every command is a plugin (a `.py` file exporting a `COMMAND`). The same API builds the built-ins and any user/device plugin, so command functionality is added by dropping a file, not editing the core.
- **Hooks** — the small set of commands that need live frontend internals (Textual widgets, the screenshot surface, modals) are registered by the host instead of shipped as plugins. Everything else is a plugin.
- **PluginContext** — the stable API every handler receives: five handles — four capability domains (`io`, `serial`, `fs`, `ui`) plus the `internal` escape hatch — covering config, serial, filesystem, output/input, and global session state.
- **Capabilities & frontend projections** — each frontend (TUI / CLI / MCP) advertises a `CapabilitySet`; commands declare what they `need`. The dispatcher gates calls, so the three frontends are *projections* of one engine through different capability sets and an unavailable command fails cleanly instead of mysteriously.
- **Dispatch pipeline** — every input line follows one ordered path: *directive* → *transform* → REPL-vs-serial routing → *capability gate* → *handler* → **CmdResult**. New behavior attaches to a stage rather than becoming a special case.
- **CmdResult & output levels** — every handler returns one uniform `CmdResult` (success / value / error / timing). The active output level (silent / quiet / normal / verbose) decides which channels actually render, so a single handler is correct from a quiet script to a verbose TUI to an MCP caller that wants only `value`.
- **Variables** — `$(NAME)` placeholders, expanded during the transform stage from three sources: user-assigned (`/var`, `$(NAME) = ...`), the loaded config, and the environment (`$(env.NAME)`).
- **Lifecycle events** — plugins react to app start/stop and script start/stop, so session-scoped state sets up and tears down without the engine knowing about it.
- **Config migration** — every config carries a schema version and is auto-upgraded on load through a chain of small migrators, so schema and command changes ship without breaking existing user configs.

## Module structure

```text
src/termapy/
├── builtins/
│   ├── commands/           #              command plugins (private _* helpers omitted; legacy aliases register from legacy.py):
│   │   ├── app.py      #  /app - app-wide state, config, and version (/app.ver)
│   │   ├── cap.py      #  /cap - unified data capture (text/binary/struct/hex)
│   │   ├── cfg.py      #  /cfg - show or change config values, project info
│   │   ├── cls.py      #  /cls - clear the terminal screen
│   │   ├── confirm.py  #  /confirm - Yes/Cancel dialog (scripts)
│   │   ├── credits.py  #  /credits - print the acknowledgments page
│   │   ├── edit.py     #  /edit - open project files in the system editor
│   │   ├── env.py      #  /env - $(env.NAME) expansion (command + transform)
│   │   ├── exit.py     #  /exit - quit the application
│   │   ├── find.py     #  /find - navigate scrollback matches (interactive)
│   │   ├── grep.py     #  /grep - search scrollback for matching lines
│   │   ├── help.py     #  /help - forgiving help with man-page detail view
│   │   ├── log.py      #  /log.* - dump / fingerprint / show the session log
│   │   ├── mcp.py      #  /mcp.* - MCP catalog, status, session log
│   │   ├── os_cmd.py   #  /os - run a shell command (gated by TERMAPY_OS_CMD_ENABLED)
│   │   ├── ping.py     #  /ping - measure serial response time
│   │   ├── plugin.py   #  /plugin - plugin folder operations
│   │   ├── port.py     #  /port - serial port list/connect/configure/signals
│   │   ├── print.py    #  /print - print plain or Rich-markup text
│   │   ├── profile.py  #  /profile.* - device-profile commands
│   │   ├── proto.py    #  /proto - binary protocol send/expect/crc testing
│   │   ├── repeat.py   #  /repeat - repeat a command N times
│   │   ├── run.py      #  /run - execute .run scripts from any host
│   │   ├── run_edit.py #  /run.edit - open .run scripts in the editor
│   │   ├── search.py   #  /search - Google-style deep command search
│   │   ├── seq.py      #  /seq - show or reset sequence counters
│   │   ├── show.py     #  /show - show file contents
│   │   ├── ss.py       #  /ss - screenshot commands (svg, txt + folder ops)
│   │   ├── stop.py     #  /stop - abort a running script
│   │   ├── term.py     #  /term.* - terminal display / session toggles
│   │   ├── var.py      #  /var - user-defined variables, $(NAME) syntax
│   │   └── xfer.py     #  /xfer - file transfer (settings + XMODEM + YMODEM)
│   ├── crc/                #              Built-in CRC plugins (sum8, sum16)
│   ├── demo/               #              Demo config, scripts, proto files, plugins
│   └── viz/                #              Built-in packet visualizers (hex, text)
├── dialogs/                # (2565 lines) Modal screens - one file per dialog
│   ├── _common.py          #   Shared CSS, dismiss bindings, port-row helper
│   ├── cfg_confirm.py      #   CfgConfirm
│   ├── config_editor.py    #   ConfigEditor - the cfg-dict editor (the big one)
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
├── mcp/                    # (2078 lines) MCP stdio server
│   ├── catalog.py          #   JSON catalog + device-state resources
│   ├── prompts.py          #   MCP prompts (draft_profile, etc.)
│   └── server.py           #   MCPHost - run_command, async events, lifecycle
├── plugins/                # (3068 lines) Plugin system - capability-handle architecture
│   ├── handles/            #   IOHandle, SerialHandle, FilesystemHandle, UIHandle, InternalHandle
│   ├── capabilities.py     #   CapabilitySet, MissingCapability
│   ├── command.py          #   Command, CmdResult, Transform, Directive
│   ├── context.py          #   PluginContext dataclass + ns/plugin_cfg/dispatch
│   ├── loader.py           #   Plugin discovery, COMMAND validation
│   └── output_levels.py    #   Silent/quiet/normal/verbose constants + ordering
├── profile/                # (1282 lines) v2 device-profile schema, loader, type registry
│   ├── loader.py           #   load/save/validate + Profile dataclass + transport apply
│   ├── matcher.py          #   match_profile_command, template_to_regex
│   ├── schema.json         #   Canonical JSON Schema (Draft 2020-12)
│   └── types.py            #   TypeRegistry, TypeDef (enum/range/pattern/format_spec/...)
├── protocol/               # (2322 lines) Binary-protocol toolkit (library-shaped, no Textual)
│   ├── core.py             #   Format-spec parser, apply_format, FrameCollector
│   ├── crc.py              #   crcglot catalog shim (100+ algorithms via crcglot pkg) + CRC plugin registry
│   ├── runner.py           #   .pro file execution
│   └── viz.py              #   Visualizer plugin loader
├── usb/                    # (3911 lines) USB lookup tables (library-shaped)
│   ├── _vendors_full.py    #   Generated USB-IF table (fallback)
│   ├── aliases.py          #   Manufacturer-string -> short display alias
│   ├── chips.py            #   (VID, PID) -> ChipInfo (model, speed, max baud)
│   └── vendors.py          #   VID -> canonical vendor name (curated short forms)
├── app.py                  # (3876 lines) Textual TUI - UI, modals, app hooks
├── capture.py              # (422 lines)  Capture state machine - text, binary, format spec
├── cli.py                  # (1105 lines) Plain-text CLI frontend - CLITerminal + _run_cli_mode
├── config.py               # (873 lines)  Config dirs, loading, validation, migration trigger
├── credits_data.py         # (181 lines)  Dependency/attribution registry: one table behind the Help tooltip, /credits, acknowledgments.md
├── defaults.py             # (583 lines)  DEFAULT_CFG, templates, CONFIG_FIELD_HELP
├── demo.py                 # (1873 lines) Simulated device for --demo mode (FakeSerial)
├── demo_ndjson.py          # (379 lines)  NDJSON simulator variant (DEMO_JSON port)
├── demo_vt100.py           # (406 lines)  Interactive ANSI widget-tour sim (DEMO_VT100 port)
├── entry.py                #              CLI argument parsing and mode dispatch (Textual-free)
├── help_dynamic.py         # (258 lines)  Reusable helpers for callable long_help
├── history_nav.py          #              REPL Up/Down history browsing cursor (Textual-free)
├── migration.py            # (701 lines)  Config schema migration chain
├── port_control.py         # (2019 lines) Pure serial port control functions - no Textual
├── proto_debug.py          # (1185 lines) Interactive protocol debug screen (Textual)
├── repl.py                 # (2137 lines) REPL engine - dispatch, scripting, transforms
├── scripting.py            # (627 lines)  Pure functions - templates, duration parsing, ANSI, size/age/duration display (frist)
├── serial_engine.py        # (707 lines)  Serial connection lifecycle, reader loop orchestrator
├── serial_port.py          # (527 lines)  Serial I/O wrapper + SerialReader data processor
├── terminal_host.py        # (649 lines)  Shared base for TUI and CLI - builds PluginContext
├── usb_tree.py             # (553 lines)  Whole USB topology - hubs, devices, interfaces (Textual-free)
├── variables.py            #              $(NAME) namespace - storage, resolution, expansion (Textual-free)
└── vt100.py                # (173 lines)  --vt100 ANSI passthrough - raw serial <-> host terminal via miniterm
```

**Engine in core, command surface in `builtins/`.** This split recurs, and the test for it
is "who calls the engine". `port_control.py`, `port_format.py`, and `usb_tree.py` are
engines; `builtins/commands/port.py` is the command surface built on them, and
`cli_flags.py` calls the same engines directly for `--ports` / `--usb`, so they stay in
core rather than moving into the plugin. `variables.py` is the same shape: it owns the
`$(NAME)` namespace that every frontend registers into at startup and that `repl.py`
expands on the dispatch path, while `builtins/commands/var.py` is just `/var` and the
`$(NAME) = value` directive. Infrastructure core needs does not live under `builtins/`,
however natural the matching command feels.

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
        "icon": Command(help="Create a desktop launcher.", handler=_icon_handler,
            sub_commands={
                "remove": Command(help="Delete the launcher.", handler=...),
                "list": Command(help="List every launcher.", handler=...),
            }),
    },
)
```

The subcommand tree is flattened at registration into dotted names (`cfg.auto`, `cfg.icon.remove`) that the dispatch system looks up directly. The `/help` command walks the tree to show hierarchical output.

### Hooks

A **hook** is a command the host (`app.py` / `cli.py` / `mcp/server.py`) registers directly via `repl.register_hook(...)` instead of shipping as a plugin file. Hooks exist for the small set of commands that need *live frontend internals* a plugin can't reach through `PluginContext` — mounting a Textual overlay, grabbing the screenshot surface, driving a modal dialog. Examples: `ss.svg`, `run.profile.*`, `delay`, `cfg.load`, `help.open`, `log.delete`.

Hooks and plugins share one registry and one dispatch path; a hook is just a late, host-supplied entry that can override a plugin of the same name (see [Loading order](#loading-order-later-overrides-earlier)). The rule of thumb: if a command can be expressed through `PluginContext`, it's a plugin (large, portable set); if it genuinely needs Textual/host state, it's a hook (small, frontend-bound set).

### PluginContext

Every handler receives a `PluginContext`, the stable API boundary between plugins and the app. The context is a thin shell over five **handles** — four capability domains, plus `internal`, the privileged escape hatch that deliberately isn't a domain:

- **`ctx.io`** — text in/out: the level-gated `result` / `output` / `status` channels (and their Rich-markup variants) plus the always-works `notify` / `status_bar` / `log` fallbacks.
- **`ctx.serial`** — the serial connection: state (`is_connected`), lifecycle (`connect`, `disconnect`, `update_port`, `apply_port_effects`), I/O primitives (`write`, `read_raw`, `drain`, `wait_idle`, `rx_queue`), and passive `rx` / `tx` byte observers.
- **`ctx.fs`** — the filesystem layer: the config-dir folders (`ss_dir`, `scripts_dir`, `proto_dir`, `cap_dir`) and `open_file()`.
- **`ctx.ui`** — TUI-strict actions (`confirm`, `notify`, `clear_screen`, `exit_app`, `screenshot`); these raise `MissingCapability` in non-TUI frontends (CLI, MCP).
- **`ctx.internal`** — the intentional escape hatch: an internal, unstable interface (`InternalHandle`) for built-ins that need privileged frontend state (Textual, threads, pyserial handles) that can't be generified.

The remaining members (`ctx.cfg`, `ctx.dispatch`, `ctx.ns`, `ctx.plugin_cfg`, `ctx.wait_for_match`) sit directly on the context. The full surface, by handle:

```text
ctx.cfg, ctx.config_path                      # plain config (read-only mapping + path)
ctx.io.result(), ctx.io.output(), ctx.io.status()        # level-gated text channels
ctx.io.result_markup(), ctx.io.output_markup(),          # level-gated Rich-markup variants
    ctx.io.status_markup()
ctx.io.notify(), ctx.io.status_bar()          # always-works fallbacks (no capability gate)
ctx.io.log()                                  # session log
ctx.serial.is_connected                       # serial state
ctx.serial.connect(), ctx.serial.disconnect() # connection lifecycle (host-orchestrated)
ctx.serial.update_port(), ctx.serial.apply_port_effects()
ctx.serial.write(), ctx.serial.read_raw()     # serial I/O primitives
ctx.serial.drain(), ctx.serial.wait_idle(), ctx.serial.rx_queue
ctx.serial.io() (context manager)             # acquire serial for synchronous read
ctx.serial.rx_observer(), ctx.serial.tx_observer()  # passive byte taps
ctx.fs.ss_dir, ctx.fs.scripts_dir, ctx.fs.proto_dir, ctx.fs.cap_dir
ctx.fs.open_file()                            # gated by gui_apps capability
ctx.ui.confirm(), ctx.ui.notify()             # TUI-strict; raise MissingCapability in CLI
ctx.ui.clear_screen(), ctx.ui.exit_app(), ctx.ui.screenshot()
ctx.internal                                    # internal/unstable interface for built-ins
ctx.internal.port()                           # raw pyserial.Serial | None (built-ins only)
ctx.dispatch(cmd)                             # re-route a command through the pipeline
ctx.wait_for_match(predicate, timeout)        # block until serial matches (gated)
ctx.ns(name)                                  # session-scoped state dict (see below)
ctx.plugin_cfg(name)                          # per-plugin persistent config
ctx.prefix                                    # active REPL command prefix (derived from cfg)
```

**A small, fixed set of visible names** on `ctx`, replacing the earlier sprawl of flat fields. The split is by responsibility, not by syntax: four handles each own one capability domain a reader can hold in their head; the fifth, `internal`, is the deliberate exception — the privileged escape hatch for built-ins that isn't a domain.

**Capability gating.** Some handle methods are gated on `CapabilitySet` flags. `ctx.ui.confirm()` requires `confirm_dialog`; `ctx.fs.open_file()` requires `gui_apps`; `ctx.wait_for_match()` requires `block_until`. Calling a gated method in an environment that didn't grant the capability raises `MissingCapability`, which the dispatcher converts to `CmdResult.fail`. Commands declare what they need with `Command(needs=CapabilitySet(...))`; the dispatcher refuses to invoke a handler whose `needs` aren't satisfied, so most capability mismatches fail loudly *before* the handler runs.

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

Hooks are stored in a flat list in load order (`ReplEngine._lifecycle_hooks`). `fire_lifecycle(name)` filters by name and calls matching handlers in registration order, catching exceptions per-hook so one bad plugin can't prevent later hooks from running. Errors surface through `ctx.status()`.

Example use: the `seq` plugin (below) owns its counter state in `ctx.ns("seq")` and wires `on_script_start` to clear it, so scripts start with a clean counter set without `ReplEngine` knowing anything about sequence counters. This is the pattern to follow for any plugin with session-scoped state that needs lifecycle management.

### Loading order (later overrides earlier)

```text
1. builtins/commands/        - built-in command plugins (one module per command family)
2. termapy_cfg/plugin/       - user plugins (all configs on this machine)
3. termapy_cfg/<name>/plugin/ - per-config plugins (one config only)
4. App hooks (app.py/cli.py) - commands needing frontend access (ss.svg, delay, run.profile.*, etc.)
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

### Worked example: the `seq` plugin

`builtins/commands/seq.py` is a small real plugin that exercises both **session-global state** (a namespace) and **lifecycle events** in one file. It owns the `seq` namespace — the counters behind the `{seqN+}` script-template placeholders — and resets that state at the right session boundaries:

```python
from datetime import datetime
from termapy.plugins import CmdResult, Command

def _now() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

# --- lifecycle: set up / reset the shared state at session boundaries ---

def on_app_start(ctx):                 # fires once, after plugins load
    ctx.ns("seq")["_start_time"] = _now()

def on_script_start(ctx):              # fires at each OUTERMOST /run (not nested)
    seq = ctx.ns("seq")                # the shared session dict
    seq.clear()                        # fresh counters per top-level script run
    seq["_start_time"] = _now()

# --- command: read the shared state ---

def _handler(ctx, args) -> CmdResult:
    counters = {k: v for k, v in ctx.ns("seq").items() if isinstance(k, int)}
    line = ", ".join(f"seq{k}={v}" for k, v in sorted(counters.items()))
    ctx.io.output(f"Counters: {line}" if line else "No counters set.")
    return CmdResult.ok(value=line)

COMMAND = Command(name="seq", help="Print sequence counters.", handler=_handler)
```

What it demonstrates:

- **Global/session state** lives in `ctx.ns("seq")` — a plain dict shared across every call for the life of the `PluginContext`. The `{seqN+}` transform writes counters here; `_handler` and the template engine read them. No module-level globals, no monkeypatching `ctx`.
- **Lifecycle** keeps that state honest: `on_app_start` seeds it once; `on_script_start` clears it at the top of each outermost script so runs don't bleed into each other — and since nested `/run` doesn't re-fire, inner scripts inherit the outer run's counters. The engine knows nothing about sequence counters; the plugin owns the policy.

Copy this shape for any plugin that needs per-session state with setup/reset semantics. (The full file adds a `_start_time` for the `{starttime}` placeholder, a `seq.reset` subcommand, and a dynamic `long_help` that reads the live state.)

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
│  │ ScriptPicker, NamePicker, ConfirmDialog  │    │
│  └──────────────────────────────────────────┘    │
│  ┌──────────────────────────────────────────┐    │
│  │ proto_debug.py - Proto Debug Screen      │    │
│  │ Interactive send/expect with visualizers │    │
│  └──────────────────────────────────────────┘    │
│  ┌──────────────────────────────────────────┐    │
│  │ App Hooks - commands needing Textual     │    │
│  │ ss.svg, delay, cfg.load, edit, help.open │    │
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

## CLI mode (`cli.py`)

`termapy --cli` runs a plain-text terminal without Textual. It shares the same `ReplEngine`, `SerialEngine`, `PluginContext`, and all built-in plugins. The difference is how the frontend wires `PluginContext` callbacks:

| Callback             | TUI (app.py)                 | CLI (cli.py)                  |
| -------------------- | ---------------------------- | ----------------------------- |
| `ctx.io._write()`    | `RichLog.write(Text(...))`   | `Rich Console.print()`        |
| `ctx.ui.confirm()`   | Modal dialog + `event.wait()`| `input()` prompt              |
| `ctx.fs.open_file()` | `open_with_system()`         | `open_with_system()`          |
| `ctx.internal.port`  | `self.ser` (via SerialEngine)| `engine.serial_port.port`     |
| `/delay`             | `set_timer()` (non-blocking) | `time.sleep()` + progress bar |

CLI-specific features: prompt_toolkit tab completion, shared command history, `/term.color on|off` toggle. CLI limitations: no `/grep` (no scrollback buffer); `/edit.cfg` opens the system editor instead of the built-in modal editor.

## MCP mode (`mcp/server.py`)

`termapy --mcp` runs a third frontend (`MCPHost`): an MCP stdio server that exposes the same `ReplEngine`, `PluginContext`, and built-in plugins to an LLM client rather than to a human. Like CLI mode it has no Textual, so the `ctx.ui.*` handle raises `MissingCapability`; everything else (serial I/O, plugins, scripting) is the same engine the TUI uses. This is why MCP is a first-class audience for the project, not a bolt-on: an LLM drives the device through the identical command pipeline a support engineer types into.

A client discovers what a device can do from a JSON **catalog** (`mcp/catalog.py`) — the command list plus device-state resources — and invokes commands through a single `run_command` tool that dispatches them down the normal pipeline. When a v2 device **profile** is loaded (`profile/`), requests and responses are *shaped* by the profile (typed arguments in, structured fields out) instead of returned as raw text. Each cfg-with-profile is served by its own `--mcp` process; there is no hot-swap, so a client config lists one entry per device. The session log is inspectable inline with `/mcp.log.dump`. For the catalog schema, profile shaping, and the destructive-action approval gate, see `mcp/catalog.py`, `mcp/server.py`, and `help/authoring-profiles.md`.

## VT100 mode (`vt100.py`)

`termapy --vt100` is the one mode that does **not** go through `ReplEngine` / `PluginContext`. It is a raw passthrough for devices that emit terminal control beyond plain lines (cursor addressing, full-screen menus, `vi`/`top`, bootloader UIs): the surface is *already* a terminal, so the right move is to hand it the byte stream and let it emulate, rather than run an emulator. `run_vt100_mode` resolves the config (mirroring the CLI resolver), opens the raw pyserial object via the shared `open_serial` path (no `SerialEngine`, no `SerialReader`), and hands it to the vendored pyserial `Miniterm` (`vendor/serial/tools/miniterm.py`), which owns the read/write loop and enables VT processing on Windows 10+. It is Textual-free by design: dropping the TUI avoids stacking a third key-interception layer (VS Code → Textual → widget), which is why interactive VT100 is *not* built inside the TUI. miniterm is treated as a hidden implementation detail: `run_vt100_mode` replaces its banner with a termapy-branded line and disables its Ctrl-T settings menu (menu key set to an inert value, so Ctrl-T passes through to the device), so the passthrough reads as a native termapy view rather than "you dropped into pyserial." It also saves/restores `sys.stdout`/`sys.stderr` around the session, because miniterm's Windows console swaps them and never restores them -- required for re-entry across mode switches. `vt100` is a peer mode of `cli`/`tui` — selected by the `--vt100` flag or `"default_ui": "vt100"`, resolved in `entry.py`'s mode loop. It is also a reversible toggle from the TUI (`/vt100` for the current device, `/demo.vt100` for the demo), mirroring `/cli` <-> `/tui`: `app._switch_to_vt100()` sets `switch_to="vt100"` and exits; the mode loop tracks the previous mode and tells `run_vt100_mode` to return `"tui"` (so Ctrl-] comes back) when entered from the TUI, or `None` (quit) when launched via the flag. Driving such a device from an LLM is a separate, future concern (a headless emulator over MCP), deliberately not this passthrough.

To show the mode doing something a terminal-aware frontend uniquely can, `--vt100 --demo` resolves (in-memory, no disk write) to the reserved `DEMO_VT100` port, backed by `demo_vt100.py`'s `FakeSerialVT100`. It subclasses `FakeSerial` for the serial duck-type surface and overrides only the I/O: `write` decodes keystrokes (arrows/`jk`/Enter/`q`) into navigation, and `in_waiting` ticks a live redraw. Frames are absolutely positioned (`ESC[row;colH`) so they carry no `\r`/`\n` and survive the RX EOL transform.

## Key data flows

### Serial read (background thread)

```text
SerialEngine.read_loop() [background thread]
  → serial.read() → rx_queue.put(data)
  → SerialReader.process(data) → ReaderResult
    → binary capture active? → CaptureEngine.feed_bytes() → skip display
    → proto_active? → suppress display
    → decode(encoding) → split on \n → batch lines
  → callbacks (NONE may block on another thread):
               on_lines        → _rx_enqueue("lines")   ┐
               on_clear        → _rx_enqueue("clear")   │ ordered buffer +
               on_capture_done → _rx_enqueue("capdone") │ ONE post_message
               on_error        → _rx_enqueue("error")   │
               on_disconnect   → _rx_enqueue("disconnect")┘
                 → SerialRx → _on_serial_rx [main] → _write_batch → RichLog
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
├── plugin/               # user plugins (all configs)
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
    └── prof/             # /run.profile timing CSVs
```

`cfg_data_dir()` auto-creates all subdirs on access. Old folder names are auto-renamed (`captures/` → `cap/`, `scripts/` → `run/`, `plugins/` → `plugin/`).

## Config migration

Every config carries a `config_version`. On load, `migrate_config()` (`migration.py`) runs the chain in `MIGRATIONS` from the file's version up to `CURRENT_CONFIG_VERSION` — one small function per step:

```text
config_version: N  ──migrators──▶  CURRENT_CONFIG_VERSION
   vN→vN+1, vN+1→vN+2, ...     (each step is one function in MIGRATIONS)
```

A migrator does whatever a schema change needs: rename a key, nest keys under a sub-dict (`port` → `serial.port`), rewrite a renamed command verb in the `*_on_connect_cmd` chains (`/color` → `/term.color`, `/ver` → `/app.ver`), or retire a key. Two supporting tables back it: `DEPRECATED_CFG` (the reason each removed key went away, surfaced as a warning) and the legacy-command tables in `legacy.py` (so `/run.legacy` can rewrite old command names inside `.run` scripts).

The payoff — and the reason this is a core concept, not plumbing — is that schema and command changes ship freely: an old config opens, upgrades itself with a chatty per-step summary, and the user never hand-edits JSON. A config written by a *newer* termapy is detected and gets one clear upgrade hint instead of a wall of "unknown key" warnings.

## Threading model

```text
┌─────────────────────┐
│ Main thread         │  Textual event loop - all UI updates
│ (async)             │  dispatch, modals, button handlers,
│                     │  Message handlers (ScriptStarted, etc.)
├─────────────────────┤
│ reader thread       │  Long-lived; OWNED BY SerialEngine
│ engine.start_reader │  (start_reader / stop_reader), so teardown
│                     │  can Thread.join() it.  Callbacks hand off
│                     │  via _rx_enqueue and NEVER block on main.
├─────────────────────┤
│ _run_script()       │  Short-lived per script/command
│ @work(thread=True)  │  /delay, /confirm, /expect, /run are run
│                     │  here by the script runner itself -- they
│                     │  need ScriptCtx or _script_stop, not a
│                     │  particular thread.  Nested /run is inline.
├─────────────────────┤
│ _auto_reconnect()   │  Short-lived, retries connection
│ _send_test()        │  Short-lived, protocol test case
│ _run_cmds()         │  Short-lived, setup/teardown commands
└─────────────────────┘
```

At most two workers run concurrently: the serial reader plus one command/script/test worker.

That is enforced in two places, not assumed. `ReplEngine.start_script` holds
the thread id of the run in progress, so a nested `/run` (same thread, inline)
is allowed while a second launch from any other thread -- the picker, a button,
another frontend -- is refused; the rule lives in the engine because the CLI and
MCP need it as much as the TUI does. `app._acquire_dispatch_guard` serializes
individual dispatches: non-blocking on the main thread, where waiting for a
guard a worker holds would deadlock against that worker's own `call_from_thread`,
and a bounded wait off it, so a colliding line is delayed rather than dropped.

Two different hand-off mechanisms, and the difference is load-bearing. Output
helpers called from a worker (`_status`, `_set_status_bar`,
`_write_output_markup`, `_on_main`) marshal via `call_from_thread`, which
**blocks** the caller until the event loop services it. The serial reader may
not use it: blocking there deadlocks teardown (`disconnect()` joins the reader
from the very thread it would wait for) and stalls the reader long enough for
the driver to drop bytes. RX and script lifecycle therefore use `post_message`,
which is fire-and-forget.

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
carries the current count).  Representative files and what they cover:

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
| test_resolve_config.py | Config resolution chain                        |
| test_cli_gold.py       | CLI gold-standard integration test             |
| test_vfs.py            | Demo VFS: file list, info, delete, isolation   |
| test_architecture.py   | Layer boundaries checked against the source    |
| test_mcp_stdio.py      | MCP over real stdio: the wire a client speaks  |
| test_xmodem.py         | XMODEM transfer, QueueByteReader, FakeSerial   |
| test_crc_builtins.py   | sum8/sum16 checksum modules                    |
| test_ymodem.py         | YMODEM transfer, batch send, FakeSerial        |

`app.py` is driven headless by `tests/test_app_pilot.py` (Textual Pilot) and the dialogs by `tests/test_dialogs.py`; `proto_debug.py` is tested manually. The serial engine, capture, reader, and dispatch layers are fully testable using `FakeSerial`.
