# Devices

A linker map has every symbol the firmware defines and none of the
registers the silicon fixes -- no linker ever sees `PORTA_OUT`. A
**device file** supplies that half: one flat JSON list of a part's
registers, with names, addresses, bit fields and the safety flags that
`/mem.*` acts on. "Device", not "CPU": an MCU's peripherals, an ADC on
the external bus and an FPGA register block are the same kind of fact, and
a board usually has several.

## Get a part

Any part with a CMSIS-SVD file -- every ARM Cortex-M vendor, most RISC-V --
is one command:

```text
/dev.import C:\...\PIC32CM-LE_DFP\1.3.280\svd\PIC32CM5164LE00100.svd category=mcu/pic32cm
  Imported 2517 registers for pic32cm5164le00100 from PIC32CM5164LE00100.svd (svd)
    -> m3_bin/lib/microchip-technology/mcu/pic32cm/pic32cm5164le00100.device.json
  50 peripherals; referenced by dev/pic32cm5164le00100.device.json
```

That writes two files: the **part** (the registers, ~2 MB, stored once in
the config's library) and a **reference** in `dev/` (three lines saying
this board uses it). From then on the part loads every time the config
loads -- TUI, CLI and MCP server alike -- and `/mem.read PORT_GROUP0_DIR`
works. Nothing goes in the `.cfg` file: a file being in `dev/` *is* the
declaration.

`category=` nests the part under its vendor, which the file supplies.
`to=global` stores it in the shared `termapy_cfg/lib/` for every config
instead. `use=off` imports without touching this config. The SVD is read
once, at import; to re-convert it every session, put the command in
`on_connect_cmd` -- a missing SVD then reports an error and the existing
part stays in force.

SVDs come from the vendor's CMSIS pack (MPLAB X's pack manager keeps
Microchip's under `.mchp_packs`) or the
[cmsis-svd-data](https://github.com/cmsis-svd/cmsis-svd-data) repository.
A format termapy doesn't know is a plugin converter with `KIND = "device"`
-- the same four names as a symbol converter.

## Installed vs available

Two questions, two commands:

```text
/dev.list              # what THIS config loaded: layer and placement
/dev.lib {pattern}     # what the libraries hold, loaded or not (* = loaded here)
```

The **library** is a tree of parts, nested however suits you
(`microchip/mcu/pic32cm/`, `lattice/fpga/`). There are two, layered the way
`plugin/` and `dev/` are: the config's own `<cfg>/lib/` over the shared
`termapy_cfg/lib/`, the per-config one winning a name clash. Keep a
board's parts in its own `lib/` and the config folder is self-contained:
check it in and its references resolve on any machine. Nothing in a
library loads by itself; it is a pool to pick from with `/dev.use`.

## Placing a part: two ADCs of the same type

A bus ADC has no SVD, so its part is written once by hand, with offsets
and `relocatable: true` -- no base address anywhere in it:

`m3_bin/lib/acme/adc/acme-adc16.device.json`

```json
{
  "device_version": 1,
  "device": "acme-adc16",
  "relocatable": true,
  "registers": [
    {"name": "STATUS", "addr": "0x00", "size": 4, "access": "ro",
     "fields": [{"name": "BUSY", "bit": 0}, {"name": "READY", "bit": 1}]},
    {"name": "CTRL",   "addr": "0x04", "size": 4,
     "fields": [{"name": "START", "bit": 0}, {"name": "CHAN", "bit": 4, "width": 3}]},
    {"name": "DATA",   "addr": "0x08", "size": 4, "access": "ro", "read_effect": true}
  ]
}
```

Then the board says where its two chips sit:

```text
/dev.use acme-adc16 at=ADC1@0x60000000 ADC2@0x60001000
  Using acme-adc16: 6 registers at ADC1@0x60000000 ADC2@0x60001000 -> dev/acme-adc16.device.json
```

which writes the reference

```json
{"device_version": 1, "ref": "acme-adc16",
 "instances": [{"name": "ADC1", "base": "0x60000000"},
               {"name": "ADC2", "base": "0x60001000"}]}
```

and yields `ADC1_STATUS` at `0x60000000`, `ADC2_STATUS` at `0x60001000`,
and so on: `/mem.read ADC1_STATUS.READY`, `/mem.write ADC2_CTRL.START 1`.
Two files, however many chips. Move one to a new chip select by editing
its base or re-running `/dev.use`; add a third with `ADC3@...`.

A relocatable part **must** be placed: `/dev.use` refuses rather than
guessing, because any addresses in the library part are an example, not
this board's. A fixed-address part (an MCU) needs no `at=`; its reference
is one line.

A reference may override `instances` and nothing else -- to change a
part's registers, edit the library part. A whole `.device.json` copied
into `dev/` still loads, and is right for a one-off.

## What a device file may say

| Key | Meaning |
|---|---|
| `device` | Identity; must match the filename (`vendor-partnumber` keeps it unique) |
| `name`, `addr`, `size` | Required per register. `addr` is absolute, or an offset when `relocatable` |
| `fields` | Bit fields (`bit`, `width`, optional `values` labels); `type` is derived from them, so never give both |
| `access`, `read_effect`, `rmw` | `rw`/`ro`/`wo`; reading changes state (a FIFO pops, a flag clears); mask-writes forbidden |
| `peripheral`, `group`, `description`, `reset` | Context for `/sym.info` and the register view |
| `relocatable` + `instances` | Offsets from a base; each placed copy prefixes its registers with its name |

Register names must be identifiers (no dots -- `.` is field access).
Registers merge over the build's symbols at load and **survive
`/sym.import`**; a build symbol with the same name wins, with a warning.
`termapy_cfg/dev/` loads into every config; a per-config file replaces a
global one with the same `device`.

## Registers are not variables

Loading a device file changes what `/mem.*` allows at those addresses. A
register is live silicon read through a driver that assumes it is the only
reader, so a bulk read over registers is refused, a write-only register
refuses every read, and single reads are logged. See
[two bargains](memory.md#two-bargains-your-variables-and-the-silicon).

See also: [Symbols](symbols.md) for the build's own symbols and the address
grammar, [Memory](memory.md) for `/mem.*` and the wire spec a device
implements.
