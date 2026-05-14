# Serial tools

Interactive commands for sending raw bytes, computing CRCs, and
inspecting serial data. These are REPL commands you type at the
prompt -- no script files needed.

For automated send/expect test scripts, see [Protocol Testing](protocol-testing.md).

![Proto subcommands](img/doc_03_help_proto.svg)

## Send bytes

`/proto.send` transmits raw bytes and displays the response. No line ending
is appended -- you control exactly what goes on the wire.

### Data formats

Hex bytes and quoted strings can be mixed freely:

```text
/proto.send 01 03 00 00 00 0A           hex bytes
/proto.send "HELLO\r"                    quoted text (supports \r \n \t \0 \\)
/proto.send 02 "DATA" 03                mix hex and text
/proto.send 0x01 "hello" 0D             0x prefix is optional
```

### Inline delays

![Inline delay example](img/doc_06_proto_delay.svg)

Insert timing gaps with `~duration` between data segments:

```text
/proto.send 00 ~25ms "AT\r"             wake byte, 25ms pause, then command
/proto.send "\r" ~5ms "AT+INFO\r"       CR to wake, 5ms settle, then query
/proto.send ~500us 01 02 03             delay before first byte
```

Supported units: `us` (microseconds), `ms` (milliseconds), `s` (seconds).

Timing precision: delays under 1ms use a spin-wait loop for accuracy.
Delays >= 1ms use OS sleep. Sub- 2 millisecond delays are best-effort due
to USB frame timing (~1ms boundaries on Full Speed USB).

### CRC append

![CRC append example](img/doc_05_proto_crc.svg)

If the first word matches a CRC algorithm name, the CRC is computed over
the data and appended automatically:

```text
/proto.send crc16-modbus 01 03 00 00 00 0A      append Modbus CRC (LE)
/proto.send crc16-modbus_be 01 03 00 00 00 0A   big-endian CRC
/proto.send crc16-modbus_ascii 01 03 00 00 00 0A CRC as hex text (e.g. "C5CD")
```

Suffixes: `_le` (little-endian, default), `_be` (big-endian), `_ascii`
(CRC appended as hex text instead of binary bytes).

When combined with delays, CRC is computed on all data bytes concatenated
(delays are excluded from the CRC calculation).

### Response display

![Proto send example](img/doc_04_proto_send.svg)

Both TX and RX show hex bytes and a smart text representation:

```text
  TX: 72 65 76 0D  "rev\r"
  RX: 42 37 20 26 20 42 38 0D 0A  "B7 & B8\r\n"
  (9 bytes, 73ms)
```

Packets longer than 16 bytes use a multi-line hex dump with ASCII sidebar.
Round-trip timing includes all inline delays.

## Hex display mode

Toggle hex display for all serial I/O with `/proto.hex on` / `/proto.hex off`.

## CRC algorithms

Every named CRC algorithm in termapy comes from the [reveng CRC
catalogue](https://reveng.sourceforge.io/crc-catalogue/all.htm)
maintained by **Greg Cook**.  That catalogue documents the polynomial,
init, reflection, and xor-out parameters for every standardized CRC
in practical use, and our test suite verifies each one against its
published `check` value on every commit.

64 algorithms are built in covering CRC-8, CRC-16, and CRC-32
families (Modbus, XMODEM, CCITT, USB, and more).

REPL commands:

- `/proto.crc.list` - show all 64 algorithms
- `/proto.crc.list *modbus*` - filter by pattern
- `/proto.crc.info crc16-modbus` - show algorithm parameters
- `/proto.crc.calc crc16-modbus 01 03 00 00 00 0A` - compute CRC
- `/proto.crc.find bin=01 03 00 00 00 0A C5 CD` - identify the algorithm used in a captured packet

### Identifying a CRC from a captured packet

`/proto.crc.find` takes the full packet you captured and figures out
which catalogue algorithm produced its CRC.  Two input forms:

- `bin=<hex bytes>` -- raw binary packet.  The last 1 / 2 / 4 bytes
  are tried as the CRC field; both big- and little-endian are
  attempted.
- `asc=<text>` -- ASCII packet with a trailing hex-encoded CRC
  (common in NMEA-style protocols).  The last 2 / 4 / 8 characters
  are parsed as hex.

Every match reports the algorithm name, field width, byte order,
expected value, and the length of the preceding data.  Catalogue
aliases (e.g. `crc16-modbus` / `crc16m`) are collapsed into a single
line.  When exactly one algorithm matches, the output also includes
the command to generate standalone source code.

```text
> /proto.crc.find bin=31 32 33 34 35 36 37 38 39 37 4B
  1 match:
  crc16-modbus  (aka crc16m)  width=16  field=last2  expected=0x4B37  endian=le  data=9 bytes

  Generate source: /proto.crc.c crc16-modbus  (or .python / .rust)
```

Limits:

- The search only covers the built-in catalogue (CRC-8, CRC-16,
  CRC-32 standard algorithms from the reveng catalogue).  A truly
  custom CRC with non-standard poly / init / refin / refout / xorout
  will not match -- the parameter space is ~10^15 for 16-bit, so
  brute-force is not tractable.  For custom CRCs, Greg Cook's
  [reveng project](https://reveng.sourceforge.io) implements an
  algebraic recovery approach that needs only a handful of matched
  sample packets; it's the established tool for that job.
- The tool assumes the CRC field is at the end of the packet.
  Protocols with the CRC in the middle or as a non-contiguous
  checksum require a different approach.
- Multiple matches usually mean the packet is too short to
  disambiguate.  Capture a second packet with a different CRC and
  run find again; the intersection narrows the candidates.

Aliases: `crc16m` = `crc16-modbus`, `crc16x` = `crc16-xmodem`.

In format specs and `/proto.send`, CRC algorithm names accept suffixes:
`_le` (little-endian, default), `_be` (big-endian), `_ascii` (hex text).

## CRC code generation

![CRC Python code generation](img/doc_07_crc_python.svg)

Generate a standalone CRC function in C, Python, or Rust for any
algorithm in the catalogue. Two implementations available:

```text
/proto.crc.python crc16-modbus           bit-by-bit (small, no tables)
/proto.crc.python crc16-modbus --table   table-driven (fast, 256-entry lookup)
/proto.crc.c crc16-xmodem               C bit-by-bit
/proto.crc.c crc16-xmodem --table       C table-driven
/proto.crc.rust crc32                    Rust bit-by-bit
/proto.crc.rust crc32 --table           Rust table-driven
```

**Bit-by-bit** -- compact code, zero RAM overhead. Best for
microcontrollers with limited memory (PIC, ATtiny).

**Table-driven** (`--table`) -- 4-8x faster. Pre-computes a
256-entry lookup table. Uses 256-1024 bytes of RAM depending
on CRC width.

All algorithm parameters (polynomial, init, reflect, xorout) are
baked into the generated code. Copy-paste into your firmware or
test script.

### Example: bit-by-bit vs table-driven

For `/proto.crc.python crc16-cms`:

```python
def crc16_cms(data: bytes) -> int:
    """crc16-cms - CMS (RPM package format)

    check: crc(b'123456789') == 0xAEE7
    """
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x8005
            else:
                crc <<= 1
            crc &= 0xFFFF
    return crc
```

And `/proto.crc.python crc16-cms --table`:

```python
_TABLE = (
    0x0000, 0x8005, 0x800F, 0x000A, 0x801B, 0x001E, 0x0014, 0x8011,
    # ... 248 more entries ...
)

def crc16_cms(data: bytes) -> int:
    """crc16-cms - CMS (RPM package format)

    check: crc(b'123456789') == 0xAEE7
    """
    crc = 0xFFFF
    for byte in data:
        crc = _TABLE[((crc >> 8) ^ byte) & 0xFF] ^ (crc << 8) & 0xFFFF
    return crc
```

Both forms return `0xAEE7` for `b"123456789"` -- the docstring shows the
catalogue check value so you can verify after pasting.

**NOTE:** Currently only the generated Python code is test-verified
against all 62 catalogue check values (both bit-by-bit and
table-driven). C and Rust output is structurally correct but not
compiled/verified by the test suite.

**Custom CRC plugins** for non-standard checksums:

```python
# sum8.py - drop into builtins/crc/ or termapy_cfg/<name>/crc/
NAME = "sum8"
WIDTH = 1

def compute(data: bytes) -> int:
    return sum(data) & 0xFF
```
