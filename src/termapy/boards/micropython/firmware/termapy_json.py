"""termapy's command monitor for a MicroPython board: the reply envelope and the serial loop.

Every MicroPython starter shares this one file; a board's main.py is its
handlers, its COMMANDS table and ``Monitor.run()`` as one asyncio task
beside the project's own.

``run()`` answers the link forever: it calls ``poll()``, then yields to the
other tasks, for POLL_INTERVAL when nothing was waiting.  ``poll()`` never
waits, so a project with a super loop of its own calls it on every pass:
each call reads the characters that have arrived, and when one ends a line
(CR, LF or both) it answers that line and returns.  Without a line ending
it returns at once, keeping what it read for the next call.  A line longer
than LINE_MAX is refused whole with ``ERR line too long``, so a link that
never sends a line ending cannot fill memory.  ``serve(COMMANDS)`` is the
blocking form, for a board that only answers commands.

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
- ``help`` answers every command's one-line help, sorted by name: in JSON
  an object of name to help, in text one aligned ``name  help`` line each.
  ``help <command>`` answers that command's line.
- A native command has a text grammar of its own (termapy_mem.py's MEM
  wire spec): a plain line gets its text reply whatever the framing, and a
  JSON request gets the reply lines as the result, or an ``ERR <reason>``
  line as the error.
- An object (``objects={"machine": machine}``) puts a Python object on the
  link under a name, with no command written for it.  A line that is the
  name lists the object's public names; ``name.<path> {values}`` finds the
  dotted path by attribute and answers it, called with the values when it
  is a function: ``machine.freq``, ``machine.Pin.value 25 1``.  A method
  reached through its class takes the object's id as its first value, so
  that line is ``Pin(25).value(1)``.  A value is a number (decimal or ``0x``
  hex), a quoted string, a path such as ``machine.Pin.OUT``, or
  ``name=value``.  Nothing is evaluated: a line is names and values, never
  Python.  A module or a class answers its public names, so ``machine`` and
  then ``machine.Pin`` find what is there.  Bytes answer as uppercase hex, a
  tuple as an array, and anything else JSON has no form for as its ``repr``.
  A name that is not there is -32601, an exception from the call a -32603
  failure naming its type.  This is MicroPython's own; the C module has no
  such thing.

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
import select
import sys
from collections import OrderedDict

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

# The longest line the monitor takes, in characters.  The longest MEM.W line
# (termapy_mem.c's TERMAPY_MEM_LINE_SIZE, 212) wrapped in a JSON request fits
# with room to spare.
LINE_MAX = 512

# The largest whole number an object line takes as a value, either sign: any
# 64-bit quantity.
VALUE_MAX = 1 << 64

# How long run() sleeps, in seconds, when no command was waiting: the most a
# command waits to be read, and the time the other tasks get.
POLL_INTERVAL = 0.01


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


def _plain(value):
    """An object line's ``value`` in a form the reply envelope can carry.

    A module or a class answers its public names, sorted, which is how a
    caller finds what an object has; bytes answer as uppercase hex, a tuple
    as a list, and anything else JSON has no form for as its ``repr``.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return "".join(f"{byte:02X}" for byte in value)
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, type) or type(value).__name__ == "module":
        return _names(value)
    return repr(value)


def _names(value):
    """The public names ``value`` has, sorted: MicroPython's ``dir`` keeps no order."""
    return sorted(name for name in dir(value) if not name.startswith("_"))


def _is_name(text):
    """Whether ``text`` is a keyword's name: letters, digits and underscores, not opening with a digit."""
    if not text or text[0].isdigit():
        return False
    return all(char.isalpha() or char.isdigit() or char == "_" for char in text)


def _word(element):
    """One ``params`` array element as a command word; a nested value is malformed."""
    if isinstance(element, str):
        return element
    if isinstance(element, (dict, list)):
        raise CommandError("malformed json", PARSE_ERROR)
    return _dumps(element)


class CommandSet:
    """Collect a board's commands with a decorator instead of a dict literal.

    The explicit ``COMMANDS = {name: (help, handler)}`` dict the starters use is
    the simplest form and stays the recommended one -- it is one visible place
    and easy to read on a board with no debugger.  This is an alternative for a
    project that prefers to decorate its handlers::

        commands = CommandSet()

        @commands.command("led", "the onboard LED: led on|off")
        def cmd_led(args):
            ...

        monitor = Monitor(commands.table, native=direct().commands())

    ``table`` is the same ``{name: (help, handler)}`` the dict form builds, so a
    monitor cannot tell which was used.  No dependency and no discovery magic:
    the decorator just fills a dict, so a handler that is never decorated is
    simply never registered, the same as a row left out of the dict.
    """

    def __init__(self):
        self.table = {}

    def command(self, name, help):
        """Register the decorated function as command ``name`` with ``help``; returns it unchanged."""

        def register(handler):
            self.table[name] = (help, handler)
            return handler

        return register


class Monitor:
    """One serial link's command monitor: dispatches lines, replies in the framing in force.

    Args:
        commands: Command name to ``(help, handler)``: a one-line help, and a
            handler that takes the list of argument words and returns a JSON
            value, or raises ``CommandError``.
        write: Writes one reply line, its line ending included; the board's
            serial link when omitted.
        native: Native commands, in the same shape, whose handler returns
            its reply's text lines (termapy_mem.py's ``commands()``).
        read: Returns one received character, or ``""`` when none is
            waiting, and never waits; the board's serial link when omitted.
        objects: Name to Python object (``{"machine": machine}``).  A line
            that is the name lists the object's public names, and
            ``name.<path> {values}`` answers the attribute the dotted path
            names, called with the values when it is a function.  Nothing
            is evaluated, but every public name under the object is in
            reach, so an object is as much access as it gives.

    Raises:
        ValueError: A command or an object reuses the name of one the
            monitor answers itself, or of another table's.
    """

    def __init__(self, commands, write=None, native=None, read=None, objects=None):
        # The monitor's own commands, in the table's shape.
        self.builtins = {
            "help": ("list the commands; help <command> for one", self._help),
            "json": ("the reply framing: on for JSON-RPC, off for text", self._framing),
            "xmodem": ("transfer a file over XMODEM: xmodem recv|send", self._xmodem),
        }
        native = native or {}
        for table in (commands, native):
            for name in table:
                if name in self.builtins:
                    raise ValueError("command name taken by the monitor: " + name)
        for name in commands:
            if name in native:
                raise ValueError("command name in both tables: " + name)
        objects = objects or {}
        for name in objects:
            if name in self.builtins or name in commands or name in native:
                raise ValueError("object name taken by a command: " + name)
        self.commands = commands
        self.native = native
        self.objects = objects
        listed = {
            name: (
                "a Python object: "
                + name
                + " lists its names, "
                + name
                + ".<name> {values} reads or calls one",
                None,
            )
            for name in objects
        }
        # help's answer, built once in name order for both framings.  An
        # OrderedDict, because MicroPython's dicts keep no order: a plain one
        # reaches the wire in hash order.
        self.help_table = OrderedDict(
            sorted(
                (name, line)
                for table in (commands, native, self.builtins, listed)
                for name, (line, _) in table.items()
            )
        )
        self.write = write or _link_write
        # None until the first poll(): the serial link is opened only on the board.
        self.read = read
        self.json = False
        self._id = None
        self._tagged = False
        # The line being received, and whether it has run past LINE_MAX.
        self._chars = []
        self._too_long = False

    async def run(self, interval=POLL_INTERVAL):
        """Answer the link forever, as an asyncio task beside the project's own.

        After answering a command it yields without waiting, so a burst of
        commands is answered quickly; with nothing waiting it sleeps for
        ``interval`` seconds, which the other tasks get.
        """
        # Imported here, so a board that only polls never loads it.
        import asyncio

        while True:
            await asyncio.sleep(0 if self.poll() else interval)

    def poll(self):
        """Answer a command if a whole line has arrived; otherwise return at once.

        For a super loop: call it on every pass.  It reads the characters
        waiting on the link, and returns after answering one line or as soon
        as no character is left, so a pass runs at most one command.

        Returns:
            True when it answered a line, False when none was complete.
        """
        if self.read is None:
            self.read = _LinkReader()
        while True:
            char = self.read()
            if not char:
                return False
            if self.feed(char):
                return True

    def feed(self, char):
        """Take one received character; at a line ending, answer the line.

        Returns:
            True when the character ended a line that held something, False
            otherwise (the second character of CRLF ends nothing).
        """
        if char in "\r\n":
            if self._too_long:
                self._chars = []
                self._too_long = False
                self._id = None
                self._tagged = False
                self._error(CommandError("line too long", PARSE_ERROR))
                return True
            if not self._chars:
                return False
            line = "".join(self._chars)
            self._chars = []
            self.handle(line)
            return True
        if self._too_long:
            return False
        if len(self._chars) >= LINE_MAX:
            # Dropped now, not at the line ending, so memory stays bounded.
            self._chars = []
            self._too_long = True
            return False
        self._chars.append(char)
        return False

    def handle(self, line):
        """Answer one received line; a blank line is ignored."""
        text = line.strip()
        if not text:
            return
        self._id = None
        self._tagged = False
        requested_json = text.startswith("{")
        if requested_json:
            self.json = True
            try:
                text = self._extract(text)
            except CommandError as e:
                self._error(e)
                return
        words = text.split()
        name, args = words[0], words[1:]
        if name.split(".")[0] in self.objects:
            self._object(name, args)
            return
        if name in self.native:
            self._native(self.native[name][1], args, requested_json)
            return
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

    def _object(self, path, words):
        """Answer an object line: the dotted ``path`` found by attribute, called with ``words`` as its values."""
        try:
            value = self._call(path, words)
        except CommandError as e:
            self._error(e)
            return
        except Exception as e:
            self._error(CommandError(type(e).__name__ + ": " + str(e), INTERNAL_ERROR))
            return
        self._result(value)

    def _call(self, path, words):
        """What ``path`` answers with ``words``: a listing, a value, or a call's result."""
        args = []
        named = {}
        for word in words:
            key, equals, text = word.partition("=")
            if equals and _is_name(key):
                named[key] = self._value(text)
            else:
                args.append(self._value(word))
        names = path.split(".")
        value = self.objects[names[0]]
        for index, name in enumerate(names[1:]):
            member = self._member(value, name, path)
            if isinstance(value, type) and callable(member) and not isinstance(member, type):
                # A method reached through its class: the first value makes the object.
                if not args:
                    raise CommandError(path + ": the first value is the " + names[index] + "'s id")
                member = getattr(value(args.pop(0)), name)
            value = member
        if len(names) == 1 or (isinstance(value, type) and not args and not named):
            if args or named:
                raise CommandError(path + " takes no values")
            return _names(value)
        if callable(value):
            return _plain(value(*args, **named))
        if args or named:
            raise CommandError(path + " takes no values")
        return _plain(value)

    def _member(self, value, name, path):
        """The public attribute ``name`` of ``value``; a missing or private one is an unknown name."""
        if not name or name[0] == "_" or not hasattr(value, name):
            raise CommandError("unknown name: " + path, UNKNOWN_COMMAND)
        return getattr(value, name)

    def _value(self, word):
        """One value word: a quoted string, a path under an object, or a number."""
        if len(word) > 1 and word[0] in "\"'" and word[-1] == word[0]:
            return word[1:-1]
        names = word.split(".")
        if names[0] in self.objects:
            value = self.objects[names[0]]
            for name in names[1:]:
                value = self._member(value, name, word)
            return value
        try:
            # int_arg is the board's one reader of a number word.
            return int_arg(word, "value", -VALUE_MAX, VALUE_MAX)
        except CommandError:
            pass
        try:
            return float(word)
        except ValueError:
            raise CommandError("invalid value: " + word) from None

    def _native(self, handler, args, requested_json):
        """Answer a native command: its text lines, or for a JSON request an envelope around them."""
        try:
            lines = handler(args)
        except Exception as e:
            # A fault, as for any command: -32603 in an envelope, ERR in text.
            if requested_json:
                self._error(CommandError(str(e), INTERNAL_ERROR))
                return
            lines = ["ERR " + str(e)]
        if not requested_json:
            for line in lines:
                self.write(line + "\r\n")
        elif len(lines) == 1 and lines[0].startswith("ERR "):
            self._error(CommandError(lines[0][4:]))
        else:
            self._result(lines)

    def _framing(self, args):
        """``json``: the framing in force, after ``json on|off`` switches it."""
        if args:
            self.json = FRAMINGS.get(args[0], False)
        return "on" if self.json else "off"

    def _help(self, args):
        """``help``: every command's one-line help, in name order; ``help <command>``: that command's."""
        table = self.help_table
        if len(args) > 1:
            raise CommandError("usage: help {command}")
        if args:
            if args[0] not in table:
                raise CommandError("unknown command: " + args[0])
            return table[args[0]]
        if self.json:
            return table
        # Text is for a person: the help lines aligned in one column.
        width = max(len(name) for name in table) + 2
        return [name + " " * (width - len(name)) + line for name, line in table.items()]

    def _xmodem(self, args):
        """``xmodem recv|send``: transfer a file over XMODEM; bare, the buffered byte count.

        The handler takes over the raw link for the transfer (the monitor does
        no I/O until it returns), then answers with the byte count.  ``recv``
        receives a file into the board buffer; ``send`` streams that buffer
        back to the host.  The host side is termapy's ``/xfer.xmodem.*``.
        """
        import termapy_xmodem

        verb = args[0] if args else "info"
        if verb == "info":
            return termapy_xmodem.buffered()
        if verb not in ("recv", "send"):
            raise CommandError("usage: xmodem {recv|send}")
        link = termapy_xmodem.RawLink()
        if verb == "recv":
            return termapy_xmodem.receive(link)
        return termapy_xmodem.send(link, termapy_xmodem.to_send())

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


def _link_write(text):
    """Send one reply line on the serial link, its line ending included."""
    if _out is None:
        sys.stdout.write(text)
    else:
        _out.write(text.encode())


class _LinkReader:
    """The serial link's input, read one character at a time without waiting."""

    def __init__(self):
        self._poller = select.poll()
        self._poller.register(sys.stdin, select.POLLIN)

    def __call__(self):
        """One waiting character, or ``""`` when none has arrived."""
        return sys.stdin.read(1) if self._poller.poll(0) else ""


def serve(commands, native=None):
    """Answer the serial link forever with ``commands`` and ``native``, waiting for each character.

    The blocking form, for a board that does nothing but answer commands;
    a board with other work calls ``Monitor.poll()`` from its own loop.
    """
    monitor = Monitor(commands, native=native)
    while True:
        monitor.feed(sys.stdin.read(1))
