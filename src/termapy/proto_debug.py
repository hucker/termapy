"""Interactive protocol debug screen.

Loads a TOML .pro script and presents test cases in a checkbox list.
Check tests to include them in a run, click Run to execute all checked
tests and see actual responses with per-byte color-coded comparison.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button, Checkbox, Input, RichLog, Rule, SelectionList, Static,
)

from termapy.config import cfg_data_dir
from termapy.protocol import (
    ProtoScript,
    TestCase,
    VisualizerInfo,
    format_hex,
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


def _colorize_log(content: str) -> Text:
    """Parse proto debug log text and return a colorized Rich Text object.

    Color rules:
        - ``=`` separator lines → dim
        - ``-`` separator lines → dim
        - ``[PASS]`` lines → bold green
        - ``[FAIL]`` lines → bold red
        - ``TX:`` / ``EXP:`` / ``RX:`` / ``Time:`` lines → styled
        - ``Summary:`` lines → bold (green if all pass, red otherwise)
        - ``setup:`` / ``teardown:`` lines → dim italic
        - ``--- Repeat`` lines → dim
        - Everything else → default

    Args:
        content: Raw log file text.

    Returns:
        Styled Rich Text object.
    """
    result = Text()
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("===") or stripped.startswith("---"):
            result.append(line, style="dim")
        elif "[PASS]" in line:
            result.append(line, style="bold bright_green")
        elif "[FAIL]" in line:
            result.append(line, style="bold red")
        elif stripped.startswith("TX:"):
            result.append(line, style="cyan")
        elif stripped.startswith("EXP:"):
            result.append(line, style="dim")
        elif stripped.startswith("RX:"):
            if "(timeout)" in line:
                result.append(line, style="bold red")
            else:
                result.append(line, style="yellow")
        elif stripped.startswith("Time:"):
            result.append(line, style="dim")
        elif stripped.startswith("Summary:"):
            # Green if no failures, red otherwise
            has_fail = "stopped on error" in stripped
            # Parse "X/Y PASS" — fail if X != Y
            parts = stripped.split("PASS")[0].split()
            if parts:
                ratio = parts[-1]  # e.g. "3/5"
                nums = ratio.split("/")
                if len(nums) == 2 and nums[0] != nums[1]:
                    has_fail = True
            style = "bold red" if has_fail else "bold bright_green"
            result.append(line, style=style)
        elif stripped.startswith("setup:") or stripped.startswith("teardown:"):
            result.append(line, style="dim italic")
        elif stripped.startswith("["):
            # Timestamp lines like [2026-03-08 ...]
            result.append(line, style="bold")
        else:
            result.append(line)
        result.append("\n")
    return result


class ProtoLogViewer(ModalScreen[None]):
    """Modal viewer for the proto debug log with color-coded output."""

    CSS = f"""
    ProtoLogViewer {{ align: center middle; }}
    ProtoLogViewer Button {{ {_BTN_CSS} }}
    #plog-dialog {{
        width: 95%; height: 95%;
        border: thick $primary; background: $surface; padding: 1 2;
    }}
    #plog-title {{ height: 1; text-align: center; text-style: bold; }}
    #plog-content {{ height: 1fr; }}
    #plog-buttons {{ height: 1; align: right middle; }}
    """

    def __init__(self, log_path: str) -> None:
        super().__init__()
        self.log_path = log_path

    def compose(self) -> ComposeResult:
        """Build the log viewer layout."""
        with Vertical(id="plog-dialog"):
            yield Static("Proto Debug Log", id="plog-title")
            yield RichLog(id="plog-content", wrap=False)
            with Horizontal(id="plog-buttons"):
                open_btn = Button("Open in OS", id="plog-open")
                open_btn.styles.background = "dodgerblue"
                yield open_btn
                yield Button("Close", id="plog-close", variant="error")

    def on_mount(self) -> None:
        """Load and colorize the log content on mount, scrolled to bottom."""
        log = self.query_one("#plog-content", RichLog)
        try:
            content = Path(self.log_path).read_text(encoding="utf-8")
        except FileNotFoundError:
            content = ""

        if content:
            colored = _colorize_log(content)
            log.write(colored)
        else:
            log.write(Text("(no log file yet)", style="dim"))

    @on(Button.Pressed, "#plog-open")
    def _open_external(self) -> None:
        """Open the log file with the system default application."""
        from termapy.config import open_with_system

        if Path(self.log_path).exists():
            open_with_system(self.log_path)

    @on(Button.Pressed, "#plog-close")
    def _close(self) -> None:
        """Close the log viewer."""
        self.dismiss(None)


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
        height: auto;
        max-height: 8;
        margin: 1 0;
    }}
    #proto-debug-detail-scroll {{
        height: 1fr;
    }}
    #proto-debug-detail {{
        height: auto;
        width: auto;
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

        with Vertical(id="proto-debug-dialog"):
            yield Static(f"Protocol Debug: {title}", id="proto-debug-title")
            sl = SelectionList[int](id="proto-debug-select")
            for tc in script.tests:
                sl.add_option((tc.name, tc.index))
            yield sl
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
                yield Input(value="10", id="input-delay",
                            type="integer", compact=True)
                yield Checkbox("Stop on Error", value=False, id="chk-stop-err")
            yield Rule()
            with ScrollableContainer(id="proto-debug-detail-scroll"):
                yield Static("", id="proto-debug-detail")
            yield Static("", id="proto-debug-status")
            yield Rule()
            with Horizontal(id="proto-debug-buttons"):
                yield Button("Setup", id="btn-dbg-setup", variant="primary")
                yield Button("Run", id="btn-dbg-send", variant="success")
                yield Button("Teardown", id="btn-dbg-teardown", variant="warning")
                log_btn = Button("Log", id="btn-dbg-log")
                log_btn.styles.background = "dodgerblue"
                yield log_btn
                yield Button("Close", id="btn-dbg-close", variant="error")

    def _get_checked_tests(self) -> list[TestCase]:
        """Get all test cases whose checkboxes are checked.

        Returns:
            List of checked TestCase objects in script order.
        """
        sl = self.query_one("#proto-debug-select", SelectionList)
        checked_indices = set(sl.selected)
        return [tc for tc in self._script.tests
                if tc.index in checked_indices]

    def _get_highlighted_test(self) -> TestCase | None:
        """Get the test case at the current cursor position.

        Returns:
            The highlighted TestCase, or None.
        """
        sl = self.query_one("#proto-debug-select", SelectionList)
        if sl.highlighted is not None:
            option = sl.get_option_at_index(sl.highlighted)
            idx = option.value
            for tc in self._script.tests:
                if tc.index == idx:
                    return tc
        return None

    @on(SelectionList.SelectionHighlighted, "#proto-debug-select")
    def _on_test_highlighted(
        self, event: SelectionList.SelectionHighlighted
    ) -> None:
        """Update detail panel when cursor moves to a test."""
        tc = self._get_highlighted_test()
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
        tc = self._get_highlighted_test()
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

    def _highlight_test(self, tc: TestCase) -> None:
        """Move the SelectionList cursor to a specific test case.

        Args:
            tc: Test case to highlight.
        """
        sl = self.query_one("#proto-debug-select", SelectionList)
        for i, script_tc in enumerate(self._script.tests):
            if script_tc.index == tc.index:
                sl.highlighted = i
                break

    @on(Button.Pressed, "#btn-dbg-send")
    def _on_send(self) -> None:
        """Run all checked tests, or the highlighted test if none checked."""
        tests = self._get_checked_tests()
        if not tests:
            # Fall back to highlighted test
            tc = self._get_highlighted_test()
            if tc is None:
                self._set_status("No tests selected", "bold red")
                return
            tests = [tc]
        self._run_tests(tests)

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

    @on(Button.Pressed, "#btn-dbg-log")
    def _on_log(self) -> None:
        """Open the debug log in the colorized log viewer."""
        self.app.push_screen(ProtoLogViewer(str(self._log_path)))

    @on(Button.Pressed, "#btn-dbg-close")
    def _on_close(self) -> None:
        """Close the debug screen."""
        self.dismiss(None)

    @property
    def _log_path(self) -> Path:
        """Path to the proto debug log file."""
        return cfg_data_dir(self._ctx.config_path) / "proto" / "debug.log"

    def _log(self, line: str) -> None:
        """Append a line to the debug log file.

        Args:
            line: Text to write (newline appended automatically).
        """
        try:
            with open(self._log_path, "a") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def _log_session_header(self, tests: list[TestCase],
                            repeat: int) -> None:
        """Write a session header to the log.

        Args:
            tests: Test cases to be executed.
            repeat: Number of repeat iterations.
        """
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        title = self._script.name or self._path.name
        sep = "=" * 80
        self._log(sep)
        self._log(f"[{ts}] Script: {title} | "
                  f"Tests: {len(tests)} | Repeat: {repeat}")
        self._log(sep)

    def _log_test_result(self, tc: TestCase, passed: bool,
                         response: bytes | None,
                         elapsed_ms: float) -> None:
        """Write a single test result line to the log.

        Args:
            tc: Test case that was executed.
            passed: Whether the test passed.
            response: Actual response bytes, or None on timeout.
            elapsed_ms: Round-trip time in milliseconds.
        """
        tag = "PASS" if passed else "FAIL"
        tx = format_hex(tc.send_data)
        exp = format_hex(tc.expect_data)
        rx = format_hex(response) if response else "(timeout)"
        self._log(f"  [{tag}]  {tc.name}")
        self._log(f"         TX:  {tx}")
        self._log(f"         EXP: {exp}")
        self._log(f"         RX:  {rx}")
        self._log(f"         Time: {elapsed_ms:.0f}ms")

    def _log_summary(self, tests: list[TestCase], pass_count: int,
                     fail_count: int, stopped: bool) -> None:
        """Write a run summary to the log.

        Args:
            tests: Test cases that were executed.
            pass_count: Total passing iterations.
            fail_count: Total failing iterations.
            stopped: Whether the run was stopped early on error.
        """
        total = pass_count + fail_count
        n = len(tests)
        line = f"Summary: {pass_count}/{total} PASS ({n} test{'s' if n > 1 else ''})"
        if stopped:
            line += " -- stopped on error"
        self._log(line)
        self._log("-" * 80)
        self._log("")

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

    def _show_run_summary(self, tests: list[TestCase],
                          pass_count: int, fail_count: int) -> None:
        """Update the status bar with a summary after running tests.

        Args:
            tests: Test cases that were executed.
            pass_count: Total passing iterations across all tests.
            fail_count: Total failing iterations across all tests.
        """
        total = pass_count + fail_count
        n_tests = len(tests)
        label = f"{n_tests} test{'s' if n_tests > 1 else ''}"
        summary = f"{pass_count}/{total} PASS ({label})"
        style = "bold bright_green" if fail_count == 0 else "bold red"
        self.app.call_from_thread(self._set_status, summary, style)
        # Show detail for last test
        self.app.call_from_thread(self._show_test_detail, tests[-1])

    @work(thread=True)
    def _run_tests(self, tests: list[TestCase]) -> None:
        """Execute one or more test cases in a background thread.

        Runs each test's setup, sends data, reads response, runs
        teardown. Supports repeat count, start delay, and stop-on-error.

        Args:
            tests: List of test cases to execute.
        """
        try:
            repeat = self.app.call_from_thread(self._get_repeat_count)
            delay_s = self.app.call_from_thread(self._get_start_delay)
            stop_on_error = self.app.call_from_thread(self._get_stop_on_error)

            self._log_session_header(tests, repeat)
            self._ctx.engine.set_proto_active(True)
            total_pass = 0
            total_fail = 0
            stopped = False
            try:
                for run_num in range(1, repeat + 1):
                    if repeat > 1:
                        self._log(f"--- Repeat {run_num}/{repeat} ---")

                    if run_num > 1 and delay_s > 0:
                        self.app.call_from_thread(
                            self._set_status,
                            f"Delay {delay_s * 1000:.0f}ms "
                            f"({run_num}/{repeat})...", "bold yellow")
                        time.sleep(delay_s)

                    for ti, tc in enumerate(tests):
                        self.app.call_from_thread(
                            self._highlight_test, tc)
                        prefix = (f"[{run_num}/{repeat}] "
                                  if repeat > 1 else "")
                        status = (f"{prefix}{tc.name} "
                                  f"({ti + 1}/{len(tests)})...")
                        self.app.call_from_thread(
                            self._set_status, status, "bold yellow")

                        if tc.setup:
                            self._log(f"  setup: {', '.join(tc.setup)}")
                        self._send_proto_cmds(tc.setup)
                        passed = self._execute_one(tc)

                        # Log the result
                        response, elapsed_ms, _ = self._results[tc.index]
                        self._log_test_result(tc, passed, response, elapsed_ms)

                        self.app.call_from_thread(
                            self._show_test_detail, tc)
                        if tc.teardown:
                            self._log(
                                f"  teardown: {', '.join(tc.teardown)}")
                        self._send_proto_cmds(tc.teardown)

                        if passed:
                            total_pass += 1
                        else:
                            total_fail += 1

                        if not passed and stop_on_error:
                            stopped = True
                            break

                    if stopped:
                        break
            finally:
                self._ctx.engine.set_proto_active(False)

            self._log_summary(tests, total_pass, total_fail, stopped)
            self._show_run_summary(tests, total_pass, total_fail)
        except RuntimeError:
            # call_from_thread fails during app shutdown — exit silently
            self._ctx.engine.set_proto_active(False)
        except Exception as e:
            self._ctx.engine.set_proto_active(False)
            self._log(f"Test runner error: {e}")

    @work(thread=True)
    def _run_cmds(self, cmds: list[str], label: str) -> None:
        """Execute a list of setup/teardown commands in a background thread.

        Args:
            cmds: List of command strings.
            label: Label for status display (e.g. "Setup", "Teardown").
        """
        try:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._log(f"[{ts}] {label}: {', '.join(cmds)}")

            self.app.call_from_thread(
                self._set_status,
                f"{label}: running...", "bold yellow")

            self._ctx.engine.set_proto_active(True)
            try:
                self._send_proto_cmds(cmds)
            finally:
                self._ctx.engine.set_proto_active(False)

            self._log(f"{label}: done ({len(cmds)} commands)")
            self.app.call_from_thread(
                self._set_status,
                f"{label}: done ({len(cmds)} commands)", "dim")
        except RuntimeError:
            # call_from_thread fails during app shutdown — exit silently
            self._ctx.engine.set_proto_active(False)
        except Exception as e:
            self._ctx.engine.set_proto_active(False)
            self._log(f"Command runner error: {e}")
