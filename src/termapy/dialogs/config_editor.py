"""Modal dialog: ConfigEditor.

Extracted from the original monolithic ``dialogs.py``.  See
``termapy.dialogs.__init__`` for the package-level public API and
the ``_common`` submodule for shared constants and helpers.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from rich.errors import MarkupError
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, Input, TextArea

from termapy.config import (
    cfg_path_for_name,
)
from termapy.defaults import DEFAULT_CFG
from termapy.dialogs._common import _DISMISS_BINDINGS, _MODAL_BTN_CSS


class ConfigEditor(ModalScreen[tuple | None]):
    """Modal dialog to edit JSON config. Returns (cfg_dict, path) or None."""

    BINDINGS = _DISMISS_BINDINGS

    CSS = f"""
    ConfigEditor {{ align: center middle; }}
    ConfigEditor Button {{ {_MODAL_BTN_CSS} }}
    #config-dialog {{
        width: 95%; height: 90%;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #config-title {{ height: 1; text-style: bold; }}
    #config-editor {{ height: 1fr; border: thick $primary; }}
    #config-help {{ height: 3; width: 1fr; }}
    #config-error {{ height: 3; color: $error; display: none; width: 1fr; overflow-y: auto; }}
    #config-error.visible {{ display: block; }}
    #save-as-row {{ height: 1; display: none; }}
    #save-as-row.visible {{ display: block; }}
    #save-as-input {{ width: 1fr; height: 1; border: none; }}
    #config-bottom {{ height: 3; }}
    #config-btn-col {{ width: auto; layout: horizontal; align: right middle; height: 3; content-align: right middle; }}
    """

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q or Escape."""
        self.dismiss(None)

    def __init__(
        self,
        cfg: dict,
        config_path: str,
        highlight_key: str = "",
    ) -> None:
        super().__init__()
        self.cfg = cfg
        self.config_path = config_path
        self._save_as_mode = False
        self._highlight_key = highlight_key
        # Read raw JSON from disk so $(env.NAME) templates are visible
        try:
            with open(config_path) as f:
                self._disk_cfg = json.load(f)
        except (OSError, json.JSONDecodeError):
            self._disk_cfg = cfg

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        with Vertical(id="config-dialog"):
            yield Static(f"JSON: {self.config_path}", id="config-title")
            yield TextArea(
                json.dumps(self._disk_cfg, indent=4),
                language="json",
                theme="monokai",
                show_line_numbers=True,
                id="config-editor",
            )
            with Horizontal(id="save-as-row"):
                yield Input(
                    placeholder="filename.cfg",
                    id="save-as-input",
                )
            with Horizontal(id="config-bottom"):
                yield Static("", id="config-help")
                yield Static("", id="config-error")
                with Horizontal(id="config-btn-col"):
                    yield Button("Save", id="cfg-save", variant="success")
                    yield Button("Save As", id="cfg-save-as", variant="primary")
                    yield Button("Cancel", id="cfg-cancel", variant="error")

    def on_mount(self) -> None:
        """Set tooltips and position cursor on highlight_key line."""
        self.query_one("#config-editor", TextArea).tooltip = (
            "Edit the raw JSON config.  Live validation shows hints below."
        )
        self.query_one("#save-as-input", Input).tooltip = (
            "New filename for Save As (the .cfg extension is added automatically)."
        )
        self.query_one("#cfg-save", Button).tooltip = (
            "Validate and save changes to this config."
        )
        self.query_one("#cfg-save-as", Button).tooltip = (
            "Save the config to a new filename."
        )
        self.query_one("#cfg-cancel", Button).tooltip = (
            "Discard changes and close."
        )
        if not self._highlight_key:
            return
        editor = self.query_one("#config-editor", TextArea)
        for row in range(editor.document.line_count):
            line = str(editor.get_line(row))
            m = self._JSON_KEY_RE.match(line)
            if m and m.group(1) == self._highlight_key:
                editor.cursor_location = (row, 0)
                break

    def _validate_json(self) -> dict | None:
        from textual.widgets import Static

        text = self.query_one("#config-editor", TextArea).text
        err = self.query_one("#config-error", Static)
        try:
            new_cfg = json.loads(text)
        except json.JSONDecodeError as e:
            err.update(f"Invalid JSON: {e}")
            err.add_class("visible")
            return None
        err.remove_class("visible")
        return new_cfg

    _JSON_KEY_RE = re.compile(r'^\s*"([^"]+)"\s*:\s*(.*?)\s*,?\s*$')

    # Quick validation for known value sets
    _VALID_VALUES: dict[str, set] = {
        "byte_size": {5, 6, 7, 8},
        "parity": {"N", "E", "O", "M", "S"},
        "stop_bits": {1, 1.5, 2},
        "flow_control": {"none", "rtscts", "xonxoff", "manual"},
        "default_ui": {"tui", "cli", "vt100"},
    }
    # Derived from DEFAULT_CFG so adding a new bool/int key to defaults
    # automatically picks up validation here -- no parallel list to drift.
    # "enabled" is added explicitly because it lives nested inside
    # custom_buttons rather than at the top level of DEFAULT_CFG.
    _BOOL_KEYS = {
        k for k, v in DEFAULT_CFG.items() if isinstance(v, bool)
    } | {"enabled"}
    _INT_KEYS = {
        k for k, v in DEFAULT_CFG.items()
        if isinstance(v, int) and not isinstance(v, bool)
    }
    _STANDARD_BAUDS = {
        300, 1200, 2400, 4800, 9600, 19200, 38400, 57600,
        115200, 230400, 460800, 921600,
    }

    def _validate_value(self, key: str, raw_val: str) -> tuple[str, str]:
        """Check a raw JSON value string.

        Returns:
            (status_line, error) - status_line shows key = value in green
            (valid), yellow (non-standard), or red (invalid).
            error is the detail message or empty string.
        """
        # Template values - resolve, show expansion, then validate resolved value
        if "$(" in raw_val:
            import re as _re

            from termapy.config import expand_env_str
            template = raw_val.strip().strip('"')
            # Highlight $(var) references in cyan
            highlighted = _re.sub(
                r"(\$\([^)]+\))", r"[cyan]\1[/]", template
            )
            resolved = expand_env_str(template)
            if resolved == template:
                return (f"[yellow]{key}[/] = {highlighted}",
                        "[yellow]variable - not resolved[/]")
            # Recurse: validate the resolved value
            fake_raw = f'"{resolved}"' if isinstance(resolved, str) else str(resolved)
            status, error = self._validate_value(key, fake_raw)
            info = f"{highlighted} -> {status}"
            return (info, error)
        val = raw_val.strip().strip('"')
        error = ""
        if key in self._BOOL_KEYS:
            if raw_val.strip() not in ("true", "false"):
                error = "[red]must be true or false[/]"
        elif key in self._VALID_VALUES:
            try:
                parsed = json.loads(raw_val.strip())
            except (json.JSONDecodeError, ValueError):
                parsed = val
            if parsed not in self._VALID_VALUES[key]:
                opts = ", ".join(str(v) for v in sorted(self._VALID_VALUES[key]))
                error = f"[red]must be one of: {opts}[/]"
        elif key == "port":
            # comports() can fail with OSError on permission/driver
            # issues (Linux udev, macOS IOKit, Windows WMI) and with
            # ImportError if pyserial's list_ports backend isn't
            # available on this platform.  Both are environmental;
            # degrade to the neutral display.  Anything else is a
            # bug and should surface.
            try:
                from serial.tools.list_ports import comports
                available = {p.device for p in comports()}
                if val.upper() == "DEMO":
                    return f"[green]{key} = {val}[/] [dim](simulated port)[/]", ""
                if val in available:
                    return f"[green]{key} = {val}[/]", ""
                if available:
                    return f"[yellow]{key} = {val}[/]", f"[yellow]port not found (available: {', '.join(sorted(available))})[/]"
                return f"[dim]{key} = {val}[/]", ""
            except (OSError, ImportError):
                return f"[dim]{key} = {val}[/]", ""
        elif key == "baud_rate":
            try:
                v = json.loads(raw_val.strip())
                if not isinstance(v, int) or v <= 0:
                    error = "[red]must be a positive integer[/]"
                elif v not in self._STANDARD_BAUDS:
                    custom = False
                    # The user is typing in the editor -- the JSON is
                    # routinely invalid between edits.  Treat any parse
                    # failure as "custom_baud unknown" without spamming;
                    # NoMatches covers the editor widget being gone
                    # during teardown.
                    try:
                        editor_cfg = json.loads(
                            self.query_one("#config-editor", TextArea).text
                        )
                        custom = editor_cfg.get("serial", {}).get("custom_baud", False)
                    except (json.JSONDecodeError, ValueError, NoMatches):
                        pass
                    if custom and v >= 300:
                        return f"[green]{key} = {val}[/] [dim](custom)[/]", ""
                    elif custom:
                        return f"[red]{key} = {val}[/]", "[red]custom baud requires >= 300[/]"
                    else:
                        return f"[yellow]{key} = {val}[/]", "[yellow]non-standard rate -- set custom_baud to true[/]"
            except (json.JSONDecodeError, ValueError):
                error = "[red]must be a positive integer[/]"
        elif key in self._INT_KEYS:
            try:
                v = json.loads(raw_val.strip())
                if not isinstance(v, int):
                    error = "[red]must be an integer[/]"
                elif v < 0:
                    error = "[red]must be positive[/]"
            except (json.JSONDecodeError, ValueError):
                error = "[red]must be an integer[/]"
        elif key == "eol":
            # Decode escape sequences via JSON, then require the result
            # to contain only CR/LF/NUL/ETX/EOT bytes (any combination).
            # Rejects junk like "abc" or "CRAP" that the green-default
            # would otherwise pass through.
            try:
                parsed = json.loads(raw_val.strip())
            except (json.JSONDecodeError, ValueError):
                parsed = val
            if (not isinstance(parsed, str)
                    or not all(c in "\r\n\0\x03\x04" for c in parsed)):
                error = (
                    r'[red]must be CR/LF/NUL/ETX/EOT bytes only '
                    r'(e.g. "", "\r", "\n", "\r\n", "\0", '
                    r'"\u0003" (ETX), "\u0004" (EOT))[/]'
                )
        if error:
            return f"[red]{key} = {val}[/]", error
        # Reached only for known keys (CFG_HELP entry exists) with no
        # error.  Either explicitly validated above (bool/int/enum/
        # port/baud) or a free-form string with no validation rule --
        # both render green to signal "recognized field, value OK".
        return f"[green]{key} = {val}[/]", ""

    def _update_help(self) -> None:
        """Update the help text based on the current cursor line."""
        from textual.widgets import Static

        from termapy.defaults import CFG_HELP

        editor = self.query_one("#config-editor", TextArea)
        help_widget = self.query_one("#config-help", Static)
        row, _ = editor.cursor_location
        line = str(editor.get_line(row))

        m = self._JSON_KEY_RE.match(line)
        if not m:
            help_widget.update("")
            return

        key = m.group(1)
        raw_val = m.group(2)
        entry = CFG_HELP.get(key)
        if not entry:
            from termapy.repl import _edit_distance
            best, best_dist = None, 3
            for k in CFG_HELP:
                d = _edit_distance(key, k)
                if d < best_dist:
                    best, best_dist = k, d
            if best:
                help_widget.update(
                    f'[red]Line {row + 1}: Unknown key "{key}"[/]\n'
                    f'Did you mean [green]"{best}"[/]?'
                )
            else:
                help_widget.update(f'[red]Line {row + 1}: Unknown key "{key}"[/]')
            return

        desc = entry[0]
        valid = entry[1]
        preview_fn = entry[2] if len(entry) > 2 else None

        if callable(valid):
            valid = valid()

        status_line, error = self._validate_value(key, raw_val)

        # Build help text: line number, description, valid values, current value, error/preview
        lines = [f"[dim]Line {row + 1}: {desc}[/]"]
        if valid:
            lines.append(f"Valid: [dim italic]{valid}[/]")
        lines.append(f"Value: {status_line}")
        if error:
            lines.append(error)
        elif preview_fn:
            # preview_fn is one of our own preview callables from the
            # config-schema entries.  If it raises that's a bug in our
            # code; let it propagate so we fix it.
            preview = preview_fn(raw_val)
            if preview:
                lines.append(preview)
        # Rich raises MarkupError on malformed markup (mismatched tags,
        # unknown styles).  NoMatches covers the widget being gone.
        # Fall back to a safe rebuild that avoids whatever line was
        # bad in the first place.
        try:
            help_widget.update("\n".join(lines))
        except (MarkupError, NoMatches):
            lines_safe = [f"[dim]Line {row + 1}: {desc}[/]"]
            if valid:
                lines_safe.append(f"Valid: [dim italic]{valid}[/]")
            lines_safe.append("Value: [red]<invalid Rich formatting>[/]")
            help_widget.update("\n".join(lines_safe))

    def on_text_area_selection_changed(self, event) -> None:
        """Update help when cursor moves."""
        self._update_help()

    def on_text_area_changed(self, event) -> None:
        """Update help when text changes (live validation)."""
        self._update_help()
        self._check_json()

    def _check_json(self) -> None:
        """Live JSON syntax check - show/hide error as user types."""
        from textual.widgets import Static
        text = self.query_one("#config-editor", TextArea).text
        err = self.query_one("#config-error", Static)
        try:
            json.loads(text)
            err.remove_class("visible")
        except json.JSONDecodeError as e:
            msg = e.msg
            if "Expecting ',' delimiter" in msg:
                msg = "missing comma"
            elif "Expecting ':' delimiter" in msg:
                msg = "missing colon after key"
            elif "Expecting value" in msg:
                msg = "missing or invalid value"
            elif "Expecting property name" in msg:
                msg = "missing key (or trailing comma)"
            elif "Unterminated string" in msg:
                msg = "missing closing quote"
            elif "Invalid control character" in msg:
                msg = "invalid character (use \\n, \\t for special characters)"
            elif "Extra data" in msg:
                msg = "unexpected content after closing brace"
            err.update(f"JSON error line {e.lineno}: {msg}")
            err.add_class("visible")

    @on(Button.Pressed, "#cfg-save")
    def save_config(self) -> None:
        # If Save As mode is active, save to the new filename
        if self._save_as_mode:
            self._do_save_as()
            return
        new_cfg = self._validate_json()
        if new_cfg is None:
            return
        # Validate config values before saving
        from termapy.config import validate_config
        warnings = validate_config(new_cfg)
        if warnings:
            from textual.widgets import Static
            err = self.query_one("#config-error", Static)
            err.update("\n".join(warnings))
            err.add_class("visible")
            return
        Path(self.config_path).parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w") as f:
            json.dump(new_cfg, f, indent=4)
        self.dismiss((new_cfg, self.config_path))

    @on(Button.Pressed, "#cfg-save-as")
    def save_as_config(self) -> None:
        self._save_as_mode = True
        self.query_one("#save-as-row").add_class("visible")
        self.query_one("#cfg-save-as").display = False
        self.query_one("#save-as-input", Input).focus()

    @on(Input.Submitted, "#save-as-input")
    def save_as_on_enter(self) -> None:
        self._do_save_as()

    def _do_save_as(self) -> None:
        new_cfg = self._validate_json()
        if new_cfg is None:
            return
        # Validate config values before saving
        from termapy.config import validate_config
        warnings = validate_config(new_cfg)
        if warnings:
            from textual.widgets import Static
            err = self.query_one("#config-error", Static)
            err.update("\n".join(warnings))
            err.add_class("visible")
            return
        filename = self.query_one("#save-as-input", Input).value.strip()
        if not filename:
            from textual.widgets import Static

            err = self.query_one("#config-error", Static)
            err.update("Enter a filename")
            err.add_class("visible")
            return
        ext = Path(filename).suffix.lower()
        if ext and ext != ".cfg":
            from textual.widgets import Static

            err = self.query_one("#config-error", Static)
            err.update("File must have a .cfg extension")
            err.add_class("visible")
            return
        if not ext:
            filename += ".cfg"
        # Place in termapy_cfg/<name>/<name>.cfg
        p = Path(filename)
        if not p.parent or p.parent == Path("."):
            name = p.stem
            p = cfg_path_for_name(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        filename = str(p)
        # Update title to reflect new filename
        base = p.stem
        old_title = new_cfg.get("title", "")
        if old_title and base not in old_title:
            new_cfg["title"] = f"{old_title} - {base}"
        elif not old_title:
            new_cfg["title"] = base
        with open(filename, "w") as f:
            json.dump(new_cfg, f, indent=4)
        self.dismiss((new_cfg, filename))

    @on(Button.Pressed, "#cfg-cancel")
    def cancel_config(self) -> None:
        self.dismiss(None)
