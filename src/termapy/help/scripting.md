# Scripting

Click the **Scripts** button or use `/run <filename>` to work with scripts.
The script picker has four actions:

- **New** — create a new script (opens the editor with a template)
- **Edit** — open the highlighted script in the editor
- **Run** — execute the highlighted script
- **Cancel** — close the picker

The script editor provides syntax highlighting (bash-style) for comments
and a name field. Scripts are saved with a `.run` extension in the per-config
`scripts/` folder.

## Script File Format

- Serial commands (sent to the device)
- `/` prefixed REPL commands (delays, screenshots, print, etc.)
- Comments (lines starting with `#`)
- Blank lines (ignored)
- Sequence counters with `{+counter}` for auto-incrementing values

## Script Commands

| Command | Description |
|---------|-------------|
| `/delay <duration>` | Pause execution (e.g. `500ms`, `2s`, `1.5s`) |
| `/expect match=<pattern> {timeout=<dur>} {quiet=on}` | Wait for serial line containing pattern (default 250ms). Aborts on timeout. |
| `/expect.regex match=<pattern> {timeout=<dur>} {quiet=on}` | Same but pattern is a regex. |
| `/confirm {message}` | Show Yes/Cancel dialog. Cancelling stops the script. |
| `/run <script>` | Run a nested script (max 5 levels deep) |
| `/run.profile <script>` | Run nested script with per-command timing |

Keywords use `key=value` syntax (spaces around `=` are OK). `match=` must be
last -- everything after it is the pattern.

## Example Script

```text
# smoke_test.run -- quick device check
AT
/expect match=OK
AT+INFO
/expect timeout=2s match=Bassomatic
AT+TEMP
/expect.regex timeout=2s match=\d+\.\d+C
AT+STATUS
/ss.svg smoke_{seq1+}
```
