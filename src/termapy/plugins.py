"""Plugin system for termapy — discovery, loading, and context API.

Plugins are .py files with a standard interface:

    from termapy.plugins import PluginContext

    NAME = "mycommand"
    ARGS = "[arg1]"
    HELP = "What this command does."

    def handler(ctx: PluginContext, args: str):
        ctx.write("Hello from plugin!")

The PluginContext provides a stable API for plugins to interact with
the terminal, serial port, config, and filesystem without touching
Textual or serial internals.

Load order: built-ins -> global plugins -> per-config plugins.
Later plugins can override earlier ones by using the same NAME.
"""

import importlib.util
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Generator


@dataclass
class EngineAPI:
    """Engine internals exposed to built-in plugins only.

    External plugins should not use this — it may change between versions.
    Access via ctx.engine from built-in command handlers.
    """

    prefix: str = "!"
    plugins: dict = field(default_factory=dict)
    get_echo: Callable = lambda: True
    set_echo: Callable = lambda val: None
    get_seq_counters: Callable = lambda: {}
    set_seq_counters: Callable = lambda val: None
    reset_seq: Callable = lambda: None
    in_script: Callable = lambda: False
    script_stop: Callable = lambda: None
    save_cfg: Callable = None       # (key, val) -> confirm dialog; None = no confirm
    apply_cfg: Callable = lambda key, val: None
    coerce_type: Callable = lambda val, existing: val
    get_hex_mode: Callable = lambda: False
    set_hex_mode: Callable = lambda enabled: None
    set_proto_active: Callable = lambda active: None
    open_proto_debug: Callable = lambda path, script: None


@dataclass
class PluginContext:
    """Stable API for plugin interaction with the terminal.

    Every plugin handler receives a PluginContext as its first argument.
    This is the only interface plugins should use — it insulates them
    from Textual, pyserial, and internal engine details.

    Attributes:
        write: Output text to the terminal. Signature: ``write(text, color="dim")``.
            Color can be any Rich color name (e.g. ``"red"``, ``"green"``, ``"dim"``).
        write_markup: Output Rich markup text to the terminal. Signature:
            ``write_markup(text)``. Supports Rich markup tags like
            ``[bold red]text[/]``.
        cfg: Read-only config dict (``MappingProxyType``). Access any config
            field with ``ctx.cfg.get("key", default)``. Do not mutate.
        config_path: Absolute path to the current JSON config file on disk.
        is_connected: Returns ``True`` if the serial port is open.
        serial_write: Send raw bytes to the serial port. No line ending is
            appended — pass exactly the bytes you want transmitted.
        serial_wait_idle: Block until the serial port has been quiet for ~400ms.
            Useful in scripts to wait for a device response before the next command.
        serial_read_raw: Collect raw bytes from the serial port with timeout-based
            framing. Signature: ``serial_read_raw(timeout_ms=1000) -> bytes``.
            Returns a complete frame (bytes) or ``b""`` on timeout.
        serial_claim: Suppress normal terminal display and claim exclusive access
            to incoming serial bytes. Low-level primitive — prefer ``serial_io()``
            context manager instead.
        serial_release: Resume normal terminal display. Low-level primitive —
            prefer ``serial_io()`` context manager instead.
        ss_dir: Path to the per-config screenshots directory (auto-created).
        scripts_dir: Path to the per-config scripts directory (auto-created).
        proto_dir: Path to the per-config protocol test scripts directory (auto-created).
        confirm: Show a Yes/Cancel confirmation dialog and return the result.
            Signature: ``confirm(message) -> bool``. **Must be called from a
            background thread** (e.g. inside a ``@work(thread=True)`` handler).
            Blocks the calling thread until the user responds.
        notify: Show a toast notification. Signature: ``notify(text, **kw)``.
            Keyword args are passed to Textual's ``App.notify()``.
        clear_screen: Clear the terminal output and reset the line counter.
        save_screenshot: Save the terminal view. Signature: ``save_screenshot(path)``.
        get_screen_text: Return all visible terminal output as a plain-text string.
        exit_app: Exit the application.
        engine: Internal engine API (``EngineAPI``). **Built-in plugins only** —
            this is unstable and may change between versions.
    """

    # Core I/O
    write: Callable             # write(text, color="dim") -> None
    write_markup: Callable = lambda text: None  # write(text) with Rich markup
    cfg: MappingProxyType | dict = field(default_factory=dict)
    config_path: str = ""

    # Serial port
    is_connected: Callable = lambda: False
    serial_write: Callable = lambda data: None
    serial_wait_idle: Callable = lambda timeout_ms=400: None
    serial_read_raw: Callable = lambda timeout_ms=1000, frame_gap_ms=0: b""
    serial_drain: Callable = lambda: 0
    serial_claim: Callable = lambda: None    # suppress terminal display, claim raw bytes
    serial_release: Callable = lambda: None  # resume normal terminal display

    # Filesystem
    ss_dir: Path = field(default_factory=lambda: Path("."))
    scripts_dir: Path = field(default_factory=lambda: Path("."))
    proto_dir: Path = field(default_factory=lambda: Path("."))

    # UI
    confirm: Callable = lambda message: False  # confirm(msg) -> bool (worker thread only)
    notify: Callable = lambda text, **kw: None
    clear_screen: Callable = lambda: None
    save_screenshot: Callable = lambda path: None
    get_screen_text: Callable = lambda: ""
    exit_app: Callable = lambda: None

    # Engine internals — used by built-in commands only
    engine: EngineAPI = field(default_factory=EngineAPI)

    @contextmanager
    def serial_io(self) -> Generator[None, None, None]:
        """Claim exclusive serial access, suppressing terminal display.

        While active, incoming bytes are queued for ``serial_read_raw()``
        instead of being displayed in the terminal.  Use this around any
        drain → write → read cycle so responses are captured reliably.

        Usage::

            with ctx.serial_io():
                ctx.serial_drain()
                ctx.serial_write(payload)
                response = ctx.serial_read_raw()
        """
        self.serial_claim()
        try:
            yield
        finally:
            self.serial_release()


@dataclass
class PluginInfo:
    """Metadata and handler for a single plugin command.

    Attributes:
        name: Command name (lowercase). Users type ``!name`` to invoke.
        args: Argument spec for help display. ``""`` = no args,
            ``"{opt}"`` = optional, ``"<required>"`` = required.
        help: One-line description shown by ``!help``.
        long_help: Extended help shown by ``!help <cmd>``. May span multiple
            lines. When empty, the one-line ``help`` is shown instead.
        handler: The command function. Signature: ``handler(ctx: PluginContext, args: str) -> None``.
        source: Where the plugin was loaded from (``"built-in"``, ``"global"``,
            or the config name).
    """

    name: str
    args: str
    help: str
    handler: Callable   # handler(ctx: PluginContext, args: str) -> None
    long_help: str = ""
    source: str = "built-in"


def builtins_dir() -> Path:
    """Return the path to the built-in plugins directory shipped with termapy."""
    return Path(__file__).parent / "builtins" / "plugins"


def load_plugins_from_dir(folder: Path, source: str = "global") -> list[PluginInfo]:
    """Discover and load plugin .py files from a directory.

    Each file must define NAME, ARGS, HELP, and handler(). Files starting
    with '_' are skipped. Files that fail to load print a warning to stderr.

    Args:
        folder: Directory to scan for .py plugin files.
        source: Label for where the plugin came from (e.g. "global", config name).

    Returns:
        List of PluginInfo, one per valid plugin file found.
    """
    plugins = []
    if not folder.is_dir():
        return plugins
    for py_file in sorted(folder.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        try:
            info = _load_plugin_file(py_file, source)
            if info:
                plugins.append(info)
        except Exception as e:
            print(f"termapy: failed to load plugin {py_file.name}: {e}", file=sys.stderr)
    return plugins


def _load_plugin_file(path: Path, source: str) -> PluginInfo | None:
    """Import a single plugin file and extract its PluginInfo.

    A valid plugin module must define at minimum ``NAME`` (str) and
    ``handler`` (callable). Optional: ``ARGS`` (str), ``HELP`` (str),
    ``PACKAGE`` (str, for namespaced commands like ``pkg.cmd``).

    Args:
        path: Path to the .py plugin file.
        source: Label for the plugin's origin.

    Returns:
        PluginInfo if the file is a valid plugin, None otherwise.
    """
    module_name = f"termapy_plugin_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)

    name = getattr(mod, "NAME", None)
    handler = getattr(mod, "handler", None)
    if not name or not handler:
        return None

    package = getattr(mod, "PACKAGE", None)
    full_name = f"{package}.{name}".lower() if package else name.lower()

    return PluginInfo(
        name=full_name,
        args=getattr(mod, "ARGS", ""),
        help=getattr(mod, "HELP", ""),
        long_help=getattr(mod, "LONG_HELP", ""),
        handler=handler,
        source=source,
    )
