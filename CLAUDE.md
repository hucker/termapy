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

- Textual is confined to the UI layer: `app.py` (the main app) plus `dialogs/`, `widgets/`, `capture_view.py`, `title_bar.py`, `proto_debug.py`, `info_views.py`, `palette_provider.py`, etc. Core modules (`repl.py`, `plugins.py`, `scripting.py`) never import Textual. Any NEW module that imports Textual is UI-layer and must be added to this list. (`pickers.py` is Textual-free — it builds picker *data*, not widgets — so it is not in this list.)
- **app.py health is NOT a line count.** A feature-rich TUI has irreducible *essential* complexity — buttons, a REPL, mouse/text handling, modals, event wiring each need code — so a large `app.py` is honest, not a smell. Do **not** relocate UI code into mixins/helper modules just to shrink the number: moving essential complexity around doesn't reduce it, it scatters it across more files and adds coupling. Health is three things, none of them size: (1) **logic stays out of the UI** — serial / dispatch / scripting / capture live in the engines (`SerialEngine`, `ReplEngine`, `TerminalHost`, plugin handles); `app.py` is UI wiring only; (2) **new device behavior is a plugin**, not `app.py` code; (3) **no duplication**. Judge these in review. There is deliberately **no line-count gate** — a size limit polices the wrong metric (it nags on legitimate feature growth and can't tell essential UI from accidental complexity). Extract from `app.py` only for concrete pain (real duplication, logic that leaked in, a change that's genuinely hard to make), **never for the number**.
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

- Plugin args: `""` = none, `{braces}` = optional, `<angle>` = required, trailing `...` = repeatable (`<frames>...`, synthesized from `ParamSpec(variadic=True)`)
- No spaces inside brace/angle groups: `{on|off}` not `{on | off}`, `{name|*}` not `{name | *}`
- The synopsis grammar is ENFORCED at registration (`validate_synopsis` via `PluginInfo.__post_init__`): square brackets, `{{` artifacts, spaced `|`, and unbalanced groups fail loud at load/boot instead of rendering wrong in /help
- **Setting commands never mutate on a bare invocation — bare QUERIES.** This
  is the `stty`/`git config` standard. A boolean setting takes `{on|off|toggle}`:
  bare shows state, `on`/`off` (any `scripting.parse_bool` token) sets, the
  explicit verb `toggle` flips, anything else errors
  (`Invalid value: X (use on/off/toggle)`). An enum setting takes
  `{val1|val2|...|cycle}`: bare shows, a value sets, `cycle` advances (wraps),
  anything else errors. Route booleans through `scripting.parse_bool_setting`
  (QUERY/TOGGLE sentinels + bool + None) and `_bool_setting`/`_cfg_toggle`;
  enums through `next_in_cycle`. TUI buttons that flip on click dispatch the
  explicit `toggle`/`cycle` verb, never the bare command. NEVER treat an
  unrecognized argument as a flip (that hid the `/term.color 2` bug).
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
at most one *rest* (a whole-line value, or a keyword that runs to end-of-line) +
at most one *variadic* positional (a repeatable token that binds a list).
Composes fine with flags, subcommands, level-suffixes (`.silent`), and
`raw_args=True` (raw_args skips only the `$(VAR)`/`$(env)` transform step, not
param parsing, so values arrive literal — e.g. `/cap.wire`). This is the default
for any multi-arg command.

**Keep it hand-rolled (documented boundary) when:**

- **Variable-arity mode dispatch** with a mutual-exclusion synopsis — `profile.load`
  (`{path|cmd=<command>}`: path XOR cmd XOR empty), bare `/cfg` (picker/query/set).
- **A keyword value with spaces followed by more keywords** — `cap.struct`/`cap.hex`
  `fmt=Temp:U1-2 Pressure:F3-6 records=5` (the spec syntax is space-separated by
  definition, shared via `protocol.parse_format_spec`); and `proto.crc.reverse`,
  whose documented `cmd=AT+RND.CUSTOM count=13 crc_bytes=2` puts keywords AFTER
  a to-end-of-line `cmd=`. `parse_keywords` would swallow them into the trigger
  text, so the hand-rolled parser (which recovers them) is load-bearing, not
  legacy. This is a PERMANENT holdout — do not "migrate" it.
- **Bare `-flag`-style literal grammar** flag parsing would eat — `search`'s `-term`.
- **Variadic positional lists** — `ymodem.send <file> {file2} ...`. (`params` can
  now express these via `ParamSpec(variadic=True)`, so this is a *not-yet-migrated*
  holdout rather than a permanent boundary; migrate when the command is next touched.)
- **An ordering constraint** where a state check must precede arg validation —
  `os_cmd` (env-gate before the arg check).

**Also weigh:**

- **Direct-handler unit tests** (`_handler(ctx, "raw args")`) must move to the
  dispatch contract when a command migrates — factor that cost into go/no-go
  (why `/env.set` was left hand-rolled).
- **Trivial single-arg** commands are net-zero-value to migrate (the parse is a
  one-liner); do it only if the typed MCP schema genuinely matters.
- **A holdout is not second-class.** `$(*NAME)` dereference is a public
  primitive (`plugins.params.resolve_deref`), not dispatcher-internal, so a
  hand-rolled handler opts in with two lines — resolve each token before
  coercing it. `proto.crc.reverse` does exactly this. Never migrate a command
  to `params` *just* to reach a primitive; call the primitive.
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
- `uv run pytest -m hardware` — the real-hardware suite (`tests/test_hardware_loopback.py`). Needs a **TX-RX-jumpered USB-serial adapter**; found by serial number (`TERMAPY_LOOPBACK_SN`, default `BG03U7VTA`) via termapy's own `resolve_port`, so it survives COM-number reshuffles. **Skips cleanly when absent**, so CI and other machines are unaffected. These cover defects that are structurally invisible to `FakeSerial` and `loop://` — neither has an OS driver, a bridge FIFO, or real baud pacing, so neither can drop a byte (that blind spot is how the 4 KB driver-buffer data loss survived a 41-finding audit). The fixture refuses to run unless the port demonstrably echoes itself: these tests write tens of KB at speed, which is harmless into a jumper and unacceptable into a live device. They carry `xdist_group` (one process may hold the port) — hence `--dist loadgroup` in `addopts`; without it xdist scatters them and every worker but one "skips" in a way that reads exactly like absent hardware.
- Coverage omits `__init__.py`, `builtins/*.py`, `dialogs/*.py`, and `vendor/*` (see `[tool.coverage.run]` in `pyproject.toml`). **`app.py` is measured** — it is Pilot-tested by `tests/test_app_pilot.py`, which boots the real `SerialTerminal` headless via `app.run_test()`, so its number is real and movable. `dialogs/` are still omitted (Pilot-testable by the same pattern, not yet done), `builtins/` are plugins covered behaviorally, `vendor/` is third-party.
- `app.py` is not UNIT tested — its logic is orchestration glue (`_switch_config` is 36 lines of `self._disconnect()` / `self.repl.replace_cfg()` / `self._history_nav.reset()`), which cannot be made pure because it IS the wiring. Test it by DRIVING the app: `app.run_test()` +
  `pilot.pause()`, then assert on state. An AST audit (2026-08-18) found only 418 Textual-free logic lines left in its 3634, none forming a cohesive module — extraction is exhausted, Pilot is the lever. Limits: `run_test()` does not model real-terminal focus/blur or `@work` timing, so cover STATE TRANSITIONS there and keep input-focus work on live iteration.
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
- **Loop variables name the element, singular: `for singular in plural:`.**
  `for token in tokens:` — never `for tok in tokens:` or `for t in tokens:`.
  When the collection has a plural name the loop variable is its singular form;
  when it doesn't, still name the element for what it is (`for byte in data:`,
  `for char in text:`). Applies to comprehensions. **Exempt:** positional and
  numeric variables (`i`/`j`/`k` indexes, `x`/`y`/`z` coordinates), `_` for an
  unused binding, tuple unpacking where each name describes its own slot
  (`for name, handler in COMMANDS.items():`), and short words that are whole
  words rather than truncations — `key` (to a dict what `i` is to an index),
  plus `app`, `raw`, `bus`, `pyc`, `hex`, `url`, `tag`, `row`, and `cmd`
  (termapy's own vocabulary: `cmd=`, `cmds`, `cmd_delay_ms`). A full word that
  isn't the singular is fine when it says more than the singular would — `for
  spec in params:` over `ParamSpec` objects, `for info in plugins:` over
  `PluginInfo`. The rule targets truncation, not variety.
  Use **folder/folders**, never `dir` (a builtin) or `directory`. Note `file`
  and `byte` are NOT builtins in Python 3 — only `bytes`/`bytearray` are — so
  both are available as element names.
- Watch for large code added to solve small problems. AI can make any spec work regardless of how much code it takes, so volume is not evidence of difficulty. Sprawling helpers, many-branched special cases, or duplicated logic across helpers usually indicate the spec was under-specified or the approach is wrong — pause and re-scope rather than piling on more code.

## Threading

- The serial reader runs on a thread **`SerialEngine` owns** (`start_reader` / `stop_reader`), not one the frontends create. Teardown is a real `Thread.join()`; `READER_JOIN_TIMEOUT_S` is a safety net, not a contract.
- `run_script()` in `repl.py` runs on a background thread via the `_run_script` wrapper.
- **Every command runs off the main thread, and none of them is marshalled to it.** Typed input, buttons, and palette entries each get their own `@work(thread=True)` worker (`app._dispatch_on_thread*`); script lines run on the script's own `_run_script` worker. Both paths land in `app._dispatch_single`, which calls `repl.dispatch_full` **inline on the calling thread** — there is no hand-off to main anywhere in dispatch. (This bullet used to claim the opposite, and that false version was the stated rationale for a live design decision, so treat it as load-bearing documentation.)
- **Output helpers marshal themselves.** `_status`, `_set_status_bar`, `_write_output_markup`, and `_on_main` compare `self._thread_id` and forward when off-main. Anything reached from a worker that touches widgets must go through one of those — never touch a widget directly.
- **No `read_loop` callback may block on another thread.** RX reaches the UI through `app._rx_enqueue` (append to an ordered buffer + at most one `post_message`), never a blocking `call_from_thread`. A blocking callback deadlocks teardown — `disconnect()` joins the reader from the very thread the callback waits for — and stalls the reader long enough for the driver to silently drop bytes (see `config.SERIAL_RX_BUFFER_BYTES`).
- `_dispatch_guard` is an `RLock` taken through `_acquire_dispatch_guard`, which is **non-blocking on the main thread and a bounded wait (`_DISPATCH_WAIT_S`) off it**. Main must never park on a lock a worker holds, or it deadlocks a worker waiting on `call_from_thread`; off main there is no such cycle, so a worker waits instead of dropping the line — a refused dispatch on a serial tool is a command that never reached the device, not a delayed one. After the wait expires it still fails with "Busy" rather than queueing. Re-entrant, so nested `ctx.dispatch` on the same thread is allowed.
- **One outermost script at a time, enforced in `ReplEngine.start_script` for every frontend.** `_script_owner` holds the thread id of the run in progress: a nested `/run` reaches `start_script` on that same thread (it executes inline via `_script_run`) and is allowed; any other thread — the picker, a button, another frontend — is refused. `run_script` re-stamps the owner because the TUI launches on a dispatch worker and runs on a `@work` thread. Do not add a second guard in a frontend; the engine owns this rule.
- **A plugin must never `clear()` the shared `script_stop_event`.** It belongs to the script, and clearing it erases a stop the user just requested. `/repeat` may clear it only when `ctx.internal.in_script()` is False, where it is itself the outermost cancellable operation.
- `_confirm()` blocks with `event.wait()` and posts a dialog via `call_from_thread` — must always be called from a background thread; main-thread misuse fails closed.
- `_BLOCKING_COMMANDS` in `repl.py` is **not** a threading mechanism despite its name. Those commands are intercepted because they need the `ScriptCtx` or must set `_script_stop` to abort the run — see the comment at its definition. The test for adding one is "does it need sctx or `_script_stop`?", never "does it block?".

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
- **`uv run ty check src/termapy/` must be 0** (same gate; the README badge tracks this and turns yellow/red on regression)
- Both tools are pinned exactly in `[dependency-groups] dev` and the rule set is pinned in `[tool.ruff.lint]`. Run them through `uv run`, never `uvx` / a global install — otherwise the gate enforces whatever version happens to be around, which is how ruff 0.16's wider defaults turned a clean tree into 636 findings. Bumping either is a deliberate chore branch with the new findings reviewed.
- **Run the gates with all extras synced** (`uv sync --all-extras`; `release_prep` does this itself before checking). ty's count depends on what's installed, not just on the source: without the extras the optional-dependency imports (mcp, jsonschema, textual_serve) report `unresolved-import`, and with them a suppression written for the missing case reads as unused. Both states are non-zero and mutually exclusive, so the gate names one environment — the richer one, which type-checks the optional code paths rather than skipping them. Consequence: do **not** add `# ty: ignore[unresolved-import]` to an optional-dependency import; guard it with `try/except ImportError` and let the extra be present at check time.

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
