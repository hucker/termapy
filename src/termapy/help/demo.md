# Demo Mode

The demo device is a simulated serial device for exploring termapy
without hardware. See [Installation](installation.md) for how to start it.

## Entering Demo Mode

```sh
termapy --demo                   # from the command line
```

Or from within termapy:

- `/demo` — switch to the demo config (creates it if needed)
- `/demo.force` — reset demo to defaults and switch to it

## What You Get

The demo creates a complete project at `termapy_cfg/demo/` with:

- A simulated device called **BASSOMATIC-77** that responds to AT
  commands, GPS/NMEA queries, and binary Modbus RTU frames
- **5 toolbar buttons**: Demo Help, AT Demo, Info, Probe, TempPlot
- **6 scripts**: welcome, at_demo, gps_demo, smoke_test, status_check, var_demo
- **3 protocol test files**: at_test, bitfield_inline, modbus_inline
- **3 plugins**: cmd (custom shortcut), probe (device query), temp_plot (sparkline)

## Device Commands

The simulated device supports three protocols:

### AT Commands (text)

| Command                    | Response                                |
| -------------------------- | --------------------------------------- |
| `AT`                       | `OK` — connection test                  |
| `AT+INFO`                  | Device info, uptime, free memory        |
| `AT+TEMP`                  | Simulated temperature reading (22-25C)  |
| `AT+LED on\|off`           | Toggle LED state                        |
| `AT+STATUS`                | LED state, uptime, connections          |
| `AT+NAME?` / `AT+NAME=val` | Query or set device name                |
| `AT+BAUD?` / `AT+BAUD=val` | Query or set baud rate                  |
| `AT+PROD-ID`               | Product ID (`BASSOMATIC-77`)            |
| `AT+RESET`                 | Simulated reboot sequence               |
| `AT+HELP.JSON`             | Device command help (JSON)              |
| `AT+TEXTDUMP <n>`          | Emit text readings                      |
| `AT+BINDUMP <n>`           | Emit binary records                     |
| `mem <addr> [len]`         | Hex memory dump                         |

### GPS / NMEA

| Command  | Response                                      |
| -------- | --------------------------------------------- |
| `$GPGGA` | Position fix (lat, lon, altitude, satellites) |
| `$GPRMC` | Recommended minimum nav (pos, speed, date)    |
| `$GPGSA` | DOP and active satellites                     |
| `$GPGSV` | Satellites in view                            |

### Modbus RTU (binary)

Use `/proto.send` with hex bytes (CRC included):

```text
/proto.send 01 03 00 00 00 01 84 0A       # read 1 register
/proto.send 01 06 00 05 04 D2 1B 56       # write register 5 = 1234
/proto.send 01 03 00 05 00 01 94 0B       # read back register 5
```

Supports function codes 0x03 (read holding registers) and 0x06
(write single register) with CRC16 enforced.

## Data Capture Commands

### Text capture

```text
/cap.text readings.txt timeout=3s cmd=AT+TEXTDUMP 50
```

### Binary capture — mixed record to CSV

```text
/cap.struct mixed.csv fmt=Label:S1-10 Counter:U11 Val16:U13-12 Val32:U17-14 Temp:F21-18 records=20 cmd=AT+BINDUMP 20
```

### Binary capture — single type

```text
/cap.struct u16_data.csv fmt=Value:U2-1 records=50 cmd=AT+BINDUMP u16 50
```

## Demo Plugins

| Plugin       | Command      | What it does                                     |
| ------------ | ------------ | ------------------------------------------------ |
| cmd.py       | `/cmd`       | Custom shortcut — wraps a device command         |
| probe.py     | `/probe`     | Device survey — send/receive cycle with output   |
| temp_plot.py | `/temp_plot` | Sample temperature N times, draw ASCII sparkline |

`probe.py` is the best starting template for writing your own plugins.
See [Writing Plugins](writing-plugins.md) for details.

---
