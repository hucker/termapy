# File Transfer

Transfer files to and from serial devices using XMODEM or YMODEM
protocols. Both are widely supported by bootloaders, firmware updaters,
and embedded systems.

## XMODEM

### Send a File

```text
/xmodem.send <file>
```

Send a file from your PC to the device. The file path is resolved
relative to the per-config `cap/` directory, or provide an absolute path.

```text
/xmodem.send firmware.bin
/xmodem.send C:\builds\release.hex
```

The device must be waiting to receive via XMODEM before you run this
command (e.g. after entering a bootloader's receive mode).

### Receive a File

```text
/xmodem.recv <file>
```

Receive a file from the device and save it to the `cap/` directory.
Supports auto-numbered filenames with `$(n000)`.

```text
/xmodem.recv dump.bin
/xmodem.recv log_$(n000).bin
```

### Protocol Details

- Automatic CRC/checksum negotiation
- 128-byte blocks
- Progress reported during transfer

## YMODEM

YMODEM extends XMODEM with batch file transfer, 1024-byte blocks,
and automatic filename/size metadata in the protocol header.

### Send File(s)

```text
/ymodem.send <file> {file2} ...
```

Send one or more files. YMODEM includes the filename and size in the
protocol, so the receiver knows what to expect.

```text
/ymodem.send firmware.bin
/ymodem.send config.json data.bin     batch send
```

### Receive File(s)

```text
/ymodem.recv {directory}
```

Receive files from the device. YMODEM provides the filename
automatically. Files are saved to the specified directory, or `cap/`
by default.

```text
/ymodem.recv                          save to cap/
/ymodem.recv C:\downloads             save to specific directory
```

### YMODEM Protocol Details

- 1024-byte blocks (1K) with CRC-16
- Filename and filesize sent in protocol header
- Batch transfer: send/receive multiple files in one session

## Common Details

- Serial display is suppressed during transfer and resumes afterward
- Transfer can be interrupted by disconnecting
- Progress reported during transfer

## XMODEM vs YMODEM

| Feature           | XMODEM              | YMODEM              |
|-------------------|---------------------|---------------------|
| Block size        | 128 bytes           | 1024 bytes          |
| Error detection   | CRC-16 or checksum  | CRC-16 always       |
| Filename in protocol | No               | Yes                 |
| Batch transfer    | No                  | Yes                 |
| Use when          | Simple bootloaders  | Larger files, batch |

## Demo Mode

The demo device supports both protocols for testing without hardware:

```text
AT+XMODEM=RECV     device enters XMODEM receive mode
AT+XMODEM=SEND     device sends a canned 256-byte payload via XMODEM
AT+YMODEM=RECV     device enters YMODEM receive mode
AT+YMODEM=SEND     device sends a canned 2048-byte payload via YMODEM
```

## Typical Workflows

**Firmware update via bootloader:**

```text
# 1. Enter bootloader mode (device-specific)
AT+BOOTLOADER
# 2. Send firmware
/ymodem.send firmware.bin
```

**Pull a log dump from a device:**

```text
# 1. Tell device to dump its log
AT+LOGDUMP
# 2. Receive it
/xmodem.recv log_$(n000).bin
```

See [Data Capture](data-capture.md) for passive serial capture to files.
