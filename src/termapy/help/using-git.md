# Using with Git

Termapy configs are self-contained folders. Add one to your project
repo and the whole team shares the same serial setup -- scripts,
protocol files, plugins, and all. Termapy auto-generates a `.gitignore`
in each config folder so transient data stays out of version control.

Launch termapy from your project root pointing at the config:

```text
termapy termapy_cfg/main_board/main_board.cfg
```

This takes you straight to the right config. A shell alias keeps it
short:

```text
alias mb="termapy termapy_cfg/main_board/main_board.cfg"
```

See [Getting Started](getting-started.md) for more launch options and
the folder layout.

## What Gets Committed

Termapy auto-generates a `.gitignore` in each config folder.
Items marked *ignored* stay local to each developer:

```text
termapy_cfg/
├── main_board/
│   ├── .gitignore              # auto-generated
│   ├── main_board.cfg
│   ├── main_board.log          # ignored
│   ├── .cmd_history.txt        # ignored
│   ├── ss/                     # ignored
│   ├── cap/                    # ignored
│   ├── prof/                   
│   ├── run/
│   ├── proto/
│   ├── viz/
│   └── plugin/
└── aux_board/
    ├── .gitignore              # auto-generated
    ├── aux_board.cfg
    ├── aux_board.log           # ignored
    ├── .cmd_history.txt        # ignored
    ├── ss/                     # ignored
    ├── cap/                    # ignored
    ├── prof/                   
    ├── run/
    ├── proto/
    ├── viz/
    └── plugin/
```

## Environment Variables

COM port names differ between machines (`COM4` on one, `COM7` on
another, `/dev/ttyUSB0` on Linux). Use an environment variable with
a fallback default so the same config works on every checkout:

```json
{
    "port": "$(env.MAIN_PORT|COM4)",
    "baud_rate": 115200,
    "default_ui": "$(env.TERMAPY_UI|tui)"
}
```

Each developer sets `MAIN_PORT` on their machine. The `|COM4` fallback
is used when the variable is not set. The checked-in config keeps the
raw template -- expansion happens in memory at load time, so the file
in your repo always has `$(env.MAIN_PORT|COM4)`. The fallback is optional
and really only useful on machines that might have a default port.
If you don't need a fallback, just use `$(env.MAIN_PORT)`.

Use any variable name that fits your project. Multiple boards? Use
descriptive names like `MAIN_PORT`, `DEBUG_PORT`, `AUX_PORT`, etc.

The above also shows setting the default UI mode with an environment variable
defaulting to the `tui`. You can use this technique for any config value -- paths, flags, whatever. Just make sure to document the expected variables for your team, and
consider providing a `.env` file or setup script to make it easy to get started.

### Setting the Variable

**Windows (permanent):**

```text
setx MAIN_PORT COM7
```

Restart your terminal after `setx` -- it does not affect the current session.

**Windows (current session only):**

```text
set MAIN_PORT=COM7
```

**macOS / Linux (add to `~/.bashrc` or `~/.zshrc` to make permanent):**

```text
export MAIN_PORT=/dev/ttyUSB0
```

See [Variables](variables.md) for the full `$(env.NAME)` syntax and
related commands.

---
