# Authoring device profiles

A device profile is a JSON file that tells termapy what commands a
serial device understands and how to interpret its responses.  The
MCP bridge consumes profiles to give LLMs structured device control:
typed JSON in, typed JSON out, with safety gating.

This page is for **anyone authoring a profile** — engineers writing
one by hand, and LLMs drafting one from a help dump.  For the
normative format specification (matching rules, timing semantics,
and the forward-compatibility policy), see `docs/profile-spec.md`
in the repository; this page is the practical guide.

Note: AI such as Claude Code can do a pretty good job of sending commands interactively
without a profile at all, just looking at any help text or sample responses that you give it. It
can work with fairly well with a small amount of trial and error.  However, if you are going
to use AI to interact with your device on an ongoing basis it is worth it to invest in creating
a profile which reduces friction and gives you more robust control and better error handling
because the LLM can understand the commands and the types.

IF you have control over the device firmware, build a command that returns the json profile directly from the device.  
This way users of your device can be up and running with AI control in a few minutes.

## File layout

A profile is a JSON object with these top-level keys:

```json
{
  "profile_version": 2,
  "profile_revision": "1.0.0",
  "profile_date": "2026-05-03",
  "device":          {"name": "...", "vendor": "...", "model": "..."},
  "error_detection": {"pattern": "^ERROR(?::\\s*(?P<message>.+))?$"},
  "types":           {"on_off": {"kind": "enum", "values": ["on", "off"]}},
  "commands":        {"AT": {...}, "AT+TEMP": {...}, ...}
}
```

Only `commands` is strictly required.  Everything else is optional.

**Wire-level settings live in cfg, not the profile.**  Baud rate,
byte size, parity, stop bits, flow control, encoding, and line
ending are session properties — they depend on the user's USB
adapter and hardware setup, not on the device contract.  Set them
in your `termapy_cfg/<name>/<name>.cfg` file.  See `help config`
for the field reference.  For NDJSON devices, set `cfg.protocol`
to `"ndjson"`; the default `"text"` covers most devices.

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

| Tier          | Meaning                                          | Examples                           |
| ------------- | ------------------------------------------------ | ---------------------------------- |
| `readonly`    | Pure observation; no state change                | `bat`, `temp_c`, `version`, `info` |
| `safe`        | Default; no enduring effect                      | `help`, `clr`                      |
| `mutable`     | Changes device state but reversible              | `set_led on`, `baud 9600`          |
| `destructive` | Irreversible / data-loss / requires confirmation | `reset`, `factory_clear`, `erase`  |

**Only `destructive` triggers the MCP confirmation gate.**  The
LLM cannot run a destructive command without explicit `confirm=true`
on the tool call (which a well-behaved client elicits from the user).
The other tiers are documentation that helps the LLM and the
engineer reason about the device.

A tier name termapy doesn't recognize is treated as `destructive`
(confirmation required): if a future spec revision adds a stronger
tier, older hosts gate it instead of silently running it.

When in doubt between `mutable` and `destructive`: **err on the side
of `destructive`**.  Friction is recoverable; data loss isn't.

### `response` (object, optional)

Describes how to parse the device's reply.  Six formats:

| `format`  | What it does                                                              | Returned `value` shape         |
| --------- | ------------------------------------------------------------------------- | ------------------------------ |
| `none`    | Verify silence: wait briefly (default 100 ms), fail if the device replies | `{"sent": true, "cmd": "..."}` |
| `text`    | Whole reply as one unstructured string — help screens, dumps, diagnostics | the raw string                 |
| `literal` | Reply must equal `pattern` exactly                                        | the string (or fail)           |
| `regex`   | `re.search(pattern, response)` — named groups + `types` coerce to dict    | `{"celsius": 23.4, ...}`       |
| `lines`   | Collect lines until `terminator` regex matches or idle gap                | `["line1", "line2", ...]`      |
| `json`    | Parse the full response as one JSON document                              | the parsed value               |

A format name termapy doesn't recognize degrades to `text` — the
call still returns the raw response instead of failing, so profiles
written for a newer spec stay usable.  Prefer `text` over the old
catch-all `regex` pattern `(?s)(?P<text>.*)`.

Common patterns:

- **Single value with units:** `regex` with `(?P<value>\d+\.\d+)V`,
  `types: {value: float}`.
- **Multi-line dump ending with `OK`:** `lines` with `terminator: "^OK$"`.
- **Multi-line with no terminator:** `lines` (idle-gap collection).
- **NDJSON device:** `json`.
- **Side-effect command, no reply:** `none`.  The executor waits a
  short window (default 100 ms, override with `timeout_ms`) and fails
  the call if the device replies anyway — catching stale profiles and
  firmware regressions instead of silently dropping the unexpected
  bytes.  Whitespace-only replies are ignored.  Set `timeout_ms: 0`
  to opt out of the silence check entirely (true fire-and-forget).

### `response.timeout_ms` (integer)

How long to wait for the response before timing out.  Defaults to
`cfg.default_response_timeout_ms` (which itself defaults to 1000
ms).  Bump for slow operations like resets, self-tests, or flash
erases.

### `response.types` (object, optional)

Maps regex named-group names to type coercions.  Recognized type
strings: `int`, `float`, `bool`, `hex` (parses base-16), `str`
(default).  Failed coercions silently fall back to the raw string —
the LLM gets *something* even on bad data.  Unrecognized coercion
names behave as `str`.

### `response.units` / `typed_args[].unit` (optional)

Unit-of-measure metadata: `"units": {"mv": "mV"}` on a response,
`"unit": "ms"` on an argument.  Purely descriptive — no conversion
happens — but it is exactly what an LLM needs to interpret a bare
number like `-287` (centi-degrees? volts?).  Declare it whenever a
value's unit isn't obvious from the name.

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

| `kind`        | Required fields            | Behavior                                                                             |
| ------------- | -------------------------- | ------------------------------------------------------------------------------------ |
| `enum`        | `values` (array)           | Exact-match against the list; values stringified for compare                         |
| `int_range`   | `min`, `max`               | Coerce to int; check `min ≤ v ≤ max`                                                 |
| `float_range` | `min`, `max`               | Coerce to float; same bounds check                                                   |
| `str_length`  | `min_len` and/or `max_len` | Coerce to str; check length against bounds                                           |
| `pattern`     | `regex`                    | `re.fullmatch(regex, value)` — anchored both ends                                    |
| `format_spec` | `spec`                     | Parsed via the protocol format-spec language; validator is a pass-through stub today |

Example:

```json
{
  "types": {
    "on_off":    {"kind": "enum", "values": ["on", "off", "true", "false", "yes", "no", "1", "0"]},
    "baud":     {"kind": "enum", "values": [9600, 19200, 38400, 57600, 115200]},
    "percent":  {"kind": "int_range", "min": 0, "max": 100},
    "voltage":  {"kind": "float_range", "min": 0.0, "max": 5.0},
    "nickname": {"kind": "str_length", "min_len": 1, "max_len": 16},
    "duration": {"kind": "pattern", "regex": "^\\d+(us|ms|s)$"},
    "byte":     {"kind": "format_spec", "spec": "Val:H1"}
  },
  "commands": {
    "ECHO":    {"help": "Toggle echo.", "typed_args": [{"name": "state", "type": "on_off"}]},
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

6. **Don't invent `response.pattern` or `response.types` without
   sample responses.**  A wrong regex is worse than `format: lines`.
   If the user provides sample responses, *then* upgrade to `regex`.
   ("response.types" here means the per-group coercion map inside a
   `response` block, NOT the top-level `types` block — see rule 10.)

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

10. **Promote repeated argument vocabularies into the top-level
    `types` block.**  When you see the same vocabulary repeated across
    commands — every toggle accepting on/off, several commands sharing
    a `["1","2","tot"]` enum, multiple args matching `<digits>(us|ms|s)`
    — declare the contract once at the top level and reference it by
    name from each `typed_args` entry.  This gives the LLM a precise,
    reusable vocabulary instead of forcing it to read every command's
    inline enum.

    Only declare values you can see in the help output — don't extend
    the vocabulary speculatively.  Pick from the six kinds (`enum`,
    `int_range`, `float_range`, `str_length`, `pattern`, `format_spec`;
    see the "Profile-local types" section above).  Custom type names
    must NOT collide with the five builtins (`int`, `float`, `bool`,
    `hex`, `str`); pick a domain name like `on_off`, `channel`,
    `duration`.

    Skip dual-mode args — commands whose help text says "accepts a
    bool OR a duration" must stay as builtin `str` (or `bool`) until
    a union kind exists, or the validator will reject the second mode.

## Drafting a profile with an AI

Two flows, depending on whether you have termapy MCP available:

### With MCP (recommended)

The bundled `draft_profile` MCP prompt embeds this entire guide and
adds critical drafting rules.  In Claude Desktop / VS Code MCP:

1. Invoke the `draft_profile` prompt (the client UI lists available
   prompts; pick this one).
2. Paste your device help dump in the `help_output` field.  Optionally
   add a startup banner, sample responses for a few high-value
   commands, and any engineer notes ("RF transmits are billable, mark
   destructive").
3. The LLM reads this guide + your inputs and returns one JSON object
   with `enabled: false` on every command.
4. Save the draft to `<cfg>/<device>.profile.json` and proceed to the
   engineer workflow below.

### Without MCP (chat-only)

Plain Claude / ChatGPT works fine.  Paste the prompt template below,
then your inputs.  Edit the `device_name` line and any `(none)`
placeholders.

````text
You are drafting a v2 device profile for termapy from artifacts I'm
pasting below.  Follow the embedded authoring guide precisely.

Critical rules (read the guide for full context):

- Every command entry MUST have `enabled: false`.  No exceptions.
- When unsure about safety, prefer `destructive`.  Friction is
  recoverable; data loss isn't.
- Default `response.format: "lines"` with `timeout_ms: 1000` unless
  sample responses justify a stricter format.
- Bump `timeout_ms` for slow commands (reset, mfg, flash, cal).
- Promote repeated argument vocabularies into the top-level `types`
  block.  Reference custom types by name from `typed_args[i].type`.
- Don't invent commands, regex patterns, or type values you can't
  see in the artifacts.
- Add a top-level `_notes` block summarizing what was inferred and
  flagging commands you couldn't classify with confidence.

Return ONE JSON object matching the v2 profile schema, wrapped in a
```json ... ``` code block.  After the code block, summarize: how
many commands, how many you flagged as needing review, and any
commands you couldn't classify.

# Authoring guide

<paste the contents of authoring-profiles.md here>

# Inputs

## Device name

<device_name>

## Help output

```text
<paste the device's `help` / `?` / `AT+HELP` output here>
```

## Startup banner

```text
<paste the banner the device prints at boot, or write "(none)">
```

## Sample responses

```text
<paste a few `cmd -> response` pairs for high-value commands, or "(none)">
```

## Engineer notes

<free-form: anything the LLM should know that isn't in the artifacts,
e.g. "the RF transmit command is billable", "this firmware is
SB_0_8 dated 2026-05-01">
````

### Drafting from device source code

If you have the firmware source (C, Rust, Python — anything), point
the LLM at it instead of (or in addition to) a help dump.  Useful
extras to ask the LLM to extract:

- **Command dispatch table** for the canonical command list (look for
  arrays of `{name, handler}` structs, sub-command routers, or a
  central `if/else if (strcmp(arg, "x") == 0)` ladder).
- **Argument parsers** for vocabulary — `parseBool`, `parseChannel`,
  enum string tables.  These define the exact `types` block entries.
- **`printf`/`puts` calls in command handlers** for response shape —
  use these to draft `response.regex` patterns with confidence.
- **Constants for timing** — `#define WDT_RESET_TIME_MS 5000` tells
  you a precise `response.timeout_ms` for the reset command.

Source-code drafts are typically more precise than help-dump drafts
(you can see the actual parsers, not just the help string), but
they're also longer to produce — the LLM has more to read.  For a
device you already have a help dump from, prefer the help-dump flow;
fall back to source when the help text is terse or ambiguous.

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
- `/profile.load <path>` — install a profile into the active session.
- `/profile.info` — inspect the active profile.
- `/mcp.info` — see destructive count, enabled-vs-draft split.
- `/profile.load cmd=<command>` — for devices that publish their own
  profile via a wire command like `AT+HELP.JSON`.  Same destination
  as the file path -- the active profile namespace -- so the rest of
  the system is agnostic to where the JSON came from.
