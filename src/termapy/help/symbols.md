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
termapy_cfg/<name>/sym/<name>.symbols.json
```

In the config's `sym/` folder. It auto-loads whenever the config
loads (startup and `/cfg.load`) in the TUI, the CLI and the MCP server
alike, and a config switch drops the previous table. The demo config ships
one (`sym/demo.symbols.json`), so `termapy --demo` has names for the addresses
its `mem` command answers for.

## Importing a linker map

```text
/sym.import build/default/mem.map
```

writes `sym/<name>.symbols.json` under the config and loads it. The format is
sniffed from the file; `format=xc32` picks one explicitly. Re-import after
every build; the sidecar is a generated file and is overwritten without
asking. Bare `/sym.load` re-reads it after a hand edit.

After the first import, **bare `/sym.import` re-runs the same map through
the same converter** — the rebuild step, without retyping a build-tree
path:

```text
/sym.import C:\proj\_build\default\mem.map    # once
/sym.import                                   # after every rebuild
```

It reads the source and recipe the sidecar recorded, so it also works on a
table imported before recipes existed (that re-import is how such a table
gains one). An explicit `<file>` or `format=` always wins.

| Format | Toolchain                              |
|--------|----------------------------------------|
| `xc32` | Microchip XC32 (GNU ld) linker map     |

More converters land in the same registry; `/help sym.import` lists what
the running build knows. To teach termapy your toolchain, write a
converter -- see below.

## Your own converter, as a plugin

A plugin file may export a converter, so the map-to-table step becomes a
pipeline you own -- no PR, no second symbol store. Drop this in
`plugin/` (global, or beside one config for one board):

```python
from termapy.symbols.converters import xc32
from termapy.symbols.table import Symbol

FORMAT = "myboard"
DESCRIPTION = "xc32 map, no function addresses, plus typed SFRs"
DETECT = ()          # explicit-only: /sym.import <map> format=myboard

_SFRS = [Symbol("U1MODE", 0xBF806000, 4, "sfr", type="u32", rmw=False)]

def convert(text):
    rows = [s for s in xc32.convert(text) if s.section != "text"]
    return rows + _SFRS
```

Same four names a built-in converter exports -- only discovery differs.
Because your `convert` is the single producer of the table, a re-import
after a rebuild reproduces all of it: the rows you filtered stay gone and
the typed rows stay put. That is the point of doing it here rather than
hand-editing the sidecar, where the next `/sym.import` would erase your
work.

A converter may set every `Symbol` field -- `type`, `rmw`, `space`,
`file` -- so the typed registers and bit fields a linker map cannot
express become script output instead of hand-maintained data.

### The contract

`convert(text)` is **pure**: text in, list out. No I/O, no config, no
globals. It must **never raise on odd input** -- a wrong-format file, a
truncated build, an empty string all return `[]`, and `/sym.import`
renders `No symbols found`. Return unsorted; `SymbolTable` sorts by
`(addr, size, name)`. Emit each symbol once: a map often lists the same
symbol in a summary and a detail section, so prefer the detail section
(full names) and use the summary only for addresses it did not cover.

**Emit the run address (VMA), never the load address (LMA).** Initialized
`.data` lives in flash at its LMA and is copied to RAM at its VMA, and GNU
`ld` maps print both. `/mem.*` talks to the running device, so an LMA
gives you a table that looks correct and reads the wrong memory.

Skip what a C program cannot name: merged literal pools
(`.rodata.str1.1`), compiler temporaries (`.L123`), Arm mapping symbols
(`$t`, `$d`, `$a`).

### Sections

Use these words where they fit, else your toolchain's own short lowercase
one (an unmapped word is harmless, just unlabeled):

| `section` | Label | Put here |
|---|---|---|
| `text` | code | Functions, vectors, any executable |
| `bss` | bss | Zero-initialized RAM |
| `data` | data | Initialized RAM |
| `rodata` | const | Constants in flash |
| `global` | global | Sizeless linker-provided symbols (`__bss_start__`, `_estack`) |
| `sfr` | sfr | Registers; a linker map never emits these |

Map your toolchain's region names on (`ER_IROM1`, `CODE` -> `text`;
`ZI` -> `bss`; `RW` -> `data`; `RO-data`, `CONST` -> `rodata`).

### Naming and detection

Name the format after the **linker**, not the CPU -- "ARM" has at least
three toolchains with three map layouts (`armlink`, `iar`, `gnu-ld`).
Lowercase, hyphens only, no underscores.

- **`DETECT = ()` keeps you out of sniffing.** Your converter is one
  board's pipeline, not a toolchain, and its input is often a map a
  built-in would also claim. Select it with `format=`.
- **If you do give it detect markers, make them unique to that
  toolchain.** Never a phrase every GNU `ld` map prints (`Linker script
  and memory map`, `Memory Configuration`) -- that would claim maps
  belonging to every other GNU-ld toolchain. Two or three independent
  markers beat one; the sniff scans the whole file, so a marker deep in
  it is fine.
- **Converters follow the config.** A converter in a config's `plugin/`
  folder exists only while that config is loaded, exactly like a command.

If your toolchain is one others use, the same four names in a module under
`symbols/converters/` make it a built-in -- one module, one registry line,
one fixture under `tests/fixtures/maps/`. `xc32.py` is the reference.

A relative path given to `/sym.import` or `/sym.load` resolves against the
config folder, not the shell's working directory. Under the MCP server,
importing a map that lives outside the config folder needs
`TERMAPY_MCP_FS_UNCONFINED=1`; bare `/sym.load` and the auto-load are always
inside the sandbox.

## Parts: device files

A linker map has every symbol the firmware defines and none of the
registers the silicon fixes -- no linker ever sees `PORTA_OUT`. Those come
from a **device file** in the config's `dev/` folder: one flat JSON list of
registers per part.

```json
{
  "device_version": 1,
  "device": "atsame54",
  "registers": [
    {"name": "PORTA_DIR", "addr": "0x40003000", "size": 4,
     "peripheral": "PORTA", "description": "Data Direction"},
    {"name": "PORTA_IN",  "addr": "0x40003020", "size": 4, "access": "ro"},
    {"name": "PA00_PINCFG", "addr": "0x40003040", "size": 1,
     "fields": [{"name": "PMUXEN", "bit": 0}, {"name": "INEN", "bit": 1}]}
  ]
}
```

`dev/` is the board's bill of materials: everything in `<cfg>/dev/` loads,
`termapy_cfg/dev/` loads into every config, and a per-config file replaces
a global one with the same `device`. Registers merge over the build's
symbols at load and **survive `/sym.import`** -- they are the board's, not
the build's, and are never written to the sidecar. A build symbol that
shares a register's name wins, with a warning.

| Key | Meaning |
|---|---|
| `name`, `addr`, `size` | Required. `addr` is absolute, or an offset from `base` when `relocatable` |
| `fields` | Bit fields (`bit`, `width`, optional `values` labels); `type` is derived from them, so never give both |
| `access`, `read_effect`, `rmw` | `rw`/`ro`/`wo`; reading changes state (a FIFO pops, a flag clears); mask-writes forbidden |
| `peripheral`, `group`, `description`, `reset` | Context for `/sym.info` and the register view |
| `relocatable` + `instances` | N copies of a part: `[{"name": "ADC1", "base": "0x60000000"}, ...]` yields `ADC1_STATUS` |

A relocatable file with no instances is an error, never a silent load at
offset 0. Names must be identifiers (no dots -- `.` is the field-access
suffix). Vendor CMSIS-SVD files carry every one of these facts; a converter
that turns one into a device file, the way `/sym.import` turns a linker
map into the sidecar, is the next step.

## Commands

| Command                            | Example                     | Does                                                    |
|------------------------------------|-----------------------------|---------------------------------------------------------|
| `/sym <addr\|name>`                | `/sym 0x2010`               | Name to address, or address to `name+offset`            |
| `/sym.import {file} {format=<value>}` | `/sym.import build/mem.map` | Convert a linker map to the sidecar and load it (bare: re-import the same map) |
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
  "recipe": {"converter": "xc32"},
  "witness": {"mtime": 1756461120.0, "size": 482113},
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

`recipe` and `witness` are written by `/sym.import` and drive the staleness
check below; both are optional, and a table without them is fine. `endian`
and `regions` are stored now and honored by `/mem.*` later.
Unknown keys are ignored. `type` reuses the format-spec language from
[protocol testing](protocol-testing.md#format-spec-language), so a register
can carry named bits: `"type": "ON:B1-4.15 UEN:B1-4.8-9 BRGH:B1-4.3"`.

## Is the table still current?

A symbol table is derived data, and the map is rebuilt on every compile.
A stale table is worse than none: `/mem.dump gTemp` reads a real address
that no longer belongs to `gTemp`.

`/sym.import` records two things so termapy can tell you. The **witness**
(`mtime` + `size`) answers *is this stale*, for one `stat` and no file
read. The **recipe** answers *can termapy fix it* — and its absence is
meaningful, not missing data: a hand-written table has no recipe and
never will.

That gives three honest answers:

| Status | Meaning | What you see |
|---|---|---|
| `in_sync` | The map is as it was at import | Nothing — silence is the good case |
| `stale` | The map was rebuilt since import | A warning at load and a `status` row in `/sym.info`, naming the exact `/sym.import` line that fixes it |
| `unknown` | The question can't be asked — no witness, a hand-written source, or a map that's gone | Nothing; this is the normal state of a hand-written table, not a defect |

```text
Symbols may be out of date: mem.map was rebuilt (size changed) since import
  rebuild: /sym.import build/mem.map format=xc32
```

Two deliberate limits:

- **termapy never regenerates the table for you.** A rebuild is the
  common case, and a terminal that rewrote your symbol table mid-session
  would be worse than a yellow line. It reports; you run the command.
- **The witness detects a *rebuild*, not a *change*.** A no-op recompile
  reports stale even when every address is identical — which is why the
  message says "rebuilt". Hashing a multi-megabyte map at every config
  load to remove a harmless false positive is the wrong trade, and
  re-importing is cheap.

The verdict is also in `/sym.info --json` as `status`, `fixable` and
`rebuild_command`, so an agent learns the table is out of date at the same
moment it learns the table exists.

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
