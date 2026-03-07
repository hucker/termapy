"""Plugin system for terapy — discovery, loading, and context API.

Plugins are .py files with a standard interface:

    NAME = "mycommand"
    ARGS = "[arg1]"
    HELP = "What this command does."

    def handler(ctx, args: str):
        ctx.write("Hello from plugin!")

The PluginContext provides a stable API for plugins to interact with
the terminal, serial port, config, and filesystem without touching
Textual or serial internals.

Load order: built-ins -> global plugins -> per-config plugins.
Later plugins can override earlier ones by using the same NAME.
"""

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class EngineAPI:
    """Engine internals exposed to built-in plugins only.

    External plugins should not use this — it may change between versions.
    Access via ctx.engine from built-in command handlers.
    """

    prefix: str = "!!"
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


@dataclass
class PluginContext:
    """Capabilities exposed to plugins. Plugins receive this as their first arg."""

    # Core I/O
    write: Callable             # write(text, color="dim") -> None
    cfg: dict = field(default_factory=dict)
    config_path: str = ""

    # Serial port
    is_connected: Callable = lambda: False
    serial_write: Callable = lambda data: None
    serial_wait_idle: Callable = lambda: None

    # Filesystem
    ss_dir: Path = field(default_factory=lambda: Path("."))
    scripts_dir: Path = field(default_factory=lambda: Path("."))

    # UI
    notify: Callable = lambda text, **kw: None
    clear_screen: Callable = lambda: None
    save_screenshot: Callable = lambda path: None
    get_screen_text: Callable = lambda: ""

    # Engine internals — used by built-in commands only
    engine: EngineAPI = field(default_factory=EngineAPI)


@dataclass
class PluginInfo:
    """Metadata and handler for a single plugin command."""

    name: str
    args: str
    help: str
    handler: Callable   # handler(ctx: PluginContext, args: str) -> None
    source: str = "built-in"  # "built-in", "global", or config name


def builtins_dir() -> Path:
    """Return the path to the built-in plugins directory shipped with terapy."""
    return Path(__file__).parent / "builtins"


def load_plugins_from_dir(folder: Path, source: str = "global") -> list[PluginInfo]:
    """Discover and load plugin .py files from a directory.

    Each file must define NAME, ARGS, HELP, and handler().
    Returns a list of PluginInfo, one per valid plugin file.
    Files that fail to load are skipped with a warning printed to stderr.
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
            print(f"terapy: failed to load plugin {py_file.name}: {e}", file=sys.stderr)
    return plugins


def _load_plugin_file(path: Path, source: str) -> PluginInfo | None:
    """Import a single plugin file and extract its PluginInfo."""
    module_name = f"terapy_plugin_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
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
        handler=handler,
        source=source,
    )
