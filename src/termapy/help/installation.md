# Installation

## Install

```sh
pip install termapy
```

Or with [uv](https://docs.astral.sh/uv/):

```sh
uv tool install termapy
```

## Run the demo

No hardware needed:

```sh
termapy --demo
```

![Termapy TUI](img/doc_01_main_tui.svg)

Type commands. The device responds. That's it.

Try `AT+INFO`, `AT+TEMP`, or `/help`. Hover over any button for a tooltip.
Click **?** for the full help guide.

## Connect your device

Click **Cfg** in the toolbar, then **New**. Pick your port and baud rate.
Click **Connect**.

![New Config dialog](img/new_cfg.png)

You're connected. Type commands and see responses.

## When you need more

- [Getting Started](getting-started.md) — config files, CLI mode, folder layout
- [Demo Mode](demo.md) — all demo device commands
- [Serial Tools](serial-tools.md) — hex send, CRC, protocol testing
- [Scripting](scripting.md) — automate command sequences
- [Writing Plugins](writing-plugins.md) — extend with Python

## Web mode (experimental, optional)

There is an experimental browser-based mode that requires an additional install:

```sh
pip install termapy[web]
termapy --web --demo
```

Or with uv:

```sh
uv tool install termapy[web]
termapy --web --demo
```

Opens on `http://localhost:8000`. Use `--web-port` to change the port.

This is not the primary way to use termapy -- the TUI and CLI modes are the
intended interfaces. Web mode has limitations: `/tui` and `/cli` mode switching
are not available, and `/help.open` may not work in the browser.

## Uninstall

```sh
uv tool uninstall termapy
```
