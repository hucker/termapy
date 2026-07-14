# Environment and compatibility

Termapy runs on Windows, macOS, and Linux.  The core features (serial
I/O, REPL, scripting, capture, screenshots) behave the same on every
platform -- but some terminal hosts add their own quirks that are
worth knowing about.

This page collects the environment-specific tips in one place.

## Operating systems

### Windows

Fully supported.  Standard install via `uv tool install termapy`.
Serial ports appear as `COM3`, `COM10`, etc.  Detected ports are
listed with `termapy --ports`.

Known quirks:
- Some USB-serial drivers (especially generic CH340 clones) don't
  emit a stable USB serial number, so the device's COM number can
  change after replug.  Mitigate by putting the SN in your config:
  `"port": "<SN>|COM3"` — see [Serial ports](ports.md) for the full
  grammar.
- `termapy --web` opens a browser-based TUI; in some configurations
  the Windows firewall prompts for permission the first time.

### macOS

Fully supported.  Standard install via `uv tool install termapy`.
Serial ports appear as `/dev/cu.usbserial-*` or similar.

Known quirks:
- The OS exposes both `/dev/tty.*` and `/dev/cu.*` per device; termapy
  defaults to `cu.*` (non-blocking open), which is what you want for
  interactive use.
- Alt-key bindings depend on terminal's Option-key handling (see the
  [Alt bindings](#alt-key-fallbacks) section below).

### Linux

Fully supported.  Serial ports appear as `/dev/ttyUSB*`, `/dev/ttyACM*`,
or `/dev/ttyS*`.

Known quirks:
- Access to `/dev/tty*` typically requires membership in the `dialout`
  group (or `uucp` on some distros).  `sudo usermod -aG dialout $USER`
  then log out and back in.  This is a general serial-on-Linux gotcha,
  not termapy-specific.
- Some terminals (especially older ones) don't emit mouse events or
  don't support 24-bit color — cosmetic degradation only.

## Terminal emulators

Termapy is built on Textual, so it inherits Textual's terminal
compatibility.  Most modern terminals work out of the box; a few have
specific quirks.

### Fully tested

- Windows Terminal (recommended on Windows)
- iTerm2 (macOS)
- Terminal.app (macOS)
- GNOME Terminal (Linux)
- Konsole (Linux)
- Alacritty
- WezTerm
- Kitty

### Works with minor caveats

- **VS Code integrated terminal** — see the dedicated section below
- **tmux / screen** — works; some clear-screen edge cases in older
  versions
- **Powershell ISE** — limited; use PowerShell 7+ in Windows Terminal
  instead
- **cmd.exe** — works but ANSI color support is minimal; prefer
  Windows Terminal

### Known not to work well

- **serial-to-another-system SSH sessions through Windows Telnet** —
  use PuTTY, Windows Terminal SSH, or WSL instead
- **Very old GNOME VTE (pre-0.62)** — missing true-color support

## VS Code integrated terminal

The VS Code integrated terminal is the single most common source of
"why doesn't my TUI act like it does in other terminals" questions.
Two distinct issues:

### Text selection

The TUI captures mouse events, so the usual click-and-drag selection
inside VS Code's terminal doesn't behave normally while termapy has
focus.  What works:

1. Hold **Alt+Shift** and drag to select.
2. While still holding the left mouse button, **right-click** to copy
   the selection.

This is general Textual-TUI behavior, not a termapy bug.  Native
terminals (Windows Terminal, iTerm2, most Linux terminals) usually
accept plain **Shift+click** to bypass the TUI's mouse capture.  For
anything longer, `Ctrl+S` saves an SVG screenshot and `Ctrl+T` a text
screenshot -- often more useful than a selection.

### Ctrl-key capture (Windows + Linux)

VS Code's integrated terminal on Windows and Linux captures
`Ctrl+P` (Quick Open), `Ctrl+S` (Save), and `Ctrl+T` (New Tab) before
they reach the shell.  Inside termapy that means those shortcuts
never fire unless you rebind them in VS Code.

**macOS VS Code** uses `Cmd+*` for those shortcuts, so `Ctrl+*`
passes through normally there -- no fallback needed.

#### Alt-key fallbacks

Termapy binds **Alt+\*** alternates for each captured key on every
platform:

| Action          | Primary | Alt fallback |
| --------------- | ------- | ------------ |
| Quit            | Ctrl+Q  | Alt+Q        |
| Save SVG        | Ctrl+S  | Alt+S        |
| Save text       | Ctrl+T  | Alt+T        |
| Command palette | Ctrl+P  | Alt+P        |

VS Code rarely captures Alt-key combos, so the fallbacks survive.
When termapy starts inside VS Code's terminal on Windows or Linux,
a green banner appears at the top of the output reminding you.

#### Releasing the Ctrl keys (optional)

If you'd rather keep your `Ctrl+*` muscle memory, edit your VS Code
`keybindings.json` to release the keys when the terminal has focus:

```json
[
  {
    "key": "ctrl+p",
    "command": "-workbench.action.quickOpen",
    "when": "terminalFocus"
  },
  {
    "key": "ctrl+s",
    "command": "-workbench.action.files.save",
    "when": "terminalFocus"
  },
  {
    "key": "ctrl+t",
    "command": "-workbench.action.showAllSymbols",
    "when": "terminalFocus"
  }
]
```

The `-` prefix unbinds the command *only* when the terminal has focus.
Clicking into an editor tab restores VS Code's default behavior.

## macOS Alt-key behavior

On macOS, the **Option** key (physically where Alt would be on a
Windows keyboard) behaves one of two ways, depending on your
terminal's settings:

- **"Option as Meta"**: Option sends `ESC+<key>`, which Textual
  interprets as an `alt+<key>` binding.  Termapy's Alt fallbacks
  work.
- **"Option as Option"** (default in some terminals): Option
  produces special characters (π, ß, ¬, etc.).  Alt bindings don't
  fire.

Configuration path per terminal:

- **iTerm2**: Profiles → Keys → "Left Option key: **Esc+**"
- **Terminal.app**: Profiles → Keyboard → "Use Option as Meta key"
- **Alacritty / Kitty / WezTerm**: check the terminal's docs; most
  default to Meta behavior that works.

On Mac with the default Ctrl+\* bindings all passing through anyway,
you rarely *need* the Alt fallbacks.  Flip this only if you're
regularly pairing with Windows/Linux muscle memory.

## KVM and cross-platform keyboards

If you use a KVM switch with a single keyboard across Mac + Windows,
the physical keys don't map where macOS expects them.  The classic
example: a Windows keyboard on a Mac places "Alt" where macOS expects
"Command" and vice versa.

Your keyboard says:    `| Ctrl | Win | Alt | Space | ... |`

macOS sees:            `| Ctrl | Alt | Cmd | Space | ... |`

So pressing what your keyboard labels "Alt" + P actually sends Cmd+P
to macOS — which VS Code captures, exactly the thing we're trying to
avoid.

The clean fix is per-keyboard modifier remapping:

1. System Settings → Keyboard → Keyboard Shortcuts → **Modifier Keys**
2. Pick the external keyboard (not "MacBook internal") from the
   dropdown
3. Swap `Option (⌥)` and `Command (⌘)`

Your MacBook's internal keyboard keeps the Mac layout; the Windows
keyboard behaves like a Windows keyboard.  Termapy's Alt fallbacks
then fire where you expect.

For more surgical control (e.g. per-app remaps), use
[Karabiner-Elements](https://karabiner-elements.pqrs.org/).

## Remote and non-interactive environments

### SSH

Termapy runs fine over SSH.  Make sure the remote terminal type is
set (`TERM=xterm-256color` is a good default).  All mouse / color
features work as long as the local terminal supports them and SSH is
forwarding escape codes verbatim.

### WSL (Windows Subsystem for Linux)

Full support.  Serial ports aren't exposed from Windows to WSL 2 by
default -- use `usbipd` or run termapy from native Windows for serial
access.

### Containers
Termapy runs in a container.  Mount the serial device via
`--device /dev/ttyUSB0`.  The Docker volume mount handles the /dev
node; group membership isn't needed if the container runs as root
(which it does by default).

### CI / headless

`--check` and `--cli --run` work without an attached TTY.  Don't use
the TUI headlessly — it needs a real terminal to render into.

## Reporting environment issues

If you hit something not documented here, the useful diagnostic
bundle is:

- OS + version
- Terminal emulator + version
- `termapy --version`
- `echo $TERM`
- `echo $TERM_PROGRAM` (empty on most terminals; `"vscode"` under
  VS Code; `"Apple_Terminal"` under Terminal.app; etc.)
- Screenshot of the misbehaviour (`Ctrl+S` inside termapy saves an
  SVG that captures the exact render)

Most environment issues reduce to "terminal doesn't forward the key"
or "terminal doesn't support this escape sequence."  The diagnostic
bundle usually points straight at the culprit.
