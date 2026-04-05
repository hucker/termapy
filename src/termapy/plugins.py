"""Plugin system for termapy - discovery, loading, and context API.

Plugins are .py files that export a ``COMMAND`` instance describing the
command hierarchy::

    from termapy.plugins import Command

    def _handler(ctx, args):
        ctx.write("Hello from plugin!")

    COMMAND = Command(
        name="mycommand",
        args="{arg1}",
        help="What this command does.",
        handler=_handler,
    )

Subcommands are declared with ``sub_commands``::

    COMMAND = Command(
        name="tool",
        help="A tool with subcommands.",
        sub_commands={
            "run": Command(args="<file>", help="Run a file.", handler=_run),
            "status": Command(help="Show status.", handler=_status),
        },
    )

Users invoke subcommands with dot notation: ``/tool.run myfile``.

Input transforms are declared with ``TRANSFORM``::

    TRANSFORM = Transform(
        name="vars",
        help="Expand $variables in serial commands.",
        serial=lambda s: expand_vars(s),
    )

A file may export both ``COMMAND`` and ``TRANSFORM``.

The PluginContext provides a stable API for plugins to interact with
the terminal, serial port, config, and filesystem without touching
Textual or serial internals.

Load order: built-ins -> global plugins -> per-config plugins.
Later plugins can override earlier ones by using the same name.
"""

from __future__ import annotations

import importlib.util
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, ClassVar, Generator


@dataclass
class CmdResult:
    """Result returned by every plugin/hook handler and by dispatch().

    Handlers return ``CmdResult.ok()`` on success or
    ``CmdResult.fail(msg="...")`` on error.  ``dispatch()`` sets
    ``elapsed_s`` automatically after the handler returns.
    """

    err_prefix: ClassVar[str] = "Error:"  # Set globally: CmdResult.err_prefix = "Fail:"

    success: bool = True
    error: str = ""
    elapsed_s: float = 0.0
    value: str = ""

    @classmethod
    def ok(cls, value: str = "") -> CmdResult:
        """Return a successful result, optionally with a value."""
        return cls(value=value)

    @classmethod
    def fail(cls, msg: str = "") -> CmdResult:
        """Return a failure result with an error message."""
        return cls(success=False, error=msg)

    @property
    def err_msg(self) -> str:
        """Formatted error string with class-level prefix for display."""
        if not self.error:
            return ""
        return f"{self.err_prefix} {self.error}"


@dataclass
class Command:
    """Plugin command declaration.

    Every plugin file must export a ``COMMAND`` instance at module level.
    Root commands require ``name``; sub_commands entries get their name
    from the dict key.

    Attributes:
        help: One-line description shown by ``/help``.
        name: Command name (lowercase). Required at root level, empty
            for sub_commands entries (name comes from the dict key).
        args: Argument spec for help display. ``""`` = no args,
            ``"{opt}"`` = optional, ``"<required>"`` = required.
        long_help: Extended help shown by ``/help <cmd>``.
        handler: The command function. Required for leaf nodes.
            Signature: ``handler(ctx: PluginContext, args: str) -> None``.
        sub_commands: Dict mapping subcommand names to ``Command`` instances.
        raw_args: When True, REPL transforms are skipped for this command.
            Use for commands that take variable names as arguments.
    """

    help: str
    name: str = ""
    args: str = ""
    long_help: str = ""
    handler: Callable | None = None
    sub_commands: dict[str, "Command"] | None = None
    raw_args: bool = False


@dataclass
class DirectiveResult:
    """Result from a pre-dispatch directive handler.

    Attributes:
        action: What to do - ``"rewrite"`` dispatches payload as a REPL
            command, ``"warn"`` shows payload in yellow, ``"error"`` shows
            payload in red, ``"none"`` means no directive matched.
        payload: Command string (for rewrite) or message (for warn/error).
    """

    action: str = "none"
    payload: str = ""


@dataclass
class Directive:
    """Pre-dispatch line rewriter declaration.

    Plugin files that intercept raw input lines before REPL/serial routing
    export a ``DIRECTIVE`` instance at module level.  Directives run in load
    order - built-ins first, then global, then per-config.  A file may export
    ``COMMAND``, ``TRANSFORM``, and/or ``DIRECTIVE``.

    The handler receives the raw input line and returns a ``DirectiveResult``
    or ``None`` to pass to the next directive.

    Attributes:
        name: Identifier for the directive (shown in /help).
        help: One-line description.
        pattern: Human-readable syntax hint (e.g. ``"$(NAME) = value"``).
        handler: ``(str) -> DirectiveResult | None``.
    """

    name: str
    help: str
    pattern: str = ""
    handler: Callable | None = None


@dataclass
class DirectiveInfo:
    """Loaded directive with source metadata.

    Attributes:
        name: Identifier for the directive.
        help: One-line description.
        pattern: Human-readable syntax hint.
        handler: Pre-dispatch rewriter function.
        source: Where the directive was loaded from.
    """

    name: str
    help: str
    pattern: str = ""
    handler: Callable | None = None
    source: str = "built-in"


@dataclass
class Transform:
    """Input rewriter declaration.

    Plugin files that rewrite command input export a ``TRANSFORM`` instance
    at module level.  Transforms run in load order - built-ins first, then
    global, then per-config.  A file may export both ``COMMAND`` and
    ``TRANSFORM``.

    Attributes:
        name: Identifier for the transform (shown in /info listings).
        help: One-line description of what the transform does.
        repl: Rewriter for REPL commands.  ``(str) -> str``.
        serial: Rewriter for device commands.  ``(str) -> str``.
    """

    name: str
    help: str
    repl: Callable | None = None    # (str) -> str, rewrites REPL commands
    serial: Callable | None = None  # (str) -> str, rewrites device commands


@dataclass
class TransformInfo:
    """Loaded transform with source metadata.

    Attributes:
        name: Identifier for the transform.
        help: One-line description.
        repl: REPL rewriter function, or None.
        serial: Serial rewriter function, or None.
        source: Where the transform was loaded from.
    """

    name: str
    help: str
    repl: Callable | None = None
    serial: Callable | None = None
    source: str = "built-in"


@dataclass
class LoadResult:
    """Result of loading plugins from a directory.

    Attributes:
        plugins: Successfully loaded PluginInfo entries.
        skipped: File names that were skipped (no COMMAND instance).
        errors: File names that raised exceptions during loading.
    """

    plugins: list = field(default_factory=list)
    transforms: list = field(default_factory=list)
    directives: list = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class EngineAPI:
    """Engine internals exposed to built-in plugins only.

    External plugins should not use this - it may change between versions.
    Access via ctx.engine from built-in command handlers.
    """

    prefix: str = "/"
    plugins: dict = field(default_factory=dict)
    get_echo: Callable = lambda: True
    set_echo: Callable = lambda val: None
    get_seq_counters: Callable = lambda: {}
    set_seq_counters: Callable = lambda val: None
    reset_seq: Callable = lambda: None
    in_script: Callable = lambda: False
    script_stop: Callable = lambda: None
    save_cfg: Callable | None = None  # (key, val) -> confirm dialog; None = no confirm
    apply_cfg: Callable = lambda key, val: None
    coerce_type: Callable = lambda val, existing: val
    get_hex_mode: Callable = lambda: False
    set_hex_mode: Callable = lambda enabled: None
    set_proto_active: Callable = lambda active: None
    open_proto_debug: Callable = lambda path, script: None
    start_capture: Callable = lambda **kw: None
    stop_capture: Callable = lambda: None
    directives: list = field(default_factory=list)
    target_commands: dict = field(default_factory=dict)
    set_target_commands: Callable = lambda cmds: None
    clear_target_commands: Callable = lambda: None
    connect: Callable = lambda port=None: None
    disconnect: Callable = lambda: None
    update_port: Callable = lambda name: None
    apply_port_effects: Callable = lambda effects: None
    rx_queue: Any = None  # queue.Queue[bytes] — raw RX for protocol handlers


@dataclass
class PluginContext:
    """Stable API for plugin interaction with the terminal.

    Every plugin handler receives a PluginContext as its first argument.
    This is the only interface plugins should use - it insulates them
    from Textual, pyserial, and internal engine details.

    Attributes:
        write: Output text to the terminal. Signature: ``write(text, color="dim")``.
            Color can be any Rich color name (e.g. ``"red"``, ``"green"``, ``"dim"``).
        write_markup: Output Rich markup text to the terminal. Signature:
            ``write_markup(text)``. Supports Rich markup tags like
            ``[bold red]text[/]``.
        log: Write a timestamped line to the session log file. Signature:
            ``log(prefix, text)`` where prefix is ``">"`` (TX), ``"<"`` (RX),
            or ``"#"`` (status). Independent of screen output - always logged
            regardless of echo settings.
        cfg: Read-only config dict (``MappingProxyType``). Access any config
            field with ``ctx.cfg.get("key", default)``. Do not mutate.
        config_path: Absolute path to the current JSON config file on disk.
        port: The underlying pyserial ``Serial`` object (or ``None`` when
            disconnected). Returns the live object - properties like
            ``ctx.port().baudrate``, ``ctx.port().dtr``, etc. reflect current
            state. This is a callable; use ``ctx.port()`` not ``ctx.port``.
        is_connected: Returns ``True`` if the serial port is open.
        serial_write: Send raw bytes to the serial port. No line ending is
            appended - pass exactly the bytes you want transmitted.
        serial_wait_idle: Block until the serial port has been quiet for ~400ms.
            Useful in scripts to wait for a device response before the next command.
        serial_read_raw: Collect raw bytes from the serial port with timeout-based
            framing. Signature: ``serial_read_raw(timeout_ms=1000) -> bytes``.
            Returns a complete frame (bytes) or ``b""`` on timeout.
        serial_claim: Suppress normal terminal display and claim exclusive access
            to incoming serial bytes. Low-level primitive - prefer ``serial_io()``
            context manager instead.
        serial_release: Resume normal terminal display. Low-level primitive -
            prefer ``serial_io()`` context manager instead.
        ss_dir: Path to the per-config screenshots directory (auto-created).
        scripts_dir: Path to the per-config scripts directory (auto-created).
        proto_dir: Path to the per-config protocol test scripts directory (auto-created).
        cap_dir: Path to the per-config cap/ directory (auto-created).
        prof_dir: Path to the per-config prof/ directory (auto-created).
        dispatch: Route a raw command through the full dispatch pipeline
            (directives, transforms, REPL/serial). Signature: ``dispatch(cmd)``.
            Thread-safe when called via ``call_from_thread``.
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
        engine: Internal engine API (``EngineAPI``). **Built-in plugins only** -
            this is unstable and may change between versions.
    """

    # Core I/O
    write: Callable             # write(text, color="dim") -> None
    write_markup: Callable = lambda text: None  # write(text) with Rich markup
    cfg: MappingProxyType | dict = field(default_factory=dict)
    config_path: str = ""

    # Logging - log(prefix, text) writes a timestamped line to the session log.
    # Prefixes: ">" TX (commands sent), "<" RX (device responses), "#" status.
    log: Callable = lambda prefix, text: None

    # Serial port
    port: Callable = lambda: None   # -> serial.Serial | None
    is_connected: Callable = lambda: False
    serial_write: Callable = lambda data: None
    serial_send: Callable = lambda text: None  # send text with configured line ending + encoding
    serial_wait_for_data: Callable = lambda timeout_ms=250: False  # wait for first byte
    serial_wait_idle: Callable = lambda timeout_ms=400: None
    serial_read_raw: Callable = lambda timeout_ms=1000, frame_gap_ms=0: b""
    serial_drain: Callable = lambda: 0
    serial_claim: Callable = lambda: None    # suppress terminal display, claim raw bytes
    serial_release: Callable = lambda: None  # resume normal terminal display
    wait_for_match: Callable = lambda predicate, timeout=5.0: None  # block until line matches

    # Filesystem
    ss_dir: Path = field(default_factory=lambda: Path("."))
    scripts_dir: Path = field(default_factory=lambda: Path("."))
    proto_dir: Path = field(default_factory=lambda: Path("."))
    cap_dir: Path = field(default_factory=lambda: Path("."))
    prof_dir: Path = field(default_factory=lambda: Path("."))

    # Dispatch - route a raw command through the full dispatch pipeline
    # (directives, transforms, REPL/serial). Thread-safe when wired via
    # call_from_thread in app.py.
    dispatch: Callable = lambda cmd: None  # dispatch(cmd) -> None

    # UI
    confirm: Callable = lambda message: False  # confirm(msg) -> bool (worker thread only)
    notify: Callable = lambda text, **kw: None
    clear_screen: Callable = lambda: None
    save_screenshot: Callable = lambda path: None
    get_screen_text: Callable = lambda: ""
    open_file: Callable = lambda path: None  # open file in system viewer/editor
    exit_app: Callable = lambda: None

    # Engine internals - used by built-in commands only
    engine: EngineAPI = field(default_factory=EngineAPI)

    # Verbose flag - controls ctx.status() visibility
    verbose: bool = True

    # -- Output channels -------------------------------------------------------

    def result(self, text: str, color: str = "green") -> None:
        """Write a command result (single-line answer). Always shown."""
        self.write(text, color)

    def output(self, text: str, color: str = "dim") -> None:
        """Write data output (listings, dumps, file contents). Always shown."""
        self.write(text, color)

    def status(self, text: str) -> None:
        """Write a status/progress message. Suppressed when verbose is off."""
        if self.verbose:
            self.write(text, "dim")

    @contextmanager
    def serial_io(self) -> Generator[None, None, None]:
        """Claim exclusive serial access, suppressing terminal display.

        While active, incoming bytes are queued for ``serial_read_raw()``
        instead of being displayed in the terminal.  Use this around any
        drain -> write -> read cycle so responses are captured reliably.

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
    """Metadata and handler for a single plugin command or subcommand.

    Attributes:
        name: Dotted command path (lowercase). Users type ``/name`` or
            ``/parent.child`` to invoke.
        args: Argument spec for help display. ``""`` = no args,
            ``"{opt}"`` = optional, ``"<required>"`` = required.
        help: One-line description shown by ``/help``.
        long_help: Extended help shown by ``/help <cmd>``. May span multiple
            lines. When empty, the one-line ``help`` is shown instead.
        handler: The command function. Signature:
            ``handler(ctx: PluginContext, args: str) -> None``.
        source: Where the plugin was loaded from (``"built-in"``, ``"global"``,
            or the config name).
        children: Dotted names of direct subcommands (empty for leaf commands).
        raw_args: When True, REPL transforms are skipped for this command.
    """

    name: str
    args: str
    help: str
    handler: Callable   # handler(ctx: PluginContext, args: str) -> None
    long_help: str = ""
    source: str = "built-in"
    children: list[str] = field(default_factory=list)
    raw_args: bool = False


@dataclass
class TargetCommand:
    """Help-only command imported from a connected device.

    These are NOT REPL commands -- they have no handler and no prefix.
    They appear in help output and suggestions only.

    Attributes:
        name: Command name as the device expects it (no / prefix).
        help: One-line description.
        args: Argument spec string (may be empty).
    """

    name: str
    help: str
    args: str = ""


def builtins_dir() -> Path:
    """Return the path to the built-in plugins directory shipped with termapy."""
    return Path(__file__).parent / "builtins" / "plugins"


def load_plugins_from_dir(folder: Path, source: str = "global") -> LoadResult:
    """Discover and load plugin .py files from a directory.

    Each file may export a ``COMMAND`` (Command dataclass) and/or a
    ``TRANSFORM`` (Transform dataclass).  Files starting with '_' are
    skipped.

    Args:
        folder: Directory to scan for .py plugin files.
        source: Label for where the plugin came from (e.g. "global", config name).

    Returns:
        LoadResult with plugins, transforms, skipped file names, and error file names.
    """
    result = LoadResult()
    if not folder.is_dir():
        return result
    for py_file in sorted(folder.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        try:
            infos, xforms, dirs = _load_plugin_file(py_file, source)
            if infos:
                result.plugins.extend(infos)
            if xforms:
                result.transforms.extend(xforms)
            if dirs:
                result.directives.extend(dirs)
            if not infos and not xforms and not dirs:
                result.skipped.append(py_file.name)
        except Exception as e:
            result.errors.append(f"{py_file.name}: {e}")
    return result


def _load_plugin_file(
    path: Path, source: str,
) -> tuple[list[PluginInfo], list[TransformInfo], list[DirectiveInfo]]:
    """Import a single plugin file and extract commands, transforms, and directives.

    A valid plugin module may export a ``COMMAND`` instance (a ``Command``
    dataclass), a ``TRANSFORM`` instance (a ``Transform`` dataclass),
    and/or a ``DIRECTIVE`` instance (a ``Directive`` dataclass).

    Args:
        path: Path to the .py plugin file.
        source: Label for the plugin's origin.

    Returns:
        Tuple of (PluginInfo list, TransformInfo list, DirectiveInfo list).
    """
    # Derive the package name if this is a builtin plugin, so the module
    # is registered under both the dynamic name and the package path.
    # This prevents duplicate module state when app.py/cli.py imports
    # builtins via the package path (e.g. termapy.builtins.plugins.var).
    module_name = f"termapy_plugin_{path.stem}"
    pkg_name = None
    try:
        builtins_root = Path(__file__).parent / "builtins"
        rel = path.resolve().relative_to(builtins_root.resolve())
        parts = list(rel.parent.parts) + [rel.stem]
        pkg_name = "termapy.builtins." + ".".join(parts)
    except ValueError:
        pass

    # If already loaded via package import, reuse that module
    if pkg_name and pkg_name in sys.modules:
        mod = sys.modules[pkg_name]
    else:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            return [], [], []
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        if pkg_name:
            sys.modules[pkg_name] = mod
        spec.loader.exec_module(mod)

    # Commands
    plugins: list[PluginInfo] = []
    cmd = getattr(mod, "COMMAND", None)
    if isinstance(cmd, Command) and cmd.name:
        plugins = _flatten_command(cmd, prefix="", source=source)

    # Transforms
    transforms: list[TransformInfo] = []
    xform = getattr(mod, "TRANSFORM", None)
    if isinstance(xform, Transform) and xform.name:
        transforms.append(TransformInfo(
            name=xform.name,
            help=xform.help,
            repl=xform.repl,
            serial=xform.serial,
            source=source,
        ))

    # Directives
    directives: list[DirectiveInfo] = []
    directive = getattr(mod, "DIRECTIVE", None)
    if isinstance(directive, Directive) and directive.name:
        directives.append(DirectiveInfo(
            name=directive.name,
            help=directive.help,
            pattern=directive.pattern,
            handler=directive.handler,
            source=source,
        ))

    return plugins, transforms, directives


def _flatten_command(
    node: Command,
    prefix: str,
    source: str,
) -> list[PluginInfo]:
    """Recursively flatten a Command tree into PluginInfo entries.

    Each node in the tree becomes a PluginInfo. Interior nodes (those
    with ``sub_commands``) get a synthetic handler that lists their
    subcommands. Leaf nodes must have a ``handler`` callable.

    Args:
        node: Command instance with name/help/handler/sub_commands.
        prefix: Dotted path prefix (empty for root).
        source: Plugin source label.

    Returns:
        List of PluginInfo for this node and all descendants.
    """
    name = node.name
    full_name = f"{prefix}.{name}".lower() if prefix else name.lower()
    sub_commands = node.sub_commands or {}
    children: list[str] = []
    result: list[PluginInfo] = []

    # Recurse into sub_commands first so we can build the children list
    for sub_name, sub_node in sub_commands.items():
        # Set name on sub-node so recursion works uniformly
        sub_node.name = sub_name
        child_infos = _flatten_command(sub_node, full_name, source)
        result.extend(child_infos)
        children.append(f"{full_name}.{sub_name}".lower())

    handler = node.handler
    if not handler and children:
        # Synthetic handler for interior nodes - lists subcommands
        handler = _make_interior_handler(full_name, children)

    if not handler:
        return result

    info = PluginInfo(
        name=full_name,
        args=node.args,
        help=node.help,
        long_help=node.long_help,
        handler=handler,
        source=source,
        children=children,
        raw_args=node.raw_args,
    )
    result.insert(0, info)
    return result


def _make_interior_handler(
    full_name: str, children: list[str],
) -> Callable:
    """Create a synthetic handler for an interior command node.

    The handler lists available subcommands when the user invokes the
    interior node directly (e.g. ``/proto`` with no subcommand).

    Args:
        full_name: Dotted command path (e.g. "proto").
        children: Dotted names of direct subcommands.

    Returns:
        A handler callable with the standard (ctx, args) signature.
    """
    def _handler(ctx: PluginContext, args: str) -> None:
        prefix = ctx.engine.prefix
        ctx.write(f"Subcommands of {prefix}{full_name}:")
        plugins = ctx.engine.plugins
        for child_name in children:
            child = plugins.get(child_name)
            if child:
                arg_str = f" {child.args}" if child.args else ""
                ctx.write(f"  {prefix}{child_name}{arg_str} - {child.help}")
    return _handler
