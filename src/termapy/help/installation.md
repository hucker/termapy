# Installation

Two commands for each package manager: one to install, one to upgrade.
We strongly recommend [uv](https://docs.astral.sh/uv/); pip works but
installs into whichever Python interpreter you run it with.

⚠️ **Exit any running termapy before upgrading.**  The running program
holds its own binary open, so the package manager can't replace the
files cleanly.  On Windows the upgrade will fail outright with a
file-in-use error.  On macOS and Linux the upgrade command will
usually succeed, but you'll keep running the old code in the live
process until you exit and relaunch -- confusing if you're expecting
a fix or feature from the new release.  Close every termapy window,
run the upgrade command, then relaunch.

## uv (recommended)

Install:

```sh
uv tool install "termapy[all]"
```

Upgrade:

```sh
uv tool upgrade termapy
```

## pip

Install:

```sh
pip install "termapy[all]"
```

Upgrade:

```sh
pip install --upgrade "termapy[all]"
```

## What `[all]` includes

`[all]` is a catch-all that bundles every optional extra so you don't
have to think about it.  Lazy imports inside termapy mean unused deps
cost nothing at runtime: TUI/CLI users never load pydantic, and `--mcp`
users never load Textual.  The only tradeoff is disk footprint --
`[all]` adds ~16 MB on top of the base install -- which is rounding
error on any modern dev machine.

## Slim installs

If you specifically need a smaller footprint (embedded targets, slim
containers, paranoid security review), pick individual extras:

| Want                         | Install                       |
|------------------------------|-------------------------------|
| TUI + CLI only               | `pip install termapy`         |
| TUI + CLI + MCP server       | `pip install "termapy[mcp]"`  |
| TUI + CLI + web mode         | `pip install "termapy[web]"`  |
| Everything                   | `pip install "termapy[all]"`  |

`--mcp` requires the `[mcp]` extra (or `[all]`) and exits with an
install hint if it's missing.  Same for `--web` and `[web]`.

## Desktop launcher

For a per-config desktop / menu icon, load the cfg and run `/cfg.icon`:

| Platform | Result |
| --- | --- |
| Windows | `.lnk` on the Desktop (cmd /k termapy "&lt;cfg file&gt;") |
| macOS | `~/Applications/<title>.app` (opens Terminal.app) |
| Linux | `~/.local/share/applications/termapy-<name>.desktop` |

`/cfg.icon --force` overwrites an existing launcher; `/cfg.icon.remove`
deletes it; `/cfg.icon.list` enumerates every termapy launcher this OS
can see.  The launcher embeds the absolute path to the cfg file, so
rerun `/cfg.icon` if you move the project.

### Quick demo

Try it against the bundled demo cfg (no hardware required):

```sh
termapy --demo                # launches the demo cfg
```

Then in the termapy REPL:

```text
/cfg.icon          # creates the launcher
/cfg.icon.list     # lists every termapy launcher on this OS
/cfg.icon.remove   # deletes it
```

The launcher appears on your Desktop (Windows), in `~/Applications`
(macOS), or in your application menu (Linux).  Double-click it to
relaunch termapy with the demo cfg preloaded.

## Uninstall

```sh
uv tool uninstall termapy
```

Or, if installed with pip:

```sh
pip uninstall termapy
```
