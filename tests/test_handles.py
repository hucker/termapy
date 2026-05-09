"""Phase 1 dual-API verification tests.

While the namespaced handles are added alongside the flat fields, both
APIs must produce the same observable behaviour.  These tests verify
that ``ctx.io.write`` and ``ctx.write``, ``ctx.serial.io()`` and
``ctx.serial_io()``, etc., all reach the same backing callables.

Phase 3 will remove the flat fields and this test file is deleted at
that point (its purpose is bounded to the transitional period).
"""

from __future__ import annotations

import pytest

from termapy.plugins import (
    CapabilitySet,
    EngineAPI,
    EngineHandle,
    FilesystemHandle,
    IOHandle,
    MissingCapability,
    PluginContext,
    SerialHandle,
    UIHandle,
)


@pytest.fixture
def ctx_with_capture():
    """PluginContext with output capture lists for assertion."""
    output = []
    markup = []
    log = []

    ctx = PluginContext(
        write=lambda text, color="dim": output.append((text, color)),
        write_markup=lambda text: markup.append(text),
        log=lambda prefix, text: log.append((prefix, text)),
        capabilities=CapabilitySet(
            confirm_dialog=True,
            ui_notify=True,
            status_bar=True,
            screen_capture=True,
            tui_mode=True,
            gui_apps=True,
            block_until=True,
        ),
    )
    return ctx, output, markup, log


class TestHandlesAreAttached:
    """Every PluginContext gets handles automatically via __post_init__."""

    def test_io_handle_attached(self, ctx_with_capture):
        # Arrange / Act
        ctx, _, _, _ = ctx_with_capture

        # Assert
        assert isinstance(ctx.io, IOHandle), "ctx.io is an IOHandle instance"

    def test_serial_handle_attached(self, ctx_with_capture):
        ctx, _, _, _ = ctx_with_capture
        assert isinstance(ctx.serial, SerialHandle), "ctx.serial is a SerialHandle"

    def test_fs_handle_attached(self, ctx_with_capture):
        ctx, _, _, _ = ctx_with_capture
        assert isinstance(ctx.fs, FilesystemHandle), "ctx.fs is a FilesystemHandle"

    def test_ui_handle_attached(self, ctx_with_capture):
        ctx, _, _, _ = ctx_with_capture
        assert isinstance(ctx.ui, UIHandle), "ctx.ui is a UIHandle"

    def test_engine_handle_attached(self, ctx_with_capture):
        ctx, _, _, _ = ctx_with_capture
        assert isinstance(ctx.engine, EngineHandle), "ctx.engine is an EngineHandle"

    def test_engine_api_alias_preserved(self):
        # EngineAPI was the old name; the alias must keep working.
        actual = EngineAPI is EngineHandle
        expected = True
        assert actual == expected, "EngineAPI alias points to EngineHandle"


class TestIOHandleDualAPI:
    """ctx.io.* and ctx.* produce the same observable output."""

    def test_io_write_equals_flat_write(self, ctx_with_capture):
        # Arrange
        ctx, output, _, _ = ctx_with_capture

        # Act
        ctx.io.write("via handle", "green")
        ctx.write("via flat", "red")

        # Assert
        actual = output
        expected = [("via handle", "green"), ("via flat", "red")]
        assert actual == expected, "both APIs hit the same write callable"

    def test_io_log_equals_flat_log(self, ctx_with_capture):
        ctx, _, _, log = ctx_with_capture
        ctx.io.log(">", "out")
        ctx.log("<", "in")
        actual = log
        expected = [(">", "out"), ("<", "in")]
        assert actual == expected, "log delegates through the same callable"

    def test_io_write_markup_delegates(self, ctx_with_capture):
        ctx, _, markup, _ = ctx_with_capture
        ctx.io.write_markup("[bold]hi[/]")
        actual = markup
        expected = ["[bold]hi[/]"]
        assert actual == expected, "write_markup goes through the same callable"

    def test_io_result_routes_via_output_level(self, ctx_with_capture):
        # Arrange -- output_level=quiet means result() shows
        ctx, output, _, _ = ctx_with_capture
        ctx.ns("flags")["output_level"] = "quiet"

        # Act
        ctx.io.result("ok", "green")

        # Assert
        actual = output
        expected = [("ok", "green")]
        assert actual == expected, "result delegates to ctx.result which respects output_level"


class TestSerialHandleDualAPI:
    """ctx.serial.* and ctx.serial_* delegate to the same callables."""

    def test_serial_write_delegates(self):
        # Arrange
        sent = []
        ctx = PluginContext(
            write=lambda *a, **kw: None,
            serial_write=lambda data: sent.append(data),
        )

        # Act
        ctx.serial.write(b"AT+VER\r")
        ctx.serial_write(b"AT+INFO\r")

        # Assert
        actual = sent
        expected = [b"AT+VER\r", b"AT+INFO\r"]
        assert actual == expected, "both forms reach the same backing serial_write"

    def test_serial_io_delegates(self):
        # Arrange
        events = []
        ctx = PluginContext(
            write=lambda *a, **kw: None,
            serial_claim=lambda: events.append("claim"),
            serial_release=lambda: events.append("release"),
        )

        # Act -- both context managers should claim/release identically
        with ctx.serial.io():
            events.append("inside-handle")
        with ctx.serial_io():
            events.append("inside-flat")

        # Assert
        actual = events
        expected = [
            "claim", "inside-handle", "release",
            "claim", "inside-flat", "release",
        ]
        assert actual == expected, "ctx.serial.io() and ctx.serial_io() are equivalent"

    def test_serial_is_connected_delegates(self):
        # Arrange
        ctx = PluginContext(
            write=lambda *a, **kw: None,
            is_connected=lambda: True,
        )

        # Act / Assert
        actual_handle = ctx.serial.is_connected()
        actual_flat = ctx.is_connected()
        assert actual_handle is True, "handle reports True"
        assert actual_flat is True, "flat reports True"


class TestFilesystemHandleDualAPI:
    """ctx.fs.<dir> matches ctx.<dir>; open_file is gated by gui_apps."""

    def test_directory_paths_match(self, tmp_path):
        # Arrange
        cap = tmp_path / "cap"
        scripts = tmp_path / "scripts"
        ctx = PluginContext(
            write=lambda *a, **kw: None,
            cap_dir=cap,
            scripts_dir=scripts,
        )

        # Assert
        actual_cap_handle = ctx.fs.cap_dir
        actual_scripts_handle = ctx.fs.scripts_dir
        assert actual_cap_handle is cap, "ctx.fs.cap_dir is the same Path"
        assert actual_scripts_handle is scripts, "ctx.fs.scripts_dir is the same Path"
        assert ctx.fs.cap_dir is ctx.cap_dir, "handle and flat are the same Path"

    def test_open_file_raises_without_gui_apps(self):
        # Arrange -- default capabilities have gui_apps=False
        opened = []
        ctx = PluginContext(
            write=lambda *a, **kw: None,
            open_file=lambda path: opened.append(path),
            capabilities=CapabilitySet(),  # no gui_apps
        )

        # Act / Assert
        with pytest.raises(MissingCapability) as excinfo:
            ctx.fs.open_file("/tmp/foo")
        assert "gui_apps" in str(excinfo.value), "error names the missing capability"
        assert opened == [], "underlying open_file was not called"

    def test_open_file_works_with_gui_apps(self):
        # Arrange
        opened = []
        ctx = PluginContext(
            write=lambda *a, **kw: None,
            open_file=lambda path: opened.append(path),
            capabilities=CapabilitySet(gui_apps=True),
        )

        # Act
        ctx.fs.open_file("/tmp/foo")

        # Assert
        actual = opened
        expected = ["/tmp/foo"]
        assert actual == expected, "gated method called the underlying open_file"


class TestUIHandleGating:
    """Every UIHandle method raises MissingCapability without the right flag."""

    def test_confirm_raises_without_capability(self):
        ctx = PluginContext(write=lambda *a, **kw: None)
        with pytest.raises(MissingCapability) as excinfo:
            ctx.ui.confirm("Are you sure?")
        assert "confirm_dialog" in str(excinfo.value), "names the capability"

    def test_notify_raises_without_capability(self):
        ctx = PluginContext(write=lambda *a, **kw: None)
        with pytest.raises(MissingCapability) as excinfo:
            ctx.ui.notify("hi")
        assert "ui_notify" in str(excinfo.value), "names the capability"

    def test_screenshot_raises_without_capability(self):
        ctx = PluginContext(write=lambda *a, **kw: None)
        with pytest.raises(MissingCapability) as excinfo:
            ctx.ui.screenshot("/tmp/x.png")
        assert "screen_capture" in str(excinfo.value), "names the capability"

    def test_exit_app_raises_without_capability(self):
        ctx = PluginContext(write=lambda *a, **kw: None)
        with pytest.raises(MissingCapability) as excinfo:
            ctx.ui.exit_app()
        assert "tui_mode" in str(excinfo.value), "names the capability"

    def test_gated_method_works_with_capability(self):
        # Arrange
        confirms = []
        ctx = PluginContext(
            write=lambda *a, **kw: None,
            confirm=lambda msg: confirms.append(msg) or True,
            capabilities=CapabilitySet(confirm_dialog=True),
        )

        # Act
        result = ctx.ui.confirm("Delete?")

        # Assert
        assert result is True, "gated method returned the underlying value"
        assert confirms == ["Delete?"], "underlying confirm was called with the message"


class TestPostConstructionOverridesFlowThrough:
    """Handles read ctx attrs live; post-construction overrides take effect."""

    def test_io_write_picks_up_overridden_callable(self):
        # Arrange -- mimics TUI's post-construction override pattern
        first = []
        ctx = PluginContext(write=lambda t, c=None: first.append((t, c)))

        # Override after construction (this is what app.py does)
        second = []
        ctx.write = lambda t, c=None: second.append((t, c))

        # Act
        ctx.io.write("after override", "blue")

        # Assert
        actual_first = first
        actual_second = second
        assert actual_first == [], "first sink not used after override"
        assert actual_second == [("after override", "blue")], "handle picked up override"
