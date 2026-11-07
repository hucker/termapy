"""Modal dialog: ConfigEditor -- edit a config's JSON, validated before it is saved.

Extracted from the original monolithic ``dialogs.py``.  See
``termapy.dialogs.__init__`` for the package-level public API and
the ``_common`` submodule for shared constants and helpers.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from rich.errors import MarkupError
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, Input, TextArea

from termapy.config import (
    ConfigExistsError,
    cfg_path_for_name,
    check_cfg_value,
    create_config,
    prepare_cfg_folder,
    write_config_file,
)
from termapy.defaults import (
    MANAGED_CFG_KEYS,
    STANDARD_BAUD_RATES,
    cfg_key,
    cfg_key_path,
    cmd_prefix,
)
from termapy.dialogs._common import (
    _DISMISS_BINDINGS,
    _FIND_BINDINGS,
    _MODAL_BTN_CSS,
    EditorTextArea,
    FindRow,
)
from termapy.dialogs.confirm_dialog import ConfirmDialog
from termapy.markup import escape_markup
from termapy.matching import closest
from termapy.plugins import PluginContext, interpolate_help
from termapy.scripting import parse_bool


class ConfigEditor(ModalScreen[tuple | None]):
    """Modal dialog to edit JSON config. Returns (cfg_dict, path) or None."""

    BINDINGS = [*_DISMISS_BINDINGS, *_FIND_BINDINGS]

    CSS = f"""
    ConfigEditor {{ align: center middle; }}
    ConfigEditor Button {{ {_MODAL_BTN_CSS} }}
    #config-dialog {{
        width: 95%; height: 90%;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #config-title {{ height: 1; text-style: bold; }}
    #config-editor {{ height: 1fr; border: thick $primary; }}
    #config-help {{ height: 4; width: 1fr; }}
    #config-error {{ height: 4; color: $error; display: none; width: 1fr; overflow-y: auto; }}
    #config-error.visible {{ display: block; }}
    #save-as-row {{ height: 1; display: none; }}
    #save-as-row.visible {{ display: block; }}
    /* The filename fields use Textual's own compact Input variant
       (``compact=True``), which IS ``height: 1; border: none`` --
       what this file used to hand-roll -- plus the focus tint and
       the invalid-state tint the hand-rolled copy did not get.
       proto_debug.py already asked for the variant by name; these
       now do too, so a one-row input is the same control wherever
       it appears and the styling is Textual's to maintain.  Only
       the width is ours. */
    #save-as-input {{ width: 1fr; }}
    #config-bottom {{ height: 4; }}
    #config-btn-col {{ width: auto; layout: horizontal; align: right middle; height: 4; content-align: right middle; }}
    """

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q or Escape."""
        self.dismiss(None)

    def __init__(
        self,
        cfg: dict,
        config_path: str,
        highlight_key: str = "",
        *,
        is_new: bool = False,
        used_by: dict[str, list[tuple[str, str]]] | None = None,
        ctx: PluginContext | None = None,
    ) -> None:
        """Edit ``cfg``, saved to ``config_path``.

        Args:
            cfg: The config to edit.
            config_path: Where Save writes it.
            highlight_key: A key to scroll to and highlight.
            is_new: ``cfg`` is a config not yet on disk (the New Config
                wizard's Advanced): Save creates it through
                ``config.create_config``, asking before replacing a
                config already there, and the editor shows ``cfg``
                rather than that file.
            used_by: Key path to the commands that declared it
                (``plugins.cfg_users``), named on the key's help line;
                the data comes in so the dialog needs no engine.
            ctx: The session's context, whose user variables count as set
                when a field names one.  Without it only the process's do.
        """
        super().__init__()
        self.cfg = cfg
        self.config_path = config_path
        self._is_new = is_new
        self._used_by = used_by or {}
        self._ctx = ctx if ctx is not None else PluginContext()
        self._save_as_mode = False
        self._highlight_key = highlight_key
        # Read raw JSON from disk so $(env.NAME) templates are visible
        try:
            if is_new:
                raise FileNotFoundError(config_path)
            with open(config_path) as f:
                self._disk_cfg = json.load(f)
        except (OSError, json.JSONDecodeError):
            self._disk_cfg = cfg

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        with Vertical(id="config-dialog"):
            yield Static(f"JSON: {self.config_path}", id="config-title", markup=False)
            editor = EditorTextArea(
                json.dumps(self._disk_cfg, indent=4),
                language="json",
                theme="monokai",
                show_line_numbers=True,
                id="config-editor",
            )
            yield editor
            yield FindRow(editor, id="config-find")
            with Horizontal(id="save-as-row"):
                yield Input(
                    placeholder="filename.cfg",
                    id="save-as-input",
                    compact=True,
                )
            with Horizontal(id="config-bottom"):
                yield Static("", id="config-help", markup=False)
                yield Static("", id="config-error", markup=False)
                with Horizontal(id="config-btn-col"):
                    yield Button("Find", id="cfg-find", variant="warning")
                    yield Button("Save", id="cfg-save", variant="success")
                    yield Button("Save As", id="cfg-save-as", variant="primary")
                    yield Button("Cancel", id="cfg-cancel", variant="error")

    def on_mount(self) -> None:
        """Set tooltips and position cursor on highlight_key line."""
        self.query_one(
            "#config-editor", TextArea
        ).tooltip = "Edit the raw JSON config.  Live validation shows hints below."
        self.query_one(
            "#save-as-input", Input
        ).tooltip = "New filename for Save As (the .cfg extension is added automatically)."
        self.query_one(
            "#cfg-find", Button
        ).tooltip = "Find text in the JSON (Ctrl+F; Alt+F in VS Code)."
        self.query_one("#cfg-save", Button).tooltip = "Validate and save changes to this config."
        self.query_one("#cfg-save-as", Button).tooltip = "Save the config to a new filename."
        self.query_one("#cfg-cancel", Button).tooltip = "Discard changes and close."
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

    def _validate_value(self, key: str, raw_val: str) -> tuple[str, str]:
        """Check a raw JSON value string.

        Returns:
            (status, error) as Rich markup with the key and value escaped
            -- status shows key = value in green (valid), yellow
            (non-standard), or red (invalid); error is the detail message
            or empty string.
        """
        # Template values.  A $(env.NAME) is expanded at load, so the editor
        # expands it here and validates the result.  Any other $(NAME) is a
        # session variable the variables engine expands when the value is
        # used ($(CFG) in echo_fmt), so the editor says so and checks only
        # that the name exists -- through the namespace's one owner.
        if "$(" in raw_val:
            import re as _re

            from termapy.config import expand_env_str
            from termapy.variables import resolve_one, variable_refs

            template = raw_val.strip().strip('"')
            # Highlight $(var) references in cyan, in the escaped template
            highlighted_markup = _re.sub(r"(\$\([^)]+\))", r"[cyan]\1[/]", escape_markup(template))
            key_markup = escape_markup(key)
            resolved = expand_env_str(template)
            names = variable_refs(resolved)
            unset = [name for name in names if name.startswith("env.")]
            if unset:
                refs = ", ".join(f"$({name})" for name in unset)
                return (
                    f"[yellow]{key_markup}[/] = {highlighted_markup}",
                    f"[yellow]variable - not resolved: {escape_markup(refs)} is not set in the "
                    "environment[/]",
                )
            if names:
                unknown = [name for name in names if resolve_one(self._ctx, name) is None]
                if unknown:
                    refs = ", ".join(f"$({name})" for name in unknown)
                    return (
                        f"[yellow]{key_markup}[/] = {highlighted_markup}",
                        f"[yellow]unknown variable: {escape_markup(refs)}[/]",
                    )
                refs = ", ".join(f"$({name})" for name in names)
                verb = "is a session variable" if len(names) == 1 else "are session variables"
                note = f"{refs} {verb}, expanded when the value is used"
                return (
                    f"[green]{key_markup}[/] = {highlighted_markup}",
                    f"[dim]{escape_markup(note)}[/]",
                )
            record = cfg_key(key)
            if record is not None and record.kind == "bool":
                # A templated boolean is read with the vocabulary at load
                # (config.expand_env_cfg); show what it reads as.
                try:
                    flag = parse_bool(resolved, what=key)
                except ValueError as e:
                    return (
                        f"[red]{key_markup}[/] = {highlighted_markup}",
                        f"[red]{escape_markup(e)}[/]",
                    )
                flag_markup = escape_markup(json.dumps(flag))
                return (f"[green]{key_markup}[/] = {highlighted_markup} -> {flag_markup}", "")
            # Recurse: validate the resolved value
            fake_raw = f'"{resolved}"' if isinstance(resolved, str) else str(resolved)
            status_markup, error_markup = self._validate_value(key, fake_raw)
            return (f"{highlighted_markup} -> {status_markup}", error_markup)
        val = raw_val.strip().strip('"')
        # ``key = val`` as escaped markup, the text every status below colors
        pair_markup = escape_markup(f"{key} = {val}")
        record = cfg_key(key)
        # A section or a list opens over several lines, so this line alone
        # says nothing about it; Save checks the whole value.
        if record is None or record.kind in ("section", "list"):
            return f"[green]{pair_markup}[/]", ""
        # The JSON text as a value.  Between edits a line is routinely not
        # JSON yet, and then it is the text as typed, which the check judges.
        try:
            value = json.loads(raw_val.strip())
        except (json.JSONDecodeError, ValueError):
            value = val
        # The one check a load and /cfg use (registry cfg-schema-one-table).
        finding = check_cfg_value(key, value, self._editor_cfg())
        if finding is not None:
            reason_markup = escape_markup(finding.reason)
            return f"[red]{pair_markup}[/]", f"[red]{reason_markup}[/]"
        if record.kind == "port":
            return self._port_status(val, pair_markup)
        if record.kind == "baud" and value not in STANDARD_BAUD_RATES:
            return f"[green]{pair_markup}[/] [dim](custom)[/]", ""
        return f"[green]{pair_markup}[/]", ""

    def _editor_cfg(self) -> dict:
        """The config as the editor holds it now, ``{}`` while it is not valid JSON.

        The user is typing, so the JSON is routinely invalid between edits;
        NoMatches covers the editor widget being gone during teardown.
        """
        try:
            parsed = json.loads(self.query_one("#config-editor", TextArea).text)
        except (json.JSONDecodeError, ValueError, NoMatches):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _port_status(self, val: str, pair_markup: str) -> tuple[str, str]:
        """What only a live editor can say about a port: whether it is plugged in now.

        comports() can fail with OSError on permission/driver issues
        (Linux udev, macOS IOKit, Windows WMI) and with ImportError if
        pyserial's list_ports backend isn't available on this platform.
        Both are environmental, so they degrade to the neutral display.
        Anything else is a bug and should surface.
        """
        try:
            from serial.tools.list_ports import comports

            from termapy import port_control

            if port_control.synthetic_facts_for_reserved(val.upper()) is not None:
                return f"[green]{pair_markup}[/] [dim](simulated port)[/]", ""
            # Present as connecting would resolve it: a serial number counts too.
            if port_control.port_present(val):
                return f"[green]{pair_markup}[/]", ""
            available = {port.device for port in comports()}
            if available:
                ports_markup = escape_markup(", ".join(sorted(available)))
                return (
                    f"[yellow]{pair_markup}[/]",
                    f"[yellow]port not found (available: {ports_markup})[/]",
                )
            return f"[dim]{pair_markup}[/]", ""
        except (OSError, ImportError):
            return f"[dim]{pair_markup}[/]", ""

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
            best = next(iter(closest(key, CFG_HELP, limit=1)), None)
            unknown = Text(f'Line {row + 1}: Unknown key "{key}"', style="red")
            if best:
                unknown.append_text(Text.assemble("\nDid you mean ", (f'"{best}"', "green"), "?"))
            help_widget.update(unknown)
            return

        prefix = cmd_prefix(self.cfg)
        desc = interpolate_help(entry[0], prefix)
        valid = entry[1]
        preview_fn = entry[2] if len(entry) > 2 else None

        if callable(valid):
            valid = valid()
        if valid:
            valid = interpolate_help(valid, prefix)

        status_markup, error_markup = self._validate_value(key, raw_val)

        # Build help text: line number, description, valid values, current value, error/preview
        warning_markup = "[bold red]DO NOT EDIT[/] - " if key in MANAGED_CFG_KEYS else ""
        heading = [f"[dim]Line {row + 1:d}:[/] {warning_markup}[dim]{escape_markup(desc)}[/]"]
        if valid:
            heading.append(f"Valid: [dim italic]{escape_markup(valid)}[/]")
        lines = [*heading, f"Value: {status_markup}"]
        if error_markup:
            lines.append(error_markup)
        elif preview_fn:
            # preview_fn is one of our own preview callables from the
            # config-schema entries.  If it raises that's a bug in our
            # code; let it propagate so we fix it.
            preview = preview_fn(raw_val)
            if preview:
                lines.append(preview)
        # The commands that declared the key (registry cfg-declared); the
        # line's key is a leaf, so serial's port resolves to its path.
        path = cfg_key_path(key)
        users = self._used_by.get(path, ()) if path else ()
        if users:
            names_markup = ", ".join(escape_markup(prefix + name) for name, _ in users)
            lines.append(f"[dim]Used by:[/] {names_markup}")
        # Rich markup with every value escaped, so only a preview of the
        # user's own markup (an echo_fmt) can be malformed: Rich raises
        # MarkupError on mismatched tags.  NoMatches covers the widget
        # being gone.  The widget gets a Text, never a string, so
        # Textual's own markup parser never reads it.
        try:
            help_widget.update(Text.from_markup("\n".join(lines)))
        except (MarkupError, NoMatches):
            lines_safe = [*heading, "Value: [red]<invalid Rich formatting>[/]"]
            help_widget.update(Text.from_markup("\n".join(lines_safe)))

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
        if self._is_new:
            self._create(new_cfg, self.config_path)
            return
        prepare_cfg_folder(self.config_path)
        write_config_file(self.config_path, new_cfg)
        self.dismiss((new_cfg, self.config_path))

    def _create(self, new_cfg: dict, path: str, *, overwrite: bool = False) -> None:
        """Write a new config through ``config.create_config``, then close with it.

        A config already there gets the same question as the New Config
        wizard and ``termapy --new``; on yes only its ``.cfg`` is replaced.
        """
        try:
            create_config(path, new_cfg, overwrite=overwrite)
        except ConfigExistsError as e:
            self.app.push_screen(
                ConfirmDialog(e.question),
                callback=lambda confirmed: (
                    self._create(new_cfg, path, overwrite=True) if confirmed else None
                ),
            )
            return
        except (ValueError, RuntimeError, OSError) as e:
            from textual.widgets import Static

            err = self.query_one("#config-error", Static)
            err.update(str(e))
            err.add_class("visible")
            return
        self.dismiss((new_cfg, path))

    def action_find(self) -> None:
        """Ctrl+F / Alt+F: open the find bar."""
        self.query_one("#config-find", FindRow).open()

    @on(Button.Pressed, "#cfg-find")
    def find_pressed(self) -> None:
        self.action_find()

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
        # Update title to reflect new filename
        base = p.stem
        old_title = new_cfg.get("title", "")
        if old_title and base not in old_title:
            new_cfg["title"] = f"{old_title} - {base}"
        elif not old_title:
            new_cfg["title"] = base
        self._create(new_cfg, str(p))

    @on(Button.Pressed, "#cfg-cancel")
    def cancel_config(self) -> None:
        self.dismiss(None)
