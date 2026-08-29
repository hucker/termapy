"""The address grammar -- what consumes a symbol table.

One whitespace-free token::

    address := head [ '.' suffix ]      suffix = text after the LAST dot,
                                        returned unconsumed (reserved)
    head    := exact-symbol | number | symref
    number  := '0x' HEX+ | HEX+ 'h' | DEC+     0x1000, 1000h, 1000 (DECIMAL)
    symref  := name [ '@' file ] [ ('+'|'-') number ]
    name    := [A-Za-z_$][A-Za-z0-9_$.]*       dots allowed: count.12, foo.isra.0

Resolution order inside a head: an exact table match wins (so ``count.12``
is the symbol, not bit 12 of ``count``), then a number, then a symbol
reference with an optional offset.  There is NO bare-hex guessing: ``1000``
is one thousand and ``CAFE`` is a name.  A trailing ``.suffix`` that no
exact match consumed comes back in ``Address.suffix`` for the typed-view
step to interpret as a bit, slice, or field; this module only splits it.

Every failure is a ``ValueError`` whose message is already phrased for a
``CmdResult.fail`` (``Unknown symbol: X``, ``Invalid address: X``,
``Invalid offset: X``, ``Ambiguous symbol: X (...)``, ``No symbols loaded.``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Final

from termapy.symbols.table import Symbol, SymbolTable, hex_digits


@dataclass(frozen=True, slots=True)
class Address:
    """A resolved address.

    Attributes:
        text: What the user typed, stripped.
        addr: The fully resolved base address (offset applied).
        symbol: The table entry the head NAMED; None for literals.  What is
            actually AT ``addr`` is the caller's ``table.lookup`` question.
        offset: The signed ``+/-off`` as typed (0 when none).
        suffix: Text after the last dot that no exact match consumed; empty
            when the head resolved whole.
    """

    text: str
    addr: int
    symbol: Symbol | None = None
    offset: int = 0
    suffix: str = ""


# Identifiers, dots included (GCC/XC32 emit count.12, foo.isra.0).  A '-' is
# admitted after the first character so ``main-zz`` reads as an unknown NAME
# rather than a malformed offset; C identifiers never contain one, so no
# real symbol is affected.
_NAME_RE: Final = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$.-]*$")
_HEX_0X_RE: Final = re.compile(r"^0[xX][0-9a-fA-F]+$")
_HEX_H_RE: Final = re.compile(r"^[0-9a-fA-F]+[hH]$")
_DEC_RE: Final = re.compile(r"^[0-9]+$")


def parse_number(text: str) -> int | None:
    """Parse a numeric literal: ``0x1000``, ``1000h`` / ``FFFFh``, or decimal.

    Never raises.  ``CAFE``, ``0x``, ``""``, ``-4`` and ``12g4`` are None.
    Shared with the memory commands' ``<len>`` and value tokens.

    Args:
        text: The token (surrounding whitespace tolerated).

    Returns:
        The value, or None when ``text`` is not a literal.
    """
    token = text.strip()
    if _HEX_0X_RE.match(token):
        return int(token[2:], 16)
    if _HEX_H_RE.match(token):
        return int(token[:-1], 16)
    if _DEC_RE.match(token):
        return int(token)
    return None


def parse_address(text: str, table: SymbolTable | None = None) -> Address:
    """Resolve one address token against an optional symbol table.

    Tries the whole token first; when that fails and the token has a dot,
    retries the text before the LAST dot and hands the remainder back as
    ``suffix``.  Only one dot is ever split off, and the error reported is
    always the one for what the user typed.

    Args:
        text: The token as typed.
        table: The loaded table, or None (literals still parse).

    Returns:
        The resolved Address.

    Raises:
        ValueError: A CLAUDE.md-phrased message (see the module docstring).
    """
    text = text.strip()
    if not text:
        raise ValueError("Invalid address: ''")
    if text.startswith(".") or text.endswith("."):
        raise ValueError(f"Invalid address: {text}")
    try:
        return _resolve(text, table)
    except ValueError as first:
        if "." not in text:
            raise
        head, _, suffix = text.rpartition(".")
        try:
            resolved = _resolve(head, table)
        except ValueError:
            raise first from None
        return replace(resolved, text=text, suffix=suffix)


def _resolve(text: str, table: SymbolTable | None) -> Address:
    """Resolve one head (no suffix handling): ``base [ +/- offset ]``.

    An exact table match on the WHOLE head wins first (a name may legally
    end in ``-1``); otherwise the base goes through :func:`_resolve_base`,
    so the exact-match rule holds for ``cafeh+4`` exactly as for ``cafeh``.
    """
    exact = _exact_match(text, table)
    if exact is not None:
        return exact
    base, offset_text, sign = _split_offset(text)
    offset = 0
    if sign:
        if not offset_text:
            raise ValueError(f"Invalid address: {text}")
        parsed_offset = parse_number(offset_text)
        if parsed_offset is None:
            raise ValueError(f"Invalid offset: {offset_text}")
        offset = sign * parsed_offset
    resolved = _resolve_base(base, table)
    addr = resolved.addr + offset
    if addr < 0:
        # An offset below the base's start; a negative address has no
        # rendering and no meaning on any bus.
        raise ValueError(f"Invalid address: {text}")
    return Address(text, addr, resolved.symbol, offset)


def _resolve_base(text: str, table: SymbolTable | None) -> Address:
    """Resolve an offset-free head: exact name, then number, then name."""
    exact = _exact_match(text, table)
    if exact is not None:
        return exact
    number = parse_number(text)
    if number is not None:
        return Address(text, number)
    name, _ = _split_file(text)
    if not _NAME_RE.match(name):
        raise ValueError(f"Invalid address: {text}")
    if table is None or not len(table):
        raise ValueError("No symbols loaded.")
    raise ValueError(f"Unknown symbol: {text}")


def _exact_match(text: str, table: SymbolTable | None) -> Address | None:
    """The one symbol ``text`` names (``@file`` split off), or None.

    Runs BEFORE the number rule so a hex-shaped name (``cafeh``) is the
    symbol.  Raises the ambiguity error when several match.
    """
    if table is None or not len(table):
        return None
    name, file = _split_file(text)
    hits = table.by_name(name, file)
    if len(hits) == 1:
        return Address(text, hits[0].addr, hits[0])
    if hits:
        raise ValueError(_ambiguous(text, name, hits, table.address_bits))
    return None


def _split_file(text: str) -> tuple[str, str]:
    """``name@file`` -> ``(name, file)``; no ``@`` -> ``(text, "")``.

    Raises:
        ValueError: A ``@`` with nothing after it (``main@``), which would
            otherwise silently read as the bare name.
    """
    name, at, file = text.partition("@")
    if at and not file:
        raise ValueError(f"Invalid address: {text}")
    return name, file


def _split_offset(text: str) -> tuple[str, str, int]:
    """Split a trailing ``+off`` / ``-off``: ``(base, offset_text, sign)``.

    A ``+`` at index >= 1 always splits (names and files never contain
    one).  A ``-`` splits only when what follows parses as a number, so a
    file stem like ``my-module.c`` is not an offset.  ``sign`` is 0 when
    nothing was split.
    """
    plus = text.find("+", 1)
    if plus > 0:
        return text[:plus], text[plus + 1:], 1
    minus = text.rfind("-")
    if minus > 0 and parse_number(text[minus + 1:]) is not None:
        return text[:minus], text[minus + 1:], -1
    return text, "", 0


def _ambiguous(typed: str, name: str, hits: list[Symbol], address_bits: int) -> str:
    """``Ambiguous symbol: tick (0x.. mon.c, 0x.. adc.c; use tick@mon.c or the address)``.

    Addresses are always listed so the message still helps when the
    converter left ``file`` empty; the hint names the first entry that
    HAS a file, or falls back to the address alone.
    """
    width = hex_digits(address_bits)
    entries = ", ".join(
        f"0x{symbol.addr:0{width}X} {symbol.file or '?'}" for symbol in hits
    )
    first_file = next((symbol.file for symbol in hits if symbol.file), "")
    hint = f"use {name}@{first_file} or the address" if first_file else "use the address"
    return f"Ambiguous symbol: {typed} ({entries}; {hint})"
