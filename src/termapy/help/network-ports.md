# Network ports

A serial port does not have to be on this machine. pyserial, which opens
every port termapy uses, reaches one across a network in two forms, and
termapy can sit at either end:

- `rfc2217://host:port`: RFC 2217, Telnet with serial port control. The
  baud rate, frame, flow control, DTR, RTS and break cross the network,
  so the far port behaves like a local one. `termapy --serve` speaks it,
  as do `ser2net` and serial device servers.
- `socket://host:port`: raw TCP. Bytes only: the far port keeps whatever
  settings it was opened with.

Nothing finds a port on another machine, so it is named: by its URL here,
and by `--listen` on the machine that shares one.

## Opening a network port

In the TUI, the port button in the title bar opens Select Serial Port.
**Network Port...** swaps the list for a field: type the URL, **Test**
opens the port and closes it again to see that it answers, and **Use**
takes it as a picked port. The New Config wizard has the same field. A
port a `termapy --serve` on this machine shares is already a row, by its
URL.

Anywhere else, the URL is the port:

```text
/port.connect rfc2217://192.168.1.57:2217        at the REPL
/port.test rfc2217://192.168.1.57:2217           open and close it, in any frontend
"port": "rfc2217://192.168.1.57:2217"            in a config
```

An open sets DTR and RTS, here or across an RFC 2217 link, so a board
that resets on them resets on a test too. Under MCP a network port also
needs `TERMAPY_MCP_NET_EGRESS=1` and `TERMAPY_ALLOWED_PORTS` naming the
host exactly; see [MCP server](mcp-server.md).

### When it does not answer

| Message | What happened |
| --- | --- |
| `Nothing answers at host:port: no server listens there` | The server is not running, or the TCP port is wrong |
| `No answer from host:port in time: the host is off or unreachable` | The machine is off, asleep, or not on this network |
| `The server at host:port did not take the connection: busy with another client, or not an RFC 2217 server` | A `--serve` port takes one client at a time; or the server speaks raw TCP, so use `socket://` |
| `The connection to host:port closed: the server stopped or the network dropped it` | The far end went away during the session |
| `Invalid port: socket://host (a network port is scheme://host:port)` | The URL has no TCP port number |

### What a network port cannot do

A `socket://` port carries bytes and nothing else: the baud rate, frame,
flow control, DTR, RTS and break never reach the device, and CTS, DSR and
CD always read 1. So on an open `socket://` port the commands that set
or read them (`/port.baud_rate`, `byte_size`, `parity`, `stop_bits`,
`flow_control`, `mode`, `dtr`, `rts`, `cts`, `dsr`, `ri`, `cd`, `break`)
refuse with the reason instead of reporting a change that never
happened, and a button showing one of them reads `-`. `rfc2217://`
carries all of them.

No network keeps time: a delay stretches or vanishes on the way. So on
either kind of network port, `/proto.send` refuses an inline `~Nms`
delay; without one it sends as usual.

Connecting runs none of these commands, so a config's own settings never
stop a network port from opening. Only a line that sets one, typed,
clicked or in an on-connect command, answers with the refusal.

## Sharing this machine's ports

**Status: prototype.** `--serve` may change or be removed in any release.

`termapy --serve` puts serial ports on the network for another computer to
open, over RFC 2217. It runs no TUI and none of the command system, so it
starts in a fraction of a second. Name each port to share, each on its own
TCP port, and the address clients connect to:

```text
termapy --serve COM3 --listen 192.168.1.57             rfc2217://192.168.1.57:2217
termapy --serve COM3 COM5 --listen 192.168.1.57        two ports
termapy --serve BG03U7VTA=2300 --listen 192.168.1.57   by USB serial number, on TCP port 2300
termapy --serve DEMO --listen localhost                the simulator, this machine only
```

termapy never picks the address: a machine has several (Wi-Fi, Ethernet, a
VPN, a WSL or Hyper-V adapter), and the wrong one opens the port to the
wrong network. Leave `--listen` out and it lists them, the default route's
marked; leave the ports out and it lists those:

```text
$ termapy --serve COM3
termapy: error: --serve needs --listen, the address clients connect to:
  --listen localhost     this machine only
  --listen 192.168.1.57  the default route's: your LAN, most likely
  --listen 172.18.208.1
e.g. termapy --serve COM3 --listen 192.168.1.57
There is no login: anyone who can reach the address can drive the port.
```

```text
termapy --serve (prototype): serial ports on the network over RFC 2217
  COM3  ->  rfc2217://192.168.1.57:2217
  COM5  ->  rfc2217://192.168.1.57:2218
  Open to the network at 192.168.1.57. There is no login:
  anyone who can reach this address can drive these ports.
  Stop with Ctrl+C, or from any termapy: /app.kill serve (termapy --kill serve).
```

On the other computer the URL is the port, as in
[Opening a network port](#opening-a-network-port), and the baud rate,
frame, DTR, RTS and break set there reach the real port.

- **The ports you name, and no others.** Unnamed ports stay free on this
  machine. A port's TCP number is fixed by its place in the list or by
  `=TCPPORT`, so a client's URL never changes.
- **Opened as a session opens one.** `TERMAPY_ALLOWED_PORTS` applies, a
  port open elsewhere is refused, and a port that will not open stops the
  run before anything listens.
- **One client per port at a time.** Another is refused at once, and the
  server prints who holds the port. A client that vanishes without closing,
  a laptop gone to sleep or off the network, is dropped within about a
  minute, so it never holds the port for good.
- **Unplugged or reset, it comes back.** The server notices within about
  a second, whether or not a client is connected: a client is dropped, and
  the port is tried again about once a second while its URL keeps listening.
  Name the port by its USB serial number, so a board that comes back as a
  new COM number is still found.
- **No login.** `--listen` with this machine's address opens the ports to
  anyone who can reach it. `--listen localhost` keeps them to this machine.
  A host name listens on every address it names: `localhost` on both `::1`
  and `127.0.0.1`, so a client tries no address that is not listening.
- **A session kind of its own.** `--alive` lists it as `serve` with every
  port it holds, where each answers and who is connected to it, a TUI that
  tries one of them is told the server holds it, and `/app.kill serve` or
  `termapy --kill serve` stops every port server.

### In the background: `--detach`

To run it in the background, add `--detach`. termapy starts the server as
a process of its own, with no window, waits until it listens, and
returns:

```text
$ termapy --serve COM7 --listen 192.168.1.57 --detach
termapy --serve is running in the background, PID 11120:
  COM7  ->  rfc2217://192.168.1.57:2217
  Open to the network at 192.168.1.57. There is no login.
  Its output: C:\Users\you\AppData\Local\termapy\log\20261124_164358-serve.log
  Stop it with: termapy --kill serve
```

Closing the window it was started from does not stop it. Its banner and
every event go to the output file, which the diagnostic log's own pruning
removes in time. A server that cannot start, a port that will not open or
a TCP port in use, prints its reason there and here, and `--detach` exits
with its code. To start one at every logon, register the same command as a
Task Scheduler task on Windows or a systemd unit on Linux or a Raspberry Pi.

## Secure setups

### Through an SSH tunnel

`--serve` has no login and no encryption. SSH supplies both: serve on
`localhost` on the machine with the device, and carry the port to yours
through a tunnel.

```text
on the machine with the device:   termapy --serve COM4 --listen localhost
on yours:                         ssh -L 2217:localhost:2217 user@device-machine
then open:                        rfc2217://localhost:2217
```

Only someone who can log in over SSH reaches the port, and nothing
listens on the network but SSH.

### WSL, Docker and virtual machines

A Linux program in WSL cannot see Windows' COM ports. `--serve` shares one
without taking it from Windows: WSL reaches Windows at the address of
Windows' WSL adapter (`ipconfig` names it `vEthernet (WSL)`; it is
`172.18.208.1` in the list above), so serve on that address and open
`rfc2217://172.18.208.1:2217` inside WSL. With WSL's mirrored networking
mode, `--listen localhost` reaches it too. A container or a virtual
machine reaches the host the same way, at its virtual adapter's address.

## Other programs

Anything built on pyserial opens the same URLs: pyserial's own terminal
(`python -m serial.tools.miniterm rfc2217://host:2217`) and `esptool`
(`--port rfc2217://host:2217`), so a board on another machine can be
flashed across the network. Most other terminals, PuTTY and Tera Term
among them, do not speak RFC 2217.
