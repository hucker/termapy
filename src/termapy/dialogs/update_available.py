"""Modal dialog: UpdateAvailableDialog.

Extracted from the original monolithic ``dialogs.py``.  See
``termapy.dialogs.__init__`` for the package-level public API and
the ``_common`` submodule for shared constants and helpers.
"""

from __future__ import annotations


from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button

from termapy.dialogs._common import _DISMISS_BINDINGS, _MODAL_BTN_CSS


class UpdateAvailableDialog(ModalScreen[str]):
    """Tiny two-button dialog that reports a pending termapy update.

    Returns one of:

    - ``"info"`` -- user clicked Info; caller should open the
      installation help page.
    - ``"ok"``   -- user clicked OK or dismissed via Ctrl+Q/Escape;
      caller does nothing beyond closing the modal.
    """

    BINDINGS = _DISMISS_BINDINGS

    CSS = f"""
    UpdateAvailableDialog {{ align: center middle; }}
    UpdateAvailableDialog Button {{ {_MODAL_BTN_CSS} }}
    #update-dialog {{
        width: 44; height: auto;
        border: solid $warning; background: $surface; padding: 1 2;
    }}
    #update-title {{ height: 1; text-style: bold; color: $warning; }}
    #update-body {{ height: auto; padding-top: 1; padding-bottom: 1; }}
    #update-buttons {{ height: 1; align: center middle; }}
    """

    def action_dismiss_modal(self) -> None:
        self.dismiss("ok")

    def on_mount(self) -> None:
        self.query_one("#update-info", Button).tooltip = (
            "Open the installation help page in a browser."
        )
        self.query_one("#update-ok", Button).tooltip = (
            "Close this notification."
        )

    def __init__(self, current: str, latest: str) -> None:
        super().__init__()
        self.current = current
        self.latest = latest

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        body = f"Installed:  {self.current}\nLatest:     {self.latest}"
        with Vertical(id="update-dialog"):
            yield Static("Update Available", id="update-title")
            yield Static(body, id="update-body")
            with Horizontal(id="update-buttons"):
                yield Button("Info", id="update-info", variant="warning")
                yield Button("OK", id="update-ok", variant="primary")

    @on(Button.Pressed, "#update-info")
    def info_pressed(self) -> None:
        self.dismiss("info")

    @on(Button.Pressed, "#update-ok")
    def ok_pressed(self) -> None:
        self.dismiss("ok")
