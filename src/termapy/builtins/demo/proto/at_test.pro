# AT command protocol test — deterministic responses only
name = "Demo AT Command Test"
frame_gap = "50ms"
quiet = false

[[test]]
name = "AT basic"
send = '"AT\r"'
expect = '"OK\r\n"'

[[test]]
name = "LED on"
send = '"AT+LED on\r"'
expect = '"OK\r\n"'

[[test]]
name = "LED off"
send = '"AT+LED off\r"'
expect = '"OK\r\n"'

[[test]]
name = "Unknown command"
send = '"INVALID\r"'
expect = "\"ERROR: Unknown command 'INVALID'\\r\\n\""
