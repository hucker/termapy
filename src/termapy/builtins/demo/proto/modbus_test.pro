# Modbus RTU protocol test
name = "Demo Modbus RTU Test"
frame_gap = "20ms"
quiet = false

[[test]]
name = "Read 2 holding registers from addr 0"
send = "01 03 00 00 00 02 C4 0B"
expect = "01 03 04 00 07 00 14 4B FD"

[[test]]
name = "Write register 10 = 0x00FF"
send = "01 06 00 0A 00 FF E9 88"
expect = "01 06 00 0A 00 FF E9 88"
