# MCP server

> **Status: preview.** This works and is used daily, but it is still being developed: its commands, files and behavior may change in any release.

Termapy can run as an [MCP](https://modelcontextprotocol.io) (Model Context Protocol) server, letting an LLM client like Claude drive your serial device through a typed JSON tool surface.

With one config in your project's `termapy_cfg/`, the server needs no arguments:

```sh
termapy --mcp
```

Or name the config it starts on:

```sh
termapy --mcp mydevice
```

Either way, the server starts a stdio loop that drives one device at a time.  No interactive UI; it speaks MCP frames on stdin/stdout and exposes one tool, `run_command`, for the client to call.

Requires the `[mcp]` install extra (or `[all]`).  See [Installation](installation.md).

## Which setup do you have?

One config is the simple case.  The rest of this section only matters once a project has more.

| Your project | What to do | What happens |
| --- | --- | --- |
| One config | Nothing | The server starts on it |
| Several configs, one you usually want | Name it `mcp_<anything>` | The server starts on it, and an agent can still load the others |
| Several configs, no usual one | Nothing | The server starts with none, and the agent is told what it can load |
| A server that starts on a config of your choice | `--mcp mydevice` | The server starts on it, and an agent can still load the others |
| Two devices at once | Two servers | See [Two devices at once](#two-devices-at-once) |

Starting with no config is not an error.  The agent is told on connecting which config it has or why it has none, and what it can load, and a `No config loaded.` reply lists the choices too.

## How it finds its config

The command line comes first, and everything else is convenience for when you don't say.

**The folder,** first match wins:

1. `--cfg-dir <folder>`
2. `TERMAPY_CFG_DIR`
3. The folder of a config named by its path (`--mcp C:/work/termapy_cfg/mydevice/mydevice.cfg`)
4. The first of these that exists, in the project's folder: `./termapy_cfg`, `./src/termapy_cfg`, `./source/termapy_cfg`

A `--cfg-dir` or `TERMAPY_CFG_DIR` that does not exist stops the server with an error.  With none of the rest found the project has no configs: the server does not fall back to the OS config folder, so one enabled for an unrelated project sees nothing of your others.

**The config it starts on,** first match wins:

| Found | Starts on |
| --- | --- |
| A config named at launch (`--mcp left`) | That one.  If it does not exist the server exits with an error, and tries nothing else.  If its port fails it is not connected, and tries nothing else |
| Exactly one config | That one |
| Exactly one named `mcp_*` | That one |
| Several named `mcp_*` | The first in name order whose port opens; with none open, the first, not connected |
| Several, none named `mcp_*` | None |
| No configs | None |

A config is `<name>/<name>.cfg`.  Another `.cfg` in that folder does not count.

**While running,** an agent can load any config in the folder by its bare name, `/cfg.load mydevice`, however the server started, including to reload one you just edited.  A path or any other file is refused.  Those configs belong to the project, but loading one runs its plugins and on-connect commands and opens its port, and `/port` can move a session to another port, so an agent can pick the wrong device.  When that matters, tell it which to use.

## When the server runs, and what it is called

- **The name is yours.**  It is the name you gave `claude mcp add` or the key in `.mcp.json`, and the agent's tool is built from it: `mcp__termapy-a__run_command`.  It selects nothing: `termapy-a` starts on a config only if its arguments name one or the rules above choose it, and two entries with the same command are two servers that follow the same rules.
- **It starts with the session.**  Claude Code starts its own copy of each enabled server when a session starts, before any tool is used, and stops it when the session ends.
- **A server holds its port while it runs,** even with the agent idle, and nothing else can open that port on any OS.  So the TUI gets "port busy" on that device, and a second chat in the same project starts a second server that cannot open it either.  Disable the server in `/mcp` to get the port back.
- **The project is where the session started:** the workspace folder in VS Code, or the folder you ran `claude` in.  From `src/`, the project is `src/`, so `./termapy_cfg` means `src/termapy_cfg`.  Claude Code passes this as `CLAUDE_PROJECT_DIR`; with another client it is the working folder that client gave the server.  A client with no project, like Claude Desktop, should name a config by its full path.
- **Enable and Disable in `/mcp`** are remembered per project.  Enabling also starts the server in the session you are in, with no restart, and later sessions start it by themselves.  On Windows, VS Code and a terminal `claude` can count as different projects, because they spell the drive letter differently (`c:` and `C:`), so enabling in one may not carry to the other.  A server that exits, as it does on a missing named config, is marked failed; **Reconnect** in `/mcp` starts it again.

## Two devices at once

A server drives one device at a time, so two at once take two servers.

**Two free servers,** for debugging two ports when which two keeps changing.  Register two entries that name no config:

```sh
claude mcp add --scope user termapy-a -- termapy --mcp
claude mcp add --scope user termapy-b -- termapy --mcp
```

In a project with several configs and no `mcp_*`, each starts with no config and holds no port, so your TUI keeps its devices until an agent needs one.  Then tell the agent which goes where ("`demo` on termapy-a, `pico` on termapy-b"), and each loads its own with `/cfg.load`.  If one is told to load a config whose port the other holds, the load succeeds, the reply says `Cannot open ...`, and `/port.connect` works once the other lets go.  In a project with one config or one `mcp_*`, both start on it: the first gets the port, and the second is not connected until it loads another.

**Named servers,** when each server normally means one device.  Name its config in a project `.mcp.json`, one entry per device, with no absolute paths:

```json
{
    "mcpServers": {
        "termapy-left": { "command": "termapy", "args": ["--mcp", "left"] },
        "termapy-right": { "command": "termapy", "args": ["--mcp", "right"] }
    }
}
```

Each server starts on its config, so `termapy-left` means the left device until an agent loads another config or moves its port.

**The chain,** a shortcut with no agent or entry to set up: name the configs `mcp_left` and `mcp_right` and give both servers a bare `--mcp`.  The first takes `mcp_left`, and the second finds that port busy and moves on to `mcp_right`.  Be careful with it, because a port opening does not mean it is the device you wanted:

- Which server gets which device follows start order, so it can change between restarts.
- If `mcp_left`'s device is unplugged, a server lands on `mcp_right`'s.
- A second chat in the project starts more servers, which take whatever is free.

Each config the chain tries is loaded before its port is, plugins included.  When an agent must know which server is which device, name the configs at launch.

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
    "eol": "\r\n",
    "auto_connect": true,
    "profile_path": "",
    "mcp_on_connect_cmd": ""
}
```

`profile_path` (default empty) lets you point at a profile in a non-conventional location.  Empty means "look for `<cfg_dir>/<cfg_name>.profile.json`" -- the convention.  `mcp_on_connect_cmd` runs a command automatically when the MCP server connects (the MCP counterpart of `tui_on_connect_cmd` / `cli_on_connect_cmd`).

### The profile file

A device profile declares what commands the device understands and how its responses should be parsed.  Every `run_command` reply uses one fixed envelope -- `cmd`, `success`, `error`, `value`, `data`, `elapsed_s`, `output_lines`, `captured_artifacts`, `async_events` (the `--json` / `/term.request` envelope in a terminal carries the first seven; the last two are collected by the MCP host).  Without a profile, the raw response text arrives in `value` and `data` is null (legacy fallthrough).  With a profile, `data` carries the typed structure, like `{"celsius": 23}`, with the raw wire text still in `value`.

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

`temp` is parameter-less: the LLM calls `run_command("temp")`, termapy sends `temp\r\n` and waits for a reply like `Temperature: 23 C`.  The regex skips the prefix and unit, extracts the digits, the `types` map coerces them to `int`, and the call's response carries `data={"celsius": 23}` (raw wire text in `value`).

`set_led` takes one argument: the LLM calls `run_command("set_led", state="on")`, termapy formats the wire bytes via `send_template` (`led on\r\n`), waits for the literal `OK` reply, and returns success.  The `types` block declares the reusable `on_off` enum; `typed_args` references it, so the LLM sees `state: "on" | "off"` in the tool definition.  `safety: mutable` means it changes device state but doesn't trigger the destructive-confirmation gate.

Full schema, response formats (`none`, `literal`, `lines`, `regex`, `json`), the four-tier safety taxonomy (`safe` / `readonly` / `mutable` / `destructive`), and the request/response executor are documented in [Authoring profiles](authoring-profiles.md).

### Lifecycle on connect / disconnect

When the MCP server connects to its port, it auto-loads the profile from `profile_path` or the convention path.  On disconnect, device-specific state (active profile, catalog, event buffers) is wiped, so reconnecting cleanly re-loads the profile.

### Output hygiene: color and echo

An MCP client is an automated peer, not a terminal, so the session defaults to **color off**: device ANSI escape codes (SGR color, cursor moves) are stripped from `async_events` and `request_mode` values, and command echo is off.  This keeps escape codes out of the JSON the model parses.  For the same reason a `markup` entry in `output_lines` carries the text a terminal shows, with termapy's own Rich color tags removed: `$(TAG) = [ok]`, not `[cyan]$(TAG)[/] = [green]\[ok][/]`.

To pass ANSI through anyway -- for a faithful capture, or a client that renders it -- send `/term.color on`.  To silence color (or a half-duplex device's echo) at the *source*, put the device's own command in `mcp_on_connect_cmd` (e.g. `color off`, `echo off`); it runs automatically on connect.  Full-screen / VT100 output (cursor-addressed dashboards and menus) can't be represented as MCP text -- view those in the TUI.

### Resources

Besides the `run_command` tool, the server publishes three resources:

- `termapy://commands.json` -- the command catalog (same content as `/mcp.catalog`).
- `termapy://device_state.json` -- port, profile, last command, and the capture files, each as `{name, bytes, mtime, age_s, uri}`, newest first.
- `termapy://capture/<name>` -- the body of one capture file.

Every `run_command` reply's `captured_artifacts` lists the capture files a command created or changed, in the same `{uri, name, bytes, mtime, age_s}` shape, so an agent can order captures by recency without re-listing.

### What an MCP client can and can't reach

The MCP server runs with no interactive session, so termapy's capability gate blocks a whole class of host access that only makes sense for a human at a terminal:

- **Shell escape (`/os`)** never runs under MCP: it needs an interactive session, which the server never has.  `TERMAPY_OS_CMD_ENABLED` does not change that; it opens `/os` only at the TUI and CLI.
- **Renaming and deleting files** are off limits.  Every `.rename` and `.delete` command (`/plugin.delete`, `/cap.rename`, `/cfg.delete`, ...) is refused, whether sent directly or inside another command, and neither `confirm=true` nor any flag changes that.  An agent cannot remove a capture, though it can write over one by reusing its name.
- **Code folders can't be written.**  `plugin/` and `viz/` hold Python that runs at the next load, so nothing an agent runs can write there or in a folder below them, whichever command names the folder, and no flag changes that.
- **A write can replace a file only inside the config's folder.**  There an agent works as a person does: `/cap.text`, `/cap.bin`, `/cap.struct`, `/cap.hex` and `/cap.poll --overwrite` start a file over, `/proto.crc.<lang> file=`, `/profile.save` and `/proto.run`'s `json_file` write over their own, and `/run.legacy --fix` rewrites a script.  A file outside the folder is never replaced, and no flag changes that.  `/cfg.new` over an existing config still needs a person's yes, and `/firmware.copy`, `/board.firmware` and `/run.record` never overwrite for anyone.
- **File viewers / editors / explorers** (`/show <path>`, `/edit`, `/cfg.explore`, the folder `.show`/`.explore` verbs) are unreachable -- they need a local desktop.
- **File access is meant to stay inside the config directory, and mostly does.**  Captures (`/cap.text|bin|struct|hex`, `/cap.poll file=`), reads (`termapy://capture/<name>`, the `.dump` commands), `/profile.load|save`, `/cfg.load`, `/log.dump`, `/run.legacy` and generated CRC source (`/proto.crc.<lang> file=`) refuse an absolute path or `../`, a path-valued cfg key (`log_file`) can't be pointed outside it via `/cfg.auto`, and `/proto.run` writes its results in the config's `proto/test/` wherever the script came from.  Known gaps remain while the server is a preview: some commands resolve a relative name from the folder termapy was started in, some Windows path forms (`\x`, `D:x`) pass the check, and a few file readers (`/profile.validate`, `/proto.run <path>`) are not checked.  Load profiles by *name*, and don't rely on the confinement to contain an agent.  To allow host-wide paths, set `TERMAPY_MCP_FS_UNCONFINED=1` in the server's shell.
- **Network ports are refused at `/port.connect`.**  pyserial "URL ports" (`/port.connect socket://host:port`, `rfc2217://...`) would open an outbound TCP connection to an arbitrary host; that command refuses them unless you set `TERMAPY_MCP_NET_EGRESS=1`.  Other routes to a URL port -- `/port <url>`, `/cfg port <url>`, a config whose port is a URL -- are not checked yet, so treat this as partial.  One such URL, `spy://`, [logs traffic to a file](ports.md#logging-the-raw-traffic), which stays in `cap/`.  Physical device ports are unaffected.
- **Environment commands are off under MCP by default.**  `/env` (which would dump every variable) and the `$(env.NAME)` expansion in a command are refused unless you opt in with `TERMAPY_MCP_ENV_ENABLED=1` in the server's shell -- env vars routinely hold secrets, and an MCP peer is remote/automated.  A config's own `$(env.NAME)` values still expand when it loads.
- **Destructive device commands** -- those a profile marks `safety: destructive`, and `/mem.write` -- refuse to run without an explicit `confirm=true`, which a well-behaved client elicits from the user.  The check covers the command you send; a destructive command run inside another (`/repeat`, `/run`, a capture's `cmd=`) is not checked yet.  The catalog carries the `safety` tier of every command that declares one.
- **Peripheral read gates need a device file.**  `/mem.dump` refuses a sweep over registers and `/mem.read` refuses a write-only one only for registers a loaded [device file](devices.md) describes (`/dev.use`); with none loaded, peripheral space is ungated memory.  Load the part before pointing an agent at `/mem.*` (see [Memory](memory.md)).

Wire-level device control (send bytes, read responses, run profiled commands) is of course available -- that's the point.  The gates above are about *host* access, not device access.  The three `TERMAPY_MCP_*` opt-in flags live in the server's shell, not the cfg, so a shared or device-fetched config cannot turn them on (the same rationale as `/os`'s `TERMAPY_OS_CMD_ENABLED`).  It can still reach the gaps above, so until they close, the MCP client's own permissions are the real boundary: run termapy under a client that asks before it acts, and don't point an agent at configs you didn't write.  Each takes any boolean token (`1`, `on`, `true`, `yes`, ...); a value outside that vocabulary refuses to start, naming the variable, rather than silently meaning off.

---

## Part 2: Hooking termapy into an MCP client

The MCP client is the program the user talks to (Claude Code, Claude Desktop, VS Code, etc.).  It launches `termapy --mcp` as a child process and routes the LLM's tool calls to it.

### Claude Code

```sh
claude mcp add --scope user termapy -- termapy --mcp
```

With no config named, it finds the one in whichever project you enable it for in `/mcp` ([How it finds its config](#how-it-finds-its-config)).  Enabling records a per-project on or off; the project's folder comes from Claude Code.  To tie an entry to one device, name its config: `-- termapy --mcp mydevice`.

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
| `run_command` responses have `data: null`, raw text in `value` only | No profile loaded; legacy fallthrough is sending bytes but not shaping the response.  Check `/mcp.info` for the profile revision; if blank, the profile didn't auto-load |
| Profile doesn't auto-load on connect           | Check the convention path (`<cfg_dir>/<cfg_name>.profile.json`) or set `profile_path` explicitly.  The session log will tell you                                         |
| Need to see what the server is doing           | Add `--mcp-verbose` to args; tees the session log to stderr                                                                                                              |
| Several servers running, or `uv sync` can't replace `termapy.exe` | Every server has the same command line (`termapy.exe --mcp`).  Ask each one `/mcp.info` for its `pid`, find it in `tasklist` / `ps`, and end a stale one by that PID (`taskkill /F /PID <pid>`, `kill <pid>`); its launchers exit with it and release `termapy.exe` |

The session log lives at `<cfg_dir>/mcp/session.log`.  Every dispatch, every TX/RX, every error is recorded there.  Open it with `/mcp.log` (system viewer), `/mcp.log.dump` (inline), or `/mcp.log.path` (just the path).  Over MCP, `/log.dump` and `$(CFG.LOG_FILE)` mean this file too: the session log is the frontend's own, never the TUI's `<name>.log`.  An agent cannot run `/log.delete`.

## See also

- [Authoring profiles](authoring-profiles.md) -- response formats, safety tiers, the request/response executor
- [Installation](installation.md) -- the `[mcp]` and `[all]` extras
- [Configuration](config.md) -- `profile_path`, `mcp_on_connect_cmd`, and the rest of the cfg field reference
- [REPL commands](commands.md) -- `/mcp.*` and `/profile.*` work in the TUI/CLI too, useful for inspection and validation
