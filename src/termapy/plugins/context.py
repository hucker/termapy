"""PluginContext, EngineAPI, and PluginConfig.

The runtime objects every plugin handler interacts with:

  - ``PluginContext`` -- the single argument every handler receives.
    Stable façade over Textual / pyserial / threading internals.
  - ``EngineAPI`` -- privileged escape hatch reachable as ``ctx.engine``.
    Built-in plugins only; unstable.
  - ``PluginConfig`` -- per-plugin persistent JSON config object.

The output-level vocabulary (``OUTPUT_LEVELS``, ``LEVEL_FLAGS``,
``parse_output_level``, ``format_kv_lines``) lives in
:mod:`termapy.plugins.output_levels` so both this module and
:class:`IOHandle` can reach it without circular imports.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Generator, TYPE_CHECKING

from termapy.plugins.capabilities import CapabilitySet
from termapy.plugins.handles.engine import EngineAPI, EngineHandle
from termapy.plugins.output_levels import (
    DEFAULT_OUTPUT_LEVEL,
    LEVEL_FLAGS,
    OUTPUT_LEVEL_RANK,
    OUTPUT_LEVELS,
    OUTPUT_MIN_RANK,
    RESULT_MIN_RANK,
    STATUS_MIN_RANK,
    format_kv_lines,
    parse_output_level,
)

if TYPE_CHECKING:
    from termapy.plugins.handles.fs import FilesystemHandle
    from termapy.plugins.handles.io import IOHandle
    from termapy.plugins.handles.serial import SerialHandle
    from termapy.plugins.handles.ui import UIHandle


# ─────────────────────────────────────────────────────────────────────────────
# PluginConfig: persistent per-plugin JSON storage
# ─────────────────────────────────────────────────────────────────────────────


class PluginConfig:
    """Persistent per-config key-value storage for a plugin.

    Each plugin's config is a JSON file at a deterministic path::

        termapy_cfg/<config>/plugin/<plugin_name>.cfg

    The config is loaded lazily on first access and cached in memory.
    Call ``save()`` to write changes to disk.

    Usage::

        def _handler(ctx, args):
            cfg = ctx.plugin_cfg("pic_map")
            cfg["map_path"] = "/path/to/mem.map"
            cfg.save()

            # Read back
            path = cfg.get("map_path", "")

    The dict-like interface supports ``get()``, ``[]``, ``[]=``,
    ``pop()``, ``in``, ``del``, and iteration.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict | None = None

    @property
    def path(self) -> Path:
        """The on-disk path to this config file."""
        return self._path

    def _ensure_loaded(self) -> dict:
        if self._data is None:
            if self._path.exists():
                try:
                    self._data = json.loads(self._path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    self._data = {}
            else:
                self._data = {}
        return self._data

    def save(self) -> None:
        """Write the current config to disk.

        Creates the parent directory if needed.

        Raises:
            OSError: If the file cannot be written.
        """
        data = self._ensure_loaded()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value, returning *default* if the key is absent."""
        return self._ensure_loaded().get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self._ensure_loaded()[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._ensure_loaded()[key] = value

    def __delitem__(self, key: str) -> None:
        del self._ensure_loaded()[key]

    def __contains__(self, key: str) -> bool:
        return key in self._ensure_loaded()

    def __iter__(self):
        return iter(self._ensure_loaded())

    def __len__(self) -> int:
        return len(self._ensure_loaded())

    def pop(self, key: str, *args: Any) -> Any:
        """Remove and return a value.  Accepts an optional default."""
        return self._ensure_loaded().pop(key, *args)

    def items(self):
        """Return key-value pairs."""
        return self._ensure_loaded().items()

    def __repr__(self) -> str:
        return f"PluginConfig({self._path})"


# ─────────────────────────────────────────────────────────────────────────────
# PluginContext: the stable façade every plugin handler receives
# ─────────────────────────────────────────────────────────────────────────────


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
        engine: Privileged escape hatch (``EngineAPI``).  Textual, threading,
            and pyserial handles that cannot be generified.  **Built-in
            plugins only** -- unstable, may change between versions.  For
            session state, prefer ``ctx.ns()``.
        ns: Return a session-scoped namespace dict, creating it on first
            access.  The supported API for storing per-session state in
            both built-in and third-party plugins.  See ``PluginContext.ns``.
            Engine toggles like ``echo``, ``hex_mode``, and ``output_level``
            live in the reserved ``flags`` namespace; plugins should use their
            own namespace name (e.g. ``ctx.ns("myplugin")``) to avoid
            collision.
        plugin_cfg: Return a persistent ``PluginConfig`` object for a named
            plugin.  The config file is stored at
            ``termapy_cfg/<config>/plugin/<name>.cfg``.  Loaded lazily,
            cached per session.  Call ``.save()`` to write to disk.
            Use ``ns()`` for session-only state, ``plugin_cfg()`` for
            state that must survive across sessions.
        status_bar: Show transient text in the bottom status bar (TUI only).
            The text shares space with the REPL input and auto-clears after
            a timeout (default 5 seconds).  No-op in CLI mode.
            Signature: ``status_bar(text, timeout=5.0)``.

    Serial observers (RX/TX byte taps) are reached through the
    :meth:`rx_observer` and :meth:`tx_observer` context managers; the bare
    register/unregister methods are intentionally not part of the public
    surface so exceptions can't leak observers.
    """

    # Core I/O
    write: Callable  # write(text, color="dim") -> None
    write_markup: Callable = lambda text: None  # write(text) with Rich markup
    cfg: MappingProxyType | dict = field(default_factory=dict)
    config_path: str = ""

    # Logging - log(prefix, text) writes a timestamped line to the session log.
    # Prefixes: ">" TX (commands sent), "<" RX (device responses), "#" status.
    log: Callable = lambda prefix, text: None

    # Serial port
    port: Callable = lambda: None  # -> serial.Serial | None
    is_connected: Callable = lambda: False
    # True when the host is running a one-shot mode (--run or --exec):
    # plugins should suppress chatter / banners that would corrupt
    # captured stdout.  False in interactive TUI / CLI REPL.
    is_oneshot: Callable = lambda: False
    serial_write: Callable = lambda data: None
    serial_send: Callable = (
        lambda text: None
    )  # send text with configured line ending + encoding
    serial_wait_for_data: Callable = lambda timeout_ms=250: False  # wait for first byte
    serial_wait_idle: Callable = lambda timeout_ms=400: None
    serial_read_raw: Callable = lambda timeout_ms=1000, frame_gap_ms=0: b""
    serial_drain: Callable = lambda: 0
    serial_claim: Callable = lambda: None  # suppress terminal display, claim raw bytes
    serial_release: Callable = lambda: None  # resume normal terminal display
    wait_for_match: Callable = (
        lambda predicate, timeout=5.0: None
    )  # block until line matches

    # Status bar - transient text in the bottom bar (TUI only, no-op in CLI)
    status_bar: Callable = lambda text, timeout=5.0: None

    # Serial observers - see raw bytes without disrupting the pipeline.
    # Underscore-prefixed: ``ctx.rx_observer(cb)`` and ``ctx.tx_observer(cb)``
    # context managers are the public path; bare register/unregister stays
    # private so leaks (observer never removed on exception) are
    # structurally impossible from plugin code.
    _add_rx_observer: Callable = lambda cb: None
    _remove_rx_observer: Callable = lambda cb: None
    _add_tx_observer: Callable = lambda cb: None
    _remove_tx_observer: Callable = lambda cb: None

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
    confirm: Callable = (
        lambda message: False
    )  # confirm(msg) -> bool (worker thread only)
    notify: Callable = lambda text, **kw: None
    clear_screen: Callable = lambda: None
    save_screenshot: Callable = lambda path: None
    get_screen_text: Callable = lambda: ""
    open_file: Callable = lambda path: None  # open file in system viewer/editor
    exit_app: Callable = lambda: None

    # Engine internals - used by built-in commands only
    engine: EngineAPI = field(default_factory=EngineAPI)

    # Per-dispatch flag set - populated by ReplEngine.dispatch from the
    # invoking command's declared Command.flags before the handler runs.
    # Reset on every dispatch. Handlers read via ``ctx.flag("--name")``
    # which normalizes aliases. Distinct from the ``ns("flags")``
    # namespace (echo/hex_mode toggles).
    active_flags: set[str] = field(default_factory=set)

    # Per-call output-level override.  None means "use the global level
    # from ctx.ns('flags')['output_level']".  Set by ReplEngine.dispatch
    # when the user invokes a command with a level suffix or flag
    # (e.g. ``cmd.quiet`` or ``cmd --silent``); cleared after dispatch.
    _call_level: str | None = None

    # Capabilities this environment provides.  Dispatch compares a
    # command's declared ``needs`` against this set before calling the
    # handler and fails with a clear message if anything is missing.
    # The REPL, script runner, CLI, TUI, and test harness each publish
    # their own capabilities when the context is constructed.  See the
    # ``CapabilitySet`` docstring for the full capability vocabulary.
    capabilities: CapabilitySet = field(default_factory=CapabilitySet)

    # Namespace registry - plugin/builtin session-scoped state.
    # See ctx.ns() below for the public interface.
    _namespaces: dict[str, dict] = field(default_factory=dict)

    # Plugin config cache - keyed by plugin name, lazy-loaded from disk.
    _plugin_cfgs: dict[str, PluginConfig] = field(default_factory=dict)

    # ── Capability-domain handles (properties, not fields) ────────────────────
    #
    # ``ctx.io`` / ``ctx.serial`` / ``ctx.fs`` / ``ctx.ui`` are namespaced
    # views of the flat fields above, exposed as properties so they are
    # NOT part of the dataclass field set.  This matters because some
    # tests clone a context via ``ctx.__class__(**ctx.__dict__)``; making
    # the handles dataclass fields would break that pattern.
    #
    # Each handle is built on access; the handle holds a ``self`` ref and
    # delegates to ``ctx.<flat>`` so post-construction overrides
    # (``ctx.write = self._status``) flow through live.
    #
    # During the dual-API transition both forms are valid and reach the
    # same backing callable:
    #   ctx.write("hi", "red")        # flat field
    #   ctx.io.write("hi", "red")     # handle method
    #
    # Phase 3 of the refactor removes the flat fields; only the handles
    # will remain.

    @property
    def io(self) -> "IOHandle":
        """Output operations: write/markup/log/result/output/status/notify/status_bar/clear_screen."""
        from termapy.plugins.handles.io import IOHandle
        return IOHandle(self)

    @property
    def serial(self) -> "SerialHandle":
        """Serial I/O: send/write/read_raw/drain/io()/rx_observer/tx_observer."""
        from termapy.plugins.handles.serial import SerialHandle
        return SerialHandle(self)

    @property
    def fs(self) -> "FilesystemHandle":
        """Per-config directories + ``open_file`` (gated by ``gui_apps``)."""
        from termapy.plugins.handles.fs import FilesystemHandle
        return FilesystemHandle(self)

    @property
    def ui(self) -> "UIHandle":
        """TUI-strict ops: confirm, screenshot, exit_app, ... (capability-gated)."""
        from termapy.plugins.handles.ui import UIHandle
        return UIHandle(self)

    # -- Namespaces ------------------------------------------------------------

    def ns(self, name: str) -> dict:
        """Return a session-scoped namespace dict, creating it on first access.

        Namespaces are uniform mutable ``dict`` s keyed by name.  They live for
        the lifetime of the ``PluginContext`` (one app session) and are the
        supported way for both built-in and third-party plugins to keep
        per-session state.  Prefer this over monkey-patching ``ctx`` or using
        module-level globals.

        Namespaces are not isolated -- any caller can read or write any
        namespace by name.  The naming convention is collision avoidance, not
        access control.  Plugins that publish state for other plugins to read
        should document their key schema.

        The ``flags`` namespace is engine-reserved for toggles like ``echo``,
        ``hex_mode``, and the ``output_level`` dial.  Third-party plugins
        should use their own namespace name (conventionally the plugin name).

        Example::

            def _handler(ctx, args):
                store = ctx.ns("myplugin")
                store["requests_sent"] = store.get("requests_sent", 0) + 1
                ctx.write(f"sent {store['requests_sent']} requests")

        Args:
            name: Namespace identifier.  Created empty on first access.

        Returns:
            The namespace dict.  Mutations persist for the life of the
            ``PluginContext``.  Successive calls with the same name return
            the same dict.
        """
        if name not in self._namespaces:
            self._namespaces[name] = {}
        return self._namespaces[name]

    def flag(self, name: str) -> bool:
        """Return True if the given flag was passed on the invoking command.

        Handlers declare flags on their ``Command(flags={...})`` dict; the
        dispatcher strips them from the args string and records them in
        ``ctx.active_flags`` before calling the handler. Aliases resolve
        to the canonical name, so ``ctx.flag("--table")`` is true whether
        the user typed ``-t`` or ``--table``.

        Args:
            name: Canonical flag name including the leading dashes
                (e.g. ``"--table"``).

        Returns:
            True if the flag was present on the invocation.
        """
        return name in self.active_flags

    def plugin_cfg(self, name: str) -> PluginConfig:
        """Return a persistent config object for a plugin.

        The config file lives at a deterministic path::

            termapy_cfg/<config>/plugin/<name>.cfg

        The file is loaded lazily on first access and cached for the
        session.  Call ``.save()`` to write changes to disk.

        Example::

            def _handler(ctx, args):
                cfg = ctx.plugin_cfg("pic_map")
                cfg["map_path"] = args.strip()
                cfg.save()

        Args:
            name: Plugin name.  Used as the config file stem.

        Returns:
            A ``PluginConfig`` instance backed by the JSON file.

        Raises:
            RuntimeError: If no config is loaded (no ``config_path``).
        """
        if name in self._plugin_cfgs:
            return self._plugin_cfgs[name]
        if not self.config_path:
            raise RuntimeError(
                f"Cannot access plugin config for {name!r}: no config loaded"
            )
        path = Path(self.config_path).parent / "plugin" / f"{name}.cfg"
        pc = PluginConfig(path)
        self._plugin_cfgs[name] = pc
        return pc

    # -- Output channels -------------------------------------------------------

    @property
    def output_level(self) -> str:
        """Active output level, honoring any per-call override.

        Falls back to the global level in ``ctx.ns("flags")`` and finally
        to ``DEFAULT_OUTPUT_LEVEL`` -- never raises on a missing key.
        """
        if self._call_level is not None:
            return self._call_level
        return self.ns("flags").get("output_level", DEFAULT_OUTPUT_LEVEL)

    def _shows(self, min_rank: int) -> bool:
        rank = OUTPUT_LEVEL_RANK.get(self.output_level, OUTPUT_LEVEL_RANK[DEFAULT_OUTPUT_LEVEL])
        return rank >= min_rank

    def result(self, text: str, color: str = "green") -> None:
        """Write a command result (single-line answer). Shown at quiet+."""
        if self._shows(RESULT_MIN_RANK):
            self.write(text, color)

    def output(self, text: str, color: str = "dim") -> None:
        """Write data output (listings, dumps, file contents). Shown at normal+."""
        if self._shows(OUTPUT_MIN_RANK):
            self.write(text, color)

    def status(self, text: str) -> None:
        """Write a status/progress message. Shown only at verbose."""
        if self._shows(STATUS_MIN_RANK):
            self.write(text, "dim")

    @contextmanager
    def serial_io(self) -> Generator[None, None, None]:
        """The synchronous-serial-read primitive.

        Claims the serial port for the duration of the block (suppresses
        display, queues bytes for ``serial_read_raw()`` instead of feeding
        ``on_lines``).  Releases on every exit path including exceptions.
        Use this for any drain -> write -> read cycle.

        This is the only public path -- the bare flag setter is
        intentionally unreachable from plugin code so exception safety
        is structural rather than a rule contributors have to remember.

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

    @contextmanager
    def rx_observer(
        self, cb: Callable[[bytes], None],
    ) -> Generator[None, None, None]:
        """Register an RX byte observer for the duration of the block.

        Observers see every raw RX byte chunk alongside the normal
        line-decoding pipeline -- they cannot modify or block it.
        Callbacks fire on the reader background thread; keep them fast
        or offload to a queue.

        Released on every exit path including exceptions, so a handler
        that crashes mid-block can't leak the observer.  This is the
        only public path -- the bare register/unregister methods are
        intentionally unreachable from plugin code so leaks are
        structurally impossible.

        Usage::

            byte_count = [0]
            def watch(data: bytes) -> None:
                byte_count[0] += len(data)

            with ctx.rx_observer(watch):
                ctx.dispatch(some_command)

            ctx.result(f"saw {byte_count[0]} RX bytes")
        """
        self._add_rx_observer(cb)
        try:
            yield
        finally:
            self._remove_rx_observer(cb)

    @contextmanager
    def tx_observer(
        self, cb: Callable[[bytes], None],
    ) -> Generator[None, None, None]:
        """Register a TX byte observer for the duration of the block.

        Observers see every raw TX byte chunk alongside the normal
        write path -- they cannot modify or block it.  Callbacks fire
        on the calling thread.

        Released on every exit path including exceptions; bare
        register/unregister methods are not exposed from plugin code.
        Pairs with :meth:`rx_observer` for full traffic taps.

        Usage::

            written = bytearray()
            with ctx.tx_observer(written.extend):
                ctx.serial_write(b"AT+VER\\r")
            ctx.result(f"sent {len(written)} bytes")
        """
        self._add_tx_observer(cb)
        try:
            yield
        finally:
            self._remove_tx_observer(cb)
