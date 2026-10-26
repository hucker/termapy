"""termapy's JSON reply envelope for a MicroPython monitor: the device side, in Python.

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

Error codes are the contract's: -32700 malformed request, -32601 unknown
command, -32602 bad arguments (a handler's ``CommandError``), -32603 a
handler that failed otherwise.  Handlers take the argument words and return
a JSON value; the monitor renders it for either framing.

This file runs on the board, so it uses only what MicroPython has: no
typing, no annotations, and ``json``'s ``separators`` for compact output.
"""

import json

PARSE_ERROR = -32700
UNKNOWN_COMMAND = -32601
BAD_ARGS = -32602
INTERNAL_ERROR = -32603

# The words ``json`` takes; anything else means off, failing closed.
FRAMINGS = {"on": True, "off": False}

# An id with more digits than this is ignored, as termapy_json.c ignores it.
ID_MAX_DIGITS = 10


class CommandError(Exception):
    """A handler's refusal: ``ERR <message>`` in text, an error envelope in JSON."""

    def __init__(self, message, code=BAD_ARGS):
        super().__init__(message)
        self.message = message
        self.code = code


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
        commands: Command name to handler.  A handler takes the list of
            argument words and returns a JSON value, or raises
            ``CommandError``.
        write: Writes one reply line, its line ending included.
    """

    def __init__(self, commands, write):
        self.commands = commands
        self.write = write
        self.json = False
        self._id = None
        self._tagged = False

    def names(self):
        """Every command the monitor answers, its own ``json`` included, sorted."""
        names = list(self.commands)
        names.append("json")
        return sorted(names)

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
        if name == "json":
            if args:
                self.json = FRAMINGS.get(args[0], False)
            self._result("on" if self.json else "off")
            return
        handler = self.commands.get(name)
        if handler is None:
            self._error(CommandError("unknown command: " + name, UNKNOWN_COMMAND))
            return
        try:
            value = handler(args)
        except CommandError as e:
            self._error(e)
            return
        except Exception as e:
            self._error(CommandError(str(e), INTERNAL_ERROR))
            return
        self._result(value)

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
