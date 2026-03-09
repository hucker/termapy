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

from termapy.plugins import PluginContext, PluginInfo, builtins_dir, load_plugins_from_dir
from termapy.scripting import expand_template, parse_duration, parse_script_lines


class ReplEngine:
    """Plugin-based REPL command engine."""

    def __init__(self, cfg: dict, config_path: str,
                 write: Callable, prefix: str = "!") -> None:
        """Initialize the REPL engine with config and plugin loading.

        Args:
            cfg: Config dict (owned by the engine, wrapped in MappingProxyType).
            config_path: Path to the JSON config file on disk.
            write: Callback for output — write(text, color="dim").
            prefix: REPL command prefix (default "!").
        """
        self._cfg_data = cfg
        self.cfg = MappingProxyType(self._cfg_data)
        self.config_path = config_path
        self.write = write              # write(text, color="dim") callback
        self.prefix = prefix
        self._seq_counters: dict[int, int] = {}
        self._seq_start_time: str = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._in_script: bool = False
        self._script_stop = Event()
        self._echo: bool = True         # echo ! command lines to screen

        # Plugin context — set by app.py after mount via set_context()
        self.ctx = PluginContext(write=write)

        # Unified plugin registry — all commands live here
        self._plugins: dict[str, PluginInfo] = {}

        # Config change callback (set by app.py)
        self._after_cfg = None  # callback: (key, new_val) -> None (post-apply refresh)

        # Load built-in plugins from termapy/builtins/
        self._load_builtins()

    def _load_builtins(self) -> None:
        """Load built-in command plugins from the builtins/ package directory."""
        for info in load_plugins_from_dir(builtins_dir(), "built-in"):
            self._plugins[info.name] = info

    # -- Plugin management ----------------------------------------------------

    def set_context(self, ctx: PluginContext) -> None:
        """Set the plugin context (called by app.py after mount)."""
        self.ctx = ctx

    def register_plugin(self, info: PluginInfo) -> None:
        """Register a plugin. Replaces any existing plugin with the same name."""
        self._plugins[info.name] = info

    def register_hook(self, name: str, args: str, help_text: str,
                      handler: Callable, source: str = "built-in") -> None:
        """Register an app-coupled command as a plugin.

        Bridge for commands that need Textual access (screenshots, connect,
        etc.). The handler receives (ctx, args) like any plugin.

        Args:
            name: Command name (e.g. "connect").
            args: Argument spec string for help display.
            help_text: One-line description for !help output.
            handler: Callable(ctx, args) invoked when the command runs.
            source: Label for origin (default "built-in").
        """
        self._plugins[name] = PluginInfo(
            name=name, args=args, help=help_text,
            handler=handler, source=source,
        )

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
            plugin.handler(self.ctx, args)
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
        """Apply a config change, save to disk, and notify."""
        self._cfg_data[key] = new_val
        try:
            with open(self.config_path, "w") as f:
                json.dump(dict(self._cfg_data), f, indent=4)
            self.write(f"{key} = {new_val!r}  (saved)", "green")
            if self._after_cfg:
                self._after_cfg(key, new_val)
        except Exception as e:
            self.write(f"Error saving config: {e}", "red")

    def _reset_seq(self) -> None:
        """Reset sequence counters and start time."""
        self._seq_counters = {}
        self._seq_start_time = datetime.now().strftime("%Y%m%d_%H%M%S")

    # -- Scripting ------------------------------------------------------------

    def start_script(self, args: str) -> Path | None:
        """Validate and prepare for script execution.

        Resolves the filename (checking scripts/ dir as fallback), validates
        that no script is already running, and resets sequence counters.

        Args:
            args: Filename string from the !run command.

        Returns:
            Resolved Path if ready to run, None if validation failed.
        """
        filename = args.strip()
        if not filename:
            self.write("Usage: !run <filename>", "red")
            return None
        path = Path(filename)
        if not path.exists():
            # Try resolving relative to the per-config scripts/ folder
            alt = self.scripts_dir / filename
            if alt.exists():
                path = alt
            else:
                self.write(f"File not found: {filename}", "red")
                if self.scripts_dir != Path("."):
                    self.write(f"  (also checked {self.scripts_dir})", "dim")
                return None
        if self._in_script:
            self.write("Script already running. Use !stop first.", "red")
            return None
        self._in_script = True
        self._script_stop.clear()
        self._seq_counters = {}
        self._seq_start_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.write(f"Running script: {filename}")
        return path

    def run_script(self, path: Path, write: Callable | None = None) -> None:
        """Execute a script file line by line (call from a background thread).

        Parses the script into serial commands, REPL commands, and delays.
        Supports !stop to abort mid-execution. Serial commands are sent with
        the configured line ending and encoding.

        This method is called from a ``@work(thread=True)`` background thread
        in ``app.py``. The default ``self.write`` callback writes directly to
        the Textual UI, which is not thread-safe. Callers pass a thread-safe
        ``write`` override (typically wrapping ``call_from_thread``) so that
        status messages are posted safely to the main event loop. This avoids
        monkey-patching ``self.write`` and keeps the override scoped to the
        call stack.

        Args:
            path: Path to the script file to execute.
            write: Optional write callback override for thread-safe output.
                Falls back to ``self.write`` when None (e.g. in tests).
        """
        w = write or self.write
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            prefix = self.prefix
            line_ending = self.cfg.get("line_ending", "\r")
            enc = self.cfg.get("encoding", "utf-8")
            parsed = parse_script_lines(lines, prefix)
            for kind, content in parsed:
                if self._script_stop.is_set():
                    w("Script stopped.")
                    break
                if kind == "skip":
                    continue
                if kind == "repl":
                    name, _, args = content.partition(" ")
                    if name.lower() == "delay":
                        expanded, self._seq_counters = expand_template(
                            args.strip(), self._seq_counters, self._seq_start_time
                        )
                        try:
                            seconds = parse_duration(expanded)
                        except ValueError as e:
                            w(str(e), "red")
                            break
                        time.sleep(seconds)
                        w(f"Delay {expanded} done.")
                    else:
                        self.dispatch(content)
                        time.sleep(0.1)
                elif kind == "serial":
                    if self.ctx.is_connected():
                        self.ctx.serial_write(
                            (content + line_ending).encode(enc)
                        )
                        self.ctx.serial_wait_idle()
            else:
                w("Script finished.")
        except Exception as e:
            w(f"Script error: {e}", "red")
        finally:
            self._in_script = False

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
    def echo(self) -> bool:
        return self._echo

    @property
    def in_script(self) -> bool:
        return self._in_script
