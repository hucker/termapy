# Contributing to Termapy

Pull requests are welcome! Plugins especially.

## Quick start

```sh
git clone https://github.com/hucker/termapy
cd termapy
uv sync
uv run termapy --demo     # verify it works
uv run pytest              # run tests
```

## Writing plugins

The easiest way to contribute is to write a plugin. Drop a `.py` file in a folder and it becomes a command. See the [Writing Plugins](src/termapy/help/writing-plugins.md) guide for the full API.

Good plugin ideas:
- Device-specific command wrappers (Modbus polling, SCPI instruments, GPS loggers)
- Data visualization (charts, gauges, dashboards)
- Protocol decoders
- Automation tools (test sequences, calibration routines)

If your plugin is generally useful, open a PR to include it in `builtins/plugins/`.

## Code contributions

1. Create a feature branch from `main`
2. Make your changes
3. Run `uv run pytest` - all tests must pass
4. Run `uv run tox` - must pass on Python 3.11-3.14
5. Update docs if you changed commands, config, or architecture
6. Rebuild HTML help: `uv run -- python -m mkdocs build`
7. Open a PR

### Code style

- Google-style docstrings and type hints
- Pure functions in `scripting.py` and `plugins.py` - no Textual/pyserial imports
- New commands go in `builtins/plugins/` unless they need Textual
- Plugin files end with a `COMMAND` dict
- OS-independent paths: use `pathlib.Path`, never split on `/` or `\\`
- No Unicode in output strings - use ASCII only (hyphens, not em dashes)
- See `CLAUDE.md` for the full conventions

### Testing

- AAA comments (`# Arrange`, `# Act`, `# Assert`) for non-trivial tests
- Assert order: `actual == expected`
- The CLI gold test (`tests/cli_gold/`) catches regressions across the full command pipeline. If you change command output, regenerate and review the gold file.

## Reporting issues

Open an issue at https://github.com/hucker/termapy/issues with:
- What you expected
- What happened
- Steps to reproduce
- Python version and OS
