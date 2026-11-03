"""The ConfigEditor dialog: what it shows, how it validates, what it dismisses with.

Pushed into a bare host app and driven with Pilot (the
``tests/test_dialogs.py`` pattern).  The config file lives under
``tmp_path``; a Save As always names a folder so nothing lands in the
checkout's ``termapy_cfg/``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Button, Input, Label, Static, TextArea

from termapy.defaults import CFG_HELP, DEFAULT_CFG, DEFAULT_CMD_PREFIX
from termapy.dialogs._common import find_occurrences, pick_occurrence
from termapy.dialogs.config_editor import ConfigEditor
from termapy.dialogs.confirm_dialog import ConfirmDialog
from termapy.plugins import PluginContext
from termapy.plugins.command import interpolate_help
from termapy.port_control import reserved_serial
from termapy.variables import set_user_var


class _Host(App):
    """Bare host: no widgets of its own, just a stage for modals."""


def _run(scenario) -> None:
    asyncio.run(scenario())


def _write_cfg(tmp_path: Path, **overrides) -> tuple[dict, Path]:
    """A DEMO config on disk; ``overrides`` go into the file verbatim."""
    serial = DEFAULT_CFG["serial"]
    assert isinstance(serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {**DEFAULT_CFG, "serial": {**serial, "port": "DEMO"}, "title": "Probe", **overrides}
    path = tmp_path / "probe" / "probe.cfg"
    path.parent.mkdir()
    path.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
    return cfg, path


async def _open(
    app: App, pilot, cfg: dict, path: Path, highlight_key: str = "", used_by=None, ctx=None
):
    """Push the editor and return it with the list its dismiss value lands in."""
    results: list[tuple | None] = []
    app.push_screen(
        ConfigEditor(cfg, str(path), highlight_key, used_by=used_by, ctx=ctx),
        callback=results.append,
    )
    await pilot.pause()
    screen = app.screen
    assert isinstance(screen, ConfigEditor), "the editor is on top"
    return screen, results


def _plain(widget: Static) -> str:
    """The widget's text with the markup rendered away."""
    return str(widget.render())


class TestWhatItShows:
    def test_editor_shows_the_disk_json_not_the_expanded_cfg(self, tmp_path):
        """The text area holds the file as written, so a $(env.NAME) template stays visible instead of its expansion."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path, title="$(env.NOPE_UNSET_123)")
            app = _Host()
            async with app.run_test() as pilot:
                screen, _ = await _open(app, pilot, {**cfg, "title": "expanded"}, path)
                text = screen.query_one("#config-editor", TextArea).text
                assert '"$(env.NOPE_UNSET_123)"' in text, "the template, not the expansion"
                assert "expanded" not in text

        _run(scenario)

    def test_highlight_key_puts_the_cursor_on_that_line(self, tmp_path):
        """Opened for a key, the cursor sits on that key's line so the help describes it at once."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            app = _Host()
            async with app.run_test() as pilot:
                screen, _ = await _open(app, pilot, cfg, path, highlight_key="eol")
                editor = screen.query_one("#config-editor", TextArea)
                row, _ = editor.cursor_location
                assert '"eol"' in str(editor.get_line(row)), "cursor on the eol line"

        _run(scenario)


class TestHelpLine:
    def test_the_help_line_names_the_commands_that_declared_the_key(self, tmp_path):
        """cfg-declared: on a key some command declared, the panel ends with those commands; on a key none did, no such line."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            app = _Host()
            used_by = {
                "serial.port": [
                    ("port.disconnect", "the port it closes"),
                    ("port.info", "the port it reports"),
                ]
            }
            async with app.run_test() as pilot:
                # Act -- opened on the port line, whose leaf resolves to serial.port
                screen, _ = await _open(
                    app, pilot, cfg, path, highlight_key="port", used_by=used_by
                )
                await pilot.pause()

                # Assert
                help_text = _plain(screen.query_one("#config-help", Static))
                assert "Used by: /port.disconnect, /port.info" in help_text, help_text

                # Act / Assert -- a key nothing declared
                editor = screen.query_one("#config-editor", TextArea)
                editor.cursor_location = (_title_row(editor), 0)
                await pilot.pause()
                assert "Used by" not in _plain(screen.query_one("#config-help", Static))

        _run(scenario)

    def test_help_describes_the_key_under_the_cursor(self, tmp_path):
        """Moving the cursor onto a known key shows its line number, description, and value."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            app = _Host()
            async with app.run_test() as pilot:
                screen, _ = await _open(app, pilot, cfg, path)
                editor = screen.query_one("#config-editor", TextArea)
                row = next(
                    r
                    for r in range(editor.document.line_count)
                    if '"eol"' in str(editor.get_line(r))
                )

                # Act
                editor.cursor_location = (row, 0)
                await pilot.pause()

                # Assert
                help_text = _plain(screen.query_one("#config-help", Static))
                expected = interpolate_help(CFG_HELP["eol"][0], DEFAULT_CMD_PREFIX)
                assert f"Line {row + 1}: {expected}" in help_text, help_text
                assert "Value: eol = \\r" in help_text, help_text

        _run(scenario)

    def test_a_managed_key_is_headed_do_not_edit_in_red(self, tmp_path):
        """config_version's help opens with a red DO NOT EDIT, rendered: no markup tag shows as text."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            app = _Host()
            async with app.run_test() as pilot:
                # Act
                screen, _ = await _open(app, pilot, cfg, path, highlight_key="config_version")
                await pilot.pause()

                # Assert
                content = screen.query_one("#config-help", Static).render()
                help_text = str(content)
                expected = f": DO NOT EDIT - {CFG_HELP['config_version'][0]}"
                assert expected in help_text, help_text
                assert "[" not in help_text, f"no markup tag shows as text: {help_text!r}"
                start = help_text.index("DO NOT EDIT")
                red = [
                    span
                    for span in content.spans
                    if span.start <= start < span.end and "red" in str(span.style)
                ]
                assert red, f"DO NOT EDIT is red: {content.spans!r}"

        _run(scenario)

    def test_an_enum_key_lists_its_values_once(self, tmp_path):
        """custom_buttons_side's help lists bottom, left, right after one Valid label (it read "Valid: Valid:")."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            app = _Host()
            async with app.run_test() as pilot:
                # Act
                screen, _ = await _open(app, pilot, cfg, path, highlight_key="custom_buttons_side")
                await pilot.pause()

                # Assert
                help_text = _plain(screen.query_one("#config-help", Static))
                assert "Valid: bottom, left, right." in help_text, help_text
                assert help_text.count("Valid:") == 1, f"one label, not two: {help_text!r}"

        _run(scenario)

    def test_no_hint_carries_its_own_valid_label(self):
        """The help line labels every hint "Valid:" itself, so a hint starting with it shows the label twice."""

        # Act
        actual = sorted(
            key
            for key, entry in CFG_HELP.items()
            if isinstance(entry[1], str) and entry[1].startswith("Valid:")
        )

        # Assert
        assert actual == [], f"drop the 'Valid:' prefix from these hints: {actual}"

    def test_unknown_key_help_suggests_the_nearest(self, tmp_path):
        """A misspelled key is called out with the closest real key as a suggestion."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            app = _Host()
            async with app.run_test() as pilot:
                screen, _ = await _open(app, pilot, cfg, path)
                editor = screen.query_one("#config-editor", TextArea)

                # Act
                editor.text = '{\n    "eco": false\n}'
                editor.cursor_location = (1, 0)
                await pilot.pause()

                # Assert
                help_text = _plain(screen.query_one("#config-help", Static))
                assert 'Line 2: Unknown key "eco"' in help_text
                assert 'Did you mean "echo"?' in help_text

        _run(scenario)

    def test_preview_key_shows_its_preview(self, tmp_path):
        """A key with a preview callable (echo_fmt) renders the preview instead of an error."""

        async def scenario():
            # The default echo_fmt is a $(CFG) template, which takes the
            # unresolved-variable path; a literal format reaches the preview.
            cfg, path = _write_cfg(tmp_path, echo_fmt="[purple]> {cmd}[/]")
            app = _Host()
            async with app.run_test() as pilot:
                screen, _ = await _open(app, pilot, cfg, path, highlight_key="echo_fmt")
                await pilot.pause()
                help_text = _plain(screen.query_one("#config-help", Static))
                assert "Preview: " in help_text, help_text

        _run(scenario)

    def test_a_line_that_is_not_a_key_clears_the_help(self, tmp_path):
        """The opening brace has no key, so the help line goes blank there."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path, title="x")
            app = _Host()
            async with app.run_test() as pilot:
                screen, _ = await _open(app, pilot, cfg, path, highlight_key="title")
                await pilot.pause()
                editor = screen.query_one("#config-editor", TextArea)

                # Act
                editor.cursor_location = (0, 0)
                await pilot.pause()

                # Assert
                assert _plain(screen.query_one("#config-help", Static)) == ""

        _run(scenario)


class TestValueValidation:
    """``_validate_value`` branch by branch: the status line and the error it pairs with."""

    @pytest.mark.parametrize(
        ("key", "raw", "fragment"),
        [
            ("echo", "maybe", "must be true or false"),
            ("echo", '"false"', "must be true or false"),
            ("custom_baud", "maybe", "must be true or false"),
            ("parity", '"X"', "must be one of: E, M, N, O, S"),
            ("json_style", '"pretty"', "must be one of: color, compact, indent"),
            ("custom_buttons_side", '"top"', "must be one of: bottom, left, right"),
            ("baud_rate", '"fast"', "must be a positive integer"),
            ("baud_rate", "0", "must be a positive integer"),
            ("cmd_delay_ms", "-1", "must be a non-negative integer"),
            ("cmd_delay_ms", '"soon"', "must be a non-negative integer"),
            ("eol", '"abc"', "must be CR/LF/NUL/ETX/EOT bytes only"),
        ],
    )
    def test_invalid_values_name_the_rule(self, tmp_path, key, raw, fragment):
        """Each typed rule (bool, enum, baud, int, eol) reports the rule the value broke."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            app = _Host()
            async with app.run_test() as pilot:
                screen, _ = await _open(app, pilot, cfg, path)
                status, error = screen._validate_value(key, raw)
                assert status.startswith("[red]"), "an invalid value renders red"
                assert fragment in error, error

        _run(scenario)

    def test_a_templated_bool_shows_what_it_reads_as(self, tmp_path, monkeypatch):
        """A $(env.X) in a boolean key is read with the vocabulary: yes shows green as true, maybe shows the Invalid message in red."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            app = _Host()
            async with app.run_test() as pilot:
                screen, _ = await _open(app, pilot, cfg, path)
                monkeypatch.setenv("CFG_ED_BOOL", "yes")
                status, error = screen._validate_value("echo", '"$(env.CFG_ED_BOOL)"')
                assert status.startswith("[green]") and status.endswith("-> true"), status
                assert error == "", "a readable token is not an error"
                monkeypatch.setenv("CFG_ED_BOOL", "maybe")
                status, error = screen._validate_value("echo", '"$(env.CFG_ED_BOOL)"')
                assert status.startswith("[red]"), status
                assert "Invalid echo: maybe (use on/off/" in error, error

        _run(scenario)

    def test_non_standard_baud_is_a_warning_unless_custom(self, tmp_path):
        """A non-standard rate is yellow with a hint, green and marked custom once custom_baud is true in the editor."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            app = _Host()
            async with app.run_test() as pilot:
                screen, _ = await _open(app, pilot, cfg, path)
                editor = screen.query_one("#config-editor", TextArea)

                # Act
                _, hint = screen._validate_value("baud_rate", "12345")
                editor.text = json.dumps({**cfg, "serial": {**cfg["serial"], "custom_baud": True}})
                await pilot.pause()
                custom_status, custom_error = screen._validate_value("baud_rate", "12345")
                _, low_error = screen._validate_value("baud_rate", "100")

                # Assert
                assert "not a standard rate" in hint, "a hint, not an error"
                assert "(custom)" in custom_status and custom_error == "", (
                    "custom_baud makes it green"
                )
                assert "custom baud requires >= 300" in low_error

        _run(scenario)

    def test_demo_port_is_the_simulated_port(self, tmp_path):
        """DEMO is accepted as the simulated port whatever the machine has plugged in."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            app = _Host()
            async with app.run_test() as pilot:
                screen, _ = await _open(app, pilot, cfg, path)
                status, error = screen._validate_value("port", '"DEMO"')
                assert "(simulated port)" in status and error == ""

        _run(scenario)

    def test_a_serial_number_port_that_resolves_is_green(self, tmp_path):
        """port-present-one-decider: a USB serial number the connect would find is accepted, not 'port not found'."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            app = _Host()
            async with app.run_test() as pilot:
                screen, _ = await _open(app, pilot, cfg, path)
                status, error = screen._validate_value("port", f'"{reserved_serial("DEMO")}"')
                assert status.startswith("[green]"), "the serial number resolves, so it is here"
                assert error == "", "no 'port not found' for a port the connect finds"

        _run(scenario)

    def test_template_values_show_their_expansion(self, tmp_path):
        """An unset $(env.NAME) is flagged unresolved; a set one shows the template, an arrow, and the resolved value's status."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            app = _Host()
            async with app.run_test() as pilot:
                screen, _ = await _open(app, pilot, cfg, path)
                _, unresolved = screen._validate_value("title", '"$(env.NOPE_UNSET_123)"')
                resolved_status, resolved_error = screen._validate_value("title", '"$(env.PATH)"')
                assert "variable - not resolved" in unresolved
                assert "->" in resolved_status and resolved_error == "", (
                    "the resolved value is validated"
                )

        _run(scenario)

    def test_a_session_variable_is_known_and_a_typo_is_not(self, tmp_path):
        """$(DATE) in a value is a session variable expanded when the value is used, never 'not resolved'; $(NOPE) is an unknown variable."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            app = _Host()
            async with app.run_test() as pilot:
                screen, _ = await _open(app, pilot, cfg, path)

                # Act
                known_status, known_note = screen._validate_value(
                    "echo_fmt", '"[purple]$(DATE)> {cmd}[/]"'
                )
                unknown_status, unknown_note = screen._validate_value(
                    "echo_fmt", '"$(NOPE_UNKNOWN_123)> {cmd}"'
                )

                # Assert
                assert known_status.startswith("[green]"), "a known variable is a valid value"
                assert (
                    "$(DATE) is a session variable, expanded when the value is used" in known_note
                )
                assert "not resolved" not in known_note
                assert unknown_status.startswith("[yellow]")
                assert "unknown variable: $(NOPE_UNKNOWN_123)" in unknown_note

        _run(scenario)

    def test_a_user_variable_is_known_through_the_sessions_context(self, tmp_path):
        """A variable the session set is known to the editor it opens, and unknown to one with no context."""

        async def scenario():
            # Arrange
            cfg, path = _write_cfg(tmp_path)
            ctx = PluginContext()
            set_user_var(ctx, "MY_TAG", "rig")
            app = _Host()
            async with app.run_test() as pilot:
                value = '"$(MY_TAG)> {cmd}"'

                # Act
                with_ctx, _ = await _open(app, pilot, cfg, path, ctx=ctx)
                known_status, _ = with_ctx._validate_value("echo_fmt", value)
                without, _ = await _open(app, pilot, cfg, path)
                unknown_status, unknown_note = without._validate_value("echo_fmt", value)

                # Assert
                assert known_status.startswith("[green]"), "the session holds that variable"
                assert unknown_status.startswith("[yellow]")
                assert "unknown variable: $(MY_TAG)" in unknown_note

        _run(scenario)


class TestLiveJsonCheck:
    def test_a_broken_edit_names_the_problem_and_the_line(self, tmp_path):
        """Typing past a missing comma shows the error line and a plain-words reason, then clears when fixed."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            app = _Host()
            async with app.run_test() as pilot:
                screen, _ = await _open(app, pilot, cfg, path)
                editor = screen.query_one("#config-editor", TextArea)
                error = screen.query_one("#config-error", Static)

                # Act
                editor.text = '{\n    "a": 1\n    "b": 2\n}'
                await pilot.pause()
                shown, message = error.has_class("visible"), _plain(error)
                editor.text = '{\n    "a": 1,\n    "b": 2\n}'
                await pilot.pause()

                # Assert
                assert shown is True and message == "JSON error line 3: missing comma", message
                assert error.has_class("visible") is False, "fixed JSON hides the error"

        _run(scenario)


class TestSave:
    def test_save_refuses_invalid_json(self, tmp_path):
        """Save on broken JSON keeps the dialog open with the parse error shown."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            app = _Host()
            async with app.run_test() as pilot:
                screen, results = await _open(app, pilot, cfg, path)
                screen.query_one("#config-editor", TextArea).text = "{not json"
                await pilot.pause()

                # Act
                await pilot.click("#cfg-save")
                await pilot.pause()

                # Assert
                assert results == [], "not dismissed"
                assert _plain(screen.query_one("#config-error", Static)).startswith("Invalid JSON")

        _run(scenario)

    def test_save_refuses_a_config_with_warnings(self, tmp_path):
        """A config the validator warns about (an unknown key) is not written; the warnings are shown instead."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            app = _Host()
            async with app.run_test() as pilot:
                screen, results = await _open(app, pilot, cfg, path)
                screen.query_one("#config-editor", TextArea).text = json.dumps(
                    {**cfg, "bogus_key": 1}
                )
                await pilot.pause()

                # Act
                await pilot.click("#cfg-save")
                await pilot.pause()

                # Assert
                assert results == [], "not dismissed"
                assert "bogus_key" in _plain(screen.query_one("#config-error", Static))
                assert json.loads(path.read_text(encoding="utf-8"))["title"] == "Probe", (
                    "file untouched"
                )

        _run(scenario)

    def test_save_writes_the_file_and_dismisses_with_it(self, tmp_path):
        """A valid edit is written to the same path and the dialog dismisses with (cfg, path)."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            app = _Host()
            async with app.run_test() as pilot:
                screen, results = await _open(app, pilot, cfg, path)
                screen.query_one("#config-editor", TextArea).text = json.dumps(
                    {**cfg, "title": "Renamed"}
                )
                await pilot.pause()

                # Act
                await pilot.click("#cfg-save")
                await pilot.pause()

                # Assert
                assert json.loads(path.read_text(encoding="utf-8"))["title"] == "Renamed", "written"
                assert len(results) == 1 and results[0] is not None, "dismissed once"
                saved_cfg, saved_path = results[0]
                assert (saved_cfg["title"], saved_path) == ("Renamed", str(path))

        _run(scenario)

    def test_save_writes_the_standard_order(self, tmp_path):
        """cfg-written-in-order: keys typed in any order are saved in DEFAULT_CFG's order."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            app = _Host()
            async with app.run_test() as pilot:
                screen, _ = await _open(app, pilot, cfg, path)
                screen.query_one("#config-editor", TextArea).text = json.dumps(
                    dict(reversed(list(cfg.items())))
                )
                await pilot.pause()

                # Act
                await pilot.click("#cfg-save")
                await pilot.pause()

                # Assert
                actual = list(json.loads(path.read_text(encoding="utf-8")))
                assert actual == list(DEFAULT_CFG), "the file reads in the defaults' order"

        _run(scenario)

    def test_cancel_and_escape_dismiss_none(self, tmp_path):
        """Cancel and Escape both dismiss with None and leave the file alone."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            app = _Host()
            async with app.run_test() as pilot:
                _, results = await _open(app, pilot, cfg, path)
                await pilot.click("#cfg-cancel")
                await pilot.pause()
                _, results_escape = await _open(app, pilot, cfg, path)
                await pilot.press("escape")
                await pilot.pause()
                assert (results, results_escape) == ([None], [None])

        _run(scenario)


class TestSaveAs:
    def test_save_as_reveals_the_row_and_needs_a_name(self, tmp_path):
        """Save As swaps in the filename row; Save then acts as Save As and refuses an empty name."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            app = _Host()
            async with app.run_test() as pilot:
                screen, results = await _open(app, pilot, cfg, path)

                # Act
                await pilot.click("#cfg-save-as")
                await pilot.pause()
                row_shown = screen.query_one("#save-as-row").has_class("visible")
                button_hidden = screen.query_one("#cfg-save-as", Button).display is False
                await pilot.click("#cfg-save")
                await pilot.pause()

                # Assert
                assert (row_shown, button_hidden) == (True, True)
                assert (
                    results == []
                    and _plain(screen.query_one("#config-error", Static)) == "Enter a filename"
                )

        _run(scenario)

    def test_save_as_rejects_a_non_cfg_extension(self, tmp_path):
        """A name with another extension is refused rather than silently renamed."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            app = _Host()
            async with app.run_test() as pilot:
                screen, results = await _open(app, pilot, cfg, path)
                await pilot.click("#cfg-save-as")
                await pilot.pause()
                screen.query_one("#save-as-input", Input).value = str(tmp_path / "copy.txt")

                # Act
                await pilot.click("#cfg-save")
                await pilot.pause()

                # Assert
                assert results == []
                assert (
                    _plain(screen.query_one("#config-error", Static))
                    == "File must have a .cfg extension"
                )

        _run(scenario)

    def test_save_as_writes_the_named_file_and_retitles(self, tmp_path):
        """Enter in the name row writes <name>.cfg where the name points, appends the stem to the title, and dismisses with the new path."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            target = tmp_path / "elsewhere" / "copy"
            app = _Host()
            async with app.run_test() as pilot:
                screen, results = await _open(app, pilot, cfg, path)
                await pilot.click("#cfg-save-as")
                await pilot.pause()
                name_input = screen.query_one("#save-as-input", Input)
                name_input.value = str(target)
                name_input.focus()
                await pilot.pause()

                # Act
                await pilot.press("enter")
                await pilot.pause()

                # Assert
                written = target.with_suffix(".cfg")
                assert written.exists(), ".cfg appended, folder created"
                assert json.loads(written.read_text(encoding="utf-8"))["title"] == "Probe - copy"
                assert len(results) == 1 and results[0] is not None, "dismissed once"
                assert results[0][1] == str(written), "the new path comes back"
                assert json.loads(path.read_text(encoding="utf-8"))["title"] == "Probe", (
                    "the original is untouched"
                )

        _run(scenario)


def _existing_target(tmp_path: Path) -> tuple[Path, Path]:
    """Another config already on disk, with a run script beside its .cfg."""
    target = tmp_path / "other" / "other.cfg"
    (target.parent / "run").mkdir(parents=True)
    target.write_text(json.dumps({"title": "old"}), encoding="utf-8")
    script = target.parent / "run" / "x.run"
    script.write_text("/print hi\n", encoding="utf-8")
    return target, script


async def _save_as(pilot, screen: ConfigEditor, target: Path) -> None:
    """Open the Save As row, type ``target`` and press Enter."""
    await pilot.click("#cfg-save-as")
    await pilot.pause()
    name_input = screen.query_one("#save-as-input", Input)
    name_input.value = str(target)
    name_input.focus()
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()


class TestCreatingOverAConfig:
    """config-created-once: Save As (and Save of a new config) asks before replacing a config."""

    def test_save_as_onto_a_config_asks_and_yes_replaces_only_the_cfg(self, tmp_path):
        """The overwrite question appears; Yes writes the .cfg, keeps the run script and dismisses."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            target, script = _existing_target(tmp_path)
            app = _Host()
            async with app.run_test() as pilot:
                screen, results = await _open(app, pilot, cfg, path)
                await _save_as(pilot, screen, target)
                asked = isinstance(app.screen, ConfirmDialog)

                # Act
                await pilot.click("#confirm-yes")
                await pilot.pause()

                # Assert
                assert asked, "the overwrite question was asked"
                assert json.loads(target.read_text(encoding="utf-8"))["title"] == "Probe - other"
                assert script.is_file(), "the run script is kept"
                assert results == [(json.loads(target.read_text(encoding="utf-8")), str(target))]

        _run(scenario)

    def test_save_as_onto_a_config_cancel_changes_nothing(self, tmp_path):
        """Cancel on the question leaves the other config as it was and the editor open."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            target, _ = _existing_target(tmp_path)
            app = _Host()
            async with app.run_test() as pilot:
                screen, results = await _open(app, pilot, cfg, path)
                await _save_as(pilot, screen, target)

                # Act
                await pilot.click("#confirm-no")
                await pilot.pause()

                # Assert
                assert json.loads(target.read_text(encoding="utf-8"))["title"] == "old"
                assert results == [], "the editor did not dismiss"
                assert app.screen is screen, "the editor is still open"

        _run(scenario)

    def test_save_of_a_new_config_over_an_existing_one_asks(self, tmp_path):
        """The wizard's Advanced editor (is_new) shows the new cfg and asks before its Save replaces a config."""

        async def scenario():
            target, _ = _existing_target(tmp_path)
            serial = DEFAULT_CFG["serial"]
            assert isinstance(serial, dict), "DEFAULT_CFG['serial'] is a dict"
            cfg = {**DEFAULT_CFG, "serial": {**serial, "port": "DEMO"}, "title": "other"}
            app = _Host()
            async with app.run_test() as pilot:
                app.push_screen(ConfigEditor(cfg, str(target), is_new=True))
                await pilot.pause()
                shown = app.screen.query_one("#config-editor", TextArea).text

                # Act
                await pilot.click("#cfg-save")
                await pilot.pause()

                # Assert
                assert '"title": "other"' in shown, "the editor shows the new cfg, not the file"
                assert isinstance(app.screen, ConfirmDialog), "Save asks before replacing"
                assert json.loads(target.read_text(encoding="utf-8"))["title"] == "old"

        _run(scenario)


class TestFindHelpers:
    """find_occurrences and pick_occurrence: the pure half of the editor's find."""

    def test_every_occurrence_is_listed_in_document_order_ignoring_case(self):
        """Two hits on one line and one on another come back in order, matched without regard to case."""
        lines = ['"title": "zqZQ",', "none", '"x": "zq"']
        actual = find_occurrences(lines, "ZQ")
        expected = [(0, 10, 2), (0, 12, 2), (2, 6, 2)]
        assert actual == expected

    def test_an_empty_needle_matches_nothing(self):
        """An empty field selects nothing rather than every position."""
        assert find_occurrences(["abc"], "") == []

    def test_occurrences_do_not_overlap(self):
        """``aa`` in ``aaa`` is one occurrence, not two."""
        assert find_occurrences(["aaa"], "aa") == [(0, 0, 2)]

    def test_the_needle_is_literal(self):
        """``$(CFG)`` and ``[/]`` are text to find, never a regex."""
        line = '"echo_fmt": "[purple]$(CFG)> {cmd}[/]",'
        assert find_occurrences([line], "$(CFG)") == [(0, 21, 6)]
        assert find_occurrences([line], "[/]") == [(0, line.index("[/]"), 3)]

    def test_forward_pick_is_inclusive_when_typing_and_exclusive_when_stepping(self):
        """Typing keeps the match under the cursor as the needle grows; Enter moves past it."""
        matches = [(0, 10, 2), (0, 12, 2), (2, 6, 2)]
        assert pick_occurrence(matches, (0, 10), 1, inclusive=True) == 0, "typing keeps the match"
        assert pick_occurrence(matches, (0, 10), 1, inclusive=False) == 1, "Enter moves on"

    def test_picks_wrap_at_both_ends(self):
        """Past the last match the first comes next; before the first, the last."""
        matches = [(0, 10, 2), (0, 12, 2), (2, 6, 2)]
        assert pick_occurrence(matches, (2, 6), 1, inclusive=False) == 0, "forward wrap"
        assert pick_occurrence(matches, (0, 10), -1, inclusive=False) == 2, "backward wrap"

    def test_no_matches_picks_none(self):
        """With nothing to show the pick is None, so the bar can say 0/0."""
        assert pick_occurrence([], (0, 0), 1, inclusive=True) is None


def _title_row(editor: TextArea) -> int:
    """The editor row holding the ``title`` key."""
    return next(
        r for r in range(editor.document.line_count) if '"title"' in str(editor.get_line(r))
    )


class TestFind:
    """The editor's find bar: any text, literal, case-insensitive; Escape closes the bar, not the dialog."""

    @staticmethod
    async def _open_find(app, pilot, cfg, path, key: str = "ctrl+f"):
        """Open the editor, focus its text and press the find key; return what the tests drive."""
        screen, results = await _open(app, pilot, cfg, path)
        editor = screen.query_one("#config-editor", TextArea)
        editor.focus()
        await pilot.pause()
        await pilot.press(key)
        await pilot.pause()
        field = screen.query_one("#config-find-input", Input)
        count = screen.query_one("#config-find-count", Label)
        return screen, results, editor, field, count

    @pytest.mark.parametrize("key", ["ctrl+f", "alt+f"])
    def test_ctrl_f_and_alt_f_open_the_bar_and_focus_its_field(self, tmp_path, key):
        """Both keys show the bar and put the cursor in its field; Alt+F is the VS Code fallback."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            app = _Host()
            async with app.run_test() as pilot:
                screen, _, _, field, _ = await self._open_find(app, pilot, cfg, path, key)

                # Assert
                assert screen.query_one("#config-find").has_class("visible"), "the bar shows"
                assert app.focused is field, "the field has the focus"

        _run(scenario)

    def test_the_find_button_opens_the_bar(self, tmp_path):
        """The Find button is the discoverable way in: a click shows the bar and focuses its field."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            app = _Host()
            async with app.run_test(size=(160, 50)) as pilot:
                screen, _ = await _open(app, pilot, cfg, path)

                # Act
                await pilot.click("#cfg-find")
                await pilot.pause()

                # Assert
                assert screen.query_one("#config-find").has_class("visible"), "the bar shows"
                assert app.focused is screen.query_one("#config-find-input", Input)

        _run(scenario)

    def test_typing_selects_the_first_match_and_the_help_panel_follows(self, tmp_path):
        """Each keystroke selects the first match after the cursor, counts it, and the key's help shows."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            app = _Host()
            async with app.run_test() as pilot:
                screen, _, editor, field, count = await self._open_find(app, pilot, cfg, path)

                # Act
                field.value = "max_grep"
                await pilot.pause()

                # Assert
                assert editor.selected_text == "max_grep", "the match is the selection"
                assert _plain(count) == "1/1"
                expected = interpolate_help(CFG_HELP["max_grep_lines"][0], DEFAULT_CMD_PREFIX)
                assert expected in _plain(screen.query_one("#config-help", Static)), (
                    "the help panel follows the selection to the found key"
                )

        _run(scenario)

    def test_enter_walks_two_matches_on_one_line_and_wraps(self, tmp_path):
        """Enter steps to the second match on the same line, then wraps to the first."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path, title="zqzq")
            app = _Host()
            async with app.run_test() as pilot:
                _, _, editor, field, count = await self._open_find(app, pilot, cfg, path)
                row = _title_row(editor)
                col = str(editor.get_line(row)).index("zqzq")
                field.value = "zq"
                await pilot.pause()
                first = (editor.selection.start, _plain(count))

                # Act
                await pilot.press("enter")
                await pilot.pause()
                second = (editor.selection.start, _plain(count))
                await pilot.press("enter")
                await pilot.pause()
                third = (editor.selection.start, _plain(count))

                # Assert
                assert first == ((row, col), "1/2")
                assert second == ((row, col + 2), "2/2"), "the second hit on the same line"
                assert third == ((row, col), "1/2"), "past the last match, the first again"

        _run(scenario)

    def test_up_steps_back_and_wraps_to_the_last(self, tmp_path):
        """Up from the first match wraps to the last; Up again comes back."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path, title="zqzq")
            app = _Host()
            async with app.run_test() as pilot:
                _, _, editor, field, count = await self._open_find(app, pilot, cfg, path)
                row = _title_row(editor)
                col = str(editor.get_line(row)).index("zqzq")
                field.value = "zq"
                await pilot.pause()

                # Act
                await pilot.press("up")
                await pilot.pause()
                wrapped = (editor.selection.start, _plain(count))
                await pilot.press("up")
                await pilot.pause()
                back = (editor.selection.start, _plain(count))

                # Assert
                assert wrapped == ((row, col + 2), "2/2"), "before the first match, the last"
                assert back == ((row, col), "1/2")

        _run(scenario)

    def test_a_changed_document_is_searched_as_it_is_now(self, tmp_path):
        """Matches are found in the text as edited: an added occurrence counts, a removed one is gone."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path, title="zqzq")
            app = _Host()
            async with app.run_test() as pilot:
                _, _, editor, field, count = await self._open_find(app, pilot, cfg, path)
                field.value = "zq"
                await pilot.pause()
                assert _plain(count) == "1/2", "two to start with"

                # Act / Assert -- one more occurrence
                editor.text = editor.text.replace("zqzq", "zqzqzq")
                await pilot.press("enter")
                await pilot.pause()
                assert _plain(count) == "1/3", "the added occurrence is counted"

                # Act / Assert -- none left
                editor.text = editor.text.replace("zqzqzq", "none")
                await pilot.press("enter")
                await pilot.pause()
                assert _plain(count) == "0/0", "the removed occurrences are gone"

        _run(scenario)

    def test_no_match_shows_zero_and_leaves_the_cursor(self, tmp_path):
        """A needle found nowhere says 0/0 and moves nothing."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            app = _Host()
            async with app.run_test() as pilot:
                _, _, editor, field, count = await self._open_find(app, pilot, cfg, path)
                editor.cursor_location = (3, 2)
                await pilot.pause()

                # Act
                field.value = "nomatchxyz"
                await pilot.pause()

                # Assert
                assert _plain(count) == "0/0"
                assert editor.cursor_location == (3, 2), "the cursor stays"

        _run(scenario)

    def test_case_and_regex_characters_are_literal(self, tmp_path):
        """``EOL`` finds the lowercase key; ``$(CFG)`` finds the prompt template, not a regex."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path)
            app = _Host()
            async with app.run_test() as pilot:
                _, _, editor, field, _ = await self._open_find(app, pilot, cfg, path)

                # Act / Assert
                field.value = "EOL"
                await pilot.pause()
                assert editor.selected_text == "eol", "case does not matter"
                field.value = "$(CFG)"
                await pilot.pause()
                assert editor.selected_text == "$(CFG)", "metacharacters are text"

        _run(scenario)

    def test_escape_closes_the_bar_first_and_the_dialog_second(self, tmp_path):
        """Escape with the bar open hides it and returns to the editor at the match; a second Escape dismisses."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path, title="zqzq")
            app = _Host()
            async with app.run_test() as pilot:
                screen, results, editor, field, _ = await self._open_find(app, pilot, cfg, path)
                row = _title_row(editor)
                col = str(editor.get_line(row)).index("zqzq")
                field.value = "zq"
                await pilot.pause()

                # Act
                await pilot.press("escape")
                await pilot.pause()

                # Assert
                assert app.screen is screen, "the dialog stays open"
                assert not screen.query_one("#config-find").has_class("visible"), "the bar hides"
                assert app.focused is editor, "the editor has the focus back"
                assert editor.selection.is_empty, "the match is no longer selected"
                assert editor.cursor_location == (row, col + 2), "the cursor stays at the match"

                # Act / Assert -- the second Escape is the dialog's
                await pilot.press("escape")
                await pilot.pause()
                assert results == [None]

        _run(scenario)

    def test_reopening_searches_the_kept_needle_afresh(self, tmp_path):
        """The field keeps its text; opening again searches the document as it is now."""

        async def scenario():
            cfg, path = _write_cfg(tmp_path, title="zqzq")
            app = _Host()
            async with app.run_test() as pilot:
                _, _, editor, field, count = await self._open_find(app, pilot, cfg, path)
                field.value = "zq"
                await pilot.pause()
                await pilot.press("escape")
                await pilot.pause()
                editor.text = editor.text.replace("zqzq", "zqzqzq")
                await pilot.pause()

                # Act
                await pilot.press("ctrl+f")
                await pilot.pause()

                # Assert
                assert field.value == "zq", "the needle is kept"
                assert _plain(count) == "1/3", "counted against the edited text"

        _run(scenario)
