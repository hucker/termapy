# Symbols

A symbol table gives names to the addresses in your firmware: `/sym main`
tells you where `main` lives, `/sym 0x2010` tells you that address is
`main+0x10`. It is the groundwork for named memory access (`/mem.*`, a
later step); on its own it replaces the "open the .map file and search"
ritual.

termapy reads exactly one symbol format, its own JSON. Converters turn a
toolchain's linker map into that file; the runtime never parses a vendor
map.

## Where it lives

```text
termapy_cfg/<name>/<name>.symbols.json
```

Beside the config, like the profile. It auto-loads whenever the config
loads (startup and `/cfg.load`) in the TUI, the CLI and the MCP server
alike, and a config switch drops the previous table. The demo config ships
one (`demo.symbols.json`), so `termapy --demo` has names for the addresses
its `mem` command answers for.

## Importing a linker map

```text
/sym.import build/default/mem.map
```

writes `<name>.symbols.json` beside the config and loads it. The format is
sniffed from the file; `format=xc32` picks one explicitly. Re-import after
every build; the sidecar is a generated file and is overwritten without
asking. Bare `/sym.load` re-reads it after a hand edit.

| Format | Toolchain                              |
|--------|----------------------------------------|
| `xc32` | Microchip XC32 (GNU ld) linker map     |

More converters land in the same registry; `/help sym.import` lists what
the running build knows. To add your toolchain, hand
[docs/symbol-converter-guide.md](https://github.com/hucker/termapy/blob/main/docs/symbol-converter-guide.md)
and one of your map files to an LLM, or follow it yourself: one module, one
registry line, one fixture.

A relative path given to `/sym.import` or `/sym.load` resolves against the
config folder, not the shell's working directory. Under the MCP server,
importing a map that lives outside the config folder needs
`TERMAPY_MCP_FS_UNCONFINED=1`; bare `/sym.load` and the auto-load are always
inside the sandbox.

## Commands

| Command                            | Example                     | Does                                                    |
|------------------------------------|-----------------------------|---------------------------------------------------------|
| `/sym <addr\|name>`                | `/sym 0x2010`               | Name to address, or address to `name+offset`            |
| `/sym.import <file> {format=<value>}` | `/sym.import build/mem.map` | Convert a linker map to the sidecar and load it      |
| `/sym.load {path}`                 | `/sym.load`                 | Reload the sidecar, or load an explicit file            |
| `/sym.unload`                      | `/sym.unload`               | Clear the loaded table (file untouched)                 |
| `/sym.search <pattern>`            | `/sym.search Mon*`          | Search names: exact, glob, regex, or substring          |
| `/sym.info`                        | `/sym.info`                 | File, source, counts by section, address range          |

`/sym` returns the *other* representation as its value: `$(A) <- /sym gTemp`
captures `0x00001000`, `$(N) <- /sym 0x2010` captures `main+0x10`.

## Address grammar

| Form          | Meaning                                                              |
|---------------|----------------------------------------------------------------------|
| `0x1000`      | hex                                                                  |
| `1000h`       | hex, assembly suffix                                                 |
| `1000`        | **decimal** 1000; bare hex is never guessed                          |
| `main`        | symbol name (exact, case-sensitive)                                  |
| `main+0x10`   | symbol plus offset (`main-4` too)                                    |
| `tick@adc.c`  | a duplicate static, qualified by file (`tick@adc`, `tick@src/adc.c`) |
| `name.<bit>`  | reserved for bit, slice and field access in a later step             |

An exact symbol name wins, so `count.12` (a name GCC emits) is the symbol,
not bit 12 of `count`. Duplicate names without a qualifier are refused with
the addresses and files listed, so you can pick one or type the address.

## The file

```json
{
  "symbols_version": 1,
  "source": "build/mem.map",
  "imported": "2026-08-29T10:12:00",
  "address_bits": 32,
  "endian": "le",
  "regions": [],
  "symbols": [
    {"name": "gTemp", "addr": "0x00001000", "size": 2, "section": "bss", "file": "sensor.c", "type": "u16"},
    {"name": "main",  "addr": "0x00002000", "size": 442, "section": "text", "file": "main.c"},
    {"name": "U1STA", "addr": "0xBF806010", "size": 4, "section": "sfr", "type": "u32", "rmw": false}
  ]
}
```

| Field     | Required | Meaning                                                                        |
|-----------|----------|--------------------------------------------------------------------------------|
| `name`    | yes      | Symbol name; dots kept verbatim                                                |
| `addr`    | yes      | `0x`-hex string or integer                                                     |
| `size`    | no       | Bytes; 0 = unknown (linker globals)                                            |
| `section` | no       | `text` / `bss` / `data` / `rodata` / `global` / `sfr` (shown as code, const, ...) |
| `file`    | no       | Defining object or source; what `name@file` matches                            |
| `type`    | no       | A scalar (`u8`..`u64`, `i8`..`i64`, `f32`, `f64`) or a format spec              |
| `rmw`     | no       | `false` forbids bit writes (W1C / self-clearing registers); honored later       |
| `space`   | no       | Reserved for Harvard parts                                                     |

`endian` and `regions` are stored now and honored by `/mem.*` later.
Unknown keys are ignored. `type` reuses the format-spec language from
[protocol testing](protocol-testing.md#format-spec-language), so a register
can carry named bits: `"type": "ON:B1-4.15 UEN:B1-4.8-9 BRGH:B1-4.3"`.

## JSON output

`/sym 0x2010 --json` (or any JSON-mode session, MCP included) returns a
record with the same keys on a hit and a miss:

```json
{"query": "0x2010", "addr": 8208, "addr_hex": "0x00002010", "symbolic": "main+0x10",
 "symbol": {"name": "main", "addr": 8192, "addr_hex": "0x00002000", "end": 8634, "size": 442,
            "section": "text", "file": "main.c", "type": "", "space": "", "rmw": true},
 "offset": 0, "suffix": ""}
```

See also: [Demo mode](demo.md) for the bundled table, [Protocol
testing](protocol-testing.md) for the format-spec syntax.
