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
from textual.widgets import Button, Checkbox, Input, Rule, Select, Static

from termapy.protocol import (
    ProtoScript,
    TestCase,
    VisualizerInfo,
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
        width: auto; height: 1; margin: 0 0 0 1;
        color: $text-muted;
    }}
    ProtoDebugScreen Rule {{
        height: 1;
        margin: 0;
        color: $primary-darken-2;
    }}

    #proto-debug-dialog {{
        width: 96%;
        height: 90%;
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
    }}
    #proto-debug-controls {{
        height: 1;
    }}
    #proto-debug-options {{
        height: 1;
    }}
    #proto-debug-status {{
        height: 1;
    }}
    #proto-debug-buttons {{
        height: 1;
    }}
    """

    def __init__(self, path: Path, ctx: PluginContext,
                 script: ProtoScript,
                 visualizers: list[VisualizerInfo]) -> None:
        """Initialize the debug screen.

        Args:
            path: Path to the .pro script file.
            ctx: Plugin context for serial I/O.
            script: Parsed TOML proto script.
            visualizers: Available packet visualizers.
        """
        super().__init__()
        self._path = path
        self._ctx = ctx
        self._script = script
        self._visualizers = visualizers
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
            with Horizontal(id="proto-debug-controls"):
                yield Static("Format:", classes="input-label")
                for i, viz in enumerate(self._visualizers):
                    checked = (i == 0)
                    chk = Checkbox(
                        viz.name, value=checked,
                        id=f"chk-viz-{i}", classes="viz-chk")
                    if viz.description:
                        chk.tooltip = viz.description
                    yield chk
            with Horizontal(id="proto-debug-options"):
                yield Static("Repeat:", classes="input-label")
                yield Input(value="1", id="input-repeat",
                            type="integer", compact=True)
                yield Static("Delay (ms):", classes="input-label")
                yield Input(value="0", id="input-delay",
                            type="integer", compact=True)
                yield Checkbox("Stop on Error", value=False, id="chk-stop-err")
            yield Rule()
            yield Static("", id="proto-debug-detail")
            yield Static("", id="proto-debug-status")
            yield Rule()
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

    @on(Checkbox.Changed, ".viz-chk")
    def _on_view_toggle(self, event: Checkbox.Changed) -> None:
        """Handle visualizer checkbox toggling — enforce at least one checked."""
        any_checked = any(
            self.query_one(f"#chk-viz-{i}", Checkbox).value
            for i in range(len(self._visualizers))
        )
        if not any_checked:
            event.checkbox.value = True
            return

        # Refresh display
        tc = self._get_selected_test()
        if tc is not None:
            self._show_test_detail(tc)

    def _get_active_visualizers(self) -> list[VisualizerInfo]:
        """Return visualizers whose checkboxes are checked.

        Returns:
            List of active VisualizerInfo instances.
        """
        active: list[VisualizerInfo] = []
        for i, viz in enumerate(self._visualizers):
            chk = self.query_one(f"#chk-viz-{i}", Checkbox)
            if chk.value:
                active.append(viz)
        return active

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

        # Render a table for each active visualizer
        for viz in self._get_active_visualizers():
            self._build_table(lines, tc, viz, actual_data, elapsed_ms, passed)
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

    def _max_line_width(self, markup: str) -> int:
        """Measure the max display width across lines of a markup string.

        Handles multi-line strings (newlines) by measuring each line
        individually and returning the widest.

        Args:
            markup: Plain text or Rich-markup string, possibly multi-line.

        Returns:
            Maximum display width in terminal cells.
        """
        return max(
            (Text.from_markup(line).cell_len
             for line in markup.split("\n")),
            default=0,
        )

    def _build_table(self, lines: list[Text | str], tc: TestCase,
                     viz: VisualizerInfo, actual_data: bytes | None,
                     elapsed_ms: float, passed: bool | None) -> None:
        """Build a bordered two-column table for one visualizer.

        Supports multi-line output from visualizers — newlines in
        ``format_bytes`` or ``format_diff`` produce continuation rows.

        Args:
            lines: List to append table lines to.
            tc: Test case.
            viz: Visualizer to use for formatting.
            actual_data: Actual received bytes, or None.
            elapsed_ms: Round-trip time.
            passed: True/False/None.
        """
        tx_str = viz.format_bytes(tc.send_data)
        exp_str = viz.format_bytes(tc.expect_data)

        # Optional header row from visualizer
        header_str = ""
        if viz.format_header is not None:
            header_str = viz.format_header(tc.send_data) or ""

        # Measure display widths (handles multi-line)
        data_widths = [self._max_line_width(tx_str),
                       self._max_line_width(exp_str)]
        if header_str:
            data_widths.append(self._max_line_width(header_str))
        actual_str = ""
        if actual_data is not None:
            actual_str = viz.format_diff(actual_data, tc.expect_data,
                                         tc.expect_mask)
            data_widths.append(self._max_line_width(actual_str))

        label_w = 10
        max_data_w = max(data_widths, default=0)
        border_l = "─" * (label_w + 2)
        border_r = "─" * (max_data_w + 2)

        # Top border + header
        lines.append(Text(f"  ┌{border_l}┬{border_r}┐", style="dim"))
        hdr = Text("  │ ")
        hdr.append("".ljust(label_w), style="dim")
        hdr.append(Text(" │ ", style="dim"))
        hdr.append(viz.name.center(max_data_w), style="bold")
        hdr.append(Text(" │", style="dim"))
        lines.append(hdr)
        lines.append(Text(f"  ├{border_l}┼{border_r}┤", style="dim"))

        # Optional field header row(s) from visualizer
        if header_str:
            self._append_markup_rows(
                lines, "", label_w, header_str, max_data_w)

        # TX row(s)
        self._append_table_rows(
            lines, "TX", label_w, tx_str, max_data_w, "bold cyan")

        # Expected row(s)
        self._append_table_rows(
            lines, "Expected", label_w, exp_str, max_data_w, "dim")

        # Actual row(s)
        if actual_data is not None:
            self._append_markup_rows(
                lines, "Actual", label_w, actual_str, max_data_w)
        elif tc.index in self._results:
            self._append_table_rows(
                lines, "Actual", label_w, "(timeout)", max_data_w,
                "bold red")

        # Bottom border
        lines.append(Text(f"  └{border_l}┴{border_r}┘", style="dim"))

    def _append_table_rows(self, lines: list[Text | str], label: str,
                           label_w: int, data: str, data_w: int,
                           style: str) -> None:
        """Append bordered table rows for plain-text data.

        Splits on newlines — the label appears on the first row only,
        continuation rows have an empty label column.

        Args:
            lines: List to append to.
            label: Row label (e.g. "TX", "Expected").
            label_w: Width of the label column.
            data: Formatted data string, possibly multi-line.
            data_w: Width of the data column.
            style: Rich style for the data portion.
        """
        data_lines = data.split("\n")
        for i, line_text in enumerate(data_lines):
            row = Text("  │ ")
            row_label = label if i == 0 else ""
            row.append(row_label.ljust(label_w), style="bold")
            row.append(Text(" │ ", style="dim"))
            row.append(line_text.ljust(data_w), style=style)
            row.append(Text(" │", style="dim"))
            lines.append(row)

    def _append_markup_rows(self, lines: list[Text | str], label: str,
                            label_w: int, markup: str,
                            data_w: int) -> None:
        """Append bordered table rows from Rich-markup content.

        Splits on newlines — the label appears on the first row only.
        Used for the Actual row where the visualizer provides
        Rich-markup strings with embedded colors.

        Args:
            lines: List to append to.
            label: Row label (e.g. "Actual").
            label_w: Width of the label column.
            markup: Rich-markup formatted string, possibly multi-line.
            data_w: Width of the data column.
        """
        markup_lines = markup.split("\n")
        for i, line_markup in enumerate(markup_lines):
            row = Text("  │ ")
            row_label = label if i == 0 else ""
            row.append(row_label.ljust(label_w), style="bold")
            row.append(Text(" │ ", style="dim"))

            content = Text.from_markup(line_markup)
            pad = data_w - content.cell_len
            if pad > 0:
                content.append(" " * pad)
            row.append_text(content)
            row.append(Text(" │", style="dim"))
            lines.append(row)

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

    def _send_proto_cmds(self, cmds: list[str]) -> None:
        """Send a list of protocol commands over serial.

        Each command is encoded, sent, and followed by a read to
        consume any response before the next command.

        Args:
            cmds: Command strings to send.
        """
        ctx = self._ctx
        line_ending = ctx.cfg.get("line_ending", "\r")
        enc = ctx.cfg.get("encoding", "utf-8")
        for cmd_text in cmds:
            ctx.serial_write((cmd_text + line_ending).encode(enc))
            ctx.serial_read_raw(1000, self._script.frame_gap_ms)

    def _execute_one(self, tc: TestCase) -> bool:
        """Execute a single send/receive cycle for a test case.

        Drains the RX queue, sends test data, reads the response,
        and stores the result.

        Args:
            tc: Test case to execute.

        Returns:
            True if the response matched expectations.
        """
        ctx = self._ctx
        script = self._script

        ctx.serial_drain()
        ctx.serial_write(tc.send_data)

        t0 = time.monotonic()
        response = ctx.serial_read_raw(tc.timeout_ms, script.frame_gap_ms)
        elapsed_ms = (time.monotonic() - t0) * 1000

        if script.strip_ansi:
            response = strip_ansi(response)

        if response:
            passed = match_response(tc.expect_data, response, tc.expect_mask)
            self._results[tc.index] = (response, elapsed_ms, passed)
        else:
            passed = False
            self._results[tc.index] = (None, elapsed_ms, False)

        return passed

    def _post_run_status(
        self, tc: TestCase, repeat: int, pass_count: int, fail_count: int
    ) -> None:
        """Update the status bar and detail panel after all iterations.

        Args:
            tc: Test case that was executed.
            repeat: Total number of iterations requested.
            pass_count: Number of passing iterations.
            fail_count: Number of failing iterations.
        """
        if repeat > 1:
            total = pass_count + fail_count
            summary = f"{pass_count}/{total} PASS"
            style = "bold bright_green" if fail_count == 0 else "bold red"
            self.app.call_from_thread(self._set_status, summary, style)
        else:
            self.app.call_from_thread(self._set_status, "")
        self.app.call_from_thread(self._show_test_detail, tc)

    @work(thread=True)
    def _send_test(self, tc: TestCase) -> None:
        """Execute a single test case in a background thread.

        Runs test-level setup, sends data, reads response, runs
        test-level teardown. Supports repeat count, start delay,
        and stop-on-error.

        Args:
            tc: Test case to execute.
        """
        repeat = self.app.call_from_thread(self._get_repeat_count)
        delay_s = self.app.call_from_thread(self._get_start_delay)
        stop_on_error = self.app.call_from_thread(self._get_stop_on_error)

        self._ctx.engine.set_proto_active(True)
        try:
            pass_count = 0
            fail_count = 0

            for run_num in range(1, repeat + 1):
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

                self._send_proto_cmds(tc.setup)
                passed = self._execute_one(tc)
                self.app.call_from_thread(self._show_test_detail, tc)
                self._send_proto_cmds(tc.teardown)

                if passed:
                    pass_count += 1
                else:
                    fail_count += 1

                if not passed and stop_on_error:
                    break
        finally:
            self._ctx.engine.set_proto_active(False)

        self._post_run_status(tc, repeat, pass_count, fail_count)

    @work(thread=True)
    def _run_cmds(self, cmds: list[str], label: str) -> None:
        """Execute a list of setup/teardown commands in a background thread.

        Args:
            cmds: List of command strings.
            label: Label for status display (e.g. "Setup", "Teardown").
        """
        self.app.call_from_thread(
            self._set_status,
            f"{label}: running...", "bold yellow")

        self._ctx.engine.set_proto_active(True)
        try:
            self._send_proto_cmds(cmds)
        finally:
            self._ctx.engine.set_proto_active(False)

        self.app.call_from_thread(
            self._set_status,
            f"{label}: done ({len(cmds)} commands)", "dim")
