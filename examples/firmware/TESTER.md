# Testing a `termapy_json` device from the host

The PICBoard firmware (branch `feat/termapy-json`) is
the reference hardware for `termapy_json.c`. This document gives a host-side
tester everything it needs to drive that device over its serial monitor and
check every reply shape the module can produce, without reading the firmware.

## 1. Connection and line discipline

| Item | Value |
|---|---|
| Port | `COM4`, FTDI FT2232 (VID 0403, PID 6015) |
| Settings | 115200 baud, 8N1, no flow control |
| Command terminator | `\r` (CR). `\n` is ignored as a line terminator. |
| Longest command line | 510 chars. The 512-byte RX ring overflows before the firmware's 1792-byte line buffer is reached, so a 511- or 512-char line gets no reply and a longer line runs its tail (length mod 512) as a command. The firmware's `max 1791 chars` message is unreachable from the wire. |
| Text reply line end | `\r\n` |
| JSON reply line end | `\n` only, one JSON object per line (NDJSON) |
| Startup | a multi-line color banner, then a blank prompt line |

Leading and trailing spaces and tabs on a command line are trimmed. An empty
line is ignored. Command names are case-sensitive except the `MEM.*` family.

**Echo.** With echo on (the default) the device repeats the command as
`> <line>` before answering, in text mode only. Echo is suppressed while JSON
framing is on. A tester should either send `echo off` first or expect the
echo line before every text-mode reply. `echo off` itself answers `off`.

**Reset to a known state** (the firmware's own pytest fixture does this
before every test):

```text
echo on
json off
color off
blink 5ms 2s
```

Each answers one line; discard the answers.

**Timing.** A scalar reply arrives within a few milliseconds. `help` prints
about 2.5 KB and takes roughly 250 ms at 115200. The firmware's tests use a
300 ms read timeout per line and an 80 ms idle gap to end a multi-line read.

## 2. Switching framing

| Command | Mode before | Bytes answered |
|---|---|---|
| `json` | text | `off\r\n` (after the echo line) |
| `json on` | text | `> json on\r\n` echo (if echo on), then `{"result":"on"}\n` |
| `json` | json | `{"result":"on"}\n` |
| `json off` | json | `off\r\n`, no echo (the line arrived while JSON was on) |
| `json xyz` | either | treated as off (`ParseBool` fails closed) |

`json on` is the only command whose echo and reply are in different framings,
because the flip happens inside the handler. Read two lines for it when echo
is on.

A line whose first non-space character is `{` is a JSON request. It flips
framing to JSON by itself, so a tester may skip `json on` and simply send
requests. The device is built with the extractor on (`TERMAPY_JSON_HAS_EXTRACT=1`).

## 3. The envelope

The JSON-RPC 2.0 response shape:

```text
success   {"result":<value>}                          or with id: {"result":<value>,"id":<int>}
error     {"error":{"code":<int>,"message":"<msg>"}}  or with id: {"error":{...},"id":<int>}
tagged    {"jsonrpc":"2.0","result":<value>,"id":<int>}   (id null when the request had none)
```

- Exactly one of `result` and `error` is present. Check for `error` first.
- Compact: no spaces after `:` or `,`.
- Key order is fixed: `jsonrpc` (only when mirrored), then `result` or
  `error`, then `id` last. Inside `error`: `code`, then `message`.
- `"jsonrpc":"2.0"` is mirrored: it leads the reply only when the request
  carried the key. A tagged reply always has `id`, `null` when the request
  had none; an untagged reply omits `id` when the request had none.
- Untagged, `id` is present only when the request was a `{...}` object
  carrying an integer `"id"`. A plain text line never has one, even right
  after an id'd request.
- A request without `id` is still answered (a JSON-RPC "notification" would
  not be); a serial link needs the reply.
- `result` is any JSON value: string, number, `true`/`false`/`null`, array,
  object.
- Strings escape `"` `\` `\n` `\r` `\t` and other control bytes as `\u00XX`;
  bytes at or above 0x20 pass through unescaped.

## 4. Request forms the device accepts

```text
{"cmd":"fw"}                                   -> dispatched as   fw
{"cmd":"json","args":"off"}                    -> dispatched as   json off
{"cmd":"fw","id":7}                            -> reply carries "id":7
{ "cmd" : "rev" , "id" : 11 }                  -> whitespace around : and , is fine
{"method":"fw","id":7}                         -> same as cmd (cmd wins if both)
{"method":"blink","params":"5ms 2s"}           -> dispatched as   blink 5ms 2s
{"method":"blink","params":["5ms","2s"]}       -> same; flat array joined by spaces
{"method":"jtest","params":[1,true,"x"]}       -> jtest 1 true x (bare scalars verbatim)
{"jsonrpc":"2.0","method":"fw","id":1}         -> {"jsonrpc":"2.0","result":"SB_0_8","id":1}
{"jsonrpc":"2.0","method":"fw"}                -> {"jsonrpc":"2.0","result":"SB_0_8","id":null}
```

Limits and failure cases, all answered in JSON framing because a `{` line
flips the mode before parsing:

| Request | Reply |
|---|---|
| `{"nope":1}` (no `"cmd"` or `"method"`) | `{"error":{"code":-32700,"message":"malformed json"}}` |
| `{"cmd":"fw` (unterminated) | same malformed envelope |
| `{"cmd","fw"}` (comma for colon) | same malformed envelope |
| `"args"` not a string (`"args":5`, `"args":["x"]`) | same malformed envelope |
| `"params"` an object, a nested array, a bare number or `null` | same malformed envelope |
| `"args"` or one `"params"` element longer than 63 chars | same malformed envelope (never silently dropped) |
| command + arguments longer than 95 chars | same malformed envelope (`SERCMD_JSON_CMD_SIZE` 96) |
| `{"cmd":5,"id":3}` (malformed but id'd) | `{"error":{"code":-32700,"message":"malformed json"},"id":3}` |
| `{"cmd":"no_such_cmd","id":5}` | `{"error":{"code":-32601,"message":"unknown command"},"id":5}` |
| `"id"` with more than 10 digits, or not an integer | id ignored; reply has no `id` (`"id":null` when tagged) |
| negative `"id":-3` | echoed as `"id":-3` |
| `{"jsonrpc":"2.0","method":"nope"}` | `{"jsonrpc":"2.0","error":{"code":-32601,"message":"unknown command"},"id":null}` |

The extractor is a substring matcher: `"cmd"`, `"method"`, `"args"`,
`"params"`, `"id"` and `"jsonrpc"` are found anywhere in the line, in any
order, each followed by a colon (so a value equal to a key name, as in
`{"cmd":"params"}`, is not mistaken for the key). Nested objects and arrays
are not parsed; `params` accepts one flat array.

## 5. Error code convention

| Code | Meaning | Where it comes from |
|---|---|---|
| -32700 | malformed JSON request | line reader |
| -32601 | unknown command | line reader, JSON mode only |
| -32602 | bad or missing arguments | converted handlers (`fault`, `jtest`) |
| 1 | device timeout with no code of its own | `tick` when the RTC does not advance |
| 7 | the fixed fixture code | `jtest err` |
| other positive | the device's own error byte | reserved for handlers that pass a device status byte through (none in this build) |

In text mode an error is the bare message with `\r\n`. Converted handlers keep
their old text wording, so `fault 1000` prints `ERR: Invalid fault code (1-999)`
in text and `{"error":{"code":-32602,"message":"ERR: Invalid fault code (1-999)"}}`
in JSON.

## 6. Three reply classes

### 6a. Typed commands (converted handlers)

| Command | Text reply | JSON `result` type | Notes |
|---|---|---|---|
| `fw` | `SB_0_8` | string | constant for this build |
| `name` | `PICBoard` | string | |
| `rev` | `B7 & B8` | string | |
| `built` | `<Mmm dd yyyy> <hh:mm:ss>` | string | compiler date and time |
| `json` | `on` / `off` | string | see section 2 |
| `tick` | digits | integer | 32768 Hz RTC count, wraps at 2^32; strictly increasing between calls |
| `fault` | digits | integer | active fault code, 0 when none |
| `fault <1-999>` | digits | integer | sets and echoes the code |
| `fault 0` | `0` | integer | clears |
| `fault 1000` | `ERR: Invalid fault code (1-999)` | error -32602 | |

Text output of a typed command is byte-identical to the pre-conversion
firmware.

### 6b. Unconverted commands (passthrough)

Every other command still prints its text through the monitor's normal
printer. In JSON mode that text is captured into one string result:

- The first byte opens `{"result":"`; the dispatcher closes it after
  the handler returns.
- Line feeds inside the output become `\n` in the string; one trailing line
  feed is dropped, so a one-line answer has no newline in it.
- ANSI escape sequences are stripped, so the result is clean even with
  `color on`.
- The envelope is always a `result`, even when the text is an error message.
- A command that prints nothing produces no reply at all.

Examples:

| Command in JSON mode | Reply |
|---|---|
| `echo on` | `{"result":"on"}` |
| `color` | `{"result":"off"}` |
| `help json` | one string containing `json` and `on\|off` |
| `help` | one string, about 2.5 KB, with `\n` between rows |
| `version` | one string with four `\n`-separated lines |
| `jtest text` | `{"result":"line one\nline two"}` |
| a 511-char line | no reply at all (RX ring overflow, see section 1); do not expect the firmware's `too long` message |

### 6c. Commands with their own protocol (not wrapped)

`MEM.R`, `MEM.W`, `MEM.M`, `MEM.INFO` and the `MEM <verb>` spellings answer
termapy's memory protocol regardless of framing: rows `XXXXXXXX: XX XX ...`
then `OK`, or `ERR usage` / `ERR length` / `ERR range`, each line `\r\n`.
`MEM.INFO` answers one JSON object (a plain object, not the envelope) followed
by `OK`. Those bytes bypass the passthrough, so a tester in JSON mode must
read them as text until `OK` or `ERR`.

## 7. `jtest`: the deterministic fixtures

`jtest <case>` exists for testers. Every case is constant, so the bytes can be
pinned exactly. JSON column: the full reply line minus the `\n`. Text column:
the bytes after the echo line.

| Case | JSON reply | Text reply |
|---|---|---|
| `str` | `{"result":"hello \"quoted\" \\ tab\tend"}` | `hello "quoted" \ tab<TAB>end\r\n` |
| `int` | `{"result":-42}` | `-42\r\n` |
| `uint` | `{"result":4294967295}` | `4294967295\r\n` |
| `float` | `{"result":3.14}` | `3.14\r\n` |
| `raw` | `{"result":true}` | `true\r\n` |
| `err` | `{"error":{"code":7,"message":"jtest error"}}` | `jtest error\r\n` |
| `arr` | `{"result":[1,"two",3.5,true]}` | `1\r\ntwo\r\n3.5\r\ntrue\r\n` |
| `obj` | `{"result":{"name":"stack","used":812,"max":4096}}` | `name stack\r\nused 812\r\nmax 4096\r\n` |
| `table` | `{"result":{"n":2,"rows":[{"name":"a","v":1},{"name":"b","v":2}]}}` | `n 2\r\nname a\r\nv 1\r\nname b\r\nv 2\r\n` |
| `text` | `{"result":"line one\nline two"}` | `line one\r\nline two\r\n` |
| (none) | `{"error":{"code":-32602,"message":"ERR: usage: jtest str\|int\|uint\|float\|raw\|err\|arr\|obj\|table\|text"}}` | `ERR: usage: jtest ...\r\n` |

`jtest text` is the only case produced by the passthrough; the others come
from the typed replies and the builder. With `color on`, `jtest text` still
answers the bytes above in JSON mode (the green color code around `line two`
is stripped) but in text mode the color codes are present.

The same table as Python, ready to paste (raw strings so the escapes are the
wire bytes):

```python
JTEST_JSON = {
    "str": r'{"result":"hello \"quoted\" \\ tab\tend"}',
    "int": '{"result":-42}',
    "uint": '{"result":4294967295}',
    "float": '{"result":3.14}',
    "raw": '{"result":true}',
    "err": '{"error":{"code":7,"message":"jtest error"}}',
    "arr": '{"result":[1,"two",3.5,true]}',
    "obj": '{"result":{"name":"stack","used":812,"max":4096}}',
    "table": '{"result":{"n":2,"rows":[{"name":"a","v":1},{"name":"b","v":2}]}}',
    "text": r'{"result":"line one\nline two"}',
}
```

## 8. Behaviors a tester should cover

1. **Framing round trip.** `json on` answers JSON; `fw` answers JSON; `json off`
   answers `off` with no echo; `fw` answers text with echo.
2. **Request auto-flip and id echo.** From text mode, `{"cmd":"fw","id":7}`
   answers with `"id":7`; a following plain `fw` answers with no `id`.
3. **Every fixture, both framings**, byte for byte (section 7).
4. **Typed live values.** `tick` is an integer that increases between calls at
   about 32768 per second; `fault 5` / `fault 0` round-trip; `fault 1000` is
   -32602.
5. **Passthrough.** `echo on` answers the wrapped string; `help` is one line of
   JSON; with `color on`, no `\x1b` byte appears in any JSON reply.
6. **Malformed requests** from section 4 each answer the -32700 envelope, and
   the device keeps answering normally afterwards.
7. **Unknown command** answers -32601 in JSON mode and
   `ERR: unknown command: <line>` in text mode.
8. **Stream validity.** Over a long mixed session every byte received in JSON
   mode splits on `\n` into lines that each `json.loads` cleanly, except the
   `MEM.*` protocol lines (section 6c).

## 9. Things that look like bugs but are not

- **Several replies for one request.** `repeat <interval> <count> <cmd>` prints
  nothing itself and then runs `<cmd>` `<count>` times; each run produces its
  own envelope, arriving on the timer. A tester must expect unsolicited lines
  after `repeat`.
- **`reset` answers and then reboots.** The banner follows after a few hundred
  milliseconds, in text, with color codes if color is on.
- **`standby` sleeps.** The reply `standby EIC-only` arrives, then the port is
  silent until a wake byte; the firmware's own standby test is known stale.
- **`mfg <process>` redirects input.** While a manufacturing process runs,
  every line goes to it instead of the monitor, still wrapped by the
  passthrough in JSON mode.
- **The echo of `json on` is text.** See section 2.
- **`jtest` and `tick` exist only in dev builds** (`APP_MONITOR_DEV_MODE`,
  on in this test bed).

## 10. Suggested termapy profile hints

- Connect sequence: `echo off`, then `json on`; both can be fire-and-forget.
- Per-command `"response": {"format": "json", "timeout_ms": 200}` for the
  typed and passthrough commands; 500 ms for `help`; `"format": "lines"` with
  terminator `^(OK|ERR .*)$` for the `MEM.*` family.
- Treat a reply with an `error` member as a command failure, `error.message`
  as the message and `error.code` as the detail; an `error_detection` pattern
  for it is `"error"\s*:\s*\{[^}]*"message"\s*:\s*"(?P<message>[^"]*)"`. Treat
  a string `result` from an unconverted command as text to show, not data to
  type.

## 11. Where the pieces live

| What | Where |
|---|---|
| Module source of truth | termapy `examples/firmware/termapy_json.c`, `.h` (commit fe76188, JSON-RPC envelope) |
| Host unit tests for the module | termapy `tests/test_firmware_json.py` + `tests/fixtures/firmware/json_host.c` |
| Firmware copy | `PICBoard/firmware/PICBoard.X/TermapyJson.c`, `.h` (names only differ) |
| Glue: putc, formatter, `json`, `jtest`, conversions | `PICBoard/firmware/PICBoard.X/Monitor.c` |
| Glue: line reader, echo suppression, request extraction | `PICBoard/firmware/PICBoard.X/SMSerCmd.c` |
| Glue: terminal putc hook | `PICBoard/firmware/PICBoard.X/main.c`, `app_put_terminal` |
| Firmware-side hardware tests (the oracle for this document) | `PICBoard/test/test_termapy_json.py`, helpers in `test/conftest.py` |
| Memory protocol | `PICBoard/firmware/PICBoard.X/TermapyMem.c` and README section `MEM` |
