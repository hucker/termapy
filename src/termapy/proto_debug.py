"""Interactive protocol debug screen.

Loads a TOML .pro script and presents test cases in a checkbox list.
Check tests to include them in a run, click Run to execute all checked
tests and see actual responses with per-byte color-coded comparison.
"""

from __future__ import annotations

import time
from datetime import datetime
from io import TextIOWrapper
from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button, Checkbox, Input, RichLog, Rule, SelectionList, Static,
)

from termapy.config import cfg_data_dir, open_with_system
from termapy.proto_runner import _build_test_result, expand_result_template
from termapy.protocol import (
    DIFF_STYLES,
    ProtoScript,
    TestCase,
    apply_format,
    diff_columns as proto_diff_columns,
    extract_fmt_title,
    format_hex,
    match_response,
    parse_format_spec,
    strip_ansi,
)
from termapy.protocol_viz import VisualizerInfo

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

# Shared button CSS matching dialogs.py pattern
_BTN_CSS = """
    min-width: 0; width: auto; height: 1; min-height: 1;
    border: none; margin: 0 0 0 1;
"""


class ProtoDebugScreen(ModalScreen[None]):
    """Interactive protocol debug screen for TOML .pro scripts."""

    BINDINGS = [("ctrl+q", "dismiss_modal", "Close")]

    def action_dismiss_modal(self) -> None:
        """Close the modal on Ctrl+Q."""
        self.dismiss(None)

    CSS = f"""
    ProtoDebugScreen {{ align: center middle; }}
    ProtoDebugScreen Button {{ {_BTN_CSS} }}
    ProtoDebugScreen Checkbox {{
        min-width: 0; height: 1; margin: 0; padding: 0;
        border: none;
    }}
    ProtoDebugScreen Input {{
        width: 4; margin: 0;
        background: $primary-background;
    }}
    ProtoDebugScreen .input-label {{
        width: 12; height: 1; margin: 0;
        color: $text-muted; text-align: right;
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
        padding: 0 2 1 2;
    }}
    #proto-debug-title {{
        height: 1;
        text-style: bold;
    }}
    #proto-debug-top {{
        height: auto;
        margin: 1 0 0 0;
    }}
    #test-col {{
        width: 50; height: auto;
    }}
    #test-label {{
        width: auto; height: 1; margin: 0;
        color: $text-muted;
    }}
    #proto-debug-select {{
        width: 48;
        height: auto;
        max-height: 8;
        border: tall $primary;
    }}
    #viz-label {{
        width: auto; height: 1; margin: 0;
        color: $text-muted;
    }}
    #sel-viz {{
        width: 28; height: auto; max-height: 5;
        margin: 0; border: tall $primary;
    }}
    #viz-col {{
        width: 30; height: auto; margin: 0 0 0 2;
    }}
    #chk-col {{
        width: auto; height: auto; margin: 1 0 0 2;
    }}
    #input-col {{
        width: auto; height: auto; margin: 1 0 0 2;
    }}
    .input-row {{
        height: 1; width: auto;
    }}
    #input-json-file {{
        width: 40; margin: 0;
        background: $primary-background;
    }}
    #proto-debug-detail {{
        height: 1fr;
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
        self._running = False
        self._log_file: TextIOWrapper | None = None
        # Filter visualizers: if script specifies viz list, keep only those
        # plus Hex and Text (always available)
        if script.viz:
            allowed = {n.lower() for n in script.viz} | {"hex", "text"}
            self._visualizers = [
                v for v in visualizers if v.name.lower() in allowed
            ]
        else:
            self._visualizers = visualizers
        self._results: dict[int, tuple[bytes | None, float, bool | None]] = {}

    def compose(self) -> ComposeResult:
        """Build the debug screen layout."""
        script = self._script
        title = script.name or self._path.name

        with Vertical(id="proto-debug-dialog"):
            yield Static(f"Protocol Debug: {title}", id="proto-debug-title")
            with Horizontal(id="proto-debug-top"):
                with Vertical(id="test-col"):
                    yield Static("Tests", id="test-label")
                    sl = SelectionList[int](id="proto-debug-select")
                    for tc in script.tests:
                        label = (f"{tc.name} [{tc.viz}]"
                                 if tc.viz else tc.name)
                        sl.add_option((label, tc.index, True))
                    yield sl
                with Vertical(id="viz-col"):
                    viz_label = Static("Visualizations", id="viz-label")
                    viz_label.tooltip = "Visualizer selection"
                    yield viz_label
                    # Sort: custom visualizers first, then Hex/Text last
                    always_last = {"hex", "text"}
                    ordered = (
                        [v for v in self._visualizers
                         if v.name.lower() not in always_last]
                        + [v for v in self._visualizers
                           if v.name.lower() in always_last]
                    )
                    viz_sl = SelectionList[int](id="sel-viz")
                    for viz in ordered:
                        idx = self._visualizers.index(viz)
                        viz_sl.add_option((viz.name, idx, idx == 0))
                    yield viz_sl
                with Vertical(id="chk-col"):
                    yield Checkbox(
                        "Stop on Error", value=False, id="chk-stop-err")
                    yield Checkbox(
                        "Show viz string", value=False, id="chk-show-viz")
                    yield Checkbox(
                        "Compact", value=False, id="chk-compact")
                    yield Checkbox(
                        "JSON", value=False, id="chk-json")
                with Vertical(id="input-col"):
                    with Horizontal(classes="input-row"):
                        yield Static("Repeat:", classes="input-label")
                        yield Input(value="1", id="input-repeat",
                                    type="integer", compact=True)
                    with Horizontal(classes="input-row"):
                        yield Static("Delay (ms):", classes="input-label")
                        yield Input(value="10", id="input-delay",
                                    type="integer", compact=True)
                    with Horizontal(classes="input-row"):
                        yield Static("JSON file:", classes="input-label")
                        json_default = (self._script.json_file
                                        or self._ctx.cfg.get(
                                            "proto_results_template",
                                            "{name}_results.json"))
                        yield Input(value=json_default,
                                    id="input-json-file", compact=True)
            yield Rule()
            yield RichLog(id="proto-debug-detail", wrap=False)
            yield Static("", id="proto-debug-status")
            yield Rule()
            with Horizontal(id="proto-debug-buttons"):
                setup_btn = Button("Setup", id="btn-dbg-setup", variant="primary",
                                   disabled=not script.setup)
                setup_btn.tooltip = ("Run setup commands" if script.setup
                                     else "No setup commands in script")
                yield setup_btn
                yield Button("Run", id="btn-dbg-send", variant="success")
                td_btn = Button("Teardown", id="btn-dbg-teardown", variant="warning",
                                disabled=not script.teardown)
                td_btn.tooltip = ("Run teardown commands" if script.teardown
                                  else "No teardown commands in script")
                yield td_btn
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
        """Update detail panel when cursor moves to a test.

        Suppressed while tests are running to preserve the scrolling log.
        """
        if self._running:
            return
        tc = self._get_highlighted_test()
        if tc is None:
            self._clear_detail()
            return
        self._show_test_detail(tc)

    @on(SelectionList.SelectedChanged, "#sel-viz")
    def _on_viz_changed(self, event: SelectionList.SelectedChanged) -> None:
        """Refresh detail panel when visualizer selection changes.

        Enforces at least one visualizer checked.
        """
        viz_sl = self.query_one("#sel-viz", SelectionList)
        if not viz_sl.selected:
            # Re-check the first option if nothing is selected
            viz_sl.select(self._visualizers[0].sort_order if self._visualizers else 0)
            return
        tc = self._get_highlighted_test()
        if tc is not None:
            self._show_test_detail(tc)

    def _get_active_visualizers(
        self, tc: TestCase | None = None,
    ) -> list[VisualizerInfo]:
        """Return visualizers whose checkboxes are checked.

        Also includes any forced per-test visualizer.

        Args:
            tc: Current test case (for per-test forced viz).

        Returns:
            List of active VisualizerInfo instances (deduplicated).
        """
        viz_sl = self.query_one("#sel-viz", SelectionList)
        checked = set(viz_sl.selected)
        active = [
            v for i, v in enumerate(self._visualizers) if i in checked
        ]

        # Add forced per-test visualizer if not already checked
        if tc and tc.viz:
            active_names = {v.name.lower() for v in active}
            if tc.viz.lower() not in active_names:
                for viz in self._visualizers:
                    if viz.name.lower() == tc.viz.lower():
                        active.append(viz)
                        break

        return active

    def _render_test_detail(self, tc: TestCase) -> Text:
        """Build Rich Text for a test case's detail view.

        Args:
            tc: The test case to render.

        Returns:
            Combined Rich Text with tables, viz strings, and result.
        """
        lines: list[Text | str] = []
        lines.append(Text(tc.name, style="bold"))
        if tc.setup:
            lines.append(Text(f"  setup: {', '.join(tc.setup)}", style="dim"))
        if tc.teardown:
            lines.append(Text(f"  teardown: {', '.join(tc.teardown)}", style="dim"))

        # Get result data if available
        actual_data: bytes | None = None
        elapsed_ms = 0.0
        passed: bool | None = None
        if tc.index in self._results:
            actual_data, elapsed_ms, passed = self._results[tc.index]

        # Show format spec strings above tables when checkbox is checked
        active_vizs = self._get_active_visualizers(tc)
        if self._get_show_viz():
            for viz in active_vizs:
                tx_spec = viz.format_spec(tc.send_data)
                if tx_spec:
                    lines.append(Text(
                        f"  [{viz.name}] TX: {tx_spec}", style="cyan"))
                rx_spec = viz.format_spec(tc.expect_data)
                if rx_spec and rx_spec != tx_spec:
                    lines.append(Text(
                        f"  [{viz.name}] RX: {rx_spec}", style="cyan"))
            lines.append("")

        # Render inline format tables (always on when present)
        compact = self._get_compact()
        if tc.send_fmt:
            title, spec = extract_fmt_title(tc.send_fmt)
            cols = parse_format_spec(spec)
            headers, values = apply_format(tc.send_data, cols)
            self._render_unified_table(
                lines, title or "Custom TX", headers,
                [("TX", values, "bold cyan", [])],
                [], [], [],
                has_actual=False, is_timeout=False,
            )
            if not compact:
                lines.append("")
        if tc.expect_fmt:
            title, spec = extract_fmt_title(tc.expect_fmt)
            cols = parse_format_spec(spec)
            _, exp_values = apply_format(tc.expect_data, cols)
            act_values: list[str] = []
            act_statuses: list[str] = []
            if actual_data is not None:
                _, _, act_values, act_statuses = proto_diff_columns(
                    actual_data, tc.expect_data, tc.expect_mask, cols)
            self._render_unified_table(
                lines, title or "Custom RX",
                [c.name for c in cols if c.type_code != "_"],
                [],
                exp_values, act_values, act_statuses,
                has_actual=(actual_data is not None),
                is_timeout=(tc.index in self._results and actual_data is None),
            )
            if not compact:
                lines.append("")

        # Render tables: TX then RX for each visualizer
        for viz in active_vizs:
            self._build_tx_table(lines, tc, viz)
            if not compact:
                lines.append("")
            self._build_rx_table(lines, tc, viz, actual_data)
            if not compact:
                lines.append("")

        # Result line (once, after all tables)
        if tc.index in self._results:
            if passed:
                lines.append(Text(
                    f"  PASS ({len(actual_data)} bytes, {elapsed_ms:.0f}ms)",
                    style="bold italic bright_green"))
            else:
                lines.append(Text("  FAIL", style="bold italic red"))

        combined = Text()
        for line in lines:
            if isinstance(line, str):
                combined.append(line)
            else:
                combined.append_text(line)
            combined.append("\n")
        return combined

    def _show_test_detail(self, tc: TestCase) -> None:
        """Show a single test's detail (interactive click mode).

        Clears the log and writes the test detail.

        Args:
            tc: The test case to display.
        """
        log = self.query_one("#proto-debug-detail", RichLog)
        log.clear()
        log.write(self._render_test_detail(tc))

    def _append_test_detail(self, tc: TestCase) -> None:
        """Append a test's detail to the scrolling log (run mode).

        Adds the test detail without clearing.

        Args:
            tc: The test case to append.
        """
        log = self.query_one("#proto-debug-detail", RichLog)
        log.write(self._render_test_detail(tc))

    def _clear_detail(self) -> None:
        """Clear the detail log panel."""
        self.query_one("#proto-debug-detail", RichLog).clear()

    def _build_tx_table(
        self, lines: list[Text | str], tc: TestCase,
        viz: VisualizerInfo,
    ) -> None:
        """Build the TX portion of a visualizer table.

        Args:
            lines: List to append table lines to.
            tc: Test case.
            viz: Visualizer to use for formatting.
        """
        tx_headers, tx_values = viz.format_columns(tc.send_data)
        self._render_unified_table(
            lines, f"{viz.name} TX", tx_headers,
            [("TX", tx_values, "bold cyan", [])],
            [], [], [],
            has_actual=False, is_timeout=False,
        )

    def _build_rx_table(
        self, lines: list[Text | str], tc: TestCase,
        viz: VisualizerInfo, actual_data: bytes | None,
    ) -> None:
        """Build the RX (Expected+Actual) portion of a visualizer table.

        Args:
            lines: List to append table lines to.
            tc: Test case.
            viz: Visualizer to use for formatting.
            actual_data: Actual received bytes, or None.
        """
        exp_headers, exp_values = viz.format_columns(tc.expect_data)

        act_values: list[str] = []
        act_statuses: list[str] = []
        if actual_data is not None:
            _, _, act_values, act_statuses = (
                viz.diff_columns(actual_data, tc.expect_data, tc.expect_mask)
            )

        self._render_unified_table(
            lines, f"{viz.name} RX", exp_headers,
            [],
            exp_values, act_values, act_statuses,
            has_actual=(actual_data is not None),
            is_timeout=(tc.index in self._results and actual_data is None),
        )

    def _render_unified_table(
        self, lines: list[Text | str], title: str,
        headers: list[str],
        extra_rows: list[tuple[str, list[str], str, list[str]]],
        exp_values: list[str], act_values: list[str],
        act_statuses: list[str],
        has_actual: bool, is_timeout: bool,
    ) -> None:
        """Render a bordered multi-column table.

        Args:
            lines: List to append table lines to.
            title: Table title (visualizer name).
            headers: Column headers.
            extra_rows: Additional rows as (label, values, style, statuses).
            exp_values: Expected row values (empty to skip).
            act_values: Actual row values (empty to skip).
            act_statuses: Per-column status for actual row.
            has_actual: Whether actual data exists.
            is_timeout: Whether this is a timeout result.
        """
        label_w = max(10, len(title))
        n_cols = len(headers)
        if n_cols == 0:
            return

        # Calculate column widths: max of header, all row values.
        # For "mixed" status, values contain Rich markup - measure visible
        # cell length instead of raw string length.
        def _visible_len(val: str, status: str) -> int:
            if status == "mixed":
                return Text.from_markup(val).cell_len
            return len(val)

        col_widths = [len(h) for h in headers]
        for _, values, _, row_statuses in extra_rows:
            for i, v in enumerate(values):
                if i < n_cols:
                    st = row_statuses[i] if i < len(row_statuses) else ""
                    col_widths[i] = max(col_widths[i], _visible_len(v, st))
        for i, v in enumerate(exp_values):
            if i < n_cols:
                col_widths[i] = max(col_widths[i], len(v))
        for i, v in enumerate(act_values):
            if i < n_cols:
                st = act_statuses[i] if i < len(act_statuses) else ""
                col_widths[i] = max(col_widths[i], _visible_len(v, st))

        # Build border strings
        def border_line(left: str, mid: str, sep: str, right: str) -> str:
            parts = [left, "─" * (label_w + 2)]
            for w in col_widths:
                parts.append(sep)
                parts.append("─" * (w + 2))
            parts.append(right)
            return "".join(parts)

        # Top border
        lines.append(Text(
            "  " + border_line("┌", "─", "┬", "┐"), style="dim"))

        # Header row
        hdr = Text("  │ ", style="dim")
        hdr.append(title.ljust(label_w), style="bold")
        for i, h in enumerate(headers):
            hdr.append(Text(" │ ", style="dim"))
            hdr.append(h.center(col_widths[i]), style="bold")
        hdr.append(Text(" │", style="dim"))
        lines.append(hdr)

        # Header separator
        lines.append(Text(
            "  " + border_line("├", "─", "┼", "┤"), style="dim"))

        # Extra rows (TX)
        for label, values, style, _ in extra_rows:
            row = Text("  │ ", style="dim")
            row.append(label.ljust(label_w), style="bold")
            for i in range(n_cols):
                row.append(Text(" │ ", style="dim"))
                val = values[i] if i < len(values) else ""
                row.append(val.ljust(col_widths[i]), style=style)
            row.append(Text(" │", style="dim"))
            lines.append(row)

        # Expected row
        if exp_values:
            row = Text("  │ ", style="dim")
            row.append("Expected".ljust(label_w), style="bold")
            for i in range(n_cols):
                row.append(Text(" │ ", style="dim"))
                val = exp_values[i] if i < len(exp_values) else ""
                row.append(val.ljust(col_widths[i]), style="dim")
            row.append(Text(" │", style="dim"))
            lines.append(row)

        # Actual row with per-column coloring
        if has_actual and act_values:
            row = Text("  │ ", style="dim")
            row.append("Actual".ljust(label_w), style="bold")
            for i in range(n_cols):
                row.append(Text(" │ ", style="dim"))
                val = act_values[i] if i < len(act_values) else ""
                status = act_statuses[i] if i < len(act_statuses) else "match"
                if status == "mixed":
                    # Per-byte markup - parse and pad to column width
                    styled = Text.from_markup(val)
                    pad = col_widths[i] - styled.cell_len
                    if pad > 0:
                        styled.append(" " * pad)
                    row.append_text(styled)
                else:
                    style = DIFF_STYLES.get(status, "")
                    row.append(val.ljust(col_widths[i]), style=style)
            row.append(Text(" │", style="dim"))
            lines.append(row)
        elif is_timeout:
            row = Text("  │ ", style="dim")
            row.append("Actual".ljust(label_w), style="bold")
            for i in range(n_cols):
                row.append(Text(" │ ", style="dim"))
                row.append("---".ljust(col_widths[i]), style="bold red")
            row.append(Text(" │", style="dim"))
            lines.append(row)

        # Bottom border
        lines.append(Text(
            "  " + border_line("└", "─", "┴", "┘"), style="dim"))

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

    def _get_show_viz(self) -> bool:
        """Get the Show Viz checkbox state."""
        return self.query_one("#chk-show-viz", Checkbox).value

    def _get_compact(self) -> bool:
        """Get the Compact checkbox state."""
        return self.query_one("#chk-compact", Checkbox).value

    def _get_json_enabled(self) -> bool:
        """Get the JSON checkbox state."""
        return self.query_one("#chk-json", Checkbox).value

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
        """Open the debug log with the system default application."""
        open_with_system(str(self._log_path))

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
        self._log_lines([line])

    def _log_lines(self, lines: list[str]) -> None:
        """Append multiple lines to the debug log in a single write.

        Uses a persistent file handle during test runs to avoid
        repeated open/close overhead on Windows.

        Args:
            lines: Text lines to write (newlines appended automatically).
        """
        try:
            if self._log_file is not None:
                self._log_file.write("\n".join(lines) + "\n")
                self._log_file.flush()
            else:
                with open(self._log_path, "a") as f:
                    f.write("\n".join(lines) + "\n")
        except OSError:
            pass

    def _open_log(self) -> None:
        """Open the log file for the duration of a test run."""
        try:
            self._log_file = open(self._log_path, "a")  # noqa: SIM115
        except OSError:
            self._log_file = None

    def _close_log(self) -> None:
        """Close the persistent log file handle."""
        if self._log_file is not None:
            try:
                self._log_file.close()
            except OSError:
                pass
            self._log_file = None

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
        self._log_lines([
            sep,
            f"[{ts}] Script: {title} | "
            f"Tests: {len(tests)} | Repeat: {repeat}",
            sep,
        ])

    def _log_test_result(
        self, tc: TestCase, passed: bool,
        response: bytes | None, elapsed_ms: float,
        vizs: list[VisualizerInfo],
    ) -> None:
        """Write a single test result line to the log.

        Args:
            tc: Test case that was executed.
            passed: Whether the test passed.
            response: Actual response bytes, or None on timeout.
            elapsed_ms: Round-trip time in milliseconds.
            vizs: Active visualizers for formatted output.
        """
        tag = "PASS" if passed else "FAIL"
        tx = format_hex(tc.send_data)
        exp = format_hex(tc.expect_data)
        rx = format_hex(response) if response else "(timeout)"
        lines = [
            f"  [{tag}]  {tc.name}",
            f"         TX:  {tx}",
            f"         EXP: {exp}",
            f"         RX:  {rx}",
            f"         Time: {elapsed_ms:.0f}ms",
        ]
        # Log visualizer column data
        for viz in vizs:
            tx_spec = viz.format_spec(tc.send_data)
            if tx_spec:
                lines.append(f"         [{viz.name}] TX spec: {tx_spec}")
            tx_hdrs, tx_vals = viz.format_columns(tc.send_data)
            lines.append(f"         [{viz.name}] TX: " + "  ".join(
                f"{h}={v}" for h, v in zip(tx_hdrs, tx_vals)))
            if response:
                rx_spec = viz.format_spec(response)
                if rx_spec:
                    lines.append(
                        f"         [{viz.name}] RX spec: {rx_spec}")
                hdrs, _, act_vals, _ = viz.diff_columns(
                    response, tc.expect_data, tc.expect_mask)
                lines.append(f"         [{viz.name}] RX: " + "  ".join(
                    f"{h}={v}" for h, v in zip(hdrs, act_vals)))
        self._log_lines(lines)

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
        self._log_lines([line, "-" * 80, ""])

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

    def _update_test_ui(self, tc: TestCase, status: str) -> None:
        """Batch UI update: highlight test, set status, append detail.

        Combines three UI mutations into a single call so that
        ``call_from_thread`` only crosses the thread boundary once
        per test instead of four times.

        Args:
            tc: Test case to display.
            status: Status bar text.
        """
        self._highlight_test(tc)
        self._set_status(status, "bold yellow")
        self._append_test_detail(tc)

    def _get_json_template(self) -> str:
        """Get the JSON filename template from the input field."""
        return self.query_one("#input-json-file", Input).value.strip()

    def _write_json_results(self, tests: list[TestCase],
                            pass_count: int, fail_count: int,
                            elapsed_ms: float,
                            template: str) -> None:
        """Write JSON test results to the proto/test/ directory.

        Uses the same format as the headless proto_runner, reusing
        ``_build_test_result`` and ``expand_result_template``.

        Args:
            tests: Test cases that were executed.
            pass_count: Total passing count.
            fail_count: Total failing count.
            elapsed_ms: Total elapsed time in milliseconds.
            template: Filename template from the UI input field.
        """
        import json

        cfg = self._ctx.cfg
        output_dir = cfg_data_dir(self._ctx.config_path) / "proto" / "test"
        output_dir.mkdir(parents=True, exist_ok=True)

        test_results = []
        for tc in tests:
            if tc.index in self._results:
                response, tc_elapsed, passed = self._results[tc.index]
                test_results.append(
                    _build_test_result(tc, response, tc_elapsed, passed))

        text = self._path.read_text(encoding="utf-8")
        config_name = Path(self._ctx.config_path).stem if self._ctx.config_path else ""
        results = {
            "meta": {
                "script": self._path.name,
                "script_name": self._script.name or self._path.stem,
                "timestamp": datetime.now().isoformat(timespec="milliseconds"),
                "config": config_name,
                "port": cfg.get("port", ""),
                "baud_rate": cfg.get("baud_rate", 0),
                "encoding": cfg.get("encoding", "utf-8"),
            },
            "summary": {
                "total": pass_count + fail_count,
                "passed": pass_count,
                "failed": fail_count,
                "elapsed_ms": round(elapsed_ms, 1),
            },
            "tests": test_results,
            "source": text,
        }

        filename = expand_result_template(template, self._path.stem,
                                          config_name)
        out_path = output_dir / filename
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2)
            self._log(f"JSON results written to {out_path}")
            return str(out_path.resolve())
        except OSError as e:
            self._log(f"Failed to write JSON results: {e}")
            return None

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
            vizs = self.app.call_from_thread(
                self._get_active_visualizers)
            self._open_log()
            self._log_session_header(tests, repeat)
            self._running = True
            self.app.call_from_thread(self._clear_detail)
            self._ctx.engine.set_proto_active(True)
            total_pass = 0
            total_fail = 0
            stopped = False
            t_start = time.monotonic()
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
                        if tc.setup:
                            self._log(f"  setup: {', '.join(tc.setup)}")
                        self._send_proto_cmds(tc.setup)
                        passed = self._execute_one(tc)

                        # Log the result
                        response, elapsed_ms, _ = self._results[tc.index]
                        self._log_test_result(
                            tc, passed, response, elapsed_ms, vizs)

                        prefix = (f"[{run_num}/{repeat}] "
                                  if repeat > 1 else "")
                        status = (f"{prefix}{tc.name} "
                                  f"({ti + 1}/{len(tests)})...")
                        self.app.call_from_thread(
                            self._update_test_ui, tc, status)

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

            total_elapsed_ms = (time.monotonic() - t_start) * 1000
            self._log_summary(tests, total_pass, total_fail, stopped)
            self._show_run_summary(tests, total_pass, total_fail)

            # Write JSON results if checkbox is checked
            json_enabled = self.app.call_from_thread(self._get_json_enabled)
            if json_enabled:
                json_template = self.app.call_from_thread(
                    self._get_json_template)
                json_file = self._write_json_results(
                    tests, total_pass, total_fail,
                    total_elapsed_ms, json_template)
                if json_file:
                    log = self.app.call_from_thread(
                        self.query_one, "#proto-debug-detail", RichLog)
                    self.app.call_from_thread(
                        log.write,
                        Text(f"  JSON: {json_file}", style="dim"))

            # Clear _running *on the UI thread* so that any pending
            # SelectionHighlighted events still see _running=True and
            # don't call _show_test_detail (which clears the log).
            self.app.call_from_thread(setattr, self, "_running", False)
        except RuntimeError:
            # call_from_thread fails during app shutdown - ignore
            pass
        except Exception as e:
            self._log(f"Test runner error: {e}")
        finally:
            self._ctx.engine.set_proto_active(False)
            try:
                self.app.call_from_thread(setattr, self, "_running", False)
            except RuntimeError:
                self._running = False
            self._close_log()

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
            # call_from_thread fails during app shutdown - exit silently
            self._ctx.engine.set_proto_active(False)
        except Exception as e:
            self._ctx.engine.set_proto_active(False)
            self._log(f"Command runner error: {e}")
