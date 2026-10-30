# Firmware

> **Status: prototype.** This is new and incomplete: its commands, files and behavior may change or be removed in any release.

termapy ships the device-side sources a monitor compiles in to speak its
protocols, so getting them is one command, not a trip to the repository.
`/firmware` lists what ships; `.copy` writes it into your tree.

| Target | Language | Files                              | Guide                                   |
| ------ | -------- | ---------------------------------- | --------------------------------------- |
| `mem`  | C        | `termapy_mem.c`, `termapy_mem.h`   | [Memory](memory.md#the-wire-spec)       |
| `json` | C        | `termapy_json.c`, `termapy_json.h` | [JSON integration](firmware-json.md)    |

```text
/firmware                          what ships: target, language, files, size
/firmware.copy                     every module of the shipped language into the config's firmware/
/firmware.copy lang=c              the same, naming the language
/firmware.copy mem folder=fw       one module into fw/ under the config's folder, created if missing
/firmware.show json                open termapy_json.c in the system viewer
/firmware.dump mem termapy_mem.h   print one file to the terminal
```

A module's files follow its language: in C a module is a `.c` and its
`.h`, so `mem` is `termapy_mem.c` and `termapy_mem.h`; a language with one
file per module ships one. Like everything termapy writes for a config,
the copy lands in that config's folder: its `firmware/` by default, and a
relative `folder=` is under the config's folder too, never wherever termapy
was started. An absolute `folder=` puts it in your firmware project
instead; over MCP that needs host-wide file access. The reply names the
full path. `.copy` never overwrites: a file already in the folder fails
the whole copy before anything is written, so a module you have edited
stays yours. `lang=` names the language when the sources ship
in several; today everything ships in C, so it is never needed. Over MCP,
`/firmware.dump` is how an agent reads a source: the file's text is the
command's value.

Each module is strict C99 with no dependency beyond the standard headers.
The suite compiles both against a host harness (`tests/test_firmware_mem.py`,
`tests/test_firmware_json.py`), so the shipped source is the tested source.
