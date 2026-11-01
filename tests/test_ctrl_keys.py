"""Tests for control characters reaching the device: /term.ctrl, and the TUI's Ctrl+C and Ctrl+D.

``/term.ctrl <key>`` sends the one byte Ctrl+<key> stands for, raw and
unpaced.  In the TUI a selection keeps Ctrl+C for copying and text in the
prompt keeps Ctrl+D for deleting; only a key nothing on screen took goes
to the device.  ``TestCommand`` drives a CLI on the DEMO port and watches
the port through a TX observer; ``TestKeys`` presses the keys in the real
TUI with a recording port.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pilot_wait import until

from termapy.cli import CLITerminal
from termapy.defaults import DEFAULT_CFG
from termapy.plugins import CmdResult


def _demo_cfg(tmp_path, name: str, **overrides) -> tuple[dict, str]:
    """A DEMO-port config on disk, not auto-connecting; returns it and its path."""
    serial = DEFAULT_CFG["serial"]
    assert isinstance(serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {**DEFAULT_CFG, "serial": {**serial, "port": "DEMO"}, "auto_connect": False, **overrides}
    path = tmp_path / name / f"{name}.cfg"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return cfg, str(path)


# ── /term.ctrl ───────────────────────────────────────────────────────────────


@pytest.fixture
def cli(tmp_path):
    """A started CLITerminal connected to the DEMO port."""
    cfg, path = _demo_cfg(tmp_path, "keys")
    terminal = CLITerminal(cfg, path, no_color=True, term_width=120)
    terminal.repl.global_root = tmp_path
    terminal._fire_app_start()
    assert terminal._connect(), "DEMO port should connect"
    yield terminal
    terminal._disconnect()


def _sent(cli, line: str) -> tuple[list[bytes], CmdResult]:
    """Dispatch ``line``; return each write that reached the port, and the result."""
    written: list[bytes] = []
    with cli.ctx.serial.tx_observer(written.append):
        result = cli._dispatch(line)
    return written, result


class TestCommand:
    @pytest.mark.parametrize(
        ("key", "byte"),
        [("c", b"\x03"), ("D", b"\x04"), ("@", b"\x00"), ("[", b"\x1b"), ("_", b"\x1f")],
    )
    def test_a_key_sends_its_byte(self, cli, key, byte):
        """Each key sends the byte Ctrl+<key> stands for, in either case, and nothing else."""
        # Act
        written, result = _sent(cli, f"/term.ctrl {key}")

        # Assert
        assert written == [byte]
        assert result.value == f"^{key.upper()}", "the value names the key as a terminal shows it"

    @pytest.mark.parametrize("name", ["esc", "ESC", "Esc"])
    def test_esc_names_escape(self, cli, name):
        """esc, in any case, sends Esc (1B), the byte Ctrl+[ stands for."""
        # Act
        written, result = _sent(cli, f"/term.ctrl {name}")

        # Assert
        assert written == [b"\x1b"]
        assert result.value == "^[", "the value is Esc as a terminal shows it"

    @pytest.mark.parametrize("key", ["1", "cc", "~", "escape"])
    def test_a_key_with_no_control_character_fails(self, cli, key):
        """A key that is not a letter, @ [ \\ ] ^ _ or esc fails, naming it, and sends nothing."""
        # Act
        written, result = _sent(cli, f"/term.ctrl {key}")

        # Assert
        expected = f"Invalid control key: {key} (use a letter A-Z, one of @ [ \\ ] ^ _, or esc)"
        assert result.error == expected
        assert written == [], "nothing reached the port"

    def test_a_control_character_is_never_paced(self, cli):
        """A control character is one raw byte: transmit pacing, which is for lines, leaves it alone."""
        # Arrange
        cli.repl._apply_cfg("tx_char_delay_ms", 5)
        cli.repl._apply_cfg("tx_line_delay_ms", 5)

        # Act
        written, _result = _sent(cli, "/term.ctrl c")

        # Assert
        assert written == [b"\x03"], "one byte, no line ending"


# ── The TUI's keys ───────────────────────────────────────────────────────────


class _RecordingPort:
    """A SerialPort that keeps every write, for the one member ``_serial_write`` touches."""

    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(bytes(data))


def _run(scenario) -> None:
    """Run one async Pilot scenario (the suite is otherwise sync)."""
    asyncio.run(scenario())


@pytest.fixture
def app_factory(tmp_path):
    """Build a SerialTerminal against a throwaway DEMO config."""

    def _make(**overrides):
        from termapy.app import SerialTerminal

        cfg, path = _demo_cfg(tmp_path, "proj", **overrides)
        app = SerialTerminal({**cfg, "_config_path": path}, path)
        # The checkout has a real termapy_cfg/plugin/; keep it out of the boot.
        app.repl.global_root = tmp_path
        return app

    return _make


async def _connected(app, pilot) -> _RecordingPort:
    """Connect the DEMO port, record what is written, and focus the prompt."""
    from textual.widgets import Input

    await pilot.pause()
    assert app._connect() is True, "DEMO port should connect"
    port = _RecordingPort()
    app._engine._serial_port = port
    app.query_one("#cmd", Input).focus()
    await pilot.pause()
    return port


class TestKeys:
    def test_ctrl_c_with_nothing_selected_goes_to_the_device(self, app_factory):
        """On an empty prompt with nothing selected, Ctrl+C sends Ctrl-C (03) to the device."""

        async def scenario():
            app = app_factory()
            async with app.run_test() as pilot:
                # Arrange
                port = await _connected(app, pilot)

                # Act
                await pilot.press("ctrl+c")
                await until(pilot, lambda: port.written)

                # Assert
                assert port.written == [b"\x03"]
                app._disconnect()

        _run(scenario)

    def test_ctrl_c_with_text_selected_copies_it(self, app_factory):
        """With text selected in the prompt, Ctrl+C copies it and sends the device nothing."""

        async def scenario():
            from textual.widgets import Input

            app = app_factory()
            async with app.run_test() as pilot:
                # Arrange
                port = await _connected(app, pilot)
                inp = app.query_one("#cmd", Input)
                inp.value = "keep this"
                inp.action_select_all()
                await pilot.pause()

                # Act
                await pilot.press("ctrl+c")
                await pilot.pause(0.2)

                # Assert
                assert app.clipboard == "keep this", "the selection was copied"
                assert port.written == [], "the device got nothing"
                app._disconnect()

        _run(scenario)

    def test_ctrl_d_on_an_empty_prompt_goes_to_the_device(self, app_factory):
        """On an empty prompt, Ctrl+D sends Ctrl-D (04) to the device."""

        async def scenario():
            app = app_factory()
            async with app.run_test() as pilot:
                # Arrange
                port = await _connected(app, pilot)

                # Act
                await pilot.press("ctrl+d")
                await until(pilot, lambda: port.written)

                # Assert
                assert port.written == [b"\x04"]
                app._disconnect()

        _run(scenario)

    def test_ctrl_d_with_text_deletes_and_the_delete_key_never_sends(self, app_factory):
        """With text, Ctrl+D deletes right of the cursor; Delete on an empty prompt sends nothing."""

        async def scenario():
            from textual.widgets import Input

            app = app_factory()
            async with app.run_test() as pilot:
                # Arrange
                port = await _connected(app, pilot)
                inp = app.query_one("#cmd", Input)
                inp.value = "AB"
                inp.cursor_position = 0

                # Act
                await pilot.press("ctrl+d")
                after_ctrl_d = inp.value
                inp.value = ""
                await pilot.press("delete")
                await pilot.pause(0.2)

                # Assert
                assert after_ctrl_d == "B", "Ctrl+D deleted the character right of the cursor"
                assert port.written == [], "neither key sent the device anything"
                app._disconnect()

        _run(scenario)

    def test_a_dialog_keeps_ctrl_c(self, app_factory):
        """Over a dialog, Ctrl+C never reaches the device: Textual stops app bindings at a modal screen."""

        async def scenario():
            from termapy.dialogs.confirm_dialog import ConfirmDialog

            app = app_factory()
            async with app.run_test() as pilot:
                # Arrange
                port = await _connected(app, pilot)
                app.push_screen(ConfirmDialog("Proceed?"))
                await pilot.pause()

                # Act
                await pilot.press("ctrl+c")
                await pilot.pause(0.2)

                # Assert
                assert port.written == [], "the dialog's Ctrl+C is not the device's"
                app._disconnect()

        _run(scenario)


class TestCtrlZ:
    """Ctrl+Z is Ctrl+D's twin: on an empty prompt it goes to the device (an AT modem's send-SMS key)."""

    def test_on_an_empty_prompt_ctrl_z_goes_to_the_device(self, app_factory):
        """On an empty prompt, Ctrl+Z sends Ctrl-Z (1A) to the device."""

        async def scenario():
            app = app_factory()
            async with app.run_test() as pilot:
                # Arrange
                port = await _connected(app, pilot)

                # Act
                await pilot.press("ctrl+z")
                await until(pilot, lambda: port.written)

                # Assert
                assert port.written == [b""]
                app._disconnect()

        _run(scenario)

    def test_with_text_ctrl_z_sends_nothing_and_keeps_it(self, app_factory):
        """With text in the prompt, Ctrl+Z is the prompt's: nothing is sent and the text stays."""

        async def scenario():
            from textual.widgets import Input

            app = app_factory()
            async with app.run_test() as pilot:
                # Arrange
                port = await _connected(app, pilot)
                inp = app.query_one("#cmd", Input)
                inp.value = "hello"

                # Act
                await pilot.press("ctrl+z")
                await pilot.pause(0.2)

                # Assert
                assert port.written == [], "the device got nothing"
                assert inp.value == "hello", "the text stays"
                app._disconnect()

        _run(scenario)


class TestEscape:
    """Escape stops what runs and keeps its own jobs; with nothing to stop it is the device's."""

    def test_with_nothing_running_escape_goes_to_the_device(self, app_factory):
        """On an empty prompt with nothing running, Escape sends Esc (1B) to the device."""

        async def scenario():
            app = app_factory()
            async with app.run_test() as pilot:
                # Arrange
                port = await _connected(app, pilot)

                # Act
                await pilot.press("escape")
                await until(pilot, lambda: port.written)

                # Assert
                assert port.written == [b"\x1b"]
                app._disconnect()

        _run(scenario)

    def test_with_text_typed_escape_clears_it_and_sends_nothing(self, app_factory):
        """With text in the prompt, Escape clears it, as Ctrl+D deletes before it sends; the device gets nothing."""

        async def scenario():
            from textual.widgets import Input

            app = app_factory()
            async with app.run_test() as pilot:
                # Arrange
                port = await _connected(app, pilot)
                inp = app.query_one("#cmd", Input)
                inp.value = "half a line"

                # Act
                await pilot.press("escape")
                await pilot.pause(0.2)

                # Assert
                assert port.written == [], "the device got nothing"
                assert inp.value == "", "the typed text is cleared"
                app._disconnect()

        _run(scenario)

    def test_leaving_history_browsing_sends_nothing(self, app_factory):
        """Escape while browsing history leaves it and empties the prompt, and sends nothing."""

        async def scenario():
            from textual.widgets import Input

            app = app_factory()
            async with app.run_test() as pilot:
                # Arrange
                port = await _connected(app, pilot)
                app.history = ["AT", "AT+INFO"]
                await pilot.press("up")
                await pilot.pause()
                assert app._history_nav.browsing, "Up started browsing"

                # Act
                await pilot.press("escape")
                await pilot.pause(0.2)

                # Assert
                assert app.query_one("#cmd", Input).value == "", "browsing ended"
                assert port.written == [], "leaving history is not the device's Escape"
                app._disconnect()

        _run(scenario)

    def test_escape_in_the_output_pane_returns_to_the_prompt_and_sends_nothing(self, app_factory):
        """Escape in the output pane goes back to the prompt; that one keypress never also reaches the device."""

        async def scenario():
            from textual.widgets import Input

            app = app_factory()
            async with app.run_test() as pilot:
                # Arrange
                port = await _connected(app, pilot)
                app.query_one("#output").focus()
                await pilot.pause()

                # Act
                await pilot.press("escape")
                await pilot.pause(0.2)

                # Assert
                assert app.focused is app.query_one("#cmd", Input), "back at the prompt"
                assert port.written == [], "the Escape was the pane's"
                app._disconnect()

        _run(scenario)

    def test_escape_stops_a_running_script_and_sends_nothing(self, app_factory, tmp_path):
        """While a script runs, Escape stops it: no Esc reaches the device, nor the script's next line."""

        async def scenario():
            app = app_factory()
            run_dir = tmp_path / "proj" / "run"
            run_dir.mkdir(exist_ok=True)
            (run_dir / "slow.run").write_text("/delay 3s\nAT\n", encoding="utf-8")
            async with app.run_test() as pilot:
                # Arrange
                port = await _connected(app, pilot)
                app._dispatch_on_thread(app._repl_line("run slow"))
                await until(pilot, lambda: app.repl.in_script)

                # Act
                await pilot.press("escape")
                await until(pilot, lambda: not app.repl.in_script)
                await pilot.pause(0.2)

                # Assert
                assert port.written == [], "neither an Esc nor the line after the delay went out"
                app._disconnect()

        _run(scenario)

    def test_escape_between_pasted_lines_stops_the_paste_and_sends_nothing(self, app_factory):
        """In the pause between pasted lines nothing holds the dispatch guard; Escape still stops the paste."""

        async def scenario():
            app = app_factory(multiline_paste_delay_ms=400)
            async with app.run_test() as pilot:
                # Arrange
                from textual import events

                port = await _connected(app, pilot)
                app.query_one("#cmd").post_message(events.Paste("A\nB\nC"))
                await until(pilot, lambda: port.written)

                # Act -- in the 400 ms gap after the first line
                await pilot.press("escape")
                await until(pilot, lambda: not app._busy)
                await pilot.pause(0.2)

                # Assert
                assert b"\x1b" not in port.written, "the paste's Escape is not the device's"
                assert port.written == [b"A\r"], "the paste stopped after its first line"
                app._disconnect()

        _run(scenario)


class TestControlKeysOff:
    """control_keys off: the keys stay termapy's, so a device a stray byte would upset never gets one."""

    def test_no_key_reaches_the_device(self, app_factory):
        """Off, Ctrl+C with nothing selected, and Ctrl+D, Ctrl+Z and Escape on an empty prompt, send nothing."""

        async def scenario():
            app = app_factory(control_keys=False)
            async with app.run_test() as pilot:
                # Arrange
                port = await _connected(app, pilot)

                # Act
                for key in ("ctrl+c", "ctrl+d", "ctrl+z", "escape"):
                    await pilot.press(key)
                await pilot.pause(0.3)

                # Assert
                assert port.written == [], "no control character went out"
                app._disconnect()

        _run(scenario)

    def test_escape_still_clears_the_prompt(self, app_factory):
        """Off, Escape keeps its prompt job: typed text is cleared."""

        async def scenario():
            from textual.widgets import Input

            app = app_factory(control_keys=False)
            async with app.run_test() as pilot:
                # Arrange
                port = await _connected(app, pilot)
                inp = app.query_one("#cmd", Input)
                inp.value = "half a line"

                # Act
                await pilot.press("escape")
                await pilot.pause(0.2)

                # Assert
                assert inp.value == "", "cleared"
                assert port.written == [], "and nothing sent"
                app._disconnect()

        _run(scenario)


class TestControlKeysSetting:
    def test_term_control_keys_queries_sets_and_toggles(self, cli):
        """/term.control_keys reads on by default, and off, on and toggle set it for the session."""
        # Act
        first = cli._dispatch("/term.control_keys").value
        cli._dispatch("/term.control_keys off")
        after_off = cli.cfg["control_keys"]
        cli._dispatch("/term.control_keys toggle")
        after_toggle = cli.cfg["control_keys"]

        # Assert
        assert first == "on", "on by default"
        assert after_off is False, "off sets it"
        assert after_toggle is True, "toggle flips it back"

    def test_term_info_reports_it(self, cli):
        """/term.info lists control_keys with the other toggles."""
        # Act
        result = cli._dispatch("/term.info")

        # Assert
        assert result.data["control_keys"] is True
