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

If your plugin is generally useful, open a PR to include it in `builtins/commands/`.

## Code contributions

1. Create a feature branch from `main`
2. Make your changes
3. Run `uv run pytest` - all tests must pass
4. Run `uv run tox` - must pass on Python 3.11-3.15
5. Update docs if you changed commands, config, or architecture (the Markdown under `src/termapy/help/`; do not rebuild the HTML help in a PR, the release does that)
6. Open a PR

### Conventions

The project's engineering rulebook is `CLAUDE.md` at the repo root. The filename is the AI tooling's convention; the content is for every contributor: the architecture rules and the invariants registry they point at (`docs/invariants.md`), code style, error-message style, the testing conventions (test headers, AAA comments, assert order, the CLI gold test), the threading rules and the release process. Read it before a non-trivial change; `ARCHITECTURE.md` explains the structure those rules protect.

## Reporting issues

Open an issue at <https://github.com/hucker/termapy/issues> with:

- What you expected
- What happened
- Steps to reproduce
- Python version and OS
