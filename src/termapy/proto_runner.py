"""Headless protocol test runner with JSON result output.

Runs .pro test scripts without the Textual TUI - opens the serial port
directly, executes tests, writes a JSON result file, and exits.

No Textual dependency.  Reuses parsing and matching from protocol.py
and serial port setup from config.py.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from termapy.config import open_serial
from termapy.protocol import (
    FrameCollector,
    ProtoScript,
    TestCase,
    load_proto_script,
    match_response,
    strip_ansi,
)


def _bytes_to_hex(data: bytes) -> str:
    """Format bytes as space-separated hex."""
    return " ".join(f"{b:02X}" for b in data)


def _bytes_to_text(data: bytes) -> str:
    """Format bytes as a repr-style text string with escape sequences."""
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        return _bytes_to_hex(data)
    return repr(text)[1:-1]  # strip surrounding quotes


def _read_frame(ser, frame_gap_ms: int, timeout_ms: int) -> bytes:
    """Read a complete frame from the serial port using silence-gap detection.

    Reads directly from the serial object (no queue, no Textual).

    Args:
        ser: Serial port object (real or FakeSerial).
        frame_gap_ms: Silence gap to detect end of frame.
        timeout_ms: Maximum time to wait for any response.

    Returns:
        Complete frame bytes, or empty bytes on timeout.
    """
    collector = FrameCollector(timeout_ms=frame_gap_ms)
    deadline = time.monotonic() + timeout_ms / 1000.0

    while time.monotonic() < deadline:
        waiting = ser.in_waiting if hasattr(ser, "in_waiting") else 0
        if waiting > 0:
            chunk = ser.read(waiting)
            if chunk:
                frame = collector.feed(chunk, time.monotonic())
                if frame is not None:
                    return frame
        else:
            frame = collector.flush(time.monotonic())
            if frame is not None:
                return frame
            time.sleep(0.01)

    return collector.flush(time.monotonic()) or b""


def _drain(ser) -> None:
    """Discard any pending bytes in the serial buffer."""
    while True:
        waiting = ser.in_waiting if hasattr(ser, "in_waiting") else 0
        if waiting > 0:
            ser.read(waiting)
        else:
            break


def _run_setup_cmds(ser, cmds: list[str], cfg: dict,
                    frame_gap_ms: int) -> None:
    """Send setup/teardown commands and drain responses."""
    line_ending = cfg.get("line_ending", "\r")
    enc = cfg.get("encoding", "utf-8")
    for cmd_text in cmds:
        ser.write((cmd_text + line_ending).encode(enc))
        _read_frame(ser, frame_gap_ms, 1000)  # drain response


def _build_test_result(tc: TestCase, response: bytes | None,
                       elapsed_ms: float, passed: bool | None) -> dict:
    """Build a JSON-serializable dict for a single test result."""
    result: dict = {
        "index": tc.index,
        "name": tc.name,
        "passed": passed,
        "elapsed_ms": round(elapsed_ms, 1),
        "send_hex": _bytes_to_hex(tc.send_data),
        "expect_hex": _bytes_to_hex(tc.expect_data),
    }
    # Add text representations
    if tc.send_data:
        result["send_text"] = _bytes_to_text(tc.send_data)
    if tc.expect_data:
        result["expect_text"] = _bytes_to_text(tc.expect_data)

    if response is not None:
        result["actual_hex"] = _bytes_to_hex(response)
        result["actual_text"] = _bytes_to_text(response)
    else:
        result["actual_hex"] = ""
        result["error"] = f"Timeout ({tc.timeout_ms}ms)"

    # Include format specs if present
    if tc.send_fmt:
        result["send_fmt"] = tc.send_fmt
    if tc.expect_fmt:
        result["expect_fmt"] = tc.expect_fmt

    return result


def expand_result_template(template: str, proto_name: str,
                           config_name: str = "") -> str:
    """Expand a result filename template.

    Supported placeholders:

    - ``{name}`` - config/project name (e.g. ``demo``)
    - ``{proto_name}`` - .pro file stem (e.g. ``at_test``)
    - ``{datetime}`` - ``YYYY-MM-DD-HH-MM-SS``
    - ``{date}`` - ``YYYYMMDD``
    - ``{time}`` - ``HHMMSS``

    For backward compatibility, ``{name}`` falls back to the proto file
    stem when no config name is provided.

    Args:
        template: Filename template string.
        proto_name: Base name from the .pro file (stem, no extension).
        config_name: Config/project name (e.g. ``demo``).

    Returns:
        Expanded filename string.
    """
    now = datetime.now()
    return template.format(
        name=config_name or proto_name,
        proto_name=proto_name,
        datetime=now.strftime("%Y-%m-%d-%H-%M-%S"),
        date=now.strftime("%Y%m%d"),
        time=now.strftime("%H%M%S"),
    )


def run_proto_tests(
    pro_path: Path,
    cfg: dict,
    output_dir: Path | None = None,
    template: str = "{name}_results.json",
) -> dict:
    """Run a .pro test script headlessly and write JSON results.

    Args:
        pro_path: Path to the .pro test script file.
        cfg: Config dict with serial settings.
        output_dir: Directory for JSON output. None = pro_path.parent / "test".
        template: Filename template for result file.

    Returns:
        JSON-serializable results dict.

    Raises:
        ValueError: If the .pro file is not TOML format with [[test]] sections.
        serial.SerialException: If the serial port cannot be opened.
    """
    # Parse script
    text = pro_path.read_text(encoding="utf-8")
    fmt, parsed = load_proto_script(text)
    if fmt != "toml":
        raise ValueError(
            f"{pro_path.name} is flat format - only TOML scripts with "
            f"[[test]] sections are supported for JSON test results"
        )
    script: ProtoScript = parsed  # type: ignore[assignment]

    # Open serial port
    ser = open_serial(cfg)

    # Resolve output directory
    if output_dir is None:
        output_dir = pro_path.parent / "test"
    output_dir.mkdir(parents=True, exist_ok=True)

    frame_gap = script.frame_gap_ms
    pass_count = 0
    fail_count = 0
    test_results: list[dict] = []
    t_start = time.monotonic()

    try:
        _drain(ser)

        # Run script-level setup commands
        _run_setup_cmds(ser, script.setup, cfg, frame_gap)

        for tc in script.tests:
            # Per-test setup
            _run_setup_cmds(ser, tc.setup, cfg, frame_gap)

            _drain(ser)
            ser.write(tc.send_data)

            t0 = time.monotonic()
            response = _read_frame(ser, frame_gap, tc.timeout_ms)
            elapsed_ms = (time.monotonic() - t0) * 1000

            if script.strip_ansi:
                response = strip_ansi(response)

            if response:
                passed = match_response(tc.expect_data, response, tc.expect_mask)
                if passed:
                    pass_count += 1
                else:
                    fail_count += 1
            else:
                passed = False
                response = None
                fail_count += 1

            test_results.append(
                _build_test_result(tc, response, elapsed_ms, passed)
            )

            # Per-test teardown
            _run_setup_cmds(ser, tc.teardown, cfg, frame_gap)

        # Script-level teardown
        _run_setup_cmds(ser, script.teardown, cfg, frame_gap)

    finally:
        try:
            ser.close()
        except Exception:
            pass

    total_elapsed = (time.monotonic() - t_start) * 1000

    results = {
        "meta": {
            "script": pro_path.name,
            "script_name": script.name or pro_path.stem,
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            "config": Path(cfg.get("_config_path", "")).stem or "",
            "port": cfg.get("port", ""),
            "baud_rate": cfg.get("baud_rate", 0),
            "encoding": cfg.get("encoding", "utf-8"),
        },
        "summary": {
            "total": pass_count + fail_count,
            "passed": pass_count,
            "failed": fail_count,
            "elapsed_ms": round(total_elapsed, 1),
        },
        "tests": test_results,
        "source": text,
    }

    # Write JSON file - script-level json_file overrides config template
    effective_template = script.json_file or template
    config_name = Path(cfg.get("_config_path", "")).stem or ""
    filename = expand_result_template(effective_template, pro_path.stem,
                                      config_name)
    out_path = output_dir / filename
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    return results
