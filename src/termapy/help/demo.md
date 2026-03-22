# Demo Mode

Try termapy without hardware using the built-in simulated device:

```sh
termapy --demo
```

This creates a `termapy_cfg/demo/` config that auto-connects to a simulated serial device. You can also set `"port": "DEMO"` in any config file.

## Available Commands

| Command                   | Response                             |
| ------------------------- | ------------------------------------ |
| `AT`                      | `OK`                                 |
| `AT+INFO`                 | Device info, uptime, free memory     |
| `AT+TEMP`                 | Simulated temperature reading        |
| `AT+LED on\|off`          | Toggle LED state                     |
| `AT+STATUS`               | LED state, uptime, connections       |
| `AT+NAME` / `AT+NAME=val` | Query or set device name            |
| `AT+BAUD` / `AT+BAUD=val` | Query or set baud rate              |
| `AT+PROD-ID`              | Returns product ID (`BASSOMATIC-77`) |
| `AT+RESET`                | Simulated reboot sequence            |
| `mem <addr> [len]`        | Hex memory dump                      |
| `AT+TEXTDUMP <n>`         | Emit n lines of text readings        |
| `AT+BINDUMP <n>`          | Emit n mixed 21-byte records (S10+U8+U16+U32+F4) |
| `AT+BINDUMP <type> <n>`   | Emit n typed binary values           |
| `help`                    | List available commands              |

## Bundled Files

The demo config includes example scripts and protocol tests:

- **Scripts:** `at_demo.run`, `smoke_test.run`, `status_check.run`
- **Proto:** `at_test.pro` (AT command tests), `bitfield_inline.pro`, `modbus_inline.pro` (Modbus RTU tests)
- **Plugin:** `probe.py` — demo plugin showing serial I/O (drain, write, read, parse). Try `/probe` to run a device survey, or `/help.dev probe` to see the annotated source as a plugin-writing guide.

The simulated device also responds to binary Modbus RTU frames (function codes 0x03 read registers, 0x06 write register) for proto debug testing.

## Try These Commands

```sh
AT                              # connection test → OK
AT+INFO                         # device info
AT+LED on                       # turn LED on
AT+STATUS                       # check LED state, uptime
mem 0x1000 32                   # hex memory dump
```

For Modbus binary commands, use `/proto.send` with hex bytes (CRC included):

```sh
/proto.send 01 03 00 00 00 01 84 0A       # read 1 register from addr 0
/proto.send 01 06 00 05 04 D2 1B 56       # write register 5 = 1234
/proto.send 01 03 00 05 00 01 94 0B       # read back register 5
```

## Data Capture Demo

### Text Capture

Capture 3 seconds of text output to a file:

```text
/text_cap n readings.txt 3s cmd=AT+TEXTDUMP 50
```

Append to an existing file:

```text
/text_cap a readings.txt 2s cmd=AT+TEXTDUMP 20
```

Auto-numbered text captures (creates readings_000.txt, readings_001.txt, ...):

```text
/text_cap n readings_$(n000).txt 3s cmd=AT+TEXTDUMP 50
```

### Binary Capture — Mixed Record (CSV)

The demo device streams 21-byte records with a string label, u8 counter,
u16, u32, and float. Capture 20 records to CSV:

```text
/bin_cap n mixed.csv fmt=Label:S1-10 Counter:U11 Val16:U13-12 Val32:U17-14 Temp:F21-18 cap_vals=20 cmd=AT+BINDUMP 20
```

### Binary Capture — Single Type

Capture 50 unsigned 16-bit values (little-endian) to CSV:

```text
/bin_cap n u16_data.csv fmt=Value:U2-1 cap_vals=50 cmd=AT+BINDUMP u16 50
```

### Binary Capture — Tab-Separated with Echo

Capture and also print values to the terminal:

```text
/bin_cap n debug.tsv fmt=A:U2-1 cap_vals=20 sep=tab echo cmd=AT+BINDUMP u16 20
```

### Binary Capture — Raw (No Format Spec)

Save raw bytes to a binary file:

```text
/bin_cap n raw_dump.bin cap_bytes=200 cmd=AT+BINDUMP u16 100
```

### Binary Capture — Auto-Numbered

Rotating capture files (data_000.csv, data_001.csv, ...):

```text
/bin_cap n data_$(n000).csv fmt=Label:S1-10 Counter:U11 Val16:U13-12 Val32:U17-14 Temp:F21-18 cap_vals=10 cmd=AT+BINDUMP 10
```

---

| | | |
|:---:|:---:|:---:|
| [← Data Capture](data-capture.md) | [Index](index.md) | |
