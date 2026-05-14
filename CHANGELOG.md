# Changelog

## 0.65.0 (2026-05-14)

### Headline: profile-local typed args

Device profiles can now declare a top-level `types` block that
`typed_args[i].type` references by name.  Six kinds in the first cut:
`enum`, `int_range`, `float_range`, `str_length`, `pattern`, and a
`format_spec` stub wired to the protocol-format-spec language for
future binary-arg validation.  Five builtins (`int`, `float`, `bool`,
`hex`, `str`) remain available without declaration; custom names
cannot shadow them.  The MCP dispatcher validates bound `typed_args`
against the active profile's type registry before bytes hit the wire
-- bad values short-circuit to a structured failure naming the arg,
type, value, and command.  The catalog inlines a `type_info` field
per typed_arg so an LLM reading `termapy://commands.json` sees the
full contract without cross-referencing the top-level block.

### V2-only profile path: `/profile.load` absorbs `/include`

`/include` and its companion family (`/include.reload`,
`/include.list`, `/include.dump`, `/include.clear`) are retired.
`/profile.load` grows three new shapes that replace them:

- `/profile.load <path>` -- load from file (existing).
- `/profile.load cmd=<command>` -- fetch a v2 profile JSON from the
  connected device, install as the active profile.  Replaces
  `/include cmd=...`.
- `/profile.load` (no args) -- reload current source (file or cmd,
  whichever was last used).
- New: `/profile.save [<path>]` -- write the active profile to disk
  (default `<cfg_dir>/<cfg_name>.profile.json` so the next connect
  auto-loads it).  Warns when every command has `enabled: false`.
- New: `/profile.unload` -- clear the active profile.

The active profile is now the single source of truth for device
commands; the catalog reads from `active_profile.commands`.  Schema
is `profile_version: const 2` (v1 retired).

**Auto-migration**: cfgs with `auto_include_on_connect: true` AND
`device_json_cmd: <cmd>` get rewritten on first load to set
`mcp_on_connect_cmd: "/profile.load cmd=<cmd>"` so existing
auto-fetch behavior is preserved with zero manual intervention.

### MCP server hardening

- `format: "none"` is now **verify-silence**: the executor waits a
  short window (default 100 ms) and fails the call with a structured
  `unexpected_output` payload if the device replies anyway.  Catches
  stale profiles and firmware regressions instead of silently
  dropping bytes.  Override with `response.timeout_ms`; opt out
  entirely with `timeout_ms: 0`.
- `async_events` field included in every `run_command` response
  (push delivery; no more polling for unsolicited device events).
- `/profile.info` shows per-field cfg-vs-profile transport drift
  so silent-override confusion is visible at a glance.

### CLI / TUI

- New cfg flag `validate_typed_args` (default false) opts into
  CLI-side typed-arg validation, mirroring MCP semantics.  Off keeps
  raw access (device errors are the source of truth); on catches
  bad values before the wire.
- Disconnect banner now names the port: `Disconnected: COM4`
  mirroring `Connected: COM4 ...`.
- `cfg_data_dir()` refuses to live-activate a cfg path inside the
  installed termapy package (no more accidental source-tree pollution
  from `termapy --cfg src/termapy/builtins/demo/demo.cfg`).

### Internals reorganized into library-shaped subpackages

Three top-level grab-bags became self-contained subpackages with
public-API re-exports.  No consumer code changes (existing imports
keep working via re-exports); the new layout positions each
subpackage as a viable standalone PyPI release when ready:

- `termapy.profile` -- schema, loader, type registry, matcher.
- `termapy.protocol` -- format-spec parser, CRC catalog (64
  algorithms) + codegen, .pro runner, visualizer loader.
- `termapy.usb` -- USB lookup tables (manufacturer alias, VID/PID
  -> chip info, VID -> vendor name + ~3,400-entry fallback).
- `termapy.dialogs` -- monolithic `dialogs.py` split per-class.

### Migration

- `config_version` bumped 16 -> 17.  Drops `auto_include_on_connect`
  and `device_json_cmd`, optionally rewriting them into
  `mcp_on_connect_cmd`.  Both retired keys land in `DEPRECATED_CFG`
  with helpful messages for hand-edited cfgs.
- `migrate_json_to_cfg` tightened to strict exact-name match
  (`<dir>/<dir>.json`) so profile files aren't renamed during
  migration.

### Other polish

- `FolderSpec` defaults flipped to the common case so FOLDERS lists
  declare only exceptions.
- Demo's `AT+HELP.JSON` publishes a `types` block, demonstrating
  the new contract end-to-end.
- Authoring guide gains a "Drafting a profile with an AI" section.
- ARCHITECTURE.md fully refreshed to reflect post-refactor tree;
  release scripts updated to handle subpackage line counts.

## Unreleased

### Added

- **``-e`` / ``--exec`` one-shot CLI mode** -- ``termapy --cli <cfg>
  -e "AT+INFO"`` connects, dispatches a single command, prints the
  response to stdout, and exits with status 0 (success) or 1
  (failure).  Implies ``--cli`` and ``--no-color``; suppresses the
  connect banner, input echo, and cfg-driven autorun so captured
  stdout contains only the command's output.  Pair with
  ``request_mode=true`` cfg for JSON-envelope output.  Mutually
  exclusive with ``--run``.  Fills the long-standing capability gap
  behind ``--cli``: previously the only way to run a single command
  non-interactively was to write a one-line ``.run`` file.

- **``ctx.is_oneshot``** -- ``PluginContext`` callable that returns
  ``True`` when the host is in ``--run`` or ``--exec`` mode (false
  for interactive TUI / CLI REPL).  Plugins authoring chatty
  on-connect logic can read this to suppress output that would
  corrupt captured stdout.

### Changed

- **``PluginContext`` namespaced capability handles (breaking for
  external plugins)** -- the old flat ``ctx`` surface (~50 fields like
  ``ctx.write``, ``ctx.serial_io``, ``ctx.cap_dir``, ``ctx.notify``) is
  replaced by five capability-domain handles: ``ctx.io``, ``ctx.serial``,
  ``ctx.fs``, ``ctx.ui``, and ``ctx.engine``.  Plugin authors now write
  ``ctx.io.write(...)``, ``ctx.serial.io()``, ``ctx.fs.cap_dir``,
  ``ctx.ui.confirm(...)``, etc.  Plugin author surface drops from ~50
  visible names to 12.  TUI-only methods on ``ctx.ui`` raise
  ``MissingCapability`` when called from CLI without declaring the
  matching capability on ``Command.needs``, replacing today's silent
  no-ops with hard failures.  See [Writing
  Plugins](src/termapy/help/writing-plugins.md) for the new shape.

- **``plugins.py`` split into ``plugins/`` package** -- the
  ~1700-line monolith becomes a 12-file package with clear domains:
  ``context.py`` (PluginContext), ``command.py`` (Command, CmdResult,
  Transform, Directive), ``capabilities.py`` (CapabilitySet,
  MissingCapability), ``loader.py`` (discovery), ``output_levels.py``
  (silent/quiet/normal/verbose), and ``handles/`` (the five capability
  handles).  All public re-exports preserved at ``termapy.plugins``.

- **``builtins/plugins/`` renamed to ``builtins/commands/``** -- removes
  the long-standing confusion between the framework package
  (``src/termapy/plugins/``) and the directory of built-in command
  modules.  37 command files moved.  No user-facing change.

- **CLI mode no longer loads Textual** -- ``_run_cli_mode`` moved from
  ``app.py`` to ``cli.py``, and ``_run_tui_mode`` is now lazy-imported
  from ``entry.py`` only when the user picks TUI.  ``termapy --cli -e
  "/help"`` previously pulled in 144 textual modules (~370ms of import
  time); now it loads zero.

- **``--cli`` argparse help text** -- now reads "Use plain-text mode
  instead of the TUI (interactive REPL by default; pair with --run
  or --exec for one-shot)" so the interactive-REPL semantics are
  obvious from ``termapy --help``.  No flag rename; the umbrella
  "CLI mode" label now covers both REPL (default) and one-shot
  (--run / --exec) forms.

- **Connect-time autorun gate** -- the cfg-driven autorun checks
  (``device_json_cmd`` auto-include, ``on_connect_cmd``, the help
  banner) previously skipped only ``--run``.  They now uniformly skip
  any one-shot mode via ``CLITerminal.is_oneshot``, so ``--exec``
  output is never polluted by autorun output.

## 0.64.0 (2026-05-01)

CLI and output-system overhaul.  The pre-0.64 boolean ``verbose`` toggle
and the all-or-nothing ``.quiet`` suffix are replaced by a single
monotonic dial -- ``silent`` < ``quiet`` < ``normal`` < ``verbose`` --
that works the same way at three scopes (session default, per-call
suffix, per-call flag).  ``termapy --ports`` gains a stable JSON shape
with multi-axis filters, and the new ``vendor`` / ``location`` /
``driver`` fields make port identification practical when you have
identical adapters or unrecognized chips.  ``--watch`` is much
lighter on CPU (3-4% during a session, was ~50% on multi-port hosts)
thanks to a fast-gather path that skips the per-port in-use probe;
user-observable reaction time is bounded by Windows USB-enumeration
latency, which polling can't move.  Old command
names continue to work as hidden forwarders, with ``/run.legacy``
extended to rewrite them.

### 0.64.0 New Features

- **Four-level output dial** -- ``/term.output {silent|quiet|normal|
  verbose}`` replaces the boolean ``/term.verbose`` toggle.  Each level
  adds a channel: ``silent`` shows nothing (script consumers read
  ``CmdResult.value``), ``quiet`` shows the answer, ``normal`` adds bulk
  data output, ``verbose`` adds progress chatter.  The same vocabulary
  works as a per-call flag (``cmd --quiet``) or suffix (``cmd.quiet``);
  the universal level dispatch means every command accepts them
  without per-handler setup.  CLI startup flags ``--silent`` /
  ``--quiet`` / ``--verbose`` set the default before the REPL boots,
  which matters most for ``--run`` script invocations.

- **``--ports --json`` with stable schema** -- one JSON object per port
  with snake_case keys, ``null`` for unknown values (not omitted),
  numeric ``vid`` / ``pid`` plus the formatted lowercase-hex ``vid_pid``
  string, and a boolean ``in_use``.  Schema is documented in
  ``help/cli.md`` and treated as API for scripts.  ``--chips --json``
  similarly emits the bundled chip lookup as structured records.

- **Multi-axis port filters** -- ``--vid`` / ``--pid`` / ``--mfg`` /
  ``--sn`` AND-compose with each other and with the existing
  ``--ports <name>`` filter.  Hex flags accept ``0x0403`` or ``0403``;
  ``--mfg`` is a case-insensitive substring; ``--sn`` is exact.
  Distinct exit codes for "bad input" (2) and "no match" (1) so CI
  pipelines can distinguish typo from missing-device.

- **Vendor / location / driver columns** -- three new fields on every
  port record, surfaced in ``--ports``, the JSON output, both port
  pickers (PortPicker and QuickSetup), and the title-bar port hover
  tooltip.

  - ``vendor`` is the silicon-vendor name resolved from the VID via a
    new curated ``usb_vendor`` table (~30 entries covering FTDI,
    Silicon Labs, WCH, Microchip, Microsoft, Espressif, ST, NXP, ...).
    Independent of the descriptor / driver-INF ``manufacturer`` string,
    so a Microchip USB-CDC chip running on Microsoft's ``usbser.sys``
    correctly reports ``Microchip`` as the silicon vendor while
    ``manufacturer_raw`` still preserves the literal ``Microsoft``.

  - ``location`` is the bus path (``1-2.3`` on Linux,
    ``Hub_#0009.Port_#0004`` on Windows after registry fallback for
    FTDI, hex location ID on macOS).  Disambiguates two physically
    distinct adapters that share VID/PID/SN, which cheap clones and
    duplicate-of-the-same-product scenarios both produce.

  - ``driver`` is the bound kernel module (Linux) or service name
    (Windows): ``ftdi_sio``, ``cdc_acm``, ``cp210x``, ``FTSER2K``,
    ``usbser``, etc.  Surfaces driver-binding problems at a glance.

- **DEMO synthesis for hardware-free CI** -- ``termapy --ports DEMO
  --json`` and ``termapy --info DEMO`` now return synthetic records
  for the reserved virtual port names so CI pipelines can exercise
  the CLI without plugging in real hardware.  Bare ``--ports`` (no
  filter) does NOT include DEMO -- it appears only when explicitly
  named, the same way pyserial's ``loop://`` URL handler is reachable
  but not enumerated.

- **``--watch`` lighter on CPU** -- the watch loop previously called a
  per-port ``_check_in_use`` probe that opens each port via
  ``serial.Serial()`` to detect contention.  On Windows this is
  ~250 ms / port (driver init on each ``CreateFile``), so gather
  scaled linearly with port count and consumed ~50% CPU during a
  watch session on a multi-port host.  A new fast-gather path skips
  the in-use probe for the watch loop only -- ``--ports`` and
  ``--info`` keep the full check -- so gather drops from 250+ ms to
  ~7 ms regardless of port count, and CPU during watch falls to
  ~3-4%.

  Note: this does **not** lower user-observable hot-plug reaction
  time meaningfully on Windows.  ``comports()`` itself returns stale
  data for 1-3 seconds after a USB event (longer for FTDI, whose
  driver does an EEPROM-read handshake on every connect), so the
  floor is OS USB-enumeration latency, not termapy's polling loop.
  Reaching that floor would require event-driven APIs (``WMI
  Win32_DeviceChangeEvent``, ``RegisterDeviceNotification``), and
  even those fire only after the driver finishes init -- so the
  practical improvement on Windows would be small.  Linux with
  ``udev`` events would benefit more; future work.

- **``/port.connect`` and ``/port.disconnect``** -- ``/port.open`` and
  ``/port.close`` are renamed to match the user-facing language already
  used in status messages (``Connected:`` / ``Disconnected.``).  Old
  names keep working as hidden forwarders.

- **Three-field manufacturer breadcrumbs** -- the ``--ports --json``
  record now exposes ``manufacturer_raw`` (literal descriptor / INF
  string), ``manufacturer`` (column-friendly aliased short form), and
  ``vendor`` (silicon vendor by VID).  These often agree; when they
  diverge -- typically because a device uses a generic class driver --
  all three are visible so engineers can spot the layering instead of
  having one source silently override the others.

### 0.64.0 Improvements

- **Faster CLI startup** -- the import-discipline guard for
  ``termapy.cli_flags`` is now mechanically enforced by a test that
  fails if Textual / Rich / prompt_toolkit ever sneak into the
  ``termapy --ports`` import path.  Cold-start under 50 ms is
  preserved.

- **Migration forwarders and ``/run.legacy`` updates** --
  ``/verbose on|off`` continues to work, translating to
  ``/term.output verbose|normal``.  ``/term.verbose`` similarly.
  The old "set silently" idiom (``echo.quiet on``, ``term.echo.quiet
  on``) is now recognized by ``/run.legacy --fix``, which rewrites it
  to the ``.silent`` form.  Existing ``.run`` scripts continue to
  work; migrate at your own pace with ``/run.legacy *``.

- **Port pickers widened to 130 columns** -- the QuickSetup ("New
  Config") and PortPicker dialogs gain horizontal space for the new
  vendor/location columns.  ``DROP_ORDER`` reprioritized so vendor
  outranks chip / vid_pid / driver when narrow -- ``Microchip`` in
  9 characters beats ``04D8:9036`` or a 32-character chip name when
  the row is squeezed.

- **FTDI Windows location recovery** -- pyserial returns ``None`` for
  ``location`` on FTDI ports because the FTDIBUS driver hides bus-
  location info from SetupAPI.  termapy now falls back to the registry:
  reads the FTDI device's ``ContainerID``, walks ``HKLM\\SYSTEM\\
  CurrentControlSet\\Enum\\USB`` for the matching USB partner node,
  and reads ``LocationInformation`` from there.  Strings are
  normalized so hub appears before port (``Hub_#0009.Port_#0004``,
  not ``Port_#0004.Hub_#0009``) to match natural reading order.

## 0.63.1 (2026-04-29)

Documentation-only patch release.  Sync of the bundled in-app help
with the v0.63.0 UI changes -- title-bar button labels (``Help``, no
longer ``?``, plus the relocated Exit ``X``), the new ``Ctrl+Shift+1..5``
hotkey aliases, the ``/term.*`` namespace, and one-line entries for
the new commands (``/run.legacy``, ``/credits``, ``/var.list``, plus
the chip-aware ``/port.list`` table).  No code changes.

## 0.63.0 (2026-04-29)

REPL UX overhaul.  Termapy's title-bar buttons, pickers, and command
popup are now reachable from typed commands, with parity between TUI
and CLI.  Display and session toggles consolidate under a new
``/term.*`` namespace, session logs gain a ``/log.*`` family for
inspection, and ``/proto.crc.find`` identifies an unknown CRC algorithm
from a captured packet.  Old command names keep working as hidden
forwarders, with a ``/run.legacy`` tool to find and rewrite them in
existing scripts.

### 0.63.0 New Features

- **``/term.*`` namespace** -- display and session toggles consolidate
  under one namespace, matching ``/port.*`` and ``/cfg.*``.  Nine
  subcommands cover the ground that used to be scattered across
  top-level commands: ``echo``, ``line_no``, ``line_endings``,
  ``verbose``, ``timestamps``, ``hex``, ``encoding``,
  ``send_bare_enter``, ``info``.  The old names (``/echo``,
  ``/line_no``, ``/show_line_endings``, ``/verbose``) keep working as
  hidden forwarders -- they redirect transparently to the ``/term.*``
  equivalents and emit a one-time dim note suggesting the new spelling.

- **``/run.legacy`` migration tool** -- scans a ``.run`` script for
  old command names and either reports or (with ``--fix``) rewrites
  in place.  ``/run.legacy *`` scans every script in the config's
  ``run/`` directory in one pass, so a project full of pre-0.63
  scripts can be migrated with a single command.

- **Bare command opens a picker** -- ``/cfg`` ``/run`` ``/proto``
  ``/port`` (no args) open the matching picker dialog in TUI mode and
  fall through to ``/help <name>`` in CLI mode, with parity across
  all four.  Each command gains a ``.help`` subcommand that prints
  the same long help plus an ``AVAILABLE FILES`` tree of what's
  actually in the relevant folder.  ``/cfg.show`` is new, opening the
  loaded config in the system viewer for symmetry with
  ``/run.show`` / ``/proto.show`` / ``/ss.show``.

- **``/log.*`` namespace for session-log inspection** -- alongside
  the existing ``/log.clear``:

      /log.show         opens the session log in the system viewer
      /log.dump {N}     prints the entire log (or last N lines) to terminal
      /log.fingerprint  writes OS, terminal, Python, termapy, config,
                        and serial port state to the log so old logs
                        are unambiguous when read back later

  Plus ``/term.log <text>`` for annotating the log without echoing
  to screen -- useful for markers, timestamps, and notes that are
  only interesting on later review.

- **``/proto.crc.find``** -- identify an unknown CRC algorithm from
  a captured packet.  Accepts ``bin=<hex>`` (tries trailing 1/2/4
  bytes as CRC, both endians) or ``asc=<text>`` (trailing 2/4/8
  chars as hex-ASCII CRC).  Iterates all 64 catalogue algorithms,
  collapses catalogue aliases to one canonical line, and prints the
  source-code generation command when exactly one algorithm matches.

- **Title-bar Exit, config-gated buttons** -- the Exit button moves
  out of the bottom bar and into the title bar as a red ``X``,
  positioned by OS convention (top-left on mac/linux, top-right on
  Windows).  The ``?`` help button is renamed to ``Help`` and moves
  after the ``Cfg`` / ``Run`` / ``Proto`` trio.  New config booleans
  ``cfg_enabled`` / ``run_enabled`` / ``proto_enabled`` (default
  true) hide the corresponding title-bar buttons for projects that
  don't use them.

- **``/proto.crc.find`` attribution & ``/credits``** -- a new
  ``ACKNOWLEDGMENTS.md`` page (bundled into help) names the projects
  Termapy depends on: Greg Cook (reveng CRC catalogue), Will McGugan
  / Textualize (Textual + Rich), Jonathan Slenders (prompt_toolkit),
  Chris Liechti (pyserial).  ``/credits`` prints it in-terminal.
  The Help button tooltip is restructured into a colored Rich table
  with author attribution.

### 0.63.0 Improvements

- **Animated ``/delay`` progress bar in TUI** -- the CLI has had
  ``[####....] 3s/10s`` for a while; the TUI now shows the same bar.
  Interactive ``/delay`` renders into the status bar; script
  ``/delay`` appends to the script overlay so
  ``delay.run [1/4] [bar]`` stays in one line.

- **Escape dismisses every modal** -- ``ConfigEditor``,
  ``MarkdownViewer``, ``QuickSetup``, ``NamePicker``, and the proto
  debug screen now accept Escape as an alternate dismiss key
  alongside Ctrl+Q.

- **Filtered command popup** -- clicking the ``/`` button with
  ``po`` typed in the input now shows only commands containing ``po``
  (``/port``, ``/proto``, ``/stop``, ...) instead of the full list
  from the top.  ``/po`` and ``po`` behave identically; arguments
  past the first token are ignored.

- **Ctrl+Shift+1..5 hotkeys** replace the unreliable ``Alt+F*``
  aliases for Help/Cfg/Run/Proto/Edit-Config.  F-keys remain primary;
  the ``Ctrl+Shift+digit`` aliases survive VS Code's terminal capture
  via ``modifyOtherKeys`` / csi-u.  The VS Code detected banner now
  labels each shortcut so users know what ``Alt+P`` / ``Alt+S`` /
  ``Alt+T`` actually do.

- **Richer ``/port.list``** -- now uses the picker-style table
  (PORT / MFG / DESCRIPTION / CHIP / SPEED / VID:PID / SN, with
  terminal-width-aware column drop) -- same format as bare ``/port``
  and the ``--ports`` CLI flag.  The zero-config welcome screen
  picks up the richer output for free.

- **``cli_intellisense`` -> ``cli_completion`` rename** --
  IntelliSense is a Microsoft trademark, and "completion" is the
  accurate name anyway (tab completion + auto-suggest + help
  toolbar).  Migration v13 -> v14 handles the rename for existing
  configs.

- **``/proto.crc.help`` -> ``/proto.crc.info``** -- the command
  shows algorithm parameters, not command help, so the name was
  misleading.

- **Command and error-message consistency audit** -- ``{on|off}``
  spellings are uniform (no inner spaces, all toggles optional).
  Error messages adopt sentence case and a small set of standard
  phrasings (``Unknown X`` / ``Invalid X`` / ``X not found`` /
  ``No X loaded.`` / ``Usage: /cmd ...``).  ``/var.list`` added as
  an explicit alias for bare ``/var`` listing.

- **New ``Environment & compatibility`` help page** -- one place
  for OS support, terminal emulators, VS Code quirks, macOS
  Option-as-Meta, KVM cross-platform keyboards, and SSH/WSL/container
  notes.  Replaces the inline VS Code block that used to live in
  ``getting-started.md``.

- **``FileTree`` extracted to ``tree_render.py``** -- single source
  of truth for box-drawing connectors, dim/cyan/blue style triplet,
  and child-indent pattern that used to be duplicated between
  ``cfg._build_tree`` and ``help.append_files_section``.  Pure
  module, no Textual or serial deps; 14 new unit tests.

- **Loopback integration tests** -- 7 new tests using pyserial's
  ``loop://`` URL exercise the termapy <-> pyserial send path:
  line-ending config, ``/raw`` bypass, CRC append via
  ``/proto.send``, latin-1 encoding on the wire, plain round-trip.
  ``/cap.bin`` with exact byte-count targets so captures close on
  target-hit (reliable) rather than timeout.

## 0.62.0 (2026-04-19)

CLI-mode release.  Termapy's ``--cli`` frontend gained a lot of
polish: it now runs without a config file, knows how to switch
configs in a live session, keeps long device responses from
colliding with the next prompt, and got one bug fix that was
actively breaking ``help``-style commands on firmware.  The
``cfg["port"]`` field learned to take USB serial numbers with
pipe-separated fallback chains, and every folder-owning command now
exposes the same ``list / explore / show / dump / clear`` subcommand
family.

### 0.62.0 New Features

- **USB serial numbers in the port field** -- ``cfg["port"]`` now
  accepts a USB serial number (``"A1B2C3D4"``) in addition to a
  device name, with a ``|``-separated fallback chain:

      "port": "A1B2C3D4|COM3"

  *"Prefer serial number A1B2C3D4; if it's not connected, fall back
  to COM3."*  Stable across replugs, stable across machines, stable
  across reboots.  Resolution happens at every connect, so
  unplug/replug events automatically find the device's new COM
  number when the SN survives.  Composes cleanly with environment
  expansion: ``"$(env.DEVICE_SN)|COM3"`` works.  Ambiguous serial
  numbers (two devices sharing ``"0001"`` from cheap CH340 clones)
  are a hard error naming both colliding devices, not a silent
  guess.

- **Zero-config ``--cli`` mode** -- ``termapy --cli`` with no config
  file used to print ``"no config found"`` and exit.  Now it shows
  a welcome banner listing available ports, names the built-in
  defaults (115200 N81 cr noecho), and hints at ``/port.open
  <name>``.  You can talk to a serial device without ever writing a
  ``.cfg`` file.

- **``/cfg.load <name>``** -- switch configs inside a live CLI
  session.  Pairs with zero-config mode: ``termapy --cli`` ->
  ``/cfg.load myproj`` -> you're in the project.  No more exiting
  and re-running to change configs.

- **``/port.open`` line-ending + echo tokens** --
  ``/port.open COM4 9600 N81 crlf echo`` sets baud + frame mode +
  line ending + echo in one command.  New tokens: ``cr`` / ``lf`` /
  ``crlf`` for line ending, ``echo`` / ``noecho`` for echo.  Port
  name must come first; everything else is order-independent.

- **Uniform folder subcommand family** -- every folder-owning
  command (``/ss``, ``/run``, ``/proto``, ``/cap``, ``/cfg``,
  ``/plugin``, ``/app``) now exposes the same shape:

      /<folder>.list       list files
      /<folder>.explore    open folder in file manager
      /<folder>.show       open newest file in system viewer
      /<folder>.dump       print newest (or named) file to terminal
      /<folder>.clear      delete all files (only on ss, cap, prof)

  Which of show/dump/clear is exposed follows the folder's nature:
  user-authored folders (run/, proto/, plugin/) don't get a
  ``.clear``; machine-generated folders (ss/, cap/, prof/) do.  No
  more guessing whether it's ``.configs`` or ``.dir`` or ``.list``.

### 0.62.0 Improvements

- **Long device responses no longer chop the next prompt** -- a
  device streaming 40+ lines of ``help`` output in ``--cli`` mode
  used to race with prompt_toolkit's prompt redraw and leave the
  prompt overlaid mid-stream.  The interactive loop now runs under
  prompt_toolkit's ``patch_stdout`` so reader-thread output gets
  buffered and inserted above the active prompt instead of
  colliding with it.  The TUI was never affected (it has a
  separate input widget); only ``--cli`` had this bug.

- **``/port <SN>`` status line names the actual device** -- when
  you typed ``/port A1B2C3D4``, the status said ``Port changed to
  A1B2C3D4 (session)`` even though you were actually on COM4.
  Now says ``Port changed to COM4 (session)``, matching the
  already-correct connect banner.

- **``termapy>`` prompt in zero-config** -- previously the CLI
  prompt read ``none>`` when no config was loaded (the ``$(CFG)``
  variable fell back to the literal ``"none"``).  The zero-config
  fallback is now ``"termapy"``, so you see ``termapy>`` instead.

- **Bottom toolbar removed** -- prompt_toolkit was reserving a
  full-width row below the prompt for a help tooltip that never
  populated.  The completion dropdown (still there, via Tab) is
  scaled to terminal height: 8 rows of dropdown on a 40+-line
  terminal, 4 rows on smaller, 0 (no dropdown) below 10 rows.

- **Empty ``"port": ""`` in configs now warns** -- a valid port is
  any of: literal device name, USB serial number, reserved name
  (``"DEMO"``, ``"DEMO_FAIL"``), or pyserial URL.  Empty string is
  invalid and now produces a validation warning on config load.
  The zero-config CLI path is the only legitimate place ``port=""``
  appears; it synthesizes in memory and never persists.

- **CI publish workflow handles PyPI double-upload** --
  ``release_publish.py`` publishes to PyPI from the dev machine,
  then GitHub's ``release:published`` event triggers the Actions
  workflow which tries to upload again.  Previously the second
  upload failed with "file already exists" and the workflow showed
  red on every release.  Now uses ``uv publish --check-url`` which
  detects the already-published file and exits 0.

### Breaking changes

- ``/cfg.configs`` renamed to ``/cfg.list``.
- ``/ss.dir`` removed; use ``/ss.list`` (which actually lists files,
  not just the folder path).
- The ``/cfg.<folder>`` listing subtree is gone --
  ``/cfg.run``, ``/cfg.ss``, ``/cfg.proto``, ``/cfg.cap``,
  ``/cfg.prof``, ``/cfg.plugin``, ``/cfg.viz`` and their
  ``.dump/.show/.explore/.clear`` children.  Use top-level
  ``/run.list``, ``/ss.list``, ``/proto.list``, ``/cap.list``,
  ``/plugin.list``, etc. instead.

## 0.61.0 (2026-04-18)

Quality-of-life release.  Termapy now tells you when a new version is
available on PyPI, consolidates its own app-level state (update checks,
recent configs, window geometry) into a single `state.json` that you
can inspect with the new `/app` command, names the process holding a
locked port on Unix, and makes the command palette a little smarter on
non-Linux platforms.  Under the hood, the `cmd_prefix` override is now
fully plumbed so users with a non-`/` prefix see it everywhere in help
output.

### 0.61.0 New Features

- **PyPI update check + Update title-bar button** -- termapy quietly
  checks PyPI on startup (no network call blocks the UI) and, when a
  newer release is available, lights up an `Update` button in the
  title bar.  Clicking it jumps straight to the installation docs so
  you can see how to upgrade for your install method.  The check runs
  at most once a day and is fully opt-out via `update_check: false`.
- **`/app` command + `state.json`** -- app-wide state (last-seen
  version, recent configs, update-check timestamp, window geometry,
  custom buttons) now lives in a single `state.json` under the
  user-level app directory instead of scattered files.  The new
  `/app` REPL command lets you query or edit any field interactively:
  `/app`, `/app recent`, `/app.set custom_button_4 "/port.info"`, etc.
- **Friendlier "port in use" errors** -- when the OS refuses to open
  a port because another process has it, termapy now reports which
  process on Linux / macOS (via `lsof`).  Instead of a bare
  `PermissionError`, you get `Cannot open /dev/ttyUSB0: permission
  denied (held by arduino, PID 12847)`.
- **Smarter `/` palette** -- the command palette hides Linux-only
  commands (`/port.latency_timer`, `/port.permissions`) on macOS and
  Windows so they don't clutter the view with never-applicable rows.
  Help text is column-aligned at position 50 for a tidier two-column
  look at wider terminal widths.
- **Plugin-source labels in startup status** -- the green startup
  banner now annotates each plugin with its origin (`builtin`,
  `global`, `config`) so you can see at a glance where a custom
  command came from when a config pulls plugins from multiple
  sources.
- **`TERMAPY_DEMO_FLEET` environment variable** -- set this and
  `--ports`, `--watch`, `--info`, and `/port.list` enumerate a
  fixed three-port synthetic fleet (FTDI FT232R on COM3, Silicon
  Labs CP2102 on COM4, Microsoft USB Serial on COM7) instead of
  real hardware.  Handy for docs, screenshots, bug reports, or
  trying the tool before you own an adapter.  Sibling to the
  existing `port: DEMO` (fake open) config value.

### 0.61.0 Improvements

- **`cmd_prefix` override is now complete** -- users with a non-`/`
  prefix in their config (`cmd_prefix: "!"`) previously saw stray
  `/` literals in help output, search hits, subcommand listings,
  and CRC error messages.  Every help-rendering site now runs the
  live prefix through the same interpolation helper, and the plugin
  source strings use a `{prefix}` sentinel that's substituted at
  render time.  A new end-to-end test proves dispatch + output
  both honor the override.
- **Friendlier CRC error messages** -- `/proto.crc.c`,
  `/proto.crc.python`, `/proto.crc.rust`, `/proto.crc.calc`, and
  `/proto.crc.help` now fail gracefully and point you at
  `/proto.crc.list` when you pass an unknown algorithm name
  (previously some paths crashed or leaked the `{prefix}`
  placeholder to the screen).
- **`proto_frame_gap_ms` now has a default** -- previously a fresh
  config was missing this key and `/proto.send` would complain;
  it now defaults to 50 ms so the first thing you try works.
- **Better shutdown hygiene** -- race conditions between the reader
  thread firing a final callback and the Textual widget tree
  tearing down are now caught by a dedicated `SHUTDOWN_RACE` tuple
  instead of a broad `except Exception`; and `open` /
  `xdg-open` child processes are reaped by a daemon thread so they
  don't leak zombies when you close help or a capture file from
  the TUI.
- **Deprecated vs. mistyped config keys** -- the config validator
  now distinguishes keys that *used to be real* (deprecated --
  silently dropped with a yellow hint) from keys that are likely
  typos (flagged in red with a did-you-mean suggestion).
- **Release-tooling improvements** -- the release script now
  asserts HTML help freshness after rebuild so stale `*.html` can't
  slip into a tag, and a new `scripts/check_dep_updates.py` surfaces
  outdated dependencies for pre-release review.

## 0.60.0 (2026-04-17)

Command-line release.  Termapy now has a set of one-shot shell flags --
`--ports`, `--watch`, `--info`, `--chips`, `--check` -- that skip the TUI
entirely and print plain text to stdout, so you can answer "what's plugged
in?" from a shell or script without waiting on Textual.  The flags pay
only pyserial's import cost and start fast.

### 0.60.0 New Features

- **`termapy --ports`** -- one-line-per-port table matching the in-app
  port picker.  Filter to a single port with `--ports=COM4`.  Row width
  adapts to the terminal so narrow windows drop low-priority columns
  (speed / chip / vid_pid) before wrapping.  Physical ports only --
  pyserial URL handlers remain a config-side concern.
- **`termapy --watch`** -- live log of port events; start watching,
  plug the cable in, see which COM number it got.  One-character
  event markers (`+` plug, `-` unplug, `~` chip re-EEPROM, blank for
  baseline and open/close transitions) make plug events punch out
  of the stream visually.  `Ctrl+C` to exit.
- **`termapy --info`** -- full per-port chip dump (same data as
  `/port.chip`), identifies chip model, USB speed class, serial
  number, and VID:PID.  Unknown chips print the VID:PID so you can
  identify them manually and nudge you to file an issue so the
  lookup table can grow.
- **`termapy --chips`** -- dump the USB-serial chip lookup table;
  `--chips=ftdi` filters to substring matches.
- **`termapy --check`** -- validate your config and print a JSON
  status line without launching the TUI.
- **USB manufacturer / serial-number columns in the picker** --
  the port picker and `/port.chip.list` now show USB serial numbers
  and an aliased manufacturer column (FTDI / MSFT / SiLabs) for
  faster disambiguation between identical cables.

### 0.60.0 Improvements

- **Command-Line Flags help page** -- a new page in the docs
  walks through `--ports`, `--watch`, and `--info` with real
  captures and an annotated marker-column legend.
- **Faster CLI startup** -- the entry point and all print-and-exit
  flags have been split into Textual-free modules, so `termapy --ports`
  no longer pays the ~300 ms / 40 MB cost of importing Textual.
  `python -m termapy --help` now answers in stdlib+pyserial time.
- **Cleaner config-path resolution** -- `find_config`, `resolve_config`,
  and `infer_config_from_run_file` moved to their own Textual-free
  module, eliminating a previously-duplicated copy in the CLI.

## 0.59.0 (2026-04-16)

Help-system release.  Every built-in command's `/help` page now opens with a one-line live status, device commands brought in by `/include` become first-class citizens in both `/help` and `/search`, and the device JSON schema picks up an optional version field so firmware updates automatically refresh the cache.

### 0.59.0 New Features

- **Dynamic help** -- `Command.long_help` (and `PluginInfo.long_help`) now accepts a callable `(PluginContext) -> str` as well as a static string. Callables are invoked at render time so a command's DESCRIPTION section can reflect live runtime state (loaded files, current connection, cached counts). Existing static-string declarations are unchanged. See `writing-plugins.md` for the pattern.
- **Dynamic help on built-in commands** -- `/help` for `/cfg`, `/port` and each port subcommand, `/include`, `/cap.*`, `/ss`, `/run.edit`, `/proto` (+ subcommands), `/edit.run|proto|plugin`, `/var`, `/env`, and `/seq` now opens with a green single-line status reflecting the current state (active cfg name, connected port, counter count, file counts in the relevant folder, etc.). Single-value commands show only the state line; multi-option commands keep their existing prose.
- **Dynamic-help helpers** -- new `termapy.help_dynamic` module supplies small reusable building blocks (`state_line`, `folder_line`, `port_status`, `cfg_status`, `ns_count`, `compose`, `green`) so plugin authors can wire dynamic DESCRIPTION content in one or two lines. See `writing-plugins.md`.
- **Target-command parity** -- `/include`'d device commands now support optional `long_help` and `flags` fields in their JSON. `/help <target>` renders a full man page (NAME, SYNOPSIS, DESCRIPTION, FLAGS, source marker) when those fields are present. `/search` indexes target commands alongside REPL plugins, tagging device-command hits with `(target)` so they're visually distinct. The demo device's `AT+HELP.JSON` uses the new fields on `AT+INFO`, `AT+TEMP`, `AT+LED`, and `AT+BINDUMP` so the feature is live out of the box in `--demo`.
- **Device schema versioning** -- `/include` JSON may carry an optional top-level `version` string (device's own schema version, not termapy's). On fetch, if the device's version is strictly newer than the cached one (PEP 440 compare), the cache is overwritten; otherwise the cache is kept. First-time fetches with no cache always apply. `/include.reload` still bypasses the gate for manual refresh. The demo device advertises `"version": "1.0.0"` so the round-trip is exercised end-to-end.

### 0.59.0 Changes

- **`/help` is now case-sensitive on exact match** -- the argument is no longer lowercased before lookup. Plugin names (conventionally lowercase) and device AT commands (conventionally upper) both match exactly. Typing `/help INCLUDE` used to silently lowercase and hit `/include`; it now falls through to the forgiving candidate list (which is still case-insensitive internally), where `/include` surfaces as a candidate. This is the change that makes `/help AT+INFO` work for included device commands.

## 0.58.0 (2026-04-15)

### 0.58.0 New Features

- **Forgiving `/help`** -- `/help` now does substring matching across command names, flags, args, and help text when there is no exact match. `/help crc` surfaces every CRC-related command; `/help dev` finds `/help.dev` and `/help.search --dev`. Results group under headings (Command Name, Flags, Help String, Arguments, Long Help) so you can see *why* each command matched, and the matched terms are underlined throughout.
- **Multi-term `/help` search** -- combine terms to narrow down in a sea of commands:
  - `/help table crc` -- commands matching both words
  - `/help crc -table` -- CRC commands, excluding the `--table` codegen ones
  - `/help --table` -- literal flag lookup

  Long-help hits render the full long_help inline so you don't need a second command.
- **`/help.search <pattern>`** -- regex search across every command. Pass `--dev` to also search handler docstrings. Returns matching command names as `CmdResult.value` so scripts can capture the list with `$(VAR) <- /help.search ...`.
- **First-class flags** -- plugin commands now declare their flags on a `Command(flags={...})` dict. The dispatcher parses and strips declared flags before the handler runs; handlers read them via `ctx.flag("--name")`. Benefits you will notice:
  - Flags appear under their own **Flags:** section in `/help <cmd>` output
  - `/help table` finds `--table` directly
  - Typos like `--tablet` get a `did you mean --table?` suggestion instead of being silently ignored

  - Short aliases work uniformly: `/run foo.run -v` and `/run foo.run --verbose` both set the same flag
- **`/cap.poll`** -- new command for polling one or more device commands on a schedule, streaming the responses to a CSV or JSONL file. Supports `count=`, `delay=`, `file=`, `labels=`, `regex=` extraction, and `--overwrite` / `--notime` flags.
- **Network serial ports** -- pyserial URL-style ports (`loop://`, `socket://<host>:<port>`, `rfc2217://...`) are now accepted wherever a port name would go.
- **`CmdResult.value` for scripting** -- plugin handlers can now return a captured value that scripts read back with `$(VAR) <- /command`. Enables composing command output into variables without parsing terminal text.
- **`/cap.poll --table` CRC codegen** -- `/proto.crc.{c,python,rust}` now ship a worked bit-by-bit vs table-driven example in the help pages, so you can see what the generated code looks like for either form.

### 0.58.0 Improvements

- **Getting Started** page gains a concise "finding commands" section listing the `/help` search forms.
- **`/cfg.info --display`**, **`/cap.poll --overwrite / --notime`**, **`/help.search --dev`**, **`/run --verbose`** all converted to first-class flags. Behavior unchanged; discoverability improved.
- **`long_help`** text added for `/cap.text`, `/cap.bin`, `/ping`, `/ping.quiet`, `/repeat` so `/help <cmd>` actually explains what they do.
- **Config help examples validated** -- README and `config.md` examples are now checked at release time to make sure they match the real config schema.

### 0.58.0 Bug Fixes

- **`/cap.poll` JSONL output** -- fixed two type-checker errors that could surface when writing JSON Lines capture output with the `--notime` flag. No behavior change.

## 0.57.0 (2026-04-12)

### 0.57.0 New Features

- **Custom baud rates** -- new `custom_baud` config option unlocks non-standard baud rates (>= 300) for hardware that needs them. By default only standard rates are accepted to catch typos that silently break connections. QuickSetup dialog adds a toggle button to switch between standard list and custom entry. Config editor blocks save on non-standard rates unless `custom_baud` is enabled.
- **TUI-only command warnings** -- CLI now shows "Only available in /tui mode" for commands that require the TUI (screenshots, line numbers, proto debug) instead of silently doing nothing.

### 0.57.0 Improvements

- **Serial engine decoupling** -- DTR/RTS/break toggle, reconnect loop, and hardware signal queries moved from the TUI into SerialEngine. All serial operations now go through the engine rather than touching pyserial directly.
- **TerminalHost base class** -- connect/disconnect lifecycle, serial helpers, capture proto_active handling, port switching, and context callbacks consolidated in the shared base class, removing duplication between TUI and CLI.
- **New Config dialog** -- first serial port selected by default, Connect button disabled with "No Ports" label when no ports are available, title case labels.
- **PyPI publish** -- release_publish.py now builds and uploads to PyPI as part of the release flow.

## 0.56.0 (2026-04-12)

Architecture and CLI experience release. Extracts a shared TerminalHost base class from the TUI and CLI frontends, replaces pyreadline3 with prompt_toolkit for cross-platform CLI intellisense, and adds serial observer hooks, plugin config persistence, and status bar APIs for plugin authors.

### 0.56.0 New Features

- **CLI intellisense** -- tab completion, fish-style auto-suggest from history, and a help toolbar that shows command docs as you type. Toggle with `/cli.intellisense on|off` or the `cli_intellisense` config key.
- **Serial observers** -- plugins can register RX/TX observer callbacks to monitor serial traffic without disrupting the display pipeline.
- **Status bar API** -- `ctx.status_bar(text)` shows transient text in the TUI bottom bar (no-op in CLI).
- **PluginConfig API** -- persistent per-config key-value storage for plugins (`ctx.plugin_cfg("name")`), backed by JSON files in the plugin/ folder.
- **PIC map plugin** -- parse Microchip PIC memory map files and look up symbols by address.
- **Port mode command** -- `/port.open` accepts baud+mode args, `/port.mode` sets serial parameters inline.
- **Lifecycle hooks** -- new `on_app_start`, `on_app_stop` hooks for plugin initialization and cleanup.

### 0.56.0 Improvements

- **TerminalHost extraction** -- shared base class for TUI and CLI consolidates serial I/O, dispatch, capture, context wiring, and hook handlers. ~240 lines of duplicated code removed, 27 callbacks written once.
- **CLI scrolling fixed** -- terminal output now scrolls correctly at the bottom of the window on all Windows console hosts.
- **Shared history** -- TUI and CLI modes use the same history file per config.
- **Test coverage boost** -- 68 new tests covering terminal_host.py (92%), repl.py (89%), overall 66% to 70%.
- **Type checker clean** -- all `ty check` overrides resolved.
- **Port in-use detection** -- correctly identifies when termapy itself has a port open vs another process.

## 0.55.0 (2026-04-09)

Serial-port diagnostics release.  Termapy now identifies the USB chip behind any serial port -- model, USB speed class, driver, latency timer, max baud, in-use state, permissions, VID:PID -- and surfaces the data through the REPL, the title bar, the port picker, the new-config dialog, and a new shell flag.  No breaking changes.

### 0.55.0 New Features

- **`/port.chip` and `/port.chip.<field>` REPL commands** -- 16 single-field subcommands (model, usb_speed, driver, latency_timer, max_baud, permissions, in_use, vid_pid, serial, negotiated, ...) accepting an optional port name or `*` for every connected port.  Backed by a `USB_SERIAL_CHIPS` lookup table covering FTDI, Silicon Labs, WCH, Prolific, and MCU native USB.
- **`/port.info` rewritten as a unified full report** -- configured serial parameters, the complete chip field set, and live DTR/RTS/DSR/CTS signal lines for the currently-connected port.  Rejects a port argument with a pointer to `/port.chip`.
- **`termapy --info[=PORT]` shell flag** -- one-shot chip diagnostics without launching the TUI.  `termapy --info` lists every connected port; `termapy --info=COM4` targets one; exit 0 on success, 1 on error.  Pipe-friendly for shell scripts and CI.
- **Column-aligned port picker** -- PORT / MANUFACTURER / DESCRIPTION / CHIP / SPEED / VID:PID with header and separator rows, data-driven column widths, and shrink-on-overflow (description first, chip second).  The new-config QuickSetup dialog uses the same helpers so its port list is byte-for-byte identical.
- **Expanded QuickSetup baud rate list** -- 300 through 3,000,000, covering the full span from legacy serial devices to FTDI H-series at 12 Mbaud.
- **`help/ports.md` -- a new serial-ports help page** -- 10-second happy path (plug in, pick a port, 115200 8N1, connect) followed by scenario-based troubleshooting: multiple cables, Linux+FTDI latency tuning, Full-Speed vs High-Speed cable purchasing, unknown-chip lookups, "in use" diagnosis, and Linux permission-denied recovery.  Cross-linked from the toolbar page and index.

### 0.55.0 Improvements

- **Unified title-bar tooltips** -- the Cfg, Port, and Connection buttons all use the same three-section format (title / aligned `key = value` body / "Click to: action" line) via a shared `_format_title_tooltip` helper.  Consistent visuals, no more bracket-markup rendering glitches.
- **`In use` detection that actually works on Windows** -- `_check_in_use()` now tries to open the port (and close it immediately) rather than returning `unknown`.
- **Windows FTDI latency-timer hint** -- reports `Latency timer: n/a (Windows - check Device Manager)` for FTDI cables on Windows, nudging users toward where the setting lives instead of a bare `n/a`.
- **Port picker screenshot added** -- `help/img/com_port_select.png` in `help/ports.md` shows what the picker looks like with real chip data.
- **New-config screenshot added** -- `help/img/new_cfg.png` in `help/config.md` under a new "Creating a new config" section documents the QuickSetup first-run flow.
- **Windows FTDI latency-timer screenshot added** -- `help/img/latency_picker.png` in `help/ports.md` shows the Device Manager Advanced port-settings dialog for the tuning walkthrough.

## 0.54.1 (2026-04-08)

Type-check cleanup release.  Brings `uvx ty check src/termapy/` from 370 diagnostics down to 0 while fixing a handful of real correctness issues surfaced by the audit.  No user-visible behavior changes.

### 0.54.1 Improvements

- **Zero ty diagnostics in first-party code** -- a mix of ~8 genuine fixes (narrowing, widening function signatures to `Mapping`, a discriminated-union return type for `load_proto_script`, fixing `QueueByteReader.getc`'s `timeout` annotation from `int` to `float` to match what ymodem actually passes) and ~4 well-documented `# ty: ignore` comments for things the type checker can't see through (POSIX-only typeshed stubs for `readline`/`sys.stdout.reconfigure` on Windows, Textual's heavily-generic `push_screen` overloads, an optional `textual_serve` import, and a cross-variable runtime invariant in `proto_debug`).
- **`pyproject.toml` excludes vendored pyserial from ty** -- the vendored tree references Linux-only modules (`fcntl`, `termios`, `select.POLL*`) in its platform shims, producing ~340 false-positive diagnostics on Windows.  `[tool.ty.src]` now excludes it; `extra-paths` in `[tool.ty.environment]` still makes `import serial` resolve from first-party code.
- **`load_proto_script` has a discriminated-union return type** -- `tuple[Literal["toml"], ProtoScript] | tuple[Literal["flat"], tuple[dict, list[Step]]]` instead of the previous untagged union.  Callers that branch on `result[0]` now get `result[1]` narrowed automatically without `# type: ignore` workarounds.
- **ty badge in the README, auto-updated by `release_prep.py`** -- the release prep script now counts diagnostics from `uvx ty check src/termapy/` and bumps the badge alongside the existing test-count automation.  Thresholds: 0-9 green, 10-19 yellow, 20+ red.  Drift is visible on every release without manual inspection.

### 0.54.1 Fixes

- **`QueueByteReader.getc` `timeout` parameter widened from `int` to `float`** -- the body always did float arithmetic (`deadline = time.monotonic() + timeout`, `min(remaining, 0.05)`) and `ymodem_xfer._read` passes fractional-second timeouts through it, but the `int` annotation was wrong.  No runtime change; just the annotation catching up to reality.
- **`CliEngine._run_script_mode` takes the script path as a parameter** -- previously it read `self.run_script` (typed `str | None`) and passed it to `Path()`, which is correct at runtime because the only caller guards with `if self.run_script:` first.  Making the parameter explicit moves the guarantee into the method signature rather than an implicit runtime invariant.

## 0.54.0 (2026-04-08)

Plugin API release. Two new primitives let plugins own their own session state and lifecycle, and five built-ins (seq, target_commands, echo, hex_mode, verbose) migrate onto them as worked examples. `EngineAPI` is now a legible escape hatch containing only Textual/threading/serial handles - anything that could live in a plain dict has been moved out.

### 0.54.0 New Features

- **`ctx.ns(name)` namespace primitive** -- plugins get a sanctioned, session-scoped `dict` for their state instead of monkeypatching `ctx` or using module globals. Lazy creation on first access, same dict returned on every call with the same name, flat visibility (any plugin can read any namespace) so cooperating plugins can share state on purpose. See `ARCHITECTURE.md` for the full pattern.
- **Plugin lifecycle hooks** -- plugins can now declare `on_app_start`, `on_app_stop`, `on_script_start`, and `on_script_stop` as top-level functions in their module. The loader discovers them alongside `COMMAND`/`TRANSFORM`/`DIRECTIVE`. Exceptions are caught per-hook so one bad plugin cannot block others. Script hooks fire only at the outermost script boundary; nested `/run` does not re-fire. No base class, no decorators - a plugin is still a module that exports stuff.
- **`flags` namespace** -- engine-reserved namespace holding `echo`, `verbose`, and `hex_mode`. Defaults are seeded once in `_build_context` and read sites use bare key access, so a missing key is a construction bug not silent drift. Third-party plugins should use their own namespace name to avoid collision.

### 0.54.0 Improvements

- **`seq` plugin is now self-contained** -- sequence counters and the `{starttime}` timestamp live in `ctx.ns("seq")`, and the plugin wires its own `on_app_start`/`on_script_start` hooks to seed and reset state. `ReplEngine` no longer has `_seq_counters`, `_seq_start_time`, or `_reset_seq`. A reader can now open `seq.py` top-to-bottom and understand every aspect of sequence-counter behavior in one file.
- **Nested `/run` no longer resets counters** -- previously, a nested script would blow away its parent's sequence counters because `start_script` cleared them unconditionally. Now the reset happens in the seq plugin's `on_script_start` hook, which only fires at the outermost script boundary. Inner scripts inherit their parent's counters, which is the less surprising behavior.
- **`EngineAPI` class docstring rewritten** -- reframed as a privileged escape hatch for Textual/threading/pyserial handles rather than a general plugin surface. The field list is now the set of things that genuinely must remain frontend-coupled; anything that could live in a plain dict has been migrated off.
- **CLI install instructions restructured** -- README leads with uv as the preferred package manager, breaks install into numbered steps with time estimates (under a minute from scratch), and simplifies the CLI-mode quickstart to `termapy --cli --demo` so readers can try it without a config.

### 0.54.0 Fixes

- **`/echo on` now actually works in CLI mode** -- previously the CLI hardcoded `get_echo=lambda: False` and `set_echo=lambda val: None` in its `EngineAPI` wiring, so `/echo on` silently did nothing and the plugin reported `off` regardless. After the namespace migration the toggle honestly flips the flag and REPL commands echo when requested, matching TUI behavior. The CLI still defaults to echo off (readline shows input) but users can now turn it on for script debugging.

### 0.54.0 Notes

- **No user-facing config changes.** All three migrated flags (`echo`, `verbose`, `hex_mode`) still read their defaults from the same places they always did.
- **`EngineAPI` lost 11 fields** (`get_echo`/`set_echo`, `get_seq_counters`/`set_seq_counters`/`reset_seq`, `target_commands`/`set_target_commands`/`clear_target_commands`, `get_hex_mode`/`set_hex_mode`, plus `PluginContext.verbose`). External plugins that reached into these via `ctx.engine` will need to migrate to `ctx.ns()`. External plugins that were already using only the public `PluginContext` surface are unaffected.

## 0.53.3 (2026-04-07)

Bug fix and tooling release.

### 0.53.3 Fixes

- **`/cap.bin` help text** -- shortened so the rendered line stays under 120 columns. The previous text wrapped at slightly different points across Rich versions, causing the CLI gold test to fail intermittently under tox.

### 0.53.3 Improvements

- **Forensic dump on CLI gold failure** -- when `tests/test_cli_gold.py` fails it now writes raw output, normalized output, expected output, and a unified diff to `tests/cli_gold/_failures/` so intermittent failures leave evidence behind.
- **Numbered step progress in `release_prep.py`** -- `[1/10] ... [10/10]` headers so a failed prep tells you exactly which stage died.

## 0.53.2 (2026-04-07)

Documentation release.

### 0.53.2 Improvements

- **`scripts/README.md`** -- documents the release automation workflow (prep, manual CHANGELOG edit, publish), enforced conventions, and recovery steps for a failed prep run.

## 0.53.1 (2026-04-07)

Maintenance release. No user-facing changes to the terminal itself.

### 0.53.1 Improvements

- **Release automation** -- new `scripts/release_prep.py` and `scripts/release_publish.py` handle version bumps, doc count updates, CHANGELOG stub generation, test runs, HTML rebuild, tagging, and GitHub release creation. Stdlib only, two-stage with a manual review checkpoint, no force pushes, no auto-merge without `--yes`. This release is the first one cut with them.

## 0.53.0 (2026-04-07)

### 0.53.0 New Features

- **Universal `.quiet` modifier** -- any command can now be invoked as `<cmd>.quiet` to suppress its terminal output, handled by a single dispatcher in `repl.py` instead of per-plugin subcommands. `/echo.quiet`, `/verbose.quiet`, and so on are all routed through the same mechanism.
- **`/expect quiet=on` keyword** -- suppress per-step status from a single `/expect` line in scripts.
- **`parse_bool` shared helper** -- all plugins now accept the same boolean tokens (on/off/1/0/true/false/yes/no) via a single helper.
- **On AI assistance** -- new help page (`on-ai-assistance.md`) documenting how termapy was built with Claude and the role tests play in that workflow.
- **Quiet mode and output channels** -- new section in `scripting.md` documenting the result/output/status channels and how to silence each.

### 0.53.0 Improvements

- **Voice and style pass** -- removed em dashes from prose, normalized headings to sentence case across README, ARCHITECTURE.md, COMPARISON.md, reddit_post.md, and all 17 help pages. No content removed; rationale for the plugin system, CRC catalogue, demo, and CLI surfaced where readers will see it.
- **Plugin help strings** -- `/echo` and `/verbose` advertise their `.quiet` variant inline instead of as a subcommand listing.
- **README cross-platform pitch** -- "runs on Windows, macOS, Linux; under 10 seconds with uv" surfaced in the intro.

## 0.52.0 (2026-04-06)

### 0.52.0 New Features

- **`/repeat` command** -- `/repeat count=<N> {delay=<dur>} {var=<name>} cmd=<command>` repeats a command N times with optional delay. Sets iteration variable (default `REPEAT_N`). Stoppable via Escape key or `/stop`.
- **sum8/sum16 CRC tests** -- 18 tests for built-in checksum modules.

### 0.52.0 Improvements

- **Vendored dependencies** -- pyserial (3.5), xmodem (0.4.7), and ymodem (1.5.3) are now bundled. Eliminates 4 install-time packages (pyserial, xmodem, ymodem, ordered-set).
- **Trimmed tree-sitter** -- only json, toml, and bash grammars installed (was 16). Drops 13 unused grammar packages.
- **textual-serve optional** -- `--web` mode requires `pip install termapy[web]`. Clear error message if missing.
- **Runtime dependencies reduced** -- ~46 packages down to ~16.
- **Assert messages** -- ~1200 test asserts now include descriptive failure messages.
- **"Who This Is Not For"** -- README section for honest expectation-setting.
- **CHANGELOG headings** -- version-prefixed sub-headings fix MD024 duplicate warnings.

## 0.51.0 (2026-04-05)

- **Web mode (experimental)** -- `termapy --web` serves the TUI in a browser via textual-serve. Use `--web-port` to customize the port.

## 0.50.0 (2026-04-05)

- **Fix help page loading** -- Removed `navigation.instant` feature that caused blank pages on localhost server.

## 0.49.0 (2026-04-05)

- **Local help server** -- Help docs served via localhost HTTP server instead of file:// URLs. Zensical search and navigation work correctly.
- **Fully offline help** -- Disabled Google Fonts; all HTML/CSS/JS is bundled with no network requests.

## 0.48.0 (2026-04-05)

- **Published to PyPI** -- `pip install termapy` or `uv tool install termapy`.
- PyPI metadata: authors, classifiers, keywords, project URLs.
- GitHub Actions workflow for automated publishing on release.

## 0.47.0 (2026-04-05)

### 0.47.0 New Features

- **`/help.run`** -- List available .run scripts with descriptions extracted from header comments.
- **`/help.plugin`** -- List loaded plugins grouped by source (application, built-in, user).
- **Scripts section in `/help`** -- Bare `/help` now shows available scripts alongside commands and target device help.
- **Script description convention** -- First line `# comment` followed by blank line is extracted as help text.

## 0.46.0 (2026-04-05)

### 0.46.0 New Features

- **Demo virtual filesystem** -- `AT+FS.LIST`, `AT+FS.INFO`, `AT+FS.DELETE` commands with 3 pre-loaded files for testing file transfers without hardware.
- **Transfer root directory** -- `/xfer.root {path}` command to show or set `file_xfer_root` config key. Both XMODEM and YMODEM resolve relative paths against this directory.
- **Escape cancels transfers** -- press Esc during XMODEM or YMODEM transfer to abort immediately.

### 0.46.0 Improvements

- **Unified dispatch threading** -- all user commands (interactive, buttons, sequences, pickers) now run on background threads, preventing UI freezes from blocking handlers.
- **XMODEM progress** -- shows packet count and byte count, deduplicates repeated lines.
- **XMODEM recv padding** -- strips trailing 0x1A padding bytes so text files open cleanly.
- **Full paths in transfer output** -- completion messages show the full resolved file path.
- **Config migration v10->v11** -- adds `file_xfer_root` key automatically.
- **Transfer root in /port.info** -- shows the configured transfer directory.
- **17 new tests** -- VFS (15) and migration (2), total 1191 tests.

## 0.45.0 (2026-04-04)

### 0.45.0 New Features

- **XMODEM file transfer** -- `/xmodem.send` and `/xmodem.recv` for sending and receiving files over serial using the XMODEM protocol. Automatic CRC/checksum negotiation, 128-byte blocks, progress reporting.
- **YMODEM file transfer** -- `/ymodem.send` and `/ymodem.recv` with batch transfer, 1024-byte blocks, and automatic filename/size metadata. Send multiple files in one session.
- **Demo device XMODEM/YMODEM** -- `AT+XMODEM=SEND/RECV` and `AT+YMODEM=SEND/RECV` in demo mode for testing file transfer without hardware.

### 0.45.0 Improvements

- **File transfer help page** -- new documentation page covering both protocols with comparison table and workflow examples.
- **32 new tests** -- 23 XMODEM tests (QueueByteReader, protocol, library integration) and 9 YMODEM tests.

## 0.44.0 (2026-04-03)

### 0.44.0 New Features

- **Silent screenshots** -- `/ss.svg.quiet` for doc automation; `.quiet` echo suppression works with any command
- **Documentation screenshots** -- `doc_screenshots.run` script generates 10 documentation SVGs from demo mode

### 0.44.0 Improvements

- **Help restructured** -- Installation=onboarding, Getting Started=real device, Demo Mode=reference. Quick Setup dialog documented with screenshot
- **11 documentation images** added across 6 help pages
- **Config keys** -- `hex_mode` and `show_line_numbers` are now config keys (reset on config switch)
- **Target commands** cleared on config switch when no `device_json_cmd`
- **`/help` shows top-level only** with subcommand count
- **CLI echo permanently off** -- readline shows input, scripts can't override
- **`delay.quiet`** suppresses output in scripts
- **Lint fixes** -- walrus operator, unused vars, Any typing, return types, @staticmethod, unused imports
- **Removed mkdocs-material** dev dependency (using `uvx zensical build`)

## 0.43.0 (2026-04-02)

### 0.43.0 New Features

- **CRC code generation** -- `/proto.crc.c`, `/proto.crc.python`, `/proto.crc.rust` generate standalone CRC functions from any of the 62 catalogue algorithms. Use `--table` for table-driven implementation. Both bit-by-bit and table-driven Python output verified against all catalogue check values.

## 0.42.0 (2026-04-02)

### 0.42.0 New Features

- **Inline delays in `/proto.send`** -- `~duration` syntax inserts timing gaps between data segments (e.g. `/proto.send 00 ~25ms "AT\r"`). Supports `us`, `ms`, `s` units. Delays under 1ms use spin-wait for precision.
- **Microsecond durations** -- `parse_duration()` now supports `us` unit throughout the app.

### 0.42.0 Improvements

- **TX/RX display** -- `/proto.send` now shows both hex and smart text for all packets. Inline delays shown as cyan hex + dim text markers.
- **Help docs split** -- "Serial Tools" (interactive send, CRC, hex mode) and "Protocol Testing" (scripts, visualizers, format specs) are now separate help pages with cross-links.
- **`\n` command separator fix** -- literal `\n` in user input no longer splits commands. Only custom button commands support `\n` as a separator. Fixes `/proto.send "text\n"` being split into two commands.
- **Input clears before execution** -- command input box clears immediately and shows "running..." during long-running commands.
- **Help formatting** -- fixed 25/25 column widths, script-only commands section, deduplicated target device rendering, button dispatch fix.
- **AT+HELP.JSON** -- demo device now lists the help command in its own JSON descriptor.

## 0.41.0 (2026-04-01)

### 0.41.0 New Features

- **Quick setup dialog** -- new config creation uses a single dialog with port picker and baud rate selector instead of multi-step flow. Auto-connects after setup.
- **CFG.\* path variables** -- 15 new context variables (`$(CFG.DIR)`, `$(CFG.FILE)`, `$(CFG.PORT)`, `$(CFG.BAUD)`, `$(CFG.PORT_FULL)`, etc.) for use in scripts and commands
- **Config directory precedence** -- `--cfg-dir` flag > `TERMAPY_CFG_DIR` env var > `./termapy_cfg` (if present, never auto-created) > OS default (`%APPDATA%\termapy`, `~/.config/termapy`, `~/Library/Application Support/termapy`)
- **Dot syntax in variables** -- `$(NAME.SUB)` supported (e.g. `$(CFG.PORT_FULL)`)

### 0.41.0 Improvements

- **Resolved paths everywhere** -- all error messages and config info show fully resolved absolute paths
- **Config info verbose-only** -- config dir/file/log paths shown only with `/verbose on`
- **Clear screen on config switch** -- clean slate when loading a new config
- **Focus command input on startup** -- input field gets focus automatically
- **Dotfiles filtered from pickers** -- config/script/proto pickers hide files starting with `.`
- **Clickable paths** -- documented in getting-started help

### 0.41.0 Bug Fixes

- **Plugin module duplication** -- builtin plugins loaded via `importlib` now share module state with package imports, fixing `$(CFG)` not expanding and `FRONT_END` showing as `unknown`

## 0.40.0 (2026-03-31)

### 0.40.0 New Features

- **Reconnect spinner** -- auto-reconnect now shows an animated spinner with amber title bar; click to cancel
- **Connection tooltip** -- title bar shows auto-connect/auto-reconnect status on hover

### 0.40.0 Improvements

- **CLI unit tests** -- 45 tests for CLITerminal (0% -> 53% coverage)
- **defaults.py tests** -- 29 tests (26% -> 97% coverage)
- **Standardized error messages** -- "Not connected." consistent across app, CLI, REPL, port control, and plugins
- **Unified file counting** -- `_count_files()` helper with `FOLDER_PATTERNS` from `folders.py`
- **`on_mount()` refactored** -- broken into `_setup_vars()`, `_build_context()`, `_register_tui_hooks()`, `_load_plugins()`, `_run_startup()`
- **`on_button_pressed()` refactored** -- 89-line if-elif chain replaced with `_BUTTON_DISPATCH` dict routing to 17 named handlers
- **`_serial_op()` helper** -- centralizes serial try/except pattern for DTR, RTS, Break, and send
- **`_sync_all_buttons()`** -- single call replaces 5 scattered sync calls after config switch
- **Protocol module split** -- CRC engine extracted to `protocol_crc.py` (292 lines), visualizer loader to `protocol_viz.py` (132 lines); `protocol.py` reduced from 1,770 to 1,370 lines
- **CI documented** -- README now describes GitHub Actions pipeline (test matrix, coverage, audit)
- **Rename `/import` to `/include`** -- avoids Python keyword collision

### 0.40.0 Bug Fixes

- **`/include` (was `/import`)** -- renamed to avoid Python keyword collision causing import issues

## 0.39.3 (2026-03-31)

### 0.39.3 Bug Fixes

- **Circular import on Python 3.11-3.13** -- lazy import of `var` in `app.py`, add `from __future__ import annotations` to `plugins.py`
- **CI audit job** -- fix `pip-audit` flag syntax, remove `continue-on-error` so security vulnerabilities fail the build

### 0.39.3 Security

- **Pygments >= 2.20.0** -- pin to fix ReDoS vulnerability (CVE in GUID lexer regex)

## 0.39.0 (2026-03-31)

### 0.39.0 New Features

- **`/import` command** -- fetch device command help from JSON over serial; auto-import on connect when `device_json_cmd` is configured
- **`/help.target`** -- show only imported target device commands
- **`/import.reload`** -- force re-import from device, ignoring cache
- **`/import.dump`** -- pretty-print imported commands as JSON
- **`/import.list`** -- list imported commands with args
- **`/import.clear`** -- remove imported commands and delete cache
- **Device help integration** -- new help page documenting how to add a JSON help command to your firmware
- **Disk caching** -- imported commands saved to `.target_menu.json` for instant reload on restart

### 0.39.0 Improvements

- **Rewrite using-git.md** -- simplified help page focused on env vars and .gitignore
- **CLI: no bare print()** -- all output routed through `_raw()` / `_err()` methods for consistent handling
- **`/proto.send` verbose control** -- CRC info, TX bytes, and timing are now verbose-only; quiet mode shows only RX
- **Better error messages** -- friendly serial open errors, config editor live JSON validation, edit-distance command suggestions
- **`repl.cmd()` helper** -- avoids hardcoded command prefix in code
- **Demo device** -- `AT+HELP.JSON` replaces text `HELP` command; GPS commands included in JSON export

### 0.39.0 Config

- **`device_json_cmd`** -- new config key for the serial command that returns device help JSON

## 0.38.1 (2026-03-30)

### 0.38.1 Bug Fixes

- **Circular import on CI** -- CmdResult re-export from scripting.py caused import cycle on clean Python 3.11 installs
- **Toolbar buttons table** -- missing separator row broke table rendering in help docs

## 0.38.0 (2026-03-30)

### 0.38.0 New Features

- **`/log.clear`** -- delete the session log file (TUI + CLI)
- **Variables help page** -- new dedicated docs page for `$(NAME)` syntax, built-ins, env vars, sequences
- **`/edit.run`, `/edit.proto`, `/edit.plugin` without args** -- lists available files instead of showing usage error

### 0.38.0 Improvements

- **`CmdResult` moved to `plugins.py`** -- plugins import from a single module (`from termapy.plugins import CmdResult, Command`)
- **`CmdResult.err_prefix`** -- `ClassVar` for global error prefix customization
- **CLI context dirs fixed** -- `ctx.scripts_dir`, `ctx.proto_dir`, etc. were defaulting to cwd instead of config dir
- **CLI hint ordering** -- "Type commands..." appears before `on_connect_cmd` output
- **Docs coverage** -- custom button JSON example, UI modes, script profiling, `/demo` commands, missing config keys

## 0.37.0 (2026-03-29)

### 0.37.0 New Features

- **Mode switching** -- `/tui` and `/cli` commands switch between TUI and CLI modes
- **`$(CFG)` variable** -- context variable resolves to current config name, usable in prompts
- **`default_ui` config** -- choose default launch mode (`tui` or `cli`), `--cli` flag overrides
- **Unified echo** -- single `_echo_cmd` function for both REPL and serial command echo
- **CLI `on_connect_cmd`** -- CLI now runs startup commands after connecting (was TUI-only)

### 0.37.0 Improvements

- **`CmdResult.err_msg`** -- consistent "Error: " prefix on all error messages
- **run_script refactored** -- `ScriptCtx`, `BLOCKING_COMMANDS` dispatch table, context manager
- **`start_script` returns `(Path, CmdResult)`** -- no more double error messages
- **TUI title tooltip** -- full connection info, features, config path on hover
- **`echo_input_fmt` supports `$(CFG)`** -- prompt shows config name in both modes
- **Config migration v9->v10** -- adds `default_ui`
- **`/ss` and `/grep` CLI errors** -- proper `CmdResult.fail()` instead of yellow warnings
- **Sub-millisecond timing** -- 6-digit precision when `< 0.001s`

### 0.37.0 Bug Fixes

- **No more double error on missing script** -- `start_script` owns error display
- **`/tui` in TUI and `/cli` in CLI** -- no-op instead of unknown command error
- **Demo table in help** -- missing separator row and column alignment fixed
- **Unused `json` import** -- removed from repl.py

## 0.36.0 (2026-03-29)

### 0.36.0 New Features

- **Output channels** -- `ctx.result()`, `ctx.output()`, `ctx.status()` for structured output
- **`/verbose` toggle** -- suppress status messages with `/verbose off`
- **`CmdResult.value`** -- commands return programmatic values (e.g. `/ver` returns `"0.36.0"`)
- **`folders.py`** -- single source of truth for folder names, patterns, and capabilities
- **`connection_string()`** -- centralized formatting with hardware signals (DTR/RTS/CTS/DSR/RI/CD)
- **`/ping` built-in** -- serial response timing with `serial_io` for accuracy
- **`/clr`** -- alias for `/cls`
- **`/raw`**, **`/help.open`** -- now available in CLI mode
- **`/demo`**, **`/demo.force`** -- work in CLI mode
- **`$(FRONT_END)`** -- launch variable (`cli` or `textual`)
- **`cli_prompt`** -- configurable CLI prompt (default `"> "`)
- **`cli_echo_input`** -- control serial echo in CLI (default off)

### 0.36.0 Improvements

- **Folder renames** -- `scripts/` -> `run/`, `plugins/` -> `plugin/` with auto-migration
- **All handlers return `CmdResult`** -- structured success/failure with timing
- **`/var` output colorized** -- cyan names, green values
- **`/cfg <key>` returns clean value** -- `115200` not `baud_rate: 115200`
- **`/echo`, `/verbose`, `/show_line_endings` return on/off** -- clean programmatic values
- **`/env` root handler** -- lists variables (same as `/env.list`)
- **CLI echo off** -- readline shows input, no redundant echo
- **`wait_for_idle` only for serial commands** -- scripts with `/print` run instantly
- **Sub-millisecond timing** -- shows 6 digits when `< 0.001s`
- **CLI `wait_for_idle` 20ms** -- test suite 43s -> 21s
- **`--no-ff` merges** -- preserve branch history in git graph

### 0.36.0 Bug Fixes

- **CLI `serial_send`/`serial_claim` not wired** -- now available
- **CLI `/cls` was no-op** -- now clears terminal
- **`/port.info` RI/CD alignment** -- fixed column spacing
- **Null handler guard** -- parent commands with no root handler no longer crash

## 0.35.0 (2026-03-29)

### 0.35.0 New Features

- **`CmdResult` dataclass** -- all plugin/hook handlers return structured success/failure with error messages and elapsed time
- **`/ping` built-in command** -- measure serial response time with `serial_io` for accurate first-byte timing
- **`/ping.quiet`** -- suppresses device response output
- **`/run.profile` in CLI mode** -- script profiling now works in both TUI and CLI
- **`/expect` keyword syntax** -- `match=`, `timeout=`, `quiet=on` keywords; `parse_keywords()` shared utility
- **`/expect.regex`** -- regex pattern matching in scripts
- **`ctx.serial_send()`** -- send text with configured line ending and encoding
- **`ctx.serial_wait_for_data()`** -- wait for first byte from device
- **`parse_keywords()`** -- shared keyword argument parser in `scripting.py` with space normalization

### 0.35.0 Improvements

- **Centralized error display** -- `dispatch()` handles all error messages in red; handlers just return `CmdResult.fail()`
- **`dispatch()` returns `CmdResult`** -- callers can detect command success/failure and read elapsed time
- **Profiler uses `CmdResult.elapsed_s`** -- timing from dispatch when available, local fallback for TUI
- **`call_from_thread` returns results** -- TUI dispatch now propagates `CmdResult` back to script thread
- **`serial_claim`/`serial_release` wired in CLI** -- `serial_io()` works in CLI mode
- **REPL command echo in cyan** -- was red, now cyan; red reserved for errors
- **Script `wait_for_idle`** -- replaces fixed 100ms sleep between commands, adapts to device response time
- **CLI lambda param names** -- consistent with `PluginContext` signatures

### 0.35.0 Bug Fixes

- **CLI gold test flaky TEXTDUMP** -- `wait_for_idle` fixes race condition with multi-line responses

## 0.34.0 (2026-03-28)

### 0.34.0 New Features

- **`/expect` command** -- wait for serial output containing a pattern in scripts. `/expect {timeout} <pattern>` blocks until matched or aborts on timeout.
- **`/expect.quiet`** -- silent on success, red on timeout
- **`ctx.wait_for_match(predicate, timeout)`** -- engine primitive for plugins to build custom matching (regex, exact, numeric, etc.)
- **`ctx.serial_send(text)`** -- send text with configured line ending and encoding. Plugins no longer need to manually assemble line endings.

### 0.34.0 Improvements

- **DEMO port recognized** -- config editor shows green with "(simulated port)" hint, port picker skipped on load
- **Script abort message** -- scripts that fail on expect timeout show "Script aborted" instead of "Script finished"
- **Demo expect_test.run** -- test script exercising expect match and timeout

### 0.34.0 Bug Fixes

- **cmd.py hardcoded line ending** -- crcsend plugin now uses `ctx.serial_send()` instead of hardcoded `\n`

## 0.33.0 (2026-03-27)

### 0.33.0 Improvements

- **Port auto-detection** -- new configs auto-select the port when only one is available; prompts with a port picker when multiple ports exist
- **Port validation on load** -- loading a config whose port is missing prompts the port picker instead of silently failing
- **Default port changed** -- default port is now empty (`""`) instead of `COM4`, making configs portable across platforms
- **Modal key handling** -- up/down/escape keys no longer leak through to the REPL when a modal dialog is open

### 0.33.0 Documentation

- **README badge rows** -- Project Status, Powered by, and Built with badge sections
- **Docs badge** -- links to GitHub Pages site
- **GitHub Actions docs workflow** -- automatic docs deployment on push to main
- **Config help** -- fixed missing table separator, removed empty table, documented port auto-detection

## 0.32.0 (2026-03-26)

### 0.32.0 Improvements

- **CLI refactored to class** - `CLITerminal` replaces closure-based `run_cli()`
- **Progress bar** - real elapsed time, sub-character resolution (ASCII/Unicode), never shows 100% early
- **`/delay.quiet`** - silent delay subcommand for scripts
- **Config editor** - port validated against available ports, `$(env)` vars resolved and validated, cyan highlighting for variables, italic valid values, bold red "DO NOT EDIT" on config_version, baud rate yellow warning for non-standard values
- **Hook tree override** - registering a hook clears all children from plugins (clean ownership)
- **`/edit` plugin** - uniform edit tree for scripts, proto, plugins, cfg, log, info
- **Visualizer docs** - rewritten with byte-level examples, expected vs actual comparison
- **Smart config resolution** - bare names, directories, file extensions resolved automatically
- **`/ss` stubs** - "not supported in CLI mode" instead of unknown command
- **`/ver` command** and `--version` flag
- **ANSI regex consolidated** - shared `ANSI_RE` and `strip_ansi()` in `scripting.py`
- **Dead code removed** - `parse_script_lines` and 7 tests
- **Unicode cleanup** - 285 em dashes, right arrows, en dashes replaced with ASCII
- **`cfg_dir()` safety** - rejects paths with file extensions
- **`load_config()` safety** - raises `FileNotFoundError` instead of auto-creating configs

### 0.32.0 Documentation

- Installation page (uv-only, no pip)
- CLI Mode section in README
- Reordered help nav: Install -> Demo -> Getting Started -> Config
- Removed manual prev/next nav tables
- CRC catalog note (62 algorithms)
- CONTRIBUTING.md, CHANGELOG.md, LICENSE (MIT)

### 0.32.0 Testing

- 854 tests across 19 test files
- CLI gold-standard integration test (476 lines)
- 16 tests for config resolution chain

## 0.31.0 (2026-03-25)

Initial public release.

### 0.31.0 Features

- **TUI terminal** - full-featured Textual UI with serial I/O, modals, config editor, custom buttons
- **CLI mode** (`--cli`) - plain-text terminal for automation, scripting, SSH, and CI/CD
- **Plugin system** - drop a `.py` file in a folder to add commands. 23 built-in plugins.
- **Scripting** - `.run` scripts with delays, prompts, variables, sequence counters
- **Protocol testing** - binary send/expect tests with 62 CRC algorithms and packet visualizers
- **Data capture** - text, binary, struct, and hex capture modes with format spec decoding
- **Demo mode** (`--demo`) - simulated device for trying everything without hardware
- **Smart config resolution** - bare names, folders, and file extensions resolved automatically
- **Port control** - full serial port management as a plugin (`/port.*` with 17 subcommands)
- **Environment variables** - `$(env.PORT)` in configs and scripts with fallback defaults
- **Git-friendly** - config folders with .gitignore, scripts/plugins/tests versioned together
- **Version** - `--version` flag and `/ver` REPL command
- **Cross-platform** - Windows, macOS, Linux. Python 3.11-3.14.
- **MIT licensed**

### 0.31.0 Architecture

- `app.py` - Textual TUI frontend
- `cli.py` - plain-text CLI frontend
- `repl.py` - REPL engine with plugin dispatch, scripting, transforms, directives
- `plugins.py` - plugin system with `PluginContext` stable API and `EngineAPI` for builtins
- `serial_engine.py` / `serial_port.py` - serial I/O layer (no Textual dependency)
- `capture.py` - capture state machine
- `protocol.py` - binary protocol engine, CRC, format specs, visualizers
- `port_control.py` - pure functions for serial port control

### 0.31.0 Testing

- 861 tests across 18 test files
- CLI gold-standard integration test (476 lines of expected output)
- Passes on Python 3.11, 3.12, 3.13, 3.14
