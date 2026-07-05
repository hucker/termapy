"""REPL engine for termapy - plugin-based command dispatch and scripting.

All commands (built-in and external) are plugins loaded as .py files.
Built-in plugins ship in termapy/builtins/. External plugins are loaded
from folders by app.py. The engine owns state (seq counters, echo, etc.)
and exposes it through PluginContext lambdas.
"""

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from io import TextIOWrapper
from pathlib import Path
from threading import Event
from types import MappingProxyType
from typing import Callable

from termapy.defaults import DEFAULT_CMD_PREFIX
from termapy.folders import CAP, PROF, PROTO, RUN, SS
from termapy.plugins import (
    LEVEL_FLAGS,
    OUTPUT_LEVELS,
    BoundaryException,
    CapabilitySet,
    CmdResult,
    DirectiveInfo,
    DirectiveResult,
    LifecycleHook,
    LongHelp,
    MissingCapability,
    PluginContext,
    PluginInfo,
    TransformInfo,
    builtins_dir,
    load_plugins_from_dir,
)
from termapy.plugins.params import parse_params, synthesize_synopsis
from termapy.scripting import (
    expand_template,
    format_duration,
    parse_duration,
    parse_keywords,
)


def _resolve_flag(raw: str, declared: dict[str, str]) -> str | None:
    """Resolve a raw flag token (``-t``, ``--table``) to its canonical name.

    Aliases in ``declared`` point at their canonical form as a string value;
    canonical entries have a description string as the value. Either way, the
    resolver follows one alias hop (no chaining) and returns the canonical
    flag name, or ``None`` if the raw token isn't a declared flag.
    """
    if raw not in declared:
        return None
    val = declared[raw]
    # Alias: value is another flag name (starts with '-' and exists in the dict).
    if val.startswith("-") and val in declared:
        return val
    return raw


def _parse_flags(
    args: str,
    declared: dict[str, str],
) -> tuple[str, set[str], str | None]:
    """Strip declared flags from ``args`` and return the normalized result.

    Tokenizes ``args`` on whitespace, extracts any token matching a declared
    flag (including aliases), and returns the remaining args joined with
    single spaces. Unknown ``-x`` / ``--xxx`` tokens produce an error.

    Tokens after a ``--`` separator are treated as literal and never parsed
    as flags. Equal-sign forms (``--foo=bar``) are not supported: every
    declared flag is boolean. This keeps the grammar trivial and matches
    every current use site.

    Args:
        args: Raw argument string after the command name.
        declared: ``Command.flags`` - maps flag names to descriptions
            (canonical) or to another flag name (alias).

    Returns:
        ``(remaining_args, active_flags, error)``. On success, ``error``
        is None and the set contains canonical flag names. On failure,
        ``error`` is a user-facing message and the other fields are empty.
    """
    if not declared:
        return args, set(), None

    tokens = args.split()
    remaining: list[str] = []
    active: set[str] = set()
    literal_rest = False
    for tok in tokens:
        if literal_rest:
            remaining.append(tok)
            continue
        if tok == "--":
            literal_rest = True
            continue
        # Only tokens that *look* like flags participate in flag parsing.
        # Positional args like "file.txt" or "0x01" pass through unchanged.
        if tok.startswith("-") and len(tok) > 1 and not tok[1].isdigit():
            canonical = _resolve_flag(tok, declared)
            if canonical is None:
                # Typo path: suggest a near match from the declared set.
                candidates = sorted(declared.keys())
                suggestion = _closest_flag(tok, candidates)
                hint = f" -- did you mean {suggestion}?" if suggestion else ""
                return "", set(), f"Unknown flag: {tok}{hint}"
            active.add(canonical)
            continue
        remaining.append(tok)
    return " ".join(remaining), active, None


def _strip_level_flags(args: str) -> tuple[str, str | None]:
    """Strip universal level flags (``--silent`` etc.) from args.

    Level flags are accepted by every command, parsed by the dispatcher
    before per-command flag parsing.  Returns ``(remaining_args, level)``
    where ``level`` is the canonical name or None if no level flag was
    seen.  Conflicting level flags on the same call use the last-wins
    rule (matches argparse).
    """
    if not args:
        return args, None
    tokens = args.split()
    remaining: list[str] = []
    level: str | None = None
    for tok in tokens:
        if tok in LEVEL_FLAGS:
            level = LEVEL_FLAGS[tok]
        else:
            remaining.append(tok)
    return " ".join(remaining), level


def _closest_flag(needle: str, candidates: list[str]) -> str | None:
    """Return the nearest declared flag by edit distance, or None."""
    best: tuple[int, str] | None = None
    for candidate in candidates:
        d = _edit_distance(needle, candidate)
        if d <= 2 and (best is None or d < best[0]):
            best = (d, candidate)
    return best[1] if best else None


def _suggest_command(
    name: str, plugins: dict, prefix: str = DEFAULT_CMD_PREFIX
) -> str | None:
    """Find close command names using edit distance (max 2, top 3)."""
    candidates = []
    for cmd in plugins:
        d = _edit_distance(name, cmd)
        if d <= 2:
            candidates.append((d, cmd))
    if not candidates:
        return None
    candidates.sort()
    top = [cmd for _, cmd in candidates[:3]]
    return ", ".join(f"{prefix}{c}" for c in top)


def _edit_distance(a: str, b: str) -> int:
    """Damerau-Levenshtein distance (transpositions count as 1 edit)."""
    la, lb = len(a), len(b)
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(
                d[i - 1][j] + 1,
                d[i][j - 1] + 1,
                d[i - 1][j - 1] + cost,
            )
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[la][lb]


@dataclass
class ScriptCtx:
    """Shared state for script execution."""

    w: Callable
    dispatch_fn: Callable | None
    prefix: str
    profile: bool
    verbose: bool
    progress: Callable | None
    on_nest: Callable | None
    delay_progress: Callable | None = None
    lines: list = field(default_factory=list)
    step: int = 0
    total: int = 0
    profile_times: list = field(default_factory=list)
    prof_fh: TextIOWrapper | None = None
    script_t0: float = 0.0
    prof_name: str = ""
    prof_path: Path | None = None

    def record(self, label: str, elapsed: float) -> None:
        """Record timing for profile and verbose output."""
        if self.verbose:
            self.w(f"[{self.step}/{self.total}] {label} ({format_duration(elapsed)})")
        if self.profile and self.prof_fh:
            self.profile_times.append((label, elapsed))
            # Raw seconds -- the .prof file is a data format, not display.
            self.prof_fh.write(f"{elapsed:.6f},{label}\n")

    def finish(self, script_name: str) -> None:
        """Display summary after successful script completion."""
        if self.verbose:
            elapsed = time.perf_counter() - self.script_t0
            self.w(f"Script {script_name} done ({format_duration(elapsed)})")
        if self.profile and self.profile_times and self.prof_fh and self.prof_path:
            total_t = sum(t for _, t in self.profile_times)
            self.prof_fh.flush()
            for line in self.prof_path.read_text(encoding="utf-8").splitlines():
                self.w(line)
            self.w(
                f"── {format_duration(total_t)} total "
                f"({len(self.profile_times)} commands) -> {self.prof_name} ──"
            )


class ReplEngine:
    """Plugin-based REPL command engine."""

    def __init__(
        self,
        cfg: dict,
        config_path: str,
        write: Callable,
        prefix: str = DEFAULT_CMD_PREFIX,
    ) -> None:
        """Initialize the REPL engine with config and plugin loading.

        Args:
            cfg: Config dict (owned by the engine, wrapped in MappingProxyType).
            config_path: Path to the JSON config file on disk.
            write: Callback for output - write(text, color="dim").
            prefix: REPL command prefix (default "/").
        """
        self._cfg_data = cfg
        self.cfg = MappingProxyType(self._cfg_data)
        self.config_path = config_path
        self.write = write  # write(text, color="dim") callback
        self.prefix = prefix
        self.cmd = lambda name: f"{prefix}{name}"
        self._script_depth: int = 0
        self._script_stack: list[str] = []  # stack of script names
        self._script_stop = Event()
        self._max_script_depth: int = 5
        # Expect watcher - predicate set by wait_for_match(), checked by feed_lines()
        self._expect_predicate: Callable[[str], bool] | None = None
        self._expect_event = Event()
        self._expect_matched_line: str = ""
        # Ring buffer of recent serial lines (ANSI-stripped).
        # Solves the race where a device responds before /expect sets the
        # predicate: wait_for_match() sets the predicate FIRST, then scans
        # this buffer for retroactive matches. feed_lines() always appends
        # here regardless of whether a predicate is active.
        # deque with maxlen is thread-safe for append/iterate in CPython.
        from collections import deque

        self._recent_lines: deque[str] = deque(maxlen=100)

        # Plugin context - set by app.py after mount via set_context()
        # Bootstrap with a minimal IOHandle wired to the constructor's
        # write callable; the host replaces this whole ctx via
        # ``set_context()`` once it builds the real one.
        from termapy.plugins.handles.io import IOHandle
        self.ctx = PluginContext(io=IOHandle(_write=write))

        # Unified plugin registry - all commands live here
        self._plugins: dict[str, PluginInfo] = {}

        # Config change callback (set by app.py)
        self._after_cfg = None  # callback: (key, new_val) -> None (post-apply refresh)

        # Transform chains - populated during plugin/transform registration
        self._repl_transforms: list[Callable] = []
        self._serial_transforms: list[Callable] = []
        self._transform_infos: list[TransformInfo] = []

        # Directive chain - pre-dispatch line rewriters
        self._directives: list[DirectiveInfo] = []

        # Post-dispatch observers - fired with (line, result) after every
        # dispatch() call, including failed dispatches.  Consumers:
        # /run.record (captures successful lines to a .run file) and
        # any future audit / repeat-last / event-stream feature.
        # Observer exceptions are caught so a buggy subscriber can't
        # break dispatch.
        self._post_dispatch_observers: list[
            Callable[[str, CmdResult], None]
        ] = []

        # Lifecycle hooks - flat list in load order. fire_lifecycle() filters
        # by name. See plugins.LIFECYCLE_HOOK_NAMES for supported hooks.
        self._lifecycle_hooks: list[LifecycleHook] = []

        # Load built-in plugins from termapy/builtins/
        self._load_builtins()

    def _load_builtins(self) -> None:
        """Load built-in command plugins from the builtins/ package directory."""
        result = load_plugins_from_dir(builtins_dir(), "built-in")
        for info in result.plugins:
            self.register_plugin(info)
        for xform in result.transforms:
            self.register_transform(xform)
        for directive in result.directives:
            self.register_directive(directive)
        for hook in result.lifecycle_hooks:
            self.register_lifecycle_hook(hook)
        self._register_script_commands()
        self._register_legacy_forwarders()

    def _register_legacy_forwarders(self) -> None:
        """Register the hidden legacy command aliases centrally.

        Old top-level commands that moved to a namespace (``/echo`` ->
        ``/term.echo``, ``/line_no`` -> ``/term.line_no``,
        ``/show_line_endings`` -> ``/term.line_endings``, ``/verbose`` ->
        ``/term.output``) used to be one plugin file each.  They now live
        in ``legacy.LEGACY_FORWARDERS`` and register here, once per
        engine, so every frontend (TUI/CLI/MCP) gets them without four
        extra files in ``builtins/commands/``.
        """
        from termapy.legacy import LEGACY_FORWARDERS

        for fwd in LEGACY_FORWARDERS:
            self.register_plugin(
                PluginInfo(
                    name=fwd.name,
                    args=fwd.args,
                    help=fwd.help,
                    handler=fwd.handler,
                    needs=fwd.needs,
                    hidden=True,
                )
            )

    def _register_script_commands(self) -> None:
        """Register the blocking commands as first-class plugins.

        ``/expect`` and ``/expect.regex`` are dispatched two ways:

        - **From a .run script**: the script runner's ``_BLOCKING_COMMANDS``
          dispatch (see ``_run_line``) intercepts BEFORE ``dispatch()`` is
          reached, calling ``_script_expect`` which can write progress to
          the script's ``sctx`` and stop the whole script on timeout.
        - **From dispatch (REPL prompt or MCP)**: ``_expect_handler``
          below runs.  Same wait, simpler return shape -- no script
          state, just ``CmdResult`` with the matched line in ``value``.

        Both paths require ``block_until=True``: scripts have it
        dynamically; MCP hosts opt in by setting it on
        ``ctx.capabilities``; the interactive REPL doesn't have it,
        so the capability gate keeps ``/expect`` script-only there.

        ``/confirm`` is a regular plugin (see ``confirm.py``) that
        declares its own ``needs``; no registration here.
        """
        from termapy.scripting import parse_duration, parse_keywords

        def _make_expect_handler(use_regex: bool) -> Callable:
            def _expect_handler(ctx: PluginContext, args: str) -> CmdResult:
                kw = parse_keywords(
                    args, {"timeout", "match"}, rest_keyword="match"
                )
                pattern = kw.get("match", "").strip()
                if not pattern:
                    return CmdResult.fail(msg="Expect: missing match= keyword")
                try:
                    timeout_s = (
                        parse_duration(kw["timeout"]) if "timeout" in kw else 5.0
                    )
                except ValueError as e:
                    return CmdResult.fail(msg=f"Expect: {e}")
                if use_regex:
                    import re as _re

                    try:
                        compiled = _re.compile(pattern)
                    except _re.error as e:
                        return CmdResult.fail(msg=f"Expect: invalid regex: {e}")

                    def predicate(line: str) -> bool:
                        return bool(compiled.search(line))
                else:

                    def predicate(line: str) -> bool:
                        return pattern in line

                match = ctx.wait_for_match(predicate, timeout=timeout_s)
                if match is None:
                    return CmdResult.fail(
                        msg=f'Expect "{pattern}" timeout after {format_duration(timeout_s)}'
                    )
                return CmdResult.ok(value=match)

            return _expect_handler

        self.register_plugin(
            PluginInfo(
                name="expect",
                args="{timeout=<dur>} match=<text>",
                help="Wait for text in serial output (blocks until match or timeout).",
                long_help=(
                    "Block until the device outputs a line containing <text>.\n"
                    "Returns the matched line as the result value; fails on\n"
                    "timeout (default 5s).\n"
                    "\n"
                    "Available wherever ``block_until`` is provided: inside\n"
                    "a .run script (always), and from the MCP server (the\n"
                    "MCPHost opts in).  The interactive REPL prompt does\n"
                    "not provide it -- type {prefix}expect there and you'll\n"
                    "get a clear capability error.\n"
                    "\n"
                    "**match= must be the LAST keyword** because it consumes\n"
                    "to end of line (text can contain spaces).\n"
                    "\n"
                    "Example:\n"
                    "  AT+CONNECT\n"
                    "  {prefix}expect timeout=5s match=CONNECTED"
                ),
                handler=_make_expect_handler(False),
                needs=CapabilitySet(block_until=True),
                raw_args=True,
            )
        )
        self.register_plugin(
            PluginInfo(
                name="expect.regex",
                args="{timeout=<dur>} match=<pattern>",
                help="Wait for regex match in serial output (blocks until match or timeout).",
                long_help=(
                    "Block until the device outputs a line matching <pattern>.\n"
                    "<pattern> is a Python regex.  See {prefix}expect for the\n"
                    "non-regex variant and the capability gate that decides\n"
                    "where this command runs.\n"
                    "\n"
                    "**match= must be the LAST keyword.**\n"
                    "\n"
                    "Example:\n"
                    "  AT+STATUS\n"
                    "  {prefix}expect.regex timeout=2s match=^\\+STATUS: \\d+$"
                ),
                handler=_make_expect_handler(True),
                needs=CapabilitySet(block_until=True),
                raw_args=True,
            )
        )

    # -- Expect / pattern matching ---------------------------------------------

    def wait_for_match(
        self,
        predicate: Callable[[str], bool],
        timeout: float = 5.0,
    ) -> str | None:
        """Block until a serial line matches predicate or timeout expires.

        Must be called from a background thread. Serial data continues
        to display normally - feed_lines() checks the predicate as lines
        arrive.

        Race-condition safety: the predicate is installed BEFORE scanning
        the recent-lines buffer. This eliminates the gap where a line
        could arrive after the buffer check but before the predicate is
        active - feed_lines() would catch it in that window.

        Args:
            predicate: Callable that takes a stripped line and returns True
                on match.
            timeout: Seconds to wait before giving up.

        Returns:
            The matched line, or None on timeout.
        """
        seconds = timeout
        self._expect_event.clear()
        self._expect_matched_line = ""
        # Install predicate FIRST so feed_lines() catches new arrivals
        self._expect_predicate = predicate
        # Now scan the buffer for lines that already arrived
        for line in list(self._recent_lines):
            if predicate(line):
                self._expect_matched_line = line
                self._expect_predicate = None
                return line
        try:
            self._expect_event.wait(timeout=seconds)
        finally:
            self._expect_predicate = None
        if self._expect_event.is_set():
            return self._expect_matched_line
        return None

    def drain_recent_lines(self) -> list[str]:
        """Snapshot and clear the recent-lines ring buffer.

        Returns the lines that had been buffered for retroactive
        wait_for_match() scans.  Callers (e.g. profile-aware request/
        response in the MCP host) drain before sending so the next
        wait cycle starts from a clean slate -- stale lines are
        archived as async events instead of leaking into the next
        response parse.
        """
        snapshot = list(self._recent_lines)
        self._recent_lines.clear()
        return snapshot

    def wait_for_lines(
        self,
        timeout: float,
        *,
        terminator: str = "",
        idle_gap: float = 0.1,
    ) -> list[str]:
        """Collect serial lines until terminator, idle gap, or timeout.

        For multi-line profile responses (``response.format == "lines"``).
        Lines that arrive after this call starts are accumulated; if
        ``terminator`` is a non-empty regex, collection stops on the
        first matching line (the terminator line is NOT included).
        Otherwise collection stops when no new line arrives for
        ``idle_gap`` seconds, or when ``timeout`` elapses overall.

        Drains and returns whatever was already in the recent-lines
        buffer first -- callers that want a clean slate should call
        ``drain_recent_lines()`` before sending and archive those
        lines elsewhere (see MCPHost._archive_stale_lines).

        Must be called from a background thread (same constraint as
        ``wait_for_match``).

        Args:
            timeout: Hard deadline in seconds.
            terminator: Optional regex; first matching line ends collection.
            idle_gap: Seconds without new lines before stopping (when no
                terminator matches).  Smaller = snappier but riskier on
                slow devices.  Default 100ms.

        Returns:
            List of lines collected (excluding the terminator line, if any).
        """
        import re as _re

        term_re = None
        if terminator:
            try:
                term_re = _re.compile(terminator)
            except _re.error:
                term_re = None

        collected: list[str] = []
        # Pull anything already buffered; this is the "drain" half.
        for line in list(self._recent_lines):
            if term_re is not None and term_re.search(line):
                self._recent_lines.clear()
                return collected
            collected.append(line)
        self._recent_lines.clear()

        deadline = time.monotonic() + timeout
        last_arrival = time.monotonic()
        while time.monotonic() < deadline:
            new_lines = list(self._recent_lines)
            if new_lines:
                self._recent_lines.clear()
                last_arrival = time.monotonic()
                for line in new_lines:
                    if term_re is not None and term_re.search(line):
                        return collected
                    collected.append(line)
            elif collected and (time.monotonic() - last_arrival) >= idle_gap:
                # Idle: nothing new for idle_gap seconds AND we have content.
                # No-content idle still waits the full timeout so slow
                # devices get a chance to start replying.
                return collected
            else:
                time.sleep(0.01)
        return collected

    def _exec_request_mode(self, command: str) -> CmdResult:
        """Send a bare device command and return its response as JSON.

        Used when ``cfg["request_mode"]`` is true -- ``/term.request on``.
        The rule is dead simple:

        - JSON-native device responds with JSON  → ``value`` is the
          parsed JSON, passed through directly.
        - Text device responds with plain text   → ``value`` is
          ``{"result": "<stripped text>"}``.

        That's it.  No duplicated ``cmd/success/error/elapsed_s`` --
        those live in the outer ``CmdResult`` (and the outer MCP
        envelope when running ``--mcp``).  ``elapsed_s`` is on
        ``CmdResult`` itself, accessible to all callers.

        Input is symmetric: a JSON object with a string ``cmd`` field
        is unwrapped, so callers can send either plain text
        (``get_voltage``) or JSON ({"cmd":"get_voltage"}).  Malformed
        JSON or JSON without a ``cmd`` key falls back to plain-text
        send; only an explicit ``cmd`` field with a non-string/empty
        value hard-errors.

        In MCP mode the profile executor runs upstream of this hook
        (``mcp/server.py:_dispatch_via_profile``) and short-circuits
        for profile-mapped commands.  In TUI/CLI there's no executor
        on this path, so request_mode applies to every bare command.
        """
        import json as _json
        import time as _time

        # MCP envelope-duplication gate: in MCP the model already has
        # the full envelope in ``CmdResult.value`` (lifted into the
        # response's ``value`` field by run_command_async).  Rendering
        # the same envelope through the output channel would put it
        # into ``output_lines`` too -- the exact duplication we set out
        # to avoid.  Computed once and reused below.
        from termapy.builtins.commands.var import _LAUNCH_VARS as _lvars
        _is_mcp = _lvars.get("FRONT_END") == "mcp"

        # Symmetric input: unwrap {"cmd": "..."} into the cmd value.
        cmd_text = command.strip()
        if cmd_text.startswith("{") and cmd_text.endswith("}"):
            try:
                parsed = _json.loads(cmd_text)
            except (ValueError, _json.JSONDecodeError):
                parsed = None
            if isinstance(parsed, dict) and "cmd" in parsed:
                cmd_value = parsed["cmd"]
                if isinstance(cmd_value, str) and cmd_value:
                    command = cmd_value
                else:
                    err_msg = (
                        'Invalid JSON input: "cmd" must be a non-empty string'
                    )
                    envelope = {
                        "cmd": cmd_text,
                        "success": False,
                        "error": err_msg,
                        "elapsed_s": 0.0,
                        "result": "",
                    }
                    if not _is_mcp:
                        self.ctx.io.result_markup(_json.dumps(envelope))
                    return CmdResult.fail(msg=err_msg, value=envelope)

        # Symmetric request-side echo: render the canonical post-unwrap
        # form so TUI/CLI scrollback shows what was sent.  Routed
        # through output_markup so it gates at normal+ (silent and
        # quiet suppress it), and MCP's session default of "quiet"
        # naturally keeps output_lines clean -- no FRONT_END gate
        # needed for this one.
        self.ctx.io.output_markup(_json.dumps({"cmd": command}))

        encoding = self.cfg.get("encoding", "utf-8")
        line_ending = self.cfg.get("line_ending", "\r")
        timeout_ms = int(self.cfg.get("response_timeout_ms", 1000))
        payload = (command + line_ending).encode(encoding)

        t0 = _time.perf_counter()
        error = ""
        text = ""
        try:
            with self.ctx.serial.io():
                self.ctx.serial.drain()
                self.ctx.serial.write(payload)
                response = self.ctx.serial.read_raw(timeout_ms=timeout_ms)
            text = response.decode(encoding, errors="replace").strip()
        except (OSError, Exception) as exc:  # noqa: BLE001 -- serial boundary
            error = f"Send error: {exc}"
        elapsed = _time.perf_counter() - t0

        # Detect device-side errors via the configured regex.  ``error``
        # is already populated for send failures (caught above); only
        # check the pattern when the send succeeded and we got text.
        #
        # Resolution order:
        #   1. Session override from ctx.ns("flags") (set by
        #      ``/term.request err=...``).  Wins when present, including
        #      "" which explicitly disables detection.
        #   2. cfg.request_err_pattern (persistent default).
        #
        # ``/term.request on`` (no err=) clears the session override, so
        # the cycle off -> on always re-enables cfg-default detection.
        if not error and text:
            flags = self.ctx.ns("flags")
            if "request_err_pattern_override" in flags:
                pattern = flags["request_err_pattern_override"]
            else:
                pattern = self.cfg.get("request_err_pattern", "")
            if pattern:
                import re as _re

                try:
                    if _re.search(pattern, text):
                        error = text
                except _re.error:
                    # Malformed regex: don't crash the dispatch.  Surface
                    # it through ``output`` so the user sees their cfg
                    # mistake at normal+; silent/quiet still suppress.
                    self.ctx.io.output(
                        f"request_err_pattern regex error: {pattern!r}",
                        "yellow",
                    )

        # ONE envelope is the canonical /term.request shape, returned
        # in CmdResult.value.  In TUI/CLI it's ALSO rendered through
        # ``result_markup`` for scrollback visibility -- it IS the
        # command's answer, gated at quiet+ so --silent suppresses it.
        # In MCP the model already has the envelope in value (lifted
        # by run_command_async); rendering it here too would put the
        # same JSON into ``output_lines``, the duplication we set out
        # to avoid.  _is_mcp was computed at the top of this function.
        success = not error
        envelope = {
            "cmd": command,
            "success": success,
            "error": error,
            "elapsed_s": round(elapsed, 4),
            # When a device error was detected, the text *is* the error
            # message.  Keep result empty so the reader (human or model)
            # doesn't see the same string in two fields.
            "result": "" if error else text,
        }
        if not _is_mcp:
            self.ctx.io.result_markup(_json.dumps(envelope))

        if error:
            result = CmdResult.fail(msg=error, value=envelope)
        else:
            result = CmdResult.ok(value=envelope)
        result.elapsed_s = elapsed
        return result

    def feed_lines(self, lines: list[str]) -> None:
        """Feed serial output lines to the expect watcher.

        Called from the serial display path (app._write_batch).
        Always appends to the recent-lines ring buffer so that
        wait_for_match() can retroactively scan lines that arrived
        before the predicate was set. If a predicate is active,
        each line is also tested for an immediate match.
        """
        from termapy.scripting import strip_ansi

        for line in lines:
            clean = strip_ansi(line)
            # Always buffer - wait_for_match() scans this retroactively
            self._recent_lines.append(clean)
            # Live match if a predicate is active
            predicate = self._expect_predicate
            if predicate is not None and predicate(clean):
                self._expect_matched_line = clean
                self._expect_event.set()
                return

    # -- Plugin management ----------------------------------------------------

    def set_context(self, ctx: PluginContext) -> None:
        """Set the plugin context (called by app.py after mount)."""
        self.ctx = ctx

    def register_plugin(self, info: PluginInfo) -> None:
        """Register a plugin. Replaces any existing plugin with the same name."""
        self._plugins[info.name] = info

    def register_transform(self, info: TransformInfo) -> None:
        """Register an input transform. Appended in load order."""
        self._transform_infos.append(info)
        if info.repl:
            self._repl_transforms.append(info.repl)
        if info.serial:
            self._serial_transforms.append(info.serial)

    def register_directive(self, info: DirectiveInfo) -> None:
        """Register a pre-dispatch directive. Appended in load order."""
        self._directives.append(info)

    def register_lifecycle_hook(self, hook: LifecycleHook) -> None:
        """Register a plugin lifecycle hook. Appended in load order."""
        self._lifecycle_hooks.append(hook)

    def fire_lifecycle(self, name: str) -> None:
        """Fire every registered lifecycle hook matching *name* in load order.

        Exceptions are caught per-hook so one bad plugin cannot prevent
        later hooks from running.  Errors surface through ``ctx.io.status``
        so they are visible without crashing the app.

        Args:
            name: Hook name (must be in ``LIFECYCLE_HOOK_NAMES``).
        """
        for hook in self._lifecycle_hooks:
            if hook.name != name:
                continue
            # Plugin hook handlers are third-party code and can raise
            # anything.  BoundaryException signals the reviewed broad
            # catch; the error goes to status so a broken plugin can't
            # take down the whole lifecycle pass.
            try:
                hook.handler(self.ctx)
            except BoundaryException as e:
                self.ctx.io.status(f"Lifecycle hook {hook.plugin}:{name} failed: {e}")

    def run_directives(self, line: str) -> DirectiveResult:
        """Run all directives in load order against a raw input line.

        Returns the first non-None DirectiveResult, or a "none" result
        if no directive matched.
        """
        for d in self._directives:
            if d.handler:
                result = d.handler(line)
                if result is not None:
                    return result
        return DirectiveResult()

    def register_hook(
        self,
        name: str,
        args: str,
        help_text: str,
        handler: Callable,
        source: str = "built-in",
        long_help: LongHelp = "",
        raw_args: bool = False,
        flags: dict[str, str] | None = None,
        needs: CapabilitySet | None = None,
        hidden: bool = False,
    ) -> None:
        """Register an app-coupled command as a plugin.

        Bridge for commands that need Textual access (screenshots, connect,
        etc.). The handler receives (ctx, args) like any plugin. If *name*
        is dotted (e.g. ``"ss.svg"``), the parent's children list is updated
        automatically.

        Args:
            name: Command name (e.g. ``"connect"`` or ``"ss.svg"``).
            args: Argument spec string for help display.
            help_text: One-line description for /help output.
            handler: Callable(ctx, args) invoked when the command runs.
            source: Label for origin (default "built-in").
            long_help: Extended help for ``/help <cmd>`` (default "").
                May be a string or a callable ``(PluginContext) -> str``
                for dynamic DESCRIPTION rendering. See ``resolve_long_help``.
            raw_args: Skip REPL transforms for this command (default False).
            flags: First-class flag declarations (see ``Command.flags``).
            needs: Environment capabilities the handler requires
                (see ``CapabilitySet``).
        """
        # Tree override: remove all children of this command from plugins.
        # When a hook takes ownership of a command, it owns the full subtree.
        prefix = name + "."
        children_to_remove = [n for n in self._plugins if n.startswith(prefix)]
        for child in children_to_remove:
            del self._plugins[child]
        # Also clean up the old entry's children list (if it existed)
        old = self._plugins.get(name)
        if old and old.children:
            old.children.clear()

        self._plugins[name] = PluginInfo(
            name=name,
            args=args,
            help=help_text,
            handler=handler,
            long_help=long_help,
            source=source,
            raw_args=raw_args,
            flags=dict(flags) if flags else {},
            needs=needs if needs is not None else CapabilitySet(),
            hidden=hidden,
        )
        # Auto-update parent's children list for dotted names
        if "." in name:
            parent_name = name.rsplit(".", 1)[0]
            parent = self._plugins.get(parent_name)
            if parent and name not in parent.children:
                parent.children.append(name)

    def command_has_raw_args(self, repl_cmd: str) -> bool:
        """Check if the first command token has ``raw_args`` set.

        Called before transforms to decide whether to skip expansion.

        Args:
            repl_cmd: REPL command string (prefix already stripped).

        Returns:
            True if the command exists and has ``raw_args=True``.
        """
        name = repl_cmd.split(None, 1)[0].lower() if repl_cmd.strip() else ""
        plugin = self._plugins.get(name)
        return plugin.raw_args if plugin else False

    def _validate_bare_typed_args(
        self, cmd: str, _status: Callable[[str, str], None],
    ) -> CmdResult | None:
        """Validate bound typed_args for a bare device command.

        Returns None to pass through (no profile match, no typed_args,
        all values valid, or no profile loaded).  Returns a
        ``CmdResult.fail`` when a value fails validation; the caller
        short-circuits and does not write bytes to the wire.

        Surfaces failures via the ``_status`` callback so the typist
        sees the error in the terminal exactly as if the device had
        replied with one.
        """
        profile = self.ctx.ns("active_profile")
        if not isinstance(profile, dict) or not profile:
            return None
        commands = profile.get("commands") or {}
        if not isinstance(commands, dict) or not commands:
            return None

        from termapy.profile import TypeRegistry, match_profile_command

        match = match_profile_command(cmd, commands)
        if match is None:
            return None
        name, spec, bound = match
        typed_args = spec.get("typed_args") or []
        if not typed_args or not bound:
            return None

        registry = TypeRegistry.from_profile(profile)
        for ta in typed_args:
            if not isinstance(ta, dict):
                continue
            arg_name = ta.get("name")
            arg_type = ta.get("type")
            if not arg_name or not arg_type or arg_name not in bound:
                continue
            outcome = registry.validate(arg_type, bound[arg_name])
            if not outcome.ok:
                msg = (
                    f"Arg {arg_name!r} invalid for {name!r}: {outcome.error}"
                )
                _status(msg, "red")
                return CmdResult.fail(msg=msg)
        return None

    # -- Full dispatch pipeline ------------------------------------------------

    def dispatch_full(
        self,
        cmd: str,
        *,
        log: Callable[[str, str], None] | None = None,
        echo_markup: Callable[[str], None] | None = None,
        status: Callable[[str, str], None] | None = None,
        serial_write: Callable[[bytes], None] | None = None,
        serial_write_raw: Callable[[str], None] | None = None,
        is_connected: Callable[[], bool] | None = None,
        eol_label: Callable[[str], str] | None = None,
    ) -> CmdResult:
        """Route a raw command through the full pipeline.

        Decides: /raw bypass -> directives -> REPL command -> serial command.
        Applies transforms and sends via callbacks. This is the testable
        core that app.py's ``_dispatch_single`` delegates to.

        Args:
            cmd: Raw command string (may have REPL prefix).
            log: Log callback - log(direction, text).
            echo_markup: Display markup text on screen.
            status: Show status message - status(text, color).
            serial_write: Send encoded bytes to serial port.
            serial_write_raw: Send raw text to serial (no transforms).
            is_connected: Returns True if serial port is open.
            eol_label: Format a line ending string for display.

        Returns:
            CmdResult with success/error status and elapsed time.
        """
        prefix = self.prefix
        _log = log or (lambda _d, _t: None)
        _echo = echo_markup or (lambda _t: None)
        _status = status or (lambda _t, _c: None)
        echo_on = self.ctx.ns("flags")["echo"]

        # 1. /raw bypass - no transforms, no directives
        if cmd.startswith(prefix + "raw "):
            raw_text = cmd[len(prefix) + 4 :]
            _log(">", cmd)
            if echo_on:
                _echo(f"[cyan]> {cmd}[/]")
            if serial_write_raw:
                serial_write_raw(raw_text)
            # Internal raw-passthrough: the bytes are gone on the wire
            # before this returns; no scriptable value.
            return CmdResult.ok(value="")

        # 2. Pre-dispatch directives (e.g. $(VAR) = value -> /var.set)
        result = self.run_directives(cmd)
        if result.action == "rewrite":
            _log(">", cmd)
            if echo_on:
                _echo(f"[cyan]> {cmd}[/]")
            return self.dispatch(result.payload)
        if result.action == "warn":
            _log(">", cmd)
            if echo_on:
                _echo(f"[cyan]> {cmd}[/]")
            _status(f"Warning: {result.payload}", "yellow")
            # Internal: warnings come back without a value to capture.
            return CmdResult.ok(value="")
        if result.action == "error":
            _log(">", cmd)
            if echo_on:
                _echo(f"[cyan]> {cmd}[/]")
            _status(f"Error: {result.payload}", "red")
            return CmdResult.fail(msg=result.payload)

        # Shared echo using echo_input_fmt for both REPL and serial
        from termapy.builtins.commands.var import expand_vars

        def _echo_cmd(text: str) -> None:
            fmt = expand_vars(self.cfg.get("echo_input_fmt", "> {cmd}"))
            _echo(fmt.replace("{cmd}", text))

        # 3. REPL command (starts with prefix)
        if cmd.startswith(prefix):
            repl_cmd = cmd[len(prefix) :].strip()
            _log(">", f"{prefix}{repl_cmd}")
            # When the user types just the prefix character ("/"), repl_cmd
            # is "" and ``repl_cmd.split()[0]`` would IndexError.  Guard
            # against that: an empty command has no first token to inspect
            # for ".silent", so just echo unconditionally.
            first_word = repl_cmd.split()[0] if repl_cmd.split() else ""
            if echo_on and ".silent" not in first_word:
                _echo_cmd(f"{prefix}{repl_cmd}")
            if self.has_repl_transforms:
                if not self.command_has_raw_args(repl_cmd):
                    try:
                        repl_cmd = self.transform_repl(repl_cmd)
                    except ValueError as e:
                        _status(str(e), "red")
                        return CmdResult.fail(msg=str(e))
            return self.dispatch(repl_cmd)

        # 4. Non-prefix line -> rewrite to /term.send <text> and dispatch.
        #    Goal: every dispatched action has a discoverable slash-command
        #    name (/help, /search, MCP catalog).  /term.send is the
        #    literal-bytes primitive; transforms + connect-check live in
        #    this fallthrough only -- /term.send (called directly) gets
        #    predictable literal-bytes semantics for LLMs and scripts.
        #
        # Strip universal level flags (--silent/--quiet/--normal/--verbose)
        # BEFORE transforms/serial-send so the flag never reaches the
        # device.  The prefix path strips inside dispatch(); the bare path
        # had no equivalent until this line, so flags leaked onto the
        # wire for bare device commands in request_mode.  Save/restore
        # _call_level so the override is scoped to this one dispatch.
        cmd, bare_level = _strip_level_flags(cmd)
        saved_bare_call_level = self.ctx._call_level
        if bare_level is not None:
            self.ctx._call_level = bare_level
        if self.has_serial_transforms:
            try:
                cmd = self.transform_serial(cmd)
            except ValueError as e:
                _status(str(e), "red")
                return CmdResult.fail(msg=str(e))

        # Skip the legacy plain-text echo when request_mode is on -- the
        # executor will render a JSON-form request envelope instead so
        # the session log stays all-JSON in that mode.  Same intent
        # ("show what was typed"), different rendering.
        if self.cfg.get("echo_input") and not self.cfg.get("request_mode"):
            echo_text = cmd
            if self.cfg.get("show_line_endings", False) and eol_label:
                le = self.cfg.get("line_ending", "\r")
                echo_text += eol_label(le)
            _echo_cmd(echo_text)

        if is_connected and not is_connected():
            _status("Not connected.", "red")
            return CmdResult.fail(msg="Not connected.")

        # Opt-in typed-arg validation against the active profile.  Off by
        # default so a typist gets raw wire access -- device errors are
        # the source of truth.  Turn on (cfg key ``validate_typed_args``)
        # to short-circuit bad args locally with the same vocabulary the
        # MCP path uses.  Skips silently when no profile is loaded or
        # the command isn't a profile entry.
        if self.cfg.get("validate_typed_args"):
            fail = self._validate_bare_typed_args(cmd, _status)
            if fail is not None:
                return fail

        # Bridge dispatch_full's serial_write callback into ctx for the
        # handler.  Hosts (CLITerminal, MCPHost) wire both to the same
        # SerialPort so this is a no-op in production; tests that pass a
        # mock serial_write to dispatch_full now reach the handler too.
        saved_serial_write = self.ctx.serial.write
        if serial_write is not None:
            self.ctx.serial.write = serial_write
        try:
            # When request_mode is on, run the bare command through the
            # request/response executor and emit a JSON envelope.  In
            # MCP mode the profile executor (mcp/server.py) runs first
            # and short-circuits for profile-mapped commands, so this
            # hook only sees commands the profile didn't claim.  In
            # TUI/CLI there's no profile executor on this path, so
            # request_mode applies to every bare command.
            if self.cfg.get("request_mode") and self.ctx.serial.write is not None:
                return self._exec_request_mode(cmd)
            return self.dispatch(f"term.send {cmd}")
        finally:
            self.ctx.serial.write = saved_serial_write
            self.ctx._call_level = saved_bare_call_level

    # -- REPL dispatch ---------------------------------------------------------

    def add_post_dispatch_observer(
        self, cb: Callable[[str, CmdResult], None],
    ) -> Callable[[str, CmdResult], None]:
        """Register ``cb(line, result)`` to fire after every dispatch().

        Used by ``/run.record`` (and any future feature that wants the
        same stream -- audit log, repeat-last-successful, etc.).

        Args:
            cb: Callable invoked with the dispatched line and its
                ``CmdResult``.  Fired even for failed dispatches; the
                subscriber decides what to do with the failure.

        Returns:
            The same callable, useful as a deregistration token.
        """
        self._post_dispatch_observers.append(cb)
        return cb

    def remove_post_dispatch_observer(
        self, token: Callable[[str, CmdResult], None],
    ) -> None:
        """Deregister a previously-added observer.  Idempotent."""
        if token in self._post_dispatch_observers:
            self._post_dispatch_observers.remove(token)

    def dispatch(self, line: str) -> CmdResult:
        """Parse and dispatch a REPL command (prefix already stripped).

        Splits the line into command name and args, expands sequence
        templates in the args, then invokes the matching plugin handler.
        Fires registered post-dispatch observers with ``(line, result)``
        before returning -- observers see every dispatch, including
        capability-gate failures and unknown-command errors.

        Args:
            line: Command string without the REPL prefix (e.g. "grep error").

        Returns:
            CmdResult with success/error status and elapsed time.
        """
        result = self._dispatch_inner(line)
        for obs in self._post_dispatch_observers:
            try:
                obs(line, result)
            except Exception as e:  # observer bug must not break dispatch
                self.write(f"Observer error: {e}", "red")
        return result

    def _dispatch_inner(self, line: str) -> CmdResult:
        """Original dispatch body -- pure dispatch logic, no observers.

        Extracted from ``dispatch()`` so the observer fire-site is one
        place at the wrapper level, not duplicated at every early return.
        """
        parts = line.split(None, 1)
        if not parts:
            plugin = self._plugins.get("help")
            if plugin:
                plugin.handler(self.ctx, "")
            # Bare-empty dispatch: fell through to help; nothing
            # scriptable to capture here.
            return CmdResult.ok(value="")
        name = parts[0].lower()
        raw_args = parts[1] if len(parts) > 1 else ""
        args = self._expand_template(raw_args)

        # Universal level-suffix modifier: any command can be invoked as
        # ``<cmd>.<level>`` (silent/quiet/normal/verbose) to override the
        # output level for that one call.  We only fall back to this if
        # no real plugin is registered with that suffix (so commands that
        # explicitly define a ``.<level>`` subcommand still win).
        call_level: str | None = None
        plugin = self._plugins.get(name)
        if plugin is None:
            for level in OUTPUT_LEVELS:
                suffix = "." + level
                if name.endswith(suffix):
                    bare_name = name[: -len(suffix)]
                    bare_plugin = self._plugins.get(bare_name)
                    if bare_plugin is not None:
                        plugin = bare_plugin
                        name = bare_name
                        call_level = level
                    break

        if plugin:
            # Capability gate: every command declares the environment
            # capabilities its handler relies on via Command.needs.  Before
            # doing any work, verify the current context provides them.
            # A mismatch fails with a clear message naming what's missing,
            # rather than letting the handler hit a no-op lambda or crash.
            missing = plugin.needs.missing_from(self._effective_capabilities())
            if missing:
                result = CmdResult.fail(
                    msg=f"/{name} requires: {', '.join(missing)} "
                    f"(not available in this environment)"
                )
                self.write(result.err_msg, "red")
                return result
            # Universal level-flag pre-pass: strip --silent/--quiet/--normal/
            # --verbose before per-command flag parsing so every command
            # accepts them without declaring them.  Suffix and flag must
            # not disagree (typo guard).
            args, flag_level = _strip_level_flags(args)
            if flag_level is not None:
                if call_level is not None and call_level != flag_level:
                    result = CmdResult.fail(
                        msg=f"Conflicting output level: .{call_level} "
                        f"and --{flag_level}"
                    )
                    self.write(result.err_msg, "red")
                    return result
                call_level = flag_level
            # First-class flag parsing: strip declared flags from args and
            # record them on the context for the handler to read via
            # ctx.flag(). Commands with no declared flags opt out entirely
            # (args passed through unchanged; set is empty).
            args, active_flags, flag_error = _parse_flags(args, plugin.flags)
            if flag_error:
                result = CmdResult.fail(msg=flag_error)
                self.write(result.err_msg, "red")
                return result
            # Declarative params: parse/coerce/validate before touching the
            # context, so a bad-argument failure returns without having
            # mutated ctx.  Commands with no declared params opt out entirely
            # (bound stays empty; args passes through unchanged).
            bound_params: dict = {}
            if plugin.params:
                bound_params, param_error = parse_params(plugin.params, args)
                if param_error:
                    usage = synthesize_synopsis(plugin.params)
                    result = CmdResult.fail(
                        msg=f"/{name}: {param_error}\nUsage: /{name} {usage}"
                    )
                    self.write(result.err_msg, "red")
                    return result
            self.ctx.active_flags = active_flags
            # Save+restore the level (rather than reset to None) so a
            # nested dispatch inside the handler -- e.g. /run cascading
            # into the script's commands -- inherits this call's level.
            # bound_params follows the same save/restore discipline (NOT the
            # active_flags set-then-clear) so a nested ctx.dispatch() can't
            # strand the outer command's parsed params.
            saved_bound_params = self.ctx.bound_params
            self.ctx.bound_params = bound_params
            saved_call_level = self.ctx._call_level
            if call_level is not None:
                self.ctx._call_level = call_level
            try:
                t0 = time.perf_counter()
                if self.ctx.output_level == "silent":
                    saved_write = self.ctx.io._write
                    saved_write_markup = self.ctx.io._write_markup
                    self.ctx.io._write = lambda text, color=None: None
                    self.ctx.io._write_markup = lambda text: None
                    try:
                        result = plugin.handler(self.ctx, args)
                    finally:
                        self.ctx.io._write = saved_write
                        self.ctx.io._write_markup = saved_write_markup
                else:
                    result = plugin.handler(self.ctx, args)
                if result is None:
                    # SPECIAL CASE: legacy / external handler returned None.
                    # Synthesize an empty success so the dispatch loop has
                    # a result to attach elapsed_s to.
                    result = CmdResult.ok(value="")
                result.elapsed_s = time.perf_counter() - t0
            # MissingCapability is the structural backstop for
            # "handler called a capability-gated handle method but didn't
            # declare the capability in Command.needs".  Surface it with
            # the handle's own clear message rather than wrapping it as
            # a generic plugin error.
            except MissingCapability as e:
                result = CmdResult.fail(msg=str(e))
            # Plugin handlers are third-party code and can raise
            # anything else.  BoundaryException signals the reviewed
            # broad catch; the failure ships back in a CmdResult.
            except BoundaryException as e:
                result = CmdResult.fail(msg=f"Plugin error ({name}): {e}")
            finally:
                self.ctx.active_flags = set()
                self.ctx.bound_params = saved_bound_params
                self.ctx._call_level = saved_call_level
        else:
            suggestion = _suggest_command(name, self._plugins, self.prefix)
            if suggestion:
                result = CmdResult.fail(
                    msg=f"Unknown command: {name} -- did you mean {suggestion}?"
                )
            else:
                result = CmdResult.fail(msg=f"Unknown command: {name}")
        if not result.success and result.error:
            self.write(result.err_msg, "red")
        return result

    # -- Engine helpers (exposed to plugins via PluginContext) -----------------

    def replace_cfg(self, cfg: dict, path: str) -> None:
        """Replace config wholesale (called by app on load/edit)."""
        self._cfg_data.clear()
        self._cfg_data.update(cfg)
        self.config_path = path

    def _apply_cfg(self, key: str, new_val) -> None:
        """Apply a config change for this session (not saved to disk).

        The config editor is the only path that persists changes to disk.
        This keeps $(env.NAME) templates in the JSON file intact.

        ``key`` may be a top-level cfg key or a pyserial key that lives
        under cfg["serial"] (post-v22).  Route to wherever it currently
        sits so callers stay oblivious to nesting.
        """
        serial = self._cfg_data.get("serial", {})
        if key in serial:
            serial[key] = new_val
        else:
            self._cfg_data[key] = new_val
        self.write(f"{key} = {new_val!r}  (session)", "green")
        if self._after_cfg:
            self._after_cfg(key, new_val)

    def _expand_template(self, text: str) -> str:
        """Expand ``{seqN}``, ``{datetime}``, ``{starttime}``, ``{clock}``, ``{elapsed}``.

        Reads the seq counter state from ``ctx.ns("seq")``.  Integer keys are
        counters; the string keys ``_start_time`` / ``_start_perf`` hold the
        frozen stamp for ``{starttime}`` and the raw monotonic clock behind
        ``{elapsed}``.  After expansion, writes the updated counters back in
        place so the namespace dict identity is preserved for any cached
        references.
        """
        seq_ns = self.ctx.ns("seq")
        start_time = seq_ns.get("_start_time", "")
        start_perf = seq_ns.get("_start_perf")
        elapsed_s = None if start_perf is None else time.monotonic() - start_perf
        counters = {k: v for k, v in seq_ns.items() if isinstance(k, int)}
        result, new_counters = expand_template(
            text, counters, start_time, elapsed_s=elapsed_s
        )
        for k in list(seq_ns):
            if isinstance(k, int):
                del seq_ns[k]
        seq_ns.update(new_counters)
        return result

    # -- Transform chains ------------------------------------------------------

    @property
    def has_repl_transforms(self) -> bool:
        """True if any plugin registered a REPL transform."""
        return bool(self._repl_transforms)

    @property
    def has_serial_transforms(self) -> bool:
        """True if any plugin registered a serial transform."""
        return bool(self._serial_transforms)

    def transform_repl(self, line: str) -> str:
        """Run all REPL transforms in load order."""
        for fn in self._repl_transforms:
            line = fn(line)
        return line

    def transform_serial(self, line: str) -> str:
        """Run all serial transforms in load order."""
        for fn in self._serial_transforms:
            line = fn(line)
        return line

    # -- Scripting ------------------------------------------------------------

    def start_script(self, args: str) -> tuple[Path | None, CmdResult]:
        """Validate and prepare for script execution.

        Resolves the filename (checking run/ dir as fallback), validates
        nesting depth, and resets sequence counters.

        Args:
            args: Filename string from the /run command.

        Returns:
            Tuple of (resolved Path, CmdResult). Path is None on failure.
        """
        filename = args.strip()
        if not filename:
            return None, CmdResult.fail(msg="Usage: /run <filename>")
        path = Path(filename)
        if not path.exists() and not path.suffix:
            path = Path(filename + ".run")
        if not path.exists():
            alt = self.scripts_dir / path.name
            if alt.exists():
                path = alt
            else:
                return None, CmdResult.fail(msg=f"File not found: {filename}")
        if self._script_depth >= self._max_script_depth:
            return None, CmdResult.fail(
                msg=f"Script nesting too deep ({self._max_script_depth} levels). Use /stop first."
            )
        outermost = self._script_depth == 0
        if outermost:
            self._script_stop.clear()
        self._script_depth += 1
        self._script_stack.append(path.name)
        if outermost:
            # on_script_start fires for the outermost script only.  The seq
            # plugin clears counters and refreshes {starttime} from its hook.
            self.fire_lifecycle("on_script_start")
        self.ctx.io.status(f"Running script: {filename}")
        # Caller pairs this with the resolved Path; auto-resolve gives
        # absolute string at the value site.
        return path, CmdResult.ok(value=path)

    # -- Script blocking command handlers ----------------------------------------

    def _script_delay(self, name: str, args: str, sctx: ScriptCtx) -> CmdResult:
        """Handle /delay in scripts - sleep on background thread.

        For ``/delay`` (not ``.silent``) with ``sctx.delay_progress``
        registered, ticks every 0.25s and posts a rendered bar string
        so the TUI overlay can animate.  For short delays (<1s) or
        the silent variant, falls back to a single blocking wait.
        """
        expanded = self._expand_template(args.strip())
        try:
            seconds = parse_duration(expanded)
        except ValueError as e:
            return CmdResult.fail(msg=str(e))
        cb = sctx.delay_progress if name == "delay" else None
        if cb is None or seconds < 1:
            self._script_stop.wait(timeout=seconds)
        else:
            from termapy.scripting import render_progress_bar

            t0 = time.perf_counter()
            while True:
                elapsed = time.perf_counter() - t0
                if elapsed >= seconds:
                    break
                if self._script_stop.is_set():
                    break
                cb(render_progress_bar(elapsed, seconds), False)
                self._script_stop.wait(timeout=0.25)
            cb("", True)
        if self._script_stop.is_set():
            return CmdResult.fail(msg="Script stopped.")
        if not sctx.profile and name == "delay":
            sctx.w(f"Delay {expanded} done.")
        return CmdResult.ok(value=str(seconds))

    def _script_confirm(self, name: str, args: str, sctx: ScriptCtx) -> CmdResult:
        """Handle /confirm in scripts - show dialog, block on background thread."""
        message = args.strip() or "Continue?"
        accepted = self.ctx.ui.confirm(message)
        if not accepted:
            sctx.w("Script cancelled by user.")
            self._script_stop.set()
        return CmdResult.ok(value="yes" if accepted else "no")

    def _script_run(self, name: str, args: str, sctx: ScriptCtx) -> CmdResult:
        """Handle /run and /run.profile in scripts - nested execution.

        Flags on /run are declared on the registered hook (see
        app.py/cli.py), but scripts bypass dispatch() and call us
        directly, so we strip flags here using the same parser.  The
        flags themselves are inherited from sctx.verbose for nested runs.
        """
        nested_profile = name == "run.profile"
        plugin = self._plugins.get(name)
        run_args = args.strip()
        if plugin:
            run_args, _active, flag_error = _parse_flags(run_args, plugin.flags)
            if flag_error:
                return CmdResult.fail(msg=flag_error)
        nested_path, result = self.start_script(run_args)
        if nested_path:
            if sctx.on_nest:
                sctx.on_nest()
            self.run_script(
                nested_path,
                write=sctx.w,
                dispatch=sctx.dispatch_fn,
                profile=nested_profile,
                progress=sctx.progress,
                on_nest=sctx.on_nest,
                verbose=sctx.verbose,
                delay_progress=sctx.delay_progress,
            )
        return result

    def _script_expect(self, name: str, args: str, sctx: ScriptCtx) -> CmdResult:
        """Handle /expect and /expect.regex in scripts - wait for serial pattern."""
        use_regex = name == "expect.regex"
        kw = parse_keywords(args, {"timeout", "quiet", "match"}, rest_keyword="match")
        pattern = kw.get("match", "").strip()
        if not pattern:
            return CmdResult.fail(msg="Expect: missing match= keyword")
        try:
            timeout_s = parse_duration(kw["timeout"]) if "timeout" in kw else 0.25
        except ValueError as e:
            return CmdResult.fail(msg=f"Expect: {e}")
        quiet = kw.get("quiet", "").lower() == "on"
        timeout_str = kw.get("timeout", "250ms")
        if use_regex:
            import re as _re

            def predicate(line: str) -> bool:
                return bool(_re.search(pattern, line))
        else:

            def predicate(line: str) -> bool:
                return pattern in line

        match = self.wait_for_match(predicate, timeout=timeout_s)
        if self._script_stop.is_set():
            return CmdResult.fail(msg="Script stopped.")
        if match is None:
            self._script_stop.set()
            return CmdResult.fail(msg=f'Expect "{pattern}" timeout after {timeout_str}')
        if not quiet:
            sctx.w(f'Expect "{pattern}" matched', "green")
        # Return the matched line so scripts can capture it via
        # ``$(LINE) <- /expect ready.quiet``.
        return CmdResult.ok(value=match)

    # Blocking commands - must run on the script's background thread because
    # they block (sleep, wait for serial, show dialog). Regular commands are
    # dispatched to the main thread via call_from_thread.
    _BLOCKING_COMMANDS: dict[str, Callable] = {
        "delay": _script_delay,
        "delay.silent": _script_delay,
        "expect": _script_expect,
        "expect.regex": _script_expect,
        "confirm": _script_confirm,
        "run": _script_run,
        "run.profile": _script_run,
    }

    def _run_line(self, stripped: str, sctx: ScriptCtx) -> CmdResult:
        """Execute one script line - blocking command or normal dispatch."""
        if stripped.startswith(sctx.prefix):
            cmd = stripped[len(sctx.prefix) :].strip()
            name, _, args = cmd.partition(" ")
            handler = self._BLOCKING_COMMANDS.get(name.lower())
            if handler:
                self.ctx.io.log(">", stripped)
                t0 = time.perf_counter()
                result = handler(self, name.lower(), args, sctx)
                result.elapsed_s = time.perf_counter() - t0
                # Display errors here (same as dispatch does for normal commands)
                if not result.success and result.error:
                    sctx.w(result.err_msg, "red")
                return result
        # Normal dispatch (REPL commands and serial)
        t0 = time.perf_counter()
        if sctx.dispatch_fn:
            cmd_result = sctx.dispatch_fn(stripped)
        else:
            if stripped.startswith(sctx.prefix):
                cmd_result = self.dispatch(stripped[len(sctx.prefix) :].strip())
            elif self.ctx.serial.is_connected():
                self.ctx.serial.write(
                    (stripped + self.cfg.get("line_ending", "\r")).encode(
                        self.cfg.get("encoding", "utf-8")
                    )
                )
                cmd_result = None
            else:
                cmd_result = None
        if cmd_result and cmd_result.elapsed_s > 0:
            elapsed = cmd_result.elapsed_s
        else:
            elapsed = time.perf_counter() - t0
        # Bare-serial or no-result branches fall back to an empty
        # success so the per-line accounting (elapsed_s, post-dispatch
        # observers) still has a CmdResult to operate on.
        result = cmd_result or CmdResult.ok(value="")
        result.elapsed_s = elapsed
        # Wait for device response after serial commands
        if not stripped.startswith(sctx.prefix):
            # serial_wait_idle may be a no-op lambda in tests or raise
            # if the port disappeared mid-script.  Fall back to a small
            # sleep so script pacing still works.
            import serial as _serial

            try:
                self.ctx.serial.wait_idle()
            except (_serial.SerialException, OSError, AttributeError):
                time.sleep(0.1)
        return result

    @contextmanager
    def _script_session(
        self, path, w, dispatch, profile, verbose, progress, on_nest, delay_progress
    ):
        """Context manager for script lifecycle - setup, yield, teardown."""
        sctx = ScriptCtx(
            w=w,
            dispatch_fn=dispatch,
            prefix=self.prefix,
            profile=profile,
            verbose=verbose,
            progress=progress,
            on_nest=on_nest,
            delay_progress=delay_progress,
        )
        try:
            all_lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            w(f"Script error: {e}", "red")
            self._script_depth -= 1
            if self._script_stack:
                self._script_stack.pop()
            if self._script_depth == 0:
                self.fire_lifecycle("on_script_stop")
            yield sctx  # yield empty context so 'with' block runs (lines is empty)
            return
        sctx.lines = [
            ln for ln in all_lines if ln.strip() and not ln.strip().startswith("#")
        ]
        sctx.total = len(sctx.lines)
        if profile:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            sctx.prof_name = f"{Path(self.config_path).stem}_{ts}.csv"
            prof_dir = Path(self.config_path).parent / "prof"
            prof_dir.mkdir(exist_ok=True)
            sctx.prof_path = prof_dir / sctx.prof_name
            sctx.prof_fh = open(sctx.prof_path, "w", encoding="utf-8")
            sctx.prof_fh.write("Duration (sec),Command\n")
            w(f"── profile: {path.name} -> {sctx.prof_name} ──")
        sctx.script_t0 = time.perf_counter()
        # Scripts execute arbitrary user commands; any command handler
        # or plugin invoked from the script can raise anything.
        # BoundaryException signals the reviewed broad catch so one
        # bad line reports an error and the script session ends
        # cleanly instead of crashing the REPL.
        try:
            yield sctx
        except BoundaryException as e:
            w(f"Script error: {e}", "red")
        finally:
            self._script_depth -= 1
            if self._script_stack:
                self._script_stack.pop()
            if self._script_depth == 0:
                self.fire_lifecycle("on_script_stop")
            if on_nest:
                on_nest()
            if sctx.prof_fh:
                sctx.prof_fh.close()

    def run_script(
        self,
        path: Path,
        write: Callable | None = None,
        dispatch: Callable | None = None,
        profile: bool = False,
        progress: Callable[[int, int], None] | None = None,
        on_nest: Callable[[], None] | None = None,
        verbose: bool = False,
        delay_progress: Callable[[str, bool], None] | None = None,
    ) -> None:
        """Execute a script file line by line (call from a background thread).

        Args:
            path: Path to the script file to execute.
            write: Optional write callback override for thread-safe output.
            dispatch: Optional dispatch callback for the full pipeline.
            profile: Enable per-command timing with CSV output.
            progress: Callback for progress updates (step, total).
            on_nest: Callback when a nested script starts.
            verbose: Show per-command timing output.
            delay_progress: Callback for ``/delay`` progress updates
                ``(bar_text, done)``.  ``done=True`` fires once on the
                final tick so the UI can restore state.
        """
        w = write or self.write
        with self._script_session(
            path, w, dispatch, profile, verbose, progress, on_nest, delay_progress
        ) as sctx:
            for step, raw_line in enumerate(sctx.lines, 1):
                if self._script_stop.is_set():
                    w("Script stopped.")
                    break
                if sctx.progress:
                    sctx.progress(step, sctx.total)
                sctx.step = step
                stripped = raw_line.strip()

                result = self._run_line(stripped, sctx)
                if not result.success and self._script_stop.is_set():
                    break
                label = stripped if len(stripped) <= 60 else stripped[:57] + "..."
                sctx.record(label, result.elapsed_s)
            else:
                # Loop completed without break
                sctx.finish(path.name)
                if not profile and self._script_depth <= 1:
                    if self._script_stop.is_set():
                        w("Script aborted.", "red")
                    elif self.ctx.output_level == "verbose":
                        w("Script finished.")

    # -- Properties -----------------------------------------------------------

    def _data_subdir(self, folder: str) -> Path:
        """Return a per-config data subdirectory, or cwd if no config."""
        if self.config_path:
            return Path(self.config_path).parent / folder
        return Path(".")

    @property
    def ss_dir(self) -> Path:
        """Screenshot directory, derived from config_path."""
        return self._data_subdir(SS)

    @property
    def scripts_dir(self) -> Path:
        """Run scripts directory, derived from config_path."""
        return self._data_subdir(RUN)

    @property
    def proto_dir(self) -> Path:
        """Protocol test scripts directory, derived from config_path."""
        return self._data_subdir(PROTO)

    @property
    def cap_dir(self) -> Path:
        """Captures directory, derived from config_path."""
        return self._data_subdir(CAP)

    @property
    def prof_dir(self) -> Path:
        """Profile directory, derived from config_path."""
        return self._data_subdir(PROF)

    @property
    def echo(self) -> bool:
        return self.ctx.ns("flags")["echo"]

    @property
    def in_script(self) -> bool:
        return self._script_depth > 0

    def _effective_capabilities(self) -> CapabilitySet:
        """Return the context's capabilities augmented with dynamic ones.

        Some capabilities don't belong on the context because they change
        per-dispatch rather than per-environment:

          - ``block_until``: true only inside a script (script runner
            executes on a background thread where blocking is safe).
          - ``serial_connected``: true only when a port is currently open.

        Kept here as a single point of truth for "what can this command
        do right now" so every dispatch sees a consistent effective set.
        """
        effective = self.ctx.capabilities
        dynamic_fields: dict[str, bool] = {}
        if self.in_script:
            dynamic_fields["block_until"] = True
        # ctx.serial.is_connected may be absent on a minimal test fake.
        # getattr with a default is forgiving without hiding bugs in
        # a real is_connected() implementation.
        serial = getattr(self.ctx, "serial", None)
        is_connected = getattr(serial, "is_connected", None) if serial is not None else None
        if is_connected is not None and is_connected():
            dynamic_fields["serial_connected"] = True
        if dynamic_fields:
            effective = effective.union(CapabilitySet(**dynamic_fields))
        return effective
