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
  ``ctx.io._write`` / ``ctx.io._write_markup`` invocation so ``run_command``
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

        # Resolve mcp/ state directory and log path -- both follow the
        # cfg, per termapy convention.  When no cfg is loaded (zero-config
        # slot waiting for /cfg.load), both are None and file logging is
        # suppressed; stderr tee under --mcp-verbose still works.
        self._mcp_dir: Path | None = None
        self._log_path: Path | None = None
        self._log_lock = threading.Lock()
        self._refresh_log_paths()

        # device_state tracking (served by termapy://device_state.json).
        # Updated by run_command_async after each dispatch.  expect_history
        # filled when an /expect call resolves.  async_events/errors stay
        # empty until Phase 5+ NDJSON pipeline.
        self._last_command: dict[str, Any] | None = None
        self._expect_history: list[dict[str, Any]] = []
        self._async_events: list[dict[str, Any]] = []
        self._async_errors: list[dict[str, Any]] = []
        # Banner-watch state, populated by _on_connect_banner_watch.
        self._banner_seen: bool = False
        self._banner_text: str = ""

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
        from termapy.builtins.commands.var import (
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

    def _resolve_mcp_dir(self) -> Path | None:
        """Return the per-config mcp/ state directory, or None if no cfg.

        Lives under the same parent as the config file so logs and
        cached profiles travel with the config -- termapy's standard
        convention.  When the slot has no cfg loaded yet, returns None
        and the caller suppresses file logging (stderr tee still works
        under ``--mcp-verbose``).
        """
        if self.config_path:
            return Path(self.config_path).parent / "mcp"
        return None

    def _refresh_log_paths(self) -> None:
        """Recompute ``_mcp_dir`` and ``_log_path`` from ``config_path``.

        Called at construction and after each ``/cfg.load`` (via
        ``_switch_to_cfg_path``) so a slot's log routes to the active
        cfg's ``mcp/`` directory.  Idempotent: safe to call any time.
        """
        self._mcp_dir = self._resolve_mcp_dir()
        if self._mcp_dir is not None:
            self._mcp_dir.mkdir(parents=True, exist_ok=True)
            self._log_path = self._mcp_dir / "session.log"
        else:
            self._log_path = None

    # -- TerminalHost overrides -----------------------------------------------

    # Note: ``_setup_context`` runs ``_refresh_log_paths`` (see below).
    # ``_setup_context`` is invoked by ``_switch_to_cfg_path`` BEFORE the
    # auto-connect, so on-connect log entries (echo off, mcp_on_connect_cmd)
    # land in the new cfg's mcp/ directory rather than the old one.

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

        When ``_log_path`` is None (zero-config slot, no cfg loaded
        yet), file logging is suppressed and only the stderr tee
        applies.  Once ``/cfg.load`` runs, ``_refresh_log_paths`` wires
        the file logger to the new cfg's ``mcp/`` directory.
        """
        with self._log_lock:
            if self._log_path is not None:
                # Defensive: ensure the log file isn't aliased to stdout.
                # In normal operation log_path is a regular file under
                # the cfg's mcp/ dir; the check here is cheap insurance.
                assert self._log_path != Path("/dev/stdout"), (
                    "MCP session log MUST NOT alias stdout (would corrupt protocol)"
                )
                try:
                    with self._log_path.open("a", encoding="utf-8") as f:
                        f.write(line.rstrip("\n") + "\n")
                except OSError:
                    pass  # logging is non-critical
            if self.verbose:
                print(line.rstrip("\n"), file=sys.stderr, flush=True)

    def _record_async_event(self, line: str, *, source: str) -> None:
        """Append an async serial event with a UTC timestamp and source tag.

        ``source`` distinguishes lines that arrived between MCP calls
        (``"between_calls"``) from lines that were sitting in the
        recent-lines buffer when a profile-mapped command was sent
        (``"pre_send_drain"``).  The ring is capped to keep the
        device_state resource bounded.
        """
        from datetime import datetime, timezone

        self._async_events.append(
            {
                "line": line,
                "source": source,
                "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
        if len(self._async_events) > 50:
            self._async_events = self._async_events[-50:]

    def _start_reader(self) -> None:
        """Start the background serial reader thread."""

        def on_lines(lines: list[str]) -> None:
            # Inbound serial bytes -> feed the engine's expect-watcher
            # ring buffer (so /expect sees them), log, and -- when
            # we're inside a run_command -- append to the buffer so
            # Claude sees them in the response.  Outside run_command
            # (e.g. async events between calls), append to async_events
            # so they survive into the next device_state read.
            self.repl.feed_lines(lines)
            buf = _buffer.get()
            for line in lines:
                self._log_line(f"< {line}")
                if buf is not None:
                    buf.append({"level": "rx", "text": line, "color": ""})
                else:
                    self._record_async_event(line, source="between_calls")

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
        """Build PluginContext with MCP-specific overrides.

        Also refreshes log paths -- ``_setup_context`` runs whenever
        the active cfg changes (construction, /cfg.load), and our log
        files live with the cfg.  Refreshing here means on-connect
        commands that fire AFTER context setup but BEFORE
        ``_switch_to_cfg_path`` returns get logged into the *new*
        cfg's mcp/ directory, not the old one.
        """
        self._refresh_log_paths()
        engine_api = self._build_engine_api()
        self.ctx = self._build_plugin_context(engine_api)
        # MCP-specific UI no-ops (no screen, no toast, no clear).
        self.ctx.io.notify = lambda text, **kw: None
        self.ctx.io.clear_screen = lambda: None
        self.ctx.ui.exit_app = lambda: None
        self.ctx.ui.get_screen_text = lambda: ""
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

        # auto-include + banner-watch fire from _on_connected (the
        # TerminalHost override below).  We don't use the on_connect
        # lifecycle hook for these because TerminalHost._connect
        # fires the lifecycle BEFORE _start_reader() -- the reader
        # thread isn't pumping yet, so /include can't read the
        # device's reply.  _on_connected runs AFTER _start_reader.

    def _on_connected(self, message: str) -> None:
        """Override: run post-reader-start hooks (auto-include, banner watch).

        TerminalHost._connect fires the on_connect lifecycle BEFORE
        starting the reader thread, then calls _on_connected after.
        Auto-include needs the reader pumping (it dispatches a serial
        command and waits for a response), so it lives here.  Banner
        watcher uses ctx.wait_for_match which also needs the reader's
        feed_lines calls.
        """
        super()._on_connected(message)
        self._on_connect_auto_load_profile(self.ctx)
        self._on_connect_auto_include(self.ctx)
        self._on_connect_run_commands(self.ctx)
        self._on_connect_banner_watch(self.ctx)

    def _clear_device_state(self) -> None:
        """Extend base clear with MCP-specific per-device attributes.

        Banner state, the last-command record, the expect history, and
        the async event/error queues all describe the box that just
        disconnected.  Carrying them across a port switch would mix
        the previous device's traces into the next session's
        device_state.json -- a confusing reading for an LLM.
        """
        super()._clear_device_state()
        self._banner_seen = False
        self._banner_text = ""
        self._last_command = None
        self._expect_history.clear()
        self._async_events.clear()
        self._async_errors.clear()

    def _on_connect_auto_load_profile(self, ctx: PluginContext) -> None:
        """Load a v2 profile on connect: explicit cfg.profile_path, then convention.

        MCP-only.  TUI/CLI users stay in text-to-text land unless they
        explicitly run ``/profile.load``.  Lookup order:

        1. ``cfg.profile_path`` -- explicit, wins.
        2. ``<cfg_dir>/<cfg_name>.profile.json`` -- convention,
           "one profile per device folder, named after the cfg".
        3. Nothing found -> no profile loaded.  ``_dispatch_via_profile``
           returns None for every call, falling through to the legacy
           literal-write path.  Lets a user spin up an MCP session
           against an unprofiled device for exploratory work.

        Errors are non-fatal: a malformed or unreadable profile logs
        a warning but doesn't block the connect.
        """
        explicit = ctx.cfg.get("profile_path", "")
        candidate: Path | None = None
        if explicit and isinstance(explicit, str):
            candidate = Path(explicit)
        elif self.config_path:
            cfg_path = Path(self.config_path)
            convention = cfg_path.parent / f"{cfg_path.stem}.profile.json"
            if convention.exists():
                candidate = convention
        if candidate is None:
            return
        if not candidate.exists():
            self._log_line(f"! auto-load profile: not found: {candidate}")
            return
        self._log_line(f"$ /profile.load {candidate}  (auto-load on connect)")
        try:
            result = self.repl.dispatch(f"profile.load {candidate}")
        except Exception as exc:  # noqa: BLE001 -- boundary
            self._log_line(f"! auto-load profile failed: {exc}")
            return
        if not result.success:
            self._log_line(f"! auto-load profile: {result.error}")

    def _on_connect_auto_include(self, ctx: PluginContext) -> None:
        """Run ``/include`` after connect when configured.

        Fires when ``cfg.auto_include_on_connect`` is True and
        ``cfg.device_json_cmd`` is set.  Errors are non-fatal -- the
        device may not actually serve a help-JSON command, in which
        case the user/profile is the source of truth.
        """
        if not ctx.cfg.get("auto_include_on_connect", True):
            return
        if not ctx.cfg.get("device_json_cmd", ""):
            return
        self._log_line("$ /include  (auto-include on connect)")
        try:
            result = self.repl.dispatch("include")
        except Exception as exc:  # noqa: BLE001 -- boundary
            self._log_line(f"! auto-include failed: {exc}")
            return
        if not result.success:
            self._log_line(f"! auto-include: {result.error}")

    def _on_connect_run_commands(self, ctx: PluginContext) -> None:
        """Fire ``on_connect_cmd`` then ``mcp_on_connect_cmd`` after connect.

        Closes a latent bug: pre-v15 MCP never ran ``on_connect_cmd``
        at all (TUI/CLI did, but MCP's ``_on_connected`` override
        silently skipped the universal key).  Now MCP fires both the
        universal key (for cfgs that genuinely want a command run on
        every frontend) and ``mcp_on_connect_cmd`` (for MCP-only
        device setup like ``echo off``).

        The v14->v15 migration moves any pre-existing ``on_connect_cmd``
        value into the per-mode interactive keys, so an upgrade does
        NOT silently start firing TUI/CLI-authored commands in MCP.
        Net effect for upgraded cfgs: same as before (nothing fires
        in MCP) until the user opts in by setting ``mcp_on_connect_cmd``
        or moving content back to the universal key.

        Errors are non-fatal: a misbehaving device-setup command logs
        a warning but doesn't block the connect.
        """
        sources: list[tuple[str, str]] = [
            (ctx.cfg.get("on_connect_cmd", ""), "on_connect_cmd"),
            (ctx.cfg.get("mcp_on_connect_cmd", ""), "mcp_on_connect_cmd"),
        ]
        for source, label in sources:
            if not source:
                continue
            for cmd in source.replace("\\n", "\n").split("\n"):
                cmd = cmd.strip()
                if not cmd:
                    continue
                self._log_line(f"$ {cmd}  ({label})")
                try:
                    result = self.repl.dispatch_full(
                        cmd,
                        log=self._log,
                        echo_markup=self.write_markup,
                        status=self.status,
                        serial_write=self._serial_write,
                        serial_write_raw=self._serial_write_raw,
                        is_connected=lambda: self.engine.is_connected,
                    )
                except Exception as exc:  # noqa: BLE001 -- boundary
                    self._log_line(f"! {label} ({cmd!r}) failed: {exc}")
                    continue
                if not result.success:
                    self._log_line(f"! {label} ({cmd!r}): {result.error}")

    def _on_connect_banner_watch(self, ctx: PluginContext) -> None:
        """Spawn a 2-second watcher for the active profile's startup banner.

        If the active profile declares ``device.startup_banner``, scan
        the first 4KB of post-connect input for that pattern.  Match
        records ``self._banner_seen`` / ``self._banner_text``; non-match
        logs a warning.  Never blocks the lifecycle.

        The watcher uses ``ctx.wait_for_match`` with a substring or
        regex predicate -- same machinery /expect uses, so it
        cooperates with the engine's recent-lines buffer and the
        reader thread's ``feed_lines`` calls.
        """
        active = ctx.ns("active_profile")
        device = active.get("device") if isinstance(active, dict) else None
        if not isinstance(device, dict):
            return
        pattern = device.get("startup_banner", "")
        if not pattern or not isinstance(pattern, str):
            return

        import re as _re

        def watch() -> None:
            try:
                compiled = _re.compile(pattern)

                def predicate(line: str) -> bool:
                    return bool(compiled.search(line))

                match = ctx.wait_for_match(predicate, timeout=2.0)
            except Exception as exc:  # noqa: BLE001 -- boundary thread
                self._log_line(f"! banner watcher error: {exc}")
                return
            if match:
                self._banner_seen = True
                self._banner_text = match
                self._log_line(f"  banner: {match}")
            else:
                self._log_line(
                    f"! banner not seen within 2s (pattern: {pattern!r})"
                )

        threading.Thread(target=watch, daemon=True, name="mcp-banner-watch").start()


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
        confirm: bool = False,
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
            confirm: Pass ``True`` only after the human has explicitly
                approved a destructive command.  Commands flagged
                ``safety: destructive`` in the active profile (e.g.
                resets, factory wipes, flash erases) refuse to run
                without it -- the server returns an error asking for
                confirmation.  Safe and readonly commands ignore this
                parameter.

        Returns:
            JSON-able dict with ``cmd``, ``success``, ``error``, ``value``,
            ``elapsed_s``, ``output_lines``, ``captured_artifacts``.
        """
        return await host.run_command_async(
            command, output, timeout_s, confirm=confirm
        )

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
            banner_seen=host._banner_seen,
            banner_text=host._banner_text,
        )

    # ── capture resources ──────────────────────────────────────────────────-
    @server.resource("termapy://capture/{filename}")
    def capture_resource(filename: str) -> str:
        """Read a capture artifact from cap_dir.  Path-traversal guarded."""
        cap_dir = Path(host.ctx.fs.cap_dir).resolve()
        target = (cap_dir / filename).resolve()
        if not target.is_relative_to(cap_dir):
            raise ValueError(f"Path traversal blocked: {filename!r}")
        if not target.exists():
            raise FileNotFoundError(f"Capture not found: {filename!r}")
        # Decode by extension; binary captures get base64-tagged hex prefix.
        if target.suffix.lower() in (".bin", ".dat"):
            return target.read_bytes().hex()
        return target.read_text(encoding="utf-8", errors="replace")

    # Prompts: user-invocable conversation recipes (e.g. draft_profile).
    # Lives in mcp/prompts.py to keep this file focused on host wiring.
    from termapy.mcp.prompts import register_prompts
    register_prompts(server, host)

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


# ── Profile-aware request/response dispatch ────────────────────────────────


def _template_to_regex(template: str) -> str:
    """Turn an `AT+LED={state}` send_template into a match regex.

    Each `{name}` placeholder becomes `(?P<name>.+?)`.  Surrounding
    literals are escaped.  Anchored at both ends so partial matches
    don't confuse lookup.
    """
    import re as _re

    pattern_parts: list[str] = []
    pos = 0
    for m in _re.finditer(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", template):
        pattern_parts.append(_re.escape(template[pos:m.start()]))
        pattern_parts.append(f"(?P<{m.group(1)}>.+?)")
        pos = m.end()
    pattern_parts.append(_re.escape(template[pos:]))
    return "^" + "".join(pattern_parts) + "$"


def _match_profile_command(
    text: str, commands: dict[str, dict],
) -> tuple[str, dict, dict[str, str]] | None:
    """Find the profile entry that ``text`` invokes, if any.

    Lookup order:

    1. Exact name match (the common case -- LLM types the literal
       command name straight from /help).
    2. Iterate entries with ``send_template`` and try to match
       ``text`` against the template-derived regex.  The first hit
       wins; profile authors shouldn't write overlapping templates.

    Returns (canonical_name, command_dict, bound_args), or None.
    """
    import re as _re

    if text in commands:
        return text, commands[text], {}
    for name, spec in commands.items():
        if not isinstance(spec, dict):
            continue
        tmpl = spec.get("send_template", "")
        if not tmpl:
            continue
        try:
            m = _re.match(_template_to_regex(tmpl), text)
        except _re.error:
            continue
        if m:
            return name, spec, m.groupdict()
    return None


def _dispatch_via_profile(
    host: MCPHost, command_text: str, *, confirm: bool = False,
) -> CmdResult | None:
    """Send a profile-mapped command and shape the response.

    Returns ``None`` when the command isn't a profile entry -- callers
    fall through to ``dispatch_full`` (today's literal-write path).
    Returns a ``CmdResult`` (success or failure) when the profile
    governs the call.

    Lifecycle:
      1. Drain stale recent-lines into async_events (source pre_send_drain)
         so the next wait cycle starts clean.
      2. Send command_text + transport.line_ending_send.
      3. Per response.format: collect lines (lines/multi-line) or wait
         for a single line (literal/regex/json) or skip wait (none).
      4. Run error_detection.pattern over the collected text first;
         a hit short-circuits to fail.
      5. Run response_parsers.parse_response and return value.

    The function is sync; it's called from inside the worker thread
    that already runs dispatch_full.
    """
    # Slash commands always go through the dispatcher -- profile only
    # rewrites bare device commands.
    if command_text.startswith(host.prefix):
        return None
    if not host.engine.is_connected:
        return None
    profile = host.ctx.ns("active_profile")
    if not profile or not isinstance(profile, dict):
        return None
    commands = profile.get("commands") or {}
    if not isinstance(commands, dict):
        return None
    match = _match_profile_command(command_text, commands)
    if match is None:
        return None

    from termapy.request_response import request_response
    from termapy.response_parsers import parse_response

    name, spec, _bound = match

    # Disabled-by-author gate.  ``enabled: false`` means "the engineer
    # has not audited this entry; do NOT run it."  We REFUSE rather
    # than fall through to /term.send because the user's intent is
    # that every command be confirmed before exposure -- a fall-
    # through would let a clever LLM bypass the audit by typing a
    # command name it shouldn't even know exists.  The catalog
    # filter hides disabled entries from the LLM's view; this gate
    # is defense in depth for cases where the LLM gets the name
    # anyway (from help text, prior chat context, etc.).
    # Default true preserves existing curated profiles unchanged.
    if not spec.get("enabled", True):
        result = CmdResult.fail(
            msg=(
                f"Command {name!r} is disabled in the active profile. "
                "Edit the profile and set enabled=true after auditing."
            ),
            value={
                "disabled": True,
                "command": name,
                "help": spec.get("help", ""),
            },
        )
        return result

    # Destructive commands MUST require human-in-the-loop approval.
    # The MCP host can't show a UI, so we surface a structured failure
    # the LLM client can elicit confirmation for, then retry with
    # confirm=True.  Marker fields in the value let well-behaved
    # clients render a confirmation prompt rather than just an error.
    safety = spec.get("safety", "safe")
    if safety == "destructive" and not confirm:
        result = CmdResult.fail(
            msg=(
                f"Confirmation required: {name!r} is destructive. "
                "Re-call with confirm=true after the user has approved."
            ),
            value={
                "needs_confirmation": True,
                "command": name,
                "safety": "destructive",
                "help": spec.get("help", ""),
            },
        )
        return result

    transport = profile.get("transport") or {}
    encoding = transport.get("encoding", "utf-8")
    eol_send = transport.get("line_ending_send", "\r")
    raw_response = spec.get("response") or {}
    response = raw_response if isinstance(raw_response, dict) else {}
    fmt = response.get("format", "none")
    timeout_s = float(
        response.get(
            "timeout_ms",
            transport.get("default_response_timeout_ms", 1000),
        )
    ) / 1000.0

    # Drain -> send -> wait pipeline lives in request_response.py so the
    # generic json_mode fallthrough (repl.py) shares identical timing
    # semantics.  fmt branch maps to:
    #   none   -> wait=False (fire-and-forget; drain+send only)
    #   lines  -> idle_gap=0.1, terminator from response.terminator
    #   else   -> idle_gap=0.05 (one-line responses settle fast)
    if fmt == "none":
        wait = False
        terminator = ""
        idle_gap_s = 0.05  # unused when wait=False
    elif fmt == "lines":
        wait = True
        terminator = response.get("terminator", "")
        idle_gap_s = 0.1
    else:
        wait = True
        terminator = ""
        idle_gap_s = 0.05

    rr = request_response(
        serial_write=host._serial_write,
        drain_recent_lines=host.repl.drain_recent_lines,
        wait_for_lines=host.repl.wait_for_lines,
        command=command_text,
        encoding=encoding,
        line_ending=eol_send,
        timeout_s=timeout_s,
        terminator=terminator,
        idle_gap_s=idle_gap_s,
        on_drained_line=lambda line: host._record_async_event(
            line, source="pre_send_drain"
        ),
        wait=wait,
    )
    if rr["error"]:
        result = CmdResult.fail(msg=rr["error"])
        result.elapsed_s = rr["elapsed_s"]
        return result
    text = rr["text"]
    collected = rr["lines"]
    elapsed = rr["elapsed_s"]

    # Error detection wins over response parsing.
    err_block = profile.get("error_detection") or {}
    err_pat = err_block.get("pattern", "") if isinstance(err_block, dict) else ""
    if err_pat and text:
        import re as _re

        try:
            err_re = _re.compile(err_pat, _re.MULTILINE)
        except _re.error:
            err_re = None
        if err_re is not None:
            for line in collected:
                m = err_re.search(line)
                if m:
                    msg = m.groupdict().get("message") or line
                    result = CmdResult.fail(msg=str(msg))
                    result.elapsed_s = elapsed
                    return result

    if fmt == "none":
        result = CmdResult.ok(value={"sent": True, "cmd": name})
        result.elapsed_s = elapsed
        return result

    if not text:
        result = CmdResult.fail(msg=f"No response within {timeout_s:.2f}s")
        result.elapsed_s = elapsed
        return result

    value = parse_response(
        text,
        fmt,
        pattern=response.get("pattern", ""),
        types=response.get("types"),
        line_pattern=response.get("line_pattern", ""),
        line_types=response.get("line_types"),
        terminator=response.get("terminator", ""),
    )
    if value is None:
        # Parser refused the text (regex didn't match, JSON didn't parse).
        # Surface raw text so the LLM can still see what came back.
        result = CmdResult.fail(
            msg=f"Response did not match {fmt} format",
            value={"raw": text, "cmd": name},
        )
        result.elapsed_s = elapsed
        return result
    result = CmdResult.ok(value=value)
    result.elapsed_s = elapsed
    return result


async def _run_command_async(
    self: MCPHost,
    command: str,
    output: str,
    timeout_s: float,
    *,
    confirm: bool = False,
) -> dict[str, Any]:
    """Implementation of MCPHost.run_command_async (attached below)."""
    # Validate output level early; default to "normal" on bad input.
    valid_levels = {"silent", "quiet", "normal", "verbose"}
    level = output if output in valid_levels else "normal"

    async with _serialize_lock(self):
        # Per-call buffer in a contextvar so the sync write/log path
        # picks it up without parameter threading.
        token = _buffer.set([])
        cap_before = _snapshot_cap_dir(Path(self.ctx.fs.cap_dir))
        line = command.strip()
        # Output-level routing: set ctx._call_level directly for the
        # duration of this dispatch.  Earlier this path appended a
        # ``--<level>`` flag to ``line`` -- but for bare device commands
        # the engine's level pre-pass only fires in ``dispatch()`` (the
        # prefix path), so the flag leaked through ``_term_send_or_request``
        # to the serial wire.  Keep level metadata-only: set it on the
        # PluginContext slot, never touches command_text.

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
                # Apply the per-call output level via ctx slot (replaces
                # the old --<level> text append).  Save/restore matches
                # the pattern in ReplEngine.dispatch.
                saved_call_level = self.ctx._call_level
                if level != "normal":
                    self.ctx._call_level = level
                try:
                    # Profile-aware path: bare device commands that
                    # match a loaded profile entry get sent + parsed
                    # per the response schema, returning a typed value.
                    # Returns None for slash commands, unmapped bare
                    # lines, or no active profile -- fall through to
                    # dispatch_full's literal-write behavior.
                    profiled = _dispatch_via_profile(self, line, confirm=confirm)
                    if profiled is not None:
                        return profiled
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
                finally:
                    self.ctx._call_level = saved_call_level

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

        cap_after = _snapshot_cap_dir(Path(self.ctx.fs.cap_dir))
        artifacts = _new_artifacts(cap_before, cap_after, Path(self.ctx.fs.cap_dir))

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

        # Pass result.value through verbatim so profile-shaped responses
        # (dicts/lists/numbers) survive to the LLM.  Coerce only the
        # legacy empty-string default -- never stringify dicts.
        value: Any = result.value if result.value not in (None, "") else ""

        # Deliver async events exactly once.  Lines that arrived between
        # MCP calls (or were drained by _dispatch_via_profile pre-send)
        # accumulated on self._async_events.  Snapshot + clear here so
        # the LLM sees them in this response and the next response
        # starts with an empty buffer.  device_state.json keeps the
        # cumulative view for callers who want the full history.
        pending_async = list(self._async_events)
        self._async_events.clear()

        # If value is a self-describing envelope (carries cmd/success/
        # error/elapsed_s itself, as /term.request produces), don't
        # duplicate those keys at the outer wire level -- the model
        # already has them in value.  Plain commands (whose value is
        # raw data, not a full envelope) get the standard outer wrap
        # so cmd/success/error/elapsed_s are still discoverable.
        is_self_describing = (
            isinstance(value, dict)
            and "cmd" in value
            and "success" in value
            and "error" in value
        )
        if is_self_describing:
            return {
                "value": value,
                "output_lines": output_lines,
                "captured_artifacts": artifacts,
                "async_events": pending_async,
            }
        return {
            "cmd": command,
            "success": bool(result.success),
            "error": result.error or "",
            "value": value,
            "elapsed_s": float(result.elapsed_s or 0.0),
            "output_lines": output_lines,
            "captured_artifacts": artifacts,
            "async_events": pending_async,
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

    # SIGINT/SIGTERM handlers translate to KeyboardInterrupt so FastMCP's
    # stdio loop unwinds cleanly through the finally block.  On Windows
    # only SIGINT is supported by signal.signal; SIGTERM is best-effort
    # and silently no-ops there.  KeyboardInterrupt is the cleanest
    # signal for asyncio to unwind.
    import signal as _signal

    def _term_handler(signum: int, _frame: Any) -> None:
        host._log_line(f"! signal {signum} received; shutting down")
        raise KeyboardInterrupt

    _orig_handlers: dict[int, Any] = {}
    for sig in (_signal.SIGINT, getattr(_signal, "SIGTERM", None)):
        if sig is None:
            continue
        try:
            _orig_handlers[sig] = _signal.signal(sig, _term_handler)
        except (ValueError, OSError):  # noqa: PERF203 - small set
            # Not in main thread on Windows, etc.  Skip.
            pass

    try:
        # FastMCP.run() blocks until the stdio peer disconnects.  No
        # other transports -- if you want HTTP, that's deferred to v2
        # (see plan: "TUI + MCP simultaneously via HTTP transport").
        server.run(transport="stdio")
    except KeyboardInterrupt:
        # Clean shutdown: fall through to finally.
        pass
    finally:
        # Restore signal handlers so we don't leave the process in
        # an unexpected state if termapy is invoked from a parent
        # that owns SIGINT.
        for sig, old in _orig_handlers.items():
            try:
                _signal.signal(sig, old)
            except (ValueError, OSError):
                pass
        host.repl.fire_lifecycle("on_app_stop")
        if host.engine.is_connected:
            host._disconnect()
