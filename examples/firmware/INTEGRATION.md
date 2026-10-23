# Integrating `termapy_json` into a device monitor

Written for: a coding assistant (Claude) adding termapy JSON support to an
existing C firmware monitor.

This guide is for wiring `termapy_json.c` / `termapy_json.h` into a device
that already has a text command monitor (a receive loop, a command table, and
handlers that print replies). After integration the same monitor answers a
human at a text prompt AND an agent over termapy, choosing the framing with
one boolean. You do not rewrite the command table, and you convert handlers
only where a typed value is worth it.

## What the module is and is not

It owns the output mode (a boolean), the reply writers that replace
`puts`/`printf` for a command's answer (scalar, structured and passthrough),
and `TermapyJson_Init`. It does **not** own your command table, your line
reader, or any command's meaning. Those stay yours. You call into this module
at three points: once at boot, once per received line (to close the
passthrough), and inside each converted handler where it used to print.

The device decodes nothing by default. termapy's REPL and MCP server already
turn a request into the plain command line your monitor understands, so the
device only encodes replies. A `{"cmd":...}` / `{"method":...}` request
extractor exists behind
`TERMAPY_JSON_HAS_EXTRACT` for a monitor that wants symmetric JSON without a
termapy host; it is on by default for parity with termapy's reference device
and costs nothing when unreferenced under `--gc-sections`.

Portable C99, no stdio, no malloc, no VLAs, and no floating point of its own.
The platform dependencies are a `putchar`-style serial out you inject once
and, optionally, a float formatter.

## Step 1 — add the files to the build

Copy `termapy_json.c` and `termapy_json.h` into the firmware source tree and
add `termapy_json.c` to the build (Makefile object list, CMake `target_sources`,
IDE project). No other dependency. Confirm it compiles under the project's
existing C flags; the module is clean under `-std=c99 -pedantic -Wall -Wextra
-Werror`.

## Step 2 — inject the platform at boot

Once, before any reply, hand the module your byte-out function and, if the
device has one, a float formatter:

```c
#include "termapy_json.h"

static int MyPut(int c) { Uart_Write((uint8_t)c); return c; }   /* your UART */

/* Optional.  Plain %.Nf, no padding: JSON numbers may not start with a space. */
static bool MyFmtFloat(double v, uint8_t decimals, char *bufP, size_t cap)
{
    return snprintf(bufP, cap, "%.*f", (int)decimals, v) > 0;
}

static const TermapyJson_Ops sJsonOps = { MyPut, MyFmtFloat };  /* or { MyPut } */

int main(void) {
    /* ... your existing init ... */
    TermapyJson_Init(&sJsonOps);   /* add this line */
    /* ... your monitor loop ... */
}
```

`MyPut` should verify the write: a UART ring that is full drops bytes, and a
dropped byte is a truncated reply the host cannot repair. Retry with a bound
rather than returning early. Until `Init` is called the writers emit nothing,
so a reply before init is silent, not a crash. Leave `fmtFloat` NULL on a
device without a float printer: the float replies then emit `null` and no
float code is linked.

## Step 3 — add the mode command

Register a command that sets the framing. The module ships a bare toggle
(`TermapyJson_ModeCmd`), but a monitor with `on|off` conventions writes its
own two-liner so the command can also query:

```c
static void CmdJson(const char *argP)
{
    if (*argP != '\0') { TermapyJson_SetMode(ParseBool(argP) ? TERMAPY_JSON_JSON : TERMAPY_JSON_TEXT); }
    TermapyJson_Reply(TermapyJson_GetMode() == TERMAPY_JSON_JSON ? "on" : "off");
}
/* add { "json", CmdJson } to your command table */
```

`json on` is answered in JSON (the flip happens before the reply); `json off`
answers the bare word `off`. A termapy profile sends `json on` on connect.

## Step 4 — route the text printer through the passthrough

This single step makes every command JSON-capable with zero handler changes.
Wherever your text output layer emits a byte (the putc you gave `printf` or
your color printer), send it through the passthrough in JSON mode, and close
the envelope after each dispatched command:

```c
static void MonitorPutc(int ch)            /* the putc your printer already uses */
{
    (void)TermapyJson_PassthroughPut(ch);  /* text mode: forwards; json: escapes into an envelope */
}

void OnLine(const char *lineP) {           /* your per-line entry point */
    Dispatch(lineP);                       /* your existing verb/arg split + table */
    TermapyJson_PassthroughEnd();          /* closes the envelope if the handler printed */
}
```

In text mode `PassthroughPut` is the plain putc. In JSON mode the first byte a
handler prints opens `{"result":"`, every byte is JSON-escaped, and
`PassthroughEnd` writes the closing quote, the id tail and the newline. A
`puts`-style `value\n` becomes `"value"` (one trailing newline is dropped);
multi-line output becomes one string with `\n` inside. Two rules:

- Feed it `\n` only. If your text layer turns `\n` into `\r\n`, do that in
  text mode, not before the passthrough, or the string carries `\r`.
- Turn ANSI color off in JSON mode, or the escape codes end up in the string.

An unconverted command always answers with `result`, even when the text it
printed is an error message. That is the trade for touching no handlers;
convert the commands whose errors the host must see as an `error` object.

## Step 5 — convert handlers where a typed value is worth it

Find where a handler prints its answer and replace that print with one reply
call. Text output stays byte-identical, so existing tests keep passing.

| Handler does today | Replace with | JSON result |
|---|---|---|
| prints a string | `TermapyJson_Reply(s)` | `"s"` quoted, escaped |
| prints an int | `TermapyJson_ReplyInt(v)` / `ReplyUInt(v)` | `v` |
| prints a float | `TermapyJson_ReplyFloat(v, decimals)` | `v` or `null` |
| prints a JSON value it built | `TermapyJson_ReplyRaw(s)` | `s` verbatim |
| prints an error | `TermapyJson_ReplyError(msg, code)` | `error` object, no `result` |

```c
/* before */
static void CmdTick(const char *argP) { (void)argP; printf("%lu\n", TickGet()); }

/* after */
static void CmdTick(const char *argP) { (void)argP; TermapyJson_ReplyUInt(TickGet()); }
```

Convert every exit of a handler, not just the success line: a handler that
prints `ERR: bad value` through the old path still answers with `result` until
that print becomes `ReplyError`. A convention that works: negative codes for
protocol and usage errors (JSON-RPC numbers, -32601 unknown command, -32602
bad arguments), positive codes for a device error code passed straight
through.

A handler must call exactly one reply per invocation, and a converted handler
must not also print through the passthrough, or the stream carries two
envelopes for one request.

## Step 6 — tables and records with the builder

For a command that prints several values, stream a JSON array or object. The
outermost `Begin` opens the envelope, the matching `End` closes it, and the
module places every comma:

```c
static void CmdHwm(const char *argP)
{
    (void)argP;
    if (TermapyJson_GetMode() == TERMAPY_JSON_TEXT) { PrintHwmTable(); return; }  /* keep your layout */
    TermapyJson_ArrayBegin();
    for (const HwmEntry *e = sTable; e->nameP != NULL; e++) {
        TermapyJson_ObjectBegin();
        TermapyJson_FieldString("name", e->nameP);
        TermapyJson_FieldUInt("used", e->get());
        TermapyJson_FieldUInt("max", e->max());
        TermapyJson_ObjectEnd();
    }
    TermapyJson_ArrayEnd();
}
/* json: {"result":[{"name":"stack","used":812,"max":4096},...]} */
```

`FieldArrayBegin(key)` / `FieldObjectBegin(key)` open a named child inside an
object. In text mode the containers print nothing and each `Item` / `Field`
prints one plain line (`value` or `key value`), so the mode branch above is
optional: drop it and the text form is that fallback layout. Nest at most
`TERMAPY_JSON_MAX_DEPTH` (32) levels.

## The wire contract you are now speaking

In JSON mode a reply is one JSON object per line in the JSON-RPC 2.0 response
shape, matching termapy's NDJSON contract so a termapy profile with a
`"format": "json"` response parses it into typed `data`, and so a stock
JSON-RPC client can talk to the device:

```text
success   {"result":<value>,"id":<int>}
error     {"error":{"code":<int>,"message":"<msg>"},"id":<int>}
```

Exactly one of `result` and `error` is present; a host checks for `error`
first. `result` is any JSON value: string, number, `null`, array or object.
`error.data` is reserved for structured detail (nothing emits it yet). Fields
are compact (no spaces after `:` or `,`) and `id` is last. JSON lines end in
`\n`; text lines end in `\r\n`.

The `"jsonrpc":"2.0"` member is mirrored: a reply carries it, first, only
when the request did. A tagged reply is a complete JSON-RPC response object
and always carries `id`, `null` when the request had none. An untagged reply
**omits** `id` entirely when no id was set, which keeps termapy's replies
short. One deviation from the spec, forced by a serial link: a request
without `id` (a JSON-RPC "notification") is still answered.

With `TERMAPY_JSON_HAS_EXTRACT` on, the extractor also accepts:

```text
{"cmd":"<name>"}                               bare command
{"cmd":"<name>","args":"<text>"}               command with a string argument
{"cmd":"<name>","id":<int>}                    id echoed in the reply
{"method":"<name>","params":"<text>"}          JSON-RPC spelling, string params
{"method":"<name>","params":["<a>",<n>,...]}   flat array, joined by spaces
{"jsonrpc":"2.0","method":"<name>","id":<int>} tagged: reply is tagged too
```

`cmd` wins over `method` and `args` over `params` when both are present.
`args` must be a string; `params` may be a string or a flat array of strings
and bare scalars (`[]` appends nothing). Run the extractor on each received
line before dispatch; `IN_JSON` hands you the plain `cmd args` line and flips
framing to JSON, `IN_BAD` wants a `ReplyError` (the tag and id were captured
first, so even that reply is correctly addressed), `IN_TEXT` means dispatch
the line as it came (and the pending id and tag are cleared). A value of the
wrong type, an element that does not fit `TERMAPY_JSON_ARGS_MAX`, or a line
that does not fit your buffer is `IN_BAD`, never silently dropped.

## Compile-time knobs

| Knob | Default | Purpose |
|---|---|---|
| `TERMAPY_JSON_HAS_EXTRACT` | 1 | Compile the `{"cmd":...}` / `{"method":...}` extractor |
| `TERMAPY_JSON_ARGS_MAX` | 64 | Size of the extractor's copy of `args` or of one `params` element |
| `TERMAPY_JSON_ID_MAX_DIGITS` | 10 | Bound on a parsed `id` |
| `TERMAPY_JSON_FLOAT_BUF` | 24 | Buffer handed to the float formatter |
| `TERMAPY_JSON_TEXT_EOL` | `"\r\n"` | Text-mode line ending |
| `TERMAPY_JSON_LINE_EOL` | `"\n"` | JSON-mode line ending |

Unreferenced API (a typed reply you never call, the builder if you never use
it) is removed by `--gc-sections` with `-ffunction-sections`; the knobs exist
for code that would otherwise be reachable, and for making the footprint a
deliberate choice rather than a linker side effect.

## Checklist before you call it done

1. `termapy_json.c` is in the build; the project still compiles clean.
2. `TermapyJson_Init` runs once at boot, before any command.
3. The text printer's putc goes through `PassthroughPut`, and the dispatcher
   calls `PassthroughEnd` after every command.
4. `json on|off` is registered; a profile can switch framing on connect.
5. Handlers whose values or errors the host must see use the typed replies;
   each has exactly one reply per exit.
6. Smoke test both framings: `fw` returns `SB_0_8`; after `json on`, `fw`
   returns `{"result":"SB_0_8"}`, an unconverted command returns
   `{"result":"<its text>"}`, a converted error returns an `error` object,
   and `{"jsonrpc":"2.0","method":"fw","id":1}` returns
   `{"jsonrpc":"2.0","result":"SB_0_8","id":1}`.

## Pitfalls

- **`Reply` vs `ReplyRaw`.** Human text is a JSON string; use `Reply`. Only a
  genuine JSON value (number, bool, object) uses `ReplyRaw`. Getting this
  wrong yields invalid JSON on the wire. Prefer the typed `ReplyInt` /
  `ReplyFloat` over hand-formatting into `ReplyRaw`.
- **One reply per handler, one path.** A typed reply plus text through the
  passthrough in the same handler produces two envelopes.
- **Float formatter output must be a plain number.** `%4.1f` pads with a
  space, which the module rejects as not-a-number and replaces with `null`.
  Use `%.*f` and let the host pad.
- **Line framing.** The module writes the line ending itself (`\r\n` text,
  `\n` JSON). Do not also print a newline in a converted handler.
- **Balance the builder.** A missing `End` leaves the envelope open and every
  later reply lands inside it. An extra `End` is ignored.
- **The extractor is a substring matcher, not a JSON parser.** It finds
  `"cmd"` / `"method"` / `"args"` / `"params"` / `"id"` / `"jsonrpc"` by
  substring followed by a colon. A value equal to a key name is skipped, but a
  string value that literally contains `"id":` is not. Your own well-formed
  command lines are fine; do not rely on it for arbitrary nested JSON input.

## Reference

The full API is in the header comment of [`termapy_json.h`](termapy_json.h).
The byte-exact behavior is pinned by `tests/test_firmware_json.py` in the
termapy repo, which compiles this module against a host harness
(`tests/fixtures/firmware/json_host.c`). For how the device then appears to an
agent over MCP, see termapy's MCP server help (`/help mcp`) and a device
profile with `"format": "json"` responses (`demo_ndjson.profile.json`).
