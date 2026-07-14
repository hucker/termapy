"""Config schema versioning and migration chain.

Each config has a "config_version" integer. Migration functions transform
configs from one version to the next. On load, migrate_config() runs all
needed migrations sequentially to bring the config up to date.

To add a migration:
    1. Bump CURRENT_CONFIG_VERSION
    2. Write a function: def _migrate_vN_to_vN1(cfg): ... return cfg
    3. Add it to MIGRATIONS: {N: _migrate_vN_to_vN1}
"""

import re
from typing import Callable

CURRENT_CONFIG_VERSION = 27

# Keys that used to be valid config fields but have been removed or
# renamed by a migration.  Maps deprecated key -> a short message
# explaining what happened.  ``validate_config`` uses this to give a
# helpful warning ("deprecated: X was renamed to Y in v6") instead
# of the generic "unknown key (typo?)" when a user's config carries
# a stale field.
#
# In normal flow a deprecated key is cleaned up by the migration
# chain on first load.  This dict catches the edge cases: hand-edited
# configs that re-add an old key, configs with a version-mismatch
# that somehow bypassed migrations, or users inspecting a very old
# file with ``--check``.
#
# Keep sorted by the migration that retired the key so the schema
# history reads chronologically.
DEPRECATED_CFG: dict[str, str] = {
    # v1 -> v2: rename
    "add_date_to_cmd": "renamed to show_timestamps in v2",
    # v3 -> v4: renames
    "baudrate": "renamed to baud_rate in v4",
    "bytesize": "renamed to byte_size in v4",
    "stopbits": "renamed to stop_bits in v4",
    "autoconnect": "renamed to auto_connect in v4",
    "autoreconnect": "renamed to auto_reconnect in v4",
    "autoconnect_cmd": "renamed to auto_connect_cmd in v4",
    "pick": "renamed to pick_port in v4",
    # v3 -> v4: removal
    "command_history_items": "removed in v4 (no replacement)",
    # v4 -> v5: removal
    "pick_port": "removed in v5 (use $(env.NAME) config expansion)",
    # v5 -> v6: renames
    "echo_cmd": "renamed to echo_input in v6",
    "echo_cmd_fmt": "renamed to echo_input_fmt in v6",
    "auto_connect_cmd": "renamed to on_connect_cmd in v6",
    "inter_cmd_delay_ms": "renamed to cmd_delay_ms in v6",
    "show_eol": "renamed to show_line_endings in v6",
    "exception_traceback": "renamed to show_traceback in v6",
    "app_border_color": "renamed to border_color in v6",
    "repl_prefix": "renamed to cmd_prefix in v6",
    "read_only": "renamed to config_read_only in v6",
    # v7 -> v8: removal
    "cap_endian": "removed in v8 (endianness now lives in the format spec)",
    # v13 -> v14: rename (IntelliSense is a Microsoft trademark)
    "cli_intellisense": "renamed to cli_completion in v14",
    # v16 -> v17: /include retired in favor of /profile.load cmd=
    "auto_include_on_connect": (
        "removed in v17 (/include retired); use mcp_on_connect_cmd="
        "\"/profile.load cmd=<command>\" instead"
    ),
    "device_json_cmd": (
        "removed in v17 (/include retired); use /profile.load cmd=<command> "
        "directly, or set mcp_on_connect_cmd to auto-fetch on connect"
    ),
    # v18 -> v19: security policy must live above the cfg layer.
    "os_cmd_enabled": (
        "retired in v19; /os is now gated by the TERMAPY_OS_CMD_ENABLED "
        "env var.  Set it to 1 in your shell to enable shell escapes."
    ),
    # v24 -> v25: removal
    "cli_echo_input": (
        "removed in v25 (was never wired to a consumer); use echo_input "
        "for device-command echo, and /term.echo_repl for REPL-command echo"
    ),
}

# Migration functions: {from_version: callable(cfg) -> cfg}
MIGRATIONS: dict[int, Callable] = {}


def _migrate_v1_to_v2(cfg: dict) -> dict:
    """Rename add_date_to_cmd -> show_timestamps."""
    if "add_date_to_cmd" in cfg:
        cfg["show_timestamps"] = cfg.pop("add_date_to_cmd")
    return cfg


def _migrate_v2_to_v3(cfg: dict) -> dict:
    """Add command_history_items with default of 30."""
    if "command_history_items" not in cfg:
        cfg["command_history_items"] = 30
    return cfg


_KEY_RENAMES_V4 = {
    "baudrate": "baud_rate",
    "bytesize": "byte_size",
    "stopbits": "stop_bits",
    "autoconnect": "auto_connect",
    "autoreconnect": "auto_reconnect",
    "autoconnect_cmd": "auto_connect_cmd",
}


def _migrate_v3_to_v4(cfg: dict) -> dict:
    """Remove command_history_items, add read_only, rename keys, prefix ! -> /."""
    cfg.pop("command_history_items", None)
    cfg.setdefault("read_only", False)
    if cfg.get("repl_prefix") == "!":
        cfg["repl_prefix"] = "/"
    for old, new in _KEY_RENAMES_V4.items():
        if old in cfg:
            cfg[new] = cfg.pop(old)
    if "pick" in cfg:
        cfg["pick_port"] = cfg.pop("pick")
    return cfg


def _migrate_v4_to_v5(cfg: dict) -> dict:
    """Remove pick_port (superseded by $(env.NAME) config expansion)."""
    cfg.pop("pick_port", None)
    return cfg


_KEY_RENAMES_V6 = {
    "echo_cmd": "echo_input",
    "echo_cmd_fmt": "echo_input_fmt",
    "auto_connect_cmd": "on_connect_cmd",
    "inter_cmd_delay_ms": "cmd_delay_ms",
    "show_eol": "show_line_endings",
    "exception_traceback": "show_traceback",
    "app_border_color": "border_color",
    "repl_prefix": "cmd_prefix",
    "read_only": "config_read_only",
}


def _migrate_v5_to_v6(cfg: dict) -> dict:
    """Rename config fields for clarity and consistency."""
    for old, new in _KEY_RENAMES_V6.items():
        if old in cfg:
            cfg[new] = cfg.pop(old)
    return cfg


MIGRATIONS[1] = _migrate_v1_to_v2
MIGRATIONS[2] = _migrate_v2_to_v3
MIGRATIONS[3] = _migrate_v3_to_v4
MIGRATIONS[4] = _migrate_v4_to_v5
MIGRATIONS[5] = _migrate_v5_to_v6


def _migrate_v6_to_v7(cfg: dict) -> dict:
    """Add send_bare_enter option (default off)."""
    cfg.setdefault("send_bare_enter", False)
    return cfg


MIGRATIONS[6] = _migrate_v6_to_v7


def _migrate_v7_to_v8(cfg: dict) -> dict:
    """Remove cap_endian (endianness now in format spec byte order)."""
    cfg.pop("cap_endian", None)
    return cfg


MIGRATIONS[7] = _migrate_v7_to_v8


def _migrate_v8_to_v9(cfg: dict) -> dict:
    """Add cli_prompt and cli_echo_input options."""
    cfg.setdefault("cli_prompt", "$(CFG)> ")
    cfg.setdefault("cli_echo_input", False)
    return cfg


MIGRATIONS[8] = _migrate_v8_to_v9


def _migrate_v9_to_v10(cfg: dict) -> dict:
    """Add default_ui option (default 'tui')."""
    cfg.setdefault("default_ui", "tui")
    return cfg


MIGRATIONS[9] = _migrate_v9_to_v10


def _migrate_v10_to_v11(cfg: dict) -> dict:
    """Add file_xfer_root option (default empty string)."""
    cfg.setdefault("file_xfer_root", "")
    return cfg


MIGRATIONS[10] = _migrate_v10_to_v11


def _migrate_v11_to_v12(cfg: dict) -> dict:
    """Add custom_baud option (default False)."""
    cfg.setdefault("custom_baud", False)
    return cfg


MIGRATIONS[11] = _migrate_v11_to_v12


def _migrate_v12_to_v13(cfg: dict) -> dict:
    """Add title-bar button visibility toggles (default all True)."""
    cfg.setdefault("cfg_enabled", True)
    cfg.setdefault("run_enabled", True)
    cfg.setdefault("proto_enabled", True)
    return cfg


MIGRATIONS[12] = _migrate_v12_to_v13


def _migrate_v13_to_v14(cfg: dict) -> dict:
    """Rename cli_intellisense -> cli_completion.

    IntelliSense is a Microsoft trademark; the feature is more
    accurately named ``cli_completion`` anyway (tab completion +
    auto-suggest + help toolbar).
    """
    if "cli_intellisense" in cfg:
        cfg["cli_completion"] = cfg.pop("cli_intellisense")
    return cfg


MIGRATIONS[13] = _migrate_v13_to_v14


def _migrate_v14_to_v15(cfg: dict) -> dict:
    """Add per-mode on_connect_cmd keys; preserve existing interactive behavior.

    Pre-v15, ``on_connect_cmd`` fired only in TUI/CLI -- MCP had a
    latent bug that silently skipped it.  v15 fixes that bug AND
    introduces ``tui_/cli_/mcp_on_connect_cmd`` for per-mode setup.

    A naive add-with-empty-defaults migration would silently start
    firing the universal command in MCP for every existing cfg --
    running interactive-only commands the user never authored for
    the LLM context.

    Instead: move the existing universal value into the per-mode
    interactive keys (preserving exact pre-v15 behavior in TUI/CLI)
    and clear the universal so MCP gets a clean slate the user fills
    in deliberately via mcp_on_connect_cmd.
    """
    existing = cfg.get("on_connect_cmd", "")
    cfg.setdefault("tui_on_connect_cmd", existing)
    cfg.setdefault("cli_on_connect_cmd", existing)
    cfg.setdefault("mcp_on_connect_cmd", "")
    cfg["on_connect_cmd"] = ""
    return cfg


MIGRATIONS[14] = _migrate_v14_to_v15


def _migrate_v15_to_v16(cfg: dict) -> dict:
    """Add request_err_pattern cfg key (default matches ERROR/ERR/FAULT).

    Used by request_mode (``/term.request on``) to detect device-side
    errors in the response text.  When the response matches the regex,
    the envelope reports success=false with the text as the error.
    Empty string disables error detection.
    """
    cfg.setdefault("request_err_pattern", r"(?i)^(ERROR|ERR|FAULT)\b")
    return cfg


MIGRATIONS[15] = _migrate_v15_to_v16


def _migrate_v16_to_v17(cfg: dict) -> dict:
    """Retire /include in favor of /profile.load cmd=<command>.

    Two cfg keys are removed:

      - ``device_json_cmd``: the command sent by /include to fetch the
        device's JSON help/profile dump.
      - ``auto_include_on_connect``: bool that auto-ran /include after a
        successful connect when device_json_cmd was set.

    Auto-fetch behavior is preserved when both keys were set in the
    pre-migration cfg: the equivalent on-connect command is appended
    to ``mcp_on_connect_cmd`` so the next MCP-mode connect performs
    the same dump-and-install flow via the new path.  Users who only
    set one of the keys (e.g. device_json_cmd without auto-include)
    get the keys removed without a rewrite -- they were manual
    /include callers and migrate to manual /profile.load callers.
    """
    auto = cfg.pop("auto_include_on_connect", None)
    cmd = cfg.pop("device_json_cmd", "")
    if auto and isinstance(cmd, str) and cmd.strip():
        new_step = f"/profile.load cmd={cmd.strip()}"
        existing = cfg.get("mcp_on_connect_cmd", "") or ""
        # Avoid double-appending if the user already migrated by hand.
        if new_step not in existing:
            cfg["mcp_on_connect_cmd"] = (
                f"{existing}\n{new_step}" if existing.strip() else new_step
            )
    return cfg


MIGRATIONS[16] = _migrate_v16_to_v17


def _migrate_v17_to_v18(cfg: dict) -> dict:
    """Profile.transport block retired; wire-format settings live in cfg.

    Adds ``protocol: "text"`` if missing so existing cfgs surface the
    new field on inspect/dump.  ``ndjson_field_routing`` lands via the
    normal defaults-backfill in ``load_config`` -- no need to write a
    nested dict here.

    Existing v2 profiles in the user's filesystem may still carry a
    ``transport`` block; ``validate_profile`` now rejects them with a
    clear error pointing at the cfg-based replacement.
    """
    cfg.setdefault("protocol", "text")
    return cfg


MIGRATIONS[17] = _migrate_v17_to_v18


def _migrate_v18_to_v19(cfg: dict) -> dict:
    """Retire ``os_cmd_enabled`` cfg key; /os moves to env-var-only gating.

    Security policy must live above the cfg layer.  A cfg cannot
    grant itself permission to run shell commands -- doing so means
    a hostile cfg is also its own audit, which is incoherent.  /os
    is now gated by ``TERMAPY_OS_CMD_ENABLED`` in the process
    environment.

    The migration pops the key.  If the prior value was True, a
    one-shot warning lands in ``_migration_warnings`` so the user
    knows their /os enablement didn't survive the upgrade -- they
    need to set the env var if they want it back.  False or absent
    values strip silently (no behavior change).
    """
    old = cfg.pop("os_cmd_enabled", None)
    if old is True:
        cfg.setdefault("_migration_warnings", []).append(
            "os_cmd_enabled was true in your cfg but has been retired "
            "in v19.  /os is now gated by the TERMAPY_OS_CMD_ENABLED "
            "env var -- set it to 1 in your shell to keep /os enabled."
        )
    return cfg


MIGRATIONS[18] = _migrate_v18_to_v19


# Conservative regex: matches ``/color`` only when it sits at the
# *start of a command* in the chain -- meaning the first non-whitespace
# token after the start of the string, a newline, or a semicolon.
# Leading whitespace between the boundary and ``/color`` is allowed.
# Plain spaces inside an argument string (``/print "the /color was
# teal"``) deliberately do not count as a boundary, so embedded
# literal ``/color`` text survives unchanged.  Trailing word-boundary
# guard prevents ``/colorful`` from being rewritten.
_COLOR_VERB_RE = re.compile(
    r"(?P<lead>^|[\n;])(?P<ws>[ \t]*)/color(?P<tail>\b)",
)


def _rewrite_color_in_chain(text: str) -> str:
    """Replace ``/color`` with ``/term.color`` at command boundaries.

    The cfg ``*_on_connect_cmd`` fields are ``\\n``-separated chains of
    REPL commands.  We rewrite ``/color`` only where it appears as a
    command verb -- not inside argument text like ``/print "the /color
    was teal"``.  Conservative on purpose; round-trip-safe to call
    repeatedly.
    """
    if not isinstance(text, str) or "/color" not in text:
        return text
    return _COLOR_VERB_RE.sub(
        r"\g<lead>\g<ws>/term.color\g<tail>", text,
    )


def _migrate_v19_to_v20(cfg: dict) -> dict:
    """Rewrite ``/color`` to ``/term.color`` in on-connect command chains.

    The CLI-only ``/color`` toggle was renamed to ``/term.color`` to
    sit alongside its display-toggle siblings (``/term.echo``,
    ``/term.line_no``, ``/term.timestamps``, ...).  A hidden legacy
    alias keeps ``/color`` working at runtime, so user .run scripts,
    shell aliases, and muscle memory continue to work.

    This migration is cosmetic: it walks the four ``*_on_connect_cmd``
    cfg fields and rewrites any ``/color`` command-verb occurrences in
    place so ``/cfg.dump`` shows the canonical name and the user
    learns the new vocabulary.  Argument text like ``/print "/color
    was teal"`` is not rewritten (see ``_COLOR_VERB_RE``).
    """
    for key in (
        "on_connect_cmd",
        "tui_on_connect_cmd",
        "cli_on_connect_cmd",
        "mcp_on_connect_cmd",
    ):
        if key in cfg:
            cfg[key] = _rewrite_color_in_chain(cfg[key])
    return cfg


MIGRATIONS[19] = _migrate_v19_to_v20


def _migrate_v20_to_v21(cfg: dict) -> dict:
    """Add ``record_enabled`` toggle for the Record button (default True).

    The Record button sits next to the REPL prompt and toggles
    ``/run.record``.  Older configs default to visible so the
    feature is discoverable; users who want it hidden flip the
    key to ``false``.
    """
    cfg.setdefault("record_enabled", True)
    return cfg


MIGRATIONS[20] = _migrate_v20_to_v21


# Keys that move from the flat top-level into cfg["serial"] in v22.
# Module-level constant so the migration step, the deprecated-key
# lookup, and any future audit code share one source of truth.
_V22_SERIAL_KEYS = (
    "port", "baud_rate", "custom_baud", "byte_size",
    "parity", "stop_bits", "flow_control",
)


def _migrate_v21_to_v22(cfg: dict) -> dict:
    """Nest pyserial config keys under ``cfg['serial']``.

    Moves ``port``, ``baud_rate``, ``custom_baud``, ``byte_size``,
    ``parity``, ``stop_bits``, ``flow_control`` from the top level
    into a ``serial`` sub-dict so the pyserial constructor args
    read as a group.  Other serial-domain keys (encoding,
    line_ending, cmd_delay_ms, protocol, etc.) stay flat for now;
    grouping them is a separate future decision.
    """
    serial = cfg.setdefault("serial", {})
    for key in _V22_SERIAL_KEYS:
        if key in cfg:
            # setdefault preserves any value already in serial[key]
            # so a partially-nested cfg (shouldn't happen in normal
            # flow but cheap to be defensive) doesn't get clobbered.
            serial.setdefault(key, cfg.pop(key))
    return cfg


MIGRATIONS[21] = _migrate_v21_to_v22


# Conservative regex: matches ``/ver`` only as a command verb -- first
# non-whitespace token after the start of the string, a newline, or a
# semicolon.  The trailing ``\b`` rewrites ``/ver`` and ``/ver.latest`` /
# ``/ver.info`` (boundary before the dot) but NOT ``/verbose`` or
# ``/version`` (no boundary mid-word), and leaves literal ``/ver`` inside
# argument text untouched.
_VER_VERB_RE = re.compile(
    r"(?P<lead>^|[\n;])(?P<ws>[ \t]*)/ver(?P<tail>\b)",
)


def _rewrite_ver_in_chain(text: str) -> str:
    """Replace ``/ver`` with ``/app.ver`` at command boundaries.

    Rewrites the verb only (``/ver``, ``/ver.latest``, ``/ver.info``),
    not literal ``/ver`` inside argument text.  Round-trip-safe.
    """
    if not isinstance(text, str) or "/ver" not in text:
        return text
    return _VER_VERB_RE.sub(r"\g<lead>\g<ws>/app.ver\g<tail>", text)


def _migrate_v22_to_v23(cfg: dict) -> dict:
    """Rewrite ``/ver`` -> ``/app.ver`` in on-connect command chains.

    ``/ver`` moved under ``/app`` (``/app.ver``, ``/app.ver.latest``,
    ``/app.ver.info``) to group the app-info commands.  A hidden legacy
    alias keeps ``/ver`` working at runtime, so this migration is
    cosmetic: it updates the four ``*_on_connect_cmd`` chains so
    ``/cfg.dump`` shows the canonical name and the user learns it.
    """
    for key in (
        "on_connect_cmd",
        "tui_on_connect_cmd",
        "cli_on_connect_cmd",
        "mcp_on_connect_cmd",
    ):
        if key in cfg:
            cfg[key] = _rewrite_ver_in_chain(cfg[key])
    return cfg


MIGRATIONS[22] = _migrate_v22_to_v23


# Ambient wall-clock placeholders retired from the {} scripting-template
# system in v24; they moved to the $() variable system (which gained a :fmt
# suffix).  {seqN}/{starttime}/{elapsed} stay as per-run stamps and are NOT
# rewritten.  Plain token swaps -- the placeholders are unambiguous.
_RETIRED_PLACEHOLDERS = {
    "{clock}": "$(TIME)",
    "{datetime}": "$(DATETIME:%Y%m%d_%H%M%S)",
}


def _rewrite_placeholders_in_chain(text: str) -> str:
    """Rewrite retired {clock}/{datetime} placeholders to their $() form.

    Applies to the on-connect command chains, whose lines used to run
    through the scripting-template expander.  Round-trip-safe.
    """
    if not isinstance(text, str):
        return text
    for old, new in _RETIRED_PLACEHOLDERS.items():
        if old in text:
            text = text.replace(old, new)
    return text


def _migrate_v23_to_v24(cfg: dict) -> dict:
    """Rewrite {clock}/{datetime} to $() form in on-connect command chains.

    The ambient wall-clock template placeholders ``{clock}`` and
    ``{datetime}`` were retired from the scripting-template system; they now
    live in the ``$()`` variable system as ``$(TIME)`` and
    ``$(DATETIME:%Y%m%d_%H%M%S)`` (the variable system gained a ``:fmt``
    suffix so it can emit a filename-safe, colon-free stamp).  A runtime
    directive keeps old scripts working; this migration makes the four
    ``*_on_connect_cmd`` chains permanent so ``/cfg.dump`` shows the
    canonical form.  ``proto_results_template`` is intentionally left alone
    -- its ``{datetime}`` is a separate Python ``str.format`` placeholder.
    """
    for key in (
        "on_connect_cmd",
        "tui_on_connect_cmd",
        "cli_on_connect_cmd",
        "mcp_on_connect_cmd",
    ):
        if key in cfg:
            cfg[key] = _rewrite_placeholders_in_chain(cfg[key])
    return cfg


MIGRATIONS[23] = _migrate_v23_to_v24


def _migrate_v24_to_v25(cfg: dict) -> dict:
    """Retire the unused ``cli_echo_input`` config key.

    ``echo_input`` now scopes to DEVICE-command echo (bare lines +
    ``/term.send``) only; REPL/slash-command echo moved to a session-only
    flag (``echo_repl``, toggled by ``/term.echo_repl``) with no cfg key.
    ``cli_echo_input`` was added in v9 but never wired to a consumer, so it
    is dropped.
    """
    cfg.pop("cli_echo_input", None)
    return cfg


MIGRATIONS[24] = _migrate_v24_to_v25


def _migrate_v25_to_v26(cfg: dict) -> dict:
    """Add ``rx_newline`` (receive-newline mode), defaulting to ``auto``.

    Splits newline handling into a transmit side (existing ``line_ending``)
    and a receive side: how incoming device output is split into lines.
    ``auto`` treats CR, LF, and CRLF all as line terminators (TeraTerm's
    Receive=AUTO) and works for any device.
    """
    cfg.setdefault("rx_newline", "auto")
    return cfg


MIGRATIONS[25] = _migrate_v25_to_v26


def _migrate_v26_to_v27(cfg: dict) -> dict:
    """Add ``strip_device_echo`` (drop half-duplex echo), defaulting off.

    Opt-in per device: when on, a request_mode response drops a leading
    line that matches the sent command (a half-duplex device echoing it
    back).
    """
    cfg.setdefault("strip_device_echo", False)
    return cfg


MIGRATIONS[26] = _migrate_v26_to_v27


def migrate_config(cfg: dict) -> dict:
    """Run config through the migration chain to bring it up to date.

    Applies migration functions sequentially from the config's current
    version to CURRENT_CONFIG_VERSION. Versions without a migration
    function are skipped (version number still advances).

    Each step that runs an actual migration function appends a
    one-line description (from the function's docstring summary)
    to ``cfg["_migration_steps"]`` so callers can display a per-step
    breakdown -- a multi-version jump only happens once per cfg, so
    a few extra status lines is more reassuring than a single
    summary like "v17 -> v21".

    Args:
        cfg: Config dict to migrate (modified in place).

    Returns:
        The migrated config dict with config_version set to current.
    """
    v = cfg.get("config_version", 0)
    steps: list[str] = []
    while v < CURRENT_CONFIG_VERSION:
        if v in MIGRATIONS:
            fn = MIGRATIONS[v]
            cfg = fn(cfg)
            # First sentence of the function's docstring as the
            # one-line description.  Trailing period stripped so
            # we can compose ``v{a} -> v{b}: {desc}`` cleanly.
            doc = (fn.__doc__ or "").strip()
            summary = doc.splitlines()[0].strip().rstrip(".") if doc else ""
            label = (
                f"v{v} -> v{v + 1}: {summary}" if summary
                else f"v{v} -> v{v + 1}"
            )
            steps.append(label)
        v += 1
    cfg["config_version"] = CURRENT_CONFIG_VERSION
    if steps:
        cfg.setdefault("_migration_steps", []).extend(steps)
    return cfg
