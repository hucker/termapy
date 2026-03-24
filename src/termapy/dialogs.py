"""Modal dialog screens for termapy.

All picker, editor, and confirmation dialogs live here.
Each is a self-contained ModalScreen with no dependency on SerialTerminal.
"""

import json
import re
from pathlib import Path

from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, TextArea
from textual.widgets.option_list import Option

from termapy.config import (
    cfg_dir,
    cfg_path_for_name,
    migrate_json_to_cfg,
    open_with_system,
)
from termapy.defaults import PROTO_TEMPLATE, SCRIPT_TEMPLATE

# Shared CSS for modal dialog buttons
_MODAL_BTN_CSS = """
    min-width: 0; width: auto; height: 1; min-height: 1;
    border: none; margin: 0 0 0 1;
"""

# Ctrl+Q binding shared by all modal dialogs
_CTRL_Q_BINDING = [("ctrl+q", "dismiss_modal", "Close")]


class ConfigEditor(ModalScreen[tuple | None]):
    """Modal dialog to edit JSON config. Returns (cfg_dict, path) or None."""

    BINDINGS = _CTRL_Q_BINDING

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
        """Close the modal on Ctrl+Q."""
        self.dismiss(None)

    def __init__(self, cfg: dict, config_path: str) -> None:
        super().__init__()
        self.cfg = cfg
        self.config_path = config_path
        self._save_as_mode = False
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
    }
    _BOOL_KEYS = {
        "auto_connect", "auto_reconnect", "send_bare_enter",
        "echo_input", "show_timestamps", "show_line_endings",
        "show_traceback", "config_read_only", "os_cmd_enabled",
        "enabled",
    }
    _INT_KEYS = {
        "baud_rate", "max_lines", "cmd_delay_ms", "max_grep_lines",
        "proto_frame_gap_ms", "byte_size",
    }

    def _validate_value(self, key: str, raw_val: str) -> str:
        """Check a raw JSON value string. Return error message or empty."""
        # Skip template values
        if "$(" in raw_val:
            return ""
        # Strip quotes for string values
        val = raw_val.strip().strip('"')
        if key in self._BOOL_KEYS:
            if raw_val.strip() not in ("true", "false"):
                return "⚠ Must be true or false"
        elif key in self._VALID_VALUES:
            try:
                parsed = json.loads(raw_val.strip())
            except (json.JSONDecodeError, ValueError):
                parsed = val
            if parsed not in self._VALID_VALUES[key]:
                opts = ", ".join(str(v) for v in sorted(self._VALID_VALUES[key]))
                return f"⚠ Must be one of: {opts}"
        elif key in self._INT_KEYS:
            try:
                v = json.loads(raw_val.strip())
                if not isinstance(v, int):
                    return "⚠ Must be an integer"
                if v < 0:
                    return "⚠ Must be positive"
            except (json.JSONDecodeError, ValueError):
                return "⚠ Must be an integer"
        return ""

    def _update_help(self) -> None:
        """Update the help text based on the current cursor line."""
        from termapy.defaults import CFG_HELP
        from textual.widgets import Static

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
            help_widget.update(f'"{key}"')
            return

        desc = entry[0]
        valid = entry[1]
        preview_fn = entry[2] if len(entry) > 2 else None

        if callable(valid):
            valid = valid()

        error = self._validate_value(key, raw_val)

        # Build help text: description, valid values, then preview or error
        lines = [f"[dim]{desc}[/]", f"[dim]{valid}[/]"]
        if error:
            lines.append(error)
        elif preview_fn:
            try:
                preview = preview_fn(raw_val)
                if preview:
                    lines.append(preview)
            except Exception:
                pass
        help_widget.update("\n".join(lines))

    def on_text_area_selection_changed(self, event) -> None:
        """Update help when cursor moves."""
        self._update_help()

    def on_text_area_changed(self, event) -> None:
        """Update help when text changes (live validation)."""
        self._update_help()

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
            new_cfg["title"] = f"{old_title} — {base}"
        elif not old_title:
            new_cfg["title"] = base
        with open(filename, "w") as f:
            json.dump(new_cfg, f, indent=4)
        self.dismiss((new_cfg, filename))

    @on(Button.Pressed, "#cfg-cancel")
    def cancel_config(self) -> None:
        self.dismiss(None)


class MarkdownViewer(ModalScreen[None]):
    """Modal dialog to display a markdown file with an option to open externally."""

    BINDINGS = _CTRL_Q_BINDING

    CSS = f"""
    MarkdownViewer {{ align: center middle; }}
    MarkdownViewer Button {{ {_MODAL_BTN_CSS} }}
    #mdv-dialog {{
        width: 90%; height: 90%;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #mdv-title {{ height: 1; text-style: bold; }}
    #mdv-content {{ height: 1fr; border: thick $primary; }}
    #mdv-buttons {{ height: 1; align: right middle; }}
    """

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q."""
        self.dismiss(None)

    def __init__(self, content: str, title: str = "", file_path: str = "") -> None:
        """Init markdown viewer.

        Args:
            content: Markdown text to display.
            title: Title shown at the top of the dialog.
            file_path: Path to the file on disk (for "Open" button).
        """
        super().__init__()
        self._md_content = content
        self._title = title
        self._file_path = file_path

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        with Vertical(id="mdv-dialog"):
            if self._title:
                yield Static(self._title, id="mdv-title")
            ta = TextArea(self._md_content, read_only=True, id="mdv-content")
            ta.soft_wrap = True
            yield ta
            with Horizontal(id="mdv-buttons"):
                if self._file_path:
                    yield Button("Open", id="mdv-open")
                yield Button("Close", id="mdv-close", variant="primary")

    @on(Button.Pressed, "#mdv-open")
    def open_external(self) -> None:
        """Open the file with the system default application."""
        if self._file_path:
            open_with_system(self._file_path)

    @on(Button.Pressed, "#mdv-close")
    def close_viewer(self) -> None:
        """Close the dialog."""
        self.dismiss(None)


class NamePicker(ModalScreen[str | None]):
    """Modal dialog to enter a name for a new config."""

    BINDINGS = _CTRL_Q_BINDING

    CSS = f"""
    NamePicker {{ align: center middle; }}
    NamePicker Button {{ {_MODAL_BTN_CSS} }}
    #name-dialog {{
        width: 40; height: auto;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #name-label {{ height: 1; text-style: bold; }}
    #name-buttons {{ height: 1; margin-top: 1; }}
    """

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q."""
        self.dismiss(None)

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        with Vertical(id="name-dialog"):
            yield Static("New config name:", id="name-label")
            yield Input(placeholder="e.g. iot_dev", id="name-input")
            with Horizontal(id="name-buttons"):
                yield Button("Cancel", id="name-cancel")

    @on(Input.Submitted, "#name-input")
    def submit_name(self) -> None:
        name = self.query_one("#name-input", Input).value.strip()
        if name:
            # Strip extension if they typed it
            name = Path(name).stem
            self.dismiss(name)

    @on(Button.Pressed, "#name-cancel")
    def cancel(self) -> None:
        self.dismiss(None)


class ConfigPicker(ModalScreen[tuple | None]):
    """Modal dialog to select a config file: load, edit, or create new."""

    BINDINGS = _CTRL_Q_BINDING

    CSS = f"""
    ConfigPicker {{ align: center middle; }}
    ConfigPicker Button {{ {_MODAL_BTN_CSS} }}
    #picker-dialog {{
        width: 50; height: 18;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #picker-title {{ height: 1; text-style: bold; }}
    #picker-list {{ height: 1fr; border: thick $primary; }}
    #picker-buttons {{ height: 1; align: right middle; }}
    """

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q."""
        self.dismiss(None)

    def __init__(self, current_path: str = "", read_only: bool = False) -> None:
        super().__init__()
        self.current_path = current_path
        self.read_only = read_only

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        d = cfg_dir()
        migrate_json_to_cfg(d)
        json_files = sorted(d.glob("*/*.cfg"))
        with Vertical(id="picker-dialog"):
            yield Static("Select Config", id="picker-title")
            ol = OptionList(id="picker-list")
            highlight_idx = None
            for i, f in enumerate(json_files):
                ol.add_option(Option(f.stem, id=str(f)))
                if str(f) == self.current_path:
                    highlight_idx = i
            ol.highlighted = highlight_idx if highlight_idx is not None else 0
            yield ol
            has_configs = bool(json_files)
            with Horizontal(id="picker-buttons"):
                yield Button(
                    "Load",
                    id="picker-load",
                    variant="success",
                    disabled=not has_configs,
                )
                yield Button(
                    "Edit",
                    id="picker-edit",
                    variant="primary",
                    disabled=not has_configs or self.read_only,
                )
                new_btn = Button("New", id="picker-new")
                new_btn.styles.background = "darkorchid"
                yield new_btn
                yield Button(
                    "Delete",
                    id="picker-delete",
                    variant="warning",
                    disabled=not has_configs or self.read_only,
                )
                yield Button("Cancel", id="picker-cancel", variant="error")

    def _selected_path(self) -> str | None:
        ol = self.query_one("#picker-list", OptionList)
        if ol.highlighted is not None:
            return str(ol.get_option_at_index(ol.highlighted).id)
        return None

    @on(Button.Pressed, "#picker-delete")
    def delete_config(self) -> None:
        path = self._selected_path()
        if path:
            self.dismiss(("delete", path))

    @on(Button.Pressed, "#picker-new")
    def new_config(self) -> None:
        self.dismiss(("new",))

    @on(Button.Pressed, "#picker-edit")
    def edit_config(self) -> None:
        path = self._selected_path()
        if path:
            self.dismiss(("edit", path))

    @on(Button.Pressed, "#picker-load")
    def load_config_btn(self) -> None:
        path = self._selected_path()
        if path:
            self.dismiss(("load", path))

    def on_key(self, event: events.Key) -> None:
        """Load the highlighted config when Enter is pressed in the list."""
        if event.key != "enter":
            return
        if not isinstance(self.focused, OptionList):
            return
        event.prevent_default()
        event.stop()
        path = self._selected_path()
        if path:
            self.dismiss(("load", path))

    @on(Button.Pressed, "#picker-cancel")
    def cancel_picker(self) -> None:
        self.dismiss(None)


class ScriptPicker(ModalScreen[tuple | None]):
    """Modal dialog to pick a script file to run, edit, or create new."""

    BINDINGS = _CTRL_Q_BINDING

    CSS = f"""
    ScriptPicker {{ align: center middle; }}
    ScriptPicker Button {{ {_MODAL_BTN_CSS} }}
    #script-dialog {{
        width: 50; height: 18;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #script-title {{ height: 1; text-style: bold; }}
    #script-list {{ height: 1fr; border: thick $primary; }}
    #script-buttons {{ height: 1; align: right middle; }}
    """

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q."""
        self.dismiss(None)

    def __init__(self, scripts_dir: Path, read_only: bool = False) -> None:
        super().__init__()
        self.scripts_dir = scripts_dir
        self.read_only = read_only

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        scripts = sorted(self.scripts_dir.glob("*"))
        scripts = [f for f in scripts if f.is_file()]
        with Vertical(id="script-dialog"):
            yield Static("Select Script", id="script-title")
            ol = OptionList(id="script-list")
            for f in scripts:
                ol.add_option(Option(f.name, id=str(f)))
            if scripts:
                ol.highlighted = 0
            yield ol
            has_scripts = bool(scripts)
            with Horizontal(id="script-buttons"):
                yield Button(
                    "Run", id="script-run", variant="success", disabled=not has_scripts
                )
                yield Button(
                    "Edit",
                    id="script-edit",
                    variant="primary",
                    disabled=not has_scripts or self.read_only,
                )
                new_btn = Button("New", id="script-new")
                new_btn.styles.background = "darkorchid"
                yield new_btn
                yield Button(
                    "Delete",
                    id="script-delete",
                    variant="warning",
                    disabled=not has_scripts or self.read_only,
                )
                yield Button("Cancel", id="script-cancel", variant="error")

    def _selected_path(self) -> str | None:
        ol = self.query_one("#script-list", OptionList)
        if ol.highlighted is not None:
            return str(ol.get_option_at_index(ol.highlighted).id)
        return None

    @on(Button.Pressed, "#script-delete")
    def delete_script(self) -> None:
        path = self._selected_path()
        if path:
            self.dismiss(("delete", path))

    @on(Button.Pressed, "#script-new")
    def new_script(self) -> None:
        self.dismiss(("new",))

    @on(Button.Pressed, "#script-edit")
    def edit_script(self) -> None:
        path = self._selected_path()
        if path:
            self.dismiss(("edit", path))

    @on(Button.Pressed, "#script-run")
    def run_script(self) -> None:
        path = self._selected_path()
        if path:
            self.dismiss(("run", path))

    def on_key(self, event: events.Key) -> None:
        """Run the highlighted script when Enter is pressed in the list."""
        if event.key != "enter":
            return
        if not isinstance(self.focused, OptionList):
            return
        event.prevent_default()
        event.stop()
        path = self._selected_path()
        if path:
            self.dismiss(("run", path))

    @on(Button.Pressed, "#script-cancel")
    def cancel_picker(self) -> None:
        self.dismiss(None)


class ProtoPicker(ModalScreen[tuple | None]):
    """Modal dialog to pick a .pro protocol script to run, edit, or create new."""

    BINDINGS = _CTRL_Q_BINDING

    CSS = f"""
    ProtoPicker {{ align: center middle; }}
    ProtoPicker Button {{ {_MODAL_BTN_CSS} }}
    #proto-dialog {{
        width: 50; height: 18;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #proto-title {{ height: 1; text-style: bold; }}
    #proto-list {{ height: 1fr; border: thick $primary; }}
    #proto-buttons {{ height: 1; align: right middle; }}
    """

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q."""
        self.dismiss(None)

    def __init__(self, proto_dir: Path, read_only: bool = False) -> None:
        super().__init__()
        self.proto_dir = proto_dir
        self.read_only = read_only

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        protos = sorted(f for f in self.proto_dir.glob("*.pro") if f.is_file())
        with Vertical(id="proto-dialog"):
            yield Static("Select Protocol Script", id="proto-title")
            ol = OptionList(id="proto-list")
            for f in protos:
                ol.add_option(Option(f.name, id=str(f)))
            if protos:
                ol.highlighted = 0
            yield ol
            has_protos = bool(protos)
            with Horizontal(id="proto-buttons"):
                yield Button(
                    "Run", id="proto-run", variant="success", disabled=not has_protos
                )
                yield Button(
                    "Debug",
                    id="proto-debug",
                    variant="warning",
                    disabled=not has_protos,
                )
                yield Button(
                    "Edit",
                    id="proto-edit",
                    variant="primary",
                    disabled=not has_protos or self.read_only,
                )
                new_btn = Button("New", id="proto-new")
                new_btn.styles.background = "darkorchid"
                yield new_btn
                yield Button(
                    "Delete",
                    id="proto-delete",
                    variant="warning",
                    disabled=not has_protos or self.read_only,
                )
                yield Button("Cancel", id="proto-cancel", variant="error")

    def _selected_path(self) -> str | None:
        """Return the path of the currently highlighted option.

        Returns:
            Absolute path string, or None if nothing is highlighted.
        """
        ol = self.query_one("#proto-list", OptionList)
        if ol.highlighted is not None:
            return str(ol.get_option_at_index(ol.highlighted).id)
        return None

    @on(Button.Pressed, "#proto-delete")
    def delete_proto(self) -> None:
        path = self._selected_path()
        if path:
            self.dismiss(("delete", path))

    @on(Button.Pressed, "#proto-new")
    def new_proto(self) -> None:
        self.dismiss(("new",))

    @on(Button.Pressed, "#proto-edit")
    def edit_proto(self) -> None:
        path = self._selected_path()
        if path:
            self.dismiss(("edit", path))

    @on(Button.Pressed, "#proto-run")
    def run_proto(self) -> None:
        path = self._selected_path()
        if path:
            self.dismiss(("run", path))

    @on(Button.Pressed, "#proto-debug")
    def debug_proto(self) -> None:
        path = self._selected_path()
        if path:
            self.dismiss(("debug", path))

    def on_key(self, event: events.Key) -> None:
        """Run the highlighted proto script when Enter is pressed in the list."""
        if event.key != "enter":
            return
        if not isinstance(self.focused, OptionList):
            return
        event.prevent_default()
        event.stop()
        path = self._selected_path()
        if path:
            self.dismiss(("run", path))

    @on(Button.Pressed, "#proto-cancel")
    def cancel_picker(self) -> None:
        self.dismiss(None)


class ProtoEditor(ModalScreen[str | None]):
    """Modal editor for .pro protocol script files with TOML highlighting."""

    BINDINGS = _CTRL_Q_BINDING

    CSS = f"""
    ProtoEditor {{ align: center middle; }}
    ProtoEditor Button {{ {_MODAL_BTN_CSS} }}
    #ped-dialog {{
        width: 90%; height: 90%;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #ped-title {{ height: 1; text-style: bold; }}
    #ped-editor {{ height: 1fr; border: thick $primary; }}
    #ped-name-row {{ height: 1; }}
    #ped-name {{ width: 1fr; height: 1; border: none; }}
    #ped-save-as-row {{ height: 1; display: none; }}
    #ped-save-as-row.visible {{ display: block; }}
    #ped-save-as-input {{ width: 1fr; height: 1; border: none; }}
    #ped-error {{ height: 1; color: $error; display: none; }}
    #ped-error.visible {{ display: block; }}
    #ped-buttons {{ height: 1; align: right middle; }}
    """

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q."""
        self.dismiss(None)

    def __init__(self, proto_dir: Path, path: str | None = None) -> None:
        super().__init__()
        self.proto_dir = proto_dir
        self.edit_path = path
        self._save_as_mode = False
        self._overwrite_ok = False

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        if self.edit_path:
            name = Path(self.edit_path).stem
            try:
                content = Path(self.edit_path).read_text(encoding="utf-8")
            except OSError:
                content = f"# Error: could not read {self.edit_path}\n"
            title = f"Edit: {Path(self.edit_path).name}"
        else:
            name = ""
            content = PROTO_TEMPLATE
            title = "New Protocol Script"

        with Vertical(id="ped-dialog"):
            yield Static(title, id="ped-title")
            yield TextArea(
                content,
                language="toml",
                show_line_numbers=True,
                id="ped-editor",
            )
            with Horizontal(id="ped-name-row"):
                yield Input(
                    placeholder="script name (without .pro)",
                    value=name,
                    id="ped-name",
                )
            with Horizontal(id="ped-save-as-row"):
                yield Input(
                    placeholder="new filename (without .pro)",
                    id="ped-save-as-input",
                )
            yield Static("", id="ped-error")
            with Horizontal(id="ped-buttons"):
                yield Button("Save", id="ped-save", variant="success")
                yield Button("Save As", id="ped-save-as", variant="primary")
                yield Button("Cancel", id="ped-cancel", variant="error")

    def _show_error(self, msg: str) -> None:
        """Display an error message in the editor.

        Args:
            msg: Error text to show.
        """
        from textual.widgets import Static

        err = self.query_one("#ped-error", Static)
        err.update(msg)
        err.add_class("visible")

    @on(Button.Pressed, "#ped-save")
    def save_proto(self) -> None:
        if self._save_as_mode:
            self._do_save_as()
            return
        name = self.query_one("#ped-name", Input).value.strip()
        if not name:
            self._show_error("Enter a script name")
            return
        if not name.endswith(".pro"):
            name += ".pro"
        content = self.query_one("#ped-editor", TextArea).text
        path = self.proto_dir / name
        path.write_text(content, encoding="utf-8")
        self.dismiss(str(path))

    @on(Button.Pressed, "#ped-save-as")
    def save_as_proto(self) -> None:
        self._save_as_mode = True
        self._overwrite_ok = False
        self.query_one("#ped-save-as-row").add_class("visible")
        self.query_one("#ped-save-as").display = False
        self.query_one("#ped-save-as-input", Input).focus()

    @on(Input.Submitted, "#ped-save-as-input")
    def save_as_on_enter(self) -> None:
        self._do_save_as()

    def _do_save_as(self) -> None:
        name = self.query_one("#ped-save-as-input", Input).value.strip()
        if not name:
            self._show_error("Enter a filename")
            return
        if not name.endswith(".pro"):
            name += ".pro"
        path = self.proto_dir / name
        if path.exists() and not self._overwrite_ok:
            self._show_error(f"{name} exists — click Save again to overwrite")
            self._overwrite_ok = True
            return
        content = self.query_one("#ped-editor", TextArea).text
        path.write_text(content, encoding="utf-8")
        self.dismiss(str(path))

    @on(Input.Submitted, "#ped-name")
    def save_on_enter(self) -> None:
        self.save_proto()

    @on(Button.Pressed, "#ped-cancel")
    def cancel_editor(self) -> None:
        self.dismiss(None)


class ScriptEditor(ModalScreen[str | None]):
    """Modal editor for .run script files with bash syntax highlighting."""

    BINDINGS = _CTRL_Q_BINDING

    CSS = f"""
    ScriptEditor {{ align: center middle; }}
    ScriptEditor Button {{ {_MODAL_BTN_CSS} }}
    #sed-dialog {{
        width: 90%; height: 90%;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #sed-title {{ height: 1; text-style: bold; }}
    #sed-editor {{ height: 1fr; border: thick $primary; }}
    #sed-name-row {{ height: 1; }}
    #sed-name {{ width: 1fr; height: 1; border: none; }}
    #sed-save-as-row {{ height: 1; display: none; }}
    #sed-save-as-row.visible {{ display: block; }}
    #sed-save-as-input {{ width: 1fr; height: 1; border: none; }}
    #sed-error {{ height: 1; color: $error; display: none; }}
    #sed-error.visible {{ display: block; }}
    #sed-buttons {{ height: 1; align: right middle; }}
    """

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q."""
        self.dismiss(None)

    def __init__(self, scripts_dir: Path, path: str | None = None) -> None:
        super().__init__()
        self.scripts_dir = scripts_dir
        self.edit_path = path
        self._save_as_mode = False
        self._overwrite_ok = False

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        if self.edit_path:
            name = Path(self.edit_path).stem
            try:
                content = Path(self.edit_path).read_text(encoding="utf-8")
            except OSError:
                content = f"# Error: could not read {self.edit_path}\n"
            title = f"Edit: {Path(self.edit_path).name}"
        else:
            name = ""
            content = SCRIPT_TEMPLATE.format(name="untitled")
            title = "New Script"

        with Vertical(id="sed-dialog"):
            yield Static(title, id="sed-title")
            yield TextArea(
                content,
                language="bash",
                show_line_numbers=True,
                id="sed-editor",
            )
            with Horizontal(id="sed-name-row"):
                yield Input(
                    placeholder="script name (without .run)",
                    value=name,
                    id="sed-name",
                )
            with Horizontal(id="sed-save-as-row"):
                yield Input(
                    placeholder="new filename (without .run)",
                    id="sed-save-as-input",
                )
            yield Static("", id="sed-error")
            with Horizontal(id="sed-buttons"):
                yield Button("Save", id="sed-save", variant="success")
                yield Button("Save As", id="sed-save-as", variant="primary")
                yield Button("Cancel", id="sed-cancel", variant="error")

    def _show_error(self, msg: str) -> None:
        from textual.widgets import Static

        err = self.query_one("#sed-error", Static)
        err.update(msg)
        err.add_class("visible")

    @on(Button.Pressed, "#sed-save")
    def save_script(self) -> None:
        if self._save_as_mode:
            self._do_save_as()
            return
        name = self.query_one("#sed-name", Input).value.strip()
        if not name:
            self._show_error("Enter a script name")
            return
        if not name.endswith(".run"):
            name += ".run"
        content = self.query_one("#sed-editor", TextArea).text
        path = self.scripts_dir / name
        path.write_text(content, encoding="utf-8")
        self.dismiss(str(path))

    @on(Button.Pressed, "#sed-save-as")
    def save_as_script(self) -> None:
        self._save_as_mode = True
        self._overwrite_ok = False
        self.query_one("#sed-save-as-row").add_class("visible")
        self.query_one("#sed-save-as").display = False
        self.query_one("#sed-save-as-input", Input).focus()

    @on(Input.Submitted, "#sed-save-as-input")
    def save_as_on_enter(self) -> None:
        self._do_save_as()

    def _do_save_as(self) -> None:
        name = self.query_one("#sed-save-as-input", Input).value.strip()
        if not name:
            self._show_error("Enter a filename")
            return
        if not name.endswith(".run"):
            name += ".run"
        path = self.scripts_dir / name
        if path.exists() and not self._overwrite_ok:
            self._show_error(f"{name} exists — click Save again to overwrite")
            self._overwrite_ok = True
            return
        content = self.query_one("#sed-editor", TextArea).text
        path.write_text(content, encoding="utf-8")
        self.dismiss(str(path))

    @on(Input.Submitted, "#sed-name")
    def save_on_enter(self) -> None:
        self.save_script()

    @on(Button.Pressed, "#sed-cancel")
    def cancel_editor(self) -> None:
        self.dismiss(None)


class CfgConfirm(ModalScreen[bool]):
    """Modal dialog to confirm a config change."""

    BINDINGS = _CTRL_Q_BINDING

    CSS = f"""
    CfgConfirm {{ align: center middle; }}
    CfgConfirm Button {{ {_MODAL_BTN_CSS} }}
    #cfg-confirm-dialog {{
        width: 50; height: 7;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #cfg-confirm-msg {{ height: 1; }}
    #cfg-confirm-buttons {{ height: 1; align: right middle; }}
    """

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q."""
        self.dismiss(False)

    def __init__(self, key: str, old_val, new_val) -> None:
        super().__init__()
        self.key = key
        self.old_val = old_val
        self.new_val = new_val

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        with Vertical(id="cfg-confirm-dialog"):
            yield Static(
                f"{self.key}: {self.old_val!r} → {self.new_val!r}",
                id="cfg-confirm-msg",
            )
            with Horizontal(id="cfg-confirm-buttons"):
                yield Button("Yes", id="cfg-yes", variant="success")
                yield Button("No", id="cfg-no", variant="error")

    @on(Button.Pressed, "#cfg-yes")
    def confirm(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#cfg-no")
    def cancel(self) -> None:
        self.dismiss(False)


class ConfirmDialog(ModalScreen[bool]):
    """Generic Yes/Cancel confirmation dialog.

    Args:
        message: Text to display in the dialog.
    """

    BINDINGS = _CTRL_Q_BINDING

    CSS = f"""
    ConfirmDialog {{ align: center middle; }}
    ConfirmDialog Button {{ {_MODAL_BTN_CSS} }}
    #confirm-dialog {{
        width: 60; height: auto; max-height: 15;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #confirm-msg {{ height: auto; }}
    #confirm-buttons {{ height: 1; align: right middle; }}
    """

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q."""
        self.dismiss(False)

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        with Vertical(id="confirm-dialog"):
            yield Static(self.message, id="confirm-msg")
            with Horizontal(id="confirm-buttons"):
                yield Button("Yes", id="confirm-yes", variant="success")
                yield Button("Cancel", id="confirm-no", variant="error")

    def on_mount(self) -> None:
        self.query_one("#confirm-yes", Button).focus()

    @on(Button.Pressed, "#confirm-yes")
    def confirm(self) -> None:
        """Dismiss with True."""
        self.dismiss(True)

    @on(Button.Pressed, "#confirm-no")
    def cancel(self) -> None:
        """Dismiss with False."""
        self.dismiss(False)


class WelcomeDialog(ModalScreen[None]):
    """Modal welcome message with a single OK button."""

    BINDINGS = _CTRL_Q_BINDING

    CSS = f"""
    WelcomeDialog {{ align: center middle; }}
    WelcomeDialog Button {{ {_MODAL_BTN_CSS} }}
    #welcome-dialog {{
        width: 70; height: 12;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #welcome-title {{ height: 1; text-style: bold; }}
    #welcome-msg {{ height: 1fr; }}
    #welcome-buttons {{ height: 1; align: center middle; }}
    """

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    def __init__(self, title: str, message: str) -> None:
        super().__init__()
        self.title_text = title
        self.message = message

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        with Vertical(id="welcome-dialog"):
            yield Static(self.title_text, id="welcome-title")
            yield Static(self.message, id="welcome-msg")
            with Horizontal(id="welcome-buttons"):
                yield Button("OK", id="welcome-ok", variant="success")

    @on(Button.Pressed, "#welcome-ok")
    def ok_pressed(self) -> None:
        self.dismiss(None)


class PortPicker(ModalScreen[str | None]):
    """Modal dialog to select an available serial port."""

    BINDINGS = _CTRL_Q_BINDING

    CSS = f"""
    PortPicker {{ align: center middle; }}
    PortPicker Button {{ {_MODAL_BTN_CSS} }}
    #port-dialog {{
        width: 60; height: 18;
        border: solid $primary; background: $surface; padding: 1 2;
    }}
    #port-title {{ height: 1; text-style: bold; }}
    #port-list {{ height: 1fr; border: thick $primary; }}
    #port-buttons {{ height: 1; align: right middle; }}
    """

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q."""
        self.dismiss(None)

    def compose(self) -> ComposeResult:
        from serial.tools.list_ports import comports
        from textual.widgets import Static

        ports = sorted(comports(), key=lambda p: p.device)
        with Vertical(id="port-dialog"):
            yield Static("Select Serial Port", id="port-title")
            ol = OptionList(id="port-list")
            if ports:
                for p in ports:
                    desc = p.description or ""
                    label = f"{p.device} - {desc}" if desc else p.device
                    ol.add_option(Option(label, id=p.device))
            else:
                ol.add_option(Option("(no ports found)", disabled=True))
            yield ol
            with Horizontal(id="port-buttons"):
                yield Button("Cancel", id="port-cancel", variant="error")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.id))

    @on(Button.Pressed, "#port-cancel")
    def cancel_port_picker(self) -> None:
        self.dismiss(None)
