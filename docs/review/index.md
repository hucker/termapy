# Termapy review series

Dated adversarial fitness-of-use reviews. Each pass is a point-in-time,
evidence-graded assessment (strengths and defects; killer problems never
minimized) that a reader can use to judge whether termapy is fit for real
serial-debugging work.

## Convergence

The question the series exists to answer is whether termapy is converging:
whether each pass finds less that is severe, or whether a stronger reader
keeps drawing from a deep pool. The table below tracks the one number that
answers it. **The series is converging when severe findings on first-pass
areas trend to zero, repeat-area passes find only minors, and no rule in
[`docs/rules.md`](../rules.md) flips from guarantee back to
assumption.** A killer in a repeat area, or a flipped guarantee, is the signal
to stop adding tests and re-plan. Severity is in each file's own vocabulary
(killer, High, major); "first pass" means no earlier review had that area as
its scope, whatever it touched in passing.

## Passes

| Date | Version | Commit | Reviewer(s) | Verdict (one line) | Severe | Total | First pass |
|---|---|---|---|---|---|---|---|
| [2026-10-14](2026-10-14-v0.76.0-opus-5.5.md) | 0.76.0 | `3b9b082` | Claude Opus 5.5, solo | Serial data path pass, scoped by data contract rather than mechanism: the 08-19 threading fixes hold, the data semantics did not -- 3 reproduced without hardware (a second `/expect` passed on the first reply, a match dropped the lines that came with it, a passive session kept every byte it received), log/capture/expect lines dropped when the TUI fell behind, MCP `/cap.text` recorded nothing, `/term.hex` showed re-encoded text, `/raw` had two behaviors. All fixed the same day. | 2 High, 7 major | 13 | no (08-19 covered the reader as a threading mechanism) |
| [2026-10-10](2026-10-10-v0.76.0-fable-5.1.md) | 0.76.0 | `4e7cb80` | Claude Fable 5.1 + 3 finders, every finding orchestrator-verified | Protocol + capture pass (first on its own): the format-spec / match / CRC-order / bit-model core is sound; the structured-capture path is not -- 2 killers reproduced without hardware (every `fmt=` capture past one 4 KB flush is misframed and never auto-stops; TUI `echo=on` blocks the reader on main per line), 2 majors on claim/success reporting, 4 majors in the spec consumers (byte ref 0 = last byte, CRC diff never computes, short data zero-pads, `/proto.run` uninterruptible), `json_file` documented for a command that never writes it. | 2 killer, 7 major | 23 | yes (the same code was touched in passing by 06-20 #1/#4/#5/F and 08-19 T9/T17) |
| [2026-09-18](2026-09-18-v0.76.0-fable-5.1.md) | 0.76.0 | `8842f37` | Claude Fable 5.1, solo | Device-memory pass (`/mem.*`, symbols, device files; partial by design): gate design sound and the read side holds; 2 reproduced gaps on the write side -- `/mem.write`'s audit pre-read bypasses the write-only gate and the read log (pops a FIFO, reads a wo register), and width-differing SVD mode aliases still trip the bulk gate on a bare single-register dump (7.6% of E54 registers, 5.6% of PIC32CM). | 1 High | 12 | yes |
| [2026-08-19](2026-08-19-v0.74.0-opus-5.md) | 0.74.0 | `1ee85bb` | Claude Opus 5 + 6 finders + 19 verifiers | Threading pass: design sound (non-blocking dispatch lock deadlock-free, RX batching well-tuned, no reader/transfer port contention); 3 mechanism gaps — unowned shared handles, teardown-by-timeout instead of join, global cancellation tokens. 1 reproduced data-integrity bug (stale reader closes the next connection's port); 2 shutdown hangs; documented threading model is inverted. | 5 High (+1 in remediation) | 22 tabled of 41 raised | no (teardown re-raised from 06-20 #7 and 07-03 R22) |
| [2026-07-14](2026-07-14-v0.72.0-opus-4.8.md) | 0.72.0 | `6dc5d72` | Claude Opus 4.8 + 3 Explore agents | Code-quality pass: core invariants intact (no layering leaks, value= contract 100%); debts localized — app.py size, /proto usage-string drift, ~12 helper-extraction duplications, 25/31 hardcoded prefixes; 2 minor bugs ({{filename}} synopsis, history-fallback divergence). Root cause: missing single owners; remediation is mechanism-first. | 0 (3 minor bugs) | 28 | no (DRY and architecture were 06-20 axes) |
| [2026-07-03](2026-07-03-v0.71.1-fable-5.md) | 0.71.1 | `cc58a45` | Claude Fable 5 (finders) + orchestrator | Real tool, strong engine; **partial pass** — 1 killer (invasive port probe can reset boards) + parity/CI gaps to fix before "goto tool" claims. | 1 killer, 5 High | 22 | yes, except R22 (06-20 #7 again) |
| [2026-06-20](2026-06-20-v0.71.1-opus-4.8.md) | 0.71.1 | — | Claude Opus 4.8 (5 agents) | Remediation tracker: top-10 findings, most since fixed; open items 7 + A–F. | 3 High (+1 in remediation) | 16 (+2 in remediation) | yes (first in the series) |

## Remainder

What has had neither a dedicated pass nor behavioral coverage, measured on
`main` at `8148715` on 2026-10-12 with the path-source coverage run (the
package-name form the suite used until then never measured 26 of the 40
command modules). This list, not a feeling, is the pool a stronger reader
would keep drawing from; it shrinks by a pass or a test file at a time.

| Area | Statements | Uncovered | Covered | Dedicated pass | Note |
|---|---|---|---|---|---|
| `cli.py`, `entry.py`, `cli_flags.py` | 854 | 423 | 50% | none | plain calls, no Pilot needed; the headless `--proto` entry got its first tests 2026-10-11 |
| `/proto.*` command module | 988 | 453 | 54% | 10-10 for `/proto.run`; the `/proto.crc.*` half deliberately out of scope | crcglot-backed; `tests/test_proto_crc_*` cover the verbs behaviorally; the non-CRC half (the connected `/proto.send`, the flat runner, `.hex`, `.info`, `.help`) got its first in-process run 2026-10-12 (`tests/test_proto_commands.py`, pending branch) |
| port control + USB enumeration | 1165 | 308 | 74% | 07-03 covered ports in part | `usb_tree.py` 62%, `port_control.py` 75% |
| demo device and its plugins | 1218 | 322 | 74% | none | `builtins/demo/plugin/*` at 0 to 16%; demo content, lowest priority |
| `port`, `edit`, `ping`, `log` commands | 491 | 267 | 46% | none | `/cap.poll` and `/log.fingerprint` got their first tests 2026-10-12 (pending branch); the `/port.*` handlers their first in-process run the same day (`tests/test_port_commands.py`, pending branch) |
| MCP server + profiles | 1245 | 189 | 85% | none as a subsystem; 07-03 covered security | `server.py` 80% |
| help, palette, info views | 633 | 171 | 73% | none | `info_views.py` has Pilot tests on a pending branch |
| `app_hooks.py` | 217 | 135 | 38% | 08-19 touched the script hooks | plain calls on a booted app |
| file transfer (xmodem, ymodem) | 216 | 133 | 38% | 08-19 verified no port contention | needs `loop://` or the hardware loopback suite |
| `vt100.py` | 84 | 24 | 71% | none | own design docs under `docs/` |
| symbols converters (map, SVD, regs) | 860 | 34 | 96% | 09-18 for `/mem.*`; the SVD converter not swept | coverage is high; a pass is about correctness against real packs |

Not in the table because they have both: the serial engine and reader
(08-19), the format-spec language and capture engine (10-10), the memory and
symbol commands (09-18), the dialogs and `app.py` (Pilot tests, pending
branches).

### Carried from earlier passes

Triaged 2026-10-12 (each file's Addenda has the disposition of every open
item); what was neither fixed nor closed lands here so a later pass finds it
listed rather than discovering it.

- **Worth a fix soon:** none. The three listed here on 2026-10-12 (06-20 F,
  E and #4) were fixed 2026-10-14; see that file's Addenda.
- **Debt, known and bounded:** quoted folder names joined to paths outside
  `folders.py` (06-20 C, ~26 sites); inline newline splits beside
  `scripting.split_cmd_lines` (06-20 D); `config_editor.py` importing
  `repl._edit_distance` (06-20 B); the duplicated `.pro` summary block (07-14
  R3); the reported duplications R5–R12 (07-14, unverified); `ctx.internal`
  outranking two stable handles (07-14 A2); the extractable script-runner
  cluster (A3); the lazy mutual import between `serial_engine` and
  `port_control` (A4); hook/plugin boundary drift (A5); the six stale
  What-comments in `plugins/capabilities.py` (A6); `repl.py`'s one pyserial
  import (A8).
- **Leads, never verified (08-19):** auto-reconnect giving up for good after a
  failed connect (`app.py`, `_reconnecting`); the reconnect worker surviving a
  manual connect and a config switch (its `stop_event` doubling as the
  reader's was confirmed and fixed 2026-10-14); a stale `_expect_event.set()`
  satisfying the next `wait_for_match`;
  config and port-effect callbacks mutating widgets on a worker thread; a
  trailing partial line dropped on disconnect (`serial_engine.py`); the help
  HTTP server never shut down. These are the brief for the first dedicated pass
  over `app.py`'s connection lifecycle.

## Conventions

- **Filename:** `YYYY-MM-DD-vX.Y.Z-<model>.md` — date first (sorts chronologically), then the version reviewed, then the primary reviewer model. Time-of-day may be appended (`YYYY-MM-DD-HHMM-...`) if more than one pass lands on a day.
- **Append-only + remediation log:** a review's *body* is **frozen once merged** — it is the point-in-time assessment and must not be edited. Progress is recorded in the trailing `## Addenda` section instead: a running remediation log of dated entries (`R…`/`K…` finding id → FIXED on `main` / released, with the commit). **Fold each addendum entry into the same commit/branch as the fix**, so fixing a finding and recording it land together. Errata go here too.
- **Rules:** a pass lists the registry ids it read under "Rules checked and holding" and updates the last-review-check column in [`docs/rules.md`](../rules.md); one found broken flips to assumption in the same Addenda entry as the fix, and back when the fix lands with a guard.
- **Grading:** every claim carries a grade — **Verified** (reproduced or exact-path read by the orchestrator), **Reported** (agent-cited, plausible, not independently re-verified), or **Assessment** (judgment / external comparison, basis stated). The fitness verdict keys off Verified findings.
- **Credibility metadata:** each file's header records the date, version, commit SHA, host/hardware, the model(s) used, the method, and — for honesty — whether the pass was complete and whether the verification stage ran.
- **Not published:** these are internal engineering records and are intentionally **not** in the mkdocs/site nav. Publishing one externally is a per-file decision.
