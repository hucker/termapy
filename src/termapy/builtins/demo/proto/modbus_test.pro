# Modbus RTU protocol test — exercises the demo device's Modbus handler
# Visualizer: Modbus (modbus_view.py) uses CRC:crc16-modbus_le
name = "Demo Modbus RTU Test"
frame_gap = "20ms"
quiet = false
viz = ["Modbus"]

[[test]]
name = "Read 1 register from addr 0"
viz = "Modbus"
send = "01 03 00 00 00 01 84 0A"
expect = "01 03 02 00 07 F9 86"

[[test]]
name = "Read 2 registers from addr 0"
viz = "Modbus"
send = "01 03 00 00 00 02 C4 0B"
expect = "01 03 04 00 07 00 14 4B FD"

[[test]]
name = "Read 5 registers from addr 100"
viz = "Modbus"
send = "01 03 00 64 00 05 C4 16"
expect = "01 03 0A 05 1B 05 28 05 35 05 42 05 4F 8C 46"

[[test]]
name = "Write register 5 = 1234"
viz = "Modbus"
send = "01 06 00 05 04 D2 1B 56"
expect = "01 06 00 05 04 D2 1B 56"

[[test]]
name = "Read back register 5 (should be 1234)"
viz = "Modbus"
send = "01 03 00 05 00 01 94 0B"
expect = "01 03 02 04 D2 3A D9"

[[test]]
name = "Write register 10 = 0x00FF"
viz = "Modbus"
send = "01 06 00 0A 00 FF E9 88"
expect = "01 06 00 0A 00 FF E9 88"

[[test]]
name = "Illegal function (expect exception)"
viz = "Modbus"
send = "01 07 00 00 00 01 75 CA"
expect = "01 87 01 82 30"
