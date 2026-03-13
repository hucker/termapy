# AT command protocol test — deterministic responses only
name = "Demo AT Command Test"
frame_gap = "50ms"
quiet = false

[[test]]
name = "AT basic"
send = '"AT\r"'
send_fmt = "Title:AT_Command Command:S1-*"
expect = '"OK\r\n"'
expect_fmt = "Title:AT_Response Response:S1-*"

[[test]]
name = "LED on"
send = '"AT+LED on\r"'
send_fmt = "Title:AT_Command Command:S1-*"
expect = '"OK\r\n"'
expect_fmt = "Title:AT_Response Response:S1-*"

[[test]]
name = "LED off"
send = '"AT+LED off\r"'
send_fmt = "Title:AT_Command Command:S1-*"
expect = '"OK\r\n"'
expect_fmt = "Title:AT_Response Response:S1-*"

[[test]]
name = "Unknown command"
send = '"INVALID\r"'
send_fmt = "Title:AT_Command Command:S1-*"
expect = "\"ERROR: Unknown command 'INVALID'\\r\\n\""
expect_fmt = "Title:AT_Response Response:S1-*"
