# Why termapy is line-based

termapy talks to a device one line at a time. The device collects characters
until a line ending arrives, then acts on the whole line. Editing, history and
type-ahead all happen in termapy, and nothing goes out until you press Enter.
This is the design, not a missing feature: the TUI has no keystroke mode and
no screen emulator, and none is planned (decision 2026-11-07 in
[decisions.md](decisions.md)).

## The case

1. **The device code stays small.** A device that takes lines needs a buffer
   and a check for the line ending, and then it hands the line to its command
   table. A keystroke console also has to handle backspace, cursor keys, tab
   completion and redraws, which can come to a thousand lines of C on every
   board. termapy's shipped C modules (`termapy_mem.c`, `termapy_json.c`) are
   called once per received line and need nothing more.
2. **History lives on the host, per config.** The commands you type are kept
   in the config's `.history` file beside its `.cfg`. Recall survives a device
   reboot, new firmware and a termapy restart, and each device keeps its own.
   Up/Down cycles through it, and type-ahead offers device commands you have
   sent before.
3. **Names are translated on the host.** A line is text that termapy can
   rewrite before it goes out. `$(NAME)` variables expand there, and
   `/mem.read gTemp` turns a C variable's name into an address through the
   symbol table imported from the build. The device sees only an address and
   a length. A name lookup on the device would cost many KB of map in flash.
4. **Registers are named too.** A device file (`dev/*.device.json`, imported
   from the vendor's SVD) names every register and bit field of the part, so
   `/mem.read U1MODE.ON` reads one bit and `/mem.write` masks a field in. A
   value is read at its width, signed or unsigned, as a field, a bit or a
   string (`/mem.str`). The device only moves bytes: widths, byte order and
   fields are termapy's job ([Memory](../src/termapy/help/memory.md)).
5. **It is built to flow into MCP.** An agent drives a device through the
   same lines an engineer types. A device command in the profile reaches the
   agent as a typed tool: its arguments are checked before anything is sent,
   its reply is waited for and matched, and its safety tier decides whether it
   needs a confirmation ([MCP server](../src/termapy/help/mcp-server.md)). A
   keystroke UI gives neither the agent nor a safety gate anything to name:
   "the F key on this screen" is not a command.

## What follows from it

- **A line can be replayed.** The session log, a paste, a recorded `.run`
  file, the CLI gold test and `/expect` all deal in lines, so what you typed
  once runs again the same way. Paste a copied session and only its commands
  run.
- **A line does not care about the transport.** It reads the same over USB
  serial, `socket://` and `rfc2217://`, and through the TUI, the CLI and MCP.
- **A line can be checked before it goes out.** termapy refuses a bad
  argument locally, before the device sees it: always under MCP, and for
  typed lines when `validate_typed_args` is on.

## What it rules out

- **No keystroke mode in the TUI.** Tab, the arrow keys and editing keys
  belong to termapy's prompt. Only control keys reach the device: Ctrl+C,
  Ctrl+D, Ctrl+Z and Escape on an empty prompt, and `/term.ctrl` sends any
  control character.
- **No screen emulator in the TUI.** Output is lines with ANSI color. Cursor
  addressing, erase-line and in-place redraws are not rendered.
- **Nothing asked of the device beyond a line.** It needs no control-code
  handling, line editing or completion. termapy shows the line it sent, so
  the device does not have to echo it either.

## Consoles you did not write

A U-Boot prompt, a Linux login or a vendor's menu is a character-at-a-time
console that nobody is going to rewrite. `--vt100` (or `/vt100` from the TUI)
hands the port to your terminal, which is already an emulator, so those
consoles work ([VT100 mode](../src/termapy/help/vt100.md)). It is a
passthrough, so none of the above applies inside it, and today it keeps no
session log.

A screen emulator that runs with no display, for an agent to read (Phase 2 of
[vt100-build-plan.md](vt100-build-plan.md)), is still open. Nobody types into
it, so it does not conflict with this design.
