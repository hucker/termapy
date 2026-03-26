# Protocol Testing

The `/proto` command provides binary protocol testing for request-response serial protocols.

## Interactive Send

Send raw hex bytes and see the response:

```text
/proto.send 01 03 00 00 00 0A C5 CD
/proto.send "HELLO\r"
/proto.send 02 "DATA" 03
```

## Protocol Test Scripts

Create `.pro` files in the per-config `proto/` folder with send/expect sequences:

```text
# example.pro
@timeout 1000ms
@frame_gap 50ms

label: Read registers
send: 01 03 00 00 00 0A C5 CD
expect: 01 03 14 ** ** ** ** ** ** ** ** ** ** ** ** ** ** ** ** ** ** ** **

label: Write register
send: 01 06 00 01 00 03 98 0B
expect: 01 06 00 01 00 03 98 0B
timeout: 500ms

# Text protocols work too
label: AT query
send: "AT+VERSION?\r"
expect: "V1." ** ** "\r"
```

Run with `/proto.run example.pro`. Each step reports PASS/FAIL.

## Script Directives

- `@timeout <duration>` — default expect timeout (default 1000ms)
- `@frame_gap <duration>` — silence gap to detect frame end (default 50ms)
- `@strip_ansi` — strip ANSI escape sequences from responses before matching
- `label: <text>` — name for the next step
- `send: <hex or "text">` — transmit raw bytes (no line ending appended)
- `expect: <pattern>` — wait for response and match (`**` = any byte)
- `timeout: <duration>` — per-step timeout override
- `delay: <duration>` — fixed sleep
- `flush: <duration>` — wait for serial silence, then discard received bytes
- `cmd: <text>` — send a plain text command with config line ending

## Hex Display Mode

Toggle hex display for all serial I/O with `/proto.hex on` / `/proto.hex off`.

## Packet Visualizers

The proto debug screen uses pluggable visualizers to decode packet bytes into
named columns. Built-in visualizers (Hex, Text, Modbus) ship with termapy. Add
your own by dropping a `.py` file into `termapy_cfg/<config>/viz/`.

Multiple visualizers can be active at once via the checklist. Enable "Show viz
string" to display the raw format spec above each table.

**Selecting visualizers in .pro files:**

Use `viz` in the script header to limit which visualizers appear in the dropdown.
Use `viz` in a `[[test]]` section to force that visualizer for the test:

```toml
viz = ["Modbus"]          # header: only show Hex, Text, Modbus in dropdown

[[test]]
name = "Read registers"
viz = "Modbus"            # force Modbus view for this test
send = "01 03 00 00 00 01 84 0A"
expect = "01 03 02 00 07 F9 86"
```

## Format Spec Language

Visualizers and binary capture use a format spec to map bytes to columns:

```text
Slave:H1 Func:H2 Addr:U3-4 Count:U5-6 CRC:crc16-modbus_le
```

Type codes: `H` (hex), `U` (unsigned), `I` (signed), `S` (string), `F` (float),
`B` (bit field), `_` (padding), `crc*` (CRC verify). Byte indices are 1-based;
byte order determines endianness (`U3-4` = big-endian, `U4-3` = little-endian).
Use `H7-*` for variable-length fields.

## CRC Algorithms

62 named CRC algorithms are built in: `crc16-modbus`, `crc16-xmodem`,
`crc16-ccitt-false`, `crc8`, `crc32`, `crc32-iscsi`, and many more.

- `/proto.crc.list` — browse all algorithms
- `/proto.crc.help <name>` — show parameters
- `/proto.crc.calc <name> {data}` — compute CRC interactively

Aliases: `crc16m` = `crc16-modbus`, `crc16x` = `crc16-xmodem`.
Endianness suffix: `_le` or `_be`.

---

| | | |
