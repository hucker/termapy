# Modbus RTU protocol test — using inline format specs
name = "Demo Modbus Inline Format"
frame_gap = "20ms"
quiet = false

[[test]]
name = "Read 1 register from addr 0"
send = "01 03 00 00 00 01 84 0A"
send_fmt = "Title:Modbus_TX Slave:H1 Func:H2 Addr:U3-4 Count:U5-6 CRC:crc16-modbus_le"
expect = "01 03 02 00 07 F9 86"
expect_fmt = "Title:Modbus_Response Slave:H1 Func:H2 Bytes:U3 R0:U4-5 CRC:crc16-modbus_le"

[[test]]
name = "Read 2 registers from addr 0"
send = "01 03 00 00 00 02 C4 0B"
send_fmt = "Title:Modbus_TX Slave:H1 Func:H2 Addr:U3-4 Count:U5-6 CRC:crc16-modbus_le"
expect = "01 03 04 00 07 00 14 4B FD"
expect_fmt = "Title:Modbus_Response Slave:H1 Func:H2 Bytes:U3 R0:U4-5 R1:U6-7 CRC:crc16-modbus_le"

[[test]]
name = "Read 5 registers from addr 100"
send = "01 03 00 64 00 05 C4 16"
send_fmt = "Title:Modbus_TX Slave:H1 Func:H2 Addr:U3-4 Count:U5-6 CRC:crc16-modbus_le"
expect = "01 03 0A 05 1B 05 28 05 35 05 42 05 4F 8C 46"
expect_fmt = "Title:Modbus_Response Slave:H1 Func:H2 Bytes:U3 R0:U4-5 R1:U6-7 R2:U8-9 R3:U10-11 R4:U12-13 CRC:crc16-modbus_le"

[[test]]
name = "Write register 5 = 1234"
send = "01 06 00 05 04 D2 1B 56"
send_fmt = "Title:Modbus_TX Slave:H1 Func:H2 Reg:U3-4 Value:U5-6 CRC:crc16-modbus_le"
expect = "01 06 00 05 04 D2 1B 56"
expect_fmt = "Title:Modbus_Echo Slave:H1 Func:H2 Reg:U3-4 Value:U5-6 CRC:crc16-modbus_le"

[[test]]
name = "Read back register 5 (should be 1234)"
send = "01 03 00 05 00 01 94 0B"
send_fmt = "Title:Modbus_TX Slave:H1 Func:H2 Addr:U3-4 Count:U5-6 CRC:crc16-modbus_le"
expect = "01 03 02 04 D2 3A D9"
expect_fmt = "Title:Modbus_Response Slave:H1 Func:H2 Bytes:U3 R0:U4-5 CRC:crc16-modbus_le"

[[test]]
name = "Write register 10 = 0x00FF"
send = "01 06 00 0A 00 FF E9 88"
send_fmt = "Title:Modbus_TX Slave:H1 Func:H2 Reg:U3-4 Value:U5-6 CRC:crc16-modbus_le"
expect = "01 06 00 0A 00 FF E9 88"
expect_fmt = "Title:Modbus_Echo Slave:H1 Func:H2 Reg:U3-4 Value:U5-6 CRC:crc16-modbus_le"

[[test]]
name = "Illegal function (expect exception)"
send = "01 07 00 00 00 01 75 CA"
send_fmt = "Title:Modbus_TX  Slave:H1 Func:H2 Data:H3-6 CRC:crc16-modbus_le"
expect = "01 87 01 82 30"
expect_fmt = "Title:Modbus_Exception Slave:H1 ErrFunc:H2 Code:U3 CRC:crc16-modbus_le"
