"""Interactive protocol debug screen.

Loads a TOML .pro script and presents test cases in a dropdown.
Select a test to see send/expected, click Send to execute and see
the actual response with per-byte color-coded comparison.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Select, Static

from termapy.protocol import (
    ProtoScript,
    TestCase,
    diff_bytes,
    format_spaced,
    match_response,
    strip_ansi,
)

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

# Shared button CSS matching dialogs.py pattern
_BTN_CSS = """
    min-width: 0; width: auto; height: 1; min-height: 1;
    border: none; margin: 0 0 0 1;
"""

# Style map for diff coloring
_DIFF_STYLE = {
    "match": "bold bright_green",
    "wildcard": "dim",
    "mismatch": "bold red",
    "extra": "bold red",
    "missing": "bold red",
}


class ProtoDebugScreen(ModalScreen[None]):
    """Interactive protocol debug screen for TOML .pro scripts."""

    CSS = f"""
    ProtoDebugScreen {{ align: center middle; }}
    ProtoDebugScreen Button {{ {_BTN_CSS} }}
    ProtoDebugScreen Checkbox {{
        min-width: 0; height: 1; margin: 0 0 0 1;
        border: none; padding: 0;
    }}
    ProtoDebugScreen Input {{
        width: 8; margin: 0 0 0 1;
        background: $primary-background;
    }}
    ProtoDebugScreen .input-label {{
        width: auto; height: 1; margin: 0 0 0 2;
        color: $text-muted;
    }}

    #proto-debug-dialog {{
        width: 90%;
        height: 80%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }}
    #proto-debug-title {{
        height: 1;
        text-style: bold;
    }}
    #proto-debug-select {{
        width: 100%;
        margin: 1 0;
    }}
    #proto-debug-detail {{
        height: 1fr;
        overflow-y: auto;
        padding: 0 1;
    }}
    #proto-debug-controls {{
        height: 1;
        align: left middle;
    }}
    #proto-debug-options {{
        height: 1;
        align: left middle;
    }}
    #proto-debug-status {{
        height: 1;
        color: $text-muted;
    }}
    #proto-debug-buttons {{
        height: 1;
        align: left middle;
    }}
    """

    def __init__(self, path: Path, ctx: PluginContext,
                 script: ProtoScript) -> None:
        """Initialize the debug screen.

        Args:
            path: Path to the .pro script file.
            ctx: Plugin context for serial I/O.
            script: Parsed TOML proto script.
        """
        super().__init__()
        self._path = path
        self._ctx = ctx
        self._script = script
        self._results: dict[int, tuple[bytes | None, float, bool | None]] = {}

    def compose(self) -> ComposeResult:
        """Build the debug screen layout."""
        script = self._script
        title = script.name or self._path.name

        options = [
            (tc.name, tc.index) for tc in script.tests
        ]

        with Vertical(id="proto-debug-dialog"):
            yield Static(f"Protocol Debug: {title}", id="proto-debug-title")
            yield Select(options, id="proto-debug-select", prompt="Select a test…")
            yield Static("", id="proto-debug-detail")
            with Horizontal(id="proto-debug-controls"):
                yield Static("Format:", classes="input-label")
                yield Checkbox("Hex", value=False, id="chk-hex")
                yield Checkbox("Text", value=True, id="chk-text")
            with Horizontal(id="proto-debug-options"):
                yield Static("Repeat:", classes="input-label")
                yield Input(value="1", id="input-repeat",
                            type="integer", compact=True)
                yield Static("Delay (ms):", classes="input-label")
                yield Input(value="0", id="input-delay",
                            type="integer", compact=True)
                yield Checkbox("Stop on Error", value=False, id="chk-stop-err")
            yield Static("", id="proto-debug-status")
            with Horizontal(id="proto-debug-buttons"):
                yield Button("Setup", id="btn-dbg-setup", variant="primary")
                yield Button("Run Test", id="btn-dbg-send", variant="success")
                yield Button("Teardown", id="btn-dbg-teardown", variant="warning")
                yield Button("Close", id="btn-dbg-close", variant="error")

    def _get_selected_test(self) -> TestCase | None:
        """Get the currently selected test case."""
        sel = self.query_one("#proto-debug-select", Select)
        if sel.value is Select.BLANK:
            return None
        idx = sel.value
        for tc in self._script.tests:
            if tc.index == idx:
                return tc
        return None

    @on(Select.Changed, "#proto-debug-select")
    def _on_test_selected(self, event: Select.Changed) -> None:
        """Update detail panel when a test is selected."""
        tc = self._get_selected_test()
        if tc is None:
            self.query_one("#proto-debug-detail", Static).update("")
            return
        self._show_test_detail(tc)

    @on(Checkbox.Changed, "#chk-hex")
    @on(Checkbox.Changed, "#chk-text")
    def _on_view_toggle(self, event: Checkbox.Changed) -> None:
        """Handle hex/text checkbox toggling — enforce at least one checked."""
        chk_hex = self.query_one("#chk-hex", Checkbox)
        chk_text = self.query_one("#chk-text", Checkbox)

        # Enforce at least one checked
        if not chk_hex.value and not chk_text.value:
            event.checkbox.value = True
            return

        # Refresh display
        tc = self._get_selected_test()
        if tc is not None:
            self._show_test_detail(tc)

    def _get_view_modes(self) -> list[tuple[str, bool]]:
        """Return list of (label, binary) for active view modes.

        Returns:
            List of tuples: ``("Hex", True)`` and/or ``("Text", False)``.
        """
        modes: list[tuple[str, bool]] = []
        chk_hex = self.query_one("#chk-hex", Checkbox)
        chk_text = self.query_one("#chk-text", Checkbox)
        if chk_hex.value:
            modes.append(("Hex", True))
        if chk_text.value:
            modes.append(("Text", False))
        return modes

    def _show_test_detail(self, tc: TestCase) -> None:
        """Render the detail panel for a test case.

        Args:
            tc: The test case to display.
        """
        lines: list[Text | str] = []
        lines.append(Text(tc.name, style="bold"))
        if tc.setup:
            lines.append(Text(f"  setup: {', '.join(tc.setup)}", style="dim"))
        if tc.teardown:
            lines.append(Text(f"  teardown: {', '.join(tc.teardown)}", style="dim"))
        lines.append("")

        # Get result data if available
        actual_data: bytes | None = None
        elapsed_ms = 0.0
        passed: bool | None = None
        if tc.index in self._results:
            actual_data, elapsed_ms, passed = self._results[tc.index]

        # Render a table for each active view mode
        for mode_label, binary in self._get_view_modes():
            self._build_table(lines, tc, mode_label, binary,
                              actual_data, elapsed_ms, passed)
            lines.append("")

        # Result line (once, after all tables)
        if tc.index in self._results:
            if passed:
                lines.append(Text(
                    f"  PASS ({len(actual_data)} bytes, {elapsed_ms:.0f}ms)",
                    style="bold italic bright_green"))
            else:
                lines.append(Text("  FAIL", style="bold italic red"))

        detail = self.query_one("#proto-debug-detail", Static)
        combined = Text()
        for line in lines:
            if isinstance(line, str):
                combined.append(line)
            else:
                combined.append_text(line)
            combined.append("\n")
        detail.update(combined)

    def _build_table(self, lines: list[Text | str], tc: TestCase,
                     mode_label: str, binary: bool,
                     actual_data: bytes | None,
                     elapsed_ms: float, passed: bool | None) -> None:
        """Build a bordered two-column table for one display mode.

        Args:
            lines: List to append table lines to.
            tc: Test case.
            mode_label: Header label ("Hex" or "Text").
            binary: True for hex mode, False for text mode.
            actual_data: Actual received bytes, or None.
            elapsed_ms: Round-trip time.
            passed: True/False/None.
        """
        tx_str = format_spaced(tc.send_data, binary)
        exp_str = format_spaced(tc.expect_data, binary)

        data_strs = [tx_str, exp_str]
        if actual_data is not None:
            data_strs.append(format_spaced(actual_data, binary))

        label_w = 10
        max_data_w = max((len(s) for s in data_strs), default=0)
        border_l = "─" * (label_w + 2)
        border_r = "─" * (max_data_w + 2)

        # Top border + header
        lines.append(Text(f"  ┌{border_l}┬{border_r}┐", style="dim"))
        hdr = Text("  │ ")
        hdr.append("".ljust(label_w), style="dim")
        hdr.append(Text(" │ ", style="dim"))
        hdr.append(mode_label.center(max_data_w), style="bold")
        hdr.append(Text(" │", style="dim"))
        lines.append(hdr)
        lines.append(Text(f"  ├{border_l}┼{border_r}┤", style="dim"))

        # TX row
        lines.append(self._table_row(
            "TX", label_w, tx_str, max_data_w, "bold cyan"))

        # Expected row
        lines.append(self._table_row(
            "Expected", label_w, exp_str, max_data_w, "dim"))

        # Actual row
        if actual_data is not None:
            lines.append(self._render_diff_row(
                tc.expect_data, actual_data, tc.expect_mask, binary,
                label_w, max_data_w))
        elif tc.index in self._results:
            lines.append(self._table_row(
                "Actual", label_w, "(timeout)", max_data_w, "bold red"))

        # Bottom border
        lines.append(Text(f"  └{border_l}┴{border_r}┘", style="dim"))

    def _table_row(self, label: str, label_w: int,
                   data: str, data_w: int, style: str) -> Text:
        """Build a single bordered table row.

        Args:
            label: Row label (e.g. "TX", "Expected").
            label_w: Width of the label column.
            data: Formatted data string.
            data_w: Width of the data column.
            style: Rich style for the data portion.

        Returns:
            Rich Text for the table row.
        """
        row = Text("  │ ")
        row.append(label.ljust(label_w), style="bold")
        row.append(Text(" │ ", style="dim"))
        row.append(data.ljust(data_w), style=style)
        row.append(Text(" │", style="dim"))
        return row

    def _render_diff_row(self, expected: bytes, actual: bytes,
                         mask: bytes, binary: bool,
                         label_w: int, data_w: int) -> Text:
        """Build a bordered Actual row with color-coded byte comparison.

        Uses the same token logic as ``format_spaced`` to guarantee
        identical column width to TX/Expected rows.

        Args:
            expected: Expected bytes.
            actual: Actual received bytes.
            mask: Wildcard mask.
            binary: Use hex display if True.
            label_w: Width of the label column.
            data_w: Width of the data column.

        Returns:
            Rich Text for the bordered actual row.
        """
        statuses = diff_bytes(expected, actual, mask)

        # Build per-byte tokens matching format_spaced logic
        _DISPLAY_ESCAPES = {
            ord("\r"): "\\r", ord("\n"): "\\n",
            ord("\t"): "\\t", 0: "\\0",
        }
        tokens: list[str] = []
        for i, status in enumerate(statuses):
            if status == "missing":
                tokens.append("-- " if binary else ". ")
            else:
                b = actual[i]
                if binary:
                    tokens.append(f"{b:02X} ")
                elif b in _DISPLAY_ESCAPES:
                    tokens.append(_DISPLAY_ESCAPES[b])
                elif 32 <= b < 127:
                    tokens.append(f"{chr(b)} ")
                else:
                    tokens.append(". ")

        # Strip trailing space from last token to match format_spaced().rstrip()
        if tokens:
            tokens[-1] = tokens[-1].rstrip()

        # Build the row
        result = Text("  │ ")
        result.append("Actual".ljust(label_w), style="bold")
        result.append(Text(" │ ", style="dim"))

        char_count = 0
        for token, status in zip(tokens, statuses):
            result.append(token, style=_DIFF_STYLE.get(status, ""))
            char_count += len(token)

        # Pad to match data column width
        if char_count < data_w:
            result.append(" " * (data_w - char_count))
        result.append(Text(" │", style="dim"))
        return result

    def _get_repeat_count(self) -> int:
        """Get the repeat count from the input field.

        Returns:
            Repeat count (minimum 1).
        """
        try:
            val = int(self.query_one("#input-repeat", Input).value)
            return max(1, val)
        except (ValueError, TypeError):
            return 1

    def _get_start_delay(self) -> float:
        """Get the start delay in seconds from the input field.

        Returns:
            Start delay in seconds (minimum 0).
        """
        try:
            val = int(self.query_one("#input-delay", Input).value)
            return max(0, val) / 1000.0
        except (ValueError, TypeError):
            return 0.0

    def _get_stop_on_error(self) -> bool:
        """Get the stop-on-error checkbox state."""
        return self.query_one("#chk-stop-err", Checkbox).value

    @on(Button.Pressed, "#btn-dbg-send")
    def _on_send(self) -> None:
        """Send the selected test."""
        tc = self._get_selected_test()
        if tc is None:
            self._set_status("No test selected", "bold red")
            return
        self._send_test(tc)

    @on(Button.Pressed, "#btn-dbg-setup")
    def _on_setup(self) -> None:
        """Run script-level setup commands."""
        if not self._script.setup:
            self._set_status("No setup commands", "dim")
            return
        self._run_cmds(self._script.setup, "Setup")

    @on(Button.Pressed, "#btn-dbg-teardown")
    def _on_teardown(self) -> None:
        """Run script-level teardown commands."""
        if not self._script.teardown:
            self._set_status("No teardown commands", "dim")
            return
        self._run_cmds(self._script.teardown, "Teardown")

    @on(Button.Pressed, "#btn-dbg-close")
    def _on_close(self) -> None:
        """Close the debug screen."""
        self.dismiss(None)

    def _set_status(self, text: str, style: str = "") -> None:
        """Update the status bar text.

        Args:
            text: Status message.
            style: Optional Rich style.
        """
        status = self.query_one("#proto-debug-status", Static)
        if style:
            status.update(Text(text, style=style))
        else:
            status.update(text)

    @work(thread=True)
    def _send_test(self, tc: TestCase) -> None:
        """Execute a single test case in a background thread.

        Runs test-level setup, sends data, reads response, runs
        test-level teardown. Supports repeat count, start delay,
        and stop-on-error.

        Args:
            tc: Test case to execute.
        """
        ctx = self._ctx
        script = self._script
        repeat = self.app.call_from_thread(self._get_repeat_count)
        delay_s = self.app.call_from_thread(self._get_start_delay)
        stop_on_error = self.app.call_from_thread(self._get_stop_on_error)

        ctx.engine.set_proto_active(True)
        try:
            line_ending = ctx.cfg.get("line_ending", "\r")
            enc = ctx.cfg.get("encoding", "utf-8")
            pass_count = 0
            fail_count = 0

            for run_num in range(1, repeat + 1):
                # Delay between iterations (not before the first)
                if run_num > 1 and delay_s > 0:
                    self.app.call_from_thread(
                        self._set_status,
                        f"Delay {delay_s * 1000:.0f}ms "
                        f"({run_num}/{repeat})...", "bold yellow")
                    time.sleep(delay_s)

                status = (f"Running... ({run_num}/{repeat})"
                          if repeat > 1 else "Running...")
                self.app.call_from_thread(
                    self._set_status, status, "bold yellow")

                # Run per-test setup commands
                for cmd_text in tc.setup:
                    ctx.serial_write((cmd_text + line_ending).encode(enc))
                    ctx.serial_read_raw(1000, script.frame_gap_ms)

                ctx.serial_drain()
                ctx.serial_write(tc.send_data)

                t0 = time.monotonic()
                response = ctx.serial_read_raw(
                    tc.timeout_ms, script.frame_gap_ms)
                elapsed_ms = (time.monotonic() - t0) * 1000

                if script.strip_ansi:
                    response = strip_ansi(response)

                if response:
                    passed = match_response(
                        tc.expect_data, response, tc.expect_mask)
                    self._results[tc.index] = (response, elapsed_ms, passed)
                else:
                    passed = False
                    self._results[tc.index] = (None, elapsed_ms, False)

                if passed:
                    pass_count += 1
                else:
                    fail_count += 1

                # Update detail panel after each iteration
                self.app.call_from_thread(self._show_test_detail, tc)

                # Run per-test teardown commands
                for cmd_text in tc.teardown:
                    ctx.serial_write((cmd_text + line_ending).encode(enc))
                    ctx.serial_read_raw(1000, script.frame_gap_ms)

                if not passed and stop_on_error:
                    break
        finally:
            ctx.engine.set_proto_active(False)

        # Final status with summary for repeat runs
        if repeat > 1:
            total = pass_count + fail_count
            summary = f"{pass_count}/{total} PASS"
            style = "bold bright_green" if fail_count == 0 else "bold red"
            self.app.call_from_thread(self._set_status, summary, style)
        else:
            self.app.call_from_thread(self._set_status, "")
        self.app.call_from_thread(self._show_test_detail, tc)

    @work(thread=True)
    def _run_cmds(self, cmds: list[str], label: str) -> None:
        """Execute a list of setup/teardown commands in a background thread.

        Args:
            cmds: List of command strings.
            label: Label for status display (e.g. "Setup", "Teardown").
        """
        ctx = self._ctx
        script = self._script

        self.app.call_from_thread(
            self._set_status,
            f"{label}: running...", "bold yellow")

        ctx.engine.set_proto_active(True)
        try:
            line_ending = ctx.cfg.get("line_ending", "\r")
            enc = ctx.cfg.get("encoding", "utf-8")
            for cmd_text in cmds:
                ctx.serial_write((cmd_text + line_ending).encode(enc))
                ctx.serial_read_raw(1000, script.frame_gap_ms)
        finally:
            ctx.engine.set_proto_active(False)

        self.app.call_from_thread(
            self._set_status,
            f"{label}: done ({len(cmds)} commands)", "dim")
