# Symbols

> **Status: preview.** This works and is used daily, but it is still being developed: its commands, files and behavior may change in any release.

A symbol table gives names to the addresses in your firmware: `/sym main`
tells you where `main` lives, `/sym 0x2010` tells you that address is
`main+0x10`. It is what named memory access (`/mem.*`) reads, and on
its own it replaces the "open the .map file and search" ritual.

termapy reads exactly one symbol format, its own JSON. Converters turn a
build's ELF, or its linker map, into that file; the runtime never parses
a vendor file.

## Where it lives

```text
termapy_cfg/<name>/sym/<name>.symbols.json
```

In the config's `sym/` folder. It auto-loads whenever the config
loads (startup and `/cfg.load`) in the TUI, the CLI and the MCP server
alike, and a config switch drops the previous table. The demo config ships
one (`sym/demo.symbols.json`), so `termapy --demo` has names for the addresses
its `mem` command answers for.

## Importing your build

```text
/sym.import out/default.elf
```

writes `sym/<name>.symbols.json` under the config and loads it. Import the
**ELF** when the build has one: it has every name, address and size, and,
when the build has debug info (`-g`), each variable's C type, so `/mem.read`
decodes `int16_t` as signed and `float` as a float. A linker map
(`/sym.import mem.map`) has names and sizes only; it stays the path for a
toolchain with no ELF, and the only source of the linker script's named
memory regions.

The format is recognized from the file (an ELF by its signature, a map by
its text); `format=elf` or `format=xc32` picks one explicitly. Re-import
after every build; the sidecar is a generated file and is overwritten
without asking. Bare `/sym.load` re-reads it after a hand edit.

After the first import, **bare `/sym.import` re-runs the same file through
the same converter** — the rebuild step, without retyping a build-tree
path:

```text
/sym.import C:\proj\out\default.elf    # once
/sym.import                          # after every rebuild
```

It reads the source and recipe the sidecar recorded, so it also works on a
table imported before recipes existed (that re-import is how such a table
gains one). An explicit `<file>` or `format=` always wins.

| Format | Reads                                                                     |
|--------|---------------------------------------------------------------------------|
| `elf`  | An ELF from any toolchain and CPU (GCC, Clang, XC32, IAR, Arm Compiler); types from its DWARF debug info |
| `xc32` | A Microchip XC32 (GNU ld) linker map                                      |

More converters land in the same registry; `/help sym.import` lists what
the running build knows. To teach termapy your toolchain, write a
converter -- see below.

### Types from the debug info

A type is written only where the debug info states it plainly, by its
encoding and byte size, never by its C name; anything else stays untyped,
exactly as a map leaves it, and reads by its size.

| C declaration                    | `type`                                              |
|----------------------------------|-----------------------------------------------------|
| an integer, `bool`, `char`       | `u8`..`u64` / `i8`..`i64` (`char` signed or not as the CPU's ABI says) |
| `float`, `double`                | `f32`, `f64`                                        |
| a pointer                        | the unsigned integer of its size                    |
| an `enum`                        | its underlying integer                              |
| an array of those                | the element's type; the size is the whole array     |
| a `struct`                       | a layout of its members (`count:U4-3 gain:F8-5`) in the ELF's byte order; bit fields wait for a later step |
| anything `const`                 | the same, with `access: ro`                         |
| a `const` scalar                 | the same, plus `value`: its initializer (an integer, an enumerator, a float, the address a pointer holds); a pointer also records what it points at (`pointee`) |

A build without `-g` imports every symbol untyped; startup and library
objects that carry their own debug info stay typed.

### Constants and aliases from the build

A `const` scalar's bytes are its value for the program's life, so the
import reads them: `/sym kBaud` ends with `= 115200`, a pointer constant
with `-> 0x41008080`. A variable's initializer is not its value and is
never read; arrays and structs of constants wait for a later step.

A pointer constant to a register block is how firmware says what a
peripheral is for, and termapy turns it into names:

```c
volatile PortGroup *const SPI_PORT = &PORT->Group[2];   /* PORTC on this board */
```

When a loaded device file places a block at exactly that address, every
register of the block gains a second name under the constant's:
`PORTC_DIR` is also `SPI_PORT_DIR`, with the register's address, size,
fields and safety flags unchanged, so `/mem.read SPI_PORT_DIR.P4` reads the
same bits under the board's own vocabulary. `/sym.info` lists each block
named (`alias: SPI_PORT -> PORTC@0x41008080  (6 registers)`), and `/sym`
shows an alias as `= PORTC_DIR`. The demo has one: its `CONSOLE` constant
points at the UART block in `dev/console.device.json`, so `/sym CONSOLE_MODE`
and `/mem.read CONSOLE_MODE.ON` work out of the box.

The rules, so nothing is guessed: the constant must be a pointer to a
struct (a pointer to one register is a value, not a name for the block);
its value must be one block's base, not an address inside it or one that
two blocks share; a name the build already owns stays the build's, and
the load says which alias it skipped. Aliases exist only in the merged
table -- they need the build and the device file -- and are rebuilt with
it; the sidecar never holds one. The firmware side is one line with
external linkage: a `static const` pointer is folded away by the optimizer
and never reaches the ELF.

## Your own converter, as a plugin

A plugin file may export a converter, so the map-to-table step becomes a
pipeline you own -- no PR, no second symbol store. Drop this in
`plugin/` (global, or beside one config for one board):

```python
from termapy.symbols.converters import xc32
from termapy.symbols.table import Symbol

FORMAT = "myboard"
DESCRIPTION = "xc32 map, no function addresses, plus typed SFRs"
DETECT = ()  # explicit-only: /sym.import <map> format=myboard

_SFRS = [Symbol("U1MODE", 0xBF806000, 4, "sfr", type="u32", rmw=False)]


def convert(text):
    rows = [s for s in xc32.convert(text) if s.section != "text"]
    return rows + _SFRS
```

Same names a built-in converter exports -- only discovery differs.
Because your `convert` is the single producer of the table, a re-import
after a rebuild reproduces all of it: the rows you filtered stay gone and
the typed rows stay put. That is the point of doing it here rather than
hand-editing the sidecar, where the next `/sym.import` would erase your
work.

A converter may set every `Symbol` field -- `type`, `rmw`, `space`,
`file` -- so the typed registers and bit fields a linker map cannot
express become script output instead of hand-maintained data.

A converter may also export `layout(source)`, returning a
`termapy.symbols.Layout`: the memory regions the file declares (`Region`
objects: `name`, `start`, `size`, `access`), and the target's address
width and byte order when the format states them. It follows the same
contract as `convert`. The built-in `xc32` reads GNU ld's `Memory
Configuration` table; `elf` reads the ELF header and its loadable
segments. Without it the table has no regions and the default width and
byte order.

A **binary** format adds `MAGIC = b"..."`, its file signature: its
`convert` and `layout` then receive the file's bytes instead of its text,
and it is recognized by that signature before any text marker is tried (a
binary file can contain another format's marker: an XC32 ELF holds
`/xc32/` in its debug strings). Its `DETECT` is ignored.

### The contract

`convert(text)` (or `convert(data)` for a binary format) is **pure**:
the file in, list out. No I/O, no config, no globals. It must **never
raise on odd input** -- a wrong-format file, a truncated build, an empty
string all return `[]`, and `/sym.import` renders `No symbols found`. Return unsorted; `SymbolTable` sorts by
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

The section decides the default access: a symbol in `text` or `rodata`
is read-only (`access: ro`) unless the converter says otherwise, so
`/mem.*` refuses to write code or a constant.

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

The registers the silicon fixes -- the half a linker map never has -- come
from **device files**, which have their own page: [Devices](devices.md).
They merge over the build's symbols at load, survive `/sym.import`, and a
build symbol with the same name wins, with a warning.

## Commands

| Command                            | Example                     | Does                                                    |
|------------------------------------|-----------------------------|---------------------------------------------------------|
| `/sym <addr\|name>`                | `/sym 0x2010`               | Name to address, or address to `name+offset`            |
| `/sym.import {file} {format=<value>}` | `/sym.import build/mem.map` | Convert a linker map to the sidecar and load it (bare: re-import the same map) |
| `/sym.load {path}`                 | `/sym.load`                 | Reload the sidecar, or load an explicit file            |
| `/sym.unload`                      | `/sym.unload`               | Clear the loaded table (file untouched)                 |
| `/sym.search <pattern>`            | `/sym.search Mon*`          | Search names: the exact name, else contains or a glob   |
| `/sym.info`                        | `/sym.info`                 | File, source, counts by section, address range, regions |

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
| `name.<bit>`  | a bit, slice or field (`U1MODE.ON`, `gFlags.4-6`); see [Memory](memory.md) |

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
  "regions": [
    {"name": "rom", "start": "0x00000000", "size": 524288, "access": "ro"},
    {"name": "ram", "start": "0x20000000", "size": 65536, "access": "rw"}
  ],
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
| `type`    | no       | A scalar (`u8`..`u64`, `i8`..`i64`, `f32`, `f64`) or a format spec; absent on a 1, 2, 4 or 8 byte symbol = the unsigned integer of its size |
| `access`  | no       | `rw` / `ro` / `wo`; absent = the section's (`ro` for `text` and `rodata`, else `rw`) |
| `rmw`     | no       | `false` forbids bit and mask writes (W1C / self-clearing registers)             |
| `space`   | no       | Reserved for Harvard parts                                                     |
| `value`   | no       | A constant's initializer as the build holds it (a number); the ELF converter writes it for every `const` scalar |
| `pointee` | no       | For a pointer constant, what it points at: `scalar`, `struct` or `other`       |
| `alias_of`| no       | The register this name stands for; set on the aliases the load derives          |

`recipe` and `witness` are written by `/sym.import` and drive the staleness
check below; both are optional, and a table without them is fine.
`address_bits` and `endian` come from an ELF's header (a map states
neither, so they default to 32 and `le`); `endian` is for reference,
since `/mem.*` uses the device's own byte order (`/mem.info`). `regions`
are the build's memory regions -- one per row of a map's memory table, or
one per loadable segment of an ELF -- each `{name, start, size, access}`
with `access` left out when the file states none; `/sym.info` shows them,
and `/mem.*` does not act on them yet. Unknown keys are ignored. `type`
reuses the format-spec language from
[protocol testing](protocol-testing.md#format-spec-language), so a register
can carry named bits: `"type": "ON:B.15 UEN:B.8-9 BRGH:B.3"` -- bits of the
register word, its width from `size` and its byte order from the device
(see [Memory](memory.md#typed-reads-and-bits)).

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
