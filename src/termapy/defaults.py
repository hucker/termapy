"""Default config values and file templates.

Pure data — no logic, no I/O, no dependencies beyond migration version.
"""

from termapy.migration import CURRENT_CONFIG_VERSION

# ── Validation constants ────────────────────────────────────────────────────────

STANDARD_BAUD_RATES = (
    110, 300, 600, 1200, 2400, 4800, 9600, 14400, 19200, 28800,
    38400, 57600, 115200, 230400, 460800, 921600,
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
