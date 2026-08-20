"""PluginContext, InternalHandle, and PluginConfig.

The runtime objects every plugin handler interacts with:

  - ``PluginContext`` -- the single argument every handler receives.
    Stable façade over Textual / pyserial / threading internals.
  - ``InternalHandle`` -- privileged escape hatch reachable as ``ctx.internal``.
    Built-in plugins only; unstable.
  - ``PluginConfig`` -- per-plugin persistent JSON config object.

The output-level vocabulary (``OUTPUT_LEVELS``, ``LEVEL_FLAGS``,
``parse_output_level``, ``format_kv_lines``) lives in
:mod:`termapy.plugins.output_levels` so both this module and
:class:`IOHandle` can reach it without circular imports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable

from termapy.defaults import cmd_prefix
from termapy.plugins.capabilities import CapabilitySet
from termapy.plugins.handles.fs import FilesystemHandle
from termapy.plugins.handles.internal import InternalHandle
from termapy.plugins.handles.io import IOHandle
from termapy.plugins.handles.serial import SerialHandle
from termapy.plugins.handles.ui import UIHandle
from termapy.plugins.output_levels import (
    DEFAULT_OUTPUT_LEVEL,
)

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
    The visible plugin-author surface is a small set of fields and
    methods:

    Base fields:

      - ``cfg``           -- read-only config dict
      - ``config_path``   -- path to the current config file
      - ``capabilities``  -- ``CapabilitySet`` advertised by the host

    Capability-domain handles (each one a focused namespace):

      - ``io``        -- write/markup/log/result/output/status/notify/status_bar/clear_screen
      - ``serial``    -- send/write/read_raw/drain/io()/rx_observer/tx_observer/...
      - ``fs``        -- cap_dir/scripts_dir/proto_dir/ss_dir/prof_dir/open_file
      - ``ui``        -- confirm/notify/screenshot/get_screen_text/exit_app (TUI-strict)
      - ``internal``  -- privileged escape hatch for built-in plugins only

    Methods on the base context (universal, not domain-specific):

      - ``dispatch(cmd)``         -- re-dispatch a REPL command
      - ``wait_for_match(...)``   -- block until a line matches (capability-gated)
      - ``is_oneshot()``          -- True if running --run / --exec
      - ``ns(name)``              -- session-scoped namespace dict
      - ``plugin_cfg(name)``      -- per-plugin persistent JSON config
      - ``flag(name)``            -- read a per-dispatch flag
      - ``prefix``                -- property; active REPL command prefix (from cfg)
      - ``output_level``          -- property; current verbose/quiet/silent dial

    See the docstring on each handle module
    (``termapy.plugins.handles.{io,serial,fs,ui,internal}``) for the
    full method reference of that domain.
    """

    cfg: MappingProxyType | dict = field(default_factory=dict)
    config_path: str = ""

    # ── Capability-domain handles ────────────────────────────────────
    # Each handle is a self-contained dataclass with its own callable
    # fields.  Hosts construct PluginContext with handle objects whose
    # callables are wired to the host's implementation.  Plugins
    # invoke them via ``ctx.<handle>.<method>(...)``.
    io: IOHandle = field(default_factory=IOHandle)
    serial: SerialHandle = field(default_factory=SerialHandle)
    fs: FilesystemHandle = field(default_factory=FilesystemHandle)
    ui: UIHandle = field(default_factory=UIHandle)
    internal: InternalHandle = field(default_factory=InternalHandle)

    # ── Universal callbacks / methods stored as fields ───────────────
    # These don't fit any single domain handle (dispatch routes to all
    # commands; wait_for_match is script-runner only; is_oneshot is a
    # property of the invocation environment).
    dispatch: Callable = lambda cmd: None
    wait_for_match: Callable = lambda predicate, timeout=5.0: None
    is_oneshot: Callable = lambda: False

    # ── Capabilities ────────────────────────────────────────────────
    # Dispatch compares a command's declared ``needs`` against this set
    # before calling the handler and fails with a clear message if
    # anything is missing.  Each environment publishes its own when
    # constructing the context.
    capabilities: CapabilitySet = field(default_factory=CapabilitySet)

    # ── Structured-output preference ─────────────────────────────────
    # True when the consumer of this dispatch wants machine-readable
    # structure (``CmdResult.data``) rather than rendered prose: the MCP
    # host sets it for every call; a future CLI ``--json`` sets it per
    # dispatch; the TUI never sets it.  Handlers producing LARGE
    # listings should branch on it and build either the prose or the
    # ``data`` records -- not both -- so neither audience pays for the
    # other's rendering.  Handlers that ignore it keep working: their
    # prose reaches structured consumers via ``output_lines``.
    wants_data: bool = False

    # ── Internal storage (not part of the plugin-author API) ─────────
    # Per-dispatch flag set populated by ReplEngine.dispatch.
    active_flags: set[str] = field(default_factory=set)
    # Per-dispatch coerced parameter values (Command.params); read via
    # ``ctx.arg()``.  Save/restored (not cleared) around the handler so a
    # nested ``ctx.dispatch()`` doesn't strand the outer command's params.
    bound_params: dict[str, Any] = field(default_factory=dict)
    # Per-call output-level override; cleared after dispatch.
    _call_level: str | None = None
    # Namespace registry for session-scoped state.
    _namespaces: dict[str, dict] = field(default_factory=dict)
    # Plugin config cache, lazy-loaded from disk.
    _plugin_cfgs: dict[str, PluginConfig] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Wire handle dependencies that need the live ctx.

        The handles are constructed by the host (or auto-defaulted to
        no-op handles by the dataclass field defaults), so we don't
        rebuild them here.  But two handles need information that
        only PluginContext knows:

          - ``IOHandle.output_level_fn`` needs to read the current
            level (which respects per-call ``_call_level`` overrides).
          - ``FilesystemHandle.capabilities`` and
            ``UIHandle.capabilities`` need the same ``CapabilitySet``
            the dispatcher reads, so capability gates fire correctly.

        Set them here so hosts don't have to remember.
        """
        self.io.output_level_fn = lambda: self.output_level
        self.sync_capabilities()

    def sync_capabilities(self) -> None:
        """Re-snapshot ``self.capabilities`` into the capability handles.

        ``FilesystemHandle`` and ``UIHandle`` hold a snapshot of the
        host's ``CapabilitySet`` (so their runtime gates -- ``fs.resolve``,
        ``fs.open_file``, ``ui.confirm`` -- read the right one).  Hosts
        that build the context first and assign ``ctx.capabilities``
        afterward (app.py / cli.py / mcp) MUST call this so the snapshot
        follows the reassignment; otherwise the handles keep the default
        (all-restrictive) set and, e.g., a CLI operator would be sandboxed
        by ``fs.resolve``.  Called once by ``__post_init__`` too.
        """
        self.fs.capabilities = self.capabilities
        self.ui.capabilities = self.capabilities

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
        ``hex``, and the ``output_level`` dial.  Third-party plugins
        should use their own namespace name (conventionally the plugin name).

        Example::

            def _handler(ctx, args):
                store = ctx.ns("myplugin")
                store["requests_sent"] = store.get("requests_sent", 0) + 1
                ctx.io._write(f"sent {store['requests_sent']} requests")

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

    def arg(self, name: str, default: Any = None) -> Any:
        """Return the parsed value of a declared parameter for this dispatch.

        Handlers declare parameters on ``Command(params=[...])``; the
        dispatcher parses, coerces, and validates them (failing before the
        handler runs) and records the coerced values here.  A declared
        parameter is always present after a successful dispatch -- absent
        optionals hold their ``default`` -- so the ``default`` argument only
        matters when reading a name the command didn't declare.

        Args:
            name: The declared parameter name (lowercase).
            default: Returned if ``name`` was not a declared parameter.

        Returns:
            The coerced value (e.g. float seconds for a ``duration`` param,
            the canonical string for an ``enum``).
        """
        return self.bound_params.get(name, default)

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

    # -- Prefix ---------------------------------------------------------------

    @property
    def prefix(self) -> str:
        """The active REPL command prefix (e.g. ``/``).

        Derived live from ``ctx.cfg`` so a runtime ``cmd_prefix`` change is
        reflected immediately -- handlers building usage strings should read
        this rather than hard-coding ``/``.
        """
        return cmd_prefix(self.cfg)

    # -- Output level ---------------------------------------------------------

    @property
    def output_level(self) -> str:
        """Active output level, honoring any per-call override.

        Falls back to the global level in ``ctx.ns("flags")`` and finally
        to ``DEFAULT_OUTPUT_LEVEL`` -- never raises on a missing key.

        Plugins read this when they need to know the current verbosity.
        Most plugins should not -- the IOHandle's ``result()`` /
        ``output()`` / ``status()`` methods route through it
        automatically.
        """
        if self._call_level is not None:
            return self._call_level
        return self.ns("flags").get("output_level", DEFAULT_OUTPUT_LEVEL)
