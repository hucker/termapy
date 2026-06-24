# VT100 / terminal-emulation support — options & plan

**Status:** planning doc (untracked). Hand-off artifact for a fresh implementation context.
**Goal:** let termapy render a serial device's VT100/ANSI screen UI (cursor positioning,
in-place redraws, progress bars, menus, ncurses-style apps), which it cannot do today.
**Hard constraint:** must work on **Windows, macOS, and Linux**. This is the only firm
requirement; everything else is a trade-off.

---

## 1. Where termapy is today (the gap)

termapy is a **line-oriented serial monitor with ANSI color**, not a terminal emulator:

- Output widget is a `RichLog` (append-only scrollback) — `src/termapy/app.py:829`.
- Incoming text is rendered with Rich's `Text.from_ansi(...)` — `src/termapy/app.py:2740-2742`
  — which interprets **SGR** (colors/styles) only.
- The receiver splits bytes into lines on `\n` and **strips bare `\r`** —
  `src/termapy/serial_port.py:304-305` — so a `\r`-based progress redraw is lost before
  rendering.
- The only cursor/erase sequence recognized is a hard-coded clear-screen special case —
  `src/termapy/serial_port.py:164` (`CLEAR_SCREEN_RE`) — proving there is no general
  escape-sequence state machine.

**Consequence:** cursor positioning (`ESC[r;cH`), erase-line (`ESC[K`), scroll regions,
alternate screen, and bare-`\r` overwrites are not handled. Device menus, spinners,
live status boards render as garbage or lost output.

A second structural fact that shapes every option: the serial-RX → display method feeds
**four consumers off the line abstraction** — RichLog display, file log (`_log_line`),
the script/expect watcher (`repl.feed_lines`), and text capture —
`src/termapy/app.py:2743-2755`; plus `/grep` and `/find` read scrollback out of the
RichLog. Any in-TUI screen mode must coexist with these.

---

## 2. Key asset already in the tree: vendored miniterm

`src/termapy/vendor/serial/tools/miniterm.py` is the full pyserial miniterm, and it
already solves the parts that are annoying and OS-specific:

- Cross-platform raw console I/O — `ConsoleBase` + per-OS subclasses (`miniterm.py:43`).
- **Windows VT output enabling** — sets `ENABLE_VIRTUAL_TERMINAL_PROCESSING`
  (`miniterm.py:140-150`) so device escapes actually render on Windows.
- The byte pump — `Miniterm.reader()` (RX→stdout) / `Miniterm.writer()` (stdin→TX)
  (`miniterm.py:388`), threads started by `.start()`, awaited by `.join()`.
- Escape/menu keys — `exit_character` = Ctrl+] , `menu_character` = Ctrl+T.
- Constructor: `Miniterm(serial_instance, echo=False, eol='crlf', filters=())`
  — takes an already-open pyserial instance.

This is the cross-platform raw-terminal plumbing the hard constraint would otherwise
force us to write and debug per-OS.

## 3. The decision rule (why approach matters more than effort)

**pyte/our-own emulation is only worth running when the output surface is NOT already a
terminal.** A real terminal (Windows Terminal, iTerm, VS Code terminal, any *nix term) is
a better VT100 emulator than we will build. So:

- If the final surface is a real terminal → hand it the raw stream (passthrough).
- If the final surface is a Textual widget / MCP text / capture file → use pyte.

This rule eliminates the tempting-but-pointless "run pyte and re-print to the real
terminal" path (Option E).

---

## 4. Options

### Option A — Passthrough: `--vt100` non-TUI mode (host terminal emulates)

termapy becomes a thin raw pipe. Open the port from cfg, hand it to a miniterm-style
pump; the **host terminal** does all VT100 emulation.

**How:** add `--vt100` to the flag set in `entry.py` (where `--cli`/`--web`/`--mcp` are
defined, ~lines 75/99/214) and a branch in the mode if-ladder (`entry.py:336-347`,
alongside `--web`/`--mcp`, before the CLI/TUI loop) → new `_run_vt100_mode(args)`:
resolve cfg (port/baud/mode + `$(env.PORT)` expansion), open the port (reuse
`serial_engine`/`serial_port`/`port_control` open logic), construct
`vendor.serial.tools.miniterm.Miniterm(port)`, set exit/menu chars, `.start()` →
`.join()`, restore console on exit.

- **A1 — wrap miniterm directly.** Fastest; inherit its stdout/stdin pump and Ctrl-T menu.
- **A2 — native pump from miniterm's `Console`.** Lift the raw-IO, write a termapy pump so
  you control the log tee, a termapy-styled escape menu, and a "drop back into the TUI"
  exit key. (miniterm's `filters`/`Transform` hooks can do the log tee without subclassing.)

**Cross-platform:** ✅ all three — the OS-specific raw console + Windows VT enable already
live in vendored miniterm. *Runtime requirement:* a VT-capable host terminal (universal on
mac/linux; Win10+ modern console / Windows Terminal / VS Code on Windows). Risk
concentrated on legacy Windows `conhost`.

**Pros**

- Cheapest by far; highest fidelity (real, battle-tested emulators).
- Interactive input works **for free** (raw stdin→serial: arrows, function keys, Ctrl-C).
- Cross-platform raw I/O already solved in-tree; **no new dependency**.
- Doesn't touch `app.py` / Textual; isolated new entry path.
- Keeps connection-level niceties: cfg-driven port/baud/mode, `$(env.NAME)` placeholders,
  optional session-log tee, auto-reconnect, miniterm's Ctrl-T menu (break, DTR/RTS, baud).

**Cons**

- All-or-nothing: while in `--vt100` you're a raw pipe, **not** in the TUI — no `/grep`,
  no REPL `/` commands, no custom buttons, no hex/timestamp decoration (those would corrupt
  a raw stream anyway). Workaround for search: tee to the log, grep the log.
- Scrollback is the host terminal's, not termapy's.
- Depends on host-terminal VT quality (fine on modern terminals; legacy consoles iffy).
- Inside the VS Code integrated terminal some keys are captured by VS Code before the app
  sees them (already noted in `help/environment.md`; worse in raw mode).

**Effort:** **S** (A1 ~1–2 days; A2 ~2–4 days).

---

### Option B — pyte-backed screen mode **inside** the TUI

A pure pyte emulator core feeds a Textual widget that renders the cell grid. Boot into it
via `--vt100` and/or toggle with `/term.screen on|off`.

**How:**

- Pure module (`protocol.py`-adjacent, no Textual): wrap `pyte.Screen` + `pyte.Stream`;
  `stream.feed(text)`; expose `screen.buffer` / `screen.dirty` / `screen.display`.
  Unit-testable by asserting `screen.display`.
- Render widget: `RichLog` can't address cells — swap via a `ContentSwitcher` between
  `RichLog` (line mode) and a screen widget. MVP render: build one Rich `Text` from
  `screen.buffer` each repaint and `Static.update()` (cheap at 80×24); later optimize with a
  custom `render_line`/`Strip` widget.
- Feed wiring: in screen mode, feed the **unsplit** decoded RX stream to pyte (bypass the
  `\n`-split/`\r`-strip in `serial_port.py`). **Tee** the existing line-splitter in parallel
  so file log / `/grep` / expect / capture keep working (the "history" view); the pyte
  screen is the "display" view.
- Toggle + config: `/term.screen` (~10 lines, follow `_flag_toggle` in
  `builtins/commands/term.py:31-45`) + a config key documented in the 4 required sites
  (DEFAULT_CFG `defaults.py`, README config table, `help/config.md`, `demo.cfg`).
- Sizing: pick dimensions from the widget size; recreate/resize the pyte screen on resize.

- **B1 — full-screen** (chrome-free Textual screen, single widget).
- **B2 — windowed/split** (device screen + REPL/inspector simultaneously).
- **B3 — read-only first** (render the screen; **defer** interactive key passthrough). Lets
  you *watch* a boot menu / progress / status board correctly without navigating it.

**Cross-platform:** ✅ all three, and **most uniform** of all options — rendering happens
inside Textual (cross-platform) against a pure-Python emulator; it does **not** depend on
the host terminal's emulation quality. Input passthrough goes through Textual key events
(cross-platform). Lowest cross-platform risk; you control the pixels.

**Pros**

- Stays in the TUI; can coexist with termapy chrome/features (split view, grep-the-log).
- Uniform rendering across OSes (you own it; host-terminal quality irrelevant).
- The pyte core is reusable beyond the TUI: `screen.display` gives MCP a rendered-screen
  snapshot and `/cap` a "screen snapshot" capture — turning escape-soup into readable text.

**Cons**

- Most work. Must build the grid-render widget (style fidelity: pyte `Char` attrs +
  default-color sentinels → Rich/Textual theme; cursor cell).
- **Interactive input is the hard part** — keys → VT input sequences → serial, fighting
  Textual's focus/binding system; needs a raw-capture focus mode + clean exit. (B3 dodges
  this by shipping read-only first.)
- Sizing / no-SIGWINCH over serial — the device often doesn't know your dimensions.
- New dependency: `pyte` (LGPL). Fine as a normal/optional pip dep under termapy's
  "vendor only unmaintained deps" policy — NOT vendored. Likely a `[term]` extra.
- Re-emulates what host terminals already do, with potentially lower long-tail VT
  conformance than a mature emulator.

**Effort:** **M** for B3 read-only (~1 week); **L** for full interactive (input passthrough
is the long pole).

---

### Option C — Hybrid: ship A now, add B later

Ship Option A (`--vt100` passthrough) for the "I just want a VT100 terminal" case; add
Option B (pyte screen mode) only if a concrete need for **simultaneity** (TUI + screen) or
**snapshots** (MCP/capture) appears.

**Pros:** fastest path to real value; defers the hard/uncertain input work; the two serve
genuinely different goals; A doesn't block B.
**Cons:** two code paths eventually; doesn't itself resolve B's design questions.
**Effort:** A's effort now; B deferred.

---

### Option D — Shell out to an external terminal (tio / picocom / minicom / PuTTY)

termapy launches an external serial terminal for VT100 work.

**Pros:** zero emulation code; mature tools.
**Cons:** **fails the cross-platform constraint cleanly** — picocom/minicom are *nix,
PuTTY is Windows, tio is partial; no single uniform tool. Breaks termapy's "one tool, one
config" value; loses cfg/env-port integration; clumsy hand-off. **Not recommended.**

---

### Option E — pyte in a CLI path re-printing to the real terminal — REJECTED

Running pyte and re-emitting a rendered grid to a terminal that already emulates VT100 is
pure overhead with worse fidelity (see the decision rule, §3). Documented here only so it
isn't re-proposed.

---

## 5. Decision matrix

| Option                | Cross-platform          | Effort          | Fidelity                | Interactive input | Keeps TUI niceties | New dep    |
| --------------------- | ----------------------- | --------------- | ----------------------- | ----------------- | ------------------ | ---------- |
| **A** passthrough     | ✅ (host-term dependent) | **S**           | Highest (real emulator) | Free              | No (raw pipe)      | None       |
| **B3** pyte read-only | ✅ (most uniform)        | **M**           | Good (pyte)             | No (deferred)     | Yes                | pyte       |
| **B** pyte full       | ✅ (most uniform)        | **L**           | Good (pyte)             | Yes (hard)        | Yes                | pyte       |
| **C** hybrid          | ✅                       | S now / L later | Highest now             | Free now          | Partial            | pyte later |
| **D** shell out       | ❌ not uniform           | S               | Highest                 | Free              | No                 | external   |
| **E** pyte→real term  | n/a                     | —               | Worse                   | —                 | —                  | pyte       |

---

## 6. Recommendation

**Option C, starting with A1.** It satisfies the hard constraint immediately (the OS work
is already vendored), gives the highest-fidelity, fully-interactive terminal for the least
code, and matches the stated use case ("come up in vt100, don't care about niceties"). Keep
pyte (Option B) in reserve for when a real need for in-TUI simultaneity or MCP/capture
screen snapshots shows up — at which point the pure pyte core also pays off outside the TUI.

If the priority is instead "uniform, self-contained rendering that never depends on the host
terminal," start with **B3** (read-only pyte) — more work, but the most platform-independent
result and reusable for MCP/capture.

---

## 7. Implementation sketch — recommended path (Option A1)

1. **Flag:** add `--vt100` in `entry.py` next to `--cli`/`--web`/`--mcp` definitions.
   (cli arg defs live in `entry.py`, not `cli_flags.py`.)
2. **Dispatch:** in the mode if-ladder (`entry.py:336-347`), add — before the CLI/TUI loop:

```python
if args.vt100:
  from termapy.vt100 import run_vt100_mode   # new, Textual-free module
  run_vt100_mode(args)
  return
```

3. **Runner (`src/termapy/vt100.py`, new):**
   - resolve cfg (reuse the same config-resolution `entry.py` already does for `--cli`),
   - open the serial port (reuse `serial_engine`/`port_control` open logic to honor
     baud/mode/flow + `$(env.PORT)`),
   - `from termapy.vendor.serial.tools.miniterm import Miniterm`,
   - `term = Miniterm(port, echo=cfg_echo, eol=<map line_ending>)`,
   - set `term.exit_character` / `term.menu_character` (consider rebinding exit to "return
     to TUI"),
   - `term.start(); term.join(); term.close()` with console restore in a `finally`.
4. **Optional niceties (A2):** session-log tee via a miniterm `Transform`/`filter`;
   auto-reconnect loop around `Miniterm`; a termapy-styled escape menu.
5. **Docs:** README "modes" section, `help/` page (new `help/vt100.md` → add to `index.md`
   + `mkdocs.yml` nav), note the VS Code integrated-terminal key-capture caveat.
6. **Tests:** the runner is mostly I/O glue; unit-test cfg→port-open argument mapping and
   line-ending→eol mapping. The pump itself is the vendored miniterm (already its own
   concern). No CLI-gold entry (non-deterministic raw passthrough).

**Branch:** `feat/vt100-passthrough` (never edit main directly; `--no-ff` merge).

---

## 8. Open decisions (resolve before/at implementation)

1. **Exit key behavior:** quit the process (miniterm default Ctrl-]) vs. **return to the
   termapy TUI** (nicer; makes `--vt100` a toggle, not a dead end). The latter needs the
   runner to be re-enterable / hand control back to the mode loop.
2. **Log tee:** tee raw RX to the cfg log file in `--vt100`? (Recommended; cheap via a
   Transform. Decide raw vs ANSI-stripped.)
3. **`/term.screen` in-TUI toggle:** ship only the CLI flag (Option A), or also a TUI
   toggle (implies Option B work)? A and B can share the `--vt100` flag name but are
   different code paths.
4. **pyte dependency packaging** (only if/when B): normal dep vs `[term]` extra; confirm
   pyte's exact LGPL terms (fine as an upgradeable pip dep, not vendored).
5. **Config key?** A pure-flag `--vt100` may need no config key; if a default-launch-in-vt100
   option is wanted, that's a new key → must hit all 4 doc sites (see §4 Option B "How").

---

## 9. Hand-off notes for a fresh implementation context

Read this doc first, then these anchors (verify line numbers — code may have moved):

- Entry/mode dispatch & flag defs: `src/termapy/entry.py` (if-ladder ~336-347; flag defs
  ~75/99/214).
- Vendored terminal: `src/termapy/vendor/serial/tools/miniterm.py` (`Miniterm` 388,
  `Console`/VT-enable 43/140-150).
- Serial open/connect logic to reuse: `src/termapy/serial_engine.py`,
  `src/termapy/serial_port.py`, `src/termapy/port_control.py`.
- Current line-oriented render (for Option B feed wiring): `src/termapy/app.py:2685-2755`,
  RichLog at `:829`; line splitting `src/termapy/serial_port.py:297-319`.
- Toggle pattern (Option B): `src/termapy/builtins/commands/term.py:31-45`.
- Config doc obligations: `DEFAULT_CFG` in `defaults.py`, README config table,
  `help/config.md`, `builtins/demo/demo.cfg`.

Project rules that apply: branch first (never edit main), `--no-ff` merges, run full test
suite before merging, `ruff`/`ty` must be 0, update README/help/ARCHITECTURE as needed.
