# File Transfer

Transfer files to and from serial devices using the XMODEM protocol.
XMODEM is widely supported by bootloaders, firmware updaters, and
embedded systems.

## Send a File

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

## Receive a File

```text
/xmodem.recv <file>
```

Receive a file from the device and save it to the `cap/` directory.
Supports auto-numbered filenames with `$(n000)`.

```text
/xmodem.recv dump.bin
/xmodem.recv log_$(n000).bin
```

Trigger the device to start sending before or after running this
command, depending on your device's XMODEM implementation.

## Protocol Details

- XMODEM with automatic CRC/checksum negotiation
- 128-byte blocks
- Progress reported during transfer
- Serial display is suppressed during transfer and resumes afterward
- Transfer can be interrupted by disconnecting

## Demo Mode

The demo device supports XMODEM for testing without hardware:

```text
AT+XMODEM=RECV     device enters receive mode (accepts files)
AT+XMODEM=SEND     device sends a canned 256-byte test payload
```

Example session:

```text
AT+XMODEM=SEND
/xmodem.recv test.bin         receives 256 bytes to cap/test.bin
```

## Typical Workflows

**Firmware update via bootloader:**

```text
# 1. Enter bootloader mode (device-specific)
AT+BOOTLOADER
# 2. Send firmware
/xmodem.send firmware.bin
```

**Pull a log dump from a device:**

```text
# 1. Tell device to dump its log
AT+LOGDUMP
# 2. Receive it
/xmodem.recv log_$(n000).bin
```

See [Data Capture](data-capture.md) for passive serial capture to files.
