# Firmware

termapy ships the device-side sources a monitor compiles in to speak its
protocols, so getting them is one command, not a trip to the repository.
`/firmware` lists what ships; `.copy` writes it into your tree.

| Target | Language | Files                              | Guide                                   |
| ------ | -------- | ---------------------------------- | --------------------------------------- |
| `mem`  | C        | `termapy_mem.c`, `termapy_mem.h`   | [Memory](memory.md#the-wire-spec)       |
| `json` | C        | `termapy_json.c`, `termapy_json.h` | [JSON integration](firmware-json.md)    |

```text
/firmware                          what ships: target, language, files, size
/firmware.copy mem                 termapy_mem.c and termapy_mem.h into the current folder
/firmware.copy json folder=fw      into fw/, created if missing
/firmware.show json                open termapy_json.c in the system viewer
/firmware.dump mem termapy_mem.h   print one file to the terminal
```

`.copy` never overwrites: a file already in the folder fails the whole copy
before anything is written, so a module you have edited stays yours.
`lang=` names the language when a target ships in several; today each ships
in C, so it is never needed. Over MCP, `/firmware.dump` is how an agent
reads a source: the file's text is the command's value.

Each module is strict C99 with no dependency beyond the standard headers.
The suite compiles both against a host harness (`tests/test_firmware_mem.py`,
`tests/test_firmware_json.py`), so the shipped source is the tested source.
