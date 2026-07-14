"""Default config values and file templates.

Pure data - no logic, no I/O, no dependencies beyond migration version.
"""

from collections.abc import Mapping

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

# Single source of truth for the REPL command prefix default.  Used
# in DEFAULT_CFG, the ``cmd_prefix(cfg)`` helper, and as the default
# for ``prefix`` parameters on ReplEngine / CommandSuggester /
# InternalHandle / _suggest_command so a change here propagates
# everywhere without a textual search for literal "/".
DEFAULT_CMD_PREFIX = "/"

DEFAULT_CFG = {
    "config_version": CURRENT_CONFIG_VERSION,
    # App
    "title": "",
    "border_color": "",
    "max_lines": 10000,
    "default_ui": "tui",
    "vt100_hint": True,
    "cmd_prefix": DEFAULT_CMD_PREFIX,
    "cli_prompt": "$(CFG)> ",
    "cli_completion": True,
    "config_read_only": False,
    "profile_path": "",
    "validate_typed_args": False,
    # Serial -- pyserial constructor args grouped under "serial".
    # Other serial-domain keys (encoding, cmd_delay_ms, protocol,
    # eol) stay flat for now; future grouping decision.
    "serial": {
        "port": "",
        "baud_rate": 115200,
        "custom_baud": False,
        "byte_size": 8,
        "parity": "N",
        "stop_bits": 1,
        "flow_control": "none",
    },
    "encoding": "utf-8",
    "cmd_delay_ms": 0,
    "protocol": "text",
    "ndjson_field_routing": {
        "response_id": "id",
        "error_field": "error",
        "event_field": "event",
    },
    "default_response_timeout_ms": 1000,
    # Connection
    "auto_connect": False,
    "auto_reconnect": False,
    "on_connect_cmd": "",
    "tui_on_connect_cmd": "",
    "cli_on_connect_cmd": "",
    "mcp_on_connect_cmd": "",
    "eol": "\r",
    # Receive newline: how incoming device output is split into lines.
    # "auto" treats CR, LF, and CRLF all as line terminators (TeraTerm's
    # Receive=AUTO -- works for any device); cr/lf/crlf force a single
    # terminator for the rare device that sends a stray CR/LF as data.
    "eol_rx": "auto",
    # Input
    "send_bare_enter": False,
    # Input echo of device commands sent to the wire (bare lines +
    # /term.send).  A separate session-only flag, echo_repl, governs echo
    # of REPL/slash commands (/cfg, /help, ...) -- it has no cfg key
    # because its default is per-host (see TerminalHost._init_flags).
    # (Device-side echo, where the device parrots your bytes, is a device
    # concern and not modelled here.)
    "echo": False,
    "echo_fmt": "[purple]$(CFG)> {cmd}[/]",
    # Logging
    "log_file": "",
    # Diagnostics
    "show_traceback": False,
    # Proto
    "proto_frame_gap_ms": 50,
    "proto_results_template": "{name}_results.json",
    # Display
    "timestamps": False,
    "eol_markers": False,
    "line_no": False,
    "hex": False,
    "request_mode": False,
    "request_err_pattern": r"(?i)^(ERROR|ERR|FAULT)\b",
    # Half-duplex devices echo the command back before answering.  When on,
    # a request_mode response drops a leading line that matches the sent
    # command.  Off by default -- opt in per device, since a device that
    # doesn't echo could in theory emit a first line matching the command.
    "strip_device_echo": False,
    "max_grep_lines": 100,
    # File transfer
    "file_xfer_root": "",
    # Title-bar buttons
    "cfg_enabled": True,
    "run_enabled": True,
    "proto_enabled": True,
    # Record button (next to the REPL prompt; toggles /run.record).
    "record_enabled": True,
    # Custom buttons
    "custom_buttons": [
        {
            "enabled": True,
            "name": "Info",
            "command": "/cfg.info",
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


# ── Config field help (description, valid values or callable) ──────────────────


def _list_ports() -> str:
    """Dynamic: list available serial ports."""
    # OSError covers OS-level enumeration failures (udev / IOKit / WMI);
    # ImportError covers a pyserial install without list_ports support
    # on this platform.
    try:
        from serial.tools.list_ports import comports

        ports = sorted(p.device for p in comports())
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
    try:
        from rich.color import Color

        parsed = Color.parse(resolved)
        # Get truecolor hex for reliable rendering
        triplet = parsed.get_truecolor()
        hex_color = f"#{triplet.red:02x}{triplet.green:02x}{triplet.blue:02x}"
        if resolved != color:
            return f"[green]Color: {color} -> {hex_color}[/]"
        return f"[green]Color: {hex_color}[/]"
    except Exception:
        return f"[bold red]????[/] unknown color: {color}"


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
        "pyserial connection parameters (edit the nested keys below).",
        "Nested: port, baud_rate, byte_size, parity, stop_bits, flow_control.",
    ),
    "port": (
        "Serial port: device name, USB serial number, fallback chain, DEMO, or URL.",
        _list_ports,
    ),
    "baud_rate": (
        "Serial baud rate. Non-standard rates require custom_baud = true.",
        "Standard: 300, 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600",
    ),
    "custom_baud": (
        "Allow non-standard baud rates (modern drivers accept arbitrary rates).",
        "Valid: true, false",
    ),
    "byte_size": ("Data bits per byte.", "Valid: 5, 6, 7, 8"),
    "parity": (
        "Parity bit.",
        "Valid: N (None), E (Even), O (Odd), M (Mark), S (Space)",
    ),
    "stop_bits": ("Stop bits.", "Valid: 1, 1.5, 2"),
    "flow_control": (
        "Flow control mode.",
        "Valid: none, rtscts, xonxoff, manual (shows DTR/RTS/Break buttons)",
    ),
    "encoding": (
        "Character encoding for serial data (set: /term.encoding).",
        "Common: utf-8, latin-1, ascii, cp437",
    ),
    "eol": (
        "Appended to each sent command (set: /term.eol).",
        r'"\r" (CR), "\n" (LF), "\r\n" (CRLF), or a combination; also \0 \x03 \x04.',
    ),
    "eol_rx": (
        "How received device output is split into lines (set: /term.eol.rx).",
        "auto (CR/LF/CRLF all break), cr, lf, or crlf. Default: auto",
    ),
    "cmd_delay_ms": (
        "Delay in ms between commands in multi-command input.",
        "0 = no delay. Positive integer.",
    ),
    "protocol": (
        "Wire format the device speaks.",
        'Valid: "text" (default, line-oriented), "ndjson" (one JSON object per line).',
    ),
    "ndjson_field_routing": (
        "NDJSON: which inbound JSON fields the MCP bridge routes on.",
        "Keys response_id/error_field/event_field; override for non-default fields.",
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
        "Fallback response wait for profile commands lacking response.timeout_ms.",
        "Positive integer ms.  Per-command response.timeout_ms always wins.",
    ),
    # Connection
    "auto_connect": (
        "Connect to the port automatically on startup.",
        "Valid: true, false    ",
    ),
    "auto_reconnect": (
        "Retry connection every 2.5s if the port drops or fails to open.",
        "Valid: true, false",
    ),
    "on_connect_cmd": (
        "Commands to run after connecting (all frontends).",
        r"Separate multiple with \n. Example: /run welcome",
    ),
    "tui_on_connect_cmd": (
        "Extra commands to run after connecting in TUI mode (after on_connect_cmd).",
        r"Separate multiple with \n. Useful for interactive-only setup.",
    ),
    "cli_on_connect_cmd": (
        "Extra commands to run after connecting in CLI mode (after on_connect_cmd).",
        r"Separate multiple with \n. Useful for interactive-only setup.",
    ),
    "mcp_on_connect_cmd": (
        "Extra commands to run after connecting in MCP mode (after on_connect_cmd).",
        r"Separate multiple with \n. Common: 'echo off' to silence device echo.",
    ),
    # Input
    "send_bare_enter": (
        "Send the line ending on an empty Enter (toggle: /term.send_bare_enter).",
        "Valid: true, false",
    ),
    "echo": (
        "Echo device commands sent to the wire (toggle: /term.echo).",
        "Valid: true, false",
    ),
    "echo_fmt": (
        "Rich markup format for echoed commands.",
        "{cmd} is replaced. Example: [purple]> {cmd}[/]",
        _preview_markup,
    ),
    "default_ui": (
        "Default UI mode when launching without a mode flag.",
        "tui, cli, vt100. Default: tui",
    ),
    "vt100_hint": (
        "Show a VS Code key-capture tip in --vt100 mode.",
        "Valid: true, false. VS Code terminals capture some keys before the device.",
    ),
    "cmd_prefix": (
        "Prefix for local REPL commands.",
        "Default: /. Example: ! would make commands like !help",
    ),
    "cli_prompt": (
        "Prompt string for CLI mode input.",
        "Default: '> '",
    ),
    "cli_completion": (
        "Enable CLI tab completion, auto-suggest, and help toolbar.",
        "true, false. Default: true",
    ),
    # Display
    "title": (
        "Title bar center text.",
        "Empty = config filename. Supports $(env.NAME).",
    ),
    "border_color": (
        "Title bar and border color.",
        "CSS name (blue, red, green) or hex (#ff6600). Empty = blue.",
        _preview_color,
    ),
    "max_lines": ("Scrollback buffer size.", "Positive integer. Default: 10000"),
    "timestamps": (
        "Prefix each line with [HH:MM:SS.mmm] (toggle: /term.timestamps).",
        "Valid: true, false",
    ),
    "eol_markers": (
        "Show dim \\r \\n markers in serial output (toggle: /term.eol.markers).",
        "Valid: true, false. Debug mode for line-ending issues.",
    ),
    "line_no": (
        "Show line numbers in serial output (toggle: /term.line_no).",
        "Valid: true, false",
    ),
    "hex": (
        "Display serial I/O as hex bytes instead of text (toggle: /term.hex).",
        "Valid: true, false",
    ),
    "request_mode": (
        "Turn bare device commands into synchronous request/response.",
        "true/false. On: replies become a JSON envelope. See /term.request.",
    ),
    "request_err_pattern": (
        "Regex on request_mode responses that flags a device-side error.",
        r"Match => success=false. Empty disables. Override: /term.request on err=<re>.",
    ),
    "strip_device_echo": (
        "Drop a half-duplex device's echoed command from request_mode replies.",
        "true/false. On: a reply's leading line matching the command is removed.",
    ),
    "max_grep_lines": (
        "Maximum lines shown by /grep.",
        "Positive integer. Default: 100",
    ),
    "file_xfer_root": (
        "Root directory for file transfers (empty = the config's cap/ folder).",
        "Absolute path, or empty for the default.",
    ),
    # Logging
    "log_file": ("Session log file path.", "Empty = <name>.log in config subfolder."),
    "show_traceback": ("Show full stack trace on serial errors.", "Valid: true, false"),
    # Proto
    "proto_frame_gap_ms": (
        "Silence gap (ms) to detect end of a binary frame.",
        "Positive integer. Default: 50",
    ),
    "proto_results_template": (
        "Filename template for proto test JSON results.",
        "Placeholders: {name}, {proto_name}, {datetime}",
    ),
    # Access
    "config_read_only": (
        "Disable Edit button in pickers.",
        "Valid: true, false. /cfg still changes in-memory values.",
    ),
    "profile_path": (
        "Explicit path to a v2 device profile.  MCP-only: --mcp loads it on connect.",
        'Valid: file path (e.g. "termapy_cfg/myrig/myrig.profile.json"), or empty.',
    ),
    "validate_typed_args": (
        "Validate bare-command typed args against the active profile before sending.",
        "true/false. Off = raw access (device errors are truth); on mirrors MCP.",
    ),
    # Title-bar buttons
    "cfg_enabled": (
        "Show the Cfg button in the title bar.",
        "Valid: true, false.",
    ),
    "run_enabled": (
        "Show the Run button in the title bar.",
        "Valid: true, false.",
    ),
    "proto_enabled": (
        "Show the Proto button in the title bar.",
        "Valid: true, false.",
    ),
    "record_enabled": (
        "Show the Record button next to the REPL prompt "
        "(toggles /run.record).",
        "Valid: true, false.",
    ),
    # Custom buttons (nested keys)
    "enabled": ("Whether this button is visible in the toolbar.", "Valid: true, false"),
    "name": ("Button display text.", "Short text shown on the button."),
    "command": (
        "Command to execute when clicked.",
        r"Serial text, /repl command, or \n-separated sequence.",
    ),
    "tooltip": (
        "Hover text for the button.",
        "Shown when mouse hovers over the button.",
    ),
    # Meta
    "config_version": (
        "[bold red]DO NOT EDIT[/] - schema version, managed automatically.",
        "Current version: " + str(CURRENT_CONFIG_VERSION),
    ),
    "custom_buttons": (
        "Array of custom toolbar button objects.",
        "Each has: enabled, name, command, tooltip",
    ),
}


SCRIPT_TEMPLATE = """\
# Script: {name}
# Lines starting with # are comments
# Lines starting with / are REPL commands
# All other lines are sent to the serial device
#
# Example:
# /delay 500ms
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
