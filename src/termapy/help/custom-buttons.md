# Custom Buttons

Add custom buttons to the toolbar by configuring `custom_buttons`
in your JSON config. Each button can send serial commands, run REPL commands,
or execute scripts. The default config includes 4 disabled placeholders —
enable them and fill in the fields, or add more entries.

Each button object has these fields:

| Field     | Description                                   |
| --------- | --------------------------------------------- |
| `enabled` | `true` to show the button, `false` to hide it |
| `name`    | Label displayed on the button                 |
| `command` | Command to execute when clicked               |
| `tooltip` | Hover text for the button                     |

## Command Format

- Plain text is sent to the serial device (e.g. `"ATZ"`)
- Commands starting with `/` run as REPL commands (e.g. `"/run test.run"`)
- Use `\n` to chain multiple commands (e.g. `"ATZ\nAT+INFO"`)
- Mixed serial and REPL commands work: `"ATZ\n/sleep 500ms\nAT+INFO"`

Custom buttons appear in the toolbar between the hardware buttons and the
system buttons (Log, SS, Cap, Exit), with a small gap separating them.
