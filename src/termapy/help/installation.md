# Installation

## Install

First install [uv](https://docs.astral.sh/uv/) if you don't have it:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh          # Mac/Linux
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"  # Windows
```

Then install termapy:

```sh
uv tool install --python 3.14 git+https://github.com/hucker/termapy@v0.43.0
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

## Uninstall

```sh
uv tool uninstall termapy
```
