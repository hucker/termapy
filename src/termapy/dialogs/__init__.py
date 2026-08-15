"""Modal dialog screens for termapy.

All picker, editor, and confirmation dialogs live here.  Each is a
self-contained ``ModalScreen`` with no dependency on
``SerialTerminal``; consumers push them with ``self.push_screen(...)``
from the host app.

Each dialog has its own file under this package -- the original
single-file ``dialogs.py`` (~1,700 lines) was split for navigation
and per-dialog diff hygiene.  Shared constants and helpers live in
``_common`` (package-private).

**This is a TUI subpackage** -- every class imports Textual, so this
package is NOT a candidate to be a standalone PyPI library (unlike
``termapy.usb`` / ``termapy.protocol`` / ``termapy.profile``).  The
win here is purely organizational.

Public API (the 14 ``ModalScreen`` classes) is re-exported below so
consumers continue to write ``from termapy.dialogs import X`` exactly
as before the split.
"""

from __future__ import annotations

from termapy.dialogs.cfg_confirm import CfgConfirm
from termapy.dialogs.config_editor import ConfigEditor
from termapy.dialogs.config_picker import ConfigPicker
from termapy.dialogs.confirm_dialog import ConfirmDialog
from termapy.dialogs.filename_dialog import FilenameDialog
from termapy.dialogs.name_picker import NamePicker
from termapy.dialogs.port_picker import PortPicker
from termapy.dialogs.proto_editor import ProtoEditor
from termapy.dialogs.proto_picker import ProtoPicker
from termapy.dialogs.quick_setup import QuickSetup
from termapy.dialogs.script_editor import ScriptEditor
from termapy.dialogs.script_picker import ScriptPicker
from termapy.dialogs.set_var_dialog import SetVarDialog
from termapy.dialogs.update_available import UpdateAvailableDialog
from termapy.dialogs.welcome_dialog import WelcomeDialog

__all__ = [
    "CfgConfirm",
    "ConfigEditor",
    "ConfigPicker",
    "ConfirmDialog",
    "FilenameDialog",
    "NamePicker",
    "PortPicker",
    "ProtoEditor",
    "ProtoPicker",
    "QuickSetup",
    "ScriptEditor",
    "ScriptPicker",
    "SetVarDialog",
    "UpdateAvailableDialog",
    "WelcomeDialog",
]
