"""Built-in plugin: serial port control - list, connect, configure, signals."""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy import port_control
from termapy.plugins import CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _apply(ctx: PluginContext, result: port_control.Result) -> None:
    """Output messages and apply side effects from a port_control function."""
    msgs, effects = result
    for text, color in msgs:
        if color:
            ctx.write(text, color)
        else:
            ctx.write(text)
    if effects:
        ctx.engine.apply_port_effects(effects)


# ── Handlers ────────────────────────────────────────────────────────────────


def _handler_root(ctx: PluginContext, args: str) -> CmdResult:
    name = args.strip()
    if name:
        ctx.engine.update_port(name)
        return CmdResult.ok()
    # No args - list subcommands
    prefix = ctx.engine.prefix
    plugins = ctx.engine.plugins
    info = plugins.get("port")
    if info and info.children:
        ctx.write(f"Subcommands of {prefix}port:")
        for child_name in sorted(info.children):
            child = plugins.get(child_name)
            if child:
                child_name.split(".")[-1]
                arg_str = f" {child.args}" if child.args else ""
                ctx.write(f"  {prefix}{child_name}{arg_str} - {child.help}")
    return CmdResult.ok()


def _handler_list(ctx: PluginContext, args: str) -> CmdResult:
    _apply(ctx, port_control.list_ports())
    return CmdResult.ok()


def _handler_open(ctx: PluginContext, args: str) -> CmdResult:
    port, baud, mode, err = port_control.parse_open_args(args)
    if err:
        ctx.write(err, "red")
        return CmdResult.fail(msg=err)
    # Apply baud/mode to config before connecting so the port opens
    # with the right settings.
    if baud is not None:
        ctx.engine.apply_port_effects({"cfg_update": {"baud_rate": baud}})
    if mode is not None:
        parity, byte_size, stop_bits = mode
        ctx.engine.apply_port_effects({"cfg_update": {
            "parity": parity,
            "byte_size": byte_size,
            "stop_bits": stop_bits,
        }})
    ctx.engine.connect(port)
    return CmdResult.ok()


def _handler_close(ctx: PluginContext, args: str) -> CmdResult:
    ctx.engine.disconnect()
    return CmdResult.ok()


def _handler_info(ctx: PluginContext, args: str) -> CmdResult:
    """Show port info for the currently-connected port.

    Dumps configured serial parameters, USB chip identification, and
    live hardware signal lines (DTR/RTS/CTS/DSR/RI/CD).  This is the
    richest port report termapy can produce, but it only works on the
    port termapy is actually connected to because the live signals
    come from an open Serial object.

    For chip info on any other port (or all ports at once), use
    /port.chip which has a compatible argument shape.
    """
    arg = args.strip()
    if arg:
        return CmdResult.fail(
            msg=(
                f"/port.info only reports on the connected port. "
                f"For chip info on {arg!r}, use /port.chip {arg}"
            )
        )
    _apply(ctx, port_control.port_info(ctx.cfg, ctx.port()))
    return CmdResult.ok()


def _handler_mode(ctx: PluginContext, args: str) -> CmdResult:
    _apply(ctx, port_control.set_mode(ctx.port(), ctx.cfg, args))
    return CmdResult.ok()


def _handler_flow(ctx: PluginContext, args: str) -> CmdResult:
    _apply(ctx, port_control.get_set_flow(ctx.port(), ctx.cfg, args))
    return CmdResult.ok()


def _handler_break(ctx: PluginContext, args: str) -> CmdResult:
    _apply(ctx, port_control.send_break(ctx.port(), args))
    return CmdResult.ok()


def _handler_chip(ctx: PluginContext, args: str) -> CmdResult:
    current = ctx.cfg.get("port", "") or ""
    connected = current if ctx.is_connected() else ""
    _apply(ctx, port_control.chip_info(args, current, connected))
    return CmdResult.ok()


def _make_chip_field_handler(field: str):
    """Build a handler for /port.chip.<field>.

    The handler takes an optional port name (or ``*`` for all ports).
    With no argument it queries the current port from cfg["port"].
    """
    def _handler(ctx: PluginContext, args: str) -> CmdResult:
        current = ctx.cfg.get("port", "") or ""
        connected = current if ctx.is_connected() else ""
        result = port_control.chip_field(field, args, current, connected)
        _apply(ctx, result)
        # Single-port single-field call: return value via CmdResult.value
        # so .quiet mode is useful for scripting.
        msgs, _ = result
        if len(msgs) == 1:
            return CmdResult.ok(value=msgs[0][0])
        return CmdResult.ok()

    return _handler


def _make_prop_handler(key: str):
    def _handler(ctx: PluginContext, args: str) -> CmdResult:
        _apply(ctx, port_control.get_set_prop(ctx.port(), ctx.cfg, key, args))
        return CmdResult.ok()

    return _handler


def _make_hw_handler(line: str):
    def _handler(ctx: PluginContext, args: str) -> CmdResult:
        _apply(ctx, port_control.get_set_hw_line(ctx.port(), line, args))
        return CmdResult.ok()

    return _handler


def _make_signal_handler(signal: str):
    def _handler(ctx: PluginContext, args: str) -> CmdResult:
        _apply(ctx, port_control.read_signal(ctx.port(), signal, args))
        return CmdResult.ok()

    return _handler


# ── COMMAND (must be at end of file) ──────────────────────────────────────────

COMMAND = Command(
    name="port",
    args="{name}",
    help="Serial port tools: open, close, list, configure.",
    handler=_handler_root,
    sub_commands={
        "list": Command(
            help="List available serial ports.",
            handler=_handler_list,
        ),
        "open": Command(
            args="{name} {baud} {mode}",
            help="Connect to the serial port (e.g. /port.open COM3 9600 N81).",
            handler=_handler_open,
        ),
        "mode": Command(
            args="{baud} {mode}",
            help="Show or set serial mode (e.g. /port.mode 9600 N81).",
            handler=_handler_mode,
        ),
        "close": Command(
            help="Disconnect from the serial port.",
            handler=_handler_close,
        ),
        "info": Command(
            help="Show status, params, chip, and live signals for the connected port.",
            handler=_handler_info,
        ),
        "baud_rate": Command(
            args="{value}",
            help="Show or set baud rate.",
            handler=_make_prop_handler("baud_rate"),
        ),
        "byte_size": Command(
            args="{value}",
            help="Show or set data bits.",
            handler=_make_prop_handler("byte_size"),
        ),
        "parity": Command(
            args="{value}",
            help="Show or set parity.",
            handler=_make_prop_handler("parity"),
        ),
        "stop_bits": Command(
            args="{value}",
            help="Show or set stop bits.",
            handler=_make_prop_handler("stop_bits"),
        ),
        "flow_control": Command(
            args="{mode}",
            help="Show or set flow control (none/rtscts/xonxoff/manual).",
            handler=_handler_flow,
        ),
        "dtr": Command(
            args="{0|1}",
            help="Show or set DTR line (hardware only).",
            handler=_make_hw_handler("dtr"),
        ),
        "rts": Command(
            args="{0|1}",
            help="Show or set RTS line (hardware only).",
            handler=_make_hw_handler("rts"),
        ),
        "cts": Command(
            help="Show CTS state (read-only).",
            handler=_make_signal_handler("cts"),
        ),
        "dsr": Command(
            help="Show DSR state (read-only).",
            handler=_make_signal_handler("dsr"),
        ),
        "ri": Command(
            help="Show Ring Indicator state (read-only).",
            handler=_make_signal_handler("ri"),
        ),
        "cd": Command(
            help="Show Carrier Detect state (read-only).",
            handler=_make_signal_handler("cd"),
        ),
        "break": Command(
            args="{duration_ms}",
            help="Send a break signal (default 250ms).",
            handler=_handler_break,
        ),
        "chip": Command(
            args="{name|*}",
            help="Identify USB-serial chip(s) and report USB speed class.",
            handler=_handler_chip,
            sub_commands={
                "device": Command(
                    args="{name|*}",
                    help="Show port device name.",
                    handler=_make_chip_field_handler("device"),
                ),
                "description": Command(
                    args="{name|*}",
                    help="Show OS-reported port description.",
                    handler=_make_chip_field_handler("description"),
                ),
                "manufacturer": Command(
                    args="{name|*}",
                    help="Show USB manufacturer string.",
                    handler=_make_chip_field_handler("manufacturer"),
                ),
                "product": Command(
                    args="{name|*}",
                    help="Show USB product string.",
                    handler=_make_chip_field_handler("product"),
                ),
                "serial": Command(
                    args="{name|*}",
                    help="Show USB serial number of the cable.",
                    handler=_make_chip_field_handler("serial"),
                ),
                "location": Command(
                    args="{name|*}",
                    help="Show USB topology location.",
                    handler=_make_chip_field_handler("location"),
                ),
                "interface": Command(
                    args="{name|*}",
                    help="Show interface label (multi-channel chips).",
                    handler=_make_chip_field_handler("interface"),
                ),
                "vid_pid": Command(
                    args="{name|*}",
                    help="Show USB VID:PID hex string.",
                    handler=_make_chip_field_handler("vid_pid"),
                ),
                "model": Command(
                    args="{name|*}",
                    help="Show identified chip model from the lookup table.",
                    handler=_make_chip_field_handler("model"),
                ),
                "usb_speed": Command(
                    args="{name|*}",
                    help="Show theoretical USB speed class for the chip.",
                    handler=_make_chip_field_handler("usb_speed"),
                ),
                "negotiated": Command(
                    args="{name|*}",
                    help="Show OS-reported negotiated USB link speed (Linux only).",
                    handler=_make_chip_field_handler("negotiated"),
                ),
                "driver": Command(
                    args="{name|*}",
                    help="Show kernel driver name (Linux only).",
                    handler=_make_chip_field_handler("driver"),
                ),
                "latency_timer": Command(
                    args="{name|*}",
                    help="Show FTDI latency timer value (Linux + FTDI only).",
                    handler=_make_chip_field_handler("latency_timer"),
                ),
                "max_baud": Command(
                    args="{name|*}",
                    help="Show maximum baud rate the chip supports.",
                    handler=_make_chip_field_handler("max_baud"),
                ),
                "permissions": Command(
                    args="{name|*}",
                    help="Show read/write permission status for the device.",
                    handler=_make_chip_field_handler("permissions"),
                ),
                "in_use": Command(
                    args="{name|*}",
                    help="Show whether another process has the port open.",
                    handler=_make_chip_field_handler("in_use"),
                ),
            },
        ),
    },
)
