# Device library: available vs installed

Status: **A, B and C built** (2026-09-16). D deferred -- see Staging.

## The problem

Device files work, but only if you already know what you have. There is no
way to ask termapy what parts exist, and the only place a device surfaces
is `/sym.info`, which lists what is *loaded* and nothing else.

Two questions have no answer today, and they are different questions:

- **What parts do I have?** — a library of prepared `.device.json` files.
- **What parts is this board using?** — the config's own bill of materials.

Conflating them is what makes "should the cfg have an import line?" feel
unanswerable. It should not have one: an import is a one-time conversion,
not a per-session declaration.

## What termapy knows about

**Only its own `.device.json` format.** Converting a vendor's SVD, ATDF,
EDC or anything else is a separate tool's job, or a community's. The
built-in `/dev.import` SVD converter stays because it exists and works,
but nothing in this design depends on it, and no new vendor format becomes
termapy's problem. A library holds normalized files; how they got that way
is outside the boundary.

## The library

A hierarchical tree the user populates:

```text
termapy_cfg/lib/
  microchip/
    mcu/
      pic32cm/
        microchip-pic32cm5164le00100.device.json
        microchip-pic32cm1216le00032.device.json
    adc/
      microchip-mcp3564.device.json
  lattice/
    fpga/
      lattice-ice40up5k.device.json
```

`vendor/parttype/family/device`, with the leaf named to be globally unique
(`mfg-partnumber`). Two facts, one home each:

- **Where the file sits** is the hierarchy — for browsing and for humans.
- **What the part IS** is the `device` field inside the file — for lookup
  and collision detection.

These must never disagree. Load refuses a file whose `device` field does
not match its filename stem, because a file at `microchip/mcu/...` that
identifies as something else is exactly the wrong-part-wrong-address
hazard the memory commands exist to prevent.

The library is **not a load layer.** Nothing in `lib/` loads. It is a pool
to select from — the module docstring already says a catalog "is a place
to COPY from and never a load layer," and this keeps that, with "copy"
becoming "reference."

## Placement belongs to the board, not the part

This is the decision the rest follows from.

The format already separates a part from its placement: `relocatable:
true` plus `instances` means the registers are offsets and the instance
says where each copy sits. So a relocatable part does **not** need editing
to be placed — placement is already a separate field. The only reason
editing feels necessary is that there is nowhere to put the instance list
except inside the file.

Put it in a **reference file** — a few lines in `dev/` naming a library
part and where it sits on this board:

```json
{
  "device_version": 1,
  "ref": "lattice-ice40up5k",
  "instances": [{"name": "FPGA0", "base": "0x60000000"},
                {"name": "FPGA1", "base": "0x60010000"}]
}
```

A fixed-address part (an MCU) needs no placement, so its reference is just
`{"device_version": 1, "ref": "microchip-pic32cm5164le00100"}`. The library
file stays pristine in both cases.

### Why a file in `dev/` and not a cfg key

The first draft of this memo put the reference list in the cfg. The
history says otherwise, and it is right: the `devices` cfg key built during
the original work was deleted because **"`dev/` IS the bill of materials,
the way `plugin/` is the plugin list."** There is no cfg key for plugins
either — across the whole tool, **folder presence is the declaration.**

A reference file keeps that invariant. `dev/` still lists the board's
parts, one file per part, and whether a given file carries registers or
points at the library is an implementation detail of that file. Nothing
new has to be explained about where devices come from.

Consequences that matter:

- **No duplication.** A 2517-register PIC32CM file exists once, however
  many configs use it. Copying it per config duplicates megabytes to
  customize nothing.
- **No drift.** An edited copy silently diverges from the library version;
  a reference cannot.
- **Re-import is safe.** Replacing a library part with a newer conversion
  keeps every board's placement, because the placement was never in the
  file.

### Why not copies

Copies were the initial instinct, on the reasoning that device files
rarely change so duplication is cheap, and placement needs editing anyway.
The second half is what does not hold — placement is a field, not an edit
— and without it the first half only argues that copies are *tolerable*,
not that they are better.

There is also an asymmetry that one rule cannot serve:

| Part | Needs placement? | Size | Copy cost |
|---|---|---|---|
| MCU (PIC32CM, E54) | No — absolute | ~2500 registers | megabytes, no benefit |
| Relocatable (FPGA, FRAM) | Yes — `instances` | small | cheap, but per-board |

Referencing serves both; copying serves only the second.

### What copies still have to keep working

`<cfg>/dev/*.device.json` must not stop loading. It is how the E54 and the
demo device work today, it is the right answer for a one-off or hand-written
part, and taking it away would break a working setup to buy nothing. The
library is an addition, not a replacement: a config's devices are the
`dev/` folder, whether each file carries registers or a `ref`.

## The two listings

Distinct concepts, so distinct commands rather than one with a flag:

```text
/dev.list              # installed: what THIS config loaded, and from where
/dev.lib {pattern}     # available: what the library holds
```

`/dev.list` answers "what is on this board" — one row per loaded device,
its register count, and its origin (`dev/` file, or a library reference).
It is the command that makes a misconfigured board visible.

`/dev.lib` answers "what could I use" — the library tree, filtered. It
needs a pattern argument because a populated library is hundreds of files;
bare should show vendors and counts rather than dumping everything.

Both are pure listings: no loading, no side effects, `data=` for agents.

## Open questions

1. ~~Does a `devices` cfg key repeat a mistake?~~ **Settled: yes, avoided.**
   The reference lives in a `dev/` file, not a cfg key — see above.
2. **Library root configurable?** `termapy_cfg/lib/` by default. Making the
   path a setting would let the library be a synced folder or a cloned
   community repo. Cheap to add, easy to defer.
3. **Instance-name collisions across parts.** Two library parts both placed
   as `ADC1` would collide in the merged table. `resolve_devices` already
   detects register-name clashes; this needs the same check one level up.
4. **What `/dev.import` writes.** Today it writes into `<cfg>/dev/`. With a
   library it should probably write into `lib/` and leave the config to
   reference it — but that changes existing behavior, so it is a separate
   decision from the rest.

## Staging

Each step is independently useful and independently shippable.

- **A (DONE) — `/dev.list`.** Installed devices only, against the mechanism that
  exists today. No new concepts, immediately answers the question that
  started this. Small.
- **B (DONE) — the library and `/dev.lib`.** The `lib/` tree, the filename/identity
  check, and the listing. Still nothing loads from it; purely additive.
- **C (DONE) — reference files.** A `dev/` file with `ref` (plus `instances` when
  relocatable) resolves against the library. The step that removes the
  duplication.
- **D (deferred) — placement ergonomics.** Whatever B and C prove is missing: a command
  to add a reference without hand-editing JSON, most likely.

Stop after any step. A and B together already give both listings, which is
what was actually asked for; C is what removes the duplication.
