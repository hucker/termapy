"""MCP stdio server: MCPHost + run_command tool + resources.

Phase 3 deliverable.  When ``termapy --mcp [config]`` runs:

1. The ``mcp`` SDK is lazy-imported.  Default termapy installs don't
   pay the pydantic load cost.
2. An MCPHost is constructed (parallel to CLITerminal): config + REPL
   engine + plugin context + serial layer.
3. A FastMCP server registers ONE tool (``run_command``) and TWO
   resource patterns (``termapy://commands.json``,
   ``termapy://capture/<filename>``).
4. The stdio loop runs.

**Architecture:**

- Stdout is reserved for MCP protocol frames.  Never ``print()`` to
  stdout.  Logging goes to ``<cfg_dir>/mcp/session.log``.
- ``--mcp-verbose`` mirrors log events to stderr in real time for
  dev observability.  Stderr is safe; only stdout is wire-sacred.
- A per-call output buffer (contextvars.ContextVar) collects every
  ``ctx.write`` / ``ctx.write_markup`` invocation so ``run_command``
  can ship them back as ``output_lines`` in the response.
- An asyncio lock serializes all ``run_command`` invocations.  The
  REPL engine is single-threaded by design; SerialEngine has one
  reader thread.  Don't try to be clever.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from pathlib import Path
from typing import TYPE_CHECKING, Any

from termapy.capture import CaptureEngine
from termapy.config import open_serial
from termapy.mcp.catalog import (
    build_catalog,
    build_device_state,
    catalog_json,
    device_state_json,
)
from termapy.plugins import (
    CapabilitySet,
    CmdResult,
    PluginContext,
)
from termapy.repl import ReplEngine
from termapy.serial_engine import SerialEngine
from termapy.terminal_host import TerminalHost

if TYPE_CHECKING:
    import argparse


_INSTALL_HINT = (
    "termapy: --mcp requires the 'mcp' optional dependency.\n"
    "Install with:  pip install termapy[mcp]\n"
    "             (or with uv:  uv pip install termapy[mcp])"
)


# ── Per-call output buffer (contextvars; safe under asyncio) ────────────────


# A list-of-dicts collected during one run_command invocation.  Each
# entry: {"level": "text"|"markup", "text": str, "color": str}.
_buffer: contextvars.ContextVar[list[dict[str, Any]] | None] = (
    contextvars.ContextVar("termapy_mcp_buffer", default=None)
)


# ── MCPHost ─────────────────────────────────────────────────────────────────


class MCPHost(TerminalHost):
    """Headless terminal host driven by MCP protocol over stdio.

    Parallel to CLITerminal: same engine + repl + ctx, different
    output sinks.  Output goes to a per-call buffer (the run_command
    response) and to ``<cfg_dir>/mcp/session.log``.  Optionally tees
    log events to stderr when ``--mcp-verbose`` is set.
    """

    def __init__(
        self,
        cfg: dict,
        config_path: str,
        *,
        verbose: bool = False,
    ) -> None:
        self.cfg = cfg
        self.config_path = config_path
        self.verbose = verbose
        self.prefix = cfg.get("cmd_prefix", "/")
        self._xfer_cancel = threading.Event()
        self._reader_thread: threading.Thread | None = None

        # Resolve mcp/ state directory and ensure it exists.
        self._mcp_dir = self._resolve_mcp_dir()
        self._mcp_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = self._mcp_dir / "session.log"
        self._log_lock = threading.Lock()

        # device_state tracking (served by termapy://device_state.json).
        # Updated by run_command_async after each dispatch.  expect_history
        # filled when an /expect call resolves.  async_events/errors stay
        # empty until Phase 5+ NDJSON pipeline.
        self._last_command: dict[str, Any] | None = None
        self._expect_history: list[dict[str, Any]] = []
        self._async_events: list[dict[str, Any]] = []
        self._async_errors: list[dict[str, Any]] = []

        # Engines (mirroring CLITerminal construction).
        self.capture = CaptureEngine(
            on_echo=lambda line: self.write(f"  {line}"),
            on_complete=lambda result: self.status(
                f"Capture complete: {result.path} ({result.size_label})"
            ),
        )
        self.engine = SerialEngine(
            cfg=cfg,
            capture=self.capture,
            open_fn=open_serial,
            log=self._log,
        )
        # ReplEngine.write goes through self.write so the engine's own
        # error/status writes (e.g. dispatch's err_msg display) feed
        # the same buffer the handler-level writes feed.
        self.repl = ReplEngine(
            cfg, config_path, write=self.write, prefix=self.prefix
        )

        # Var/cfg interpolation, matching CLITerminal.
        from termapy.builtins.plugins.var import (
            register_cfg_vars,
            set_context_var,
            set_launch_var,
        )
        from termapy.config import cfg_log_path

        set_launch_var("FRONT_END", "mcp")
        set_context_var(
            "CFG",
            lambda: Path(self.config_path).stem if self.config_path else "termapy",
        )
        register_cfg_vars(
            get_config_path=lambda: self.config_path,
            get_cfg=lambda: self.cfg,
            get_log_path=lambda: cfg_log_path(self.config_path)
            if self.config_path
            else "",
        )
        self._setup_context()

    # -- mcp/ state directory --------------------------------------------------

    def _resolve_mcp_dir(self) -> Path:
        """Return the per-config mcp/ state directory.

        Lives under the same parent as the config file so logs and
        cached profiles travel with the config.  For zero-config
        sessions (no config_path), falls back to cwd/mcp.
        """
        if self.config_path:
            return Path(self.config_path).parent / "mcp"
        return Path.cwd() / "mcp"

    # -- TerminalHost overrides -----------------------------------------------

    def write(self, text: str, color: str = "") -> None:
        """Capture into the active per-call buffer; log; tee to stderr if verbose."""
        buf = _buffer.get()
        if buf is not None:
            buf.append({"level": "text", "text": text, "color": color or ""})
        # Always log so post-mortem review has the full picture.
        self._log_line(f"  {text}")

    def write_markup(self, text: str) -> None:
        """Capture rich-markup output as a separate entry kind."""
        buf = _buffer.get()
        if buf is not None:
            buf.append({"level": "markup", "text": text, "color": ""})
        self._log_line(f"  {text}")

    def status(self, text: str, color: str = "") -> None:
        """Status message: same buffer sink as write, indented in the log."""
        buf = _buffer.get()
        if buf is not None:
            buf.append({"level": "text", "text": text, "color": color or "dim"})
        self._log_line(f"  {text}")

    def _log(self, direction: str, text: str) -> None:
        """SerialEngine log callback.  ``direction`` is ``>`` (TX) or ``<`` (RX)."""
        self._log_line(f"{direction} {text}")

    def _log_line(self, line: str) -> None:
        """Append a line to the session log.  Tee to stderr if verbose.

        NEVER writes to stdout -- stdout is the MCP wire.  Asserts at
        runtime that no log path resolves to ``sys.stdout`` so a future
        misconfiguration fails fast.
        """
        # Defensive: ensure the log file isn't aliased to stdout.  In
        # normal operation log_path is a regular file under cap_dir; the
        # check here is cheap insurance.
        assert self._log_path != Path("/dev/stdout"), (
            "MCP session log MUST NOT alias stdout (would corrupt protocol)"
        )
        with self._log_lock:
            try:
                with self._log_path.open("a", encoding="utf-8") as f:
                    f.write(line.rstrip("\n") + "\n")
            except OSError:
                pass  # logging is non-critical
            if self.verbose:
                print(line.rstrip("\n"), file=sys.stderr, flush=True)

    def _start_reader(self) -> None:
        """Start the background serial reader thread."""

        def on_lines(lines: list[str]) -> None:
            # Inbound serial bytes -> log + (when we're inside a
            # run_command) buffer them so Claude sees them in the
            # response.  Outside run_command (e.g. async events between
            # calls), only logged.
            for line in lines:
                self._log_line(f"< {line}")
                buf = _buffer.get()
                if buf is not None:
                    buf.append({"level": "rx", "text": line, "color": ""})

        self._reader_thread = threading.Thread(
            target=self.engine.read_loop,
            kwargs={
                "on_lines": on_lines,
                "on_clear": lambda: None,
                "on_capture_done": lambda: self._stop_capture(),
                "on_error": lambda detail: self._log_line(
                    f"! Serial error: {detail}"
                ),
                "on_disconnect": lambda: self._log_line("! Serial disconnected"),
            },
            daemon=True,
        )
        self._reader_thread.start()

    def _confirm(self, message: str) -> bool:
        """No interactive UI in MCP mode -- destructive commands fail-fast."""
        return False

    # -- Context setup ---------------------------------------------------------

    def _setup_context(self) -> None:
        """Build PluginContext with MCP-specific overrides."""
        engine_api = self._build_engine_api()
        self.ctx = self._build_plugin_context(engine_api)
        # MCP-specific UI no-ops (no screen, no toast, no clear).
        self.ctx.notify = lambda text, **kw: None
        self.ctx.clear_screen = lambda: None
        self.ctx.exit_app = lambda: None
        self.ctx.get_screen_text = lambda: ""
        # Wire wait_for_match to the engine's serial-line predicate
        # waiter so /expect can block on serial input.  Each
        # run_command runs in a worker thread (asyncio executor); the
        # asyncio lock serializes them, so the engine's single-active-
        # predicate model is honored.
        self.ctx.wait_for_match = self.repl.wait_for_match
        # Capabilities: baseline + block_until (for /expect).  Keeps
        # tui_mode/confirm_dialog/etc. off so commands that need a
        # screen still fail-fast.
        self.ctx.capabilities = CapabilitySet(block_until=True)
        self.repl.set_context(self.ctx)
        # MCP runs without echoing typed input (there's no human typing).
        # Default output_level "quiet" so Claude gets results, not
        # progress chatter.  Per-call override via run_command(output=...).
        self._init_flags(echo=False)
        self.ctx.ns("flags")["output_level"] = "quiet"
        self._register_hooks()

    def _register_hooks(self) -> None:
        """Register MCP-mode hooks that aren't built-in plugins.

        Mirrors a subset of CLITerminal._register_hooks().  Notable
        difference: no progress bar UI for /delay (MCP has no display).
        """
        from termapy.scripting import parse_duration

        def _hook_delay(ctx: PluginContext, args: str) -> CmdResult:
            """Sleep for ``args`` (e.g. ``500ms``, ``2s``).  No UI."""
            try:
                duration = parse_duration(args)
            except ValueError as e:
                return CmdResult.fail(msg=f"Invalid delay: {e}")
            import time as _time

            _time.sleep(duration)
            return CmdResult.ok()

        self.repl.register_hook(
            "delay",
            "<duration>",
            "Pause for a duration (e.g. 500ms, 2s).",
            _hook_delay,
            source="app",
        )
        self.repl.register_hook(
            "delay.silent",
            "<duration>",
            "Pause silently.",
            _hook_delay,
            source="app",
        )


# ── FastMCP server wiring (lazy, only when --mcp runs) ──────────────────────


def _build_server(host: MCPHost) -> Any:
    """Return a configured FastMCP server with one tool + two resources.

    Lazy-imports the SDK so importing termapy.mcp.server stays cheap.
    """
    from mcp.server.fastmcp import FastMCP

    server: Any = FastMCP("termapy")

    # ── run_command tool ────────────────────────────────────────────────────
    @server.tool()
    async def run_command(
        command: str,
        output: str = "normal",
        timeout_s: float = 30.0,
    ) -> dict[str, Any]:
        """Run a single termapy REPL command and return its result.

        Args:
            command: The literal REPL command line, with prefix.  Examples:
                ``/help``, ``/port.connect COM3``, ``AT+VER`` (sends to
                serial via /term.send), ``/cap.text out.txt timeout=2s``.
            output: Output level for this call.  One of ``silent``,
                ``quiet``, ``normal`` (default), ``verbose``.  Translated
                into the universal ``--<level>`` flag the engine already
                supports.
            timeout_s: Outer wall-clock cap for the call (default 30s).

        Returns:
            JSON-able dict with ``cmd``, ``success``, ``error``, ``value``,
            ``elapsed_s``, ``output_lines``, ``captured_artifacts``.
        """
        return await host.run_command_async(command, output, timeout_s)

    # ── catalog resource ────────────────────────────────────────────────────
    @server.resource("termapy://commands.json")
    def commands_resource() -> str:
        """Termapy's command catalog as JSON.  Same content as /mcp.catalog."""
        return catalog_json(host.ctx)

    # ── device_state resource ──────────────────────────────────────────────-
    @server.resource("termapy://device_state.json")
    def device_state_resource() -> str:
        """Live snapshot of port/profile/last_command/captures.

        The LLM-as-debugger "where am I" view.  Refreshed on every
        read.  Tracks state that lives on MCPHost (last_command,
        expect_history, async_events/errors) plus things derived
        from ctx (port, profile, captures).
        """
        return device_state_json(
            host.ctx,
            last_command=host._last_command,
            expect_history=host._expect_history,
            async_events=host._async_events,
            async_errors=host._async_errors,
        )

    # ── capture resources ──────────────────────────────────────────────────-
    @server.resource("termapy://capture/{filename}")
    def capture_resource(filename: str) -> str:
        """Read a capture artifact from cap_dir.  Path-traversal guarded."""
        cap_dir = Path(host.ctx.cap_dir).resolve()
        target = (cap_dir / filename).resolve()
        if not target.is_relative_to(cap_dir):
            raise ValueError(f"Path traversal blocked: {filename!r}")
        if not target.exists():
            raise FileNotFoundError(f"Capture not found: {filename!r}")
        # Decode by extension; binary captures get base64-tagged hex prefix.
        if target.suffix.lower() in (".bin", ".dat"):
            return target.read_bytes().hex()
        return target.read_text(encoding="utf-8", errors="replace")

    return server


# ── Lock + run_command implementation ──────────────────────────────────────-


# Bolt the async helper onto MCPHost via monkey-patch so the class body
# stays focused on lifecycle / sinks.  The async lock + dispatch + buffer
# pattern is MCP-specific glue; keeping it adjacent to the FastMCP wiring
# above makes the data flow easier to follow.


def _serialize_lock(host: MCPHost) -> asyncio.Lock:
    """Lazily attach (and reuse) the per-host asyncio lock."""
    lock = getattr(host, "_run_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        host._run_lock = lock
    return lock


def _snapshot_cap_dir(cap_dir: Path) -> dict[str, float]:
    """Return {filename: mtime} for files in cap_dir.  Empty when missing."""
    if not cap_dir.exists():
        return {}
    return {
        p.name: p.stat().st_mtime
        for p in cap_dir.iterdir()
        if p.is_file()
    }


def _new_artifacts(
    before: dict[str, float], after: dict[str, float], cap_dir: Path
) -> list[dict[str, Any]]:
    """Identify capture files added or modified during a dispatch."""
    out: list[dict[str, Any]] = []
    for name, mtime in sorted(after.items()):
        if before.get(name) == mtime:
            continue
        path = cap_dir / name
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        out.append(
            {
                "uri": f"termapy://capture/{name}",
                "name": name,
                "bytes": size,
            }
        )
    return out


async def _run_command_async(
    self: MCPHost,
    command: str,
    output: str,
    timeout_s: float,
) -> dict[str, Any]:
    """Implementation of MCPHost.run_command_async (attached below)."""
    # Validate output level early; default to "normal" on bad input.
    valid_levels = {"silent", "quiet", "normal", "verbose"}
    level = output if output in valid_levels else "normal"

    async with _serialize_lock(self):
        # Per-call buffer in a contextvar so the sync write/log path
        # picks it up without parameter threading.
        token = _buffer.set([])
        cap_before = _snapshot_cap_dir(Path(self.ctx.cap_dir))
        line = command.strip()
        # Append --<level> flag so the engine's universal level pre-pass
        # routes the call at the desired loudness.  "normal" is the
        # default; skip the suffix to avoid pointless flag-parsing.
        if level != "normal":
            line = f"{line} --{level}"

        try:
            # Run the (synchronous) dispatch in a thread so we can apply
            # an outer wall-clock timeout without blocking the asyncio
            # loop.  Termapy's REPL engine is single-threaded; this
            # offload is safe because the asyncio lock above serializes
            # MCP-driven dispatches.  Critical: run via copy_context()
            # so the per-call buffer (a contextvar set just above) is
            # visible inside the worker thread.
            loop = asyncio.get_running_loop()

            def _run_sync() -> CmdResult:
                self._log_line(f"$ {command}  ({level})")
                # Use dispatch_full so /raw bypass + directive layer
                # + the /term.send fallthrough all behave normally.
                return self.repl.dispatch_full(
                    line,
                    log=self._log,
                    echo_markup=self.write_markup,
                    status=self.status,
                    serial_write=self._serial_write,
                    serial_write_raw=self._serial_write_raw,
                    is_connected=lambda: self.engine.is_connected,
                )

            ctx_snapshot = contextvars.copy_context()
            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = loop.run_in_executor(ex, ctx_snapshot.run, _run_sync)
                try:
                    result = await asyncio.wait_for(fut, timeout=timeout_s)
                except (asyncio.TimeoutError, FutTimeout):
                    result = CmdResult.fail(
                        msg=f"timeout after {timeout_s}s"
                    )
        finally:
            output_lines = _buffer.get() or []
            _buffer.reset(token)

        cap_after = _snapshot_cap_dir(Path(self.ctx.cap_dir))
        artifacts = _new_artifacts(cap_before, cap_after, Path(self.ctx.cap_dir))

        # Track for device_state resource.  ISO 8601 timestamp matches
        # the spec's "<at>" field; UTC with explicit Z avoids TZ surprises.
        from datetime import datetime, timezone

        at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._last_command = {
            "cmd": command,
            "success": bool(result.success),
            "elapsed_s": float(result.elapsed_s or 0.0),
            "at": at,
        }
        # /expect calls go into expect_history with match status.
        cmd_name = (command.strip().lstrip(self.prefix).split() or [""])[0]
        if cmd_name in ("expect", "expect.regex"):
            self._expect_history.append(
                {
                    "command": command,
                    "matched": bool(result.success),
                    "value": result.value or "",
                    "elapsed_s": float(result.elapsed_s or 0.0),
                    "at": at,
                }
            )
            # Cap history at last 50 entries to keep the resource small.
            if len(self._expect_history) > 50:
                self._expect_history = self._expect_history[-50:]

        return {
            "cmd": command,
            "success": bool(result.success),
            "error": result.error or "",
            "value": result.value or "",
            "elapsed_s": float(result.elapsed_s or 0.0),
            "output_lines": output_lines,
            "captured_artifacts": artifacts,
        }


# Attach the async method.  Keeps MCPHost class focused on lifecycle/sinks
# and the run_command logic (which is MCP-specific glue) co-located with
# its FastMCP wiring above.
MCPHost.run_command_async = _run_command_async  # type: ignore[attr-defined]


# ── Entry point ────────────────────────────────────────────────────────────


def run_mcp_stdio(args: argparse.Namespace) -> None:
    """Run the MCP stdio server.  Called by ``termapy --mcp``.

    Verifies the SDK is installed, builds an MCPHost, registers the
    tool + resources, then enters the FastMCP stdio loop.  Stdout is
    reserved for protocol frames; never ``print()`` to stdout.
    """
    try:
        import mcp  # noqa: F401
    except ImportError:
        print(_INSTALL_HINT, file=sys.stderr)
        sys.exit(1)

    # Lazy-import config helpers so termapy.mcp.server stays cheap.
    from termapy.config import load_config
    from termapy.config_resolve import find_config, resolve_config

    cfg_arg = getattr(args, "config", None)
    cfg_path = ""
    if cfg_arg:
        resolved = resolve_config(cfg_arg)
        if resolved:
            cfg_path = resolved
    else:
        # No positional config: try to auto-detect a single .cfg in cwd.
        auto, _ = find_config()
        if auto:
            cfg_path = auto

    if cfg_path:
        cfg = load_config(cfg_path)
    else:
        # Zero-config fallback: in-memory defaults.
        from termapy.defaults import DEFAULT_CFG

        cfg = dict(DEFAULT_CFG)
        cfg_path = ""

    verbose = bool(getattr(args, "mcp_verbose", False))
    host = MCPHost(cfg, cfg_path, verbose=verbose)

    if verbose:
        print(
            f"termapy --mcp: host built; cfg={cfg_path or '(none)'}, "
            f"mcp_dir={host._mcp_dir}",
            file=sys.stderr,
        )

    # Auto-connect if a port is configured.  Failure to open a port
    # doesn't abort the server -- Claude can /port.connect later.
    if cfg.get("port"):
        try:
            host._connect()
        except (OSError, Exception) as exc:  # noqa: BLE001 - boundary
            host._log_line(f"! Auto-connect failed: {exc}")

    server = _build_server(host)

    # Fire on_app_start lifecycle so plugins that registered hooks at
    # plugin-load time get a chance to initialize.
    host.repl.fire_lifecycle("on_app_start")
    try:
        # FastMCP.run() blocks until the stdio peer disconnects.  No
        # other transports -- if you want HTTP, that's deferred to v2
        # (see plan: "TUI + MCP simultaneously via HTTP transport").
        server.run(transport="stdio")
    finally:
        host.repl.fire_lifecycle("on_app_stop")
        if host.engine.is_connected:
            host._disconnect()
