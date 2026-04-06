"""Vendored third-party packages.

Why vendor?
    Termapy bundles xmodem, ymodem, and pyserial to reduce the number
    of pip-installed dependencies users need. These are pure-Python,
    MIT-licensed libraries that rarely change (pyserial's last release
    was November 2020). See LICENSES.md in this directory for details.

How pyserial vendoring works:
    pyserial's 28 source files all use absolute imports like
    ``import serial`` and ``from serial.tools.list_ports import ...``.
    Rather than rewriting ~40 internal imports to relative paths, we
    add this vendor directory to the front of ``sys.path``. That way
    ``import serial`` finds our bundled copy at
    ``termapy/vendor/serial/`` before looking for a system-installed
    pyserial package.

    We use sys.path.insert (not sys.modules tricks) because pyserial's
    own __init__.py does ``from serial.serialutil import *`` during
    loading — it needs ``serial`` to be findable as a top-level package
    from the very start of its own import.

Bootstrap locations:
    - App startup: ``termapy/__init__.py`` does ``import termapy.vendor``
    - Test suite: ``tests/conftest.py`` does ``import termapy.vendor``
    Both run before any code does ``import serial``.
"""

import os
import sys

# ---------------------------------------------------------------------------
# Add the vendor directory to sys.path so that ``import serial`` resolves
# to termapy/vendor/serial/ rather than requiring an external pyserial.
# Inserted at position 0 so our copy wins over any system-installed one.
# ---------------------------------------------------------------------------
_vendor_dir = os.path.dirname(os.path.abspath(__file__))
if _vendor_dir not in sys.path:
    sys.path.insert(0, _vendor_dir)
