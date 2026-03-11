# How Termapy Compares

Termapy is a serial terminal workbench for embedded engineers — built for
scripting, protocol testing, and automation in addition to general-purpose
terminal emulation. It is new (2025), has one developer, requires Python 3.11+
and `uv` to install, and has no community. This comparison is
honest about that.

## Feature Comparison

| Feature                     | Termapy                                                                                                   | RealTerm                                | CoolTerm                   | Tera Term                    | Docklight                             | HTerm                |
| --------------------------- | --------------------------------------------------------------------------------------------------------- | --------------------------------------- | -------------------------- | ---------------------------- | ------------------------------------- | -------------------- |
| **Command scripting**       | `.run` files with delays, sequences, confirmations                                                        | Batch/COM automation                    | macOS only (AppleScript)   | Full macro language (TTL)    | VBScript engine                       | No                   |
| **Programmable extensions** | Python plugins (drop-in `.py` files)                                                                      | No (COM API for external tools)         | No                         | DLL extensions (C/C++)       | No                                    | No                   |
| **Binary protocol testing** | `.pro` scripts with hex send/expect, interactive debug screen, scrolling results, visualizer data in logs | Strong — hex/dec/binary send            | Send hex strings only      | Hex display only             | Strong — visual send/expect sequences | Hex/dec/binary send  |
| **CRC tools**               | 62 named algorithms, `/proto crc` list/help/calc with hex, text, or file input, check-string verification | No built-in                             | No                         | No                           | CRC auto-calculation in sequences     | No                   |
| **Packet visualizers**      | Pluggable — Hex, Text, Modbus built-in; drop `.py` for custom decoders with per-column diff coloring      | No                                      | No                         | No                           | Predefined protocol decoders          | No                   |
| **In-line tool commands**   | Yes — `/grep`, `/cfg`, `/proto`, `/seq` from the input bar                                                | No                                      | No                         | No                           | No                                    | No                   |
| **Config management**       | JSON files with per-config dirs (scripts, plugins, screenshots, logs)                                     | Minimal                                 | Save/load connection files | INI file with GUI dialogs    | Project files                         | XML files            |
| **Command history**         | Per-config, Up/Down arrow cycling with type-ahead                                                           | Basic                                   | Basic                      | Basic                        | N/A                                   | N/A                  |
| **Custom buttons**          | Configurable toolbar — serial, REPL, or script commands                                                   | No                                      | No                         | No                           | Predefined send sequences             | No                   |
| **Screenshots**             | Built-in SVG + text capture                                                                               | No                                      | No                         | Unknown                      | No                                    | No                   |
| **Search scrollback**       | `/grep` with regex                                                                                        | 200-line buffer, no search              | Hex view search only       | Unknown                      | Find Sequence                         | No                   |
| **Auto-reconnect**          | Yes, with autoconnect command sequences                                                                   | Yes                                     | Yes, configurable delay    | Yes (USB replug detect)      | Yes (hotplug recovery)                | No                   |
| **Session logging**         | Auto per-config log file                                                                                  | Yes, timestamped                        | Yes, text/binary           | Yes                          | Yes                                   | Yes                  |
| **Hex display mode**        | Toggle with `/proto hex`                                                                                  | Multiple formats (hex/dec/binary/float) | Side-by-side hex/text      | Hex format                   | HEX/dec/binary                        | HEX/dec/binary/ASCII |
| **Terminal emulation**      | ANSI color rendering                                                                                      | Basic                                   | Limited ANSI               | Full VT100–VT382             | No — protocol analyzer                | No — raw display     |
| **Cross-platform**          | Win/Mac/Linux (Python TUI)                                                                                | Windows only                            | Win/Mac/Linux (native GUI) | Windows only                 | Windows only                          | Win/Linux            |
| **Cost**                    | Free, open source                                                                                         | Free, open source                       | Free (donationware)        | Free, open source            | ~$190 (free eval)                     | Free                 |
| **Line ending control**     | CR/LF/CRLF configurable                                                                                   | Yes                                     | Yes                        | Yes                          | Full byte control                     | Yes                  |
| **Demo/simulation mode**    | `--demo` or `/demo` — simulated device, no hardware needed                                                | No                                      | No                         | No                           | No                                    | No                   |
| **Maturity**                | New (2025)                                                                                                | ~15 years                               | ~10 years                  | 25+ years                    | ~15 years                             | ~10 years            |
| **Community**               | None                                                                                                      | SourceForge forums                      | User forums                | Active open-source community | Commercial support                    | Minimal              |
| **Installer**               | Requires Python + uv                                                                                      | Windows .exe                            | Native installer           | Windows .exe                 | Windows installer                     | Standalone binary    |

## Two Layers of Scripting

Termapy has two distinct automation layers that serve different users:

### Script files (`.run`) — for technicians

Linear sequences of serial commands, delays, and REPL commands. Comparable to
Tera Term macros or Docklight send sequences. No programming required — they
look like what you'd type by hand, just automated.

```text
# smoke_test.run
AT
/delay 300ms
AT+INFO
/delay 500ms
AT+TEMP
/confirm Reset device?
AT+RESET
/delay 1s
```

### Python plugins (`.py`) — for engineers

Full Python with access to the serial port, config, and UI through a stable
API. Can implement arbitrary protocol logic, CRC calculations, state machines,
auto-responders, or device simulators.

```python
from termapy.plugins import Command
from termapy.protocol import get_crc_registry

def _handler(ctx, args):
    crc = get_crc_registry()["crc16-xmodem"].compute(args.encode())
    ctx.serial_write(f"{args} {crc:04X}\n".encode())
    ctx.write(f"Sent: {args} {crc:04X}", "green")

COMMAND = Command(
    name="crcsend",
    args="<text>",
    help="Send text with XMODEM CRC-16 appended.",
    handler=_handler,
)
```

And here it is in the shell reporting 31C3 as the CRC...which matches the XMODEM test case.
This is TRIVIAL for LLMs (and people) to write code for.

```shell
> /crcsend 123456789
Sent: '123456789' 31C3  
```

No equivalent exists in the other tools. Tera Term has DLL extensions but
those require C/C++ and a compiler. Docklight has VBScript but it's sandboxed
to a small object model. Termapy plugins also work through a defined API
(the `PluginContext`), but they're full Python — you can import any library,
do file I/O, make network calls, or spin up threads. They're plain `.py`
files dropped into a folder, including the built-in commands themselves.
Drop a file in a folder and you can add commands, override builtins, implement
protocol handlers, or build a full device simulator. No compilation, no
registration, no restart.

**Security note:** plugins are plain Python with no sandbox — the same trust
model as pip packages or VS Code extensions. Only load plugins you wrote or
reviewed. That said, this is true of any machine with Python installed; the
plugin system doesn't create an attack surface that `python script.py`
doesn't already have.

This is also where AI-assisted development works well — the plugin API surface
is small enough (6 core functions) that an LLM can generate a working plugin
from a one-paragraph description.

## Where Others Win

- **Tera Term** — best terminal emulation (VT100 through VT382), mature
  macro language with decades of refinement, SSH/Telnet support.
- **Docklight** — most polished protocol testing UI with visual sequence
  builder, CRC auto-calculation, trigger-based auto-responses.
- **RealTerm** — deepest hex/binary display options (decimal, float,
  signed/unsigned, I2C/SPI awareness).
- **CoolTerm** — easiest cross-platform install (native GUI, no dependencies),
  clean interface for simple serial work.
- **All of them** — years to decades of real-world use, bug fixes, community
  knowledge, Stack Overflow answers, and one-click installers.

Termapy has none of that history. It compensates with architecture — a plugin
system and REPL that make it extensible in ways the others aren't — but
architecture isn't a substitute for maturity.

## Note on Serial Studio

Serial Studio is sometimes listed alongside serial terminals, but it's a
different kind of tool — a real-time data visualization dashboard (plots,
gauges, maps) rather than an interactive terminal. It's excellent at what
it does, but it doesn't compete in the same category as termapy, CoolTerm,
or Tera Term.
