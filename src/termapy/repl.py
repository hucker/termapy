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

from termapy.plugins import (
    DirectiveInfo,
    DirectiveResult,
    PluginContext,
    PluginInfo,
    TargetCommand,
    TransformInfo,
    builtins_dir,
    load_plugins_from_dir,
)
from termapy.folders import CAP, PROF, PROTO, RUN, SS
from termapy.plugins import CmdResult
from termapy.scripting import expand_template, parse_duration, parse_keywords


def _suggest_command(name: str, plugins: dict, prefix: str = "/") -> str | None:
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
            fmt = f"{elapsed:.6f}" if elapsed < 0.001 else f"{elapsed:.3f}"
            self.w(f"[{self.step}/{self.total}] {label} ({fmt}s)")
        if self.profile and self.prof_fh:
            self.profile_times.append((label, elapsed))
            self.prof_fh.write(f"{elapsed:.6f},{label}\n")

    def finish(self, script_name: str) -> None:
        """Display summary after successful script completion."""
        if self.verbose:
            elapsed = time.perf_counter() - self.script_t0
            fmt = f"{elapsed:.6f}" if elapsed < 0.001 else f"{elapsed:.3f}"
            self.w(f"Script {script_name} done ({fmt}s)")
        if self.profile and self.profile_times and self.prof_fh and self.prof_path:
            total_t = sum(t for _, t in self.profile_times)
            fmt = f"{total_t:.6f}" if total_t < 0.001 else f"{total_t:.3f}"
            self.prof_fh.flush()
            for line in self.prof_path.read_text(encoding="utf-8").splitlines():
                self.w(line)
            self.w(f"── {fmt}s total ({len(self.profile_times)} commands) -> {self.prof_name} ──")


class ReplEngine:
    """Plugin-based REPL command engine."""

    def __init__(
        self, cfg: dict, config_path: str, write: Callable, prefix: str = "/"
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
        self._seq_counters: dict[int, int] = {}
        self._seq_start_time: str = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._script_depth: int = 0
        self._script_stack: list[str] = []  # stack of script names
        self._script_stop = Event()
        self._max_script_depth: int = 5
        self._echo: bool = True  # echo ! command lines to screen
        # Expect watcher — predicate set by wait_for_match(), checked by feed_lines()
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
        self.ctx = PluginContext(write=write)

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

        # Target device commands - help-only, not dispatched
        self._target_commands: dict[str, TargetCommand] = {}

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

    # -- Expect / pattern matching ---------------------------------------------

    def wait_for_match(
        self,
        predicate: Callable[[str], bool],
        timeout: float = 5.0,
    ) -> str | None:
        """Block until a serial line matches predicate or timeout expires.

        Must be called from a background thread. Serial data continues
        to display normally — feed_lines() checks the predicate as lines
        arrive.

        Race-condition safety: the predicate is installed BEFORE scanning
        the recent-lines buffer. This eliminates the gap where a line
        could arrive after the buffer check but before the predicate is
        active — feed_lines() would catch it in that window.

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
            # Always buffer — wait_for_match() scans this retroactively
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
        long_help: str = "",
        raw_args: bool = False,
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
            raw_args: Skip REPL transforms for this command (default False).
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

    # -- Target commands -------------------------------------------------------

    def set_target_commands(self, commands: dict[str, TargetCommand]) -> None:
        """Replace all target commands with a new set from /import."""
        self._target_commands.clear()
        self._target_commands.update(commands)

    def clear_target_commands(self) -> None:
        """Remove all imported target commands."""
        self._target_commands.clear()

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

        # 1. /raw bypass - no transforms, no directives
        if cmd.startswith(prefix + "raw "):
            raw_text = cmd[len(prefix) + 4 :]
            _log(">", cmd)
            if self._echo:
                _echo(f"[cyan]> {cmd}[/]")
            if serial_write_raw:
                serial_write_raw(raw_text)
            return CmdResult.ok()

        # 2. Pre-dispatch directives (e.g. $(VAR) = value -> /var.set)
        result = self.run_directives(cmd)
        if result.action == "rewrite":
            _log(">", cmd)
            if self._echo:
                _echo(f"[cyan]> {cmd}[/]")
            return self.dispatch(result.payload)
        if result.action == "warn":
            _log(">", cmd)
            if self._echo:
                _echo(f"[cyan]> {cmd}[/]")
            _status(f"Warning: {result.payload}", "yellow")
            return CmdResult.ok()
        if result.action == "error":
            _log(">", cmd)
            if self._echo:
                _echo(f"[cyan]> {cmd}[/]")
            _status(f"Error: {result.payload}", "red")
            return CmdResult.fail(msg=result.payload)

        # Shared echo using echo_input_fmt for both REPL and serial
        from termapy.builtins.plugins.var import expand_vars

        def _echo_cmd(text: str) -> None:
            fmt = expand_vars(self.cfg.get("echo_input_fmt", "> {cmd}"))
            _echo(fmt.replace("{cmd}", text))

        # 3. REPL command (starts with prefix)
        if cmd.startswith(prefix):
            repl_cmd = cmd[len(prefix) :].strip()
            _log(">", f"{prefix}{repl_cmd}")
            if self._echo and not repl_cmd.startswith("echo.quiet"):
                _echo_cmd(f"{prefix}{repl_cmd}")
            if self.has_repl_transforms:
                if not self.command_has_raw_args(repl_cmd):
                    try:
                        repl_cmd = self.transform_repl(repl_cmd)
                    except ValueError as e:
                        _status(str(e), "red")
                        return CmdResult.fail(msg=str(e))
            return self.dispatch(repl_cmd)

        # 4. Serial command - apply transforms, encode, send
        if self.has_serial_transforms:
            try:
                cmd = self.transform_serial(cmd)
            except ValueError as e:
                _status(str(e), "red")
                return CmdResult.fail(msg=str(e))

        if self.cfg.get("echo_input"):
            echo_text = cmd
            if self.cfg.get("show_line_endings", False) and eol_label:
                le = self.cfg.get("line_ending", "\r")
                echo_text += eol_label(le)
            _echo_cmd(echo_text)

        if is_connected and not is_connected():
            _status("Not connected - command not sent", "red")
            return CmdResult.fail(msg="Not connected - command not sent")

        line_ending = self.cfg.get("line_ending", "\r")
        if serial_write:
            try:
                serial_write(
                    (cmd + line_ending).encode(self.cfg.get("encoding", "utf-8"))
                )
            except (OSError, Exception) as e:
                _status(f"Send error: {e}", "red")
                return CmdResult.fail(msg=f"Send error: {e}")
        return CmdResult.ok()

    # -- REPL dispatch ---------------------------------------------------------

    def dispatch(self, line: str) -> CmdResult:
        """Parse and dispatch a REPL command (prefix already stripped).

        Splits the line into command name and args, expands sequence
        templates in the args, then invokes the matching plugin handler.

        Args:
            line: Command string without the REPL prefix (e.g. "grep error").

        Returns:
            CmdResult with success/error status and elapsed time.
        """
        parts = line.split(None, 1)
        if not parts:
            plugin = self._plugins.get("help")
            if plugin:
                plugin.handler(self.ctx, "")
            return CmdResult.ok()
        name = parts[0].lower()
        raw_args = parts[1] if len(parts) > 1 else ""
        args, self._seq_counters = expand_template(
            raw_args, self._seq_counters, self._seq_start_time
        )
        plugin = self._plugins.get(name)
        if plugin:
            try:
                t0 = time.perf_counter()
                result = plugin.handler(self.ctx, args)
                if result is None:
                    result = CmdResult.ok()
                result.elapsed_s = time.perf_counter() - t0
            except Exception as e:
                result = CmdResult.fail(msg=f"Plugin error ({name}): {e}")
        else:
            suggestion = _suggest_command(name, self._plugins, self.prefix)
            if suggestion:
                result = CmdResult.fail(msg=f"Unknown command: {name} -- did you mean {suggestion}?")
            else:
                result = CmdResult.fail(msg=f"Unknown command: {name}")
        if not result.success and result.error:
            self.write(result.err_msg, "red")
        return result

    # -- Engine helpers (exposed to plugins via PluginContext) -----------------

    @staticmethod
    def _coerce_type(value_str: str, existing: object) -> object:
        """Coerce a string value to match the type of the existing config value.

        Args:
            value_str: Raw string from user input.
            existing: Current config value whose type determines the conversion.

        Returns:
            Converted value matching the type of existing.

        Raises:
            ValueError: If conversion fails (e.g. non-boolean string for a bool field).
        """
        if isinstance(existing, bool):
            if value_str.lower() in ("true", "1", "yes", "on"):
                return True
            if value_str.lower() in ("false", "0", "no", "off"):
                return False
            raise ValueError(f"Expected bool, got '{value_str}'")
        if isinstance(existing, int):
            return int(value_str)
        if isinstance(existing, float):
            return float(value_str)
        return value_str

    def replace_cfg(self, cfg: dict, path: str) -> None:
        """Replace config wholesale (called by app on load/edit)."""
        self._cfg_data.clear()
        self._cfg_data.update(cfg)
        self.config_path = path

    def _apply_cfg(self, key: str, new_val) -> None:
        """Apply a config change for this session (not saved to disk).

        The config editor is the only path that persists changes to disk.
        This keeps $(env.NAME) templates in the JSON file intact.
        """
        self._cfg_data[key] = new_val
        self.write(f"{key} = {new_val!r}  (session)", "green")
        if self._after_cfg:
            self._after_cfg(key, new_val)

    def _reset_seq(self) -> None:
        """Reset sequence counters and start time."""
        self._seq_counters = {}
        self._seq_start_time = datetime.now().strftime("%Y%m%d_%H%M%S")

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
        if self._script_depth == 0:
            self._script_stop.clear()
        self._script_depth += 1
        self._script_stack.append(path.name)
        self._seq_counters = {}
        self._seq_start_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.ctx.status(f"Running script: {filename}")
        return path, CmdResult.ok()

    # -- Script blocking command handlers ----------------------------------------

    def _script_delay(self, name: str, args: str, sctx: ScriptCtx) -> CmdResult:
        """Handle /delay in scripts — sleep on background thread."""
        expanded, self._seq_counters = expand_template(
            args.strip(), self._seq_counters, self._seq_start_time,
        )
        try:
            seconds = parse_duration(expanded)
        except ValueError as e:
            return CmdResult.fail(msg=str(e))
        t0 = time.perf_counter()
        self._script_stop.wait(timeout=seconds)
        if self._script_stop.is_set():
            return CmdResult.fail(msg="Script stopped.")
        elapsed = time.perf_counter() - t0
        if not sctx.profile:
            sctx.w(f"Delay {expanded} done.")
        return CmdResult.ok()

    def _script_confirm(self, name: str, args: str, sctx: ScriptCtx) -> CmdResult:
        """Handle /confirm in scripts — show dialog, block on background thread."""
        message = args.strip() or "Continue?"
        if not self.ctx.confirm(message):
            sctx.w("Script cancelled by user.")
            self._script_stop.set()
        return CmdResult.ok()

    def _script_run(self, name: str, args: str, sctx: ScriptCtx) -> CmdResult:
        """Handle /run and /run.profile in scripts — nested execution."""
        nested_profile = name == "run.profile"
        run_args = args.strip()
        run_tokens = run_args.split()
        run_args = " ".join(t for t in run_tokens if t not in ("-v", "--verbose"))
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
            )
        return result

    def _script_expect(self, name: str, args: str, sctx: ScriptCtx) -> CmdResult:
        """Handle /expect and /expect.regex in scripts — wait for serial pattern."""
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
        return CmdResult.ok()

    # Blocking commands — must run on the script's background thread because
    # they block (sleep, wait for serial, show dialog). Regular commands are
    # dispatched to the main thread via call_from_thread.
    _BLOCKING_COMMANDS: dict[str, Callable] = {
        "delay": _script_delay,
        "delay.quiet": _script_delay,
        "expect": _script_expect,
        "expect.regex": _script_expect,
        "confirm": _script_confirm,
        "run": _script_run,
        "run.profile": _script_run,
    }

    def _run_line(self, stripped: str, sctx: ScriptCtx) -> CmdResult:
        """Execute one script line — blocking command or normal dispatch."""
        if stripped.startswith(sctx.prefix):
            cmd = stripped[len(sctx.prefix):].strip()
            name, _, args = cmd.partition(" ")
            handler = self._BLOCKING_COMMANDS.get(name.lower())
            if handler:
                self.ctx.log(">", stripped)
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
                cmd_result = self.dispatch(stripped[len(sctx.prefix):].strip())
            elif self.ctx.is_connected():
                self.ctx.serial_write(
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
        result = cmd_result or CmdResult.ok()
        result.elapsed_s = elapsed
        # Wait for device response after serial commands
        if not stripped.startswith(sctx.prefix):
            try:
                self.ctx.serial_wait_idle()
            except Exception:
                time.sleep(0.1)
        return result

    @contextmanager
    def _script_session(self, path, w, dispatch, profile, verbose, progress, on_nest):
        """Context manager for script lifecycle — setup, yield, teardown."""
        sctx = ScriptCtx(
            w=w, dispatch_fn=dispatch, prefix=self.prefix,
            profile=profile, verbose=verbose,
            progress=progress, on_nest=on_nest,
        )
        try:
            all_lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            w(f"Script error: {e}", "red")
            self._script_depth -= 1
            if self._script_stack:
                self._script_stack.pop()
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
        try:
            yield sctx
        except Exception as e:
            w(f"Script error: {e}", "red")
        finally:
            self._script_depth -= 1
            if self._script_stack:
                self._script_stack.pop()
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
        """
        w = write or self.write
        with self._script_session(path, w, dispatch, profile, verbose, progress, on_nest) as sctx:
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
                    elif self.ctx.verbose:
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
        return self._echo

    @property
    def in_script(self) -> bool:
        return self._script_depth > 0
