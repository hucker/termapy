"""Built-in plugin: include device command help from JSON.

Named 'include' (not 'import') because 'import' is a Python reserved keyword.
C programmers will recognize the analogy to #include.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.plugins import CmdResult, Command, TargetCommand
from termapy.scripting import parse_duration, parse_keywords

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

_CACHE_NAME = ".target_menu.json"


def _cache_path(ctx: PluginContext) -> Path:
    """Return the path to the cached target menu JSON file."""
    return Path(ctx.config_path).parent / _CACHE_NAME


def _build_commands(cmd_dict: dict) -> dict[str, TargetCommand]:
    """Build TargetCommand dict from a commands dict."""
    commands: dict[str, TargetCommand] = {}
    for name, entry in cmd_dict.items():
        if isinstance(entry, dict) and "help" in entry:
            commands[name] = TargetCommand(
                name=name,
                help=entry["help"],
                args=entry.get("args", ""),
            )
    return commands


def _to_json_dict(target: dict[str, TargetCommand]) -> dict:
    """Convert target commands back to the JSON format."""
    return {
        "commands": {
            name: {"help": tc.help, "args": tc.args}
            for name, tc in sorted(target.items())
        }
    }


def _save_cache(ctx: PluginContext, target: dict[str, TargetCommand]) -> None:
    """Write target commands to the cache file."""
    try:
        _cache_path(ctx).write_text(
            json.dumps(_to_json_dict(target), indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def _load_cache(ctx: PluginContext) -> dict[str, TargetCommand] | None:
    """Load target commands from cache file. Returns None if missing/corrupt."""
    path = _cache_path(ctx)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cmd_dict = data.get("commands", data) if "commands" in data else data
        if not isinstance(cmd_dict, dict):
            path.unlink(missing_ok=True)
            return None
        commands = _build_commands(cmd_dict)
        if not commands:
            path.unlink(missing_ok=True)
            return None
        return commands
    except (OSError, json.JSONDecodeError, ValueError):
        path.unlink(missing_ok=True)
        return None


def _read_json(ctx: PluginContext, timeout_ms: int) -> dict | None:
    """Read serial data and extract the first valid JSON object.

    Accumulates bytes, scans for '{', and tries json.loads() from there.
    Returns the parsed dict on success, None on timeout.
    """
    buf = b""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
        chunk = ctx.serial_read_raw(timeout_ms=remaining_ms)
        if not chunk:
            break
        buf += chunk
        text = buf.decode(ctx.cfg.get("encoding", "utf-8"), errors="replace")
        start = text.find("{")
        if start < 0:
            continue
        try:
            return json.loads(text[start:])
        except json.JSONDecodeError:
            continue
    return None


def _fetch_and_include(ctx: PluginContext, cmd: str, timeout_ms: int) -> CmdResult:
    """Send command, read JSON, build TargetCommands, save cache."""
    if not ctx.is_connected():
        return CmdResult.fail(msg="Not connected.")

    with ctx.serial_io():
        ctx.serial_drain()
        ctx.serial_send(cmd)
        data = _read_json(ctx, timeout_ms)

    if data is None:
        return CmdResult.fail(msg="Include: no valid JSON received (timeout).")
    if not isinstance(data, dict):
        return CmdResult.fail(msg="Include: expected a JSON object (dict).")

    # Accept {"commands": {...}} wrapper or flat dict
    cmd_dict = data.get("commands", data) if "commands" in data else data
    if not isinstance(cmd_dict, dict):
        return CmdResult.fail(msg="Include: 'commands' must be a JSON object.")

    commands = _build_commands(cmd_dict)
    skipped = len(cmd_dict) - len(commands)
    if skipped:
        ctx.status(f"  Skipped {skipped} entries missing 'help' field")

    if not commands:
        return CmdResult.fail(msg="Include: JSON contained no valid commands.")

    ctx.engine.set_target_commands(commands)
    _save_cache(ctx, commands)
    ctx.result(f"Included {len(commands)} device commands.")
    return CmdResult.ok(value=str(len(commands)))


def _parse_include_args(ctx: PluginContext, args: str):
    """Parse /include args, returning (cmd, timeout_ms) or CmdResult on error."""
    kw = parse_keywords(args, {"timeout", "cmd"}, rest_keyword="cmd")
    cmd = kw.get("cmd", "") or ctx.cfg.get("device_json_cmd", "")
    if not cmd:
        return CmdResult.fail(
            msg="Usage: /include {timeout=<dur>} cmd=<command>\n"
            "  Or set device_json_cmd in your config."
        )
    try:
        timeout_ms = int(parse_duration(kw.get("timeout", "1s")) * 1000)
    except ValueError as e:
        return CmdResult.fail(msg=f"Include: {e}")
    return cmd, timeout_ms


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Include device command help from JSON (cached).

    Check order: memory cache -> disk cache -> serial command.
    Use /include.reload to force a refresh from the device.

    Args:
        ctx: Plugin context for serial I/O and output.
        args: Keyword args: timeout=<dur>, cmd=<command>.
    """
    # 1. Memory cache
    existing = ctx.engine.target_commands
    if existing:
        ctx.result(f"{len(existing)} device commands (cached).")
        return CmdResult.ok(value=str(len(existing)))

    # 2. Disk cache
    from_disk = _load_cache(ctx)
    if from_disk:
        ctx.engine.set_target_commands(from_disk)
        ctx.result(f"Included {len(from_disk)} device commands (from cache).")
        return CmdResult.ok(value=str(len(from_disk)))

    # 3. Serial command
    parsed = _parse_include_args(ctx, args)
    if isinstance(parsed, CmdResult):
        return parsed
    cmd, timeout_ms = parsed
    return _fetch_and_include(ctx, cmd, timeout_ms)


def _handler_reload(ctx: PluginContext, args: str) -> CmdResult:
    """Force re-include from device, ignoring all caches."""
    parsed = _parse_include_args(ctx, args)
    if isinstance(parsed, CmdResult):
        return parsed
    cmd, timeout_ms = parsed
    return _fetch_and_include(ctx, cmd, timeout_ms)


def _handler_dump(ctx: PluginContext, args: str) -> CmdResult:
    """Pretty-print the included target commands as JSON."""
    target = ctx.engine.target_commands
    if not target:
        ctx.result("No target commands included.")
        return CmdResult.ok()
    for line in json.dumps(_to_json_dict(target), indent=2).splitlines():
        ctx.output(f"  {line}")
    return CmdResult.ok()


def _handler_clear(ctx: PluginContext, args: str) -> CmdResult:
    """Remove all included target commands and delete cache file."""
    ctx.engine.clear_target_commands()
    try:
        _cache_path(ctx).unlink(missing_ok=True)
    except OSError:
        pass
    ctx.result("Target commands cleared.")
    return CmdResult.ok()


def _handler_list(ctx: PluginContext, args: str) -> CmdResult:
    """List currently included target commands."""
    target = ctx.engine.target_commands
    if not target:
        ctx.result("No target commands included.")
        return CmdResult.ok()
    for name in sorted(target):
        tc = target[name]
        arg_str = f" {tc.args}" if tc.args else ""
        ctx.output(f"  {name}{arg_str} -- {tc.help}")
    ctx.result(f"{len(target)} target commands.")
    return CmdResult.ok(value=str(len(target)))


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    "Include device command help from JSON response.",
    name="include",
    args="{timeout=<dur>} {cmd=<command>}",
    handler=_handler,
    long_help="""\
Sends a command to the device and parses the JSON response to include
command help. Included commands appear in suggestions and /help but
are not REPL commands -- type them directly as device commands.

Check order: memory -> .target_menu.json -> serial command.
Use /include.reload to force a refresh from the device.
Use /include.clear to remove commands and delete the cache.

  /include cmd=AT+HELP.JSON
  /include timeout=2s cmd=HELP_JSON
  /include                       (uses device_json_cmd from config)

JSON format: {"commands": {"cmd": {"help": "...", "args": "..."}, ...}}""",
    sub_commands={
        "reload": Command(
            "Re-include from device, ignoring all caches.",
            handler=_handler_reload,
            args="{timeout=<dur>} {cmd=<command>}",
        ),
        "dump": Command(
            "Dump included commands as JSON.",
            handler=_handler_dump,
        ),
        "clear": Command(
            "Remove all included target commands and cache.",
            handler=_handler_clear,
        ),
        "list": Command(
            "List currently included target commands.",
            handler=_handler_list,
        ),
    },
)
