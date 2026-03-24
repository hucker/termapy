"""REPL engine for termapy — plugin-based command dispatch and scripting.

All commands (built-in and external) are plugins loaded as .py files.
Built-in plugins ship in termapy/builtins/. External plugins are loaded
from folders by app.py. The engine owns state (seq counters, echo, etc.)
and exposes it through PluginContext lambdas.
"""

import json
import time
from datetime import datetime
from pathlib import Path
from threading import Event
from types import MappingProxyType
from typing import Callable

from termapy.plugins import (
    DirectiveInfo, DirectiveResult, PluginContext, PluginInfo, TransformInfo,
    builtins_dir, load_plugins_from_dir,
)
from termapy.scripting import expand_template, parse_duration


class ReplEngine:
    """Plugin-based REPL command engine."""

    def __init__(self, cfg: dict, config_path: str,
                 write: Callable, prefix: str = "/") -> None:
        """Initialize the REPL engine with config and plugin loading.

        Args:
            cfg: Config dict (owned by the engine, wrapped in MappingProxyType).
            config_path: Path to the JSON config file on disk.
            write: Callback for output — write(text, color="dim").
            prefix: REPL command prefix (default "/").
        """
        self._cfg_data = cfg
        self.cfg = MappingProxyType(self._cfg_data)
        self.config_path = config_path
        self.write = write              # write(text, color="dim") callback
        self.prefix = prefix
        self._seq_counters: dict[int, int] = {}
        self._seq_start_time: str = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._script_depth: int = 0
        self._script_stack: list[str] = []  # stack of script names
        self._script_stop = Event()
        self._max_script_depth: int = 5
        self._echo: bool = True         # echo ! command lines to screen

        # Plugin context — set by app.py after mount via set_context()
        self.ctx = PluginContext(write=write)

        # Unified plugin registry — all commands live here
        self._plugins: dict[str, PluginInfo] = {}

        # Config change callback (set by app.py)
        self._after_cfg = None  # callback: (key, new_val) -> None (post-apply refresh)

        # Transform chains — populated during plugin/transform registration
        self._repl_transforms: list[Callable] = []
        self._serial_transforms: list[Callable] = []
        self._transform_infos: list[TransformInfo] = []

        # Directive chain — pre-dispatch line rewriters
        self._directives: list[DirectiveInfo] = []

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

    def register_hook(self, name: str, args: str, help_text: str,
                      handler: Callable, source: str = "built-in",
                      long_help: str = "",
                      raw_args: bool = False) -> None:
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
        self._plugins[name] = PluginInfo(
            name=name, args=args, help=help_text,
            handler=handler, long_help=long_help, source=source,
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

    # -- Dispatch -------------------------------------------------------------

    def dispatch(self, line: str) -> None:
        """Parse and dispatch a REPL command (prefix already stripped).

        Splits the line into command name and args, expands sequence
        templates in the args, then invokes the matching plugin handler.

        Args:
            line: Command string without the REPL prefix (e.g. "grep error").
        """
        parts = line.split(None, 1)
        if not parts:
            plugin = self._plugins.get("help")
            if plugin:
                plugin.handler(self.ctx, "")
            return
        name = parts[0].lower()
        raw_args = parts[1] if len(parts) > 1 else ""
        args, self._seq_counters = expand_template(
            raw_args, self._seq_counters, self._seq_start_time
        )
        plugin = self._plugins.get(name)
        if plugin:
            try:
                plugin.handler(self.ctx, args)
            except Exception as e:
                self.write(f"Plugin error ({name}): {e}", "red")
        else:
            self.write(f"Unknown REPL command: {name}", "red")

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

    def start_script(self, args: str) -> Path | None:
        """Validate and prepare for script execution.

        Resolves the filename (checking scripts/ dir as fallback), validates
        that no script is already running, and resets sequence counters.

        Args:
            args: Filename string from the /run command.

        Returns:
            Resolved Path if ready to run, None if validation failed.
        """
        filename = args.strip()
        if not filename:
            self.write("Usage: /run <filename>", "red")
            return None
        path = Path(filename)
        if not path.exists() and not path.suffix:
            path = Path(filename + ".run")
        if not path.exists():
            # Try resolving relative to the per-config scripts/ folder
            alt = self.scripts_dir / path.name
            if alt.exists():
                path = alt
            else:
                self.write(f"File not found: {filename}", "red")
                if self.scripts_dir != Path("."):
                    self.write(f"  (also checked {self.scripts_dir})", "dim")
                return None
        if self._script_depth >= self._max_script_depth:
            self.write(
                f"Script nesting too deep ({self._max_script_depth} levels). "
                "Use /stop first.",
                "red",
            )
            return None
        if self._script_depth == 0:
            self._script_stop.clear()
        self._script_depth += 1
        self._script_stack.append(path.name)
        self._seq_counters = {}
        self._seq_start_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.write(f"Running script: {filename}")
        return path

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

        Every non-blank, non-comment line is routed through the full dispatch
        pipeline (directives, transforms, REPL/serial). The only exception is
        ``/delay`` which sleeps in the background thread to avoid blocking the
        UI event loop.

        Args:
            path: Path to the script file to execute.
            write: Optional write callback override for thread-safe output.
                Falls back to ``self.write`` when None (e.g. in tests).
            dispatch: Optional dispatch callback that routes a raw command
                through the full pipeline (``_dispatch_single`` via
                ``call_from_thread``). Falls back to a local REPL-only
                dispatch when None (e.g. in tests).
        """
        w = write or self.write
        prefix = self.prefix
        profile_times: list[tuple[str, float]] = []
        prof_fh = None
        try:
            all_lines = path.read_text(encoding="utf-8").splitlines()
            lines = [
                ln for ln in all_lines
                if ln.strip() and not ln.strip().startswith("#")
            ]
            total = len(lines)
            if profile:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                prof_name = f"{Path(self.config_path).stem}_{ts}.csv"
                prof_dir = Path(self.config_path).parent / "prof"
                prof_dir.mkdir(exist_ok=True)
                prof_path = prof_dir / prof_name
                prof_fh = open(prof_path, "w", encoding="utf-8")
                prof_fh.write("Duration (sec),Command\n")
                w(f"── profile: {path.name} → {prof_name} ──")
            script_t0 = time.perf_counter()
            for step, raw_line in enumerate(lines, 1):
                if self._script_stop.is_set():
                    w("Script stopped.")
                    break
                if progress:
                    progress(step, total)
                stripped = raw_line.strip()
                cmd_t0 = time.perf_counter()
                # THREADING: run_script runs in a background thread, but
                # dispatch() routes commands to the main thread via
                # call_from_thread. Commands that block (delay, confirm)
                # MUST be handled here in the background thread — if they
                # run on the main thread, call_from_thread fails and
                # blocking calls freeze the UI. When adding new commands
                # that block or use call_from_thread internally, add them
                # here as special cases.
                if stripped.startswith(prefix):
                    cmd = stripped[len(prefix):].strip()
                    name, _, args = cmd.partition(" ")
                    if name.lower() == "delay":
                        self.ctx.log(">", stripped)
                        expanded, self._seq_counters = expand_template(
                            args.strip(), self._seq_counters,
                            self._seq_start_time,
                        )
                        try:
                            seconds = parse_duration(expanded)
                        except ValueError as e:
                            w(str(e), "red")
                            break
                        t0 = time.perf_counter()
                        # Sleep in small steps so _script_stop is responsive
                        self._script_stop.wait(timeout=seconds)
                        if self._script_stop.is_set():
                            w("Script stopped.")
                            break
                        if profile:
                            elapsed = time.perf_counter() - t0
                            profile_times.append((stripped, elapsed))
                            prof_fh.write(f"{elapsed:.6f},{stripped}\n")
                        else:
                            w(f"Delay {expanded} done.")
                        if verbose:
                            elapsed = time.perf_counter() - cmd_t0
                            w(f"[{step}/{total}] /delay {expanded} ({elapsed:.3f}s)")
                        continue
                    if name.lower() in ("run", "run.profile"):
                        self.ctx.log(">", stripped)
                        nested_profile = name.lower() == "run.profile"
                        run_args = args.strip()
                        # Strip -v/--verbose from nested /run — inherited
                        run_tokens = run_args.split()
                        run_args = " ".join(
                            t for t in run_tokens if t not in ("-v", "--verbose")
                        )
                        nested_path = self.start_script(run_args)
                        if nested_path:
                            if on_nest:
                                on_nest()
                            t0 = time.perf_counter()
                            self.run_script(
                                nested_path,
                                write=w,
                                dispatch=dispatch,
                                profile=nested_profile,
                                progress=progress,
                                on_nest=on_nest,
                                verbose=verbose,
                            )
                            if verbose:
                                elapsed = time.perf_counter() - t0
                                w(f"[{step}/{total}] /run {nested_path.name} ({elapsed:.3f}s)")
                            if profile:
                                elapsed = time.perf_counter() - t0
                                profile_times.append((stripped, elapsed))
                                prof_fh.write(f"{elapsed:.6f},{stripped}\n")
                        continue
                    if name.lower() == "confirm":
                        self.ctx.log(">", stripped)
                        message = args.strip() or "Continue?"
                        t0 = time.perf_counter()
                        if not self.ctx.confirm(message):
                            w("Script cancelled by user.")
                            self._script_stop.set()
                        if profile:
                            elapsed = time.perf_counter() - t0
                            profile_times.append((stripped, elapsed))
                            prof_fh.write(f"{elapsed:.6f},{stripped}\n")
                        continue
                # Everything else goes through the full dispatch pipeline
                t0 = time.perf_counter()
                if dispatch:
                    dispatch(stripped)
                else:
                    # Fallback for tests: classify and handle locally
                    if stripped.startswith(prefix):
                        self.dispatch(stripped[len(prefix):].strip())
                    elif self.ctx.is_connected():
                        self.ctx.serial_write(
                            (stripped + self.cfg.get("line_ending", "\r"))
                            .encode(self.cfg.get("encoding", "utf-8"))
                        )
                elapsed = time.perf_counter() - t0
                if verbose:
                    label = stripped if len(stripped) <= 60 else stripped[:57] + "..."
                    w(f"[{step}/{total}] {label} ({elapsed:.3f}s)")
                if profile:
                    label = stripped if len(stripped) <= 60 else stripped[:57] + "..."
                    profile_times.append((label, elapsed))
                    prof_fh.write(f"{elapsed:.6f},{label}\n")
                time.sleep(0.1)
            else:
                if verbose:
                    script_elapsed = time.perf_counter() - script_t0
                    w(f"Script {path.name} done ({script_elapsed:.3f}s)")
                if profile and profile_times:
                    total = sum(t for _, t in profile_times)
                    # Dump the CSV file to terminal
                    prof_fh.flush()
                    for line in prof_path.read_text(encoding="utf-8").splitlines():
                        w(line)
                    w(f"── {total:.3f}s total ({len(profile_times)} commands) → {prof_name} ──")
                elif self._script_depth <= 1:
                    w("Script finished.")
        except Exception as e:
            w(f"Script error: {e}", "red")
        finally:
            self._script_depth -= 1
            if self._script_stack:
                self._script_stack.pop()
            if on_nest:
                on_nest()
            if prof_fh:
                prof_fh.close()

    # -- Properties -----------------------------------------------------------

    @property
    def ss_dir(self) -> Path:
        """Screenshot directory, derived from config_path."""
        if self.config_path:
            return Path(self.config_path).parent / "ss"
        return Path(".")

    @property
    def scripts_dir(self) -> Path:
        """Scripts directory, derived from config_path."""
        if self.config_path:
            return Path(self.config_path).parent / "scripts"
        return Path(".")

    @property
    def proto_dir(self) -> Path:
        """Protocol test scripts directory, derived from config_path."""
        if self.config_path:
            return Path(self.config_path).parent / "proto"
        return Path(".")

    @property
    def cap_dir(self) -> Path:
        """Captures directory, derived from config_path."""
        if self.config_path:
            return Path(self.config_path).parent / "cap"
        return Path(".")

    @property
    def prof_dir(self) -> Path:
        """Profile directory, derived from config_path."""
        if self.config_path:
            return Path(self.config_path).parent / "prof"
        return Path(".")

    @property
    def echo(self) -> bool:
        return self._echo

    @property
    def in_script(self) -> bool:
        return self._script_depth > 0
