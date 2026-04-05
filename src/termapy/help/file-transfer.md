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

## File Paths

By default, file paths are resolved relative to the per-config `cap/`
directory. Absolute paths are always accepted.

```text
/xmodem.send firmware.bin              cap/firmware.bin
/xmodem.send C:\builds\release.hex     absolute path
/xmodem.recv dump.bin                  saved to cap/dump.bin
/xmodem.recv log_$(n000).bin           auto-numbered in cap/
/ymodem.send config.json               cap/config.json
/ymodem.recv                           saved to cap/ (filename from protocol)
/ymodem.recv C:\downloads              saved to specified directory
```

### Custom transfer root

```text
/xfer.root                             show current root
/xfer.root C:\builds\firmware          set a custom root
```

When set, both send and receive resolve relative paths against this
directory instead of `cap/`. The transfer root is shown as a clickable
path when a config loads (with verbose on).

This sets the `file_xfer_root` config key, which can also be edited
directly in the JSON config file.

The `cap/` directory lives inside the active config folder. For example,
if your config is `termapy_cfg/mydevice/mydevice.cfg`, then `cap/` is
`termapy_cfg/mydevice/cap/`. In demo mode, it's `termapy_cfg/demo/cap/`.

Click the **Cap** button in the toolbar to open the `cap/` folder.

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

The demo device has a virtual filesystem pre-loaded with sample files.

### Filesystem commands

```text
AT+FS.LIST                  list files with sizes
AT+FS.INFO                  file count and total size
AT+FS.DELETE <file>         delete a file
```

### File transfer

```text
AT+XMODEM=SEND <file>      send a file from device via XMODEM
AT+XMODEM=RECV <file>      receive into device VFS via XMODEM
AT+YMODEM=SEND <file>      send a file from device via YMODEM
AT+YMODEM=RECV              receive into device VFS (name from protocol)
```

Pre-loaded files: `config.dat` (64 bytes), `device_log.txt`,
`firmware_v1.bin` (2048 bytes).

### Example session

```text
AT+FS.LIST                          see what's on the device
/ymodem.recv                        pull firmware_v1.bin to your PC
/ymodem.send new_firmware.bin       push a file to the device
AT+FS.LIST                          see it arrived
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
