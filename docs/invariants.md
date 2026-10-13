# Invariants

The rules the code relies on, one row each, in one place. Until 2026-10-12 they
lived in four: the guards in `tests/test_architecture.py`, the prose bullets in
`CLAUDE.md`, each review's "invariants checked and holding" list, and the
maintainer's head. This table is the registry; those are the pointers.

**Reading a row.** `id` is stable and kebab-case, the hook a test header leads
with (`"""ui-layer-textual: ..."""`) and a review cites. `kind` is the column
the table exists for: a **guarantee** is enforced by the named test, so a
violation anywhere fails `pytest`; an **assumption** holds because someone read
the code, and stays true only until someone does not. `enforced by` names the
test function or test file for a guarantee, `review` for an assumption, with
instance tests (evidence for some commands, not the rule) noted after a
semicolon. `source` is where the rule is stated. `last review check` is the
most recent adversarial pass that read it at the path; guarantees are checked
on every run, so the date there is the last human confirmation only.

**Keeping it honest.** `tests/test_architecture.py::test_invariants_registry_matches_the_guards`
reads this table and fails when a row names a test that does not exist, a
guarantee names no test, or a guard in that file lacks a registered id in its
header. A new guard therefore cannot ship unlisted, and a renamed test cannot
leave a row pointing at nothing. A change that relies on an invariant names its
id; a new invariant gets a row the same day, an assumption until its guard
exists; a review that finds one broken flips it to assumption until the fix
lands and says so in its Addenda.

| id | statement | kind | enforced by | source | last review check |
|---|---|---|---|---|---|
| `core-imports-published-only` | Core imports from `builtins/` only a plugin's published surface, never a private handler or private state. | guarantee | `test_core_does_not_import_from_plugins` | CLAUDE.md Architecture | 2026-07-14 |
| `io-semantic-channels` | A handler reaches the terminal through the semantic channels (`result`, `output`, `status`), never `_write` or `_write_markup`, aliased or not. | guarantee | `test_plugins_do_not_use_engine_io_primitives` | CLAUDE.md Architecture | 2026-07-14 |
| `ui-layer-textual` | Only the listed UI-layer modules import Textual; `termapy.entry`, `cli_flags` and `port_format` load none, transitively included. | guarantee | `test_textual_stays_in_the_ui_layer`, `test_entry_import_does_not_load_textual` | CLAUDE.md Architecture | 2026-07-14 |
| `engine-transport-agnostic` | `plugins/`, `scripting.py`, `symbols/` and `memory.py` import no serial transport. | guarantee | `test_engine_modules_have_no_serial_dependency` | CLAUDE.md Architecture | 2026-07-14 |
| `bool-one-vocabulary` | Wherever termapy turns user text into a boolean, in any frontend or file, the text is read by `scripting.parse_bool`, directly or through `parse_bool_setting` or `ParamSpec(type="bool")`. Absence yields the default the reader declares; a reader with no declared default fails loud. A present but unrecognized token always errors, never silently False or the default. A file format's own boolean is type-checked at load, not re-read from text. A device's on/off grammar belongs to the device, and a confirmation prompt tests for yes rather than parsing a boolean. | guarantee | `test_booleans_go_through_parse_bool` (no second vocabulary anywhere under `src/termapy`), `test_unrecognized_raises`, `test_absence_without_a_default_raises` (the reader's contract) | CLAUDE.md Conventions | 2026-10-10 |
| `folders-ensure-only` | A data folder is created only by `ensure_folder` at a write and removed by `prune_empty_folders` at `on_app_start`, `on_config_load` and `on_app_stop`; a raw `mkdir` on one is a bug. | guarantee | `test_data_folders_are_created_only_by_ensure_folder`, `test_removes_only_empty_folders_named_in_folders` | CLAUDE.md Architecture | never |
| `symbols-one-owner` | Only `symbols/session.py` writes `ctx.ns("symbols")`; readers go through `get_table`, `get_devices` and `get_build_table`. | guarantee | `test_symbols_namespace_is_touched_only_by_its_session_module` | CLAUDE.md Config | 2026-09-18 |
| `test-headers` | Every collected test opens with a one-line docstring header saying what it pins. | guarantee | `test_every_test_has_a_header` | CLAUDE.md Testing | never |
| `invariants-registry` | This table and the guards in `tests/test_architecture.py` agree both ways: a row's tests exist, a guarantee names one, and every guard carries a registered id. | guarantee | `test_invariants_registry_matches_the_guards` | this file | never |
| `command-has-test` | Every registered command is named by at least one test file, a family generated from one handler counting once; the known-untested set is an allowlist that only shrinks, so a new command ships with its test. | guarantee | `test_every_command_is_mentioned_by_a_test` | CLAUDE.md Testing | never |
| `script-single-owner` | One outermost script at a time, enforced in `ReplEngine.start_script` for every frontend: a nested `/run` on the owning thread is allowed, a launch from any other thread is refused. | guarantee | `test_a_second_launch_from_another_thread_is_refused`, `test_ownership_is_released_when_the_run_ends` | CLAUDE.md Threading | 2026-08-19 |
| `dispatch-guard-main-never-parks` | `_dispatch_guard` is non-blocking on the main thread and a bounded wait off it; main never parks on a lock a worker holds. | guarantee | `test_the_main_thread_is_refused_immediately`, `test_a_worker_waits_for_the_guard_instead_of_dropping_the_line` | CLAUDE.md Threading | 2026-08-19 |
| `rx-never-blocks` | No `read_loop` callback blocks on another thread; RX reaches the UI through `_rx_enqueue` (an append plus at most one `post_message`), the capture echo included. | guarantee | `test_enqueue_never_blocks_and_posts_one_wakeup`, `test_capture_echo_rides_the_rx_queue` | CLAUDE.md Threading; review 2026-08-19 | 2026-10-10 |
| `reader-owned-by-engine` | `SerialEngine` owns the reader thread; disconnect is a real join and leaves no reader behind. | guarantee | `test_disconnect_leaves_no_reader_thread_behind` | CLAUDE.md Threading | 2026-08-19 |
| `capture-owns-claim` | A capture holds the serial claim from its start to its own stop, in every frontend; a script's end neither releases nor extends it. | guarantee | `test_scripted_stop_releases_the_claim`, `test_claim_outlives_the_script_while_the_capture_runs` | review 2026-10-10 (C3) | 2026-10-10 |
| `confirm-off-main` | `_confirm` blocks on an event and runs only off the main thread; a script stop releases a waiting confirm. | guarantee | `test_a_script_stop_releases_a_waiting_confirm` | CLAUDE.md Threading | 2026-08-19 |
| `output-helpers-marshal` | `_status`, `_set_status_bar`, `_write_output_markup` and `_on_main` forward themselves to the main thread when called off it. | guarantee | `test_status_from_a_worker_reaches_the_screen`, `test_find_bar_updates_are_marshalled` | CLAUDE.md Threading | 2026-08-19 |
| `synopsis-grammar` | A registered synopsis obeys the grammar (braces and angles, no spaces inside a group, balanced) and fails at registration otherwise. | guarantee | `test_synopsis_validation.py` | CLAUDE.md Conventions; review 2026-07-14 (K2) | 2026-10-10 |
| `credits-single-source` | Every runtime, optional and vendored dependency has a `Credit` record and every record is a dependency; the three surfaces iterate the one table. | guarantee | `test_credits_data.py` | CLAUDE.md Precommit | never |
| `format-type-table-single-source` | `TYPE_CODES` is the one source for the parser's code sets and the generated README and help type tables. | guarantee | `test_format_tables_sync.py` | CLAUDE.md Precommit | 2026-10-10 |
| `python-support-declared-once` | The classifiers, the tox envlist, the CI matrix and the badge agree on the supported Python versions. | guarantee | `test_python_versions.py` | release gate | never |
| `register-spec-word-form` | A word-relative `B.<bits>` field is accepted only with `word=True`; a packet spec refuses it at parse, where the user typed it. | guarantee | `test_protocol_word_fields.py` | review 2026-10-09 | 2026-10-10 |
| `byte-refs-one-based` | A format-spec byte reference is numbered from 1: `0` is refused at parse in every form (single, either range end, wildcard prefix, bit field), so no reader or inject ever reaches Python's index -1. | guarantee | `test_byte_index_zero_is_refused` | review 2026-10-10 (P1) | 2026-10-10 |
| `crc-column-verifies` | A CRC column is verified, not byte-compared: the algorithm runs over the data range and the result is checked against the CRC the packet carries; a column whose indices reach past the data renders `?` and diffs `missing`. | guarantee | `test_valid_crc_against_a_wildcarded_expect_matches`, `test_invalid_crc_against_its_own_literal_mismatches`, `test_crc_on_data_shorter_than_its_width` | review 2026-10-10 (P2, P3) | 2026-10-10 |
| `hex-capture-refuses-non-hex` | A hex-mode capture aborts on a line that is not hex, naming the line, and nothing after it reaches the file; a dropped line would misframe every record behind it. | guarantee | `test_a_non_hex_line_aborts_the_capture` | review 2026-10-10 (C5) | 2026-10-10 |
| `script-overlay-owns-bar` | While a script runs the script overlay owns the bottom bar: the started handler enables the bar it mounts into so Stop is clickable, the typed path's busy greying leaves an owned bar alone, a scripted capture mounts no overlay of its own, and the finished handler hands the bar and focus back. | guarantee | `test_a_typed_run_mounts_the_overlay_and_restores_the_bar`, `test_the_stop_button_ends_a_script`, `test_a_scripted_capture_leaves_the_bar_to_the_script` | review 2026-06-20; Pilot 2026-10-12 | 2026-06-20 |
| `commands-off-main` | Every command runs off the main thread, inline on the calling worker; dispatch hands nothing to main. | assumption | review | CLAUDE.md Threading | 2026-08-19 |
| `plugin-never-clears-stop` | A plugin never `clear()`s the shared script-stop event; `/repeat` and `/proto.run` may, only when they are the outermost cancellable operation. | assumption | review | CLAUDE.md Threading | 2026-08-19 |
| `blocking-commands-need-sctx` | `_BLOCKING_COMMANDS` holds the commands that need the `ScriptCtx` or must set `_script_stop`, not the commands that block. | assumption | review | CLAUDE.md Threading | never |
| `call-from-thread-wrapped` | Every `call_from_thread` site is wrapped in `try/except RuntimeError` for shutdown. | assumption | review | CLAUDE.md Exception Safety | 2026-08-19 |
| `xfer-no-port-contention` | The reader thread is the sole `port.read()` caller; a transfer drains `rx_queue` under the claim and never reads the port itself. | assumption | review | review 2026-08-19 | 2026-08-19 |
| `value-scriptable` | A handler that produces data returns `CmdResult.ok(value=...)`; a side-effect-only handler returns `value=""`. | assumption | review; instance `test_search_returns_value_for_scripting` | CLAUDE.md Conventions | 2026-07-14 |
| `data-structured-twin` | `CmdResult.data` is a JSON shape built by a core producer, never prose; a large listing branches on `wants_data` and builds one of the two, not both. | assumption | review; instance `test_run_list_wants_data_returns_records_without_prose` | CLAUDE.md Conventions | never |
| `setting-bare-queries` | A setting command never mutates on a bare invocation: bare shows, a value sets, `toggle` or `cycle` flips, an unknown token errors; a TUI button dispatches the explicit verb. | assumption | review; instance `test_output_bare_queries`, `test_bare_reports_default_cr` | CLAUDE.md Conventions | 2026-10-10 |
| `number-vocabularies` | A count is decimal (`ParamSpec` int, or `int()` with an `Invalid <thing>` error); an address, size or mask goes through `symbols.parse_number`; a duration through `parse_duration`; hex through `parse_hex`, `parse_data` or `parse_pattern`. | assumption | review (audit 2026-09-03) | CLAUDE.md Conventions | 2026-10-10 |
| `usage-from-declaration` | Wrong arity raises `UsageError` and the dispatcher renders the usage from the declaration; the two documented holdouts are the only hand-written `Usage:` strings. | assumption | review | CLAUDE.md Error Messages | 2026-10-10 |
| `error-message-style` | A `fail()` message is sentence case, keyword-led when its subject is a keyword, period-terminated for a state, bare for a validation, in the standard phrasings. | assumption | review | CLAUDE.md Error Messages | 2026-10-10 |
| `infra-not-under-builtins` | Anything core needs at startup or on the dispatch path lives in core; a command that exposes it is a thin surface under `builtins/`. | assumption | review (the import guard catches the direction, not the placement) | CLAUDE.md Architecture | never |
| `device-files-not-build` | `dev/*.device.json` hold the board's registers: they merge over the sidecar's symbols at load, survive `/sym.import`, never reach the sidecar, and one installer rebuilds the merged table. | assumption | review | CLAUDE.md Config; review 2026-09-18 | 2026-09-18 |
| `sidecars-listed-once` | Every stem-named sidecar is listed once in `folders.SIDECARS`; `rename_config` carries every entry and `cfg_data_dir` migrates any with a folder out of the root. | assumption | review; instance `test_non_standard_layout_renames_only_the_file` | CLAUDE.md Config | never |
| `demo-cfg-mirrors-defaults` | Every `DEFAULT_CFG` key appears in `builtins/demo/demo.cfg` and in the config reference docs. | assumption | review | CLAUDE.md Config | never |
| `plugin-load-order` | Builtins, then the global folder, then the per-config folder, then app hooks; later overrides earlier, and a folder plugin whose name an app hook owns is skipped with a warning. | assumption | review; instance tests in `test_plugin_resolve.py` | CLAUDE.md Architecture | never |
| `no-crash-to-trace` | A handler runs inside the dispatcher's try/except; nothing reaches the user as a stack trace. | assumption | review | CLAUDE.md Exception Safety | never |
