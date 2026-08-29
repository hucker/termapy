# Termapy help

Welcome to `termapy`, a TUI serial terminal with scripting, protocol testing, and data capture.

![Termapy TUI](img/doc_01_main_tui.svg)

Select a topic:

- [Installation](installation.md): requirements, install with uv or pip
- [Environment & compatibility](environment.md): OS, terminal emulators, VS Code quirks, KVM/keyboard gotchas
- [Demo mode](demo.md): try everything without hardware
- [Getting started](getting-started.md): connect to real hardware, config files, folder layout
- [Configuration](config.md): JSON config, field reference, config management
- [Serial ports](ports.md): picking a port, chip info, USB speed, latency tuning
- [Command-line flags](cli.md): `--ports`, `--watch`, `--info`, and friends
- [VT100 mode](vt100.md): raw ANSI passthrough for cursor-addressed devices
- [Custom buttons](custom-buttons.md): adding toolbar buttons
- [Toolbar and shortcuts](toolbar.md): buttons, keyboard shortcuts, command palette
- [REPL commands](commands.md): full command reference table
- [Variables](variables.md): `$(NAME)` expansion, environment and date/time variables, `$(*NAME)` dereference
- [Scripting](scripting.md): automating command sequences
- [Serial tools](serial-tools.md): `/proto.send` raw bytes with inline delays, hex mode, the CRC catalog, detection and code generation
- [Protocol testing](protocol-testing.md): `.pro` send/expect test scripts, the format-spec language, packet visualizers
- [Data capture](data-capture.md): text and binary capture to files
- [File transfer](file-transfer.md): XMODEM and YMODEM send and receive over serial
- [Writing plugins](writing-plugins.md): add custom commands with Python
- [Device help](device-help.md): integrate your device's commands into termapy
- [Authoring profiles](authoring-profiles.md): write the device profile that powers device help and MCP
- [MCP server](mcp-server.md): let an LLM client like Claude drive your device through typed JSON tools
- [Using with git](using-git.md): version control, team workflow, env vars
- [On AI assistance](on-ai-assistance.md): how termapy was built with Claude, and what testing has to do with it
- [Acknowledgments](acknowledgments.md): the open-source projects and authors termapy depends on
