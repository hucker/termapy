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

A leading `#` comment block (lines 1..N, ending at the first blank or
non-comment line) is the script's docstring: the first line is the
summary shown by `/run.list`; the full block is what `/run.help <script>`
prints.

## Recording a session

The fastest way to make a `.run` script is to record what you typed.

- **TUI:** click the **Record** button next to the REPL prompt, enter
  a filename, run the commands, click **Stop**.  Hide the button with
  `record_enabled: false` in your config.
- **CLI / MCP:** `/run.record <filename>` starts, bare `/run.record`
  stops.  Same lifecycle, no button.

Only successful dispatches land in the file -- failed commands,
typos, and `/run.record` itself are skipped.  The target file is
refused if it already exists (delete or pick a new name).  Add a `#`
docstring at the top of the recorded file and it becomes self-describing
in `/run.list` and `/run.help`.

## Script commands

| Command | Description |
|---------|-------------|
| `/delay <duration>` | Pause execution (e.g. `500ms`, `2s`, `1.5s`) |
| `/expect match=<pattern> {timeout=<dur>} {quiet=on}` | Wait for serial line containing pattern (default 250ms). Aborts on timeout. |
| `/expect.regex match=<pattern> {timeout=<dur>} {quiet=on}` | Same but pattern is a regex. |
| `/confirm {message}` | Show Yes/Cancel dialog. Canceling stops the script. |
| `/run <script>` | Run a nested script (max 5 levels deep) |
| `/run.profile <script>` | Run nested script with per-line timing |

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

## Output levels

A single dial controls how loud commands are. Four monotonic levels:

| Level     | Result | Output | Status | Use                                       |
| --------- | :----: | :----: | :----: | ----------------------------------------- |
| `silent`  |   —    |   —    |   —    | scripts reading only `CmdResult.value`    |
| `quiet`   |   ✓    |   —    |   —    | show the answer, drop the rest            |
| `normal`  |   ✓    |   ✓    |   —    | default                                   |
| `verbose` |   ✓    |   ✓    |   ✓    | show progress chatter and timing          |

Set the session default with `/term.output <level>`. Override for a single
call with `cmd --<level>` or `cmd.<level>`:

```text
/term.output quiet              # session default
/cfg baud_rate                  # shows 115200
/help port --verbose            # one call back to verbose
/cap show capture.bin --silent  # run, drop output, only value returns
```

Set the level at startup with `--silent`, `--quiet`, or `--verbose` on the
`termapy` command line.

For plugin authors: `ctx.result()` is the answer, `ctx.output()` is bulk
data, `ctx.status()` is progress chatter. Each gates on the active level.
Handlers that produce scriptable data must call `CmdResult.ok(value=...)`
so silent mode is useful.

> **Migrating older scripts.** Scripts that used `/verbose on/off`,
> `/term.verbose on/off`, or `*.quiet` as a "set silently" idiom keep
> working via hidden forwarders. Run `/run.legacy <file>` to preview
> the canonical replacements, or `/run.legacy --fix <file>` to rewrite
> in place. `/run.legacy *` processes every script in `run/`.

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
