"""Built-in plugin: include device command help from JSON.

Named 'include' (not 'import') because 'import' is a Python reserved keyword.
C programmers will recognize the analogy to #include.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

from packaging.version import InvalidVersion, Version

from termapy.help_dynamic import compose, green, ns_count
from termapy.plugins import CapabilitySet, CmdResult, Command, TargetCommand
from termapy.scripting import parse_duration, parse_keywords

if TYPE_CHECKING:
    from termapy.plugins import PluginContext, TargetCommand  # noqa: F401

_CACHE_NAME = ".target_menu.json"


def _cache_path(ctx: PluginContext) -> Path:
    """Return the path to the cached target menu JSON file."""
    return Path(ctx.config_path).parent / _CACHE_NAME


def _is_newer(new: str | None, cached: str | None) -> bool:
    """Return True if ``new`` is strictly newer than ``cached``.

    Versions are compared with PEP 440 semantics (``packaging.Version``)
    so ``"1.10"`` beats ``"1.9"`` and ``"2024.11.5"`` works.  If either
    side isn't parseable (e.g. a git hash like ``a3f2c91``), we fall
    back to plain string inequality: differ = newer, equal = not newer.

    A ``None`` new version is never newer than anything.  A ``None``
    cached version means the cache has no version recorded; any
    explicit new version is treated as newer so the first time a
    device starts publishing a version, the richer data wins.
    """
    if new is None:
        return False
    if cached is None:
        return True
    try:
        return Version(new) > Version(cached)
    except InvalidVersion:
        return new != cached


def _extract_version(data: dict) -> str | None:
    """Pull the top-level ``version`` key off an include JSON blob.

    Returns ``None`` when absent, when the value isn't a string, or
    when the string is empty -- every "unknown" shape collapses to
    the same "no version" answer so callers don't have to branch.
    """
    v = data.get("version")
    if isinstance(v, str) and v:
        return v
    return None


# ``target_meta`` holds metadata about the currently-loaded target
# command set.  Today that's just the schema version string; keeping it
# in its own namespace means the ``target_commands`` dict stays a pure
# mapping of ``{name: TargetCommand}`` which is what every other
# consumer (help.py, search.py, app.py, cli.py) iterates.
_META_NS = "target_meta"


def _set_version(ctx: PluginContext, version: str | None) -> None:
    """Record the version of the currently-loaded target command set."""
    meta = ctx.ns(_META_NS)
    if version:
        meta["version"] = version
    else:
        meta.pop("version", None)


def _get_version(ctx: PluginContext) -> str | None:
    """Return the version of the currently-loaded target command set."""
    return ctx.ns(_META_NS).get("version")


_VALID_SAFETY = ("safe", "readonly", "mutable", "destructive")


def _sanitize_typed_args(raw: object) -> list[dict]:
    """Coerce a raw ``typed_args`` value to the canonical list-of-dicts.

    Accepts only a list of dicts; everything else collapses to ``[]``.
    Per-item we keep recognized keys and drop the rest -- forward-compat
    is "ignore unknown keys," same as the top-level entry shape.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    keep = {"name", "type", "required", "default", "help", "min", "max", "enum"}
    for item in raw:
        if not isinstance(item, dict):
            continue
        cleaned = {k: v for k, v in item.items() if k in keep}
        if cleaned:
            out.append(cleaned)
    return out


def _sanitize_response(raw: object) -> dict:
    """Coerce a raw ``response`` value to the canonical dict shape."""
    if not isinstance(raw, dict):
        return {}
    keep = {
        "format", "pattern", "types", "terminator",
        "line_pattern", "line_types", "timeout_ms",
    }
    return {k: v for k, v in raw.items() if k in keep}


def _build_commands(cmd_dict: dict) -> dict[str, TargetCommand]:
    """Build TargetCommand dict from a commands dict.

    The JSON entry shape is::

        {
            "help":          "<one-line summary>",       # required
            "args":          "<arg spec>",                # optional v1
            "long_help":     "<multi-line prose>",        # optional v1
            "flags":         {"--name": "description"},   # optional v1
            "typed_args":    [{...}, ...],                # optional v2
            "send_template": "AT+VOLT={mv}",              # optional v2
            "response":      {"format": "...", ...},      # optional v2
            "safety":        "safe|readonly|destructive", # optional v2
            "rate_limit_hz": 5.0,                         # optional v2
            "timeout_ms":    1000,                        # optional v2
            "subcommands":   {...}                        # optional v2 (recursive)
        }

    Only ``help`` is required.  Unknown keys are ignored so a device can
    emit future fields without breaking older termapy.  Malformed
    values are silently dropped rather than failing the whole include.
    v1 manifests with only ``help`` + ``args`` produce TargetCommands
    whose v2 fields are at their defaults; ``_to_json_dict`` omits
    those defaults so v1 round-trips remain byte-stable.
    """
    commands: dict[str, TargetCommand] = {}
    for name, entry in cmd_dict.items():
        if not (isinstance(entry, dict) and "help" in entry):
            continue
        raw_long = entry.get("long_help", "")
        long_help = raw_long if isinstance(raw_long, str) else ""
        raw_flags = entry.get("flags", {})
        flags: dict[str, str] = {}
        if isinstance(raw_flags, dict):
            flags = {
                str(k): str(v)
                for k, v in raw_flags.items()
                if isinstance(k, str) and isinstance(v, str)
            }
        # v2 fields: each defaults to a v1-equivalent value.
        typed_args = _sanitize_typed_args(entry.get("typed_args"))
        send_template = entry.get("send_template", "")
        if not isinstance(send_template, str):
            send_template = ""
        response = _sanitize_response(entry.get("response"))
        safety = entry.get("safety", "safe")
        if safety not in _VALID_SAFETY:
            safety = "safe"
        # ``enabled`` defaults True so existing manifests stay exposed.
        # Profiles authored from legacy help dumps explicitly set False.
        raw_enabled = entry.get("enabled", True)
        enabled = bool(raw_enabled) if isinstance(raw_enabled, bool) else True
        try:
            rate_limit_hz = float(entry.get("rate_limit_hz", 0.0))
        except (TypeError, ValueError):
            rate_limit_hz = 0.0
        try:
            timeout_ms = int(entry.get("timeout_ms", 0))
        except (TypeError, ValueError):
            timeout_ms = 0
        sub_raw = entry.get("subcommands")
        subcommands = (
            _build_commands(sub_raw) if isinstance(sub_raw, dict) else {}
        )
        commands[name] = TargetCommand(
            name=name,
            help=entry["help"],
            args=entry.get("args", ""),
            long_help=long_help,
            flags=flags,
            typed_args=typed_args,
            send_template=send_template,
            response=response,
            safety=safety,
            enabled=enabled,
            rate_limit_hz=rate_limit_hz,
            timeout_ms=timeout_ms,
            subcommands=subcommands,
        )
    return commands


def _to_json_dict(
    target: dict[str, TargetCommand], version: str | None = None,
) -> dict:
    """Convert target commands back to the JSON format.

    Empty ``long_help`` / ``flags`` are omitted so a round-trip of an
    "old-shape" JSON (help + args only) stays byte-identical.  A
    ``None`` or empty ``version`` is likewise omitted -- devices that
    never ship a version keep round-tripping unchanged.

    v2 fields are also omitted when at their defaults, so a v1
    manifest still round-trips byte-stably.  Order: v1 fields first
    (help, args, long_help, flags), then v2 fields, so existing v1
    consumers reading the JSON see no shape change.
    """
    out: dict[str, dict] = {}
    for name, tc in sorted(target.items()):
        entry: dict = {"help": tc.help, "args": tc.args}
        if tc.long_help:
            entry["long_help"] = tc.long_help
        if tc.flags:
            entry["flags"] = dict(tc.flags)
        # v2 fields -- omit defaults to preserve v1 byte-stability.
        if tc.typed_args:
            entry["typed_args"] = [dict(a) for a in tc.typed_args]
        if tc.send_template:
            entry["send_template"] = tc.send_template
        if tc.response:
            entry["response"] = dict(tc.response)
        if tc.safety and tc.safety != "safe":
            entry["safety"] = tc.safety
        # ``enabled`` round-trips only when False (the non-default).
        # Default True omitted so existing v1/v2 manifests stay
        # byte-identical through a load/save cycle.
        if not tc.enabled:
            entry["enabled"] = False
        if tc.rate_limit_hz:
            entry["rate_limit_hz"] = tc.rate_limit_hz
        if tc.timeout_ms:
            entry["timeout_ms"] = tc.timeout_ms
        if tc.subcommands:
            entry["subcommands"] = _to_json_dict(tc.subcommands)["commands"]
        out[name] = entry
    payload: dict = {}
    if version:
        payload["version"] = version
    payload["commands"] = out
    return payload


def _save_cache(
    ctx: PluginContext,
    target: dict[str, TargetCommand],
    version: str | None = None,
) -> None:
    """Write target commands (and optional schema version) to the cache file."""
    try:
        _cache_path(ctx).write_text(
            json.dumps(_to_json_dict(target, version), indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def _load_cache(
    ctx: PluginContext,
) -> tuple[dict[str, TargetCommand], str | None] | None:
    """Load target commands + cached schema version from disk.

    Returns ``(commands, version)`` on success or ``None`` if the cache
    is missing, corrupt, or empty.  The version is the cached JSON's
    top-level ``version`` field, or ``None`` if absent.
    """
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
        version = _extract_version(data) if isinstance(data, dict) else None
        return commands, version
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


def _fetch_and_include(
    ctx: PluginContext, cmd: str, timeout_ms: int, *, force: bool = False,
) -> CmdResult:
    """Send command, read JSON, build TargetCommands, cache on version win.

    Version gating:
      - ``force=True`` (``/include.reload``): always overwrite.
      - ``force=False`` (auto path): load the cache first, parse both
        versions, and keep the cache when the fetched version isn't
        strictly newer.  See ``_is_newer``.

    The compare uses the cache's file copy as the source of truth for
    "what version do we currently have" -- the in-memory ns can be
    empty on a fresh process even when a cache exists on disk.
    """
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

    new_version = _extract_version(data)

    # Version gate -- only on the auto path, and only when a cache exists.
    # No cache => always use the fetch (covers first-time include).
    if not force:
        cached = _load_cache(ctx)
        if cached is not None:
            cached_commands, cached_version = cached
            if not _is_newer(new_version, cached_version):
                target = ctx.ns("target_commands")
                target.clear()
                target.update(cached_commands)
                _set_version(ctx, cached_version)
                ctx.result(
                    f"Included {len(cached_commands)} device commands "
                    f"(cache kept, version {cached_version or '?'} "
                    f">= fetched {new_version or '?'})."
                )
                return CmdResult.ok(value=str(len(cached_commands)))

    target = ctx.ns("target_commands")
    target.clear()
    target.update(commands)
    _set_version(ctx, new_version)
    _save_cache(ctx, commands, new_version)
    tag = f" (v{new_version})" if new_version else ""
    ctx.result(f"Included {len(commands)} device commands{tag}.")
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
    existing = ctx.ns("target_commands")
    if existing:
        ctx.result(f"{len(existing)} device commands (cached).")
        return CmdResult.ok(value=str(len(existing)))

    # 2. Disk cache
    from_disk = _load_cache(ctx)
    if from_disk is not None:
        commands, version = from_disk
        existing.update(commands)
        _set_version(ctx, version)
        tag = f" (v{version})" if version else ""
        ctx.result(
            f"Included {len(commands)} device commands (from cache{tag})."
        )
        return CmdResult.ok(value=str(len(commands)))

    # 3. Serial command
    parsed = _parse_include_args(ctx, args)
    if isinstance(parsed, CmdResult):
        return parsed
    cmd, timeout_ms = parsed
    return _fetch_and_include(ctx, cmd, timeout_ms)


def _handler_reload(ctx: PluginContext, args: str) -> CmdResult:
    """Force re-include from device, ignoring all caches and version checks."""
    parsed = _parse_include_args(ctx, args)
    if isinstance(parsed, CmdResult):
        return parsed
    cmd, timeout_ms = parsed
    return _fetch_and_include(ctx, cmd, timeout_ms, force=True)


def _handler_dump(ctx: PluginContext, args: str) -> CmdResult:
    """Pretty-print the included target commands as JSON.

    Preserves the ``version`` key from the original JSON so a dump is
    a valid input to a future ``/include.reload``.
    """
    target = ctx.ns("target_commands")
    if not target:
        ctx.result("No target commands included.")
        return CmdResult.ok()
    payload = _to_json_dict(target, _get_version(ctx))
    for line in json.dumps(payload, indent=2).splitlines():
        ctx.output(f"  {line}")
    return CmdResult.ok()


def _handler_clear(ctx: PluginContext, args: str) -> CmdResult:
    """Remove all included target commands and delete cache file.

    Only touches the ``target_commands`` namespace and the on-disk
    cache.  ``active_profile`` is owned by ``/profile.load`` and the
    disconnect lifecycle hook -- ``/include`` never writes it, so it
    has nothing to clean up here.
    """
    ctx.ns("target_commands").clear()
    _set_version(ctx, None)
    try:
        _cache_path(ctx).unlink(missing_ok=True)
    except OSError:
        pass
    ctx.result("Target commands cleared.")
    return CmdResult.ok()


def _handler_list(ctx: PluginContext, args: str) -> CmdResult:
    """List currently included target commands."""
    target = ctx.ns("target_commands")
    if not target:
        ctx.result("No target commands included.")
        return CmdResult.ok()
    for name in sorted(target):
        tc = target[name]
        arg_str = f" {tc.args}" if tc.args else ""
        ctx.output(f"  {name}{arg_str} -- {tc.help}")
    ctx.result(f"{len(target)} target commands.")
    return CmdResult.ok(value=str(len(target)))


# ── Dynamic long_help ─────────────────────────────────────────────────────────

_INCLUDE_PROSE = """\
Sends a command to the device and parses the JSON response to include
command help. Included commands appear in suggestions and /help but
are not REPL commands -- type them directly as device commands.

Check order: memory -> .target_menu.json -> serial command.
Use /include.reload to force a refresh from the device.
Use /include.clear to remove commands and delete the cache.

  /include cmd=AT+HELP.JSON
  /include timeout=2s cmd=HELP_JSON
  /include                       (uses device_json_cmd from config)

JSON format: {"commands": {"cmd": {"help": "...", "args": "..."}, ...}}"""


def _include_state_line(ctx: PluginContext) -> str:
    n = ns_count(ctx, "target_commands")
    if n == 0:
        return green("Currently included: none")
    word = "command" if n == 1 else "commands"
    return green(f"Currently included: {n} device {word}")


def _include_long_help(ctx: PluginContext) -> str:
    return compose(_include_state_line(ctx), _INCLUDE_PROSE)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    "Include device command help from JSON response.",
    name="include",
    args="{timeout=<dur>} {cmd=<command>}",
    handler=_handler,
    long_help=_include_long_help,
    sub_commands={
        "reload": Command(
            "Re-include from device, ignoring all caches.",
            handler=_handler_reload,
            args="{timeout=<dur>} {cmd=<command>}",
            needs=CapabilitySet(serial_connected=True),
            long_help=_include_state_line,
        ),
        "dump": Command(
            "Dump included commands as JSON.",
            handler=_handler_dump,
            long_help=_include_state_line,
        ),
        "clear": Command(
            "Remove all included target commands and cache.",
            handler=_handler_clear,
            long_help=_include_state_line,
        ),
        "list": Command(
            "List currently included target commands.",
            handler=_handler_list,
            long_help=_include_state_line,
        ),
    },
)
