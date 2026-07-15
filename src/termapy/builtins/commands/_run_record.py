"""Private handlers for the /run.record sub-command.

Filename is underscore-prefixed so the plugin loader skips this
module -- the actual sub-command is mounted in ``run.py`` as part
of ``/run``'s ``sub_commands`` dict, importing ``_handler`` and
``_LONG_HELP`` from here.  Splitting keeps the recorder's
substantive logic (observer registration, file lifecycle, state
invariants) testable in isolation while letting the ``Command``
declaration sit naturally next to the other run subs and properly
populate ``/help run``'s subcommand listing.

The recorder subscribes to ReplEngine's post-dispatch observer
(``ctx.internal.add_post_dispatch_observer``).  Every successful
dispatch -- REPL command or device command -- is appended to the
target file as raw text.  Failed dispatches and ``/run.record``
itself are skipped.  The file is opened with ``mode="x"`` (exclusive
create; refuses if the file exists) and ``.flush()`` runs after
every write, so a crash mid-recording leaves a partial but usable
file.

The loop from "I figured this out at the prompt" to "this is a
reusable .run script" reduces to:

    /run.record my_script
    /port.connect
    AT+VER
    /cap.text out.txt timeout=1s
    /run.record       # bare = stop

The recorded file ends up in ``<cfg_dir>/run/my_script.run`` and
plays back via ``/run my_script``.  Add a ``# Docstring`` at the
top to make it self-describing (see ``termapy.run_docstring``).

Design notes:

- Single-recording invariant.  Starting a second recording while
  one is active fails with a clear message; the user must stop
  first.
- ``/run.record`` lines never appear in the output -- the observer
  filters them out.
- Module-level state.  A handler module is the natural home for
  "is anything recording right now?" since a single REPL has at
  most one active recorder.  The TUI Record button asks via
  ``ctx.internal.is_recording()`` which forwards to ``is_active()``
  below.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, TextIO

from termapy.plugins import CmdResult, UsageError

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


@dataclass
class _Active:
    """In-flight recording state.  ``_active`` holds None or one of these."""
    path: Path
    file: TextIO
    line_count: int
    observer: Callable[[str, CmdResult], None]


_active: _Active | None = None


def is_active() -> bool:
    """Return True iff a recording is currently in flight.

    Called from ``TerminalHost._is_recording`` (wired into the
    internal handle as ``ctx.internal.is_recording``) so the TUI
    Record button can read state without importing this module
    statically.
    """
    return _active is not None


def _start(ctx: PluginContext, raw_name: str) -> CmdResult:
    """Open the target file and register the post-dispatch observer."""
    global _active

    if _active is not None:
        return CmdResult.fail(
            msg=f"Already recording to {_active.path.name}; "
            f"stop with /run.record first.",
        )

    if (
        ctx.internal.add_post_dispatch_observer is None
        or ctx.internal.remove_post_dispatch_observer is None
    ):
        # Defensive: hosts that don't wire the observer pair can't
        # record.  TerminalHost wires both, so the only way to hit
        # this is a misconfigured embed.
        return CmdResult.fail(
            msg="This host does not support /run.record "
            "(post-dispatch observer not wired).",
        )

    # Filename hygiene.  Auto-append .run if missing; refuse
    # other suffixes -- a recorded .txt would be confusing.
    name = raw_name.strip()
    if not name:
        raise UsageError()
    if "/" in name or "\\" in name:
        return CmdResult.fail(
            msg="Recording target must be a bare filename, not a path"
        )
    if name.endswith(".run"):
        target = name
    elif "." in name:
        return CmdResult.fail(
            msg=f"Recording target must be a .run file: got {name!r}",
        )
    else:
        target = name + ".run"

    scripts_dir = ctx.fs.scripts_dir
    if not scripts_dir.is_dir():
        scripts_dir.mkdir(parents=True, exist_ok=True)
    path = scripts_dir / target

    # ``mode="x"`` is exclusive-create: opens for writing only if
    # the file does not yet exist.  Matches /cap.text's no-clobber
    # policy and gives the user a clear "delete first or pick a new
    # name" failure mode instead of silently overwriting.
    try:
        fh = path.open("x", encoding="utf-8")
    except FileExistsError:
        return CmdResult.fail(
            msg=f"File exists: {target}.  Delete it or pick a new name.",
        )
    except OSError as e:
        return CmdResult.fail(msg=f"Cannot open {target}: {e}")

    # Observer closure.  Captures the module-level _active by name
    # so a stop call (which clears _active to None) is observed
    # correctly on the next dispatch.
    def _observe(line: str, result: CmdResult) -> None:
        if _active is None:  # paranoia: stop raced with dispatch
            return
        stripped = line.strip()
        # Skip /run.record itself in every form: bare, with args,
        # leading-space variants.  Using the prefix lets us catch
        # both "run.record" and "run.record foo" without listing
        # every form.
        if stripped == "run.record" or stripped.startswith("run.record "):
            return
        if not result.success:
            return
        _active.file.write(line + "\n")
        _active.file.flush()
        _active.line_count += 1

    token = ctx.internal.add_post_dispatch_observer(_observe)
    _active = _Active(path=path, file=fh, line_count=0, observer=token)
    ctx.io.result(f"Recording to {path}")
    return CmdResult.ok(value=path)


def _stop(ctx: PluginContext) -> CmdResult:
    """Close the file and deregister the observer."""
    global _active

    if _active is None:
        return CmdResult.fail(msg="Not recording.")

    path = _active.path
    count = _active.line_count
    try:
        if ctx.internal.remove_post_dispatch_observer is not None:
            ctx.internal.remove_post_dispatch_observer(_active.observer)
        _active.file.close()
    finally:
        _active = None

    word = "command" if count == 1 else "commands"
    ctx.io.result(f"Recorded {count} {word} to {path}")
    return CmdResult.ok(value=path)


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Toggle recording.  Bare /run.record stops; with arg starts."""
    if args.strip():
        return _start(ctx, args)
    return _stop(ctx)


# ── Public surface consumed by run.py's sub_commands ─────────────────────────
# This file is underscore-prefixed; the plugin loader skips it.
# Run's COMMAND lives in run.py and mounts ``_handler`` / ``_LONG_HELP``
# under the "record" sub_command key.

_LONG_HELP = """\
Record successfully-dispatched REPL and device commands to a
.run file in the per-config run/ directory.  /run.record itself
and failed dispatches are skipped, so the resulting file plays
back cleanly.

Usage:
  /run.record <filename>    Start recording (auto-adds .run).
  /run.record               Stop recording.

The file is opened with exclusive-create mode and flushed after
every write, so:

  - An existing file is refused with a clear message; the user
    explicitly deletes or renames before re-recording.
  - A crash mid-recording leaves a partial but usable file.
  - Starting a second recording while one is active is an error.

Suggested workflow: record, then add a ``#`` docstring at the
top of the file describing what the script does -- /run.list
and /run.help will pick it up.

TUI: the Record button next to the REPL prompt does the same
toggle, prompting for a filename via a modal on start.  Hide
the button with ``record_enabled: false`` in the config."""


__all__ = ["_handler", "_LONG_HELP", "is_active"]
