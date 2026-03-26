"""Default config values and file templates.

Pure data - no logic, no I/O, no dependencies beyond migration version.
"""

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

DEFAULT_CFG = {
    "config_version": CURRENT_CONFIG_VERSION,
    # App
    "title": "",
    "border_color": "",
    "max_lines": 10000,
    "cmd_prefix": "/",
    "config_read_only": False,
    "os_cmd_enabled": False,
    # Serial
    "port": "COM4",
    "baud_rate": 115200,
    "byte_size": 8,
    "parity": "N",
    "stop_bits": 1,
    "flow_control": "none",
    "encoding": "utf-8",
    "cmd_delay_ms": 0,
    # Connection
    "auto_connect": False,
    "auto_reconnect": False,
    "on_connect_cmd": "",
    "line_ending": "\r",
    # Input
    "send_bare_enter": False,
    # Input echo
    "echo_input": False,
    "echo_input_fmt": "[purple]> {cmd}[/]",
    # Logging
    "log_file": "",
    # Diagnostics
    "show_traceback": False,
    # Proto test results
    "proto_results_template": "{name}_results.json",
    # Display
    "show_timestamps": False,
    "show_line_endings": False,
    "max_grep_lines": 100,
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

# ── Config field help (description, valid values or callable) ──────────────────


def _list_ports() -> str:
    """Dynamic: list available serial ports."""
    try:
        from serial.tools.list_ports import comports

        ports = sorted(p.device for p in comports())
        return "Available: " + (", ".join(ports) if ports else "(no ports found)")
    except Exception:
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
            base = c[len(prefix):].strip("_").strip()
            if base in COLOR_ALIASES:
                # Map to Rich-style name: dark_orange -> dark_orange3
                rich_name = f"{prefix}_{base}"
                try:
                    from rich.color import Color
                    Color.parse(rich_name)
                    return rich_name
                except Exception:
                    # Fall back to the base hex
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
        label = f"{color} -> {hex_color}" if resolved != color else f"{color} ({hex_color})"
        return f"[on {hex_color}]    [/] {label}"
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
    "port": (
        "Serial port name. Use $(env.NAME|fallback) for portability.",
        _list_ports,
    ),
    "baud_rate": (
        "Serial baud rate.",
        "Standard: 300, 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600",
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
        "Character encoding for serial data.",
        "Common: utf-8, latin-1, ascii, cp437",
    ),
    "line_ending": (
        "Appended to each sent command.",
        r'Valid: "\r" (CR), "\r\n" (CRLF), "\n" (LF)',
    ),
    "cmd_delay_ms": (
        "Delay in ms between commands in multi-command input.",
        "0 = no delay. Positive integer.",
    ),
    # Connection
    "auto_connect": (
        "Connect to the port automatically on startup.",
        "Valid: true, false    ",
    ),
    "auto_reconnect": (
        "Retry connection every second if the port drops.",
        "Valid: true, false",
    ),
    "on_connect_cmd": (
        "Commands to run after connecting.",
        r"Separate multiple with \n. Example: /run welcome",
    ),
    # Input
    "send_bare_enter": (
        "Send line ending when Enter pressed with no input.",
        "Valid: true, false",
    ),
    "echo_input": ("Echo sent commands in the terminal output.", "Valid: true, false"),
    "echo_input_fmt": (
        "Rich markup format for echoed commands.",
        "{cmd} is replaced. Example: [purple]> {cmd}[/]",
        _preview_markup,
    ),
    "cmd_prefix": (
        "Prefix for local REPL commands.",
        "Default: /. Example: ! would make commands like !help",
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
    "show_timestamps": ("Prefix each line with [HH:MM:SS.mmm].", "Valid: true, false"),
    "show_line_endings": (
        "Show dim \\r \\n markers in serial output.",
        "Valid: true, false. Debug mode for line-ending issues.",
    ),
    "max_grep_lines": (
        "Maximum lines shown by /grep.",
        "Positive integer. Default: 100",
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
    "os_cmd_enabled": (
        "Allow /os to run shell commands.",
        "Valid: true, false. Security risk if enabled.",
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
        "Schema version - managed automatically.",
        "Do not edit. Current version: " + str(CURRENT_CONFIG_VERSION),
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
# /sleep 500ms
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
