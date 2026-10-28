"""termapy's MEM commands for a MicroPython board: the memory wire spec, pure reads and writes.

The same wire contract as the C module termapy_mem.c (help page "Memory",
"The wire spec"), so termapy's /mem.* reads and writes a board running this
exactly as one running the C:

    MEM.R <addr> <len>       ->  <ADDR>: <XX XX ...>  rows of 16 bytes, then OK
    MEM.W <addr> <hex>       ->  OK
    MEM.M <addr> <and> <or>  ->  <ADDR>: <the word before>, then OK
    MEM.INFO                 ->  {"max_block": 64, ...}, then OK
    any failure              ->  ERR usage, ERR length or ERR range

The board knows nothing about its memory: it reads and writes whatever
address it is given.  termapy does the checking, from the JSON that
describes the part, because a chip's whole map kept in the chip does not
scale to small devices.  A guard passed to ``Memory`` is the board's own
lock: ``guard(addr, count, write)`` sees every access before it runs and
refuses it by returning False, the reply ``ERR range``, so a board can
fence off what must never be touched without knowing anything else.  With
no guard, an address the chip cannot reach faults the CPU: on the ESP32-S3
that is a reset, not an error.

Every verb takes exactly its arguments (anything more is ``ERR usage``), and
a reply is whole: the block is read before any row is formed, so ``ERR
range`` never follows a partial listing.  A handler returns the reply's
text lines, which the monitor sends as text whatever its framing
(``serve``'s ``native``), since the spec is text.

This file runs on the board, so it uses only what MicroPython has.
"""

import sys

MAX_BLOCK = 64
ADDRESS_BITS = 32
ROW_BYTES = 16

# The last address, and the largest number a 32-bit count field holds.
U32_MAX = 0xFFFFFFFF

_HEX_DIGITS = "0123456789abcdefABCDEF"


def _err(reason):
    """A failure reply: the one line ``ERR <reason>``."""
    return ["ERR " + reason]


def _hex(word):
    """``word``'s value when it is 1 to 8 hex digits with no prefix, else None."""
    if not 1 <= len(word) <= 8 or any(char not in _HEX_DIGITS for char in word):
        return None
    return int(word, 16)


def _address(word):
    """An address: hex with an optional ``0x``, at most eight digits; None otherwise."""
    return _hex(word[2:] if word[:2] in ("0x", "0X") else word)


def _count(word):
    """A decimal count that fits 32 bits; None otherwise."""
    if not word or any(char not in "0123456789" for char in word):
        return None
    value = int(word)
    return value if value <= U32_MAX else None


def _row(addr, data):
    """One data row: the address of its first byte (ADDRESS_BITS / 4 digits), a colon, the bytes in hex."""
    return f"{addr:08X}:" + "".join(f" {byte:02X}" for byte in data)


def widths(addr, count):
    """``(offset, width)`` per access over ``count`` bytes from ``addr``: aligned words, then halves, then bytes.

    The access pattern of termapy_mem.c's direct helpers, so a peripheral
    register is read and written whole, at its own width.
    """
    offset = 0
    while offset < count:
        at = addr + offset
        remaining = count - offset
        if at % 4 == 0 and remaining >= 4:
            width = 4
        elif at % 2 == 0 and remaining >= 2:
            width = 2
        else:
            width = 1
        yield offset, width
        offset += width


class Memory:
    """The MEM verbs over a platform's two byte operations, an optional IRQ lock and an optional guard.

    Args:
        read: ``read(addr, count)``: the ``count`` bytes at ``addr``, or
            None when the platform refuses them.
        write: ``write(addr, data)``: stores ``data`` at ``addr``; returns
            False when the platform refuses.
        lock: Masks interrupts and returns what ``unlock`` restores.  With
            ``unlock`` it makes MEM.M atomic; without, MEM.M answers
            ``ERR usage``.
        unlock: Restores what ``lock`` returned.
        guard: ``guard(addr, count, write)``: True lets the access run,
            False refuses it as ``ERR range`` before anything is touched.
            None lets every address through.
    """

    def __init__(self, read, write, lock=None, unlock=None, guard=None):
        self.read = read
        self.write = write
        self.lock = lock
        self.unlock = unlock
        self.guard = guard

    def commands(self):
        """The verbs as monitor commands, name to ``(help, handler)``; each handler returns its reply lines."""
        return {
            "MEM.R": ("read memory: MEM.R <addr> <len>, the address in hex", self._read),
            "MEM.W": ("write memory: MEM.W <addr> <hex bytes>", self._write),
            "MEM.M": ("masked write with IRQs off: MEM.M <addr> <and> <or>", self._modify),
            "MEM.INFO": (
                "the memory interface: block limit, address width, byte order",
                self._info,
            ),
        }

    def _allowed(self, addr, count, write):
        """True when the access stays below the top of the address space and the guard lets it run."""
        if addr + count - 1 > U32_MAX:
            return False
        return self.guard is None or self.guard(addr, count, write)

    def _read(self, args):
        """MEM.R <addr> <len>: the block as rows, then OK."""
        if len(args) != 2:
            return _err("usage")
        addr = _address(args[0])
        count = _count(args[1])
        if addr is None or count is None:
            return _err("usage")
        if not 1 <= count <= MAX_BLOCK:
            return _err("length")
        if not self._allowed(addr, count, False):
            return _err("range")
        data = self.read(addr, count)
        if data is None:
            return _err("range")
        rows = [
            _row(addr + offset, data[offset : offset + ROW_BYTES])
            for offset in range(0, count, ROW_BYTES)
        ]
        return rows + ["OK"]

    def _write(self, args):
        """MEM.W <addr> <hex>: store the bytes, then OK; spaces may separate the pairs."""
        if len(args) < 2:
            return _err("usage")
        addr = _address(args[0])
        if addr is None:
            return _err("usage")
        data = bytearray()
        for word in args[1:]:
            for start in range(0, len(word), 2):
                pair = word[start : start + 2]
                value = _hex(pair) if len(pair) == 2 else None
                if value is None:
                    return _err("usage")
                if len(data) >= MAX_BLOCK:
                    return _err("length")
                data.append(value)
        if not self._allowed(addr, len(data), True):
            return _err("range")
        if self.write(addr, bytes(data)) is False:
            return _err("range")
        return ["OK"]

    def _modify(self, args):
        """MEM.M <addr> <and> <or>: ``word = (word & and) | or`` with IRQs off; the reply is the word before."""
        if self.lock is None or self.unlock is None or len(args) != 3:
            return _err("usage")
        addr = _address(args[0])
        and_mask = _hex(args[1])
        or_mask = _hex(args[2])
        if addr is None or and_mask is None or or_mask is None or len(args[1]) != len(args[2]):
            return _err("usage")
        # The masks' digit count is the width: 2, 4 or 8 digits for a byte, a half or a word.
        digits = len(args[1])
        width = 1 if digits <= 2 else 2 if digits <= 4 else 4
        if addr % width:
            return _err("usage")
        # Asked before the lock, as a read and as a write: a refusal never masks IRQs.
        if not (self._allowed(addr, width, False) and self._allowed(addr, width, True)):
            return _err("range")
        state = self.lock()
        try:
            old = self.read(addr, width)
            stored = False
            if old is not None:
                # The word as the core sees it, so the masks apply on either byte order.
                value = int.from_bytes(old, sys.byteorder)
                new = ((value & and_mask) | or_mask).to_bytes(width, sys.byteorder)
                stored = self.write(addr, new) is not False
        finally:
            self.unlock(state)
        if not stored:
            return _err("range")
        return [_row(addr, old), "OK"]

    def _info(self, args):
        """MEM.INFO: the block limit, address width, byte order, and that MEM.M is there."""
        if args:
            return _err("usage")
        endian = "le" if sys.byteorder == "little" else "be"
        modify = ', "modify": true' if self.lock is not None and self.unlock is not None else ""
        # By hand rather than json.dumps: MicroPython's dicts keep no order, and this is the C's order.
        info = (
            f'{{"max_block": {MAX_BLOCK}, "address_bits": {ADDRESS_BITS}, '
            f'"endian": "{endian}"{modify}}}'
        )
        return [info, "OK"]


def direct(guard=None):
    """``Memory`` over the chip's own bus through machine.mem8/16/32, with the IRQ lock: what a board runs.

    Args:
        guard: The board's own rules, ``guard(addr, count, write)``; see ``Memory``.
    """
    import machine

    # Each access width's view of the bus.
    views = {1: machine.mem8, 2: machine.mem16, 4: machine.mem32}

    def read(addr, count):
        data = bytearray()
        for offset, width in widths(addr, count):
            # Masked to the width: a port may hand a word back signed.
            value = views[width][addr + offset] & ((1 << (8 * width)) - 1)
            data.extend(value.to_bytes(width, sys.byteorder))
        return bytes(data)

    def write(addr, data):
        for offset, width in widths(addr, len(data)):
            word = int.from_bytes(data[offset : offset + width], sys.byteorder)
            views[width][addr + offset] = word

    return Memory(read, write, machine.disable_irq, machine.enable_irq, guard)
