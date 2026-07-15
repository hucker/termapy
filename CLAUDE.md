# CLAUDE.md

## Project

Termapy — TUI serial terminal. Textual + pyserial. Python 3.11+.

## Key Files

- `app.py` — Textual app (UI, serial I/O, modals, config)
- `repl.py` — REPL engine (dispatch, scripting)
- `plugins.py` — plugin system (discovery, loading, PluginContext)
- `scripting.py` — pure functions (templates, duration parsing)
- `help.md` — in-app help (bundled via `[tool.uv.build]`)
- `builtins/` — built-in REPL commands
- `ARCHITECTURE.md` — architecture overview

All paths relative to `src/termapy/`.

## Architecture

- Textual is confined to the UI layer: `app.py` (the main app) plus `dialogs/`, `widgets.py`, `capture_view.py`, `title_bar.py`, `proto_debug.py`, etc. Core modules (`repl.py`, `plugins.py`, `scripting.py`) never import Textual
- `plugins.py` and `scripting.py` — zero Textual/pyserial deps
- `repl.py` bridges plugins and app via `PluginContext` callbacks
- Load order: builtins → global → per-config → app hooks (later overrides earlier)
- External plugins use `PluginContext` only. `InternalHandle` (`ctx.internal`) is internal/unstable.

## Config

- Config dirs: `termapy_cfg/<name>/` with `plugin/`, `ss/`, `run/`, `proto/`, `viz/`, `cap/` subdirs
- Most users have a single config per project folder (one device), but multiple configs are supported for working with more than one device — each folder gets its own isolated `termapy_cfg/` so setups don't bleed together
- `termapy_cfg/` is gitignored
- New `DEFAULT_CFG` options must also be added to `builtins/demo/demo.cfg`
- New config keys must be documented in: `DEFAULT_CFG` (defaults.py), README.md config reference table, `help/config.md` field reference, and the demo.cfg template

## Rules

- Never edit files/folders on main branch.
- Always merge with `--no-ff` to preserve branch history in the graph.

## Library references

- **crcglot conceptual map**: <https://raw.githubusercontent.com/hucker/crcglot/main/llms.txt> — pull this before working on `/proto.crc.*` integration; it summarizes the four verbs (compute / detect-reverse / generate / encode-verify), the `LANGUAGES` and `ALGORITHMS` registries, and links to the per-surface docs.  Pinned to current crcglot floor (see `pyproject.toml`).

## Conventions

- Plugin args: `""` = none, `{braces}` = optional, `<angle>` = required
- No spaces inside brace/angle groups: `{on|off}` not `{on | off}`, `{name|*}` not `{name | *}`
- Toggle commands use `{on|off}` (optional) — bare invocation queries or toggles state; arg sets it
- REPL prefix: `/`
- Modals return tuples: `("run", path)`, `("new",)`, `("edit", path)`
- Buttons: rainbow palette, Exit always red (`error`)
- SS/Scripts buttons always visible with file-count tooltips
- Plugin files end with `COMMAND` dict preceded by:
  `# ── COMMAND (must be at end of file) ──────────────────────────────────────────`
- Subcommands = distinct operations (`port.list` vs `port.connect`)
- Toggles/values = args (`echo on`, `cfg baudrate 9600`)
- Handlers that produce scriptable data must return `CmdResult.ok(value=...)` — without it, scripts in quiet mode get nothing. See `CmdResult` docstring in `plugins.py`.

## Declarative Command Parameters (`Command.params`) vs hand-rolled

Some commands declare their args as `params=[ParamSpec(...)]` (the dispatcher
parses/coerces/validates + synthesizes usage/help/MCP-schema); others parse `args`
by hand. This split is **intentional** — use this rule, and if a command is
hand-rolled where a reader might expect params, leave a one-line comment saying
why (anchored to this section). See `docs/param-spec-implementation.md` and
`plugins/params.py`.

**Use `params` when** the grammar is positional tokens + `key=value` keywords +
at most one *rest* (a whole-line value, or a keyword that runs to end-of-line).
Composes fine with flags, subcommands, level-suffixes (`.silent`), and
`raw_args=True` (raw_args skips only the `$(VAR)`/`$(env)` transform step, not
param parsing, so values arrive literal — e.g. `/cap.wire`). This is the default
for any multi-arg command.

**Keep it hand-rolled (documented boundary) when:**

- **Variable-arity mode dispatch** with a mutual-exclusion synopsis — `profile.load`
  (`{path|cmd=<command>}`: path XOR cmd XOR empty), bare `/cfg` (picker/query/set).
- **A keyword value with spaces followed by more keywords** — `cap.struct`/`cap.hex`
  `fmt=Temp:U1-2 Pressure:F3-6 records=5` (the spec syntax is space-separated by
  definition, shared via `protocol.parse_format_spec`).
- **Bare `-flag`-style literal grammar** flag parsing would eat — `search`'s `-term`.
- **Variadic positional lists** — `ymodem.send <file> {file2} ...`.
- **An ordering constraint** where a state check must precede arg validation —
  `os_cmd` (env-gate before the arg check).

**Also weigh:**

- **Direct-handler unit tests** (`_handler(ctx, "raw args")`) must move to the
  dispatch contract when a command migrates — factor that cost into go/no-go
  (why `/env.set` was left hand-rolled).
- **Trivial single-arg** commands are net-zero-value to migrate (the parse is a
  one-liner); do it only if the typed MCP schema genuinely matters.
- Within one file, prefer all-params or clearly-documented holdouts — don't
  cherry-pick subcommands without a "why" comment (see `cap.py`).

## Error Messages (`CmdResult.fail(msg=...)`)

- Sentence case: capitalize the first letter (`"Count is required"`, not `"count is required"`)
- Exception: when the subject is a literal keyword/flag name, keep its native case (`"cmd= must have at least one command"`)
- No leading whitespace — the dispatcher adds the `Error:` prefix and formats the line
- State statements end with a period: `"No config loaded."`, `"Not connected."`, `"No log file."`
- Validation/lookup errors do NOT end with a period: `"Unknown config key: X"`, `"Invalid delay: Y"`
- Standard phrasings (reuse these verbatim before inventing a new form):
  - `"Unknown <thing>: <name>"` — categorical lookup failure (command, algorithm, folder, key)
  - `"Invalid <thing>: <value>"` — value failed validation (regex, duration, count, format spec)
  - `"<Thing> not found: <name>"` — file, directory, or config missing from disk
  - `"No <thing> loaded."` / `"Not connected."` — app state precondition unmet
  - Wrong arity → `raise UsageError()` (optional detail arg) — the dispatcher renders `"Usage: <prefix><cmd> <args>"` from the registered declaration. NEVER hand-write a `"Usage: ..."` string in a handler; the only sanctioned hand-written forms are the two documented holdouts (multi-form synopses like `/proto.crc.<lang>`, bare-parent redirects like `/proto.<sub>`)
  - `"<Thing> error: {e}"` — wrap an exception from a named subsystem (`"Type error"`, `"Parse error"`, `"Read error"`)
- Module-prefix style (`"Ping: ..."`, `"Include: ..."`, `"Expect: ..."`) is acceptable when **every** error from that plugin uses the same prefix — don't mix styles within one plugin

## Development

```sh
uv run pytest              # run tests
uv run termapy             # run the app
uv run termapy --cfg-dir . # use cwd for configs
```

## Testing

- `uv run pytest` — full suite (~110s); use this before commit/merge
- `uv run pytest -m "not slow"` — fast suite (~25s) for tight iteration. Skips ~230 subprocess-spawning, real-serial-loopback, and sleep-based tests. Use during dev; ALWAYS run the full suite before pushing.
- `uv run pytest -m slow` — only the slow tests (useful when debugging a specific subprocess test)
- Coverage omits `__init__.py`, `builtins/*.py`, `app.py`, `dialogs/*.py`, and `vendor/*` (see `[tool.coverage.run]` in `pyproject.toml`). The reported percent is therefore **core-module** coverage, not whole-repo: `app.py`/`dialogs/` are the Textual UI (integration-tested via Pilot + the CLI gold test, not unit-tested), `builtins/` are plugins covered behaviorally, `vendor/` is third-party. Whole-repo coverage (only `vendor/` omitted) is ~61%.
- `app.py` not unit tested — only non-UI modules
- Run tests before commit; full suite before merging to main
- AAA comments (`# Arrange`, `# Act`, `# Assert`) for non-trivial tests
- Assert comments required
- Assert order: `actual == expected`
- Assert messages required for non-obvious asserts — state the *intent* (what a failure means), not a restatement of the expression, which pytest already prints. Self-documenting value-checks (e.g. `assert parsed.field == expected` in a parse test whose name conveys the scenario) are exempt. When an assert carries an explanatory comment, make it the message instead (`assert x == 500  # override` → `assert x == 500, "override"`). Suite compliance is ~94% by this standard, not 100% by an absolute one.
- Non-trivial: use `actual`/`expected` variables
- Multiple checks: `actual_x == expected_x` pattern
- Avoid mocking and monkey-patching.  Confirmation required!

## CLI Gold Test

- `tests/cli_gold/cli_test.run` — deterministic script exercising ~100 commands
- `tests/cli_gold/cli_test.expected` — gold standard output (~780 lines; grows as commands are added, so this is approximate)
- Deliberately does NOT exercise `/help proto.crc`: its SUBCOMMANDS block is generated from `crcglot.LANGUAGES`, so a crcglot language addition would redden the gold with no termapy change. Stable proto.crc coverage (`/proto.crc.info`, `.calc`) stays; the help renderer is covered by the other `/help <cmd>` pages.
- Regenerating the gold file requires manual review — the expected output is the source of truth for CLI regression testing
- Only deterministic commands allowed — no timestamps, random values, or hardware-dependent output
- No Unicode in output strings — use ASCII only (hyphens, not em dashes)

## Code Style

- If code manages state, use a class — not functions with closures over shared variables
- Pure functions are fine and preferred when there's no state (e.g. `scripting.py`)
- `scripting.py` — pure functions, no state, no I/O
- `plugins.py` — no Textual/pyserial imports
- New commands → plugins in `builtins/` unless they need Textual
- Textual-dependent commands → `register_hook()` in `app.py`
- Google-style docstrings and type hints
- Warn about hacks, threads, delays, magic,monkeypatch, test constants
- OS-independent path handling: use `pathlib.Path` — never split on `/` or `\\`
- Watch for large code added to solve small problems. AI can make any spec work regardless of how much code it takes, so volume is not evidence of difficulty. Sprawling helpers, many-branched special cases, or duplicated logic across helpers usually indicate the spec was under-specified or the approach is wrong — pause and re-scope rather than piling on more code.

## Threading

- `read_serial()` runs on a background thread via `@work(thread=True)`
- `run_script()` in `repl.py` runs on a background thread via `_run_script` wrapper
- Script commands are dispatched to the main thread via `call_from_thread`
- Commands that **block** or **use `call_from_thread` internally** (e.g. `/delay`, `/confirm`) must NOT be dispatched to the main thread — handle them as special cases directly in `run_script()`'s background thread
- `_confirm()` blocks with `event.wait()` and posts a dialog via `call_from_thread` — must always be called from a background thread
- When refactoring dispatch paths, verify that blocking commands still run on background threads

## Exception Safety

- `call_from_thread` → `try/except RuntimeError` (shutdown)
- `ser.write/dtr/rts/send_break` → catch `(OSError, SerialException)`
- Config load/write → catch `Exception`/`OSError`
- Non-critical file I/O (history, logs) → swallow `OSError`
- Plugin handlers called inside `dispatch()` try/except — no unguarded calls
- Never crash to stack trace — use `_status()` for errors

## Precommit

- Update README.md and help/*.md
- Update ARCHITECTURE.md if modules, plugins, or structure changed
- **Do NOT hand-update the test/coverage/ty counts or badges on feature
  builds.** The test count, coverage percent, ty diagnostic count, and the
  per-module line counts in README.md and ARCHITECTURE.md are refreshed
  ONLY by `release_prep.py` during the release process (see the
  "Auto-refreshed every release" list below). Editing them on every branch
  just churns the diff and causes merge conflicts between parallel
  branches, and the numbers go stale again on the next merge anyway. Leave
  them; release trues them up.
- Do NOT rebuild HTML help in feature commits — it adds noise to diffs
- HTML help rebuild should be a separate commit before release versions
- Run tox, pytest, coverage review
- **`uv run ruff check src/termapy/ tests/` must be 0** (release_prep refuses to cut a release otherwise)
- **`uvx ty check src/termapy/` must be 0** (same gate; the README badge tracks this and turns yellow/red on regression)

## Release

Two-stage automation in `scripts/`. From clean main:

1. `python scripts/release_prep.py <version>` -- cuts `release/v<version>`, bumps `pyproject.toml` and `mkdocs.yml`, refreshes `uv.lock`, refreshes line/test/coverage/ty counts in `ARCHITECTURE.md` and `README.md` (including the ty + coverage badges), inserts a CHANGELOG stub, runs `pytest` and `tox`, builds HTML with `uvx zensical build`, makes two commits (HTML rebuild, then `Release v<version>`).
2. Manual review: edit `CHANGELOG.md` to replace the TODO stub with a user-facing summary, then `git commit --amend --no-edit`.
3. `python scripts/release_publish.py --yes` -- merges release branch to main with `--no-ff`, tags `v<version>`, pushes main + tag + release branch, creates the GitHub release with notes pulled from CHANGELOG.

Hard gates (step 1 aborts):

- ruff and ty must both report zero issues
- `main` must be in sync with `origin/main`
- tag `v<version>` must not already exist

Auto-refreshed every release (`update_readme_md` / `update_architecture_md`):

- test count (`pytest --collect-only`) — written to **exactly one place**, the
  README's "Test coverage" `<details>` summary line. Everywhere else the count
  is imprecise prose ("extensively tested", "thousands of tests") that never
  needs updating. ARCHITECTURE.md and the README body line carry no count.
- coverage percent (parsed from `pytest --cov` `TOTAL` line) — only the README
  summary line + the coverage badge. Prose describes coverage in general terms
  and carries no percentage.
- ty diagnostic count + badge color
- ARCHITECTURE.md per-module line counts (the tree diagram is the one home for
  exact line counts — prose elsewhere names modules without counts)

**General rule: exact numbers live only in release-updated structured spots
(the summary line, badges, the ARCHITECTURE tree). Prose uses general terms
("extensively tested", "the largest UI modules", "heavily covered") so no
hand-maintained figure can drift.** Don't reintroduce a count/percent/line
figure into a sentence. Floor/approximate language is always fine in prose
("100+ algorithms", "more than 2000 tests", "~60%") — it stays true as the
real number grows, so it isn't a maintenance dependency. Every release_prep
substitution targeting one of the exact-count spots must fail loud
(`re.subn` + `die()` on a miss) so a reworded target can't silently drift.

No RC versions, no leading `v` in the version arg, never run from anywhere but main. Scripts are stdlib-only and fail loud. See script docstrings for details.

## Politeness

- Be polite in messaging. Don't praise sycophantically.
- Focus on clear concise and helpful without unnecessary flattery or emotional language.
- Avoid phrases like "Great choice!", "You're doing amazing work!"
- The goal is high quality, production-ready docs and code, not emotional support.
