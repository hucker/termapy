"""The config schema, one table, and the file templates.

``CFG_KEYS`` holds every key once: its default, its kind and what bounds it,
and its help.  ``DEFAULT_CFG``, ``CFG_HELP``, ``CFG_CHOICES`` and
``MANAGED_CFG_KEYS`` are views of it, so a new key is one row (registry
``cfg-schema-one-table``); ``config.check_cfg_value`` reads the kinds.

Pure data - no logic, no I/O, no dependencies beyond migration version.
"""

import copy
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Any

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
# The spaces per level JSON is indented by on screen; 0 is one line.  The
# order is /term.json_fmt.indent's cycle order.
JSON_INDENTS = (0, 2, 4)
# How loud commands are, quietest first.  The order is /term.output's cycle order.
OUTPUT_LEVELS = ("silent", "quiet", "normal", "verbose")
DEFAULT_OUTPUT_LEVEL = "normal"
# Where the custom buttons sit: in the bottom bar, or a column down the
# left or right edge of the output pane (room for many more).
CUSTOM_BUTTON_SIDES = ("bottom", "left", "right")
# The frontends ``default_ui`` can start in.
UI_MODES = ("tui", "cli", "vt100")
# How device output is split into lines (TeraTerm's Receive newline:
# AUTO/CR/LF/CR+LF).
RX_NEWLINE_MODES = ("auto", "cr", "lf", "crlf")
# The wire format the device speaks.
PROTOCOLS = ("text", "ndjson")

# Single source of truth for the REPL command prefix default.  Used
# in DEFAULT_CFG, the ``cmd_prefix(cfg)`` helper, and as the default
# for ``prefix`` parameters on ReplEngine / CommandSuggester /
# InternalHandle so a change here propagates
# everywhere without a textual search for literal "/".
DEFAULT_CMD_PREFIX = "/"

# ── What the schema's rows call ─────────────────────────────────────────────


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
    from rich.color import Color, ColorParseError

    from termapy.markup import escape_markup

    try:
        parsed = Color.parse(resolved)
        # Get truecolor hex for reliable rendering
        triplet = parsed.get_truecolor()
        hex_color = f"#{triplet.red:02x}{triplet.green:02x}{triplet.blue:02x}"
        if resolved != color:
            return f"[green]Color: {escape_markup(color)} -> {escape_markup(hex_color)}[/]"
        return f"[green]Color: {escape_markup(hex_color)}[/]"
    except ColorParseError:
        return f"[bold red]????[/] unknown color: {escape_markup(color)}"


def _preview_markup(raw_val: str) -> str:
    """Preview a Rich markup format string with sample data."""
    fmt = raw_val.strip().strip('"').strip()
    if not fmt:
        return ""
    return f"Preview: {fmt.replace('{cmd}', 'AT+INFO')}"


# ── The schema ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CfgKey:
    """One config key: everything termapy knows about it, in one place.

    Attributes:
        path: The key as a dotted path (``serial.baud_rate``); a section's
            row comes before its leaves.
        default: The value a config without the key has; ``None`` for a
            section, whose default is its leaves.
        kind: Which of ``config.check_cfg_value``'s checks it gets:
            ``bool``, ``int``, ``str``, ``enum``, ``path``, ``regex``,
            ``encoding``, ``command``, ``port``, ``baud``, ``eol``,
            ``section`` or ``list``.
        choices: An ``enum``'s values, spelled as the file holds them.
        min: The least an ``int`` may be; ``None`` for no bound.
        help: One line on what it does, for the config editor and
            ``/help <key>``.  At most 70 characters, shown with ``{prefix}`` filled in.
        hint: Its valid values in one line, or a callable that lists them.
        preview: Shows a typed value in the config editor (a color, a format).
        managed: termapy writes it, never the user: the editor heads its
            help line with a red DO NOT EDIT and ``/cfg`` refuses it.
        session_flag: The key starts the session flag of the same name
            (``ctx.ns("flags")``): every load seeds the flag from it, and
            ``/cfg`` sets both, so the key is what its setter reports.
    """

    path: str
    default: Any
    kind: str
    choices: Collection[object] = ()
    min: int | None = None
    help: str = ""
    hint: str | Callable[[], str] = ""
    preview: Callable[[str], str] | None = None
    managed: bool = False
    session_flag: bool = False


# Every key, and the layout of every config file: termapy writes a config in
# this order (``config.ordered_cfg``), so a file reads group by group, the
# settings edited most first and the long button list last.  Related settings
# are grouped by order, not nested (decision 2026-10-30); only the pyserial
# arguments sit in a dict of their own.  A new key goes in its group here.
CFG_KEYS: tuple[CfgKey, ...] = (
    CfgKey(
        "config_version",
        CURRENT_CONFIG_VERSION,
        "int",
        help="Config format version; termapy updates it when it migrates the file.",
        hint="Current version: " + str(CURRENT_CONFIG_VERSION),
        managed=True,
    ),
    # Identity
    CfgKey(
        "title",
        "",
        "str",
        help="Text in the center of the title bar.",
        hint="Empty = config filename. Supports $(env.NAME).",
    ),
    CfgKey(
        "border_color",
        "",
        "str",
        help="Color of the title bar and the output border.",
        hint="CSS name (blue, red, green) or hex (#ff6600). Empty = blue.",
        preview=_preview_color,
    ),
    # Port -- pyserial constructor arguments
    CfgKey(
        "serial",
        None,
        "section",
        help="pyserial connection settings; edit the nested keys below.",
        hint="Nested: port, baud_rate, byte_size, parity, stop_bits, flow_control.",
    ),
    CfgKey(
        "serial.port",
        "",
        "port",
        help="Port name, USB serial number, SN|COM3 fallback, DEMO, or a URL.",
        hint=_list_ports,
    ),
    CfgKey(
        "serial.baud_rate",
        115200,
        "baud",
        help="Line speed in bits per second; must match the device.",
        hint="Standard: 110 to 921600, 115200 most common. Others: custom_baud.",
    ),
    CfgKey(
        "serial.custom_baud",
        False,
        "bool",
        help="Allow a baud_rate off the standard list; without it one is refused.",
        hint="true, false",
    ),
    CfgKey(
        "serial.byte_size",
        8,
        "enum",
        choices=VALID_BYTE_SIZES,
        help="Data bits per character; 8 for nearly every modern device.",
        hint="5, 6, 7, 8. Default: 8",
    ),
    CfgKey(
        "serial.parity",
        "N",
        "enum",
        choices=VALID_PARITIES,
        help="Parity bit on each character; must match the device, usually N.",
        hint="N (None), E (Even), O (Odd), M (Mark), S (Space). Default: N",
    ),
    CfgKey(
        "serial.stop_bits",
        1,
        "enum",
        choices=VALID_STOP_BITS,
        help="Stop bits ending each character; must match the device, usually 1.",
        hint="1, 1.5, 2. Default: 1",
    ),
    CfgKey(
        "serial.flow_control",
        "none",
        "enum",
        choices=VALID_FLOW_CONTROLS,
        help="How the port paces data: RTS/CTS wires, XON/XOFF bytes, or neither.",
        hint="none, rtscts, xonxoff, manual (shows DTR/RTS/Break buttons)",
    ),
    # Connecting
    CfgKey(
        "auto_connect",
        False,
        "bool",
        help="Open the port as soon as termapy starts.",
        hint="true, false",
    ),
    CfgKey(
        "auto_reconnect",
        False,
        "bool",
        help="Retry every 2.5 s when the port drops or fails to open.",
        hint="true, false",
    ),
    CfgKey(
        "on_connect_cmd",
        "",
        "command",
        help="Command run after every connect, in every frontend.",
        hint="One command; for several, a .run file. Example: {prefix}run welcome",
    ),
    CfgKey(
        "tui_on_connect_cmd",
        "",
        "command",
        help="Extra command after connecting in the TUI, after on_connect_cmd.",
        hint="One command; for several, a .run file. For TUI-only setup.",
    ),
    CfgKey(
        "cli_on_connect_cmd",
        "",
        "command",
        help="Extra command after connecting in the CLI, after on_connect_cmd.",
        hint="One command; for several, a .run file. For CLI-only setup.",
    ),
    CfgKey(
        "mcp_on_connect_cmd",
        "",
        "command",
        help="Extra command after connecting under MCP, after on_connect_cmd.",
        hint="One command; for several, a .run file. Common: device echo off.",
    ),
    # Line text: what goes out, how it comes back
    CfgKey(
        "encoding",
        "utf-8",
        "encoding",
        help="Turns device bytes into text and back (set: {prefix}term.encoding).",
        hint="Common: utf-8, latin-1, ascii, cp437. Default: utf-8",
    ),
    CfgKey(
        "eol",
        "\r",
        "eol",
        help="Line ending added to each line sent to the device ({prefix}term.eol).",
        hint=r'"\r" CR (default), "\n" LF, "\r\n" CRLF; also \0 \x03 \x04.',
    ),
    # Transmit delay (TeraTerm's msec/char and msec/line): a device line
    # goes out one character at a time, this many ms apart, and waits this
    # long after its line ending.  For a device that drops bytes arriving
    # back to back.  Binary (file transfers, /proto, /raw) is never paced.
    CfgKey(
        "tx_char_delay_ms",
        0,
        "int",
        min=0,
        help="Pause in ms after each character of a line sent to the device.",
        hint="0 = off. For a device that drops bytes sent back to back.",
    ),
    CfgKey(
        "tx_line_delay_ms",
        0,
        "int",
        min=0,
        help="Pause in ms after each line sent to the device.",
        hint="0 = off. Lines only: binary sends are never paced.",
    ),
    # Receive newline: how incoming device output is split into lines.
    # "auto" treats CR, LF, and CRLF all as line terminators (TeraTerm's
    # Receive=AUTO -- works for any device); cr/lf/crlf force a single
    # terminator for the rare device that sends a stray CR/LF as data.
    CfgKey(
        "eol_rx",
        "auto",
        "enum",
        choices=RX_NEWLINE_MODES,
        help="How device output is split into lines (set: {prefix}term.eol.rx).",
        hint="auto (CR, LF and CRLF all end a line), cr, lf, crlf. Default: auto",
    ),
    CfgKey(
        "eol_markers",
        False,
        "bool",
        help=r"Show dim \r \n markers in serial output ({prefix}term.eol.markers).",
        hint="true, false. For tracking down line-ending problems.",
    ),
    CfgKey(
        "send_bare_enter",
        False,
        "bool",
        help="Enter on an empty prompt sends a line ending ({prefix}term.send_bare_enter).",
        hint="true, false",
    ),
    CfgKey(
        "cmd_delay_ms",
        0,
        "int",
        min=0,
        help="Pause in ms after each of the TUI's on-connect commands.",
        hint="0 = no pause. Non-negative integer.",
    ),
    # A paste of several lines into the prompt sends them all, one after
    # another as if typed, this many ms apart (TeraTerm's paste, and its
    # PasteDelayPerLine default); off, only the first line lands.
    CfgKey(
        "multiline_paste",
        True,
        "bool",
        help="Pasting several lines into the prompt sends each one, as if typed.",
        hint="true, false. Off: only the first line lands. Escape stops a paste.",
    ),
    CfgKey(
        "multiline_paste_delay_ms",
        10,
        "int",
        min=0,
        help="Pause in ms between the lines of a multi-line paste.",
        hint="0 = no pause. Non-negative integer. Default: 10 (TeraTerm's).",
    ),
    # The TUI's Ctrl+C (nothing selected), Ctrl+D and Ctrl+Z (empty prompt)
    # and Escape (nothing running, empty prompt) go to the device, as in
    # any terminal.  false keeps them termapy's, for a device a stray
    # control byte would upset.
    CfgKey(
        "control_keys",
        True,
        "bool",
        help="Ctrl+C/D/Z and Esc go to the device when termapy has no use for them.",
        hint="true, false ({prefix}term.control_keys). Off: copy, delete, clear only.",
    ),
    # Input echo of device commands sent to the wire (bare lines +
    # /term.send).  A separate session-only flag, echo_repl, governs echo
    # of REPL/slash commands (/cfg, /help, ...) -- it has no cfg key
    # because its default is per-host (see TerminalHost._init_flags).
    # (Device-side echo, where the device parrots your bytes, is a device
    # concern and not modeled here.)
    CfgKey(
        "echo",
        False,
        "bool",
        help="Show the lines you send to the device in the output ({prefix}term.echo).",
        hint="true, false",
        session_flag=True,
    ),
    CfgKey(
        "echo_fmt",
        "[purple]$(CFG)> {cmd}[/]",
        "str",
        help="Rich markup for a line shown by echo; {cmd} is the line sent.",
        hint="{cmd} is replaced. Example: [purple]> {cmd}[/]",
        preview=_preview_markup,
    ),
    # Display
    CfgKey(
        "timestamps",
        False,
        "bool",
        help="Prefix each line with [HH:MM:SS.mmm] (toggle: {prefix}term.timestamps).",
        hint="true, false",
    ),
    CfgKey(
        "line_no",
        False,
        "bool",
        help="Show line numbers in serial output (toggle: {prefix}term.line_no).",
        hint="true, false",
        session_flag=True,
    ),
    CfgKey(
        "hex",
        False,
        "bool",
        help="Show received bytes as hex instead of text (toggle: {prefix}term.hex).",
        hint="true, false",
        session_flag=True,
    ),
    CfgKey(
        "color",
        True,
        "bool",
        help="Color in the output, termapy's and the device's ({prefix}term.color).",
        hint="true, false",
        session_flag=True,
    ),
    CfgKey(
        "output_level",
        DEFAULT_OUTPUT_LEVEL,
        "enum",
        choices=OUTPUT_LEVELS,
        help="How loud commands are ({prefix}term.output).",
        hint="silent, quiet, normal, verbose",
        session_flag=True,
    ),
    CfgKey(
        "max_lines",
        10000,
        "int",
        min=1,
        help="Lines the output pane keeps; past this the oldest drop off.",
        hint="Positive integer. Default: 10000",
    ),
    CfgKey(
        "max_grep_lines",
        100,
        "int",
        min=1,
        help="Most lines {prefix}grep shows for one search.",
        hint="Positive integer. Default: 100",
    ),
    # Device replies
    CfgKey(
        "protocol",
        "text",
        "enum",
        choices=PROTOCOLS,
        help="Wire format the device speaks.",
        hint='"text" (default, line-oriented), "ndjson" (one JSON object per line).',
    ),
    CfgKey(
        "ndjson_field_routing",
        None,
        "section",
        help="NDJSON: which inbound JSON fields the MCP bridge routes on.",
        hint="Keys response_id, error_field, event_field; change for other names.",
    ),
    CfgKey(
        "ndjson_field_routing.response_id",
        "id",
        "str",
        help="NDJSON field name carrying a reply's correlation id.",
        hint="Default: id",
    ),
    CfgKey(
        "ndjson_field_routing.error_field",
        "error",
        "str",
        help="NDJSON field name that marks a message as an error.",
        hint="Default: error",
    ),
    CfgKey(
        "ndjson_field_routing.event_field",
        "event",
        "str",
        help="NDJSON field name that marks an unsolicited event.",
        hint="Default: event",
    ),
    CfgKey(
        "request_mode",
        False,
        "bool",
        help="Every answer, from the device or termapy, comes as a JSON envelope.",
        hint="true, false. See {prefix}term.request.",
    ),
    CfgKey(
        "request_err_pattern",
        r"(?i)^(ERROR|ERR|FAULT)\b",
        "regex",
        help="Regex that marks a request_mode reply as a device-side error.",
        hint="Match: success=false. Empty: off. {prefix}term.request on err=<re>",
    ),
    # Half-duplex devices echo the command back before answering.  When on,
    # a request_mode response drops a leading line that matches the sent
    # command.  Off by default -- opt in per device, since a device that
    # doesn't echo could in theory emit a first line matching the command.
    CfgKey(
        "strip_device_echo",
        False,
        "bool",
        help="Drop a half-duplex device's echoed command from request_mode replies.",
        hint="true, false. On: a first reply line matching the command is dropped.",
    ),
    CfgKey(
        "json_fmt",
        None,
        "section",
        help="How JSON shows on screen, termapy's and a device's.",
        hint="Keys highlight, indent. A pipe always gets one line per JSON value.",
    ),
    CfgKey(
        "json_fmt.highlight",
        True,
        "bool",
        help="Highlight JSON on screen ({prefix}term.json_fmt.highlight).",
        hint="true, false. Shows only with color on ({prefix}term.color).",
    ),
    CfgKey(
        "json_fmt.indent",
        2,
        "enum",
        choices=JSON_INDENTS,
        help="Spaces per level for JSON on screen ({prefix}term.json_fmt.indent).",
        hint="0 (one line), 2 or 4.",
    ),
    # Device profile
    CfgKey(
        "profile_path",
        "",
        "path",
        help="Device profile MCP loads on connect; empty = found by convention.",
        hint='File path, e.g. "termapy_cfg/myrig/myrig.profile.json", or empty.',
    ),
    CfgKey(
        "validate_typed_args",
        False,
        "bool",
        help="Check typed device commands against the profile before sending.",
        hint="true, false. Off: the device judges (raw access); on: as MCP does.",
    ),
    CfgKey(
        "default_response_timeout_ms",
        1000,
        "int",
        min=1,
        help="Reply wait for a profile command that sets no response.timeout_ms.",
        hint="Positive integer ms. Default: 1000. A command's own timeout wins.",
    ),
    # Files
    CfgKey(
        "log_file",
        "",
        "path",
        help="The TUI's session log; empty = <config>.log in the config's folder.",
        hint="MCP logs to <config>.mcp.log beside it; the CLI keeps no log.",
    ),
    CfgKey(
        "file_xfer_root",
        "",
        "path",
        help="Folder XMODEM/YMODEM use; empty = the config's cap/ folder.",
        hint="Absolute path, or empty. Set: {prefix}xfer.root",
    ),
    # Protocol tests
    CfgKey(
        "proto_frame_gap_ms",
        50,
        "int",
        min=1,
        help="Silence in ms that ends a binary frame in a protocol test.",
        hint="Positive integer. Default: 50",
    ),
    CfgKey(
        "proto_results_template",
        "{name}_results.json",
        "str",
        help="File name for a protocol test's JSON results.",
        hint="Placeholders: {name}, {proto_name}, {datetime}, {date}, {time}",
    ),
    # Startup and frontends
    CfgKey(
        "default_ui",
        "tui",
        "enum",
        choices=UI_MODES,
        help="Frontend to start in when no mode flag (--cli, --vt100) is given.",
        hint="tui, cli, vt100. Default: tui",
    ),
    CfgKey(
        "quiet_start",
        True,
        "bool",
        help="Show only the connection and any problem at start; detail in the log.",
        hint="true, false. False also shows the load table and full connect line.",
    ),
    CfgKey(
        "vt100_hint",
        True,
        "bool",
        help="In --vt100 mode under VS Code, show a tip about captured keys.",
        hint="true, false. VS Code terminals capture some keys before the device.",
    ),
    CfgKey(
        "cmd_prefix",
        DEFAULT_CMD_PREFIX,
        "str",
        help="Starts a termapy command; any other line goes to the device.",
        hint="Default: /. Example: ! makes commands like !help",
    ),
    CfgKey(
        "cli_prompt",
        "$(CFG)> ",
        "str",
        help="Prompt shown for input in CLI mode.",
        hint="Default: '$(CFG)> ', the config name. $(...) variables expand.",
    ),
    CfgKey(
        "cli_completion",
        True,
        "bool",
        help="CLI tab completion, history auto-suggest and the help toolbar.",
        hint="true, false. Default: true",
    ),
    # Buttons: the title bar's, Record next to the REPL prompt (toggles
    # /run.record), the pickers' Edit, then the custom buttons.
    CfgKey(
        "cfg_enabled",
        True,
        "bool",
        help="Show the Cfg button in the title bar.",
        hint="true, false",
    ),
    CfgKey(
        "run_enabled",
        True,
        "bool",
        help="Show the Run button in the title bar.",
        hint="true, false",
    ),
    CfgKey(
        "proto_enabled",
        True,
        "bool",
        help="Show the Proto button in the title bar.",
        hint="true, false",
    ),
    CfgKey(
        "record_enabled",
        True,
        "bool",
        help="Show the Record button by the prompt (toggles {prefix}run.record).",
        hint="true, false",
    ),
    CfgKey(
        "config_read_only",
        False,
        "bool",
        help="Grey out Edit, Rename and Delete in the cfg, run and proto pickers.",
        hint="true, false. {prefix}cfg still changes in-memory values.",
    ),
    CfgKey(
        "custom_buttons_side",
        "bottom",
        "enum",
        choices=CUSTOM_BUTTON_SIDES,
        help="Where custom buttons go: the bottom bar or a left or right column.",
        hint=f"{', '.join(CUSTOM_BUTTON_SIDES)}. A column scrolls when it holds more than fit.",
    ),
    CfgKey(
        "custom_buttons",
        [
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
        "list",
        help="Toolbar buttons of your own, each running a command.",
        hint="Each has: enabled, name, command, tooltip. See help: custom-buttons.",
    ),
)

# The fields of one custom button: help only, since they live inside a list
# and so have no path.
CUSTOM_BUTTON_FIELDS: tuple[CfgKey, ...] = (
    CfgKey("enabled", False, "bool", help="Show this button on the toolbar.", hint="true, false"),
    CfgKey("name", "", "str", help="Label shown on the button.", hint="Short text."),
    CfgKey(
        "command",
        "",
        "str",
        help="What a click runs: text for the device or a termapy command.",
        hint=r"One line, or several separated by \n.",
    ),
    CfgKey(
        "tooltip",
        "",
        "str",
        help="Hover text for the button.",
        hint="Shown when the mouse rests on the button.",
    ),
)


def _leaf(record: CfgKey) -> str:
    """A record's name without its section (``baud_rate`` for ``serial.baud_rate``)."""
    return record.path.rpartition(".")[2]


def _default_cfg_view() -> dict[str, Any]:
    """The defaults as a config holds them, nested by path."""
    cfg: dict[str, Any] = {}
    for record in CFG_KEYS:
        section, _, leaf = record.path.rpartition(".")
        if section:
            cfg[section][leaf] = copy.deepcopy(record.default)
        else:
            cfg[leaf] = {} if record.kind == "section" else copy.deepcopy(record.default)
    return cfg


def _help_row(record: CfgKey) -> tuple:
    """A record as ``CFG_HELP`` holds it: help, hint, and a preview if it has one."""
    if record.preview is None:
        return (record.help, record.hint)
    return (record.help, record.hint, record.preview)


# The views.  Each key's name in the file is its leaf, so these are by leaf.
DEFAULT_CFG: dict[str, Any] = _default_cfg_view()
CFG_HELP: dict[str, tuple] = {
    _leaf(record): _help_row(record) for record in (*CFG_KEYS, *CUSTOM_BUTTON_FIELDS)
}
# Every key whose value is one of a fixed set: load warns on anything else,
# the config editor marks it red, and /cfg matches a typed value in any case
# (config.cfg_choice).
CFG_CHOICES: dict[str, Collection[object]] = {
    _leaf(record): record.choices for record in CFG_KEYS if record.kind == "enum"
}
# Keys termapy writes itself, never the user.
MANAGED_CFG_KEYS = frozenset(_leaf(record) for record in CFG_KEYS if record.managed)
# Keys that start a session flag of the same name.
SESSION_FLAG_KEYS = frozenset(_leaf(record) for record in CFG_KEYS if record.session_flag)

_RECORD_BY_PATH = {record.path: record for record in CFG_KEYS}
_BUTTON_FIELD_BY_NAME = {record.path: record for record in CUSTOM_BUTTON_FIELDS}


def default_cfg() -> dict:
    """Return a fresh deep copy of ``DEFAULT_CFG``, for any cfg that will be mutated.

    ``dict(DEFAULT_CFG)`` shares the nested dicts (``serial``, ...), so
    ``cfg["serial"]["port"] = "COM3"`` would change the global default.
    """
    import copy

    return copy.deepcopy(DEFAULT_CFG)


def cfg_key_paths() -> tuple[str, ...]:
    """Every config key as a dotted path, in ``CFG_KEYS`` order.

    A top-level key is its own path; a section adds a path per leaf after
    it (``serial``, then ``serial.port``, ``serial.baud_rate``, ...).  The
    custom buttons are a list, so their fields are not paths.  The one
    spelling of "a config key" for a declaration (``Command.cfg``), a
    reader (``cfg_value``) and the bool table (``config.BOOL_CFG_PATHS``).
    """
    return tuple(record.path for record in CFG_KEYS)


def cfg_key_path(name: str) -> str | None:
    """The path a typed name means, or ``None`` when it names no key.

    A known path is itself; a bare leaf of a nested key resolves when it
    is unique (``baud_rate`` -> ``serial.baud_rate``); anything else,
    including a leaf two dicts share, is ``None``.
    """
    paths = cfg_key_paths()
    if name in paths:
        return name
    matches = [path for path in paths if "." in path and path.rpartition(".")[2] == name]
    return matches[0] if len(matches) == 1 else None


def cfg_key(name: str) -> CfgKey | None:
    """The record a typed name means, or ``None`` when it names no key.

    A path or a unique leaf (``baud_rate``), as ``cfg_key_path`` reads
    them, or a custom button's field, which has no path.
    """
    path = cfg_key_path(name)
    return _RECORD_BY_PATH[path] if path is not None else _BUTTON_FIELD_BY_NAME.get(name)


def cfg_value(cfg: Mapping, path: str) -> Any:
    """``cfg``'s value at ``path``, or ``DEFAULT_CFG``'s when the config lacks it.

    The one reader of a config value with a default: the default is the
    table's, never a literal at the call site.  A path ``DEFAULT_CFG``
    has no entry for raises ``KeyError``: that is a typo in code, not a
    user's file, so it fails loud.
    """
    parts = path.split(".")
    default: Any = DEFAULT_CFG
    for part in parts:
        default = default[part]
    node: Any = cfg
    for part in parts:
        if not isinstance(node, Mapping) or part not in node:
            # A container default is copied, as default_cfg copies: a
            # caller must never hold the table's own list or dict.
            return copy.deepcopy(default) if isinstance(default, (dict, list)) else default
        node = node[part]
    return node


def cfg_slot(cfg: Mapping, name: str) -> tuple[Mapping, str] | None:
    """Where a typed key sits in ``cfg``: the dict that holds it and its name there.

    A top-level key, the table's or one only the file has, is ``cfg``
    itself; a path or a unique leaf of a nested key is its section's dict
    (``baud_rate`` and ``serial.baud_rate`` both give ``cfg["serial"]``,
    ``indent`` and ``json_fmt.indent`` give ``cfg["json_fmt"]``).  The one answer for
    ``/cfg``, the session store and the TUI's confirm.

    Returns:
        ``(container, leaf)``, or None when ``cfg`` does not hold the key.
    """
    if name in cfg:
        return cfg, name
    path = cfg_key_path(name)
    if path is None or "." not in path:
        return None
    section, _, leaf = path.rpartition(".")
    container = cfg.get(section)
    if isinstance(container, Mapping) and leaf in container:
        return container, leaf
    return None


def cmd_prefix(cfg: Mapping) -> str:
    """Return ``cfg["cmd_prefix"]``, the table's default when the key is unset."""
    return cfg_value(cfg, "cmd_prefix")


def quiet_start(cfg: Mapping) -> bool:
    """True when the startup shows only the connection and any problem.

    ``cfg["quiet_start"]`` with the project default as fallback, read by
    everything the setting quiets (the load report, the connect line), so
    a cfg dict built without the key still behaves like a loaded one.
    """
    return bool(cfg_value(cfg, "quiet_start"))


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
