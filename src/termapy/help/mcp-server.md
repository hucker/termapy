# MCP server

Termapy can run as an [MCP](https://modelcontextprotocol.io) (Model Context Protocol) server, letting an LLM client like Claude drive your serial device through a typed JSON tool surface.

Bare form -- auto-discovers a single `.cfg` under `./termapy_cfg/` (or the OS config dir):

```sh
termapy --mcp
```

Explicit form -- point at a specific cfg file (recommended for MCP client configs, where cwd is non-deterministic):

```sh
termapy --mcp termapy_cfg/mydevice/mydevice.cfg
```

Either way, the server starts a stdio loop bound to one device.  No interactive UI; it speaks MCP frames on stdin/stdout and exposes one tool, `run_command`, for the client to call.

## Architecture: one server per device

Each cfg is its own server process.  Two devices on the bench means two `termapy --mcp` processes -- different ports, different profiles, never conflict.  No hot-swap; if you want a different device, point a different MCP client entry at a different cfg.

Requires the `[mcp]` install extra (or `[all]`).  See [Installation](installation.md).

---

## Part 1: Setting up termapy for a device

A "device setup" is a [config folder](getting-started.md) that holds everything termapy needs to talk to one specific piece of hardware.  For MCP use, two files matter:

| File                  | Purpose                                                                            | Required?   |
| --------------------- | ---------------------------------------------------------------------------------- | ----------- |
| `<name>.cfg`          | Port, baud rate, line ending, etc.                                                 | Yes         |
| `<name>.profile.json` | Command schema: how requests are sent and how responses are parsed into typed JSON | Recommended |

Both files live at the top of the cfg folder, alongside `<name>.log` and `<name>.history`.

### The cfg file

Standard termapy [config](config.md).  The MCP-relevant slice (partial example -- see the [field reference](config.md) for the rest):

```json
{
    "serial": {
        "port": "COM4",
        "baud_rate": 115200
    },
    "line_ending": "\r\n",
    "auto_connect": true,
    "profile_path": "",
    "mcp_on_connect_cmd": ""
}
```

`profile_path` (default empty) lets you point at a profile in a non-conventional location.  Empty means "look for `<cfg_dir>/<cfg_name>.profile.json`" -- the convention.  `mcp_on_connect_cmd` runs a command automatically when the MCP server connects (the MCP counterpart of `tui_on_connect_cmd` / `cli_on_connect_cmd`).

### The profile file

A device profile declares what commands the device understands and how its responses should be parsed.  Without a profile, MCP `run_command` works but returns raw text (legacy fallthrough).  With a profile, it returns typed JSON like `{"celsius": 23}`.

The authoritative, machine-readable schema ships inside the package as [`profile/schema.json`](https://github.com/hucker/termapy/blob/main/src/termapy/profile/schema.json) -- point an LLM at it (or feed it into your MCP client's context) to draft, audit, or validate profiles automatically.

Minimal example -- one parameter-less command and one with an enum-typed parameter:

```json
{
    "profile_version": 2,
    "profile_revision": "1.0.0",
    "profile_date": "2026-07-03",
    "device": {"name": "MyDevice", "vendor": "ACME"},
    "types": {
        "on_off": {
            "kind": "enum",
            "values": ["on", "off"],
            "help": "LED state: on or off."
        }
    },
    "commands": {
        "temp": {
            "help": "Read board temperature in Celsius.",
            "safety": "readonly",
            "response": {
                "format": "regex",
                "pattern": "Temperature:\\s+(?P<celsius>-?\\d+)\\s*C",
                "types": {"celsius": "int"},
                "timeout_ms": 300
            }
        },
        "set_led": {
            "help": "Turn the status LED on or off.",
            "safety": "mutable",
            "send_template": "led {state}",
            "typed_args": [
                {"name": "state", "type": "on_off", "required": true}
            ],
            "response": {
                "format": "literal",
                "pattern": "OK",
                "timeout_ms": 200
            }
        }
    }
}
```

`temp` is parameter-less: the LLM calls `run_command("temp")`, termapy sends `temp\r\n` and waits for a reply like `Temperature: 23 C`.  The regex skips the prefix and unit, extracts the digits, the `types` map coerces them to `int`, and the call returns `value={"celsius": 23}`.

`set_led` takes one argument: the LLM calls `run_command("set_led", state="on")`, termapy formats the wire bytes via `send_template` (`led on\r\n`), waits for the literal `OK` reply, and returns success.  The `types` block declares the reusable `on_off` enum; `typed_args` references it, so the LLM sees `state: "on" | "off"` in the tool definition.  `safety: mutable` means it changes device state but doesn't trigger the destructive-confirmation gate.

Full schema, response formats (`none`, `literal`, `lines`, `regex`, `json`), the four-tier safety taxonomy (`safe` / `readonly` / `mutable` / `destructive`), and the request/response executor are documented in [Authoring profiles](authoring-profiles.md).

### Lifecycle on connect / disconnect

When the MCP server connects to its port, it auto-loads the profile from `profile_path` or the convention path.  On disconnect, device-specific state (active profile, catalog, event buffers) is wiped, so reconnecting cleanly re-loads the profile.

### What an MCP client can and can't reach

The MCP server runs with no interactive session, so termapy's capability gate blocks a whole class of host access that only makes sense for a human at a terminal:

- **Shell escape (`/os`)** is unreachable under MCP (needs an interactive session) *and* separately gated by `TERMAPY_OS_CMD_ENABLED`.
- **File viewers / editors / explorers** (`/show <path>`, `/edit`, `/cfg.explore`, the folder `.show`/`.explore` verbs) are unreachable -- they need a local desktop.
- **Capture reads** (`termapy://capture/<name>`) and **`.dump` commands** (`/cap.dump`, `/run.dump`, ...) are contained to the config folder; an absolute path or `../` is refused.  Reading files elsewhere is what `/show` is for, and `/show` is gated off.
- **Environment access is off under MCP by default.**  `/env` (which would dump every variable) and the `$(env.NAME)` expansion are refused unless you opt in with `TERMAPY_MCP_ENV_ENABLED=1` in the server's shell -- env vars routinely hold secrets, and an MCP peer is remote/automated.  Like `/os`, the flag lives in the environment, not the cfg, so a cfg can't grant itself the read.
- **Destructive device commands** (profile `safety: destructive`) refuse to run without an explicit `confirm=true`, which a well-behaved client elicits from the user.

Wire-level device control (send bytes, read responses, run profiled commands) is of course available -- that's the point.  The gates above are about *host* access, not device access.

---

## Part 2: Hooking termapy into an MCP client

The MCP client is the program the user talks to (Claude Code, Claude Desktop, VS Code, etc.).  It launches `termapy --mcp` as a child process and routes the LLM's tool calls to it.

### Claude Code

```sh
claude mcp add --scope user termapy-mydevice -- termapy --mcp C:/path/to/termapy_cfg/mydevice/mydevice.cfg
```

Use `--scope user` (or `--scope project` for a team-shared `.mcp.json`).  The default `local` scope registers the server for the current directory only and it silently stops loading after some restarts -- if the tool vanishes, re-add with an explicit scope.

### Claude Desktop

Edit `%APPDATA%\Claude\claude_desktop_config.json` (Windows) or `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
    "mcpServers": {
        "termapy-mydevice": {
            "command": "termapy",
            "args": ["--mcp", "C:/path/to/termapy_cfg/mydevice/mydevice.cfg"]
        }
    }
}
```

Restart Claude Desktop.  The tool `mcp__termapy-mydevice__run_command` shows up; the LLM can now drive the device.

For multiple devices, add more entries -- one per cfg.  The key (`termapy-mydevice`) is the tool name prefix the LLM will see.

### VS Code

Native MCP support uses `.vscode/mcp.json` (workspace-scoped) or user settings (global):

```json
{
    "servers": {
        "termapy-mydevice": {
            "command": "termapy",
            "args": ["--mcp", "termapy_cfg/mydevice/mydevice.cfg"]
        }
    }
}
```

Workspace-scoped is nice for team projects -- commit `.vscode/mcp.json` and everyone who clones gets the same MCP wiring.

### Running from a dev clone

If you're developing termapy itself (or want to test changes without releasing), point the client at `uv run` and set `cwd`:

```json
{
    "mcpServers": {
        "termapy-mydevice": {
            "command": "uv",
            "args": ["run", "termapy", "--mcp", "termapy_cfg/mydevice/mydevice.cfg"],
            "cwd": "/path/to/termapy/repo"
        }
    }
}
```

Edit-restart-test cycle: edit termapy source, restart the MCP client, the new code runs.

---

## Troubleshooting

| Symptom                                        | Likely cause                                                                                                                                                             |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Client shows no termapy tool                   | MCP client config not reloaded -- restart the client.  In Claude Code, also check the registration scope (see above)                                                     |
| `--mcp requires the 'mcp' optional dependency` | Missing extra: `uv tool install "termapy[all]"`                                                                                                                          |
| `run_command` returns raw text, not typed JSON | No profile loaded; legacy fallthrough is sending bytes but not shaping the response.  Check `/mcp.info` for the profile revision; if blank, the profile didn't auto-load |
| Profile doesn't auto-load on connect           | Check the convention path (`<cfg_dir>/<cfg_name>.profile.json`) or set `profile_path` explicitly.  The session log will tell you                                         |
| Need to see what the server is doing           | Add `--mcp-verbose` to args; tees the session log to stderr                                                                                                              |

The session log lives at `<cfg_dir>/mcp/session.log`.  Every dispatch, every TX/RX, every error is recorded there.  Open it with `/mcp.log` (system viewer), `/mcp.log.dump` (inline), or `/mcp.log.path` (just the path).

## See also

- [Authoring profiles](authoring-profiles.md) -- response formats, safety tiers, the request/response executor
- [Installation](installation.md) -- the `[mcp]` and `[all]` extras
- [Configuration](config.md) -- `profile_path`, `mcp_on_connect_cmd`, and the rest of the cfg field reference
- [REPL commands](commands.md) -- `/mcp.*` and `/profile.*` work in the TUI/CLI too, useful for inspection and validation
