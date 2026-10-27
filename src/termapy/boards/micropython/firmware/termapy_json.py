"""termapy's command monitor for a MicroPython board: the reply envelope and the serial loop.

Every MicroPython starter shares this one file; a board's main.py is its
handlers, its COMMANDS table and ``serve(COMMANDS)``.

The same wire contract as the C module termapy_json.c (help page "JSON
integration"), so termapy, an MCP agent or any JSON-RPC client talks to a
board running this exactly as to one running the C:

- Text framing (the default): a reply is the value as text, one item per
  line, each ending ``\\r\\n``; a failure is ``ERR <message>``.
- JSON framing: a reply is one compact JSON-RPC 2.0 response object per
  line, ending ``\\n``: ``{"result":<value>}`` or
  ``{"error":{"code":<int>,"message":"<msg>"}}``, with ``"jsonrpc":"2.0"``
  first when the request carried it and ``"id"`` last when it had one.
- ``json on|off`` switches framing (anything but ``on`` means off, failing
  closed); bare ``json`` answers the framing.  A line starting with ``{`` is
  a JSON request and switches to JSON framing by itself: ``{"cmd":..}`` or
  ``{"method":..}``, with ``"args"`` (a string) or ``"params"`` (a string or
  a flat array, joined by spaces).
- ``help`` answers every command's one-line help: in JSON an object of
  name to help, in text one aligned ``name  help`` line per command, sorted.
  ``help <command>`` answers that command's line.

Error codes are the contract's: -32700 malformed request, -32601 unknown
command, -32602 bad arguments (a handler's ``CommandError``), -32603 a
handler that failed otherwise.  Each command is a one-line help and a
handler that takes the argument words and returns a JSON value; the monitor
renders the value for either framing.  ``int_arg`` reads a number word the
way termapy's profile does: decimal or ``0x`` hex, out of range refused.
``on_off_arg`` reads a board switch the same way: on or 1, off or 0.

This file runs on the board, so it uses only what MicroPython has: no
typing, no annotations, and ``json``'s ``separators`` for compact output.
"""

import json
import sys

PARSE_ERROR = -32700
UNKNOWN_COMMAND = -32601
BAD_ARGS = -32602
INTERNAL_ERROR = -32603

# The words ``json`` takes; anything else means off, failing closed.
FRAMINGS = {"on": True, "off": False}

# The words a board's own switch takes (``led``), and the state each means.
ON_OFF = {"on": True, "1": True, "off": False, "0": False}

# An id with more digits than this is ignored, as termapy_json.c ignores it.
ID_MAX_DIGITS = 10


class CommandError(Exception):
    """A handler's refusal: ``ERR <message>`` in text, an error envelope in JSON."""

    def __init__(self, message, code=BAD_ARGS):
        super().__init__(message)
        self.message = message
        self.code = code


def int_arg(word, what, low, high):
    """``word`` as a whole number from ``low`` to ``high``: decimal, or hex after ``0x``.

    A value out of range is refused, never clipped, as termapy refuses it
    for an MCP agent through the profile's int_range type.  A typed line
    reaches the board unchecked unless the config sets
    validate_typed_args, so this refusal is the one a person sees.

    Raises:
        CommandError: ``word`` is not a number, or is out of range.
    """
    try:
        value = int(word[2:], 16) if word[:2] in ("0x", "0X") else int(word)
    except ValueError:
        value = None
    if value is None or not low <= value <= high:
        message = "invalid " + what + ": " + word
        raise CommandError(message + " (use " + str(low) + " to " + str(high) + ")")
    return value


def on_off_arg(word, what):
    """``word`` as a switch: True for ``on`` or ``1``, False for ``off`` or ``0``.

    Anything else is refused, as termapy refuses it for an MCP agent
    through the profile's enum type.  ``json`` keeps its own reading
    (anything but ``on`` is off), the wire contract termapy_json.c shares.

    Raises:
        CommandError: ``word`` is not one of the four.
    """
    if word not in ON_OFF:
        raise CommandError("invalid " + what + ": " + word + " (use on, off, 1 or 0)")
    return ON_OFF[word]


def _dumps(value):
    """``value`` as compact JSON text, the envelope's form."""
    return json.dumps(value, separators=(",", ":"))


def _text_lines(value):
    """The text-framing lines of a reply value: one per item, one per key."""
    if isinstance(value, bool):
        return ["true" if value else "false"]
    if isinstance(value, list):
        lines = []
        for item in value:
            lines.extend(_text_lines(item))
        return lines
    if isinstance(value, dict):
        return [str(key) + " " + _text_lines(item)[0] for key, item in value.items()]
    return str(value).split("\n")


def _request_id(value):
    """The id a request carries: an integer of at most ID_MAX_DIGITS digits, else None."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if len(str(abs(value))) > ID_MAX_DIGITS:
        return None
    return value


def _word(element):
    """One ``params`` array element as a command word; a nested value is malformed."""
    if isinstance(element, str):
        return element
    if isinstance(element, (dict, list)):
        raise CommandError("malformed json", PARSE_ERROR)
    return _dumps(element)


class Monitor:
    """One serial link's command monitor: dispatches lines, replies in the framing in force.

    Args:
        commands: Command name to ``(help, handler)``: a one-line help, and a
            handler that takes the list of argument words and returns a JSON
            value, or raises ``CommandError``.
        write: Writes one reply line, its line ending included.

    Raises:
        ValueError: A command reuses the name of one the monitor answers itself.
    """

    def __init__(self, commands, write):
        # The monitor's own commands, in the table's shape.
        self.builtins = {
            "help": ("list the commands; help <command> for one", self._help),
            "json": ("the reply framing: on for JSON-RPC, off for text", self._framing),
        }
        for name in commands:
            if name in self.builtins:
                raise ValueError("command name taken by the monitor: " + name)
        self.commands = commands
        self.write = write
        self.json = False
        self._id = None
        self._tagged = False

    def handle(self, line):
        """Answer one received line; a blank line is ignored."""
        text = line.strip()
        if not text:
            return
        self._id = None
        self._tagged = False
        if text.startswith("{"):
            self.json = True
            try:
                text = self._extract(text)
            except CommandError as e:
                self._error(e)
                return
        words = text.split()
        name, args = words[0], words[1:]
        entry = self.builtins.get(name) or self.commands.get(name)
        if entry is None:
            self._error(CommandError("unknown command: " + name, UNKNOWN_COMMAND))
            return
        try:
            value = entry[1](args)
        except CommandError as e:
            self._error(e)
            return
        except Exception as e:
            self._error(CommandError(str(e), INTERNAL_ERROR))
            return
        self._result(value)

    def _framing(self, args):
        """``json``: the framing in force, after ``json on|off`` switches it."""
        if args:
            self.json = FRAMINGS.get(args[0], False)
        return "on" if self.json else "off"

    def _help(self, args):
        """``help``: every command's one-line help; ``help <command>``: that command's."""
        table = {}
        for commands in (self.commands, self.builtins):
            for name, (line, _) in commands.items():
                table[name] = line
        if len(args) > 1:
            raise CommandError("usage: help {command}")
        if args:
            if args[0] not in table:
                raise CommandError("unknown command: " + args[0])
            return table[args[0]]
        if self.json:
            return table
        # Text is for a person: sorted by hand (MicroPython's dicts keep no order), help aligned.
        width = max(len(name) for name in table) + 2
        return [name + " " * (width - len(name)) + table[name] for name in sorted(table)]

    def _extract(self, text):
        """A JSON request as the ``cmd args`` line it asks for; the id and tag are kept first."""
        try:
            request = json.loads(text)
        except ValueError:
            raise CommandError("malformed json", PARSE_ERROR) from None
        if not isinstance(request, dict):
            raise CommandError("malformed json", PARSE_ERROR)
        self._tagged = "jsonrpc" in request
        self._id = _request_id(request.get("id"))
        name = request["cmd"] if "cmd" in request else request.get("method")
        if not isinstance(name, str) or not name.strip():
            raise CommandError("malformed json", PARSE_ERROR)
        if "args" in request:
            args = request["args"]
            if not isinstance(args, str):
                raise CommandError("malformed json", PARSE_ERROR)
        elif "params" in request:
            params = request["params"]
            if isinstance(params, str):
                args = params
            elif isinstance(params, list):
                args = " ".join(_word(element) for element in params)
            else:
                raise CommandError("malformed json", PARSE_ERROR)
        else:
            args = ""
        return (name + " " + args).strip()

    def _envelope(self, member):
        """Write one JSON-RPC response line around ``member``, mirroring the tag and id."""
        head = '{"jsonrpc":"2.0",' if self._tagged else "{"
        if self._id is not None:
            tail = ',"id":' + str(self._id) + "}"
        elif self._tagged:
            tail = ',"id":null}'
        else:
            tail = "}"
        self.write(head + member + tail + "\n")

    def _result(self, value):
        """Reply with ``value`` in the framing in force."""
        if self.json:
            self._envelope('"result":' + _dumps(value))
        else:
            for line in _text_lines(value):
                self.write(line + "\r\n")

    def _error(self, error):
        """Reply with a failure in the framing in force."""
        if self.json:
            body = '{"code":' + str(error.code) + ',"message":' + _dumps(error.message) + "}"
            self._envelope('"error":' + body)
        else:
            self.write("ERR " + error.message + "\r\n")


# Raw bytes out, so a JSON line ends in exactly "\n" and a text line in "\r\n".
_out = getattr(sys.stdout, "buffer", None)


def write(text):
    """Send one reply line, its line ending included."""
    if _out is None:
        sys.stdout.write(text)
    else:
        _out.write(text.encode())


def read_line():
    """One line from the serial link, CR, LF or CRLF terminated; blank lines skipped."""
    chars = []
    while True:
        char = sys.stdin.read(1)
        if char in "\r\n":
            if chars:
                return "".join(chars)
        else:
            chars.append(char)


def serve(commands):
    """Answer the serial link forever with ``commands``: a line in, a reply out."""
    monitor = Monitor(commands, write)
    while True:
        monitor.handle(read_line())
