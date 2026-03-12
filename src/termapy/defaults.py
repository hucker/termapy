"""Default config values and file templates.

Pure data — no logic, no I/O, no dependencies beyond migration version.
"""

from termapy.migration import CURRENT_CONFIG_VERSION

DEFAULT_CFG = {
    "config_version": CURRENT_CONFIG_VERSION,
    # App
    "title": "",
    "app_border_color": "",
    "max_lines": 10000,
    "repl_prefix": "/",
    "read_only": False,
    "os_cmd_enabled": False,
    # Serial
    "port": "COM4",
    "baud_rate": 115200,
    "byte_size": 8,
    "parity": "N",
    "stop_bits": 1,
    "flow_control": "none",
    "encoding": "utf-8",
    "inter_cmd_delay_ms": 0,
    # Connection
    "auto_connect": False,
    "auto_reconnect": False,
    "auto_connect_cmd": "",
    "line_ending": "\r",
    # Input echo
    "echo_cmd": False,
    "echo_cmd_fmt": "[purple]> {cmd}[/]",
    # Logging
    "log_file": "",
    # Diagnostics
    "exception_traceback": False,
    # Display
    "show_timestamps": False,
    "show_eol": False,
    "max_grep_lines": 100,
    # Custom buttons
    "custom_buttons": [
        {"enabled": True, "name": "Info", "command": "/info", "tooltip": "Project info"},
        {"enabled": False, "name": "Btn2", "command": "", "tooltip": "Custom button 2"},
        {"enabled": False, "name": "Btn3", "command": "", "tooltip": "Custom button 3"},
        {"enabled": False, "name": "Btn4", "command": "", "tooltip": "Custom button 4"},
    ],
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
#
# Each [[test]] section is one send/expect step:
#
# [[test]]
# label = "Read holding registers"
# send = "01 03 00 00 00 0A C5 CD"
# expect = "01 03 14 ** ** ** ** ** ** ** ** ** ** ** ** ** ** ** ** ** ** **"
#
# [[test]]
# label = "AT query"
# send = '"AT+VERSION?\\r"'
# expect = '"V1." ** ** "\\r"'
#
# Use ** for wildcard bytes (match anything).
# Use "quoted strings" for text with optional \\r \\n \\t escapes.
# Per-step overrides: timeout, delay, flush, cmd

[settings]
timeout = "1000ms"
frame_gap = "50ms"

[[test]]
label = "Example step"
send = "01 02 03"
expect = "01 02 03"
"""
