"""Simulated VT100/ANSI serial device for the ``DEMO_VT100`` port name.

Exists so ``termapy --vt100 --demo`` shows the passthrough doing something
only a terminal-aware mode can: a colored, cursor-addressed, interactive UI.
Plain demo devices (``DEMO``/``DEMO_JSON``) emit line-oriented text, which
looks identical in CLI and VT100 mode and proves nothing about VT100.

It's a small tour of terminal UI widgets behind a navigable menu (Up/Down or
``j``/``k`` move, Enter opens, ``q``/Backspace goes back, Ctrl-] quits):

* **Status** - a live dashboard of colored bar **gauges** that animate.
* **Motor control** - an adjustable **slider** (left/right or ``-``/``+``)
  whose RPM readout tracks the value.
* **Calibration** - a **progress bar** that redraws in place with a bare
  ``\\r`` (the classic serial pattern), doubling as a passthrough transparency
  canary -- if ``\\r`` is rewritten, it scrolls instead of redrawing.

Most screens speak ANSI by **absolute cursor addressing** (``ESC[row;colH``)
plus erase-line/erase-below, so their frames carry no ``\\r``/``\\n``;
Calibration is the deliberate exception (above).

Duck-types ``serial.Serial`` by subclassing :class:`termapy.demo.FakeSerial`,
which already provides the full port surface (control lines, ``in_waiting``,
locked ``read``/``write``). Only the I/O behavior is overridden here:
``write`` decodes keystrokes into navigation/redraws, and ``in_waiting`` ticks
the time-driven animations (status gauges, calibration progress).
"""

from __future__ import annotations

import math
import time

from termapy.demo import FakeSerial

# Menu label -> screen key (parallel tuples; index is the selection).
_MENU_ITEMS = ("Status", "Motor control", "Calibration")
_SCREENS = ("status", "motor", "calib")

# ANSI helpers (SGR codes have zero display width, so line padding is always
# computed on the plain text and color is wrapped around it).
_RESET = "\x1b[0m"
_CYAN = "\x1b[1;36m"
_DIM = "\x1b[2m"
_GREEN = "\x1b[32m"
_RED = "\x1b[31m"
_REVERSE = "\x1b[7m"


class FakeSerialVT100(FakeSerial):
    """Interactive VT100 demo device (reserved port ``DEMO_VT100``).

    Args:
        baudrate: Baud rate (cosmetic, like the base ``FakeSerial``).
    """

    _W = 44                    # inner box width (chars)
    _STATUS_DT = 0.25          # dashboard refresh interval (seconds)
    _CALIB_DT = 0.12           # calibration progress step interval
    _CALIB_STEP = 5            # percent added per calibration step

    def __init__(self, baudrate: int = 115200) -> None:
        super().__init__(baudrate=baudrate, port="DEMO_VT100")
        # Idle reads block briefly so the miniterm reader loop paces itself
        # instead of spinning when no frame is pending.
        self._timeout = 0.05
        self._started = False
        self._screen = "menu"
        self._sel = 0
        self._keybuf = bytearray()
        self._frame_n = 0           # animation counter (status gauges)
        self._t0 = time.monotonic()
        self._last_frame = 0.0
        self._motor = 40            # motor slider, percent
        self._calib = 0             # calibration progress, percent
        self._calib_drawn = False   # has the calib screen done its full draw?

    # -- serial.Serial overrides -------------------------------------------

    @property
    def in_waiting(self) -> int:
        """Bytes available, ticking the first frame and time-driven redraws.

        Generating frames here (rather than in ``read``) means the miniterm
        loop sees the full frame size via ``read(in_waiting or 1)`` and reads
        each frame in one call.
        """
        with self._lock:
            now = time.monotonic()
            if not self._started:
                self._started = True
                self._last_frame = now
                self._enqueue(self._render())
            elif self._tick(now):
                self._last_frame = now
                self._enqueue(self._render())
            return len(self._output_buf)

    def write(self, data: bytes) -> int:
        """Decode keystrokes into navigation and enqueue a redraw."""
        with self._lock:
            self._keybuf.extend(data)
            changed = False
            for key in self._consume_keys():
                changed |= self._apply(key)
            if changed:
                self._last_frame = time.monotonic()
                self._enqueue(self._render())
        return len(data)

    def flush(self) -> None:
        """No-op; nothing is buffered on the TX side of this fake."""

    # -- time-driven animation ---------------------------------------------

    def _tick(self, now: float) -> bool:
        """Advance the active screen's animation. Returns True to redraw."""
        dt = now - self._last_frame
        scr = self._screen
        if scr == "status" and dt >= self._STATUS_DT:
            self._frame_n += 1
            return True
        if scr == "calib" and dt >= self._CALIB_DT and self._calib < 100:
            self._calib = min(100, self._calib + self._CALIB_STEP)
            return True
        return False

    # -- input decoding ----------------------------------------------------

    def _consume_keys(self) -> list[str]:
        """Drain ``_keybuf`` into logical keys, leaving partial sequences.

        Returns:
            A list drawn from ``up``/``down``/``left``/``right``/``select``/
            ``back``. Unknown keys are ignored. An incomplete
            escape sequence at the end of the buffer is left for next time.
        """
        keys: list[str] = []
        buf = self._keybuf
        arrows = {ord("A"): "up", ord("B"): "down", ord("C"): "right", ord("D"): "left"}
        while buf:
            b0 = buf[0]
            if b0 == 0x1B:  # ESC
                if len(buf) == 1:
                    break  # maybe the start of a sequence; wait for more
                if buf[1] == ord("["):
                    if len(buf) < 3:
                        break  # wait for the final byte
                    final = buf[2]
                    del buf[:3]
                    if final in arrows:
                        keys.append(arrows[final])
                else:
                    del buf[:1]  # lone ESC -> back
                    keys.append("back")
                continue
            del buf[:1]
            if b0 in (0x0D, 0x0A):
                keys.append("select")
            elif b0 in (ord("k"), ord("w")):
                keys.append("up")
            elif b0 in (ord("j"), ord("s")):
                keys.append("down")
            elif b0 in (ord("h"), ord("-"), ord("_")):
                keys.append("left")
            elif b0 in (ord("l"), ord("+"), ord("=")):
                keys.append("right")
            elif b0 in (ord("q"), 0x08, 0x7F):  # q / Backspace / Delete
                keys.append("back")
        return keys

    def _apply(self, key: str) -> bool:
        """Apply one logical key. Returns True if the screen needs redrawing."""
        scr = self._screen
        if scr == "menu":
            if key == "up":
                self._sel = (self._sel - 1) % len(_MENU_ITEMS)
                return True
            if key == "down":
                self._sel = (self._sel + 1) % len(_MENU_ITEMS)
                return True
            if key == "select":
                self._open(_SCREENS[self._sel])
                return True
            return False
        if key == "back":
            self._screen = "menu"
            return True
        if scr == "motor":
            if key == "left":
                self._motor = max(0, self._motor - 5)
                return True
            if key == "right":
                self._motor = min(100, self._motor + 5)
                return True
            return False
        return False

    def _open(self, screen: str) -> None:
        """Switch to a sub-screen, resetting its per-screen state."""
        self._screen = screen
        self._frame_n = 0
        self._last_frame = time.monotonic()
        if screen == "motor":
            self._motor = 40
        elif screen == "calib":
            self._calib = 0
            self._calib_drawn = False

    # -- rendering ---------------------------------------------------------

    def _render(self) -> bytes:
        """Render the current screen.

        Most screens are absolutely positioned (``ESC[row;colH``). Calibration
        is the exception -- it redraws in place with a bare ``\\r`` (see
        ``_calib_frame``), so it doubles as a passthrough transparency canary.
        """
        if self._screen == "calib":
            return self._calib_frame()
        renderers = {
            "menu": self._menu_lines,
            "status": self._status_lines,
            "motor": self._motor_lines,
        }
        lines = renderers.get(self._screen, self._menu_lines)()
        parts = [f"\x1b[{i + 1};1H{line}\x1b[K" for i, line in enumerate(lines)]
        parts.append("\x1b[J")  # clear anything left below a shorter screen
        return "".join(parts).encode("utf-8")

    # -- shared layout helpers ---------------------------------------------

    def _box(self) -> str:
        return "+" + "-" * self._W + "+"

    def _row(self, plain: str) -> str:
        """Frame a plain content string inside the box, padded to width."""
        return "|" + plain.ljust(self._W)[: self._W] + "|"

    def _title(self, text: str) -> str:
        return "|" + _CYAN + text.center(self._W) + _RESET + "|"

    def _color_row(self, plain: str, colored: str) -> str:
        """Frame a row whose *colored* form has the same width as *plain*."""
        pad = " " * max(0, self._W - len(plain))
        return "|" + colored + pad + "|"

    def _bar(self, frac: float, width: int) -> tuple[str, str]:
        """Return (plain, colored) bar strings of *width* chars for *frac*."""
        frac = max(0.0, min(1.0, frac))
        fill = round(frac * width)
        plain = "#" * fill + "-" * (width - fill)
        color = _GREEN if frac < 0.6 else "\x1b[33m" if frac < 0.85 else _RED
        return plain, f"{color}{plain}{_RESET}"

    def _hint(self, text: str) -> str:
        return _DIM + "  " + text + _RESET

    # -- per-screen renderers ----------------------------------------------

    def _menu_lines(self) -> list[str]:
        lines = [self._box(), self._title(" Bassomatic v77  -  DEMO_VT100 "), self._row("")]
        for i, name in enumerate(_MENU_ITEMS):
            cell = f"  {'>' if i == self._sel else ' '} {name}".ljust(self._W)[: self._W]
            lines.append("|" + (_REVERSE + cell + _RESET if i == self._sel else cell) + "|")
        lines += [self._row(""), self._box(), self._hint("Up/Down move   Enter select   Ctrl-] quit")]
        return lines

    def _status_lines(self) -> list[str]:
        n = self._frame_n
        up = int(time.monotonic() - self._t0)
        temp = 62 + 4 * math.sin(n / 4)
        motor = 1500 + 200 * math.sin(n / 5)
        volts = 11.8 + 0.4 * math.sin(n / 7)
        led = "ON " if (n // 2) % 2 == 0 else "OFF"
        return [
            self._box(),
            self._title(f" Bassomatic v77 status  -  up {up // 60:02d}:{up % 60:02d} "),
            self._row(""),
            self._gauge("Temp", (temp - 50) / 40, f"{temp:5.1f} C"),
            self._gauge("Motor", motor / 3000, f"{int(motor):4d} rpm"),
            self._gauge("Volts", (volts - 9) / 6, f"{volts:5.1f} V"),
            self._row(""),
            self._row(f"  LED: {led}     GPS: FIX (9 sat)"),
            self._box(),
            self._hint("live - updating   q back   Ctrl-] quit"),
        ]

    def _gauge(self, label: str, frac: float, value: str) -> str:
        plain_bar, col_bar = self._bar(frac, 12)
        plain = f"  {label:<6} [{plain_bar}]  {value}"
        colored = f"  {label:<6} [{col_bar}]  {value}"
        return self._color_row(plain, colored)

    def _motor_lines(self) -> list[str]:
        pct = self._motor
        rpm = int(pct / 100 * 3000)
        width = 18
        pos = round(pct / 100 * (width - 1))
        track_plain = "-" * pos + "O" + "-" * (width - 1 - pos)
        track_col = f"{_GREEN}{'-' * pos}\x1b[1;37mO{_RESET}{_GREEN}{'-' * (width - 1 - pos)}{_RESET}"
        plain = f"  [{track_plain}]  {pct:3d}%  ->  {rpm:4d} rpm"
        colored = f"  [{track_col}]  {pct:3d}%  ->  {rpm:4d} rpm"
        return [
            self._box(),
            self._title(" Motor control "),
            self._row(""),
            self._color_row(plain, colored),
            self._row(""),
            self._box(),
            self._hint("Left/Right adjust   q back   Ctrl-] quit"),
        ]

    def _calib_frame(self) -> bytes:
        """Calibration redraws in place with a bare ``\\r`` -- the classic
        serial progress-bar pattern, unlike the other screens' absolute
        positioning.

        It doubles as a transparency canary: if the passthrough ever rewrites
        ``\\r`` (the EOL-transform bug), the bar scrolls instead of redrawing in
        place -- visible proof in any real terminal that bytes go through raw.
        """
        line = self._calib_progress_line()
        if self._calib_drawn:
            # In-place update: CR returns to column 1; overwrite the bar line.
            return ("\r" + line).encode("utf-8")
        self._calib_drawn = True
        head = f"{_CYAN}Calibration{_RESET}  -  bare-\\r redraw   (q back, Ctrl-] quit)"
        # Full draw: clear, header (CRLF), blank line, then the bar line LAST so
        # the cursor stays on it for the CR-based updates that follow.
        return f"\x1b[2J\x1b[H{head}\r\n\r\n{line}".encode("utf-8")

    def _calib_progress_line(self) -> str:
        pct = self._calib
        if pct >= 100:
            label = "Complete"
        elif pct < 30:
            label = "Zeroing sensors"
        elif pct < 70:
            label = "Measuring offsets"
        else:
            label = "Writing calibration"
        _, col_bar = self._bar(pct / 100, 30)
        return f"  [{col_bar}] {pct:3d}%  {label}\x1b[K"
