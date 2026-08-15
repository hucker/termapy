"""Simulated NDJSON serial device for ``DEMO_JSON`` port name.

Modern-device archetype: every wire message is a single-line JSON
object terminated by ``\\n``.  Tests and demos exercise the
recommended MCP path against this simulator without needing real
hardware.

**Wire format (matches smart_sensor.profile.json):**

Request (write):
    ``{"cmd": "<name>", "args": {...}, "id": <int>}\\n``

Response (read):
    ``{"ok": true, "result": {...}, "id": <int>}\\n``  or
    ``{"ok": false, "error": "<msg>", "code": <int>, "id": <int>}\\n``

Async (read, no preceding request):
    ``{"event": "<name>", ...}\\n``  or
    ``{"error": "<msg>", "code": <int>}\\n``

**Startup:** the first read after open delivers a ``ready`` banner:
    ``{"event": "ready", "device": "BASSOMATIC", "fw": "1.2.3"}\\n``

**Command catalog (matches smart_sensor.profile.json):**

* ``get_temp``      -> ``{"celsius": <float>}``
* ``get_status``    -> full state dict
* ``set_threshold`` -> echo of the new threshold
* ``set_mode``      -> echo of the new mode
* ``calibrate``     -> ``{"calibrated": true}``
* ``reset``         -> fire-and-forget; banner re-emits on next read

**Async events / errors:** not auto-emitted (would make tests flaky).
Tests use ``emit_event(...)`` and ``emit_async_error(...)`` to inject
on demand; the bridge then exercises the ``ndjson_field_routing`` cfg
keys.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

_BANNER: dict[str, Any] = {
    "event": "ready",
    "device": "BASSOMATIC",
    "fw": "1.2.3",
}


def _line(obj: dict) -> bytes:
    """Serialize a dict as a single NDJSON line (no embedded newlines).

    ``ensure_ascii=True`` is intentional -- the wire is bytes; pretty
    Unicode display happens in termapy's terminal layer.
    """
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


class FakeSerialNDJSON:
    """Simulated NDJSON serial device.  Duck-types ``serial.Serial``.

    Selected by setting ``cfg["serial"]["port"] = "DEMO_JSON"``.  Same shape as
    ``demo.FakeSerial`` so the engine can use it transparently.
    """

    def __init__(self, baudrate: int = 115200, port: str = "DEMO_JSON") -> None:
        self._port: str = port
        self._baudrate: int = baudrate
        self._is_open: bool = True
        self._bytesize: int = 8
        self._parity: str = "N"
        self._stopbits: float = 1
        self._dtr: bool = True
        self._rts: bool = True
        self._rtscts: bool = False
        self._xonxoff: bool = False
        self._timeout: float | None = None

        self._lock = threading.Lock()
        self._input_buf = bytearray()
        self._output_buf = bytearray()

        # Banner is queued on first read; tracked so reset can re-arm it.
        self._banner_pending: bool = True
        # Device state -- the "model" the LLM interrogates.
        self._state: dict[str, Any] = {
            "led": False,
            "temp_c": 23.5,
            "threshold_c": 50.0,
            "mode": "idle",
            "uptime_s": 0,
        }
        self._start_time: float = time.time()

    # -- serial.Serial properties ------------------------------------------

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def port(self) -> str:
        return self._port

    @port.setter
    def port(self, value: str) -> None:
        self._port = value

    @property
    def baudrate(self) -> int:
        return self._baudrate

    @baudrate.setter
    def baudrate(self, value: int) -> None:
        self._baudrate = value

    @property
    def bytesize(self) -> int:
        return self._bytesize

    @bytesize.setter
    def bytesize(self, value: int) -> None:
        self._bytesize = value

    @property
    def parity(self) -> str:
        return self._parity

    @parity.setter
    def parity(self, value: str) -> None:
        self._parity = value

    @property
    def stopbits(self) -> float:
        return self._stopbits

    @stopbits.setter
    def stopbits(self, value: float) -> None:
        self._stopbits = value

    @property
    def timeout(self) -> float | None:
        return self._timeout

    @timeout.setter
    def timeout(self, value: float | None) -> None:
        self._timeout = value

    @property
    def dtr(self) -> bool:
        return self._dtr

    @dtr.setter
    def dtr(self, value: bool) -> None:
        self._dtr = value

    @property
    def rts(self) -> bool:
        return self._rts

    @rts.setter
    def rts(self, value: bool) -> None:
        self._rts = value

    @property
    def rtscts(self) -> bool:
        return self._rtscts

    @rtscts.setter
    def rtscts(self, value: bool) -> None:
        self._rtscts = value

    @property
    def xonxoff(self) -> bool:
        return self._xonxoff

    @xonxoff.setter
    def xonxoff(self, value: bool) -> None:
        self._xonxoff = value

    @property
    def cts(self) -> bool:
        return True

    @property
    def dsr(self) -> bool:
        return True

    @property
    def ri(self) -> bool:
        return False

    @property
    def cd(self) -> bool:
        return True

    @property
    def in_waiting(self) -> int:
        with self._lock:
            self._maybe_queue_banner()
            return len(self._output_buf)

    # -- serial.Serial methods ---------------------------------------------

    def write(self, data: bytes) -> int:
        """Buffer input; on each ``\\n``, parse JSON and dispatch."""
        with self._lock:
            self._input_buf.extend(data)
            self._process_input()
        return len(data)

    def read(self, size: int = 1) -> bytes:
        """Read up to ``size`` bytes, blocking up to ``self.timeout`` seconds."""
        deadline = time.time() + (self._timeout or 0)
        while True:
            with self._lock:
                self._maybe_queue_banner()
                if self._output_buf:
                    chunk = bytes(self._output_buf[:size])
                    del self._output_buf[:size]
                    return chunk
            if time.time() >= deadline:
                return b""
            time.sleep(0.001)

    def readline(self, size: int = -1) -> bytes:
        """Read one ``\\n``-terminated line, blocking up to ``self.timeout``.

        Matches ``serial.Serial.readline``: returns bytes including the
        trailing newline, or whatever was buffered if the timeout fires
        first.  Duck-typing pyserial means consumers expecting this
        method (e.g. legacy text-protocol parsers) keep working.
        """
        deadline = time.time() + (self._timeout or 0)
        out = bytearray()
        while True:
            with self._lock:
                self._maybe_queue_banner()
                while self._output_buf:
                    b = self._output_buf[0]
                    out.append(b)
                    del self._output_buf[:1]
                    if b == 0x0A:  # '\n'
                        return bytes(out)
                    if size != -1 and len(out) >= size:
                        return bytes(out)
            if time.time() >= deadline:
                return bytes(out)
            time.sleep(0.001)

    def close(self) -> None:
        self._is_open = False

    def send_break(self, duration: float = 0.25) -> None:
        """No-op for simulated device."""

    # -- Public test helpers (inject async traffic on demand) ─────────────

    def emit_event(self, event_name: str, **extra: Any) -> None:
        """Queue an async event line for the next read.

        Used by tests to exercise the bridge's NDJSON
        ``ndjson_field_routing`` cfg keys without dealing with timing.
        """
        with self._lock:
            payload = {"event": event_name}
            payload.update(extra)
            self._output_buf.extend(_line(payload))

    def emit_async_error(self, message: str, code: int = -1) -> None:
        """Queue an async (un-id'd) error line for the next read."""
        with self._lock:
            self._output_buf.extend(_line({"error": message, "code": code}))

    # -- Internal -----------------------------------------------------------

    def _maybe_queue_banner(self) -> None:
        """Lazily queue the startup banner.

        Called from in_waiting() and read() so the first byte the host
        sees is the banner -- whether it polls or blocks first.  Caller
        must hold ``self._lock``.
        """
        if self._banner_pending:
            self._output_buf.extend(_line(_BANNER))
            self._banner_pending = False

    def _process_input(self) -> None:
        """Drain complete ``\\n``-terminated lines from the input buffer."""
        while True:
            try:
                idx = self._input_buf.index(b"\n")
            except ValueError:
                return  # no complete line yet
            raw = bytes(self._input_buf[:idx])
            del self._input_buf[: idx + 1]
            self._handle_line(raw)

    def _handle_line(self, raw: bytes) -> None:
        """Parse one inbound line as JSON; dispatch or emit error."""
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            return  # blank lines are ignored
        try:
            req = json.loads(text)
        except json.JSONDecodeError as e:
            # Malformed input: respond with an error.  No id available
            # so the bridge will route this as an async error.
            self._output_buf.extend(
                _line({"error": f"invalid json: {e}", "code": -2})
            )
            return
        if not isinstance(req, dict):
            self._output_buf.extend(
                _line({"error": "request must be a JSON object", "code": -3})
            )
            return
        response = self._dispatch_request(req)
        if response is not None:
            self._output_buf.extend(_line(response))

    def _dispatch_request(self, req: dict) -> dict | None:
        """Route a parsed request to its handler.  Returns the response dict.

        Returning ``None`` means fire-and-forget (no response on the wire).
        ``reset`` uses this to mimic real-device behavior where the
        next thing the host sees is the post-reset banner.
        """
        cmd = req.get("cmd", "")
        raw_args = req.get("args")
        args: dict = raw_args if isinstance(raw_args, dict) else {}
        req_id = req.get("id")

        # Update the live uptime so /get_status sees a moving value.
        self._state["uptime_s"] = int(time.time() - self._start_time)

        if cmd == "get_temp":
            return self._ok({"celsius": self._state["temp_c"]}, req_id)
        if cmd == "get_status":
            return self._ok(dict(self._state), req_id)
        if cmd == "set_threshold":
            try:
                celsius = float(args.get("celsius", self._state["threshold_c"]))
            except (TypeError, ValueError):
                return self._err("celsius must be a number", -10, req_id)
            self._state["threshold_c"] = celsius
            return self._ok({"set": celsius}, req_id)
        if cmd == "set_mode":
            mode = str(args.get("mode", ""))
            if mode not in ("idle", "active", "sleep", "diagnostic"):
                return self._err(f"unknown mode: {mode}", -11, req_id)
            self._state["mode"] = mode
            return self._ok({"mode": mode}, req_id)
        if cmd == "calibrate":
            return self._ok({"calibrated": True}, req_id)
        if cmd == "reset":
            # Fire-and-forget: re-arm the banner so the next read sees it.
            self._banner_pending = True
            self._state["mode"] = "idle"
            return None
        return self._err(f"unknown cmd: {cmd}", -1, req_id)

    @staticmethod
    def _ok(result: dict, req_id: Any) -> dict:
        """Build a success response.  Includes ``id`` only when the request had one."""
        out: dict[str, Any] = {"ok": True, "result": result}
        if req_id is not None:
            out["id"] = req_id
        return out

    @staticmethod
    def _err(message: str, code: int, req_id: Any) -> dict:
        """Build an error response.  Includes ``id`` only when the request had one."""
        out: dict[str, Any] = {"ok": False, "error": message, "code": code}
        if req_id is not None:
            out["id"] = req_id
        return out
