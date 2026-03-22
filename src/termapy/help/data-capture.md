# Data Capture

Capture serial output to files without interrupting normal display or logging.

## Text Capture (timed)

```text
/text_cap <mode> <file> <duration> {cmd=command...}
/text_cap.stop
```

Modes: `append`/`a`, `new`/`n`. Duration: e.g. `2s`, `500ms`.
Everything after `cmd=` is sent to the device after capture starts.
Data is written as ANSI-stripped text, one line at a time.

```text
/text_cap n log.txt 3s cmd=AT+INFO
/text_cap a session.txt 10s
```

## Binary Capture (sized)

```text
/bin_cap <mode> <file> {fmt=spec} <cap_vals=N|cap_bytes=N> {sep=comma|tab|space} {echo} {cmd=command...}
/bin_cap.stop
```

Use `fmt=` with the format spec language to define the record structure.
Byte ranges are 1-based. Omit `fmt=` for raw binary capture.

- `cap_vals=N` — number of records (record size derived from format spec)
- `cap_bytes=N` — total bytes (works with or without format spec)
- `sep=comma|tab|space` — column separator (default comma, produces CSV)
- `echo` — print formatted values to terminal
- Header row written when columns have names (e.g. `Temp:U1-2`)

## Format Spec Examples

```text
# Single unsigned 16-bit column (big-endian)
/bin_cap n data.csv fmt=Val:U1-2 cap_vals=50 cmd=AT+BINDUMP u16 50

# Mixed-type record: string + integers + float (little-endian)
/bin_cap n mixed.csv fmt=Label:S1-10 Counter:U11 Val16:U13-12 Val32:U17-14 Temp:F21-18 cap_vals=20 cmd=AT+BINDUMP 20

# Hex dump of raw bytes
/bin_cap n packets.csv fmt=Header:H1-4 Payload:H5-20 cap_vals=100 cmd=stream

# Tab-separated with echo
/bin_cap n log.tsv fmt=A:U1-2 B:F3-6 cap_vals=100 sep=tab echo cmd=read

# Raw binary (no format spec)
/bin_cap n raw.bin cap_bytes=256 cmd=read_all
```

## Format Spec Quick Reference

| Spec      | Meaning                              |
|-----------|--------------------------------------|
| `U1`      | 1-byte unsigned                      |
| `U1-2`    | 2-byte unsigned, big-endian          |
| `U2-1`    | 2-byte unsigned, little-endian       |
| `U1-4`    | 4-byte unsigned                      |
| `I1-2`    | 2-byte signed integer                |
| `F1-4`    | 4-byte IEEE 754 float                |
| `F1-8`    | 8-byte double                        |
| `S1-10`   | 10-byte ASCII string                 |
| `H1-4`    | 4 bytes as combined hex              |

See [Protocol Testing](protocol-testing.md) for the full format spec language.

## Auto-Numbered Filenames

Use `$(n000)` in filenames for auto-incrementing sequence numbers.
The number of zeros sets the digit width (max 3). A counter file in `cap/`
tracks the last-used number across sessions, with rollover.

| Pattern     | Range   |
|-------------|---------|
| `$(n0)`     | 0–9     |
| `$(n00)`    | 00–99   |
| `$(n000)`   | 000–999 |

```text
/text_cap n log_$(n000).txt 3s cmd=AT+INFO
# → log_000.txt, log_001.txt, log_002.txt, ...
```

Bare filenames are saved to the per-config `cap/` directory.
A progress bar and Stop button overlay the toolbar during capture.
The **Cap** button opens the cap/ folder.

---

| | | |
|:---:|:---:|:---:|
| [← Protocol Testing](protocol-testing.md) | [Index](index.md) | [Demo Mode →](demo.md) |
