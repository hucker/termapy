# Termapy review series

Dated adversarial fitness-of-use reviews. Each pass is a point-in-time,
evidence-graded assessment (strengths and defects; killer problems never
minimized) that a reader can use to judge whether termapy is fit for real
serial-debugging work.

## Passes

| Date | Version | Commit | Reviewer(s) | Verdict (one line) |
|---|---|---|---|---|
| [2026-07-03](2026-07-03-v0.71.1-fable-5.md) | 0.71.1 | `cc58a45` | Claude Fable 5 (finders) + orchestrator | Real tool, strong engine; **partial pass** — 1 killer (invasive port probe can reset boards) + parity/CI gaps to fix before "goto tool" claims. |
| [2026-06-20](2026-06-20-v0.71.1-opus-4.8.md) | 0.71.1 | — | Claude Opus 4.8 (5 agents) | Remediation tracker: top-10 findings, most since fixed; open items 7 + A–F. |

## Conventions

- **Filename:** `YYYY-MM-DD-vX.Y.Z-<model>.md` — date first (sorts chronologically), then the version reviewed, then the primary reviewer model. Time-of-day may be appended (`YYYY-MM-DD-HHMM-...`) if more than one pass lands on a day.
- **Append-only:** a review file is **frozen once merged**. It is a point-in-time record, not a living document. Later changes (a finding fixed in a later release, an erratum) go under a trailing `## Addenda` section as dated entries — never by editing the body.
- **Grading:** every claim carries a grade — **Verified** (reproduced or exact-path read by the orchestrator), **Reported** (agent-cited, plausible, not independently re-verified), or **Assessment** (judgment / external comparison, basis stated). The fitness verdict keys off Verified findings.
- **Credibility metadata:** each file's header records the date, version, commit SHA, host/hardware, the model(s) used, the method, and — for honesty — whether the pass was complete and whether the verification stage ran.
- **Not published:** these are internal engineering records and are intentionally **not** in the mkdocs/site nav. Publishing one externally is a per-file decision.
