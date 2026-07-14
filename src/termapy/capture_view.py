"""TUI capture-progress view: start/stop, status bar, and progress widget.

Previously lived as six ``_cap_*`` methods on ``SerialTerminal`` in
``app.py``.  Extracted here so the capture-display surface is a named,
self-contained subsystem -- ``ls src/termapy/`` now shows ``capture_view.py``
alongside the other top-level pieces.

Each function takes the app as its first argument and reaches widget,
timer, and engine state via ``app.X``.  Two of the six --
``_cap_start`` and ``_cap_stop`` -- are referenced from outside this
module (the host wires them onto ``internal.start_capture`` /
``internal.stop_capture`` in ``_build_context``, the cap-stop button
binding looks them up by attribute name, and the capture engine's
``on_capture_done`` callback fires ``_cap_stop`` via
``call_from_thread``).  SerialTerminal keeps thin method stubs for
those two so the string-lookup + bound-method paths keep working;
all six free functions live here.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from textual.css.query import NoMatches
from textual.widgets import Button, Input, Static

# Mirror of the SHUTDOWN_RACE tuple in app.py.  Used by widget-touching
# helpers here to swallow Textual teardown races (NoMatches when a
# widget tree has been unmounted, RuntimeError when a worker thread
# tries to call_from_thread on an already-exited app loop).
SHUTDOWN_RACE: tuple[type[BaseException], ...] = (NoMatches, RuntimeError)

if TYPE_CHECKING:
    from termapy.app import SerialTerminal  # noqa: F401 -- type-hint surface


def _cap_start(
    app,
    *,
    path: Path,
    file_mode: str,
    mode: str,
    duration: float = 0.0,
    target_bytes: int = 0,
    columns: list | None = None,
    record_size: int = 0,
    sep: str = ",",
    echo: bool = False,
    hex_mode: bool = False,
    timeout: float = 0.0,
) -> bool:
    """Start a file capture session (delegates to CaptureEngine)."""
    if app._capture.active:
        app._status("Capture already active - use .stop first.", "yellow")
        return False

    started = app._capture.start(
        path=path,
        file_mode=file_mode,
        mode=mode,
        duration=duration,
        target_bytes=target_bytes,
        columns=columns,
        record_size=record_size,
        sep=sep,
        echo=echo,
        hex_mode=hex_mode,
        timeout=timeout,
    )
    if not started:
        app._status(f"Cannot open capture file: {path}", "red")
        return False

    if mode == "text":
        app._cap_timer = app.set_timer(duration, app._cap_stop)
    else:
        app._engine.serial_claimed = True
        if timeout > 0:
            app._cap_timer = app.set_timer(timeout, app._cap_stop)
        else:
            app._cap_timer = None

    _cap_show_progress(app)
    raw = not columns
    mode_label = "raw" if raw else ("fmt" if columns else "text")
    app._log_line("#", f"capture start: {path} mode={mode_label}")
    return True


def _cap_stop(app) -> None:
    """End file capture: delegate to engine, restore UI."""
    if not app._capture.active:
        return

    result = app._capture.stop()

    if not app.repl.in_script:
        app._engine.serial_claimed = False

    if app._cap_timer:
        app._cap_timer.stop()
        app._cap_timer = None

    _cap_hide_progress(app)

    if result:
        if result.error:
            app._status(f"Capture aborted: {result.error} ({result.path})", "red")
            app._log_line("#", f"capture aborted: {result.path} ({result.error})")
        else:
            app._status(
                f"Capture complete: {result.path} ({result.size_label})", "green"
            )
            app._log_line("#", f"capture end: {result.path} ({result.size_label})")
    app._sync_cap_button()


def _cap_show_progress(app) -> None:
    """Mount a progress overlay in the bottom bar."""
    if app.repl.in_script:
        return  # script overlay owns the bar
    try:
        bar = app.query_one("#bottom-bar")
        for child in bar.children:
            child.display = False
        label = Static("", id="cap-label")
        label.styles.width = "1fr"
        stop_btn = Button("Stop", id="cap-stop", variant="error")
        bar.mount(label)
        bar.mount(stop_btn)
        app._cap_progress_timer = app.set_interval(0.5, lambda: _cap_update_progress(app))
        inp = app.query_one("#cmd", Input)
        inp.disabled = True
        inp.focus()
    except SHUTDOWN_RACE:
        pass  # capture started before mount or during teardown


def _cap_update_progress(app) -> None:
    """Update the capture progress label."""
    prog = app._capture.get_progress()
    if not prog:
        return
    try:
        label = app.query_one("#cap-label", Static)
    except SHUTDOWN_RACE:
        return  # overlay torn down; next timer tick will be a no-op
    if prog.mode == "text":
        label.update(
            f" Capturing -> {prog.path_name}  [{prog.pct}%]  "
            f"{prog.remaining_s:.1f}s left  {prog.bytes_captured} bytes"
        )
    else:
        label.update(
            f" Capturing -> {prog.path_name}  [{prog.pct}%]  "
            f"{prog.bytes_captured}/{prog.target_bytes} bytes"
        )


def _cap_hide_progress(app) -> None:
    """Remove the capture overlay and restore normal buttons."""
    if app._cap_progress_timer:
        app._cap_progress_timer.stop()
        app._cap_progress_timer = None
    if app.repl.in_script:
        return  # script overlay owns the bar
    try:
        bar = app.query_one("#bottom-bar")
        for widget in bar.query("#cap-label, #cap-stop"):
            widget.remove()
        for child in bar.children:
            child.display = True
        inp = app.query_one("#cmd", Input)
        inp.disabled = False
        inp.focus()
    except SHUTDOWN_RACE:
        pass  # overlay gone or main widgets unmounted


def _run_progress_bar(app, seconds: float, label: str) -> None:
    """Block on the caller's thread while animating a progress bar in
    the bottom-bar status label.  Same rendering as the CLI and
    script-path delays -- shares ``render_progress_bar`` in
    scripting.py.

    Escape (action_stop_script) sets ``repl._script_stop`` and ends
    the wait early.  Caller must be on a background thread; label
    writes hop to the main thread via ``_on_main``.
    """
    from termapy.scripting import render_progress_bar

    t0 = time.perf_counter()
    canceled = False
    while True:
        elapsed = time.perf_counter() - t0
        if elapsed >= seconds:
            break
        if app.repl._script_stop.is_set():
            canceled = True
            break
        app._on_main(
            app._set_progress_label,
            render_progress_bar(elapsed, seconds),
        )
        time.sleep(0.25)
    app._on_main(app._set_progress_label, "")
    if canceled:
        app._on_main(app._status, f"Delay {label} canceled.", "red")
    else:
        app._on_main(app._status, f"Delay {label} done.")


