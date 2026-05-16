"""Built-in plugin: show termapy version and check PyPI for updates."""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins import CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _installed_version() -> str:
    """Return the installed termapy version, or 'unknown' for source checkouts.

    PackageNotFoundError fires when termapy is being run from a
    git clone without ``pip install .`` -- common during
    development.  That's the only exception this path can
    legitimately raise; anything else is a bug worth surfacing.
    """
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version("termapy")
    except PackageNotFoundError:
        return "unknown"


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    ver = _installed_version()
    ver_str = f"termapy v{ver}"
    ctx.io.result(ver_str)
    return CmdResult.ok(value=ver_str)


def _handler_latest(ctx: PluginContext, args: str) -> CmdResult:
    """Return just the latest termapy version on PyPI (bare data getter).

    Symmetric with ``/ver``: ``/ver`` reports the LOCAL installed
    version, ``/ver.latest`` reports the REMOTE PyPI version.  No
    comparison, no prose -- the value is meant for scripting
    (``$(LATEST) <- /ver.latest``).

    Bypasses the 7-day throttle used by the background banner --
    the user typed the command, they want a fresh answer.  Network
    failure returns an error rather than silently succeeding.
    """
    from termapy.update_check import check_now

    installed = _installed_version()
    latest, _ = check_now(installed if installed != "unknown" else "0")
    if latest is None:
        return CmdResult.fail(msg="Could not reach PyPI.")
    ctx.io.result(latest)
    return CmdResult.ok(value=latest)


def _handler_info(ctx: PluginContext, args: str) -> CmdResult:
    """Verbose human-readable installed-vs-PyPI comparison.

    Matches the ``/X.info`` convention used by /cfg.info,
    /port.info, /mcp.info, /profile.info -- the "show me the rich
    state of this thing" verb.  Use ``/ver.latest`` for just the
    bare PyPI version, or ``/ver`` for just the installed version.

    Bypasses the 7-day throttle used by the background banner --
    the user typed the command, they want a fresh answer.
    """
    from termapy.update_check import check_now

    installed = _installed_version()
    if installed == "unknown":
        return CmdResult.fail(
            msg="Cannot check for updates: termapy is not installed "
            "(running from a source checkout?)."
        )

    latest, outdated = check_now(installed)
    if latest is None:
        return CmdResult.fail(msg="Could not reach PyPI.")

    if outdated:
        line = (
            f"installed v{installed}  ->  latest v{latest}  "
            f"(update available)"
        )
    else:
        line = f"installed v{installed}  (up to date; latest v{latest})"
    ctx.io.result(line)
    return CmdResult.ok(value=latest)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="ver",
    help="Show termapy version.",
    handler=_handler,
    sub_commands={
        "latest": Command(
            help="Show the latest termapy version on PyPI (bare value).",
            long_help=(
                "Fetches the latest termapy version from PyPI and "
                "prints just the version string -- the data-getter "
                "symmetric to /ver (which reports installed).  Set as "
                "CmdResult.value so scripts can capture it with "
                "$(LATEST) <- /ver.latest.\n"
                "\n"
                "Bypasses the 7-day throttle used by the background "
                "banner -- this always hits PyPI fresh.  Network "
                "failure returns an error.  Times out after 2 seconds.\n"
                "\n"
                "See /ver.info for a verbose installed-vs-PyPI compare."
            ),
            handler=_handler_latest,
        ),
        "info": Command(
            help="Show installed-vs-PyPI version comparison.",
            long_help=(
                "Fetches the latest termapy version from PyPI and "
                "compares it to the installed version.  Prints a "
                "human-readable line: either 'up to date' or "
                "'update available' along with both version numbers.\n"
                "\n"
                "Bypasses the 7-day throttle used by the background "
                "banner -- this always hits PyPI fresh.  Network "
                "failure returns an error.  Times out after 2 seconds.\n"
                "\n"
                "Sets CmdResult.value to the latest PyPI version.  "
                "See /ver.latest for just the bare PyPI value, or "
                "/ver for just the installed version."
            ),
            handler=_handler_info,
        ),
    },
)
