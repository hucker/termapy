# Bitfield format spec demo — uses Modbus writes + reads to exercise bit fields
#
# Demonstrates:
#   B4-5.0-2   — multi-byte bit range as integer (Mode)
#   B4-5.3-5   — multi-byte bit range as integer (Chan)
#   B4-5.8-11  — multi-byte bit range as integer (Gain)
#   b4-5.0-15  — full 16-bit control word as binary (MSB first)
#   b4-5.15-0  — same word as binary (LSB first / reversed)
#   U6-7       — unsigned 16-bit big-endian (Sensor)
#
# Uses the demo simulator's Modbus handler: write registers, then read
# them back so the response is deterministic and tests pass.
name = "Demo Bitfield Inline Format"
frame_gap = "20ms"  # silence gap that marks end of a response frame
quiet = false        # show decoded packets in the output

# Write control register (reg 0 = 0x0A2B: Mode=3, Chan=5, Gain=10)
[[test]]
name = "Setup — write control register"
send = "01 06 00 00 0A 2B CF 75"
send_fmt = "Title:Modbus_Write Slave:H1 Func:H2 Reg:U3-4 Value:H5-6 CRC:crc16-modbus_le"
expect = "01 06 00 00 0A 2B CF 75"
expect_fmt = "Title:Modbus_Echo Slave:H1 Func:H2 Reg:U3-4 Value:H5-6 CRC:crc16-modbus_le"

# Write sensor register (reg 1 = 0x04D2 = 1234)
[[test]]
name = "Setup — write sensor register"
send = "01 06 00 01 04 D2 5A 97"
send_fmt = "Title:Modbus_Write Slave:H1 Func:H2 Reg:U3-4 Value:U5-6 CRC:crc16-modbus_le"
expect = "01 06 00 01 04 D2 5A 97"
expect_fmt = "Title:Modbus_Echo Slave:H1 Func:H2 Reg:U3-4 Value:U5-6 CRC:crc16-modbus_le"

# Read back both registers — decode as bit fields (integer display)
[[test]]
name = "Bit fields as integers — Mode=3, Chan=5, Gain=10, Sensor=1234"
send = "01 03 00 00 00 02 C4 0B"
send_fmt = "Title:Modbus_Read Slave:H1 Func:H2 Addr:U3-4 Count:U5-6 CRC:crc16-modbus_le"
expect = "01 03 04 0A 2B 04 D2 0B 7E"
expect_fmt = "Title:Bitfield Slave:H1 Func:H2 Bytes:U3 Mode:B4-5.0-2 Chan:B4-5.3-5 Gain:B4-5.8-11 Sensor:U6-7 CRC:crc16-modbus_le"

# Same read — decode control word as binary strings
[[test]]
name = "Bit fields as binary — control word 0x0A2B"
send = "01 03 00 00 00 02 C4 0B"
send_fmt = "Title:Modbus_Read Slave:H1 Func:H2 Addr:U3-4 Count:U5-6 CRC:crc16-modbus_le"
expect = "01 03 04 0A 2B 04 D2 0B 7E"
expect_fmt = "Title:Binary Slave:H1 Func:H2 Bytes:U3 Ctrl:b4-5.0-15 Sensor:U6-7 CRC:crc16-modbus_le"

# Intentional FAIL — wrong expected Gain (expect 11 instead of 10)
[[test]]
name = "Expected fail — wrong Gain value"
send = "01 03 00 00 00 02 C4 0B"
send_fmt = "Title:Modbus_Read Slave:H1 Func:H2 Addr:U3-4 Count:U5-6 CRC:crc16-modbus_le"
expect = "01 03 04 0B 2B 04 D2 0A 82"
expect_fmt = "Title:Fail_Demo Slave:H1 Func:H2 Bytes:U3 Mode:B4-5.0-2 Chan:B4-5.3-5 Gain:B4-5.8-11 Sensor:U6-7 CRC:crc16-modbus_le"
