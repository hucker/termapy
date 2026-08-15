"""Handles attached to ``PluginContext``.

Four modules own a capability-domain handle -- ``ctx.io``,
``ctx.serial``, ``ctx.fs``, ``ctx.ui`` -- each a focused public
namespace.  The fifth, ``ctx.internal`` (:class:`InternalHandle`), is
deliberately not a domain: it's the privileged, unstable escape hatch
for built-in plugins only.

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

from termapy.plugins.handles.fs import FilesystemHandle
from termapy.plugins.handles.internal import InternalHandle
from termapy.plugins.handles.io import IOHandle
from termapy.plugins.handles.serial import SerialHandle
from termapy.plugins.handles.ui import UIHandle

__all__ = [
    "InternalHandle",
    "FilesystemHandle",
    "IOHandle",
    "SerialHandle",
    "UIHandle",
]
