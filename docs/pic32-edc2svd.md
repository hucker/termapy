# PIC32 register maps via edc2svd -- restart notes

Status as of 2026-10-03: the path is proven end to end on one part, no
termapy code was needed, and one gap remains (register access). Nothing
below is committed; this file is the restart point.

## The decision

PIC register support is an open-source path, not termapy code. The
device-library design note already draws the line: termapy knows only its
own `.device.json`, keeps its one SVD importer, and "converting a vendor's
SVD, ATDF, EDC or anything else is a separate tool's job, or a
community's". The classic PICs (MIPS PIC32MX/MZ, 16-bit, 8-bit) have no
SVDs, only Microchip EDC `.PIC` files, so the open-source piece is an
EDC-to-SVD converter. One exists.

Two standing rules apply:

- `/mem.*` is an OPTIONAL interface that is STANDARD when present. Nothing
  here may make any part of termapy require it.
- This work goes on its OWN branch (`docs/pic32-edc2svd` or similar),
  never on `chore/termapy-mem-regions`.

## The pipeline

| step | what | status |
|---|---|---|
| edc2svd | external tool, MIT, Rust, <https://github.com/kiffie/edc2svd> | installed, v0.5.0 |
| SVD import | termapy's `/dev.import part.svd` | exists, unchanged |
| termapy support | device file in `lib/`, reference in `dev/`, `/mem.*` by register name | exists, unchanged |

Install and convert:

```text
cargo install edc2svd
edc2svd "C:\Program Files\Microchip\MPLABX\v6.30\packs\Microchip\PIC32MX_DFP\1.6.369\edc\PIC32MX795F512L.PIC" PIC32MX795F512L.svd
```

Then, inside termapy (any cfg; the demo works for a dry run):

```text
/dev.import PIC32MX795F512L.svd
/dev.list
/sym UART1_U1MODE
/sym.search UART1_*
```

Note `/sym.info` takes no name; `/sym <name>` is the lookup.

## Where the source files are

MPLAB X 6.30 bundles 3838 EDC `.PIC` files and zero SVDs, under
`C:\Program Files\Microchip\MPLABX\v6.30\packs\Microchip\<family>_DFP\<ver>\edc\`.
Every PIC, dsPIC and AVR family is there; PIC32MX_DFP alone has 120 parts.
Newer packs pulled on demand into `C:\Users\chuck\.mchp_packs\` may carry
SVDs as well (PIC32CM-LE does: 22 SVDs), in which case `/dev.import` the
SVD directly and skip edc2svd. The rule is pack version, not CPU core.

## Proof, 2026-10-03, PIC32MX795F512L

- `.PIC` (1.3 MB) -> SVD (2.9 MB): 69 peripherals, 2955 registers, the
  CLR/SET/INV siblings included.
- `/dev.import` in a scratch `--demo` cfg: 2955 registers into
  `lib/pic32mx795f512l.device.json`, referenced from `dev/`; merged table
  2968 symbols. Bit fields and reset values present.
- No per-mode register aliases (edc2svd merges EDC's modes); every one of
  the 2955 addresses is distinct.
- Names come out `PERIPH_REG`: `UART1_U1MODE`, `PORTA_TRISA`, `WDT_WDTCON`,
  so a bit is `UART1_U1MODE.BRGH`.

Spot checks, all matching the datasheet:

| register | address |
|---|---|
| UART1_U1MODE | 0xBF806000 |
| UART1_U1MODECLR | 0xBF806004 |
| UART1_U1STA | 0xBF806010 |
| UART1_U1TXREG | 0xBF806020 |
| UART1_U1RXREG | 0xBF806030 |
| WDT_WDTCON | 0xBF800000 |
| PORTA_TRISA | 0xBF886000 |
| PORTA_PORTA | 0xBF886010 |

## The gap: every register imports as read-write

edc2svd emits no `<access>` element, so the importer records `rw` for all
2955 registers. termapy's write-only read refusal and read-only write
refusal therefore never fire on these parts. Until fixed, a PIC32 device
file is an address book with bit names, not a safety net: fine for
`/mem.read UART1_U1MODE.BRGH`, not something to let an agent sweep.

The EDC source has what is needed. Every `edc:SFRDef` carries a per-bit
`edc:access` string, MSB first: `r` read-only bit, `n` normal read-write,
`-` unimplemented (and `w` write-only where it occurs). U1RXREG is all
`r`; WDTCON is a mix. The CLR/SET/INV registers are not separate
`SFRDef`s: the base register has `edc:portals="CLR SET INV"` and edc2svd
synthesizes the three siblings from it. They are write-only by PIC32
architecture.

### The patch, for a fork of edc2svd

- Derive SVD `<access>` from the string: `read-only` when every
  implemented bit is `r`, `write-only` when every one is `w`,
  `read-write` otherwise; per-field `<access>` from the field's bit span
  where SVD allows it.
- Mark every synthesized portal register (`*CLR`, `*SET`, `*INV`)
  `write-only`.
- termapy's importer already honors `access`; nothing changes on this
  side.

Fork is usable at once via `cargo install --git <fork>`; send the change
upstream as a pull request (repo is MIT, 8 stars, last push 2023-10, so
expect to wait).

### What the patch does not fix

Read side effects. Reading U1RXREG pops the FIFO and nothing in EDC says
so; no vendor format does except Nordic's. Same situation as every SVD
part today, which is why the bulk-read gate is address-based and single
reads are logged.

## Remaining work

1. Fork edc2svd, add `<access>` as above, re-run the proof, confirm
   `/mem.read` of a `*CLR` register is refused and `/mem.write` of
   U1RXREG is refused. Pull request upstream.
2. On the docs branch: a "PIC32 MIPS parts: convert the EDC file with
   edc2svd, then import" paragraph in the Get-a-part section of
   `src/termapy/help/devices.md`, and one README line. No termapy code,
   no test that exercises edc2svd (it owns its correctness; the importer
   is already tested), no credits entry (external tool, not a dependency).
3. Redistribution: converting your own packs for your own bench is fine.
   Publishing converted files needs the DFP license read first.
4. 16-bit and 8-bit PICs are untested. edc2svd is PIC32-scoped; the 8-bit
   quirks (joined 16-bit registers, banked data sectors) are probably
   unsupported and would be upstream work too.

## Related

- `docs/device-library-design.md` -- the boundary this follows.
- Help topics: devices, symbols, memory.
- Earlier finding: SVD's alias-per-mode shape (PIC32CM) and why testing
  one vendor's files is not enough.
