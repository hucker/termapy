# Termapy Device Profile Specification (profile_version 2)

Normative reference for the device-profile format: a declarative JSON
description of a REPL-style device's command catalog, argument types,
response contracts, and safety metadata.

The machine-checkable half of this spec is
[`src/termapy/profile/schema.json`](../src/termapy/profile/schema.json)
(JSON Schema, Draft 2020-12). This document carries the semantics a
schema cannot: matching rules, timing behavior, coercion tables, and
the compatibility policy. Where the two disagree, this document wins
and the schema has a bug.

Termapy's MCP bridge is the *reference implementation*, not the owner:
any tool may consume or produce profiles. The `termapy.profile`
subpackage (loader, validator, type registry, matcher) is a standalone
kernel with no engine/MCP/UI dependencies, usable as a library.

---

## 1. Scope and division of responsibility

A profile describes **the device's contract**: what commands it
accepts, what they mean, what comes back, and how dangerous they are.

A profile deliberately does **not** describe the user's session: baud
rate, port, line ending, encoding, and protocol framing live in the
termapy cfg file, which points at the profile via `profile_path`. The
profile never points back. One profile serves any wiring of the same
device (UART today, TCP tomorrow).

Three producers write profiles, in increasing order of effort:

1. **Self-describing firmware** — the device emits its own (usually
   minimal) profile as JSON over the wire.
2. **Generated** — `--profile-from-help` converts a captured help dump
   into a draft profile (every entry `enabled: false` until audited).
3. **Hand-authored** — an engineer curates the full contract, usually
   starting from one of the above.

## 2. File format

- **JSON is canonical.** UTF-8, no comments. Validation, caching,
  wire transfer, and `save` all use JSON.
- **TOML is accepted on load** as an authoring convenience (raw regex
  strings without double-escaping, comments, multi-line help). A save
  cycle rewrites it as JSON; there is no format preservation.
- Instance files SHOULD carry
  `"$schema": "https://termapy.org/profile.schema.json"` for editor
  tooling. Keys beginning with `$` are reserved for such conventions.
- **Extension namespace:** keys beginning with `x_` or `x-` are yours
  at every level; conforming consumers never warn about them and never
  assign them meaning.

## 3. Versioning and compatibility policy

This is the load-bearing section. The design goal: **a profile written
for a newer spec revision degrades gracefully on an older host; it
never hard-fails unless the major version changed.**

### 3.1 The one hard gate

`profile_version` (integer, currently `2`) is the only version check
that may reject a profile. It bumps **only for breaking changes** —
reshaped fields, changed meaning of existing fields. Hosts MUST reject
a profile whose major version they do not implement, with a clear
message naming both versions.

`profile_revision` (semver string) and `profile_date` (ISO date)
version the *content*, not the format. They drive precedence when two
copies of a profile compete (higher revision wins, then newer date,
then device-fetched beats hand-authored).

### 3.2 Additive evolution: unknown fields

New optional fields may appear within major version 2 at any level.
Consumers MUST ignore fields they do not recognize. Linters and
validators SHOULD surface them as warnings (never errors) so authors
catch typos — termapy reports them as "compatibility warnings" on
`/profile.load` and `/profile.validate`.

### 3.3 Additive evolution: unknown values

Open vocabularies degrade with **defined semantics** instead of
failing. A conforming consumer MUST implement this table:

| Field | Canonical values | Unrecognized value behaves as |
|---|---|---|
| `response.format` | `none, text, literal, lines, regex, json` | `text` — return the raw response string. Data stays usable; shape is the most conservative one. |
| `safety` | `safe, readonly, mutable, destructive` | `destructive` — require confirmation. A future stronger-than-destructive tier must gate on old hosts, not silently run. |
| `types.<name>.kind` | `enum, int_range, float_range, str_length, pattern, format_spec` | Fail-closed at *dispatch*: the profile loads, but any argument declared with that type refuses to send ("cannot validate → do not transmit"). |
| coercion names (`response.types`, `line_types`, `error_detection.types` values) | `int, float, bool, hex, str` | `str` — the captured text passes through raw. |

The direction of each degrade is chosen by what it protects: response
handling degrades toward *availability* (you still get your data),
safety degrades toward *caution* (you must confirm), argument typing
degrades toward *not sending unvalidated bytes to hardware*.

`profile_version` itself is deliberately excluded from this leniency.

### 3.4 Reserved fields

Fields the schema accepts but the reference bridge does not act on
yet. Producers may set them; consumers preserve and may surface them.

- `commands.<name>.timeout_ms` — reserved; use `response.timeout_ms`.
- `commands.<name>.subcommands` — reserved for nested catalogs; the
  canonical spelling of a multi-word command today is a **flat key
  containing spaces** (`"cobs encode"`).
- `commands.<name>.rate_limit_hz` — advisory; exported to the MCP
  catalog for the consumer to honor, not yet enforced by the bridge.

## 4. Top level

| Field | Type | Notes |
|---|---|---|
| `profile_version` | int, required in practice | Must be `2` (§3.1). |
| `profile_revision` | semver string | Content version; drives precedence. |
| `profile_date` | `YYYY-MM-DD` | Precedence tiebreaker. |
| `device` | object | `name`, `vendor`, `model` (identity); `prompt` (trailing prompt string, default response terminator when set); `startup_banner` (regex; the bridge logs match/non-match after connect, never blocks). |
| `error_detection` | object | Global error grammar, §8. |
| `types` | object | Named argument types, §6. |
| `commands` | object | The catalog, §5. Keys are command names exactly as the device expects them. |

A minimal valid profile — what tiny self-describing firmware should
emit — is:

```json
{"profile_version": 2,
 "commands": {"rev": {"help": "Show hardware revision"}}}
```

Catalog and help work immediately; commands without a `response`
contract fall through to the literal-write path.

## 5. Commands and matching

### 5.1 Entry shape

Only `help` is required. Everything else — `long_help`, `typed_args`,
`send_template`, `response`, `safety`, `enabled`, legacy v1 fields
(`args`, `flags`) — is optional.

### 5.2 Matching algorithm (normative)

Given an input line (REPL prefix already absent — prefixed lines never
consult the profile):

1. **Exact key match.** If the line equals a key in `commands`, that
   entry matches with **no bound arguments**.
2. **Template match.** Otherwise, iterate `commands` **in key order as
   written in the file**; for each entry with a `send_template`,
   convert the template to an anchored regex (each `{name}` placeholder
   becomes a lazy named group `(?P<name>.+?)`, literal text is
   regex-escaped) and try it. **First hit wins.** Authors are
   responsible for avoiding overlapping templates.
3. No match → the line is not profile-governed (termapy falls through
   to a literal write).

### 5.3 What goes on the wire

The caller's text goes to the device **verbatim** (plus the cfg's line
ending). The host never renders templates, never substitutes defaults,
never reorders arguments. `send_template` exists to *recognize and
bind* inline arguments, not to construct output.

Consequences, spelled out:

- `typed_arg.default` documents **the device's behavior** when the
  argument is omitted. `required: true` + `default: "wdt"` is
  meaningful: the wire grammar has a slot, and bare invocation lets the
  device fill it.
- Bare invocation of a templated command (exact key match) binds no
  arguments, so argument validation does not run for it.

### 5.4 Gates, in order

Before any bytes are sent: (1) `enabled: false` refuses outright — the
entry exists but has not been audited (generated profiles start this
way); (2) the safety gate (§9); (3) typed-argument validation (§6).
Refusals from earlier gates win over later ones.

## 6. Argument typing

`typed_args` is a list of `{name, type, required?, default?, help?,
unit?, min?, max?, enum?}` descriptors. `type` resolves against five
**builtins** — `int`, `float`, `bool`, `hex`, `str` — then against the
profile's top-level `types` block.

A custom type is `{kind, help?, ...}` with per-kind fields:

| kind | Required fields | Accepts |
|---|---|---|
| `enum` | `values` (str/number/bool; stringified) | exact match against the list |
| `int_range` | `min`, `max` | integer within bounds |
| `float_range` | `min`, `max` | float within bounds |
| `str_length` | `min_len` and/or `max_len` | string length within bounds |
| `pattern` | `regex` | `re.fullmatch` against the regex |
| `format_spec` | `spec` | parsed for catalog display; validation is a declared stub today |

Custom type names MUST NOT shadow builtins (load-time error). A type
definition that fails to build (bad regex, missing fields, unknown
kind) does not block the profile: it carries its error and any argument
using it refuses at dispatch with that message — fail-closed per §3.3.

Validation runs **before the wire**: a rejected argument means nothing
was transmitted.

`unit` (on a typed arg, and per named group via `response.units` /
`response.line_units`) is purely descriptive metadata — e.g. `"mV"`,
`"cdeg"`, `"ms"` — surfaced to humans and LLMs. No conversion is ever
performed. Declare it whenever a bare number would be ambiguous.

## 7. Response contracts

`response.format` declares the shape; omitting `response` entirely
means the command has no contract (literal write, no shaped value).

| format | Waits | Produces | Notes |
|---|---|---|---|
| `none` | verify-silence window | `{"sent": true}` | The contract says *no reply*. Default window 100 ms; any non-whitespace output inside it fails the contract (catches stale profiles and firmware regressions). `timeout_ms: 0` opts out of waiting entirely (true fire-and-forget). |
| `text` | yes | the whole response as one string | Unstructured human-oriented output: help screens, dumps, diagnostics. No pattern needed. Also the degrade target for unknown formats. |
| `literal` | yes | the stripped text, if it equals `pattern` | Fixed acknowledgements (`OK`). |
| `lines` | yes | `list[str]`, or `list[dict]` with `line_pattern` | Collects until `terminator` (regex), else `device.prompt` if set, else idle timeout. `pattern` filters kept lines; `line_pattern` named groups (+ `line_types`) parse each line. |
| `regex` | yes | dict of named groups (+ `types` coercion), list for anonymous groups, else matched substring | `re.search` over the collected text. |
| `json` | yes | the parsed JSON value | Applied to the collected response text. |

### 7.1 Timing (normative for the reference bridge)

- Wait window: `response.timeout_ms`, else the cfg's
  `default_response_timeout_ms` (bridge default 1000).
- Collection ends early when no new line arrives for the idle gap:
  100 ms for `lines`, 50 ms for other waiting formats.
- Empty response where the format expects one → failure
  (`No response within <t>`); the raw text (if a parser refused it) is
  surfaced alongside the failure so nothing is hidden.
- Before every profile-governed send, stale buffered lines are drained
  and archived as `pre_send_drain` async events — late output from a
  previous command is preserved, attributed, and kept out of this
  command's parse.

### 7.2 Coercion (normative)

| name | Rule | On failure |
|---|---|---|
| `int` | base-10 `int()` | raw string passes through |
| `float` | `float()` | raw string |
| `hex` | base-16 `int()` | raw string |
| `bool` | case-insensitive: true ⇐ `on 1 true yes y t`; false ⇐ `off 0 false no n f` | raw string |
| `str` | identity | — |

Coercion never raises and never discards: a value that will not coerce
arrives as the raw string. If a regex matched, the LLM gets data.

## 8. Error detection

`error_detection.pattern` is the device's global error grammar (e.g.
`(?i)^(ERROR|ERR|FAULT)\b`). It runs over every collected response
**before** response parsing; a hit short-circuits the command to a
failure carrying the matching line (or the pattern's `message` group if
it defines one). `codes` optionally maps error codes to human-readable
meaning. `async: true` declares that errors can arrive unsolicited
between commands; such matches route to the async-error channel rather
than failing an in-flight command.

## 9. Safety and gating

Canonical tiers, in increasing order of concern: `readonly` (pure
observation), `safe` (no enduring effect — also the default when the
field is omitted), `mutable` (state-changing but reversible),
`destructive` (irreversible or data-loss).

The reference bridge requires explicit confirmation (`confirm=true`)
for `destructive` and for any unrecognized tier (§3.3). The gate is a
**cooperative marker, not a security boundary**: it surfaces the
profile's declared metadata so a well-behaved client confirms with the
human; raw literal writes bypass it by construction. Real protection
belongs to the device.

Authoring rule: any profile containing side-effecting commands SHOULD
set `safety` explicitly on every command rather than rely on the
default.

## 10. Regex dialect

All patterns (`response.pattern`, `terminator`, `line_pattern`,
`error_detection.pattern`, type `regex`, `startup_banner`) are
**Python `re` syntax**. Named groups use `(?P<name>...)`. Portable
profiles SHOULD stay within the common subset (character classes,
anchors, alternation, quantifiers, named groups, `(?s)`/`(?i)` flags)
and avoid lookbehind and backreferences. Prefer `format: text` over a
catch-all regex — `(?s)(?P<text>.*)` is the legacy spelling of what
`text` now says directly.

## 11. Conformance summary

A conforming **producer**: emits UTF-8 JSON matching the schema;
bumps `profile_revision` on any content change; bumps nothing else for
additive changes; uses `x_*` keys for private extensions.

A conforming **consumer**: rejects only on unsupported
`profile_version`, malformed JSON/TOML, or structural violations
(non-object command, missing `help`); ignores unknown fields (warning
allowed, failure not); implements the §3.3 degrade table; treats the
retired `transport` block as a hard error with a migration message
(the one deliberate exception, because silently ignoring it would
change wire behavior).

## 12. Worked example

```json
{
  "$schema": "https://termapy.org/profile.schema.json",
  "profile_version": 2,
  "profile_revision": "1.0.0",
  "profile_date": "2026-07-11",
  "device": {"name": "Acme PSU", "vendor": "Acme", "model": "PSU-100",
             "prompt": "> "},
  "error_detection": {"pattern": "(?i)^ERR\\b",
                      "codes": {"03": "interlock open"}},
  "types": {
    "channel": {"kind": "enum", "values": ["1", "2"],
                "help": "Output channel."}
  },
  "commands": {
    "idn": {"help": "Identify the instrument.",
            "safety": "readonly",
            "response": {"format": "text", "timeout_ms": 500}},
    "volt": {"help": "Read output voltage.",
             "send_template": "volt {ch}",
             "typed_args": [{"name": "ch", "type": "channel",
                             "required": true}],
             "safety": "readonly",
             "response": {"format": "regex",
                          "pattern": "(?P<mv>-?\\d+)",
                          "types": {"mv": "int"},
                          "units": {"mv": "mV"},
                          "timeout_ms": 500}},
    "output": {"help": "Switch an output on or off.",
               "send_template": "output {ch} {state}",
               "typed_args": [
                 {"name": "ch", "type": "channel", "required": true},
                 {"name": "state", "type": "bool", "required": true}],
               "safety": "mutable",
               "response": {"format": "literal", "pattern": "OK",
                            "timeout_ms": 500}},
    "factory_reset": {"help": "Erase calibration and settings.",
                      "safety": "destructive",
                      "response": {"format": "none"}}
  }
}
```
