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
                short = child_name.split(".")[-1]
                arg_str = f" {child.args}" if child.args else ""
                ctx.write(f"  {prefix}{child_name}{arg_str} - {child.help}")
    return CmdResult.ok()


def _handler_list(ctx: PluginContext, args: str) -> CmdResult:
    _apply(ctx, port_control.list_ports())
    return CmdResult.ok()


def _handler_open(ctx: PluginContext, args: str) -> CmdResult:
    ctx.engine.connect(args.strip() if args.strip() else None)
    return CmdResult.ok()


def _handler_close(ctx: PluginContext, args: str) -> CmdResult:
    ctx.engine.disconnect()
    return CmdResult.ok()


def _handler_info(ctx: PluginContext, args: str) -> CmdResult:
    _apply(ctx, port_control.port_info(ctx.cfg, ctx.port()))
    return CmdResult.ok()


def _handler_flow(ctx: PluginContext, args: str) -> CmdResult:
    _apply(ctx, port_control.get_set_flow(ctx.port(), ctx.cfg, args))
    return CmdResult.ok()


def _handler_break(ctx: PluginContext, args: str) -> CmdResult:
    _apply(ctx, port_control.send_break(ctx.port(), args))
    return CmdResult.ok()


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
            args="{name}",
            help="Connect to the serial port (optional port override).",
            handler=_handler_open,
        ),
        "close": Command(
            help="Disconnect from the serial port.",
            handler=_handler_close,
        ),
        "info": Command(
            help="Show port status, serial parameters, and hardware lines.",
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
    },
)
