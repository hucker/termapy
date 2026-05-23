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
# EngineAPI / _suggest_command so a change here propagates
# everywhere without a textual search for literal "/".
DEFAULT_CMD_PREFIX = "/"

DEFAULT_CFG = {
    "config_version": CURRENT_CONFIG_VERSION,
    # App
    "title": "",
    "border_color": "",
    "max_lines": 10000,
    "default_ui": "tui",
    "cmd_prefix": DEFAULT_CMD_PREFIX,
    "cli_prompt": "$(CFG)> ",
    "cli_echo_input": False,
    "cli_completion": True,
    "config_read_only": False,
    "profile_path": "",
    "validate_typed_args": False,
    # Serial -- pyserial constructor args grouped under "serial".
    # Other serial-domain keys (encoding, cmd_delay_ms, protocol,
    # line_ending) stay flat for now; future grouping decision.
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
    "line_ending": "\r",
    # Input
    "send_bare_enter": False,
    # Input echo
    "echo_input": False,
    "echo_input_fmt": "[purple]$(CFG)> {cmd}[/]",
    # Logging
    "log_file": "",
    # Diagnostics
    "show_traceback": False,
    # Proto
    "proto_frame_gap_ms": 50,
    "proto_results_template": "{name}_results.json",
    # Display
    "show_timestamps": False,
    "show_line_endings": False,
    "show_line_numbers": False,
    "hex_mode": False,
    "request_mode": False,
    "request_err_pattern": r"(?i)^(ERROR|ERR|FAULT)\b",
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
    "port": (
        "Port spec: device name (COM4), USB serial number (A1B2C3D4), "
        "fallback chain (A1B2C3D4|COM3), reserved name (DEMO), or URL. "
        "Use $(env.NAME)|fallback for portability.",
        _list_ports,
    ),
    "baud_rate": (
        "Serial baud rate. Non-standard rates require custom_baud = true.",
        "Standard: 300, 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600",
    ),
    "custom_baud": (
        "Allow non-standard baud rates. Modern serial drivers support arbitrary rates; disable (default) to catch typos.",
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
        "Character encoding for serial data.",
        "Common: utf-8, latin-1, ascii, cp437",
    ),
    "line_ending": (
        "Appended to each sent command.",
        r'CR/LF/NUL/ETX/EOT bytes only: "", "\r" (CR), "\n" (LF), '
        r'"\r\n" (CRLF), "\n\r" (LFCR), "\0" (NUL), '
        r'"\u0003" (ETX), "\u0004" (EOT), or any combination.',
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
        'Object with keys response_id, error_field, event_field. '
        'Defaults: {"response_id": "id", "error_field": "error", '
        '"event_field": "event"}.  Override only when the device uses '
        "different field names.",
    ),
    "default_response_timeout_ms": (
        "Fallback wait for any profile-driven command without an "
        "explicit response.timeout_ms.",
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
        "Send line ending when Enter pressed with no input.",
        "Valid: true, false",
    ),
    "echo_input": ("Echo sent commands in the terminal output.", "Valid: true, false"),
    "echo_input_fmt": (
        "Rich markup format for echoed commands.",
        "{cmd} is replaced. Example: [purple]> {cmd}[/]",
        _preview_markup,
    ),
    "default_ui": (
        "Default UI mode when launching without --cli flag.",
        "tui, cli. Default: tui",
    ),
    "cmd_prefix": (
        "Prefix for local REPL commands.",
        "Default: /. Example: ! would make commands like !help",
    ),
    "cli_prompt": (
        "Prompt string for CLI mode input.",
        "Default: '> '",
    ),
    "cli_echo_input": (
        "Echo sent commands in CLI mode (readline already shows input).",
        "true, false. Default: false",
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
    "show_timestamps": ("Prefix each line with [HH:MM:SS.mmm].", "Valid: true, false"),
    "show_line_endings": (
        "Show dim \\r \\n markers in serial output.",
        "Valid: true, false. Debug mode for line-ending issues.",
    ),
    "show_line_numbers": (
        "Show line numbers in serial output.",
        "Valid: true, false",
    ),
    "hex_mode": (
        "Display serial I/O as hex bytes instead of text.",
        "Valid: true, false",
    ),
    "request_mode": (
        "Turn bare device commands into synchronous request/response.",
        "Valid: true, false. When true, bare device commands are sent and "
        "their response is captured into a JSON envelope "
        "{cmd, success, error, elapsed_s, result} -- see /term.request. "
        "Also accepts JSON-shape input ({\"cmd\":\"...\"}) so callers can "
        "stay symmetric. Profile-mapped commands keep their declared "
        "response.format (more-specific wins).",
    ),
    "request_err_pattern": (
        "Regex applied to request_mode response text to detect "
        "device-side errors.",
        r"When the response matches, success=false and the text becomes "
        r"the envelope's error.  Default: (?i)^(ERROR|ERR|FAULT)\b "
        r"(matches 'ERR:', 'ERROR ', 'FAULT', case-insensitive).  Empty "
        r"string disables error detection.  Override per-session via "
        r"/term.request on err=<regex>.",
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
    "profile_path": (
        "Explicit path to a v2 device profile.  MCP-only: --mcp loads it on connect.",
        'Valid: file path (e.g. "termapy_cfg/myrig/myrig.profile.json"), or empty.',
    ),
    "validate_typed_args": (
        "When on, the CLI validates bare-command typed_args against the active "
        "profile's type registry before writing to the wire (mirrors the MCP "
        "behavior).  Default off keeps raw access -- device errors are the "
        "source of truth.  Turn on when iterating on a profile to surface bad "
        "values without a serial round-trip.",
        "Valid: true, false.",
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
