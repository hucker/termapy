# Termapy Device Profile — v2 Spec

A **device profile** declaratively describes how a serial device speaks. Profiles are consumed by the termapy MCP bridge (the reference implementation) but the format is tool-agnostic — anyone can build a different consumer from the same schema.

## Mission

Make it easy to support tiny → powerful serial devices via MCP.

- **Default path:** text-in/JSON-out OR JSON-in/JSON-out. Modern devices speak JSON because they're talking to robots.
- **Legacy support:** direct, narrow handling for sync text commands and async noise (echo, prompts, errors, banners).
- **Profiles** describe each device declaratively. The bridge does the rest.

## The losing condition

> If users conclude *"I'll just have an LLM write the whole thing in C or Python"* beats *"I'll have the LLM write me a profile that termapy handles,"* the project has failed.

Every field, every default, every constraint is evaluated against this test: **does this addition make the profile path easier, faster, or safer than equivalent codegen?** If a field requires a comment to understand, or pushes the schema toward expressing logic, reject it.

## Schema discipline (immutable rules)

1. **Profiles are declarative-only.** No embedded code, no `pre_send_python_hook`, no expression languages.
2. **Behavior beyond pattern matching belongs in `.run` scripts or custom code.** State machines, multi-step calibration, conditional command flow — all out of scope. Use a script.
3. **Scope is the four response patterns + transport noise handling.** Anything that doesn't fit goes to a script.
4. **Readability is a feature.** A user reading a profile for an unfamiliar device should understand it in 30 seconds.

## Designing a new device for this bridge

If you control the firmware, do this:

- **Emit NDJSON.** One JSON object per line, `\n` delimited. Even for trivial messages — `{"event": "ready"}` beats `READY`. You're talking to robots; speak the robot dialect.
- **Use `id` for request/response correlation.** The bridge will match `{"ok": true, "result": ..., "id": 7}` to the request that sent `id=7`.
- **Use `error` for errors.** A response with an `error` field flips the bridge's `success=false` automatically.
- **Use `event` for async messages.** Telemetry, faults, banners — `{"event": "tick", "value": 42}`. The bridge routes them to a separate stream so they don't pollute command responses.

For these devices the profile is shockingly small (~20 lines of JSON). The bridge does almost nothing per-device.

If you can't change the firmware (legacy device), the profile gets bigger but still smaller than custom code: declare echo/prompt/line-endings + a regex per command and you're done.

## The four response patterns

| # | Send shape         | Response shape                   | When to use                                  |
| - | ------------------ | -------------------------------- | -------------------------------------------- |
| 1 | JSON object        | JSON object                      | **Recommended.** Modern device, NDJSON.       |
| 2 | Single-line text   | JSON object                      | **Recommended for tiny MCUs.** `printf`-cheap.|
| 3 | Single-line text   | Single line, regex-parsed        | Legacy. Manifest does the parsing.           |
| 4 | Single-line text   | Multi-line until terminator      | Legacy. Optional per-line regex.             |

Plus orthogonal: `response.format: "none"` for fire-and-forget commands.

## File format

- **Canonical:** JSON. Schema, docs, reference profiles, cache files, wire format — all JSON.
- **TOML accepted on load** as an author convenience. The bridge uses Python's stdlib `tomllib`; no extra dependency.
- **Save always writes JSON.** A `.toml` source that goes through a save cycle becomes `.json`.

## Top-level shape

```json
{
  "profile_version": 2,
  "profile_revision": "1.4.0",
  "profile_date": "2026-04-30",

  "device":          { ... },
  "transport":       { ... },
  "error_detection": { ... },
  "commands":        { ... }
}
```

### `profile_version`

Schema version. `2` for v2. Absent or `1` = v1 (backward-compatible commands-dict-only mode).

### `profile_revision` (semver) and `profile_date` (ISO 8601)

Used by the bridge to choose between candidate profiles when both a hand-authored file and a device-fetched cache exist.

**Precedence rule:**

1. Higher `profile_revision` wins (semver compare).
2. If equal, newer `profile_date` wins.
3. If revision and date both equal but content differs, **device-fetched wins** — the user hasn't bumped, implying the change wasn't reviewed.
4. Profiles missing both fields are treated as `0.0.0` / epoch — they always lose to a versioned candidate.

### `device`

```json
"device": {
  "name":            "ACME-PSU-3000",
  "vendor":          "ACME",
  "model":           "PSU-30A",
  "startup_banner":  "ACME-PSU v\\d+\\.\\d+ ready",
  "prompt":          "psu> "
}
```

- `startup_banner` (optional regex) — bridge logs match/non-match after connect; never blocks.
- `prompt` (optional string) — trailing prompt; used as default response terminator when set.

### `transport`

```json
"transport": {
  "protocol":                 "ndjson",
  "baud_rate":                115200,
  "byte_size":                8,
  "parity":                   "N",
  "stop_bits":                1,
  "flow_control":             "none",
  "line_ending_send":         "\n",
  "line_ending_recv":         "\n",
  "encoding":                 "utf-8",
  "echo":                     false,
  "inter_command_delay_ms":   50,
  "default_response_timeout_ms": 1000,
  "field_routing": {
    "response_id": "id",
    "error_field": "error",
    "event_field": "event"
  }
}
```

When the profile loads, the bridge applies these to the live session: baud/byte_size/parity/stop_bits/flow_control reconnect SerialEngine if needed; line endings/encoding update the session config; inter-command delay throttles outbound writes.

### `field_routing` (NDJSON only)

For NDJSON devices, the bridge auto-routes inbound messages by field presence:

1. Has `event_field` (default `"event"`) → async event → `device_state.async_events`.
2. Else has `error_field` (default `"error"`) → error → flips response `success=false`.
3. Else has `response_id` (default `"id"`) → correlate to in-flight request by id.
4. Else → response to last request.

No regex needed. Override only if the device uses different conventions.

### `error_detection` (text protocol)

```json
"error_detection": {
  "pattern": "ERR=(?P<code>\\d+)",
  "async":   true,
  "types":   {"code": "int"},
  "codes":   {"1": "syntax", "10": "busy"}
}
```

Applied to every device response after per-command parsing. If the pattern matches, the response gets `error_detection: {code: 1, message: "syntax"}` populated and `success` flips to `false`.

When `async: true`, unsolicited matches (no command in flight) route to `device_state.async_errors`.

### `commands`

Map of command name to descriptor:

```json
"commands": {
  "AT+VER": {
    "help":          "Read firmware version.",
    "args":          "",
    "long_help":     "Returns major.minor.patch.",
    "safety":        "readonly",
    "send_template": "AT+VER",
    "typed_args":    [],
    "response": {
      "format":     "regex",
      "pattern":    "VER=(?P<major>\\d+)\\.(?P<minor>\\d+)\\.(?P<patch>\\d+)",
      "types":      {"major": "int", "minor": "int", "patch": "int"},
      "timeout_ms": 500
    },
    "rate_limit_hz": 0,
    "timeout_ms":    0
  }
}
```

Only `help` is required. Every other field is optional with a sensible default.

#### `safety` tier

- `safe` (default) — freely callable.
- `readonly` — explicitly declared as non-mutating; same call semantics as safe but useful annotation for codegen.
- `destructive` — surfaces `annotations.destructiveHint=true` to MCP clients (Claude prompts for confirmation). Bridge does not block — confirmation lives at the MCP client.

#### `typed_args`

For codegen and validation. Each arg:

```json
{"name": "mv", "type": "int", "required": true, "help": "Millivolts.",
 "min": 0, "max": 30000}
```

Types: `int` / `float` / `bool` / `hex` / `str`. Optional `enum` for `str` args.

#### `send_template`

Python-format-style template with named placeholders, e.g. `"AT+VOLT={mv}"`. Empty = use the command name verbatim. For NDJSON protocol, the bridge serializes args as a JSON object instead of using the template.

#### `response.format` — the four patterns

- `"none"` — fire-and-forget. Bridge sends, doesn't read.
- `"literal"` — response (after strip) must equal `pattern`. Used for AT-style `OK` replies.
- `"lines"` — collect lines until `terminator` matches; optional `line_pattern` parses each line into a typed dict.
- `"regex"` — `re.search(pattern, response)`. No groups → matched substring. Unnamed groups → list. Named groups → dict, type-coerced per `types`.
- `"json"` — `json.loads(response)`. NDJSON devices use this.

Type coercion: `int` / `float` / `bool` / `hex` / `str`. Failed coercion returns the raw string (parsers never raise).

## What goes in a profile vs what goes in a script

If your need is in the table below, it's a profile field:

| Need                                | Profile field                                  |
| ----------------------------------- | ---------------------------------------------- |
| Send a command, parse response      | `commands.<name>.response.format`              |
| Map error codes to meanings         | `error_detection.codes`                        |
| Throttle a command                  | `commands.<name>.rate_limit_hz`                |
| Mark destructive                    | `commands.<name>.safety: "destructive"`        |
| Validate arg types                  | `commands.<name>.typed_args`                   |
| Auto-strip echo                     | `transport.echo: true`                         |
| Auto-correlate JSON responses       | `transport.field_routing`                      |
| Detect device banner                | `device.startup_banner`                        |

If your need is in the table below, it's a `.run` script:

| Need                                | Where it goes                          |
| ----------------------------------- | -------------------------------------- |
| Multi-step login / handshake        | `.run` script                          |
| Calibration sweep with intermediate decisions | `.run` script              |
| "Send X if last response was Y"     | `.run` script                          |
| Setup → measure → teardown sequence | `.run` script                          |
| Custom binary protocol              | `.pro` (binary protocol script)        |
| Mode-dependent command sets         | Separate profile per mode + script switching |

The boundary is sharp on purpose: profiles are *data*, scripts are *logic*. Every time we've considered relaxing this boundary, the schema started looking like a programming language.

## Validation

### Standalone CLI

```sh
termapy --validate-profile path/to/device.profile.json
```

Exit 0 on valid, 1 with line-numbered errors. Works without any other dependencies. Use this in CI pipelines for profile authoring.

### From the REPL

```
/profile.validate <path>
```

Same checks, integrated into the termapy session.

## Schema location

The canonical JSON Schema ships with the package:

```python
from termapy.profile import SCHEMA_PATH
# or load as JSON: src/termapy/profile.schema.json
```

The schema is versioned alongside termapy. Breaking changes increment `profile_version`. Additive changes don't bump (forward-compat: unknown fields are ignored).

## Reference profiles

Three archetypes ship in `tests/fixtures/profiles/`:

- **`at_modem.profile.json`** — text-in / regex-out, AT command set. Pattern 3.
- **`register_psu.profile.json`** — text-in / lines-out, register-readback style. Patterns 3 + 4.
- **`smart_sensor.profile.json`** — JSON-in / JSON-out, NDJSON-native modern device. Pattern 1.

Read these before writing your own. They cover the schema breadth.
