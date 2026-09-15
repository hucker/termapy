# Adding a linker-map converter

Instructions for writing a converter that turns one toolchain's linker map
into termapy's symbol table, so `/sym` and every `/mem.*` command accept
names from that build. Written to be handed to an LLM together with a real
map file from the toolchain in question, and readable by a person doing the
same job by hand.

Scope: one new module under `src/termapy/symbols/converters/`, one registry
line, one fixture, one test file, two doc lines. Nothing else changes.

If you don't want to ship it upstream, the same four names in a `plugin/`
file make a converter for your machine alone -- see section 8.

## 1. What you are building

termapy reads exactly one symbol format, its own JSON sidecar
(`<cfg_dir>/sym/<cfg_stem>.symbols.json`). A converter is a pure function from
map text to a list of `Symbol` records; `/sym.import` calls it, wraps the
result in a `SymbolTable`, and writes the sidecar. The runtime never parses
a vendor map, and the converter never touches a file, a config, or a device.

Everything above the sidecar already works for any toolchain: the address
grammar (`main+0x10`, `tick@adc.c`), `/sym.search`, dump-row annotation,
typed reads, register bit fields. The converter's only job is names,
addresses, sizes, sections and object files.

## 2. Read these first

Open them in this order. The first two are the whole contract; the third is
the one shipped converter and the pattern to copy.

| File | Why |
|---|---|
| `src/termapy/symbols/converters/__init__.py` | The registry: `ConverterSpec`, `CONVERTERS`, `find_converter`, and the four names a module must export |
| `src/termapy/symbols/table.py` | `Symbol` (the record you emit) and `SymbolTable` (what wraps it; you do not construct one) |
| `src/termapy/symbols/converters/xc32.py` | The reference converter: regex table at the top, multi-pass `convert`, documented limits |
| `tests/test_symbols_xc32.py` | The reference tests, including the registry-shape and pairwise-exclusivity tests that will run against your module automatically |
| `tests/fixtures/maps/xc32_sample.map` | What a fixture looks like: a real map trimmed to ~70 lines |
| `src/termapy/help/symbols.md` | The user-facing page; you add one table row |

## 3. The contract

### 3.1 Module exports

The module `src/termapy/symbols/converters/<format>.py` exposes exactly
these four names. All four are read at registry import, so a missing one
fails loudly at startup rather than at `/sym.import` time.

```python
FORMAT: Final[str] = "armlink"                       # registry key; see naming below
DESCRIPTION: Final[str] = "Arm Compiler (armlink) image map"   # one line for /help sym.import
DETECT: Final[tuple[str, ...]] = ("Image Symbol Table", ...)   # any ONE substring identifies the format

def convert(text: str) -> list[Symbol]: ...          # pure; never raises; [] when nothing matched
```

### 3.2 The `Symbol` record

Positional order is `(name, addr, size, section)`; the rest are keywords.

| Field | Fill it? | Rule |
|---|---|---|
| `name` | yes | The identifier as the C program spells it. Dots kept verbatim (`count.12`). |
| `addr` | yes | `int`. Parse hex with `int(text, 16)`. A vendor map prints its own fixed radix, so bare hex without `0x` is fine here; the "never guess bare hex" rule is about what a user types at the prompt. **Emit the run address (VMA), never the load address (LMA).** Initialized `.data` lives in flash at its LMA and is copied to RAM at its VMA; `/mem.*` talks to the running device, so only the VMA is right. GNU ld maps print both. |
| `size` | yes | Bytes from the linker; `0` when the map gives none (linker globals). |
| `section` | yes | One of the six words below when it maps cleanly, else the toolchain's own short lowercase word. |
| `file` | when the map says | The defining object, reduced to a stem (`adc.c` or `adc`), so `name@file` can split duplicate statics. Reuse `xc32.object_file_stem`; it is toolchain-neutral. |
| `space` | Harvard parts only | On a part where flash and RAM addresses overlap (8-bit PIC, AVR, 8051) and the map says which space a symbol is in, fill it with the map's own word (`code`, `data`, `xdata`). Nothing reads it yet; it is the hook for when the address grammar and the wire spec grow a space qualifier. Leave it empty on a flat address space. |
| `type`, `rmw` | no | Leave the defaults. Types come from a hand edit of the sidecar or a later step; a linker map does not know them. |

Section vocabulary and its display label:

| `section` | Label | Put here |
|---|---|---|
| `text` | code | Functions, vectors, any executable |
| `bss` | bss | Zero-initialized RAM |
| `data` | data | Initialized RAM |
| `rodata` | const | Constants in flash |
| `global` | global | Sizeless linker-provided symbols (`__bss_start__`, `_estack`) |
| `sfr` | sfr | Reserved for hand-written tables; a map never emits it |

Map your toolchain's region names onto these (`ER_IROM1`, `CODE` -> `text`;
`ZI` -> `bss`; `RW` -> `data`; `RO-data`, `CONST` -> `rodata`). The section
drives only the label and the `/sym.info` counts, so an unmapped word is
harmless, just unlabeled.

### 3.3 Behavioral rules

- **Pure.** Text in, list out. No I/O, no config, no globals, no logging.
- **Never raise on odd input.** A wrong-format file, a truncated build, an
  empty string: all return `[]`. The handler renders
  `No symbols found in <file> (<format>)`; that is the whole error path.
- **Return unsorted.** `SymbolTable` sorts by `(addr, size, name)`.
- **Emit each symbol once.** A map often lists the same symbol in a summary
  and a detail section. Prefer the detail section (full names) and use the
  summary only for addresses the detail pass did not see. Track seen
  addresses or `(addr, name)` pairs. Two symbols MAY legitimately share a
  name (statics in different files) or an address (aliases); whether to keep
  aliases is a converter decision you document.
- **Skip what a C program cannot name.** Merged literal pools
  (`.rodata.str1.1`), compiler temporaries (`.L123`), Arm mapping symbols
  (`$t`, `$d`, `$a`), section-start markers that are not symbols.
- **Line endings are not yours.** The handler reads with
  `errors="replace"`; you use `text.splitlines()`, which handles `\r\n`.
  Never anchor a regex on `\n`.
- **Regexes at module level**, `re.compile`, named groups, `Final`, with a
  comment showing the exact map line each one matches (copy the xc32 style).
  A reader must be able to check a regex against the fixture by eye.
- **Module docstring** states the passes in order, what wins on conflict,
  and the known limits. The xc32 docstring is the template.

### 3.4 `DETECT` rules

- Markers must be **unique to this toolchain**. Never a phrase every GNU
  `ld` map prints (`Linker script and memory map`, `Memory Configuration`),
  or a future `gnu-ld` converter can never claim its own maps.
- Two or three independent markers are better than one: a header the
  toolchain always prints, plus a toolchain path or banner that appears
  near the top. The sniff scans the whole text, so a marker deep in the
  file is fine.
- Registry order is sniff precedence. Put a specific toolchain before a
  generic one.
- The pairwise-exclusivity test enforces this against every fixture: your
  fixture must contain one of your markers and none of any other
  converter's, and every other fixture must contain none of yours.

### 3.5 Naming the format key

Name the **linker**, not the CPU. "ARM" has at least three toolchains with
three map layouts.

| Toolchain | Key |
|---|---|
| Arm Compiler 5/6 (Keil MDK, `armlink`) | `armlink` |
| IAR Embedded Workbench (`ilink`) | `iar` |
| GNU `ld` used generically (`arm-none-eabi-gcc`, RISC-V GCC, ...) | `gnu-ld` |
| NXP/Freescale CodeWarrior | `codewarrior` |
| TI Code Generation Tools (`cl2000`, `armcl`) | `ti-cl` |
| Microchip XC8 / XC16 | `xc8` / `xc16` |

Lowercase, hyphens only. **No underscores**: the exclusivity test derives
the owner of a fixture from the stem before the first `_`, so a key like
`gnu_ld` breaks it. Add a version suffix (`armlink-5`) only if two versions
of the same linker actually print incompatible maps.

## 4. Steps

### Step 1: get a real map and cut a fixture

Ask for a map from a real build if one was not supplied. Do not invent one;
the point of the fixture is that it is what the linker actually prints.

Trim it to a fixture at `tests/fixtures/maps/<format>_<anything>.map`
(the part before the first `_` must equal `FORMAT`). Keep it small, under
about a hundred lines, but keep one instance of **every syntactic shape**
the converter must handle:

- a function and a variable in each section you map
- a symbol whose name and address are on different lines, if the map does that
- a truncated name in a summary section, if the map has one, alongside its full form
- two statics with the same name from different object files
- a sizeless linker global
- at least one thing to skip (a literal pool, a mapping symbol)
- the `DETECT` marker(s), where they really occur

Real symbol names are fine. Strip absolute paths that identify a person's
machine if they add nothing.

### Step 2: write the module

Copy the shape of `xc32.py`: docstring, `FORMAT` / `DESCRIPTION` /
`DETECT`, the regex table, `convert`. Walk the map once per pass with
`for line in lines:`. Loop variables name the element in the singular
(`line`, `symbol`), and a regex hit is `match`, not `m`; `xc32.py` predates
that rule, so do not copy its `m`. Google-style docstrings and type hints
throughout.

### Step 3: register it

In `converters/__init__.py`, one import and one tuple line:

```python
from termapy.symbols.converters import armlink, xc32
...
CONVERTERS: Final[tuple[ConverterSpec, ...]] = (
    ConverterSpec(xc32.FORMAT, xc32.DETECT, xc32.convert, xc32.DESCRIPTION),
    ConverterSpec(armlink.FORMAT, armlink.DETECT, armlink.convert, armlink.DESCRIPTION),
)
```

That line is the only registration. `FORMATS`, the `format=` value on
`/sym.import`, `/help sym.import`, and the "Unknown map format" error
message all derive from it.

### Step 4: write the tests

`tests/test_symbols_<format>.py`, mirroring `test_symbols_xc32.py`:

- a module-scoped fixture that reads the map, and one that builds
  `SymbolTable(<module>.convert(text))`
- the total count, and one spot check per syntactic shape from Step 1:
  name, `addr`, `size`, `section`, `file`
- the dedup rule: a symbol present in two map sections appears once
- the skip rule: the literal pool / mapping symbol is absent
- `find_converter(text=map_text).format == FORMAT` (sniff)
- `find_converter(format_name=FORMAT)` (explicit)
- `convert("hello") == []` and `convert("") == []` (never raises)

House style: `# Arrange` / `# Act` / `# Assert` comments, `actual ==
expected` order, and an assert message that says what a failure means, not
what the expression is. No mocking; the input is a string.

The tests you did not write also run: `test_symbols_xc32.py` checks every
registered converter for a callable `convert`, a non-empty `DETECT`, a
non-empty `DESCRIPTION`, and unique keys, and runs the pairwise-exclusivity
test over every fixture.

### Step 5: run the gates

```sh
uv run pytest tests/test_symbols_<format>.py tests/test_symbols_xc32.py tests/test_sym_commands.py -q
uv run pytest -m "not slow" -q
uv run ruff check src/termapy/ tests/
uv run ty check src/termapy/
```

All four must be clean. Then a live check with your real map, from a
folder that has a config:

```text
/sym.import build/firmware.map
/sym.info
/sym <a name you know>
/sym.search <a prefix>
```

### Step 6: documentation

- `src/termapy/help/symbols.md`: one row in the "Importing a linker map"
  format table.
- `ARCHITECTURE.md`: one line under `converters/` in the module tree,
  matching the `xc32.py` line. Leave the line counts alone; the release
  script refreshes them.
- Do not rebuild the HTML help and do not edit README, CHANGELOG, or any
  count or badge.

## 5. Non-goals

- **No ELF, DWARF, or `.sym` parsing.** A different feature with a
  different reader; a map converter stays a text parser.
- **No edits to** `address.py`, `table.py`, `session.py`, `format.py`, or
  `builtins/commands/sym.py`. If the map genuinely needs a field `Symbol`
  lacks, stop and raise it as a design question rather than adding one.
- **No `type` inference.** A linker map has no types. Users add `type` to
  the sidecar by hand or load a profile.
- **No byte order.** `/mem.*` typed reads take endianness from the device
  (`MEM.INFO`) or the profile's memory block, not from the symbol table.
  The table's `endian` field is stored and unused; leave it.
- **No `address_bits`.** `/sym.import` writes 32 regardless of the part.
  This is a printed hex width only. A user on a 16-bit part can edit the
  sidecar; a converter has no channel to set it today (known limit).

## 6. Where to look in common maps

The map in hand is the authority. This table only says where to start
reading; verify every claim against the file before writing a regex.

| Toolchain | Section to parse | Typical row shape |
|---|---|---|
| GNU `ld` (any GCC target) | `Linker script and memory map` | `.text.name  0xaddr  0xsize  obj.o`, with name-only lines whose address follows on the next line; globals as `0xaddr  name` |
| Arm Compiler `armlink` | `Image Symbol Table` | `name  0xaddr  <Data or Thumb Code>  size  obj.o(section)`; skip `$t`/`$d` rows |
| IAR `ilink` | `ENTRY LIST` | `name  0xaddr  size  <Code or Data>  obj.o` |
| TI CGT | `GLOBAL SYMBOLS: SORTED BY Symbol Address` | `addr  name` pairs, sizes from the `SECTION ALLOCATION MAP` |
| CodeWarrior | `# Symbol Table` / `Linker Generated Symbols` | `addr  size  name  file` columns; layout varies by generation |

A GNU `ld` map from a non-Microchip toolchain will match nothing today,
because the XC32 converter deliberately detects only Microchip-specific
markers. A `gnu-ld` converter would sit after `xc32` in the registry and
detect on `Linker script and memory map`; that is the one case where a
generic marker is correct, because it is the fallback.

## 7. Acceptance checklist

- [ ] Module exports `FORMAT`, `DESCRIPTION`, `DETECT`, `convert`; key is lowercase, hyphens only, named after the linker
- [ ] `convert` is pure, returns `[]` on junk, never raises, emits each symbol once
- [ ] `DETECT` has no generic GNU `ld` phrase; fixture stem prefix equals `FORMAT`
- [ ] Fixture covers every syntactic shape the regex table handles
- [ ] One `ConverterSpec` line added to `CONVERTERS`, in precedence order
- [ ] `tests/test_symbols_<format>.py` covers count, one spot check per shape, dedup, skip, sniff, explicit, junk
- [ ] pytest (fast suite), ruff, ty all clean
- [ ] `help/symbols.md` table row and `ARCHITECTURE.md` tree line added; nothing else touched

## 8. If you would rather not send a PR

A **plugin converter** exports the same four names from a file in a
`plugin/` folder, so your toolchain works on your machine without
touching termapy. Reach for it when the converter is yours alone -- one
board, one build, or a pipeline that also filters rows and adds typed
registers:

```python
from termapy.symbols.converters import xc32
from termapy.symbols.table import Symbol

FORMAT = "myboard"
DESCRIPTION = "xc32 map, no function addresses, plus typed SFRs"
DETECT = ()          # explicit-only: /sym.import <map> format=myboard

def convert(text):
    rows = [s for s in xc32.convert(text) if s.section != "text"]
    return rows + [Symbol("U1MODE", 0xBF806000, 4, "sfr", type="u32", rmw=False)]
```

Everything in sections 3 and 6 applies unchanged -- it is the same
contract. The differences: give `DETECT` an empty tuple unless your
format is genuinely unlike every built-in (a board pipeline should not
hijack sniffing), the converter lives and dies with the config that
loaded it, and a raise is caught and reported as
`Converter error: <format>: ...` rather than crashing the session. See
"Your own converter, as a plugin" in `help/symbols.md`.

**A built-in converter is still the right answer for a real toolchain**
-- one shipped module serves everyone on that compiler and is sniffed
automatically. Use a plugin when the pipeline is specific to you.

## 9. If you would rather skip the converter entirely

The sidecar is plain JSON, and `/sym.load` reads any file of that shape.
A twenty-line script in your build (in any language) that walks your map
and writes this gets you named memory access with no termapy change at all:

```json
{
  "symbols_version": 1,
  "source": "build/firmware.map",
  "imported": "2026-09-14T09:00:00",
  "address_bits": 32,
  "endian": "le",
  "regions": [],
  "symbols": [
    {"name": "gTemp", "addr": "0x20000010", "size": 2, "section": "bss", "file": "sensor.c"},
    {"name": "main",  "addr": "0x08000400", "size": 442, "section": "text", "file": "main.c"}
  ]
}
```

Field rules are in `help/symbols.md` under "The file". A converter is the
same work done once, shipped for everyone on that toolchain, and sniffed
automatically by `/sym.import`.
