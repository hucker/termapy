"""Capability-domain handles attached to ``PluginContext``.

Each module owns one handle class corresponding to one capability
domain on ``ctx``.  Plugin authors interact with these via the
attribute names ``ctx.io``, ``ctx.serial``, ``ctx.fs``, ``ctx.ui``,
and ``ctx.engine``.

Each handle is a thin façade over the underlying flat fields on
``PluginContext`` -- they delegate to ``self._ctx.<x>`` so
post-construction overrides (TUI's ``ctx.write = self._status``,
``app.py``'s post-build ``ctx.notify = ...``) flow through live.

Capability gating happens at the handle-method level for restrictive
(default-False) capabilities only.  Baseline capabilities are
guaranteed by every shipped environment, so a runtime gate would
never fire and just adds noise.

See :class:`MissingCapability` in ``capabilities`` for the exception
that gated methods raise when their capability is absent.
"""

from termapy.plugins.handles.engine import EngineHandle
from termapy.plugins.handles.fs import FilesystemHandle
from termapy.plugins.handles.io import IOHandle
from termapy.plugins.handles.serial import SerialHandle
from termapy.plugins.handles.ui import UIHandle


__all__ = [
    "EngineHandle",
    "FilesystemHandle",
    "IOHandle",
    "SerialHandle",
    "UIHandle",
]
