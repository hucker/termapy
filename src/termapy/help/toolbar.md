# Toolbar and shortcuts

## Title bar buttons

The top bar has four always-visible buttons plus a centered config name:

| Button          | Position  | Action                                       |
| --------------- | --------- | -------------------------------------------- |
| **?**           | Top-left  | Open this help guide                         |
| **Cfg**         | Top-left  | New / Edit / Load a config                   |
| **Run**         | Top-left  | Pick, run, or create a script                |
| **Proto**       | Top-left  | Open a protocol test script                  |
| **Config name** | Center    | Click to edit the currently-loaded config    |
| **Port name**   | Top-right | Click to open the serial port picker         |
| **Status**      | Far-right | Click to connect or disconnect               |

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

| Button      | When Visible                 | Action                              |
| ----------- | ---------------------------- | ----------------------------------- |
| **/**       | Always                       | Show REPL command picker            |
| **DTR:0/1** | `flow_control` is `"manual"` | Toggle the DTR hardware line        |
| **RTS:0/1** | `flow_control` is `"manual"` | Toggle the RTS hardware line        |
| **Break**   | `flow_control` is `"manual"` | Send a 250ms serial break signal    |
| **Log**     | Always                       | Open the session log in your editor |
| **SS**      | Always                       | Open the screenshot folder          |
| **Cap**     | Always                       | Open the cap/ folder                |
| **Scripts** | Always                       | Pick, run, create, or edit a script |
| **Custom**  | `custom_buttons` enabled     | User-defined command buttons        |
| **Exit**    | Always                       | Close the connection and quit       |

## Keyboard shortcuts

| Key         | Action                              |
| ----------- | ----------------------------------- |
| **Ctrl+Q**  | Quit (also closes any open dialog)  |
| **Ctrl+L**  | Clear screen                        |
| **Ctrl+P**  | Open command palette                |
| **F5**      | Save SVG screenshot                 |
| **F6**      | Open screenshot folder              |
| **F7**      | Save text screenshot                |
| **Up/Down** | Cycle through command history       |
| **Escape**  | Clear input / exit history browsing |
| **Right**   | Accept type-ahead suggestion        |

## Command palette

Press **Ctrl+P** to open the command palette, which provides quick access to:

- Help
- Select Port
- Connect / Disconnect
- Edit, Load, or Create a Config
- View / Delete Log File
- Clear Screen
- Save SVG / Text Screenshot
- Open Screenshot Folder
- Open Captures Folder
- Show Newest Screenshot
- Show Newest Text Capture
- Exit
