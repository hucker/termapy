# Installation

## Requirements

- Python 3.11 or later
- [uv](https://docs.astral.sh/uv/)

If you don't have `uv`:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh          # Mac/Linux
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"  # Windows
```

## Install

```sh
uv tool install --python 3.14 git+https://github.com/hucker/termapy@v0.35.0
```

This puts `termapy` on your PATH as a standalone command. `uv` downloads Python 3.14 automatically if it's not already installed.

## Try without installing

```sh
uvx --from git+https://github.com/hucker/termapy@v0.35.0 termapy --demo
```

This runs termapy in a temporary environment that is cleaned up automatically.

## Verify

```sh
termapy --version
termapy --demo
```

## Uninstall

```sh
uv tool uninstall termapy
```
