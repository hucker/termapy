# Scripting

Click the **Scripts** button or use `/run <filename>` to work with scripts.
The script picker has four actions:

- **New:** create a new script (opens the editor with a template)
- **Edit:** open the highlighted script in the editor
- **Run:** execute the highlighted script
- **Cancel:** close the picker

The script editor provides syntax highlighting (bash-style) for comments
and a name field. Scripts are saved with a `.run` extension in the per-config
`run/` folder.

## Script file format

- Serial commands (sent to the device)
- `/` prefixed REPL commands (delays, screenshots, print, etc.)
- Comments (lines starting with `#`)
- Blank lines (ignored)
- Sequence counters with `{+counter}` for auto-incrementing values

## Script commands

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

## Profiling

Run a script with per-line timing to find slow spots:

```text
/run.profile smoke_test
```

This saves a CSV to the `prof/` folder with elapsed time for each line.

| Command                      | Description                              |
| ---------------------------- | ---------------------------------------- |
| `/run.profile <script>`      | Run script with per-line timing          |
| `/run.profile.cmd <command>` | Profile a single command                 |
| `/run.profile.show`          | Open newest profile in system viewer     |
| `/run.profile.dump`          | Print newest profile to the terminal     |
| `/run.profile.explore`       | Open `prof/` folder in file explorer     |
| `/run.profile.list`          | List profile files                       |

## Quiet mode and output channels

Termapy commands write to three output channels:

- **Result:** the answer to a command. Always shown. Example: `/cfg baud_rate` printing `115200`.
- **Output:** data dumps, file contents, listings. Always shown. Example: `/show file.txt`.
- **Status:** progress messages, intermediate notes, "doing X now..." lines. Suppressed when verbose is off.

When you run a script, the status channel is usually noise. You want to see what happened, not a running commentary of every step. Termapy gives you three ways to quiet things down:

| What you want to silence                | How                                                          |
| --------------------------------------- | ------------------------------------------------------------ |
| Status messages from all commands       | `/verbose off` (or `/verbose.quiet off` in scripts, no echo) |
| Echoing of each command before it runs  | `/echo off` (or `/echo.quiet off` in scripts)                |
| Per-step status from a single `/expect` | Add `quiet=on` to the `/expect` line                         |

The `.quiet` variants (`/verbose.quiet`, `/echo.quiet`) set the value without echoing the change itself. This is useful at the top of a script or in `on_connect_cmd` where the act of toggling shouldn't itself produce output.

`/verbose` only affects the *status* channel. Command results and data output are always visible. Quiet mode is about silencing chatter, not silencing answers. If you run `/cfg baud_rate` in quiet mode, you still get `115200`. You just don't get the dim status line that would normally precede it.

```text
# clean_check.run -- quiet output, just the results
/echo.quiet off
/verbose.quiet off
AT+INFO
/expect quiet=on match=Bassomatic
AT+TEMP
/expect quiet=on match=C
```

For plugin authors: `ctx.result()`, `ctx.output()`, and `ctx.status()` are the three channels. Use `status()` for anything that's progress narration so users can suppress it cleanly.

## Example script

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
