# Termapy review series

Dated adversarial fitness-of-use reviews. Each pass is a point-in-time,
evidence-graded assessment (strengths and defects; killer problems never
minimized) that a reader can use to judge whether termapy is fit for real
serial-debugging work.

## Passes

| Date | Version | Commit | Reviewer(s) | Verdict (one line) |
|---|---|---|---|---|
| [2026-08-19](2026-08-19-v0.74.0-opus-5.md) | 0.74.0 | `1ee85bb` | Claude Opus 5 + 6 finders + 19 verifiers | Threading pass: design sound (non-blocking dispatch lock deadlock-free, RX batching well-tuned, no reader/transfer port contention); 3 mechanism gaps — unowned shared handles, teardown-by-timeout instead of join, global cancellation tokens. 1 reproduced data-integrity bug (stale reader closes the next connection's port); 2 shutdown hangs; documented threading model is inverted. |
| [2026-07-14](2026-07-14-v0.72.0-opus-4.8.md) | 0.72.0 | `6dc5d72` | Claude Opus 4.8 + 3 Explore agents | Code-quality pass: core invariants intact (no layering leaks, value= contract 100%); debts localized — app.py size, /proto usage-string drift, ~12 helper-extraction duplications, 25/31 hardcoded prefixes; 2 minor bugs ({{filename}} synopsis, history-fallback divergence). Root cause: missing single owners; remediation is mechanism-first. |
| [2026-07-03](2026-07-03-v0.71.1-fable-5.md) | 0.71.1 | `cc58a45` | Claude Fable 5 (finders) + orchestrator | Real tool, strong engine; **partial pass** — 1 killer (invasive port probe can reset boards) + parity/CI gaps to fix before "goto tool" claims. |
| [2026-06-20](2026-06-20-v0.71.1-opus-4.8.md) | 0.71.1 | — | Claude Opus 4.8 (5 agents) | Remediation tracker: top-10 findings, most since fixed; open items 7 + A–F. |

## Conventions

- **Filename:** `YYYY-MM-DD-vX.Y.Z-<model>.md` — date first (sorts chronologically), then the version reviewed, then the primary reviewer model. Time-of-day may be appended (`YYYY-MM-DD-HHMM-...`) if more than one pass lands on a day.
- **Append-only + remediation log:** a review's *body* is **frozen once merged** — it is the point-in-time assessment and must not be edited. Progress is recorded in the trailing `## Addenda` section instead: a running remediation log of dated entries (`R…`/`K…` finding id → FIXED on `main` / released, with the commit). **Fold each addendum entry into the same commit/branch as the fix**, so fixing a finding and recording it land together. Errata go here too.
- **Grading:** every claim carries a grade — **Verified** (reproduced or exact-path read by the orchestrator), **Reported** (agent-cited, plausible, not independently re-verified), or **Assessment** (judgment / external comparison, basis stated). The fitness verdict keys off Verified findings.
- **Credibility metadata:** each file's header records the date, version, commit SHA, host/hardware, the model(s) used, the method, and — for honesty — whether the pass was complete and whether the verification stage ran.
- **Not published:** these are internal engineering records and are intentionally **not** in the mkdocs/site nav. Publishing one externally is a per-file decision.
