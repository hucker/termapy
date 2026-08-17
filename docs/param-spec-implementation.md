# Declarative Command Parameters — Implementation Instructions

Status: draft for implementation (revised after a code-grounded review — see "Revision notes" at end)
Scope: termapy plugin argument declaration, parsing, validation, help generation, MCP catalog
Prereq reading: `src/termapy/plugins/command.py`, `src/termapy/repl.py` (`_dispatch_inner`, `_parse_flags`),
`src/termapy/scripting.py` (`parse_keywords`, `parse_duration`), `src/termapy/mcp/catalog.py`
(`_command_descriptor`), `src/termapy/builtins/commands/help.py` (`_render_man_page`)

---

## 1. Goal

Replace per-plugin hand-rolled argument parsing/validation/usage strings with a declarative
parameter spec on `Command`. A command declares its parameters once; the dispatcher parses,
coerces, validates, and fails with a uniform auto-generated usage message before the handler
runs. Help text and the MCP catalog derive parameter documentation from the same declaration.

Current duplication being eliminated (measured on main, `builtins/commands/`):

- **37** hand-written `Usage:` strings **inside `CmdResult.fail(...)`** — these are the ones a
  param spec retires. (There are ~102 further `Usage:`/parameter mentions in `long_help`,
  docstrings, and comments; those are prose and are only touched where a migrated command's
  `long_help` parameter *table* is deleted — see param-help-noduplicate.)
- **37** `except ValueError` parse/validate blocks (they line up 1:1 with the fail-usages).
- Every parameter documented in up to 3 places (handler parse code, `Command.args`,
  `long_help` parameter table) that can drift independently. (Verified live: `ping`'s
  `long_help` claims `timeout` "default: 1s" while the code defaults to `250ms` — exactly the
  drift this eliminates.)

## 2. Non-goals (do NOT do these in this work)

- **NG-1**: Do not change `SerialHandle` or any other ctx handle signatures.
- **NG-2**: Do not build the time runtime kernel (stop-aware sleep, budgets, cancellation).
  Duration *parsing* is in scope as a param type; sleeping/deadlines are not.
- **NG-3**: Do not merge with or import the profile `TypeRegistry`
  (`src/termapy/profile/types.py`). The vocabularies should *rhyme* (enum, int range) but
  plugin param coercion must not depend on profile loading. Convergence is a future task.
- **NG-4**: Do not change the existing `flags` mechanism or level-flag stripping. Params sit
  alongside flags, mirroring their *integration* pattern — but NOT their context-storage
  pattern (see param-ctx-nesting for why `active_flags`' set-then-clear is the wrong model
  to copy).
- **NG-5**: Do not change wire behavior of any command. This is a front-of-handler refactor.
  Where a migrated help string then disagrees with preserved behavior (the `ping` 250ms case),
  fix the *doc*, never the behavior.
- **NG-6**: Do not migrate `proto.py` in this pass (largest file, heaviest custom parsing —
  it gets its own follow-up once the mechanism is proven).

## 3. Design precedent — follow the flags *integration* pattern

`Command.flags` is the template for every *integration* decision. It already establishes:

1. Declaration lives on the `Command` dataclass.
2. Dispatcher (`_dispatch_inner` in `repl.py`) parses pre-handler, strips recognized tokens,
   fails dispatch with a clear message on error, and records results on the context.
3. Handler signature is unchanged `(ctx, args)`; handlers read parsed values through a
   context accessor (`ctx.flag(name)` → for params, `ctx.arg(name)`).
4. **Empty declaration = complete opt-out, zero behavior change.** This is the back-compat
   guarantee. A command with `params=[]` (default) is dispatched exactly as today.

Mirror properties 1–4. Do NOT mirror how `flags` *stores* its result on the context — see
param-ctx-nesting.

---

## 4. Specification

Spec IDs below are stable; reference them in commit messages and in test
names/docstrings.  (The first draft called for a `@pytest.mark.spec(...)`
marker "per the project traceability convention" -- no such convention exists
in this repo, so traceability lives in the test names/docstrings instead.)

### param-decl-dataclass

Add `ParamSpec` (frozen dataclass) to a new module `src/termapy/plugins/params.py`:

```python
@dataclass(frozen=True)
class ParamSpec:
    name: str                     # keyword name, lowercase (e.g. "timeout")
    type: str = "str"             # see param-types
    required: bool = False
    default: Any = None           # post-coercion value (e.g. 1.0 for "1s")
    help: str = ""                # one line; feeds help + MCP catalog
    hint: str = ""                # override synopsis type-hint (e.g. "<name>")
    positional: bool = False      # consumed from positional tokens, in declared order
    rest: bool = False            # consumes to end of line; at most one; must sort last
    values: tuple = ()            # enum only: EnumValue entries (see param-type-enum)
    min: float | None = None      # int/float only
    max: float | None = None
```

Add to `Command`:

```python
params: list[ParamSpec] = field(default_factory=list)
```

Validation of the *declaration itself* happens once, at load, in `Command.__post_init__`
(construction time — for builtins that is import, for external plugins it is the loader's
`getattr(mod, "COMMAND")`; both "fail at load, loudly"). `__post_init__` calls
`params.validate_param_specs(self.params, self.name)`, which raises `ValueError` on:
duplicate names; more than one `rest=True`; a `rest` param that is also `positional`; enum
with empty `values`; `default` present on a `required` param; `type="command"` without
`rest=True`; a `params`-carrying command that is also `raw_args=True`; and `args` non-empty
*and* `params` non-empty (one source of truth — declare or hand-write, never both).
(spec: **param-decl-validation**)

### param-decl-optin

`params=[]` (the default) means the dispatcher performs no param parsing and passes `args`
through untouched. Byte-identical dispatch behavior for undeclared commands. This is the
hard back-compat line; write an explicit regression test (dispatch a params-free command and
assert identical result + identical `args` reaching the handler).

### param-types

Type registry local to `plugins/params.py`, coercion functions `str -> (ok, value|error)`:

| type       | coercion                                             | notes |
|------------|------------------------------------------------------|-------|
| `str`      | identity                                             | |
| `int`      | `int(text)`; then min/max check                      | |
| `float`    | `float(text)`; then min/max check                    | |
| `duration` | `termapy.scripting.parse_duration` → float seconds   | canonical unit is **float seconds** at this boundary; handlers still `* 1000` at ms call sites (`ctx.serial.read_raw(timeout_ms=...)`), so "no `parse_duration(` in migrated handlers" is the gate, NOT "no `* 1000`". `parse_duration` raises `ValueError`; coercion catches it. |
| `enum`     | case-insensitive match against values + aliases      | see param-type-enum |
| `path`     | identity string (no resolution)                      | resolution stays in handlers (cap dir vs scripts dir differ); do NOT case-fold |
| `command`  | identity string                                      | semantic marker for `cmd=`-style params; declaration-validation REQUIRES `rest=True` (a command consumes to EOL); MCP catalog exposes the type so LLM clients know it's a nested command |

### param-type-enum

Enum values need aliases because existing commands accept them
(`mode=new|n|append|a`, `sep=comma|tab|space`). Represent as:

```python
@dataclass(frozen=True)
class EnumValue:
    canonical: str
    aliases: tuple[str, ...] = ()
```

Coercion returns the canonical form, so handlers compare against one spelling only (matches
`cap._parse_mode`, which already returns `'w'`/`'a'`). Error message lists canonical values
(aliases shown in long help only).

### param-parse-order

In `_dispatch_inner`, insert param parsing **after** `_parse_flags` (flags are stripped
first, exactly as today, and after level-suffix/level-flag resolution — do NOT reorder) and
**before** the handler call:

1. Tokenize with `parse_keywords(args, keywords={non-positional param names}, rest_keyword=<the rest param name, if any>)`.
   Reuse the existing function — its `key = value` normalization, case-insensitivity, and
   rest-keyword-consumes-to-EOL semantics are the established user-facing grammar and must
   not change. (spec: **param-parse-grammar**)
2. Assign positional params: split `result["_positional"]` on whitespace, bind to
   `positional=True` params in declaration order. **Positional values cannot contain spaces**
   (whitespace is the token boundary) — this matches every current site (`cap` does
   `positional[0]`), so NG-5 holds; state it, don't "fix" it. Extra positional tokens after
   all positional params are bound → dispatch failure (see param-fail-message).
   **Positional-rest** (added in the Phase-5 sweep): a positional param with
   `rest=True` is the *last* positional and consumes the whole remaining line as
   one value, so a command/regex/path may contain spaces (`/os ls -la`,
   `/grep foo bar`, `/profile.validate my file.json`).  It is mutually exclusive
   with a keyword rest (both would claim "the rest").  This was surfaced by a
   real regression: the Phase-3 `/profile.validate` migration used a plain
   positional `<path>`, which whitespace-split spaced paths — positional-rest is
   the fix.
   **Variadic positional**: a positional param with `variadic=True` is the *last*
   positional and binds every remaining token as a `list`, one `coerce_value` per
   element (so `list[int]` / `list[enum]` and per-element `min`/`max` work).
   `rest` and `variadic` are both tail consumers and are mutually exclusive on
   the same command — `rest` joins the tail into one string, `variadic` keeps the
   elements apart. A variadic **positional** and a rest **keyword** are different
   slots and DO compose (that pairing is what gives `/proto.crc.detect` both its
   `<frames>...` and its `frame=` forms). An absent variadic binds a fresh `[]`,
   so it never declares a default; a *required* variadic must bind non-empty.
   Declaration validation rejects: variadic on a keyword, `rest`+`variadic`
   together, a variadic with a default, two tail positionals, and a tail
   positional that is not last.
2b. Resolve `$(*NAME)` dereferences (spec: **param-deref**). Runs per token, in
   every token-scoped slot — fixed positionals, variadic elements, and non-`rest`
   keyword values — *after* the split and *before* coercion, which is what makes
   the arity of `$(*NAME)` exactly 1 regardless of the value's content. `rest`
   values are excluded on both sides: a rest value is a whole line, not an
   argument, so it has no arity to guarantee (use `$(NAME)` there). The resolver
   arrives as `parse_params(..., deref=)`, injected by the dispatcher so this
   module keeps no dependency on the variable store; `deref=None` (the default,
   and what `raw_args=True` commands get) disables it entirely. A resolved value
   is data — never re-split, re-scanned, or matched again.
3. Coerce each bound value per its type. First failure short-circuits.
4. Check `required` params are present.
5. Apply `default` for absent optional params (defaults are already-coerced values;
   do not run them through coercion).

### param-ctx-access

Bind results to the context via new internal storage `bound_params: dict[str, Any]` and a
public accessor on `PluginContext`:

```python
def arg(self, name: str, default: Any = None) -> Any:
    """Parsed value of a declared parameter for the current dispatch."""
    return self.bound_params.get(name, default)
```

Handlers keep signature `(ctx, args)`. `args` still arrives (post-flag-strip, pre-param-strip
— i.e., unchanged from today) so migrated handlers can ignore it and unmigrated code paths
are unaffected.

### param-ctx-nesting

`bound_params` is **saved before / restored after** the handler call, exactly like
`_call_level` (`saved = self.ctx._call_level` … `finally: self.ctx._call_level = saved` in
`_dispatch_inner`). Do **NOT** copy the `active_flags` pattern.

Why this matters (grounded in the current code): `active_flags` is *set then cleared to
`set()`* in the `finally`, not restored. A handler that calls `ctx.dispatch()` and then reads
`ctx.flag()` afterward already sees an empty set — tolerable for flags only because handlers
read them before dispatching. `ctx.arg()` must be readable throughout the handler, including
after a nested `ctx.dispatch()` (4 migration targets — `cap`, `repeat`, `term`, `var` —
dispatch inside their handlers). Save/restore is the only pattern that survives nesting:
the inner dispatch saves the outer's `bound_params`, installs its own, and restores the
outer's on exit. (spec: **param-ctx-nesting**)

> Note (NOT this work, NG-4): the `active_flags` set-then-clear means `ctx.flag()` read after
> a nested dispatch returns empty. It's latent (no current handler does this) — flag it in a
> follow-up, don't fix it here, and don't inherit it.

### param-fail-message

All parse/validation failures produce one uniform shape and never reach the handler:

```
Error: /cap.text: <reason>
Usage: /cap.text <file> timeout=<dur> {mode=new|append} {echo=on|off} {cmd=...}
```

Reason phrasing, fixed vocabulary (do not improvise variants):

- missing required: `missing required parameter 'timeout'`
- bad coercion: `invalid timeout: '2x' (expected duration, e.g. 500ms, 1.5s)`
- enum miss: `invalid mode: 'appendd' (expected one of: new, append)`
- range: `count must be between 1 and 1000 (got 0)`
- unexpected positional: `unexpected argument: 'foo'`
- undefined `$(*NAME)`: `unknown variable: 'frame'`
- malformed `$(*NAME)`: `invalid reference: 'x$(*a)y' ($(*NAME) must be the whole argument)`

The `Usage:` line is **derived** (see param-help-synth), not hand-written. This is what
retires the 37 hand-written fail-usages.

### param-help-synth

Synthesize the synopsis string from `params` when `Command.args == ""` and
`params` is non-empty:

- required → `name=<type-hint>` or `<name>` for positional
- optional → wrapped in `{...}`, matching the existing help convention documented on
  `Command.args`
- rest param rendered last with the existing `(must be last)` annotation
- duration hint is `<dur>`, enum hint is `a|b|c` (canonicals)

`help.py` (`_render_man_page`): (a) the SYNOPSIS section, currently gated on `if plugin.args`,
uses the synthesized synopsis when `args` is empty and `params` is present; (b) a new
`PARAMETERS` section is emitted after DESCRIPTION when `params` is non-empty — one line per
param: `name=<hint>    help-text (default: X)` — formatted to match the current hand-written
tables. During migration the hand-written parameter tables are **deleted** from `long_help`,
leaving only prose. A command that declares params and still contains a `Parameters:` block
in its `long_help` fails a lint test (spec: **param-help-noduplicate**).

`Command.args` non-empty + `params` non-empty → registration-time error (see
param-decl-validation). One source of truth.

### param-mcp-catalog

`mcp/catalog.py::_command_descriptor` (the dict currently exporting `args`/`help`/`long_help`/
`raw_args`): add `"params": [ {name,type,required,default,help,values} … ]`. This requires
`PluginInfo` (command.py:388, built at loader.py:260) to carry a `params` field copied from
`Command.params`. The synthesized synopsis continues to go out through the existing `args`
key so current consumers see no shape change. This gives MCP clients structured, typed
argument schemas for builtins — parity with profile `typed_args` for device commands.

### param-gold-tests

`tests/cli_gold` golden files change wherever error/usage messages are emitted. Rules:

- Gold-file updates land in the **same commit** as the command migration that caused them,
  never batched separately — the diff must show cause and effect together.
- A migration commit that changes gold files for a command it didn't touch is a bug.
- Regenerate via the test's own `_normalize` (see how the CLI gold `.expected` is produced),
  then eyeball the diff — the gold is the source of truth.

---

## 5. Phased plan (commit-sized)

**Phase 0 — mechanism** (no plugin behavior changes)

1. `plugins/params.py`: `ParamSpec`, `EnumValue`, type coercions, `validate_param_specs`,
   `parse_params`, `synthesize_synopsis`, `render_parameters_block`. Unit tests per spec ID.
2. `Command.params` field + `__post_init__` → `validate_param_specs`. `PluginInfo.params`.
3. `repl.py::_dispatch_inner` integration behind the `params` non-empty gate, `ctx.arg()`,
   `bound_params` save/restore. Regression test: dispatch of a params-free command is
   byte-identical (param-decl-optin).
4. `help.py` SYNOPSIS-from-params + PARAMETERS block; `catalog.py` export.

**Phase 1 — pilot: `/ping`** (smallest real surface: `count`, `timeout` duration,
`cmd` rest — and its `quiet` subcommand shares the same params). Deletes its usage string,
`except` blocks, and `long_help` parameter tables (both parent and `quiet`). Preserve the
250ms timeout default (NG-5) — the synthesized help will read `250ms`, fixing the current
"1s" drift. Validates the mechanism end-to-end including gold updates. **Stop and review the
diff shape before proceeding** — this commit is the template every later migration copies.

**Phase 2 — `/repeat`** (adds `int` with `min=1`, exercises required-vs-default).

**Phase 3 — `/profile`** subcommands.  Outcome (done): migrated `/profile.validate`
(a clean required positional `<path>` -- the first positional-required exercise).
`/profile.load` was **deliberately left hand-rolled** -- it is a three-way shape
dispatch (empty=reload / bare-path=file / `cmd=`=device) whose `{path|cmd=<command>}`
synopsis expresses a *mutual exclusion* the synthesized synopsis cannot (it would
degrade to independent optionals and shift edge disambiguation, e.g. filenames
containing `=`).  Forcing it would make the docs worse, so this is the first real
use of the escape hatch (a comment on the command anchors the reason here).
`/profile.save`'s only "Usage" is a state error (no cfg to derive a default path),
not argument parsing -- no migration value.

**Phase 4 — `/cap`** (largest win: 4 subcommands, enum-with-alias `mode`/`sep`,
positional `<file>`, required duration, rest `cmd`). Retire `_extract_keyword_sections` /
`_parse_mode` where param parsing now covers them; keep only if a subcommand has grammar the
spec genuinely can't express — and if so, record why in a comment anchored to this doc.

**Phase 5 — sweep** remaining simple commands (`os_cmd`, `var`, `find`, `grep`, etc.)
one commit each. `proto.py` explicitly deferred (NG-6).

Each migration commit must: (a) delete the hand-written fail-usage string(s),
(b) delete the parse/validate try/except blocks, (c) delete the `long_help` parameter
table lines, (d) update gold files, (e) carry `@pytest.mark.spec` markers on new tests.

## 6. Acceptance criteria

- [ ] `params=[]` commands: dispatch path provably unchanged (regression test, param-decl-optin)
- [ ] Migrated command rejects bad input with the uniform message shape before handler entry
- [ ] `/help <cmd>` for a migrated command shows synthesized synopsis + PARAMETERS block;
      no duplicated parameter docs remain in its long_help
- [ ] MCP catalog entry for a migrated command carries structured `params`
- [ ] Nested `ctx.dispatch()` inside a migrated handler does not corrupt outer bound params
      (explicit test: outer reads `ctx.arg()` after an inner dispatch)
- [ ] `key = value` spacing, case-insensitive keywords, and `cmd=` rest-consumption behave
      exactly as on main (grammar tests, param-parse-grammar)
- [ ] Grep gate at end of Phase 5: count of `Usage:` **inside `CmdResult.fail(`** in
      `builtins/commands/` reduced to proto.py + genuinely-custom-grammar holdouts; no
      `parse_duration(` calls remain in migrated handlers

## 7. Known landmines

- `parse_keywords` lowercases keyword matching but preserves value case — keep that; enum
  coercion handles case-insensitivity on the value side, `path`/`str`/`command` must not
  case-fold values.
- `repeat.py` imports `_VARS` from `var.py` inside its handler — unrelated coupling; leave it.
- cap.poll's `wait_gap` and the hardcoded 5s quiet-gap deadline are *runtime* time behavior —
  declare `wait_gap` as a duration param, do not touch the loop internals (NG-2).
- `raw_args=True` commands skip REPL transforms; `params` on a `raw_args=True` command is a
  registration-time error in v1 (8 such commands today).
- Level suffix (`/cmd.silent`) and level flags are resolved before flag parsing today;
  param parsing goes after both. Don't reorder.

## 8. Revision notes (2026-07-04, code-grounded review)

Changes from the first draft, all verified against the tree:

- Usage-string count corrected 47 → **37** (`CmdResult.fail` usages only; ~102 doc-usages
  are out of scope). §6 grep gate now targets `Usage:` inside `fail(`, not bare `Usage:`.
- **param-ctx-nesting rewritten**: the first draft said both "mirror `active_flags`" and
  "save/restore like `_call_level`" — contradictory. Ground truth: `active_flags` is
  set-then-cleared (not restored); `_call_level` is save/restore. `bound_params` MUST use
  save/restore, or `ctx.arg()` returns defaults after any nested dispatch. Rationale added.
- Declaration validation moved to `Command.__post_init__` (fires at load for both builtins
  and external plugins) and `command`-type-requires-`rest` added to it.
- Terminology fixed: the subcommand field is `sub_commands` (not "children"/"subcommands");
  the MCP hook is `_command_descriptor` (not `_plugin_entry`); `PluginInfo`/loader plumbing
  for `params` made explicit.
- Positional-values-cannot-contain-spaces limitation stated (matches current behavior).
- Duration gate clarified: handlers still `* 1000` for ms; the gate is "no `parse_duration(`".
- Noted the live `ping` default drift (help "1s" vs code 250ms) as the worked example of the
  triplicate-doc problem; migration preserves 250ms and fixes the doc via synthesis.
