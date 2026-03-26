# Installation

## Requirements

- Python 3.11 or later
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Install with uv (recommended)

Try it without installing:

```sh
uvx --from git+https://github.com/hucker/termapy@v0.31.0 termapy --demo
```

Or install permanently:

```sh
uv pip install git+https://github.com/hucker/termapy@v0.31.0
```

## Install with pip

```sh
pip install git+https://github.com/hucker/termapy@v0.31.0
```

## Verify

```sh
termapy --version
termapy --demo
```

`--version` prints the version number. `--demo` launches a simulated
device so you can try everything without hardware.
