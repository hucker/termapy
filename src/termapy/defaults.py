"""Default config values and file templates.

Pure data - no logic, no I/O, no dependencies beyond migration version.
"""

from collections.abc import Collection, Mapping

from termapy.migration import CURRENT_CONFIG_VERSION

# ── Validation constants ────────────────────────────────────────────────────────

STANDARD_BAUD_RATES = (
    110,
    300,
    600,
    1200,
    2400,
    4800,
    9600,
    14400,
    19200,
    28800,
    38400,
    57600,
    115200,
    230400,
    460800,
    921600,
)
VALID_BYTE_SIZES = {5, 6, 7, 8}
VALID_PARITIES = {"N", "E", "O", "M", "S"}
VALID_STOP_BITS = {1, 1.5, 2}
VALID_FLOW_CONTROLS = {"none", "rtscts", "xonxoff", "manual"}
# How termapy prints JSON (request mode, --json): one line, indented, or
# indented and highlighted.  The order is /term.json's cycle order.
JSON_STYLES = ("compact", "indent", "color")
# Where the custom buttons sit: in the bottom bar, or a column down the
# left or right edge of the output pane (room for many more).
CUSTOM_BUTTON_SIDES = ("bottom", "left", "right")
# The frontends ``default_ui`` can start in.
UI_MODES = ("tui", "cli", "vt100")
# Every cfg key whose value is one of a fixed set, spelled as the file
# holds it: validate_config warns on anything else, the config editor
# marks it red, and /cfg matches a typed value in any case
# (config.cfg_choice).
CFG_CHOICES: dict[str, Collection[object]] = {
    "byte_size": VALID_BYTE_SIZES,
    "parity": VALID_PARITIES,
    "stop_bits": VALID_STOP_BITS,
    "flow_control": VALID_FLOW_CONTROLS,
    "default_ui": UI_MODES,
    "json_style": JSON_STYLES,
    "custom_buttons_side": CUSTOM_BUTTON_SIDES,
}
# Cfg keys termapy writes itself, never the user: the config editor heads
# their help line with a red DO NOT EDIT.
MANAGED_CFG_KEYS = frozenset({"config_version"})

# Single source of truth for the REPL command prefix default.  Used
# in DEFAULT_CFG, the ``cmd_prefix(cfg)`` helper, and as the default
# for ``prefix`` parameters on ReplEngine / CommandSuggester /
# InternalHandle so a change here propagates
# everywhere without a textual search for literal "/".
DEFAULT_CMD_PREFIX = "/"

# The defaults, and the layout of every config file: termapy writes a config
# in this order (``config.ordered_cfg``), so a file reads group by group,
# the settings edited most first and the long button list last.  Related
# settings are grouped by order, not nested (decision 2026-10-30); only the
# pyserial arguments sit in a dict of their own.
DEFAULT_CFG = {
    "config_version": CURRENT_CONFIG_VERSION,
    # Identity
    "title": "",
    "border_color": "",
    # Port -- pyserial constructor arguments
    "serial": {
        "port": "",
        "baud_rate": 115200,
        "custom_baud": False,
        "byte_size": 8,
        "parity": "N",
        "stop_bits": 1,
        "flow_control": "none",
    },
    # Connecting
    "auto_connect": False,
    "auto_reconnect": False,
    "on_connect_cmd": "",
    "tui_on_connect_cmd": "",
    "cli_on_connect_cmd": "",
    "mcp_on_connect_cmd": "",
    # Line text: what goes out, how it comes back
    "encoding": "utf-8",
    "eol": "\r",
    # Transmit delay (TeraTerm's msec/char and msec/line): a device line
    # goes out one character at a time, this many ms apart, and waits this
    # long after its line ending.  For a device that drops bytes arriving
    # back to back.  Binary (file transfers, /proto, /raw) is never paced.
    "tx_char_delay_ms": 0,
    "tx_line_delay_ms": 0,
    # Receive newline: how incoming device output is split into lines.
    # "auto" treats CR, LF, and CRLF all as line terminators (TeraTerm's
    # Receive=AUTO -- works for any device); cr/lf/crlf force a single
    # terminator for the rare device that sends a stray CR/LF as data.
    "eol_rx": "auto",
    "eol_markers": False,
    "send_bare_enter": False,
    "cmd_delay_ms": 0,
    # A paste of several lines into the prompt sends them all, one after
    # another as if typed, this many ms apart (TeraTerm's paste, and its
    # PasteDelayPerLine default); off, only the first line lands.
    "multiline_paste": True,
    "multiline_paste_delay_ms": 10,
    # The TUI's Ctrl+C (nothing selected), Ctrl+D and Ctrl+Z (empty prompt)
    # and Escape (nothing running, empty prompt) go to the device, as in
    # any terminal.  false keeps them termapy's, for a device a stray
    # control byte would upset.
    "control_keys": True,
    # Input echo of device commands sent to the wire (bare lines +
    # /term.send).  A separate session-only flag, echo_repl, governs echo
    # of REPL/slash commands (/cfg, /help, ...) -- it has no cfg key
    # because its default is per-host (see TerminalHost._init_flags).
    # (Device-side echo, where the device parrots your bytes, is a device
    # concern and not modeled here.)
    "echo": False,
    "echo_fmt": "[purple]$(CFG)> {cmd}[/]",
    # Display
    "timestamps": False,
    "line_no": False,
    "hex": False,
    "max_lines": 10000,
    "max_grep_lines": 100,
    # Device replies
    "protocol": "text",
    "ndjson_field_routing": {
        "response_id": "id",
        "error_field": "error",
        "event_field": "event",
    },
    "request_mode": False,
    "request_err_pattern": r"(?i)^(ERROR|ERR|FAULT)\b",
    # Half-duplex devices echo the command back before answering.  When on,
    # a request_mode response drops a leading line that matches the sent
    # command.  Off by default -- opt in per device, since a device that
    # doesn't echo could in theory emit a first line matching the command.
    "strip_device_echo": False,
    "json_style": "compact",
    # Device profile
    "profile_path": "",
    "validate_typed_args": False,
    "default_response_timeout_ms": 1000,
    # Files
    "log_file": "",
    "file_xfer_root": "",
    # Protocol tests
    "proto_frame_gap_ms": 50,
    "proto_results_template": "{name}_results.json",
    # Startup and frontends
    "default_ui": "tui",
    "quiet_start": True,
    "vt100_hint": True,
    "cmd_prefix": DEFAULT_CMD_PREFIX,
    "cli_prompt": "$(CFG)> ",
    "cli_completion": True,
    "show_traceback": False,
    # Buttons: the title bar's, Record next to the REPL prompt (toggles
    # /run.record), the pickers' Edit, then the custom buttons.
    "cfg_enabled": True,
    "run_enabled": True,
    "proto_enabled": True,
    "record_enabled": True,
    "config_read_only": False,
    "custom_buttons_side": "bottom",
    "custom_buttons": [
        {
            "enabled": True,
            "name": "Info",
            "command": "{prefix}cfg.info",
            "tooltip": "Project info",
        },
        {"enabled": False, "name": "Btn2", "command": "", "tooltip": "Custom button 2"},
        {"enabled": False, "name": "Btn3", "command": "", "tooltip": "Custom button 3"},
        {"enabled": False, "name": "Btn4", "command": "", "tooltip": "Custom button 4"},
    ],
}


def default_cfg() -> dict:
    """Return a fresh deep-copy of ``DEFAULT_CFG``.

    Use this instead of ``dict(DEFAULT_CFG)`` for any cfg that will
    be mutated.  Shallow ``dict(...)`` copies leave the nested
    sub-dicts (``serial``, ``ndjson_field_routing``) shared with
    the module-level ``DEFAULT_CFG``, so mutations like
    ``cfg["serial"]["port"] = "COM3"`` would corrupt the global
    default.  ``deepcopy`` is the correct primitive.
    """
    import copy

    return copy.deepcopy(DEFAULT_CFG)


def cmd_prefix(cfg: Mapping) -> str:
    """Return ``cfg["cmd_prefix"]`` with the project default as fallback.

    Replaces the repeated ``cfg.get("cmd_prefix", "/")`` idiom scattered
    across 15+ call sites.  Both this helper and ``DEFAULT_CMD_PREFIX``
    resolve from the same constant, so the literal ``"/"`` lives in
    exactly one place.
    """
    return cfg.get("cmd_prefix", DEFAULT_CMD_PREFIX)


def quiet_start(cfg: Mapping) -> bool:
    """True when the startup shows only the connection and any problem.

    ``cfg["quiet_start"]`` with the project default as fallback, read by
    everything the setting quiets (the load report, the connect line), so
    a cfg dict built without the key still behaves like a loaded one.
    """
    return bool(cfg.get("quiet_start", DEFAULT_CFG["quiet_start"]))


# ── Config field help (description, valid values or callable) ──────────────────


def _list_ports() -> str:
    """Dynamic: list available serial ports."""
    # OSError covers OS-level enumeration failures (udev / IOKit / WMI);
    # ImportError covers a pyserial install without list_ports support
    # on this platform.
    try:
        from serial.tools.list_ports import comports

        ports = sorted(port.device for port in comports())
        return "Available: " + (", ".join(ports) if ports else "(no ports found)")
    except (OSError, ImportError):
        return "(cannot list ports)"


# Common color names that Rich doesn't recognize -> hex equivalents
COLOR_ALIASES: dict[str, str] = {
    "brown": "#8B4513",
    "pink": "#FFB6C1",
    "orange": "#FFA500",
    "gray": "#808080",
    "grey": "#808080",
    "silver": "#C0C0C0",
    "olive": "#808000",
    "maroon": "#800000",
    "navy": "#000080",
    "teal": "#008080",
    "aqua": "#00FFFF",
    "lime": "#00FF00",
    "fuchsia": "#FF00FF",
    "coral": "#FF7F50",
    "salmon": "#FA8072",
    "gold": "#FFD700",
    "indigo": "#4B0082",
    "crimson": "#DC143C",
    "tomato": "#FF6347",
    "chocolate": "#D2691E",
    "peru": "#CD853F",
    "sienna": "#A0522D",
    "beige": "#F5F5DC",
    "ivory": "#FFFFF0",
    "lavender": "#E6E6FA",
    "khaki": "#F0E68C",
    "sky_blue": "#87CEEB",
    "skyblue": "#87CEEB",
    # light_ variants Rich doesn't have
    "light_blue": "#ADD8E6",
    "light_red": "#FF6B6B",
    "light_yellow": "#FFFFE0",
    "light_purple": "#D8BFD8",
    "light_magenta": "#FF77FF",
    "light_orange": "#FFD39B",
    "light_brown": "#C4A882",
    # dark_ variants Rich doesn't have
    "dark_brown": "#5C3317",
    "dark_pink": "#C71585",
    "dark_gray": "#404040",
    "dark_grey": "#404040",
    "light_gray": "#C0C0C0",
    "light_grey": "#C0C0C0",
}


def resolve_color(color: str) -> str:
    """Resolve a color name, falling back to COLOR_ALIASES for common names.

    Also handles light/dark prefixes: lightpink -> light pink -> #FFB6C1
    brightened/darkened via Rich's color system.
    """
    c = color.lower().strip()
    # Direct alias match
    if c in COLOR_ALIASES:
        return COLOR_ALIASES[c]
    # Handle light/dark prefix with base color alias
    for prefix in ("light", "dark"):
        if c.startswith(prefix):
            base = c[len(prefix) :].strip("_").strip()
            if base in COLOR_ALIASES:
                # Map to Rich-style name: dark_orange -> dark_orange3
                rich_name = f"{prefix}_{base}"
                try:
                    from rich.color import Color, ColorParseError

                    Color.parse(rich_name)
                    return rich_name
                except ColorParseError:
                    # Rich doesn't know this prefixed name -- fall back
                    # to the base hex from the alias table.
                    return COLOR_ALIASES[base]
    return color


def _preview_color(raw_val: str) -> str:
    """Preview a color value as a Rich swatch."""
    color = raw_val.strip().strip('"').strip()
    if not color:
        return ""
    resolved = resolve_color(color)
    from termapy.markup import escape_markup

    try:
        from rich.color import Color

        parsed = Color.parse(resolved)
        # Get truecolor hex for reliable rendering
        triplet = parsed.get_truecolor()
        hex_color = f"#{triplet.red:02x}{triplet.green:02x}{triplet.blue:02x}"
        if resolved != color:
            return f"[green]Color: {escape_markup(color)} -> {escape_markup(hex_color)}[/]"
        return f"[green]Color: {escape_markup(hex_color)}[/]"
    except Exception:
        return f"[bold red]????[/] unknown color: {escape_markup(color)}"


def _preview_markup(raw_val: str) -> str:
    """Preview a Rich markup format string with sample data."""
    fmt = raw_val.strip().strip('"').strip()
    if not fmt:
        return ""
    try:
        preview = fmt.replace("{cmd}", "AT+INFO")
        return f"Preview: {preview}"
    except Exception:
        return ""


# (description, valid_values_or_callable, optional_preview_callable)
CFG_HELP: dict[str, tuple] = {
    # Serial
    "serial": (
        "pyserial connection settings; edit the nested keys below.",
        "Nested: port, baud_rate, byte_size, parity, stop_bits, flow_control.",
    ),
    "port": (
        "Port name, USB serial number, SN|COM3 fallback, DEMO, or a URL.",
        _list_ports,
    ),
    "baud_rate": (
        "Line speed in bits per second; must match the device.",
        "Standard: 110 to 921600, 115200 most common. Others: custom_baud.",
    ),
    "custom_baud": (
        "Allow a non-standard baud_rate; most modern drivers take any rate.",
        "true, false",
    ),
    "byte_size": (
        "Data bits per character; 8 for nearly every modern device.",
        "5, 6, 7, 8. Default: 8",
    ),
    "parity": (
        "Parity bit on each character; must match the device, usually N.",
        "N (None), E (Even), O (Odd), M (Mark), S (Space). Default: N",
    ),
    "stop_bits": (
        "Stop bits ending each character; must match the device, usually 1.",
        "1, 1.5, 2. Default: 1",
    ),
    "flow_control": (
        "How the port paces data: RTS/CTS wires, XON/XOFF bytes, or neither.",
        "none, rtscts, xonxoff, manual (shows DTR/RTS/Break buttons)",
    ),
    "encoding": (
        "Turns device bytes into text and back (set: {prefix}term.encoding).",
        "Common: utf-8, latin-1, ascii, cp437. Default: utf-8",
    ),
    "eol": (
        "Line ending added to each line sent to the device ({prefix}term.eol).",
        r'"\r" CR (default), "\n" LF, "\r\n" CRLF; also \0 \x03 \x04.',
    ),
    "tx_char_delay_ms": (
        "Pause in ms after each character of a line sent to the device.",
        "0 = off. For a device that drops bytes sent back to back.",
    ),
    "tx_line_delay_ms": (
        "Pause in ms after each line sent to the device.",
        "0 = off. Lines only: binary sends are never paced.",
    ),
    "eol_rx": (
        "How device output is split into lines (set: {prefix}term.eol.rx).",
        "auto (CR, LF and CRLF all end a line), cr, lf, crlf. Default: auto",
    ),
    "cmd_delay_ms": (
        "Pause in ms after each of the TUI's on-connect commands.",
        "0 = no pause. Non-negative integer.",
    ),
    "multiline_paste": (
        "Pasting several lines into the prompt sends each one, as if typed.",
        "true, false. Off: only the first line lands. Escape stops a paste.",
    ),
    "multiline_paste_delay_ms": (
        "Pause in ms between the lines of a multi-line paste.",
        "0 = no pause. Non-negative integer. Default: 10 (TeraTerm's).",
    ),
    "control_keys": (
        "Ctrl+C/D/Z and Esc go to the device when termapy has no use for them.",
        "true, false ({prefix}term.control_keys). Off: copy, delete, clear only.",
    ),
    "protocol": (
        "Wire format the device speaks.",
        '"text" (default, line-oriented), "ndjson" (one JSON object per line).',
    ),
    "ndjson_field_routing": (
        "NDJSON: which inbound JSON fields the MCP bridge routes on.",
        "Keys response_id, error_field, event_field; change for other names.",
    ),
    "response_id": (
        "NDJSON field name carrying a reply's correlation id.",
        "Default: id",
    ),
    "error_field": (
        "NDJSON field name that marks a message as an error.",
        "Default: error",
    ),
    "event_field": (
        "NDJSON field name that marks an unsolicited event.",
        "Default: event",
    ),
    "default_response_timeout_ms": (
        "Reply wait for a profile command that sets no response.timeout_ms.",
        "Positive integer ms. Default: 1000. A command's own timeout wins.",
    ),
    # Connection
    "auto_connect": (
        "Open the port as soon as termapy starts.",
        "true, false",
    ),
    "auto_reconnect": (
        "Retry every 2.5 s when the port drops or fails to open.",
        "true, false",
    ),
    "on_connect_cmd": (
        "Command run after every connect, in every frontend.",
        "One command; for several, a .run file. Example: {prefix}run welcome",
    ),
    "tui_on_connect_cmd": (
        "Extra command after connecting in the TUI, after on_connect_cmd.",
        "One command; for several, a .run file. For TUI-only setup.",
    ),
    "cli_on_connect_cmd": (
        "Extra command after connecting in the CLI, after on_connect_cmd.",
        "One command; for several, a .run file. For CLI-only setup.",
    ),
    "mcp_on_connect_cmd": (
        "Extra command after connecting under MCP, after on_connect_cmd.",
        "One command; for several, a .run file. Common: device echo off.",
    ),
    # Input
    "send_bare_enter": (
        "Enter on an empty prompt sends a line ending ({prefix}term.send_bare_enter).",
        "true, false",
    ),
    "echo": (
        "Show the lines you send to the device in the output ({prefix}term.echo).",
        "true, false",
    ),
    "echo_fmt": (
        "Rich markup for a line shown by echo; {cmd} is the line sent.",
        "{cmd} is replaced. Example: [purple]> {cmd}[/]",
        _preview_markup,
    ),
    "default_ui": (
        "Frontend to start in when no mode flag (--cli, --vt100) is given.",
        "tui, cli, vt100. Default: tui",
    ),
    "vt100_hint": (
        "In --vt100 mode under VS Code, show a tip about captured keys.",
        "true, false. VS Code terminals capture some keys before the device.",
    ),
    "quiet_start": (
        "Show only the connection and any problem at start; detail in the log.",
        "true, false. False also shows the load table and full connect line.",
    ),
    "cmd_prefix": (
        "Starts a termapy command; any other line goes to the device.",
        "Default: /. Example: ! makes commands like !help",
    ),
    "cli_prompt": (
        "Prompt shown for input in CLI mode.",
        "Default: '$(CFG)> ', the config name. $(...) variables expand.",
    ),
    "cli_completion": (
        "CLI tab completion, history auto-suggest and the help toolbar.",
        "true, false. Default: true",
    ),
    # Display
    "title": (
        "Text in the center of the title bar.",
        "Empty = config filename. Supports $(env.NAME).",
    ),
    "border_color": (
        "Color of the title bar and the output border.",
        "CSS name (blue, red, green) or hex (#ff6600). Empty = blue.",
        _preview_color,
    ),
    "max_lines": (
        "Lines the output pane keeps; past this the oldest drop off.",
        "Positive integer. Default: 10000",
    ),
    "timestamps": (
        "Prefix each line with [HH:MM:SS.mmm] (toggle: {prefix}term.timestamps).",
        "true, false",
    ),
    "eol_markers": (
        "Show dim \\r \\n markers in serial output ({prefix}term.eol.markers).",
        "true, false. For tracking down line-ending problems.",
    ),
    "line_no": (
        "Show line numbers in serial output (toggle: {prefix}term.line_no).",
        "true, false",
    ),
    "hex": (
        "Show received bytes as hex instead of text (toggle: {prefix}term.hex).",
        "true, false",
    ),
    "request_mode": (
        "Every answer, from the device or termapy, comes as a JSON envelope.",
        "true, false. See {prefix}term.request.",
    ),
    "request_err_pattern": (
        "Regex that marks a request_mode reply as a device-side error.",
        r"Match: success=false. Empty: off. {prefix}term.request on err=<re>",
    ),
    "json_style": (
        "How JSON prints in request_mode and --json (set: {prefix}term.json).",
        "compact (one line), indent, color (indented and highlighted).",
    ),
    "strip_device_echo": (
        "Drop a half-duplex device's echoed command from request_mode replies.",
        "true, false. On: a first reply line matching the command is dropped.",
    ),
    "max_grep_lines": (
        "Most lines {prefix}grep shows for one search.",
        "Positive integer. Default: 100",
    ),
    "file_xfer_root": (
        "Folder XMODEM/YMODEM use; empty = the config's cap/ folder.",
        "Absolute path, or empty. Set: {prefix}xfer.root",
    ),
    # Logging
    "log_file": (
        "The TUI's session log; empty = <config>.log in the config's folder.",
        "MCP logs to mcp/session.log; the CLI keeps no log.",
    ),
    "show_traceback": ("Show full stack trace on serial errors.", "true, false"),
    # Proto
    "proto_frame_gap_ms": (
        "Silence in ms that ends a binary frame in a protocol test.",
        "Positive integer. Default: 50",
    ),
    "proto_results_template": (
        "File name for a protocol test's JSON results.",
        "Placeholders: {name}, {proto_name}, {datetime}, {date}, {time}",
    ),
    # Access
    "config_read_only": (
        "Grey out Edit, Rename and Delete in the cfg, run and proto pickers.",
        "true, false. {prefix}cfg still changes in-memory values.",
    ),
    "profile_path": (
        "Device profile MCP loads on connect; empty = found by convention.",
        'File path, e.g. "termapy_cfg/myrig/myrig.profile.json", or empty.',
    ),
    "validate_typed_args": (
        "Check typed device commands against the profile before sending.",
        "true, false. Off: the device judges (raw access); on: as MCP does.",
    ),
    # Title-bar buttons
    "cfg_enabled": (
        "Show the Cfg button in the title bar.",
        "true, false",
    ),
    "run_enabled": (
        "Show the Run button in the title bar.",
        "true, false",
    ),
    "proto_enabled": (
        "Show the Proto button in the title bar.",
        "true, false",
    ),
    "record_enabled": (
        "Show the Record button by the prompt (toggles {prefix}run.record).",
        "true, false",
    ),
    # Custom buttons (nested keys)
    "enabled": ("Show this button on the toolbar.", "true, false"),
    "name": ("Label shown on the button.", "Short text."),
    "command": (
        "What a click runs: text for the device or a termapy command.",
        r"One line, or several separated by \n.",
    ),
    "tooltip": (
        "Hover text for the button.",
        "Shown when the mouse rests on the button.",
    ),
    # Meta
    "config_version": (
        "Config format version; termapy updates it when it migrates the file.",
        "Current version: " + str(CURRENT_CONFIG_VERSION),
    ),
    "custom_buttons_side": (
        "Where custom buttons go: the bottom bar or a left or right column.",
        f"{', '.join(CUSTOM_BUTTON_SIDES)}. A column scrolls when it holds more than fit.",
    ),
    "custom_buttons": (
        "Toolbar buttons of your own, each running a command.",
        "Each has: enabled, name, command, tooltip. See help: custom-buttons.",
    ),
}


SCRIPT_TEMPLATE = """\
# Script: {name}
# Lines starting with # are comments
# Lines starting with {prefix} are REPL commands
# All other lines are sent to the serial device
#
# Example:
# {prefix}delay 500ms
# AT+INFO
"""

PROTO_TEMPLATE = """\
# Protocol Test Script
# Rename this file to something meaningful, e.g. read_registers.pro
#
# Directives (optional):
#   timeout = "1000ms"     # default expect timeout
#   frame_gap = "50ms"     # silence gap to detect end of frame
#   strip_ansi = true      # strip ANSI escapes from responses
#   json_file = "{name}-{proto_name}-{datetime}.json"  # JSON result filename
#
# Each [[test]] section is one send/expect step:
#
# [[test]]
# name = "Read holding registers"
# send = "01 03 00 00 00 0A C5 CD"
# expect = "01 03 14 ** ** ** ** ** ** ** ** ** ** ** ** ** ** ** ** ** ** **"
#
# [[test]]
# name = "AT query"
# send = '"AT+VERSION?\\r"'
# expect = '"V1." ** ** "\\r"'
#
# Use ** for wildcard bytes (match anything).
# Use "quoted strings" for text with optional \\r \\n \\t escapes.
# Per-step overrides: timeout, delay, flush, cmd
#
[settings]
timeout = "1000ms"
frame_gap = "50ms"

[[test]]
name = "Example step"
send = "01 02 03"
expect = "01 02 03"
# Inline format specs (optional, decode bytes into named columns):
# send_fmt = "Addr:H1 Cmd:H2 Data:H3"
# expect_fmt = "Addr:H1 Cmd:H2 Data:H3"
"""
