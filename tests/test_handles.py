"""Tests for the namespaced PluginContext handles.

After Phase 3, ``PluginContext`` has no flat fields -- every
host-provided callable lives on its capability-domain handle
(``ctx.io``, ``ctx.serial``, ``ctx.fs``, ``ctx.ui``, ``ctx.engine``).
These tests verify that:

  - Every PluginContext gets the 5 handles by default (no None).
  - Hosts can wire callables into a handle and they reach the right
    namespace.
  - Capability-gated methods on UIHandle and FilesystemHandle raise
    :class:`MissingCapability` when the host's CapabilitySet doesn't
    advertise the required flag.
  - PluginContext.__post_init__ correctly threads the capabilities
    snapshot into ``ctx.fs`` and ``ctx.ui`` so the gates fire on
    the right CapabilitySet.
  - IOHandle.result/output/status route through the live
    ``ctx.output_level`` so per-call ``--silent`` / ``cmd.quiet``
    overrides take effect.
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
        io=IOHandle(
            write=lambda text, color="dim": output.append((text, color)),
            write_markup=lambda text: markup.append(text),
            log=lambda prefix, text: log.append((prefix, text)),
        ),
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
    """Every PluginContext gets default handles even with no kwargs."""

    def test_default_handles_are_real_instances(self):
        ctx = PluginContext()
        assert isinstance(ctx.io, IOHandle), "ctx.io is an IOHandle"
        assert isinstance(ctx.serial, SerialHandle), "ctx.serial is a SerialHandle"
        assert isinstance(ctx.fs, FilesystemHandle), "ctx.fs is a FilesystemHandle"
        assert isinstance(ctx.ui, UIHandle), "ctx.ui is a UIHandle"
        assert isinstance(ctx.engine, EngineHandle), "ctx.engine is an EngineHandle"

    def test_engine_api_alias_preserved(self):
        # EngineAPI was the old name; the alias must keep working.
        actual = EngineAPI is EngineHandle
        expected = True
        assert actual == expected, "EngineAPI alias points to EngineHandle"


class TestIOHandle:
    """IOHandle delegates to wired callables and respects output level."""

    def test_write_delegates_to_wired_callable(self, ctx_with_capture):
        ctx, output, _, _ = ctx_with_capture
        ctx.io.write("hello", "green")
        actual = output
        expected = [("hello", "green")]
        assert actual == expected, "write goes to the wired callable"

    def test_write_markup_delegates(self, ctx_with_capture):
        ctx, _, markup, _ = ctx_with_capture
        ctx.io.write_markup("[bold]hi[/]")
        actual = markup
        expected = ["[bold]hi[/]"]
        assert actual == expected, "write_markup goes to the wired callable"

    def test_log_delegates(self, ctx_with_capture):
        ctx, _, _, log = ctx_with_capture
        ctx.io.log(">", "out")
        ctx.io.log("<", "in")
        actual = log
        expected = [(">", "out"), ("<", "in")]
        assert actual == expected, "log goes to the wired callable"

    def test_result_routes_via_output_level(self, ctx_with_capture):
        # Arrange -- output_level=quiet means result() shows
        ctx, output, _, _ = ctx_with_capture
        ctx.ns("flags")["output_level"] = "quiet"

        # Act
        ctx.io.result("ok", "green")

        # Assert
        actual = output
        expected = [("ok", "green")]
        assert actual == expected, "result writes when level >= quiet"

    def test_result_suppressed_at_silent_level(self, ctx_with_capture):
        ctx, output, _, _ = ctx_with_capture
        ctx.ns("flags")["output_level"] = "silent"
        ctx.io.result("hidden")
        actual = output
        expected = []
        assert actual == expected, "result is suppressed at silent"

    def test_status_only_at_verbose(self, ctx_with_capture):
        ctx, output, _, _ = ctx_with_capture
        # quiet/normal: hidden
        ctx.ns("flags")["output_level"] = "normal"
        ctx.io.status("normal-hidden")
        # verbose: shown
        ctx.ns("flags")["output_level"] = "verbose"
        ctx.io.status("verbose-shown")
        actual = output
        expected = [("verbose-shown", "dim")]
        assert actual == expected, "status only fires at verbose"

    def test_per_call_level_override(self, ctx_with_capture):
        ctx, output, _, _ = ctx_with_capture
        # Global level: silent.  Per-call override: normal.
        ctx.ns("flags")["output_level"] = "silent"
        ctx._call_level = "normal"
        ctx.io.result("via-override", "green")
        actual = output
        expected = [("via-override", "green")]
        assert actual == expected, "per-call _call_level wins over global"


class TestSerialHandle:
    """SerialHandle delegates to wired callables and provides ctx managers."""

    def test_send_delegates(self):
        sent = []
        ctx = PluginContext(
            serial=SerialHandle(send=lambda text: sent.append(text)),
        )
        ctx.serial.send("AT+VER\r")
        actual = sent
        expected = ["AT+VER\r"]
        assert actual == expected, "send goes to the wired callable"

    def test_io_context_manager_calls_claim_and_release(self):
        events = []
        ctx = PluginContext(
            serial=SerialHandle(
                claim=lambda: events.append("claim"),
                release=lambda: events.append("release"),
            ),
        )
        with ctx.serial.io():
            events.append("inside")
        actual = events
        expected = ["claim", "inside", "release"]
        assert actual == expected, "io() claims, yields, releases"

    def test_io_releases_on_exception(self):
        events = []
        ctx = PluginContext(
            serial=SerialHandle(
                claim=lambda: events.append("claim"),
                release=lambda: events.append("release"),
            ),
        )
        with pytest.raises(RuntimeError):
            with ctx.serial.io():
                events.append("inside")
                raise RuntimeError("boom")
        actual = events
        expected = ["claim", "inside", "release"]
        assert actual == expected, "release fires even on exception"

    def test_rx_observer_register_and_release(self):
        registered = []
        ctx = PluginContext(
            serial=SerialHandle(
                _add_rx_observer=lambda cb: registered.append(("add", cb)),
                _remove_rx_observer=lambda cb: registered.append(("remove", cb)),
            ),
        )
        cb = lambda data: None
        with ctx.serial.rx_observer(cb):
            assert registered == [("add", cb)], "added on enter"
        actual = registered
        expected = [("add", cb), ("remove", cb)]
        assert actual == expected, "removed on exit"


class TestFilesystemHandle:
    """FilesystemHandle exposes paths and gates open_file on gui_apps."""

    def test_directory_paths_round_trip(self, tmp_path):
        cap = tmp_path / "cap"
        scripts = tmp_path / "scripts"
        ctx = PluginContext(
            fs=FilesystemHandle(cap_dir=cap, scripts_dir=scripts),
        )
        actual_cap = ctx.fs.cap_dir
        actual_scripts = ctx.fs.scripts_dir
        assert actual_cap is cap, "cap_dir round-trips through the handle"
        assert actual_scripts is scripts, "scripts_dir round-trips through the handle"

    def test_open_file_raises_without_gui_apps(self):
        opened = []
        ctx = PluginContext(
            fs=FilesystemHandle(_open_file_impl=lambda path: opened.append(path)),
            capabilities=CapabilitySet(),  # no gui_apps
        )
        with pytest.raises(MissingCapability) as excinfo:
            ctx.fs.open_file("/tmp/foo")
        assert "gui_apps" in str(excinfo.value), "error names the missing capability"
        assert opened == [], "underlying open_file was not called"

    def test_open_file_works_with_gui_apps(self):
        opened = []
        ctx = PluginContext(
            fs=FilesystemHandle(_open_file_impl=lambda path: opened.append(path)),
            capabilities=CapabilitySet(gui_apps=True),
        )
        ctx.fs.open_file("/tmp/foo")
        actual = opened
        expected = ["/tmp/foo"]
        assert actual == expected, "gated method called the underlying impl"


class TestUIHandleGating:
    """Every gated UIHandle method raises MissingCapability without the right flag."""

    def test_confirm_raises_without_capability(self):
        ctx = PluginContext()
        with pytest.raises(MissingCapability) as excinfo:
            ctx.ui.confirm("Are you sure?")
        assert "confirm_dialog" in str(excinfo.value), "names the capability"

    def test_notify_raises_without_capability(self):
        ctx = PluginContext()
        with pytest.raises(MissingCapability) as excinfo:
            ctx.ui.notify("hi")
        assert "ui_notify" in str(excinfo.value), "names the capability"

    def test_screenshot_raises_without_capability(self):
        ctx = PluginContext()
        with pytest.raises(MissingCapability) as excinfo:
            ctx.ui.screenshot("/tmp/x.png")
        assert "screen_capture" in str(excinfo.value), "names the capability"

    def test_exit_app_is_not_gated(self):
        # ctx.ui.exit_app is intentionally NOT gated -- the underlying
        # impl is a no-op in CLI/MCP and a real exit in TUI.  Plugins
        # gate via Command.needs=CapabilitySet(interactive=True) at the
        # dispatcher level instead.
        called = []
        ctx = PluginContext(ui=UIHandle(_exit_app_impl=lambda: called.append(True)))
        ctx.ui.exit_app()
        actual = called
        expected = [True]
        assert actual == expected, "exit_app calls through unconditionally"

    def test_gated_method_works_with_capability(self):
        confirms = []
        ctx = PluginContext(
            ui=UIHandle(_confirm_impl=lambda msg: confirms.append(msg) or True),
            capabilities=CapabilitySet(confirm_dialog=True),
        )
        result = ctx.ui.confirm("Delete?")
        assert result is True, "gated method returned the underlying value"
        assert confirms == ["Delete?"], "underlying confirm was called with the message"


class TestPostInitWiring:
    """PluginContext.__post_init__ threads ctx state into handles."""

    def test_io_output_level_fn_reads_ctx_level(self):
        # Build a ctx where io's output_level_fn should reflect ctx.output_level
        ctx = PluginContext()
        ctx.ns("flags")["output_level"] = "verbose"
        actual = ctx.io.output_level_fn()
        expected = "verbose"
        assert actual == expected, "io reads the live ctx output_level"

    def test_fs_capabilities_match_ctx_capabilities(self):
        caps = CapabilitySet(gui_apps=True)
        ctx = PluginContext(capabilities=caps)
        actual = ctx.fs.capabilities
        expected = caps
        assert actual == expected, "fs capabilities snapshot matches ctx"

    def test_ui_capabilities_match_ctx_capabilities(self):
        caps = CapabilitySet(confirm_dialog=True)
        ctx = PluginContext(capabilities=caps)
        actual = ctx.ui.capabilities
        expected = caps
        assert actual == expected, "ui capabilities snapshot matches ctx"
