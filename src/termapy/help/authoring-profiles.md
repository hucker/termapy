# Authoring device profiles

A device profile is a JSON file that tells termapy what commands a
serial device understands and how to interpret its responses.  The
MCP bridge consumes profiles to give LLMs structured device control:
typed JSON in, typed JSON out, with safety gating.

This page is for **anyone authoring a profile** — engineers writing
one by hand, and LLMs drafting one from a help dump.  It's the
single source of truth for the schema, the safety taxonomy, and
the rules.

## File layout

A profile is a JSON object with these top-level keys:

```json
{
  "profile_version": 2,
  "profile_revision": "1.0.0",
  "profile_date": "2026-05-03",
  "device":          {"name": "...", "vendor": "...", "model": "..."},
  "transport":       {"protocol": "text", "baud_rate": 115200, ...},
  "error_detection": {"pattern": "^ERROR(?::\\s*(?P<message>.+))?$"},
  "commands":        {"AT": {...}, "AT+TEMP": {...}, ...}
}
```

Only `commands` is strictly required.  Everything else is optional but
strongly recommended — `transport.line_ending_send` in particular
controls whether bytes get terminated with `\r`, `\n`, or `\r\n` on
the wire.

## Per-command schema

Each entry in `commands` describes one device command:

```json
"AT+TEMP": {
  "enabled": true,
  "help":    "Read temperature",
  "args":    "",
  "long_help": "Returns the on-board sensor reading...",
  "safety":  "readonly",
  "response": {
    "format":  "regex",
    "pattern": "(?P<celsius>-?\\d+\\.\\d+)C",
    "types":   {"celsius": "float"},
    "timeout_ms": 200
  }
}
```

### `enabled` (boolean, default true)

The kill switch.  `false` means "this entry exists in the profile but
is hidden from the MCP catalog and not dispatched by the executor."
A bare invocation of a disabled command falls through to literal
`/term.send` (the same behavior you get without any profile at all).

**When drafting a profile for a legacy device** — for example after
pasting a device's `help` output into Claude — every entry MUST start
with `enabled: false`.  The engineer reviews each command and flips
the flag to `true` only after auditing safety, args, and (ideally)
testing one or two sample responses.  The default of `true` exists
to keep curated profiles working unchanged; it is **not** the default
you should emit when generating a draft.

### `help` (string, required)

One-line description.  Shown by `/help <cmd>`.  Copy verbatim from
the device's own help table when authoring from a help dump.

### `args` (string, optional)

Argument syntax for human display.  Conventions:
`<required>`, `{optional}`, `a|b|c` for enums.

### `long_help` (string, optional)

Multi-line prose for `/help <cmd>`.  Useful for commands with subtle
semantics, examples, or warnings.

### `safety` (string, default `"safe"`)

Four tiers, in order of increasing caution:

| Tier          | Meaning                                            | Examples                          |
|---------------|----------------------------------------------------|-----------------------------------|
| `readonly`    | Pure observation; no state change                  | `bat`, `temp_c`, `version`, `info`|
| `safe`        | Default; no enduring effect                        | `help`, `clr`                     |
| `mutable`     | Changes device state but reversible                | `set_led on`, `baud 9600`         |
| `destructive` | Irreversible / data-loss / requires confirmation   | `reset`, `factory_clear`, `erase` |

**Only `destructive` triggers the MCP confirmation gate.**  The
LLM cannot run a destructive command without explicit `confirm=true`
on the tool call (which a well-behaved client elicits from the user).
The other tiers are documentation that helps the LLM and the
engineer reason about the device.

When in doubt between `mutable` and `destructive`: **err on the side
of `destructive`**.  Friction is recoverable; data loss isn't.

### `response` (object, optional)

Describes how to parse the device's reply.  Five formats:

| `format`   | What it does                                                                 | Returned `value` shape          |
|------------|------------------------------------------------------------------------------|---------------------------------|
| `none`     | Fire-and-forget; don't wait for a reply                                      | `{"sent": true, "cmd": "..."}`  |
| `literal`  | Reply must equal `pattern` exactly                                           | the string (or fail)            |
| `regex`    | `re.search(pattern, response)` — named groups + `types` coerce to dict       | `{"celsius": 23.4, ...}`        |
| `lines`    | Collect lines until `terminator` regex matches or idle gap                   | `["line1", "line2", ...]`       |
| `json`     | Parse the full response as one JSON document                                 | the parsed value                |

Common patterns:

- **Single value with units:** `regex` with `(?P<value>\d+\.\d+)V`,
  `types: {value: float}`.
- **Multi-line dump ending with `OK`:** `lines` with `terminator: "^OK$"`.
- **Multi-line with no terminator:** `lines` (idle-gap collection).
- **NDJSON device:** `json`.
- **Side-effect command, no reply:** `none`.

### `response.timeout_ms` (integer)

How long to wait for the response before timing out.  Defaults to
`transport.default_response_timeout_ms` (which itself defaults to
1000 ms).  Bump for slow operations like resets, self-tests, or
flash erases.

### `response.types` (object, optional)

Maps regex named-group names to type coercions.  Recognized type
strings: `int`, `float`, `bool`, `hex` (parses base-16), `str`
(default).  Failed coercions silently fall back to the raw string —
the LLM gets *something* even on bad data.

### `send_template` (string, optional)

For commands that take a single inline argument.  Use Python-format
syntax: `"AT+LED {state}"` (note the space — the wire syntax matches
the device, not necessarily termapy convention).  The LLM types
`AT+LED on`; termapy matches against the template, sends it through.

If omitted, the command is sent verbatim.

### `typed_args` (array, optional)

Structured argument schema used by codegen tools.  Each entry:
`{"name": "...", "type": "<builtin or custom name>", "required": true,
"help": "...", "enum": [...], "min": ..., "max": ...}`.

`type` accepts either a **builtin** (`int`, `float`, `bool`, `hex`,
`str`) or a **custom name declared in the profile's top-level `types`
block** (see below).  When the MCP dispatcher binds a `typed_args`
entry, the validator runs before the request hits the wire — bad
values short-circuit to a structured failure naming the rejected
value, the violated constraint, and the canonical command name.

## Profile-local types

A v2 profile may declare a top-level `types` block — a map of named
user-defined types referenced by `typed_args[i].type`.  This is how a
device declares its own argument vocabulary (e.g. one device's
lenient bool of `on/off/true/false/yes/no/1/0/high/low` vs. another's
strict `0/1`) without forking the schema.

The five builtins always resolve directly; custom names cannot
shadow them (`bool`, `int`, etc. are reserved).  Six `kind` values
are recognized:

| `kind` | Required fields | Behavior |
| --- | --- | --- |
| `enum` | `values` (array) | Exact-match against the list; values stringified for compare |
| `int_range` | `min`, `max` | Coerce to int; check `min ≤ v ≤ max` |
| `float_range` | `min`, `max` | Coerce to float; same bounds check |
| `str_length` | `min_len` and/or `max_len` | Coerce to str; check length against bounds |
| `pattern` | `regex` | `re.fullmatch(regex, value)` — anchored both ends |
| `format_spec` | `spec` | Parsed via the protocol format-spec language; validator is a pass-through stub today |

Example:

```json
{
  "types": {
    "onoff":    {"kind": "enum", "values": ["on", "off", "true", "false", "yes", "no", "1", "0"]},
    "baud":     {"kind": "enum", "values": [9600, 19200, 38400, 57600, 115200]},
    "percent":  {"kind": "int_range", "min": 0, "max": 100},
    "voltage":  {"kind": "float_range", "min": 0.0, "max": 5.0},
    "nickname": {"kind": "str_length", "min_len": 1, "max_len": 16},
    "duration": {"kind": "pattern", "regex": "^\\d+(us|ms|s)$"},
    "byte":     {"kind": "format_spec", "spec": "Val:H1"}
  },
  "commands": {
    "ECHO":    {"help": "Toggle echo.", "typed_args": [{"name": "state", "type": "onoff"}]},
    "SETBAUD": {"help": "Change baud.",  "typed_args": [{"name": "rate",  "type": "baud"}]},
    "SETDUTY": {"help": "Duty cycle.",   "typed_args": [{"name": "pct",   "type": "percent"}]}
  }
}
```

Notes:

- **`format_spec` is a wired-up stub.**  The schema accepts it and the
  registry parses the `spec` string via the same format-spec parser
  used by `/proto.*` (see the protocol-testing guide).  The validator
  is currently a pass-through — calls succeed without checking
  individual bytes — so authors can declare binary-field types today
  and the byte-level enforcement lands when needed.
- **Case is significant.**  Enum members match exactly.  If a device
  accepts both `ON` and `on`, list both explicitly.
- **No `types` block ⇒ no behavior change.**  Existing profiles using
  only builtin `typed_args.type` values keep working identically.
- The catalog emits the `types` block verbatim and inlines a
  `type_info` field on each `typed_args` entry so an LLM reading the
  catalog sees the full contract per arg without cross-referencing.

## Top-level blocks

### `transport`

Wire-level rules.  Most useful keys:

- `protocol`: `"text"` (default) or `"ndjson"`
- `line_ending_send`: `"\r"`, `"\n"`, or `"\r\n"` — bytes appended on send
- `line_ending_recv`: same vocabulary, used to split incoming text
- `encoding`: `"utf-8"`, `"latin-1"`, `"ascii"`, etc.
- `default_response_timeout_ms`: integer, default 1000

### `error_detection`

Server-side error pattern, applied across all responses.  When the
device returns text matching `pattern`, the executor fails the call
with the captured `message` group as the error string.  Wins over
`response.pattern` if both could match.

```json
"error_detection": {
  "pattern": "^ERROR(?::\\s*(?P<message>.+))?$"
}
```

### `device`

Documentation only: `name`, `vendor`, `model`, optional `prompt`
string, optional `startup_banner` regex.

## Authoring rules for LLMs drafting from a help dump

When a user pastes a device help table and asks for a profile draft:

1. **Every entry gets `enabled: false`.**  No exceptions.  The user
   reviews and flips them on one at a time.

2. **Copy `help` and `args` verbatim** from the help table.

3. **Classify `safety` from the description.**  Use the table above.
   When uncertain, prefer the more conservative tier and add a note
   to the top-level `_notes` block (see below).

4. **Default `response.format: "lines"`** with no terminator and
   `timeout_ms: 1000`.  This produces a list-of-strings result that's
   safe and useful even without sample responses.

5. **Bump `response.timeout_ms`** for commands whose names or help
   text suggest slow operations: `reset`, `mfg`, `standby`, anything
   involving `flash`, `wait`, `erase`, `cal`.

6. **Don't invent `response.pattern` or `types` without sample
   responses.**  A wrong regex is worse than `format: lines`.  If
   the user provides sample responses, *then* upgrade to `regex`.

7. **Mark hazardous commands** even when not strictly destructive.
   GPIO writes, motor controls, RF transmits, fee-based API calls —
   set `safety: destructive` so the gate fires and add a note.

8. **Add a top-level `_notes` block** summarizing what was inferred
   and what needs human review:

   ```json
   "_notes": {
     "drafted_from": "help output pasted by user",
     "needs_review": [
       "mfg — could be destructive (factory programming?); please confirm",
       "baud — changes wire baud rate; bridge may need reconnect handling",
       "gpio — drives outputs; consider physical safety implications",
       "repeat — recursive, response shape depends on inner command"
     ]
   }
   ```

   Underscore-prefixed keys are accepted by the schema as metadata.

9. **Never set `enabled: true` in a draft**, even for commands that
   look obviously safe like `version`.  The user toggles each one
   themselves — that's the audit signature.

## Workflow for the engineer

1. Get a help dump from the device.
2. (Optional) Capture sample responses for high-value commands.
3. Open Claude (or another LLM) in chat.
4. Paste the help dump and ask for a v2 profile draft.  The LLM
   reads this guide (embedded in the `draft_profile` MCP prompt)
   and produces a draft with `enabled: false` everywhere.
5. Save the draft as `<cfg>/<device>.profile.json`.
6. Run `/profile.load <device>.profile.json` in termapy.
7. Review each command.  Set `enabled: true` on the ones you've
   audited.  Reload (`/profile.load` again) to pick up changes.
8. Test against the real device through MCP.
9. As you discover response shapes, upgrade entries from
   `format: lines` to typed `regex`.

The profile is just a JSON file; edit it freely.

## See also

- `/profile.validate <path>` — schema check against this guide.
- `/profile.load <path>` — load a profile and apply transport rules.
- `/profile.info` — inspect the active profile.
- `/mcp.info` — see destructive count, enabled-vs-draft split.
- `/include` — for devices that publish their own profile via a
  command like `AT+HELP.JSON`.  The reverse direction of authoring.
