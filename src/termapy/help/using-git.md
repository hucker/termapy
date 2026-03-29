# Using with Git

`Termapy` configs are self-contained — everything lives in one folder.
This makes them easy to share across a team via version control. If you
have a git folder for your embedded project you can put your termapy
config folder under git and make it visible to the whole team so everybody
shares development folders. There are mechanisms to make developer-specific
configs safe to check in.

## The Config File

The `.cfg` file is the heart of every project. It defines the serial
connection, display settings, custom buttons, and all the behavior that
makes `termapy` useful for your specific device. Most projects only need
this one file — scripts, proto files, and plugins are optional extras.

A typical shared config:

```json
{
    "port": "$(env.MAIN_PORT|COM4)",
    "baud_rate": 115200,
    "title": "Main Board",
    "auto_connect": true,
    "echo_input": true,
    "on_connect_cmd": "AT+INFO",
    "custom_buttons": [
        {"enabled": true, "name": "Status", "command": "AT+STATUS", "tooltip": "Query device status"},
        {"enabled": true, "name": "Reset",  "command": "AT+RESET",  "tooltip": "Reset device"}
    ]
}
```

The `$(env.MAIN_PORT|COM4)` syntax is key — it reads the COM port from
an environment variable so the same config works on every developer's
machine. See [Handling COM Ports](#handling-com-ports-across-machines) below.

For the full list of config fields, see [Configuration](config.md).

## Folder Structure

When you commit a `termapy` config folder, it looks like this:

```text
your_project/
├── firmware/               # your project files
├── docs/
└── termapy_cfg/
    └── main_board/
        ├── main_board.cfg    # ← the config (always committed)
        ├── .gitignore          # ← auto-generated (excludes transient data)
        ├── run/                # .run script files (committed)
        ├── proto/              # .pro protocol test files (committed)
        ├── plugin/             # custom plugins (committed)
        ├── viz/                # custom visualizers (committed)
        ├── ss/                 # screenshots (ignored)
        ├── cap/                # data captures (ignored)
        └── prof/               # profile output (ignored)
```

## What Gets Committed

**Tracked** (shared with your team):

- `<name>.cfg` — the config file
- `run/*.run` — automation scripts
- `proto/*.pro` — protocol test files
- `plugin/*.py` — custom plugins
- `viz/*.py` — custom visualizers
- `.gitignore` — auto-generated

**Ignored** (local to each developer):

- `*.log` — session logs
- `.cmd_history.txt` — REPL history
- `.cap_seq` — capture sequence counter
- `_profile_tmp_*` — temporary profile scripts
- `ss/` — screenshots
- `cap/` — data captures
- `prof/` — profile output

`Termapy` automatically creates a `.gitignore` in each config folder
that excludes the transient data above.

## Handling COM Ports Across Machines

COM port names differ between machines (`COM4` on one, `COM7` on
another, `/dev/ttyUSB0` on Linux). Hardcoding the port in a shared
config breaks on other machines.

The recommended approach: use environment variables with fallbacks.

```json
{
    "port": "$(env.MAIN_PORT|COM4)"
}
```

Each developer sets `MAIN_PORT` on their machine. The config file stays
portable — it's expanded in memory at load time, so the checked-in
file keeps the raw `$(env.MAIN_PORT|COM4)` template.

The name `MAIN_PORT` is just a convention — use any name that makes
sense for your project. If you have multiple ports, give them
descriptive names:

```json
{
    "port": "$(env.MAIN_PORT|COM4)"
}
```

A test fixture config might reference a different variable:

```json
{
    "port": "$(env.DEBUG_PORT|COM8)"
}
```

Environment variables work in any string config value:

```json
{
    "port": "$(env.MAIN_PORT|COM4)",
    "title": "$(env.DEVICE_NAME|Dev Board)"
}
```

## Setting Environment Variables

### Windows (permanent — recommended)

1. Open **Start** → search "Environment Variables" → click **Edit the system environment variables**
2. Click **Environment Variables...**
3. Under **User variables**, click **New**
4. Variable name: `MAIN_PORT`, Variable value: `COM7`
5. Click **OK** — restart any open terminals for the change to take effect

Or from a command prompt (sets a **user-level** variable, persists across reboots):

```text
setx MAIN_PORT COM7
```

Close and reopen your terminal after running `setx` — it does not
affect the current session.

### Windows (temporary — current session only)

```text
set MAIN_PORT=COM7
termapy --cfg-dir ./termapy_cfg
```

### macOS / Linux

```text
export MAIN_PORT=/dev/ttyUSB0
```

Add to `~/.bashrc` or `~/.zshrc` to make it permanent.

## Team Workflow

1. Create a config with `Cfg → New`
2. Set `"port": "$(env.MAIN_PORT|COM4)"` in the config
3. Write scripts, proto files and plugins as needed
4. Add the `termapy_cfg/<name>/` folder to your repo
5. Each developer sets their `MAIN_PORT` environment variable
6. Everyone runs `termapy --cfg-dir ./termapy_cfg`
7. Git tracks changes

The config, scripts, and proto files are shared. Each developer's
COM port, logs, and screenshots stay local.
