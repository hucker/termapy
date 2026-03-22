# Toolbar & Shortcuts

## Toolbar Buttons

The bottom bar has buttons. Some appear based on context:

| Button      | When Visible                 | Action                              |
| ----------- | ---------------------------- | ----------------------------------- |
| **/**       | Always                       | Show REPL command picker            |
| **DTR:0/1** | `flow_control` is `"manual"` | Toggle the DTR hardware line        |
| **RTS:0/1** | `flow_control` is `"manual"` | Toggle the RTS hardware line        |
| **Break**   | `flow_control` is `"manual"` | Send a 250ms serial break signal    |
| **Log**     | Always                       | Open the session log in your editor |
| **SS**      | Always                       | Open the screenshot folder          |
| **Cap**     | Always                       | Open the captures folder            |
| **Scripts** | Always                       | Pick, run, create, or edit a script |
| **Custom**  | `custom_buttons` enabled     | User-defined command buttons        |
| **Exit**    | Always                       | Close the connection and quit       |

## Keyboard Shortcuts

| Key        | Action                               |
| ---------- | ------------------------------------ |
| **Ctrl+Q** | Quit (also closes any open dialog)   |
| **Ctrl+L** | Clear screen                         |
| **Ctrl+P** | Open command palette                 |
| **F5**     | Save SVG screenshot                  |
| **F6**     | Open screenshot folder               |
| **F7**     | Save text screenshot                 |
| **Up/Down**| Cycle through command history        |
| **Escape** | Clear input / exit history browsing  |
| **Right**  | Accept type-ahead suggestion         |

## Command Palette

Press **Ctrl+P** to open the command palette, which provides quick access to:

- Select Port
- Connect / Disconnect
- Edit, Load, or Create a Config
- Open Log File
- Delete Log File
- Clear Screen
- Save Screenshots
- Open Screenshot / Captures Folder
- Show Newest Screenshot / Capture

---

| | | |
|:---:|:---:|:---:|
| [← Getting Started](getting-started.md) | [Index](index.md) | [REPL Commands →](commands.md) |
