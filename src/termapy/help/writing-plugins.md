# Writing plugins

Plugins are `.py` files that add REPL commands. Drop a file into a
`plugin/` folder and it loads automatically. No compilation, no
registration, no restart.

## Security model

Plugin auto-load runs arbitrary Python in-process — same trust shape
as `conftest.py`, `direnv`, `Makefile`, `pip install setup.py`, or
any other tool that reads code from a directory. **Treat
`termapy_cfg/<name>/plugin/` and `termapy_cfg/plugin/` the same way
you treat those: don't point termapy at config folders you don't
trust.**

If you need a stronger guarantee — for example when running
`--mcp` with an LLM driver, or in CI, or against a freshly cloned
project repo — set the env var:

```bash
# bash / zsh
export TERMAPY_TRUSTED_PLUGINS_ONLY=1

# PowerShell
$env:TERMAPY_TRUSTED_PLUGINS_ONLY = "1"
```

With that flag set, termapy skips **both** filesystem plugin
discovery passes (global `termapy_cfg/plugin/` and per-cfg
`termapy_cfg/<name>/plugin/`).  Only built-in commands — the ones
shipped inside the wheel, in `site-packages` — will load.  The
trust boundary collapses to "your Python environment," which is
the boundary every other Python tool already uses.

This flag lives in the environment, **not** in the cfg file.  A
hostile cfg cannot disable its own gate.

A second related gate is `TERMAPY_OS_CMD_ENABLED`: when truthy,
`/os` is allowed to spawn shell commands.  Was a cfg key
(`os_cmd_enabled`) through v0.65; same rationale forced the move
to env-var-only in v0.66.

## Quick start: copy and modify

The fastest way to write a plugin is to copy an existing one:

1. Copy `probe.py` from the demo plugins folder
2. Rename it to `your_plugin.py`
3. Change the command name, help text, and handler logic
4. Drop it into `termapy_cfg/plugin/` (all configs) or `termapy_cfg/<config>/plugin/` (one config)

## How plugins work

When `termapy` starts, it scans the `plugin/` folders for `.py` files.
Each file is imported and checked for a `COMMAND` object at module level.
If found, that command is registered in the REPL, and users can invoke it
by typing its name with the command prefix (e.g. `/hello`).

The `COMMAND` object tells `termapy`:

- **name:** what the user types to invoke it (`/name`)
- **args:** the argument syntax shown in `/help` (`{optional}` or `<required>`)
- **help:** one-line description shown in `/help`
- **handler:** the Python function to call when the command runs

## The handler function

The handler is where your plugin logic lives. It is called whenever
a user types your command in the REPL input, or when a `.run` script
contains your command. The handler receives two arguments:

- **ctx** (PluginContext): your interface to the terminal, serial port,
  config, and filesystem. This is the only API your plugin needs.
- **args** (str): everything the user typed after the command name.
  For `/hello world`, args is `"world"`. For `/hello`, args is `""`.

The handler can do anything: print output, send commands to the serial
device, read responses, write files, or chain other REPL commands.

## Plugin file structure

A minimal plugin:

```python
from termapy.plugins import Command, PluginContext

def _handler(ctx: PluginContext, args: str):
    """Called when the user types /hello."""
    name = args.strip() or "world"
    ctx.io.result(f"Hello, {name}!")

# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="hello",
    args="{name}",          # {braces} = optional, <angle> = required
    help="Say hello.",
    handler=_handler,
)
```

The `COMMAND` object must be defined after all the functions it references.
`Termapy` looks for this specific name. If your file doesn't have a
`COMMAND` object, it is silently skipped.

## The PluginContext shape

The `ctx` object is a thin shell over **five capability handles**, each
owning one domain. Plugin authors see 12 visible names on `ctx`:

```python
def _handler(ctx, args):
    if not ctx.cfg.get("encoding"):           # plain config (read-only)
        ...
    ctx.io.result("Hello", "green")           # final answer (quiet+)
    ctx.io.output("listing line")             # bulk data (normal+)
    ctx.io.status("loading...")               # progress chatter (verbose only)

    with ctx.serial.io():                     # claim serial for sync read
        ctx.serial.write(b"AT\r")
        resp = ctx.serial.read_raw()

    csv_path = ctx.fs.cap_dir / "out.csv"     # filesystem paths
    ctx.fs.open_file(csv_path)                # open in system viewer

    ctx.ui.confirm("Sure?")                   # TUI-only dialog (gated)

    ctx.dispatch("/var.set X 5")              # re-route a command
    ctx.ns("my_plugin")["counter"] += 1       # session-scoped storage

    return CmdResult.ok(value=...)
```

The five handles:

- `ctx.io` -- write to user (terminal, log, fallback notifications)
- `ctx.serial` -- read/write the serial port, observe bytes
- `ctx.fs` -- per-config directories and file opening
- `ctx.ui` -- TUI-only operations (dialogs, screenshots); raises in CLI
- `ctx.engine` -- internal SPI for built-ins; external plugins should avoid it

### Capability gating

Some handle methods are gated on `CapabilitySet` flags. Calling a gated
method without declaring the capability raises `MissingCapability`,
which the dispatcher converts to `CmdResult.fail`. The fix is to declare
what your command needs:

```python
COMMAND = Command(
    name="ask", help="Prompt user.",
    needs=CapabilitySet(confirm_dialog=True),  # refuse to dispatch in CLI
    handler=_handler,
)
```

The dispatcher refuses to invoke a handler whose `needs` aren't satisfied,
so most capability mismatches fail loudly *before* the handler runs.

Gated methods today:

- `ctx.fs.open_file` -- requires `gui_apps` (no-op in headless/SSH)
- `ctx.ui.confirm` -- requires `confirm_dialog` (implies `block_until`)
- `ctx.ui.notify` -- requires `ui_notify` (use `ctx.io.notify` for the
  always-works fallback)
- `ctx.ui.clear_screen` -- requires `tui_mode` (use `ctx.io.clear_screen`)
- `ctx.ui.screenshot` -- requires `screen_capture` (no fallback)
- `ctx.ui.exit_app` -- requires `tui_mode`
- `ctx.wait_for_match` -- requires `block_until`

## Returning scriptable values

Handlers return `CmdResult` to indicate success or failure:

- `CmdResult.ok(value="...")` -- success, `value` is available to scripts
- `CmdResult.ok(value="")` -- success, no scriptable data (pure side-effect)
- `CmdResult.fail(msg="...")` -- failure with error message

**`value=` is required on `CmdResult.ok()`.** Pass the data your command
produces (a path, a count, a parsed response, a toggle state).  For pure
side-effect commands (`/cls`, `/exit`) pass `value=""` explicitly so the
"no scriptable data" intent is visible at the call site.

**Paths auto-resolve to absolute strings.** If `value` is a `pathlib.Path`,
the constructor calls `str(path.resolve())` for you, so handlers can
write `CmdResult.ok(value=path)` directly -- no manual `str()` /
`.resolve()` boilerplate.

Scripts run in quiet mode (or via `$(VAR) <- /cmd`) read the `value`
field; the type checker catches missing-`value=` calls so a "I forgot"
gap can't ship silently.

```python
def _handler(ctx: PluginContext, args: str):
    temp = read_temperature()
    ctx.io.result(f"Temperature: {temp}C")
    return CmdResult.ok(value=str(temp))
```

Examples that should set `value=`:

- Query commands (`/port.baud_rate` returns `"115200"`)
- State toggles (`/echo on` returns `"on"`)
- Computed values (`/proto.crc.calc` returns the CRC)
- Ping timings, version strings, variable values

Examples that should not:

- Pure side-effect commands (`/cls`, `/edit`, `/cap.stop`)
- Commands that print multiple lines (`/cfg.configs`, `/help`)

## Dynamic help for runtime state

If your command owns runtime state that a user should see right on its
help page - a loaded file, an open connection, a count of cached items -
set `long_help` to a function instead of a string. The function takes
the `PluginContext` and returns a string. It's invoked at render time,
so whatever it reads from `ctx.ns(...)` or `ctx.cfg` is live.

```python
def _dynamic_long_help(ctx):
    profile = ctx.ns("active_profile") or {}
    commands = profile.get("commands") or {}
    if commands:
        rev = profile.get("profile_revision") or "(none)"
        state = (
            f"Active profile: {len(commands)} device command(s) "
            f"(rev {rev})."
        )
    else:
        state = "No profile loaded."
    return f"""{state}

Load a device profile with /profile.load <path>, or fetch one from
the connected device with /profile.load cmd=<command>."""

COMMAND = Command(
    name="profile",
    help="Device profile commands.",
    long_help=_dynamic_long_help,   # a function, not a string
    handler=_handler,
)
```

When the user runs `/help include`, the DESCRIPTION section calls this
function and the first line reflects the current state. No change to
the rendering path, no extra registration - the `long_help` field just
accepts either form.

Two caveats:

1. **Read ctx defensively.** Use `ctx.ns("x").get("k", default)`
   rather than indexing blindly. Help may be invoked at any moment,
   including before your plugin's state is populated.
2. **Never raise.** The renderer catches exceptions and substitutes
   `(dynamic help failed: <error>)` so `/help` never crashes, but a
   noisy fallback is worse than a thoughtful default like
   `"(not loaded)"`.

### Reusable helpers (`termapy.help_dynamic`)

Most dynamic help lines fall into a handful of shapes, so the built-ins
share a small helper module. Prefer these over hand-rolling - the
output is green-on-default and uniform across every command.

```python
from termapy.help_dynamic import (
    state_line,   # "Current <label> = <value>" in green
    folder_line,  # "<N> files in <folder>/" in green
    port_status,  # "Connected: COM3 @ 115200 8N1" or "Not connected"
    cfg_status,   # "Active cfg = demo (2 configs available)"
    ns_count,     # len(ctx.ns(name)), guards a missing ns
    compose,      # join non-empty parts with a blank line between
    green,        # wrap any text in green markup
)

def _long_help(ctx):
    return compose(
        folder_line(ctx, "run", noun="script"),
        "Run a .run script from the run/ folder.",
    )
```

`compose` drops empty parts, so a callable that returns `""` for
"no state yet" collapses gracefully. For a single-value command
that needs no prose, you can pass the helper directly:

```python
"baud_rate": Command(
    help="Show or set baud rate.",
    long_help=lambda ctx: state_line("baud rate", ctx.cfg["serial"]["baud_rate"]),
    handler=_baud_handler,
),
```

## Serial I/O pattern

Most plugins follow this pattern: send a command, read the response, do something with it.

```python
def _handler(ctx: PluginContext, args: str):
    if not ctx.serial.is_connected():
        return CmdResult.fail(msg="Not connected.")

    encoding = ctx.cfg.get("encoding", "utf-8")
    line_ending = ctx.cfg.get("line_ending", "\r")

    with ctx.serial.io():                # suppress terminal, claim serial
        ctx.serial.drain()               # discard stale bytes
        ctx.serial.write(f"YOUR_COMMAND{line_ending}".encode(encoding))
        raw = ctx.serial.read_raw()      # read response with timeout
        text = raw.decode(encoding, errors="replace").strip()

    ctx.io.result(text)
    return CmdResult.ok(value=text)
```

Key points:

- `ctx.serial.io()` suppresses the normal terminal display during I/O
- `ctx.serial.drain()` clears any leftover bytes before your command
- `ctx.serial.write()` sends raw bytes; you add the line ending
- `ctx.serial.read_raw()` waits for a complete response (timeout-based framing)

Even cleaner: declare the connection requirement on `Command` and let
the dispatcher gate the call:

```python
COMMAND = Command(
    name="hello", help="Say hello to the device.",
    needs=CapabilitySet(serial_connected=True),
    handler=_handler,
)
```

The handler then doesn't need to check `is_connected` -- the dispatcher
returns `Not connected.` automatically when the port is down.

## Useful library imports

Beyond `termapy.plugins`, three pyserial-adjacent subpackages are
available for plugins that need typed device contracts, binary
protocol parsing, or USB chip lookup.  Each is self-contained
(no Textual / no engine deps) and re-exported via its package's
`__init__`:

```python
# Device profile schema, loader, type registry (validate typed args,
# resolve profile-local types, walk a v2 profile dict).
from termapy.profile import (
    TypeRegistry, load_profile, validate_profile, profile_command_view,
)

# Binary protocol toolkit (format-spec parser, CRC catalog, .pro runner,
# visualizer loader).  See protocol-testing.md for the format-spec
# language and serial-tools.md for the CRC catalogue.
from termapy.protocol import (
    parse_format_spec, apply_format, FrameCollector, get_crc_registry,
)

# USB lookup tables (VID -> vendor, VID:PID -> chip + max baud,
# manufacturer-string -> short alias).  Pure data + lookup helpers;
# pyserial gives you the VID/PID/manufacturer, these add value on top.
from termapy.usb import chip, vendor_for, mfg
```

Each subpackage's `__init__.py` carries a library-usage docstring
with a worked example.  Treat them as importable libraries: a plugin
that crunches binary frames doesn't need to vendor a CRC catalogue
itself.

## PluginContext API reference

### Output (`ctx.io`)

All handler output flows through three semantic channels gated by the
session's output level (`silent`/`quiet`/`normal`/`verbose`).  Use the
plain methods for unstructured text (with optional color) and the
`_markup` variants when the text contains Rich markup tags like
`[bold red]Warning![/]`.

| Method | Shown at | Use for |
| --- | --- | --- |
| `ctx.io.result(text, color)` | quiet+ | The command's final answer (single line). |
| `ctx.io.output(text, color)` | normal+ | Bulk data: listings, dumps, file contents. |
| `ctx.io.status(text)` | verbose only | Progress chatter, debug-y notes. |
| `ctx.io.result_markup(text)` | quiet+ | Like `result()` but text is Rich markup. |
| `ctx.io.output_markup(text)` | normal+ | Like `output()` but text is Rich markup. |
| `ctx.io.status_markup(text)` | verbose only | Like `status()` but text is Rich markup. |
| `ctx.io.notify(text)` | always | Notification (toast in TUI, plain print in CLI). |
| `ctx.io.status_bar(text)` | always | Status-bar update (auto-no-op in CLI/MCP). |
| `ctx.io.clear_screen()` | always | Screen clear (auto-no-op outside TUI). |
| `ctx.io.log(prefix, text)` | always | Session log (`">"` TX, `"<"` RX, `"#"` status). |

**Errors** flow through `return CmdResult.fail(msg=...)` -- the dispatcher
paints the red error line.  Do not write red error text via the output
channels.

**Forbidden in handler code**: `ctx.io._write` and `ctx.io._write_markup`
(the underscore-prefixed primitives that bypass level gating) are
engine-internal.  A CI grep guard fails the build if a builtin calls
either directly.

### Config

| Member | Description |
| --- | --- |
| `ctx.cfg` | Read-only config mapping |
| `ctx.config_path` | Path to the `.cfg` file |
| `ctx.cfg.get("key", default)` | Read a config value |

### Serial port (`ctx.serial`)

| Member | Description |
| --- | --- |
| `ctx.serial.is_connected()` | Returns `True` if the serial port is open |
| `ctx.serial.port()` | The raw pyserial object, or `None` when disconnected |
| `ctx.serial.io()` | Context manager for exclusive serial access |
| `ctx.serial.drain()` | Discard stale bytes in the receive buffer |
| `ctx.serial.write(data)` | Send raw bytes (no line ending added) |
| `ctx.serial.read_raw(timeout_ms)` | Read response bytes with timeout framing |
| `ctx.serial.wait_idle(timeout_ms)` | Wait for serial output to settle |
| `ctx.serial.wait_for_data(timeout_ms)` | Block until at least one byte arrives |
| `ctx.serial.rx_observer(cb)` | Context manager: passive RX byte tap |
| `ctx.serial.tx_observer(cb)` | Context manager: passive TX byte tap |

### Filesystem (`ctx.fs`)

| Member | Description |
| --- | --- |
| `ctx.fs.ss_dir` | Screenshots directory (Path) |
| `ctx.fs.scripts_dir` | Scripts directory (Path) |
| `ctx.fs.proto_dir` | Protocol test scripts directory (Path) |
| `ctx.fs.cap_dir` | Captures directory (Path) |
| `ctx.fs.prof_dir` | Profile output directory (Path) |
| `ctx.fs.open_file(path)` | Open in system viewer/editor (gated on `gui_apps`) |

### TUI-only (`ctx.ui`)

These raise `MissingCapability` when called from CLI without declaring
the matching capability on `Command.needs`.

| Member | Capability gate |
| --- | --- |
| `ctx.ui.confirm(message)` | `confirm_dialog` (implies `block_until`) |
| `ctx.ui.notify(text)` | `ui_notify` |
| `ctx.ui.status_bar(text)` | `status_bar` |
| `ctx.ui.clear_screen()` | `tui_mode` |
| `ctx.ui.screenshot(path)` | `screen_capture` |
| `ctx.ui.get_screen_text()` | `screen_capture` |
| `ctx.ui.exit_app()` | `tui_mode` |

### Top-level

| Member | Description |
| --- | --- |
| `ctx.dispatch(cmd)` | Run a REPL or serial command through the full pipeline |
| `ctx.wait_for_match(predicate, timeout)` | Block until serial matches (gated on `block_until`) |
| `ctx.ns(name)` | Get/create a session-scoped state dict |
| `ctx.plugin_cfg(name)` | Get a per-plugin persistent config dict |
| `ctx.is_oneshot()` | True when running under `--exec` (one-shot CLI mode) |
| `ctx.engine` | Internal SPI for built-ins; external plugins should avoid |

## Subcommands

Use `sub_commands` for related operations (e.g. `/tool.run`, `/tool.status`):

```python
COMMAND = Command(
    name="tool",
    help="A tool with subcommands.",
    sub_commands={
        "run":    Command(args="<file>", help="Run.", handler=_run),
        "status": Command(help="Show status.", handler=_status),
    },
)
```

## Example plugins

The demo config ships with four plugins of increasing complexity:

- **cmd.py:** minimal. Wraps a single AT command in a custom name.
- **probe.py:** intermediate. Send/receive cycle with formatted output, good starting template.
- **temp_plot.py:** advanced. Repeated sampling, response parsing, ASCII sparkline visualization.
- **traffic.py:** advanced. RX/TX byte tap (`/traffic.count`, `/traffic.hexdump`, `/traffic.rate`, `/traffic.snoop`); demonstrates `ctx.serial.rx_observer()` / `ctx.serial.tx_observer()` context managers for passive monitoring without disrupting the normal pipeline.

`temp_plot.py` is the best example for plugins that *send and parse* a single
device response.  It shows:

- Declaring `needs=CapabilitySet(serial_connected=True)` for connection gating
- Reading config for encoding and line ending
- Using `ctx.serial.io()` for a multi-read loop
- Parsing numeric values from device responses
- Handling edge cases (no data, invalid count)
- Rendering results with Rich markup

`traffic.py` is the best example for plugins that *watch* the byte stream
without disrupting normal operation.  It shows:

- Using `ctx.serial.rx_observer(cb)` and `ctx.serial.tx_observer(cb)` as
  the canonical passive-tap pattern -- observers are released on every
  exit path including exceptions, no try/finally needed
- Coordinating an observer with a `threading.Event` for "wait for X"
  semantics (the snoop subcommand)
- Letting normal device output continue flowing through the pipeline
  while traffic is also being measured / logged in the background

Run `/temp_plot` and `/traffic.count AT+VER` in demo mode to see them
in action, then read the source.

## Using AI coding tools

`temp_plot.py` was generated in one shot by Claude Code with full
project context. If you use an AI coding assistant with access to the
termapy source, describing what you want often produces a working
plugin on the first try. The key is that the AI can see `probe.py`,
the device protocol, and the PluginContext API all at once.

Without full project context, expect to iterate. The serial I/O timing
and response parsing are device-specific and hard to get right from an
API reference alone.

For more on how termapy itself was built with LLM tooling, see
[On AI assistance](on-ai-assistance.md).
