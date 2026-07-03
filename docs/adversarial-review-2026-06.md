# Adversarial review findings — remediation tracker

Review date: 2026-06-20. Method: 5 parallel review agents (threading/races,
exception handling, DRY, architecture, core-logic correctness) + real-hardware
COM7 loopback test + manual verification of every High/Critical claim.

Baseline at review time: `ruff` 0, `ty` 0, gold test passing. Hardware loopback
(ping echo, binary `/proto.send` round-trip, disconnect/reconnect) all passed.

Severities below are the **verified** severities (some differ from the agents'
original ratings — see notes). "Verified" = I reproduced the bug or read the
exact code path; "Reported" = credible agent finding I did not independently
reproduce.

Status legend: `[ ]` todo `[~]` in progress `[x]` done `[-]` deferred / won't-fix (tracked elsewhere)

---

## Top 10

- [x] **3. Hardcoded CRC algorithm count** — High — *DONE 2026-06-20 (merge 00bbef1)*
  - Files: `proto.py:2254,2001`, `help/serial-tools.md:92,97`
  - Was "64" / live `len(ALGORITHMS)` (113); now "100+ / more than 100".
  - `/proto.crc.list` tally kept dynamic + filter-aware (deliberate).
  - Follow-up: `html/serial-tools.html` still says "64" — fix at next HTML rebuild.

- [x] **1. `parse_hex` silently corrupts malformed input** — High — *DONE 2026-06-20 (merge 2bfe9fb)*
  - File: `src/termapy/protocol/core.py:59-78` (`parse_hex`)
  - Was: `findall` scavenged any 2-hex pair anywhere -> `parse_hex("ABC")`==`b"\xab"`,
    `parse_hex("010 203")`==`b"\x01 "`, no error.
  - Fixed: tokenize on whitespace/comma; each token must be an even-length run of
    hex digits (optional `0x`); else raise `Invalid hex byte: <tok>`. Run-together
    hex (`0103000A`) and `0x`-prefixed forms still parse.
  - Caller impact: only `capture.py:224` (hex mode), already in `except ValueError`,
    so a malformed line is now skipped, not scavenged into wrong bytes.
  - Tests added: `odd_nibble`, `misplaced_space`, `embedded_garbage`, `whitespace_only`.

- [x] **2. Concurrent interactive dispatch corrupts shared `ctx`** — High — *DONE 2026-06-20 (merge 5fdb9e1); manually verified in TUI (input+buttons grey, 2nd command blocked)*
  - Files: `app.py` (`_dispatch_single`, `_dispatch_quiet`, `on_input_submitted`,
    `_set_input_busy`); root cause was non-exclusive workers + main-thread
    `_dispatch_quiet` racing the save/restore in `repl.py:1108-1124,1251-1292`.
  - Scope check: **TUI-only.** MCP already serializes run_command via an
    `asyncio.Lock` (`mcp/server.py` :90/:134/:599); CLI is synchronous. The
    user's MCP "two threads own the port" worry does not apply.
  - Approach taken (differs from the original note below): `exclusive=True` does
    NOT work -- Textual can't kill a running Python thread, so a "cancelled"
    worker keeps mutating `ctx`. Instead:
    1. Non-blocking re-entrant `threading.RLock` (`_dispatch_guard`) around the
       `dispatch_full` call in BOTH `_dispatch_single` and `_dispatch_quiet`
       (the single choke point all command paths share). Concurrent dispatch
       from another thread -> "Busy - a command is still running." Non-blocking
       so the main thread never deadlocks a worker waiting on call_from_thread;
       re-entrant so nested ctx.dispatch on the same thread passes through.
    2. UX: `on_input_submitted` greys `#bottom-bar` (input + buttons) for the
       typed-command path; re-enabled in the worker finally. Skipped when a
       script overlay owns the bar (keeps Stop clickable).
  - Verified: headless Pilot run (concurrent dispatch refused, free dispatch not;
    bottom-bar disable toggles), ruff/ty 0, full suite 2602 passed.
  - Manual test still owed before merge: real TUI -- double-Enter during a slow
    command, palette during a command, transfer + Escape still cancels, script
    Stop still works.

- [-] **4. Capture text byte-count uses `len(str)`, not encoded bytes** — Low (was Med) — *DEFERRED -> [hucker/termapy#25](https://github.com/hucker/termapy/issues/25)*
  - File: `src/termapy/capture.py:240-250` (`feed_text`): `self._bytes += len(text)+1`
  - Cosmetic only: the captured file is correct; just the reported `byte_count` /
    "~N KB" readout undercounts (non-ASCII chars + the un-counted `\r` on Windows).
  - Re-rated Low after review: text-mode capture stops on a TIMER and its progress
    bar is TIME-based, so `_bytes` is informational, not a stop trigger. Bin/struct
    captures count real bytes correctly. No data loss, no wrong stop.
  - Fix when convenient (see issue): `len((text+"\n").encode("utf-8"))`, or
    `newline=""` on open for exact on-disk size.

- [x] **5. Capture write failure swallowed -> stuck capture + claimed port** — Med — *DONE 2026-06-20 (merge 34c2c89); engine verified by unit tests. TUI "aborted" message gated behind #26.*
  - File: `src/termapy/capture.py` (`feed_text`/`_flush_bin`/`feed_bytes`/`stop`).
  - Fixed: CaptureEngine records the write error; `feed_bytes`/`feed_text` return
    True to signal stop; the existing on_capture_done path stops the capture and
    releases the serial claim. `CaptureResult.error` carries the message; host
    stop paths show "Capture aborted: <error>" instead of "complete".
  - Tests: text + binary write-failure and clean-capture regression tests pass;
    updated a capture-stop test mock for the new `error` field.
  - NOTE: surfacing the message in the **TUI** is gated behind a separate
    pre-existing bug found while verifying this (see #26 below). The engine fix
    is frontend-agnostic and the claim release rides the proven target-reached
    plumbing, so the core robustness win lands regardless.

- [x] **6. `custom_buttons` backfill aliases module-global `DEFAULT_CFG`** — Med — *DONE 2026-06-20 (merge 1506bc5)*
  - File: `src/termapy/config.py` (`load_config` backfill).
  - Fixed: backfill now `copy.deepcopy()`s both top-level and nested-dict defaults,
    so a config missing a mutable default (e.g. `custom_buttons`) gets its own copy
    instead of aliasing the module-global; in-session edits can't corrupt it.
  - Test added: `test_backfilled_mutable_default_is_deep_copied` (distinct object +
    mutation doesn't change `DEFAULT_CFG`). Verified empirically too.

- [ ] **7. Reader teardown uses bounded timed-wait, not a join** — Med — *Verified (downgraded from agent "Critical")*
  - Files: `serial_engine.py:379-392` (`disconnect`, `reader_stopped.wait(0.3)`),
    `app.py:1255-1259` (`_connect`, `wait(0.3)`); both `disconnect()` and the
    reader `finally` close/null the shared `_port_obj`.
  - Why downgraded: real ports open `timeout=0.05` (config.py:697), so `read()`
    can't block past 50ms << 300ms wait. Safe on the normal path (clean hardware
    reconnect confirmed). Latent: breaks if the read timeout is raised or the OS
    stalls a read.
  - Fix: replace the timed waits with a real reader-thread join (track the worker
    handle); have only one owner close/null `_port_obj`; or use a generation
    counter so a stale reader's `finally` can't touch a newer port.

- [x] **8. `ctx.serial.port()` leaks raw pyserial through the "stable façade"** — Med — *DONE (merge 9d81f92); raw accessor moved to ctx.internal. Verified on COM7.*
  - Approach chosen: **moved** the raw accessor off `ctx.serial` onto
    `ctx.internal.port()` (the explicitly-unstable, built-ins-only escape hatch) --
    matches the project's own "one-off privileged needs live on ctx.internal"
    philosophy, rather than the agent's suggested typed-methods route (which would
    clutter the clean handle). `ctx.serial` no longer exposes pyserial.
  - Touched: `serial.py`/`internal.py` (handle defs), `terminal_host.py` (wiring),
    call sites in `port.py`/`log.py`/`mcp/catalog.py`, the getattr lookups in
    `help_dynamic.py` (new `_internal_attr`; caught by the gold test), + docs.
  - Verified on real COM7 hardware (command handlers + `/port` help "Connected:"
    line) and full suite (2606 passed); ruff/ty clean.

- [-] **9. DRY: duplicated frontend/dialog logic** — Low (was Med) — *SKIPPED after assessment 2026-06-20: low/negative ROI*
  - **Pickers (`FilePickerBase`): not worth it.** The 3 pickers diverge on ~8 axes
    (data source `current_path:str` vs `scripts_dir:Path`, id-prefix, primary verb
    load/run/debug, every tooltip's wording, proto's extra Debug button). Truly
    identical code is only ~30 lines (`_selected_path`/`on_key`/`action_dismiss`);
    a base would need ~8 params and subclasses still carry their CSS/compose/
    tooltips. Net ~a wash on LOC, worse on readability. Don't.
  - **The real (small) wins, deferred as low-value nits:**
    - `split_cmd_lines(raw)` pure helper for `raw.replace("\\n","\n").split("\n")`
      -- the only shareable part of connect-cmd (dispatch differs per frontend);
      same as item D below, 7 call sites total.
    - `resolved_port_name(engine, fallback="")` for the 6 `getattr(port_obj,"port",
      <fallback>) or <fallback>` copies (fallback differs each site).
  - Pure DRY nits, no correctness impact -> not pursuing now.

- [x] **10. `_auto_reconnect` has no reentrancy guard** — Low — *DONE 2026-06-22 (merge e1d6754)*
  - Fixed: split into a main-thread `_auto_reconnect` (checks+sets `_reconnecting`
    before spawning -- race-free since both spawn sites run on main) plus the
    `@work _run_reconnect` worker (clears the flag in finally). A guard *inside*
    the worker would have been too late (TOCTOU, same class as bug 2).
  - Verified via headless Pilot (second concurrent call blocked); full suite 2606.

---

## Discovered during remediation (not in the original 10)

- [x] **TUI file capture broken** — High — *DONE 2026-06-20 (merge 36af818); manually verified in demo TUI. Closes [#26](https://github.com/hucker/termapy/issues/26) on push.*
  - Found while verifying bug 5. `/cap.*` in the TUI failed immediately with
    `Plugin error: no running event loop`.
  - Two layers, both fixed: (1) the `/cap` command runs on the `@work` dispatch
    thread, but `_cap_start`/`_cap_stop` schedule Textual timers + mount the
    overlay (main-thread-only) -> now marshalled via `_on_main`; (2) the progress
    helpers `_cap_show_progress`/`_cap_hide_progress`/`_cap_update_progress` were
    module functions called as `app.<name>()` -> now called as module functions.
  - Was pre-existing on main (regression from the dispatch-threading +
    extract-capture-view refactors); not from any review branch.
  - No suite test added: project convention is "UI tested manually" and prior
    UI/smoke tests were deliberately removed. Verified via headless Pilot
    throwaway (`c:/tmp/tui_cap_test.py`) + manual demo. Pilot smoke test noted in
    #26 as a follow-up if UI-test infra is ever adopted.

- [ ] **CI Tests red on main** — Infra — *FILED -> [hucker/termapy#27](https://github.com/hucker/termapy/issues/27)*
  - Surfaced when pushing this work: the `Tests` workflow on main has been failing
    since ~2026-06-06 (pre-existing, not from these fixes). Two causes:
    (1) **io-channel-guard** -- `ctx.io._write` in `builtins/log.py` + `run.py`;
    (2) **Linux platform tests** -- `test_cfg_icon::TestWindowsCreate` +
    `test_cli::TestHookRunProfile` pass on Windows but fail on ubuntu CI.
  - Lesson (the local != CI gap): a green local Windows `pytest` does NOT mean CI
    is green. CI runs pytest-only (3.11-3.14) + pip-audit + the io-guard grep;
    check `gh run list --workflow Tests` before claiming a push will pass.

---

## Additional lower-priority items (from the agents, worth tracking)

- [ ] **A. `serial_claimed` setter redundant + slightly racy rebind** — Low
  - `serial_engine.py:264-268`: the construction-time `lambda: self._serial_claimed`
    already tracks the field; the setter's `self._reader._serial_claimed = lambda:
    value` rebind is redundant (and the `if self._reader` is a check-then-use).
    Drop the rebind.

- [ ] **B. `proto_debug.py` imports private `_build_test_result`** — Low
  - `proto_debug.py:30` reaches into `protocol/runner.py` internals. Promote it to
    a public function (also `dialogs/config_editor.py:304` imports private
    `repl._edit_distance`).

- [ ] **C. Folder-name literals bypass `folders.py` SSOT** — Low/Med
  - ~15 sites hardcode `"run"`/`"proto"`/`"plugin"`/`"prof"` etc. instead of the
    `folders.py` constants or `ctx.fs.*_dir`. Worst cluster: `var.py:149-155`.

- [ ] **D. `"\\n" -> "\n"` line-split repeated 7×** — Low
  - `app.py:1274,1277,2367`, `cli.py:891`, `mcp/server.py:523`, `cap.py:395`,
    `run_profile_hooks.py:66`. Extract `scripting.split_cmd_lines(raw)`.

- [ ] **E. Redundant `except (OSError, Exception)` tuples** — Low (clarity)
  - `serial_port.py:154` and `config.py:266` lack boundary annotations and hide
    non-serial bugs — narrow to `except OSError`. (`SerialException` subclasses
    `OSError`, so `except OSError` already covers it.)

- [ ] **F. Other small correctness sharp-edges** — Low
  - `core.py:1325-1330` `_format_column_value` fabricates `0x00` for out-of-range
    indices (display path) — return `"?"` instead.
  - `proto.py:1585-1588` `_send_and_capture` strips a trailing bare `\n` that can
    be a CRC payload byte — make the strip mode-gated for binary/reverse.
  - `core.py:1138-1141` `parse_format_spec` raises uncaught `ValueError`/`IndexError`
    on a token missing `:` or with empty type body — used un-guarded by
    `proto_debug.py`.

---

## Confirmed clean (no action — recorded so they aren't re-reviewed)

Import boundaries (pure layers Textual/pyserial-free), capability gate +
`MissingCapability` backstop, `ctx.internal` not leaking to the surface, config
migration chain v1->v23 (no data loss, version-gated/idempotent),
`expand_vars`/`{seqN+}`/nested-run depth (5 frames), CRC endianness,
`call_from_thread` sites uniformly `RuntimeError`-guarded, XMODEM/YMODEM resource
cleanup.
