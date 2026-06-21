# VT100 / terminal support — build plan (decisions locked)

**Status:** authoritative implementation handoff. Supersedes the exploratory
`VT100_PLAN.md` (options menu). Untracked draft — no commit yet.
**Audience:** a fresh implementation context (and future-you). Read top to bottom,
confirm the Open Decisions (§8), then start Phase 1.

**Goal:** let termapy deal with serial devices that emit terminal control beyond plain
lines — from `\r` progress redraws up to full cursor-addressed VT100/ANSI UIs.
**Hard constraint:** must work on **Windows, macOS, and Linux**. The only firm
requirement; everything else is a trade-off already decided below.

---

## 1. Decisions locked (and the reasoning, so it isn't re-litigated)

1. **There are three tiers of "terminal-ness," not one** (see §3). Pick per device class;
   don't assume "VT100" means full emulation.
2. **Decision rule:** run an emulator (pyte) *only when the output surface is not already a
   terminal*. If the surface is a real terminal → hand it the raw stream (passthrough). If
   the surface is a Textual widget / MCP text / capture file → use pyte. (This kills the
   "run pyte and re-print to a real terminal" non-starter.)
3. **DO NOT build interactive VT100 inside the Textual TUI.** It stacks a *third* layer of
   key interception — VS Code (steals keys) → Textual bindings/focus (steals more) → your
   widget must capture + re-encode → serial. That is maximum effort *and* maximum
   fidgetiness (the same friction termapy already fights in VS Code, doubled). Rejected; see
   §6.
4. **Humans driving a device → passthrough (Phase 1).** A non-TUI `--vt100` mode *drops the
   Textual layer entirely* (termapy isn't a TUI in that mode). One interception layer
   (the host terminal) — the same one every serial terminal faces — and *zero* extra layers
   if run in a standalone terminal tab instead of VS Code's integrated one.
5. **If pyte ever renders inside the TUI, it is READ-ONLY** (a viewer/snapshot). Viewing
   routes no keystrokes to the device, so it sidesteps the interception stack. Never wire
   device-bound key passthrough through Textual.
6. **An LLM driving a device → headless pyte core + MCP (Phase 2).** No human keyboard → no
   interception at all. The LLM sends key *names* via a tool; termapy encodes the bytes.
   Per the tools-vs-LLM economics: push the deterministic terminal emulation into the tool
   (pyte), not the model. Raw-byte LLM driving is token-expensive (re-sends the whole screen
   on every redraw) and nondeterministic (the model emulates the terminal "in its head").
7. **VT100-drive is a fallback for interface-poor devices**, not a default. A screen +
   keystroke interface is hard to safety-gate (you can't easily mark "the `F` key on this
   menu = factory reset"), whereas a named-command MCP API can be gated per command. Prefer
   a device's command/JSON API whenever one exists.

---

## 2. Key existing asset + reuse targets

`src/termapy/vendor/serial/tools/miniterm.py` is the full pyserial miniterm and already
solves the OS-specific hard parts — directly serving the cross-platform constraint:

- Cross-platform raw console I/O — `ConsoleBase` + per-OS subclasses (`miniterm.py:43`).
- **Windows VT output enabling** — `ENABLE_VIRTUAL_TERMINAL_PROCESSING` (`miniterm.py:140-150`).
- Byte pump — `Miniterm.reader()`/`writer()`; `.start()`/`.join()`/`.close()` (`miniterm.py:388`).
- Escape/menu keys — `exit_character` = Ctrl+] , `menu_character` = Ctrl+T.
- Constructor — `Miniterm(serial_instance, echo=False, eol='crlf', filters=())`; takes an
  already-open pyserial instance.

**Reuse for Phase 1:**
- Config resolution: mirror `_run_cli_mode` (`cli.py:973`) — demo / infer-from-run-file /
  resolve / find / default-in-memory. Consider extracting a shared helper.
- Port open: `SerialEngine.connect()` (`serial_engine.py:331`) opens via
  `self._open_fn(self._cfg)` (the open fn lives in `port_control.py`; honors port name,
  baud/mode, `$(env.PORT)` expansion, serial-number resolution). **Grab the raw pyserial
  object and hand it to `Miniterm` — do NOT start termapy's `SerialReader`; Miniterm owns
  the read loop in passthrough.**

---

## 3. The three tiers of need (scope the device class first)

| Tier | What the device emits | Who | termapy today | Fix |
|------|----------------------|-----|---------------|-----|
| 1. Line-oriented | plain lines, maybe SGR color | most MCU/bare-metal, AT, NMEA, Modbus | ✅ handled | none |
| 2. Bare `\r` redraw | progress bars/spinners rewriting one line | *near-universal* across firmware | ❌ strips `\r` (`serial_port.py:305`) | **Phase 0** (cheap, broad) |
| 3. Full VT100 | cursor addressing, menus, ncurses | embedded Linux consoles (vi/top/menuconfig), BIOS/SOL, network/industrial menus, bootloaders | ❌ not handled | **Phase 1** (passthrough) / **Phase 2** (pyte) |

Tier 2 is the highest-ROI, broadest win and is *independent* of VT100 work.

---

## 4. Phase 1 (PRIMARY) — `--vt100` passthrough mode

Non-TUI raw pipe; the host terminal does VT100 emulation.

**Steps**
1. **Flag:** add `--vt100` where `--cli`/`--web`/`--mcp` are defined in `entry.py`
   (~lines 75/99/214); store `args.vt100`.
2. **Mode selection — cfg *and* CLI both pick it.** vt100 is a *terminal-interaction
   mode*, a peer of `cli`/`tui` — NOT an early `--web`/`--mcp`-style separate runtime. Fold
   it into the **existing** mode mechanism (`entry.py:378-420`), which already resolves
   `default_ui` from the cfg and lets `--cli` override it:
   - **cfg:** allow `default_ui: "vt100"` — widen the valid set at `entry.py:402`
     (`if mode not in ("cli", "tui", "vt100")`); `mode` is read from `default_ui` at
     `entry.py:401`.
   - **CLI:** `--vt100` overrides cfg — add `if args.vt100: mode = "vt100"` alongside the
     `args.cli` check at `entry.py:378`.
   - **loop branch** (`entry.py:405-413`):
     ```python
     elif mode == "vt100":
         from termapy.vt100 import run_vt100_mode   # new, Textual-free module
         result = run_vt100_mode(args)
     ```
   Precedence is then identical to today: **`--vt100` flag > cfg `default_ui` > "tui"
   default.** Bonus: the loop already switches modes via the return value, so "exit vt100 →
   drop into the TUI" (Open Decision §8.2) is just `run_vt100_mode` returning `"tui"`.
3. **New module `src/termapy/vt100.py` (no Textual import):**
   - resolve cfg (mirror `cli.py:973`),
   - open the raw port (reuse the `port_control` open path; see §2) — raw pyserial object,
     no `SerialReader`,
   - `from termapy.vendor.serial.tools.miniterm import Miniterm`,
   - `term = Miniterm(port, echo=<cfg echo>, eol=<map cfg line_ending → 'cr'|'lf'|'crlf'>)`,
   - set `term.exit_character` / `term.menu_character` (see Open Decision §8.2),
   - `term.start(); term.join()`; `finally: term.close()` + console restore.
4. **Optional (A2 niceties):** session-log tee via a Miniterm `filters`/`Transform`;
   auto-reconnect loop around `Miniterm`; termapy-styled escape menu.
5. **Docs:** README "modes" section; new `help/vt100.md` (add to `help/index.md` +
   `mkdocs.yml` nav); note the VS Code integrated-terminal key-capture caveat.
6. **Tests:** unit-test cfg→`Miniterm` argument mapping and `line_ending`→`eol` mapping.
   No CLI-gold entry (raw passthrough is non-deterministic). The pump is vendored miniterm
   (its own concern).

**Cross-platform:** ✅ all three via vendored miniterm. *Runtime need:* a VT-capable host
terminal (universal on mac/linux; Win10+/Windows Terminal/VS Code on Windows; legacy
`conhost` iffy). Standalone terminal tab = zero extra key-interception layers; VS Code
integrated terminal = one layer (same as any serial terminal there).

**Effort:** S (wrap ~1–2 days; +niceties ~2–4 days).
**Done when:** `termapy --vt100 <cfg>` gives an interactive terminal where a device's menus
/ vi / top render and respond correctly on win/mac/linux.

---

## 5. Phase 2 (OPTIONAL, high-leverage) — headless pyte core + MCP

Build the emulation *core* once; it powers MCP drive, optional read-only TUI viewer, and
`/cap` snapshots.

1. **Pure module `src/termapy/terminal_emulator.py` (no Textual):** wrap `pyte.Screen` +
   `pyte.Stream`; `feed(text)`; expose `render_text()` (→ `screen.display`), `dirty`,
   `cursor`, `resize(cols, rows)`. Unit-testable by asserting `render_text()`.
2. **Dependency:** add `pyte` as an optional extra (`[term]`) — NOT vendored (it's
   maintained; LGPL is fine as an upgradeable pip dep under the project's vendor-only-dead
   policy).
3. **MCP tools (`mcp/server.py`):**
   - `read_screen` → `render_text()` (clean rendered grid, not escape soup),
   - `send_keys "Down Down Enter"` → name→VT-bytes map → serial write.
   Headless: no keyboard, no Textual → zero key-interception.
4. **Optional read-only TUI viewer:** `ContentSwitcher` between `RichLog` (line mode) and a
   screen widget; feed the *unsplit* decoded RX to the emulator; **tee** the existing
   line-splitter so file log / `/grep` / expect / capture keep working
   (`app.py:2685-2755`). **Read-only — no device-bound key passthrough.**
5. **Optional `/cap` screen snapshot:** capture `render_text()` instead of raw bytes.

**Cross-platform:** ✅ most uniform — pure-Python pyte + rendering you control; independent
of host-terminal quality.
**Effort:** core S; MCP tools S–M; read-only viewer M.
**Done when:** an MCP client can `read_screen`/`send_keys` to navigate a menu-only device;
core has unit tests.

---

## 6. Rejected approaches (with why — do not re-propose)

- **Interactive VT100 inside the TUI** — 3-layer key interception (VS Code → Textual →
  device); max effort, max fidgetiness. If you want device-screen + chrome together, use the
  Phase-2 **read-only** viewer instead.
- **Raw-byte MCP** (LLM parses escape soup, sends raw keys) — token-expensive and
  nondeterministic; push emulation into the tool (pyte) per §1.6.
- **Shell out to tio/picocom/minicom/PuTTY** — no single uniform cross-platform tool
  (**fails the hard constraint**); breaks "one tool, one config."
- **pyte → re-print to a real terminal** — re-emulating what the host already does; pure
  overhead, worse fidelity.

---

## 7. Build order & recommendation

1. **Phase 1 (`--vt100` passthrough)** — the primary deliverable; cheapest real capability;
   matches "come up in vt100, don't care about niceties."
2. **Phase 0 (`\r` progress fix)** — optional, independent, cheap, helps almost every
   device; do it whenever (good warm-up).
3. **Phase 2 (pyte core + MCP)** — only if LLM-driving interface-poor devices is a real
   goal. High leverage because the core has three consumers.

`feat/vt100-passthrough` for Phase 1. Phases are independent branches.

---

## 8. Open decisions (confirm at kickoff, before coding)

1. **Scope:** Phase 1 only, or 1 + 2? (Recommend 1 first.) Include Phase 0?
2. **Exit key:** quit the process (miniterm default Ctrl-]) vs **return to the termapy
   TUI** (nicer toggle). The mode loop (`entry.py:405-420`) already supports this —
   `run_vt100_mode` returns `"tui"` to switch back — so it's cheap; decide if it's wanted.
3. **Log tee in passthrough:** yes/no; raw vs ANSI-stripped.
4. **pyte packaging (Phase 2):** `[term]` extra (recommended) vs normal dep.
5. **Config activation = a new *value*, not a new key.** `default_ui` already exists
   (`"tui"`/`"cli"`); adding `"vt100"` needs no new key — just widen validation at
   `entry.py:402` and document the new value in the README config table, `help/config.md`,
   and the `demo.cfg` comment. `--vt100` is the override flag (peer of `--cli`).

---

## 9. Code anchors (verify line numbers — code drifts)

- Mode selection (peer of cli/tui) + flag defs: `src/termapy/entry.py` (mode loop &
  `default_ui` resolution ~378-420; `--cli` override ~378; valid-set check ~402; flag defs
  ~75/99/214).
- Vendored terminal: `src/termapy/vendor/serial/tools/miniterm.py` (`Miniterm` 388 / ctor
  394; `Console`/VT-enable 43 / 140-150).
- Config resolution to mirror: `src/termapy/cli.py:973` (`_run_cli_mode`).
- Port open to reuse: `src/termapy/serial_engine.py:331` (`connect` → `self._open_fn(cfg)`);
  open fn in `src/termapy/port_control.py`.
- Line renderer / `\r` strip (Phase 0 + viewer feed): `src/termapy/serial_port.py:297-319`
  (split 304-305; `CLEAR_SCREEN_RE` 164); display path `src/termapy/app.py:2685-2755`,
  RichLog `:829`, `Text.from_ansi` `:2740-2742`.
- Toggle pattern (if a `/term.screen` toggle is added): `src/termapy/builtins/commands/term.py:31-45`.

---

## 10. Project rules (apply throughout)

- Branch first; **never edit main directly**. `--no-ff` merges.
- Full test suite before merging (`uv run pytest`); `ruff` and `ty` must be **0**
  (`uv run ruff check src/termapy/ tests/`, `uvx ty check src/termapy/` — install
  `--all-extras` so MCP/jsonschema-gated tests run and ty resolves imports).
- Update README / `help/*.md` (+ `index.md` + `mkdocs.yml` nav) / `ARCHITECTURE.md`.
- Args convention: `""` none, `{braces}` optional, `<angle>` required.
- Don't rebuild HTML help in feature commits.

---

## 11. Fresh-context kickoff prompt (copy-paste into a new session)

> We're implementing VT100/terminal support in termapy. **Read `VT100_BUILD_PLAN.md` first**
> — it has the locked design decisions, the phased build, and code anchors.
>
> Locked context (don't re-litigate): hard constraint is win/mac/linux; **do not build
> interactive VT100 inside the Textual TUI** (key-interception stacking); humans → `--vt100`
> passthrough leaning on the already-vendored `miniterm`; LLMs → headless pyte core + MCP.
>
> Start with **Phase 1** (`--vt100` passthrough). Before writing code: (1) confirm the
> §8 Open Decisions with me — especially the exit-key behavior and whether to include
> Phase 0; (2) read the §9 code anchors; (3) propose the `vt100.py` module skeleton plus the
> `entry.py` flag + dispatch diff, and wait for my OK.
>
> Follow project rules: branch `feat/vt100-passthrough` (never edit main), `--no-ff` merge,
> full `pytest` + `ruff` + `ty` (0, with `--all-extras`) before merge, update
> README/help/ARCHITECTURE.
