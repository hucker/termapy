# Writing Plugins

Plugins are `.py` files that add REPL commands. Drop a file into a
`plugins/` folder and it loads automatically — no compilation, no
registration, no restart.

## Quick Start: Copy and Modify

The fastest way to write a plugin is to copy an existing one:

1. Copy `probe.py` from the demo plugins folder
2. Rename it to `your_plugin.py`
3. Change the command name, help text, and handler logic
4. Drop it into `termapy_cfg/plugins/` (all configs) or `termapy_cfg/<config>/plugins/` (one config)

## How Plugins Work

When `termapy` starts, it scans the `plugins/` folders for `.py` files.
Each file is imported and checked for a `COMMAND` object at module level.
If found, that command is registered in the REPL — users can invoke it
by typing its name with the command prefix (e.g. `/hello`).

The `COMMAND` object tells `termapy`:

- **name** — what the user types to invoke it (`/name`)
- **args** — the argument syntax shown in `/help` (`{optional}` or `<required>`)
- **help** — one-line description shown in `/help`
- **handler** — the Python function to call when the command runs

## The Handler Function

The handler is where your plugin logic lives. It is called whenever
a user types your command in the REPL input, or when a `.run` script
contains your command. The handler receives two arguments:

- **ctx** (PluginContext) — your interface to the terminal, serial port,
  config, and filesystem. This is the only API your plugin needs.
- **args** (str) — everything the user typed after the command name.
  For `/hello world`, args is `"world"`. For `/hello`, args is `""`.

The handler can do anything: print output, send commands to the serial
device, read responses, write files, or chain other REPL commands.

## Plugin File Structure

A minimal plugin:

```python
from termapy.plugins import Command, PluginContext

def _handler(ctx: PluginContext, args: str):
    """Called when the user types /hello."""
    name = args.strip() or "world"
    ctx.write(f"Hello, {name}!")

# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="hello",
    args="{name}",          # {braces} = optional, <angle> = required
    help="Say hello.",
    handler=_handler,
)
```

The `COMMAND` object must be defined after all the functions it references.
`Termapy` looks for this specific name — if your file doesn't have a
`COMMAND` object, it is silently skipped.

## Serial I/O Pattern

Most plugins follow this pattern: send a command, read the response, do something with it.

```python
def _handler(ctx: PluginContext, args: str):
    if not ctx.is_connected():
        ctx.write("Not connected.", "red")
        return

    encoding = ctx.cfg.get("encoding", "utf-8")
    line_ending = ctx.cfg.get("line_ending", "\r")

    with ctx.serial_io():           # suppress terminal, claim serial
        ctx.serial_drain()          # discard stale bytes
        ctx.serial_write(f"YOUR_COMMAND{line_ending}".encode(encoding))
        raw = ctx.serial_read_raw() # read response with timeout
        text = raw.decode(encoding, errors="replace").strip()

    ctx.write(text)
```

Key points:

- `serial_io()` suppresses the normal terminal display during I/O
- `serial_drain()` clears any leftover bytes before your command
- `serial_write()` sends raw bytes — you add the line ending
- `serial_read_raw()` waits for a complete response (timeout-based framing)

## PluginContext API Reference

### Output

| Method | Description |
| --- | --- |
| `ctx.write(text, color)` | Print to terminal. Color: `"red"`, `"green"`, `"cyan"`, `"dim"`, etc. |
| `ctx.write_markup(text)` | Print Rich markup (e.g. `[bold red]Warning![/]`) |
| `ctx.notify(text)` | Show a toast notification |
| `ctx.clear_screen()` | Clear the terminal |

### Config

| Method | Description |
| --- | --- |
| `ctx.cfg` | Read-only config dict |
| `ctx.config_path` | Path to the `.cfg` file |
| `ctx.cfg.get("key", default)` | Read a config value |

### Serial I/O

| Method | Description |
| --- | --- |
| `ctx.is_connected()` | True if the serial port is open |
| `ctx.serial_io()` | Context manager for exclusive serial access |
| `ctx.serial_drain()` | Discard stale bytes in the receive buffer |
| `ctx.serial_write(data)` | Send raw bytes (no line ending added) |
| `ctx.serial_read_raw()` | Read response bytes with timeout framing |
| `ctx.serial_wait_idle()` | Wait for ~400ms of silence |

### Filesystem

| Method | Description |
| --- | --- |
| `ctx.ss_dir` | Screenshots directory (Path) |
| `ctx.scripts_dir` | Scripts directory (Path) |
| `ctx.proto_dir` | Protocol test scripts directory (Path) |
| `ctx.cap_dir` | Captures directory (Path) |
| `ctx.prof_dir` | Profile output directory (Path) |

### Other

| Method | Description |
| --- | --- |
| `ctx.dispatch(cmd)` | Run a REPL or serial command |
| `ctx.confirm(message)` | Yes/Cancel dialog → bool (background thread only) |
| `ctx.log(prefix, text)` | Write to session log (`">"` TX, `"<"` RX, `"#"` status) |

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

## Example Plugins

The demo config ships with three plugins of increasing complexity:

- **cmd.py** — minimal: wraps a single AT command in a custom name
- **probe.py** — intermediate: send/receive cycle with formatted output, good starting template
- **temp_plot.py** — advanced: repeated sampling, response parsing, ASCII sparkline visualization

`temp_plot.py` is the best example for real-world plugin development. It shows:

- Checking connection before I/O
- Reading config for encoding and line ending
- Using `serial_io()` for a multi-read loop
- Parsing numeric values from device responses
- Handling edge cases (no data, invalid count)
- Rendering results with Rich markup

Run `/temp_plot` in demo mode to see it in action, then read the source.

## Using AI Coding Tools

`temp_plot.py` was generated in one shot by Claude Code with full
project context. If you use an AI coding assistant with access to the
termapy source, describing what you want often produces a working
plugin on the first try. The key is that the AI can see `probe.py`,
the device protocol, and the PluginContext API all at once.

Without full project context, expect to iterate — the serial I/O timing
and response parsing are device-specific and hard to get right from an
API reference alone.
