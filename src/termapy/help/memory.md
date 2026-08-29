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

| Command                   | Example                     | Does                                                        |
|---------------------------|-----------------------------|-------------------------------------------------------------|
| `/mem.dump <addr> {len}`  | `/mem.dump gTemp 16`        | Hexdump `len` bytes (default 64), rows annotated with symbols |
| `/mem.write <addr> <hex>` | `/mem.write gFlags 07000000` | Write hex bytes (`1B00`, `1B 00`, `0x1B 0x00`); audited     |
| `/mem.info`               | `/mem.info`                 | Dialect, block limit, address width, byte order, and their sources |

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

## The wire spec

A device supports `/mem.*` by answering three line-oriented commands.
This is the whole contract; about 40 lines of C on any monitor.

```text
MEM.R <addr> <len>      ->  <ADDR>: <XX XX XX ...>     one or more rows
                            OK
MEM.W <addr> <hex>      ->  OK
MEM.INFO                ->  {"max_block": 64, "address_bits": 32, "endian": "le"}
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
- `MEM.INFO` is optional. When the device answers it, termapy learns the
  block limit, address width and byte order from the device itself and no
  profile block is needed. Without it, the defaults are 64 / 32 / `le`.
- The copy loop should store at the natural width when the length is 2 or
  4 and the address is aligned: a byte store to a peripheral register is a
  bus fault on PIC32 and Cortex-M.

The demo device (`termapy --demo`) is the reference implementation: it
serves 12 KB of RAM at `0x1000` and the UART SFR window at `0xBF806000`,
seeded so the demo symbol table's names read as real values (`gTemp` is
27, `U1MODE` has `ON` and `BRGH` set). Its older `mem <addr> [len]`
command is left as it was: the example of a device with its own grammar.

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
names the wire grammar; `termapy` is the spec above, and a device with
its own peek/poke grammar is a later revision of this block (its keys are
reserved). An unrecognized dialect loads but `/mem.*` refuse to guess.

See also: [Symbols](symbols.md) for the address grammar and the symbol
table, [MCP server](mcp-server.md) for the confirm gate.
