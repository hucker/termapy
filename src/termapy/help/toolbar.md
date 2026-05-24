# Toolbar and shortcuts

## Title bar buttons

The top bar has the title-bar buttons plus a centered config name:

| Button          | Position    | Action                                          |
| --------------- | ----------- | ----------------------------------------------- |
| **Cfg**         | Top-left    | New / Edit / Load a config                      |
| **Run**         | Top-left    | Pick, run, or create a script                   |
| **Proto**       | Top-left    | Open a protocol test script                     |
| **Help**        | Top-left    | Open this help guide                            |
| **Config name** | Center      | Click to edit the currently-loaded config       |
| **Port name**   | Top-right   | Click to open the serial port picker            |
| **Status**      | Far-right   | Click to connect or disconnect                  |
| **X**           | Corner (OS) | Red — close the connection and quit             |

The **X** sits top-left on macOS/Linux and top-right on Windows, matching
each platform's window-control convention.  Cfg, Run, and Proto can be
hidden per config via `cfg_enabled`, `run_enabled`, and `proto_enabled`.
Typing the bare command (e.g. `/cfg`) is equivalent to clicking the
button.

Hover over any of the config, port, or status buttons to see a detailed
tooltip with current settings and chip information.

### Port picker

Clicking the port name in the title bar (or picking **Select Port**
from the command palette) opens a dialog listing every connected
serial port with its USB chip identification.  For a full tour of the
port picker, the `/port.info` and `/port.chip` commands, the
`termapy --info` CLI flag, and the rest of termapy's serial-port
diagnostics, see [Serial ports](ports.md).

## Toolbar buttons

The bottom bar has buttons. Some appear based on context:

| Button      | When Visible                 | Action                                          |
| ----------- | ---------------------------- | ----------------------------------------------- |
| **/**       | Always                       | Show REPL command picker (filters as you type)  |
| **DTR:0/1** | `flow_control` is `"manual"` | Toggle the DTR hardware line                    |
| **RTS:0/1** | `flow_control` is `"manual"` | Toggle the RTS hardware line                    |
| **Break**   | `flow_control` is `"manual"` | Send a 250ms serial break signal                |
| **Log**     | Always                       | Open the session log in your editor             |
| **SS**      | Always                       | Open the screenshot folder                      |
| **Cap**     | Always                       | Open the cap/ folder                            |
| **Scripts** | Always                       | Pick, run, create, or edit a script             |
| **Custom**  | `custom_buttons` enabled     | User-defined command buttons                    |

## Keyboard shortcuts

| Key                    | Action                                              |
| ---------------------- | --------------------------------------------------- |
| **Ctrl+Q**             | Quit (also dismisses any open dialog)               |
| **Escape**             | Dismiss dialog / clear input / exit history         |
| **F1**–**F4**          | Help / Cfg / Run / Proto (same as the buttons)      |
| **Ctrl+Shift+1**–**5** | Same four, plus Edit Config on **5**                |
| **Ctrl+L**             | Clear screen                                        |
| **Ctrl+P**             | Open command palette                                |
| **F5**                 | Save SVG screenshot                                 |
| **F6**                 | Open screenshot folder                              |
| **F7**                 | Save text screenshot                                |
| **Up/Down**            | Cycle through command history                       |
| **Right**              | Accept type-ahead suggestion                        |

The `Ctrl+Shift+digit` aliases work inside VS Code's integrated
terminal, which intercepts some F-keys.

## Command palette

Press **Ctrl+P** (or **Alt+P** if your terminal captures Ctrl+P --
VS Code's integrated terminal does) to open the command palette.
It drops down from the top-center of the window, like VS Code's
command palette, with fuzzy filtering as you type and a most-recently-used
list when opened with an empty query.

Quick access to:

- Help
- Find in scrollback / Grep scrollback / Search command help
- Select Port
- Connect / Disconnect
- Edit, Load, or Create a Config
- Load Run Script
- View / Delete Log File
- Clear Screen
- Save SVG / Text Screenshot
- Open Screenshot Folder
- Open Captures Folder
- Show Newest Screenshot
- Show Newest Text Capture
- Exit
