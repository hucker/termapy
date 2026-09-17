# Memory

`/mem.dump` and `/mem.write` peek and poke a device's memory by address
or by name, on any architecture, through two monitor commands the device
implements. The device only ever moves bytes; names come from the
[symbol table](symbols.md), and widths, byte order and bit fields are
termapy's job (a later step).

```text
/mem.dump gTemp 16
00001000  1B 00 40 06 7D 44 05 00 00 00 0C 00 00 00 00 00  |..@.}D..........|  gTemp
/mem.write gFlags 07000000
Wrote 4 bytes at 0x00001008  gFlags  (was 05000000)
```

Addresses take every `/sym` form: `0x1000`, `1000h`, `1000` (decimal),
`gTemp`, `main+0x10`, `tick@adc.c`. Dump rows are annotated with the
symbol that contains them when a table is loaded.

## Commands

| Command                     | Example                      | Does                                                        |
|-----------------------------|------------------------------|-------------------------------------------------------------|
| `/mem.dump <target> {len} {type}` | `/mem.dump gTemp 0x10`  | Hexdump; `u16`/`u32` hex-word columns, `i*` decimal, `f*` floats; `addr=off` / `ascii=off` drop columns (both off = bare values). No `len` dumps the symbol's own size, else 64 bytes |
| `/mem.read <target> {type}` | `/mem.read U1MODE.ON`        | One typed value: scalar, `char`, register field, bit (`.15`) or slice (`.4-6`) |
| `/mem.write <target> <hex>` | `/mem.write gFlags 07000000` | Write hex bytes; with a `.field`/`.bit` target the value is masked in (audited) |
| `/mem.or <target> <mask>`   | `/mem.or gFlags 0x10`        | The boolean set on one word: `.or` set, `.and` keep, `.clear` = `word &= ~mask`, `.xor` toggle, `.not` invert; atomic via `MEM.M` where expressible |
| `/mem.str <target> {max}`   | `/mem.str sBanner`           | A NUL-terminated string as a first-class value (default cap 256) |
| `/mem.info` (bare `/mem`)   | `/mem`                       | Availability status + dialect, block limit, address width, byte order, atomic modify -- and their sources; re-probes `MEM.INFO`, so it doubles as the check after connect |

`/mem.dump` returns the bytes as one hex string (`$(B) <- /mem.dump gTemp 2`
captures `1B00`); `/mem.write` returns the byte count. In JSON mode the
dump carries `rows` (`addr`, `addr_hex`, `hex`, `ascii`, `symbolic`) and
the write carries `before_hex` / `after_hex`.

**Every write is audited.** The session log (the TUI's `<name>.log`, the
MCP server's log; the one-shot CLI keeps none) gets one line per write:

```text
# MEM.W 0x00001008 before=05000000 after=07000000 origin=cli
```

Over the [MCP server](mcp-server.md) `/mem.write` is a destructive
command: it refuses to run until the client passes `confirm=true`, the
same gate a destructive profile entry gets.

## Typed reads and bits

The symbol table's `type` gives bytes meaning: `/mem.read gTemp` decodes
the seeded `u16` to `27`, `/mem.read gPressure` the `f32` to `1013.25`.
A register typed with a format spec shows its named fields, and each
field is addressable -- readable and writable -- by name:

```text
/mem.read U1MODE            ->   ON = 1   UEN = 0   BRGH = 1
/mem.read U1MODE.ON         ->   U1MODE.ON = 1  (word 0x00008008 at 0xBF806000)
/mem.write U1MODE.ON 0      ->   Set U1MODE.ON = 0  (0x00008008 -> 0x00000008)
/mem.write gFlags.4-6 5     ->   a three-bit slice, LSB0
/mem.or gFlags 0x10         ->   OR 0x00000010 at 0x00001008  gFlags  (0x00000005 -> 0x00000015)
```

Register field specs use the format-spec language exactly as protocol
testing does -- **byte order in the spec IS the byte order in memory** --
so a little-endian 32-bit register lists its bytes MSB-first:
`"ON:B4-1.15"` is bit 15 of the LE word at bytes 1-4. Bit numbers are
LSB0 over the logical word, the hardware convention.

Bit and mask writes are one masked word operation: atomic on the device
(`MEM.M`) when it advertises one, otherwise a host-side read +
write-back that can race an ISR touching the same register (`/mem.xor`
always takes that path -- XOR is not expressible as AND/OR masks). A
symbol marked `"rmw": false` (write-1-to-clear and self-clearing
registers) refuses every masked form: write the whole word instead.
Every mutation is audited in the session log with the word before and
after.

## Strings

`/mem.str <target> {max}` reads a NUL-terminated string -- the C string
at `sBanner` is a value, not sixteen hex pairs. `$(V) <- /mem.str
sBanner` captures the text; the display escapes non-printables; without
a NUL inside the cap (default 256) the result is marked truncated.
`/mem.read <target> char` renders one byte as `'A' (0x41)`.

## Two bargains: your variables, and the silicon

`/mem.*` reads two different kinds of address, and they do not come with
the same guarantee.

**Your build's symbols** -- the RAM and flash a [symbol table](symbols.md)
names -- are the low-cost bargain termapy is built around. Reading
`gTemp` observes a variable your firmware already keeps. It has no side
effect, nothing else is watching that byte, and reading it twice gives
the same answer twice.

**A [device file](devices.md)'s registers** are not that. They are an intrusive probe
on live silicon, usually through a driver that assumes it is the only
thing on the bus. Reading a FIFO data port pops a byte the driver never
receives; reading some status registers clears the flags an interrupt
was waiting on. The syntax is identical, which is exactly the problem:
the damage does not look like a terminal error, it looks like a flaky
*device*, days later.

So reads into peripheral space are treated differently:

| Situation | What happens |
|-----------|--------------|
| A read covering two or more device registers | Refused -- read one by name |
| `/mem.str` whose scan could reach any register | Refused -- registers are not strings |
| A register with `"access": "wo"` | Refused, on every reading path including the read half of `/mem.or` and `/mem.not` |
| One register, named | Allowed, and logged |

`/mem.dump UMODE` reads UMODE and stops: a dump with no length takes the
symbol's own size, so naming a register does not sweep its neighbors.

The count is **distinct registers, not names.** SVD describes a peripheral
once per operating mode, so a SERCOM's `CTRLA` arrives as six definitions
at one address -- `I2CM_CTRLA`, `I2CS_CTRLA`, `SPIM_CTRLA`, `SPIS_CTRLA`,
`USART_INT_CTRLA`, `USART_EXT_CTRLA`. They are six names for the same four
bytes, so reading any one of them counts as one register, not six.

Naming a register is a deliberate act and stays allowed, on the same
principle as writing: **once you name it, it is your call.** What is
refused is the *sweep* -- a `/mem.dump` over a peripheral range reads
every address in it precisely because it does not know what is there,
and that is the one move guaranteed to hit something that minds.

Every allowed peripheral read is logged, beside the write audit:

```text
# MEM.R 0xBF806000 len=4 regs=UMODE origin=cli
# MEM.R 0xBF806014 len=4 regs=UDATA read_effect=UDATA origin=cli
```

Reads of ordinary memory are not logged -- they have no side effect, and
logging them would bury the lines that matter. The `read_effect=` note
marks a register whose file says reading it changes device state, which
is what makes a later "what happened on Tuesday?" answerable.

These gates need no per-register knowledge beyond what a device file
already carries, which is deliberate: `access` is populated in every
vendor SVD, but `readAction` -- the field that would say *which*
registers are destructive to read -- is empty in all of them. That
knowledge lives only in datasheet prose, so termapy refuses the sweep
rather than pretending to a safety list it cannot have.

## The wire spec

A device supports `/mem.*` by answering three line-oriented commands.
This is the whole contract; a drop-in reference implementation ships in
the repo at `examples/firmware/termapy_mem.c`. It owns only the wire
grammar: you hand it a `TermapyMem_Ops` with two byte operations --
`read(addr, dst, count)` and `write(addr, src, count)` -- an optional
IRQ-mask `lock`/`unlock` pair (that is all `MEM.M` needs), and a
putchar-compatible output. Flat-MCU implementations of the two ops ship
as `TermapyMem_DirectRead/Write` with an editable region table and
width-aware accesses; banked parts, external memories and host tests
supply their own. No allocation, no printf.

```text
MEM.R <addr> <len>      ->  <ADDR>: <XX XX XX ...>     one or more rows
                            OK
MEM.W <addr> <hex>      ->  OK
MEM.M <addr> <and> <or> ->  <ADDR>: <old word bytes>    the PRE-modify word
                            OK
MEM.INFO                ->  {"max_block": 64, "address_bits": 32,
                             "endian": "le", "modify": true}
                            OK
any failure             ->  ERR <reason>
```

- `<addr>` is sent as `0x`-prefixed hex; `<len>` is decimal; `<hex>` is
  contiguous hex pairs (`1B00`). Parse the address with `strtoul(s, 0, 16)`
  and it accepts either form.
- A row is the address of its first byte in upper-case hex (8 digits on a
  32-bit part), a colon, then hex pairs separated by spaces. Rows may be
  any length; 16 bytes is conventional. termapy checks that consecutive
  rows are contiguous and that they add up to `<len>`.
- Every successful command ends with a line reading exactly `OK`. Any
  failure is a single line `ERR <reason>`; the demo device uses `usage`,
  `length` (more than `max_block` bytes) and `range` (outside its
  windows).
- Lines that are none of row / `OK` / `ERR` are ignored, so a monitor that
  echoes the command or prints a prompt is fine.
- `MEM.M` is optional: an atomic masked write, `word = (word & and) | or`
  with interrupts masked, width from the masks' digit count (2/4/8 hex
  digits = u8/u16/u32, address aligned to the width). Its reply is one
  row carrying the PRE-modify word, so the host's audit line gets a true
  "before" without a separate racy read. Without it, termapy's bit and
  mask writes fall back to read + write-back, which can race an ISR.
- `MEM.INFO` is optional. When the device answers it, termapy learns the
  block limit, address width, byte order and whether `MEM.M` exists
  (`"modify": true`) from the device itself and no profile block is
  needed. Without it, the defaults are 64 / 32 / `le` / no.
- The copy loop should store at the natural width when the length is 2 or
  4 and the address is aligned: a byte store to a peripheral register is a
  bus fault on PIC32 and Cortex-M.

The demo device (`termapy --demo`) is the reference implementation: it
serves 16 KB of memory from `0x0000` and the UART SFR window at `0xBF806000`,
seeded so the demo symbol table's names read as real values (`gTemp` is
27, `U1MODE` has `ON` and `BRGH` set). Its older `mem <addr> [count]` /
`mem <addr> =<hex>` command serves the same bytes through a typical
monitor's own grammar -- the worked example for the template dialect below.

## The profile block

When the device has no `MEM.INFO`, or you want to pin the facts, add a
`memory` block to the [profile](authoring-profiles.md):

```json
"memory": {
  "dialect": "termapy",
  "max_block": 64,
  "address_bits": 32,
  "endian": "le"
}
```

Explicit profile values win over `MEM.INFO`, which wins over the
defaults; `/mem.info` shows which source each fact came from. `dialect`
names the wire grammar: `termapy` is the spec above. An unrecognized
dialect loads but `/mem.*` refuse to guess.

With no profile block at all, a connected device that never answers
`MEM.INFO` is remembered as having **no memory interface** for the rest
of the connection: every `/mem.*` refuses immediately with the recorded
reason (no per-command timeouts) until `/mem.info` retries the probe or
you reconnect. A profile block is the author vouching for the device,
so its presence always proceeds. A template block whose `write` is
omitted is **read-only**: writes and bit operations refuse before
touching the device.

## A device with its own grammar: the template dialect

Most monitors already have a peek/poke command. Describe it and `/mem.*`
speak it -- names, chunking, the audit line and the MCP gate all stay:

```json
"memory": {
  "dialect": "template",
  "read": "mem {addr:X} {len}",
  "write": "mem {addr:X} ={byte:02X}",
  "ack": "^ok\\b",
  "error": "(?i)^\\s*err\\b",
  "max_block": 256,
  "address_bits": 32,
  "endian": "le"
}
```

| Field        | Default                           | Meaning                                                                 |
|--------------|-----------------------------------|-------------------------------------------------------------------------|
| `read`       | required                          | `str.format` template with `{addr}` and `{len}`; format specs work (`{addr:08X}`) |
| `row`        | `ADDR: XX XX ...` (hex, `0x` optional) | Regex with `addr` and `hex` groups matching one data row; stops at the first non-pair token, so an ASCII column is ignored |
| `row_bytes`  | 16                                | Most bytes one row carries                                              |
| `write`      | none (read-only)                  | Template with `{addr}` and `{byte}` (one byte per command) or `{hex}` (a block of pairs) |
| `ack`        | none                              | Regex a successful write reply must contain (`^ok\b`)                 |
| `error`      | `(?i)^\s*(err\|error\|fault)\b`   | Regex flagging a failed command anywhere in the reply                   |
| `terminator` | none                              | Regex that ends a reply early (a prompt); otherwise the reply ends at the idle gap |
| `settle_ms`  | 100                               | Idle gap that ends a reply                                              |

Reads are chunked to `max_block` (the device's count limit); a `{byte}`
write template sends one command per byte, so a 4-byte write is four
exchanges. The device's own error line is reported as
`Device error: err: address range ... not mapped`. The demo ships
`demo_legacy.profile.json` with exactly this block: `/profile.load
demo_legacy.profile.json` then `/mem.dump gTemp 16` reads the same bytes
through the legacy `mem` grammar; `/profile.unload` returns to the spec.

See also: [Symbols](symbols.md) for the address grammar and the symbol
table, [MCP server](mcp-server.md) for the confirm gate.
