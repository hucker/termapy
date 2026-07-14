"""Update tagged config examples in help docs to match DEFAULT_CFG.

Finds <!-- validate-config-keys --> tags in markdown files, parses the
next JSON code block, and rewrites it with all current config keys in
DEFAULT_CFG order. Preserves non-default values from the original example
(e.g. realistic port names, custom buttons).

Usage:
    python scripts/update_doc_configs.py

Called automatically by release_prep.py. Can also be run standalone
after adding a new config key.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running as `python scripts/update_doc_configs.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

CONFIG_TAG = "<!-- validate-config-keys -->"
REPO_ROOT = Path(__file__).resolve().parent.parent
HELP_DIR = REPO_ROOT / "src" / "termapy" / "help"


def _find_tagged_blocks() -> list[tuple[Path, int]]:
    """Find (file_path, tag_line_number) for each tagged block."""
    md_files = list(HELP_DIR.glob("*.md")) + [REPO_ROOT / "README.md"]
    results = []
    for md_path in sorted(md_files):
        lines = md_path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if CONFIG_TAG in line:
                results.append((md_path, i))
    return results


def _extract_json(lines: list[str], tag_line: int) -> tuple[dict, int, int] | None:
    """Extract and parse the JSON block after tag_line.

    Returns (parsed_dict, start_line, end_line) or None.
    """
    start = None
    end = None
    for i in range(tag_line + 1, len(lines)):
        if lines[i].strip().startswith("```json"):
            start = i + 1
        elif start is not None and lines[i].strip() == "```":
            end = i
            break
    if start is None or end is None:
        return None
    json_text = "\n".join(lines[start:end])
    return json.loads(json_text), start, end


def update_doc_configs() -> list[str]:
    """Update all tagged config examples. Returns list of updated file descriptions."""
    from termapy.defaults import DEFAULT_CFG
    from termapy.migration import CURRENT_CONFIG_VERSION

    expected_keys = set(DEFAULT_CFG.keys())
    updated = []

    for file_path, tag_line in _find_tagged_blocks():
        text = file_path.read_text(encoding="utf-8")
        lines = text.splitlines()

        result = _extract_json(lines, tag_line)
        if result is None:
            print(f"  WARN: {file_path.name}:{tag_line + 1} - no JSON block found after tag")
            continue

        example_cfg, start, end = result
        actual_keys = set(example_cfg.keys())
        missing = expected_keys - actual_keys
        unknown = actual_keys - expected_keys
        version_stale = example_cfg.get("config_version") != CURRENT_CONFIG_VERSION

        if not missing and not unknown and not version_stale:
            print(f"  OK   {file_path.name}:{tag_line + 1}")
            continue

        # Build updated config in DEFAULT_CFG key order
        new_cfg = {}
        for key in DEFAULT_CFG:
            if key == "custom_buttons":
                new_cfg[key] = example_cfg.get(key, DEFAULT_CFG[key])
            elif key in example_cfg:
                new_cfg[key] = example_cfg[key]
            else:
                new_cfg[key] = DEFAULT_CFG[key]
        new_cfg["config_version"] = CURRENT_CONFIG_VERSION

        # Remove unknown keys
        json_text = json.dumps(new_cfg, indent=4)
        new_lines = json_text.split("\n")

        # Rewrite the block in the file
        all_lines = text.splitlines(keepends=True)
        # Find the actual start/end in the keepends version
        s = None
        e = None
        for i in range(tag_line + 1, len(all_lines)):
            if all_lines[i].strip().startswith("```json"):
                s = i + 1
            elif s is not None and all_lines[i].strip() == "```":
                e = i
                break
        if s is not None and e is not None:
            new_block = [line + "\n" for line in new_lines]
            all_lines[s:e] = new_block
            file_path.write_text("".join(all_lines), encoding="utf-8")

        changes = []
        if missing:
            changes.append(f"added {sorted(missing)}")
        if unknown:
            changes.append(f"removed {sorted(unknown)}")
        if version_stale:
            changes.append(f"version {example_cfg.get('config_version')} -> {CURRENT_CONFIG_VERSION}")
        desc = f"{file_path.name}:{tag_line + 1} - {', '.join(changes)}"
        print(f"  FIXED {desc}")
        updated.append(desc)

    return updated


# ── Config field-reference table sync ─────────────────────────────────────────
#
# The reference tables in README.md / config.md restate every cfg key.  The
# Field + Default columns and the column alignment are mechanical (they drift
# whenever a key is renamed/added/removed), so we regenerate them from
# DEFAULT_CFG; the hand-written Description column is preserved verbatim.  A
# renamed-away key drops out; a new key gets a flagged TODO row so the author
# notices it needs a description.

import re  # noqa: E402 -- local to the table-sync section

REF_FILES = [HELP_DIR / "config.md"]  # config.md is the single canonical table
REF_START_RE = re.compile(r"<!-- config-reference:start.*?-->", re.DOTALL)
REF_END = "<!-- config-reference:end -->"
_ROW_SPLIT = re.compile(r"(?<!\\)\|")  # split on unescaped pipes

# config_version is auto-managed by the migration system, not a user setting;
# the reference table omits it by convention.
_REF_SKIP = {"config_version"}


def _flat_default_map() -> dict:
    """Display-key -> default value.  serial.* is expanded to dotted keys."""
    from termapy.defaults import DEFAULT_CFG

    out: dict = {}
    for k, v in DEFAULT_CFG.items():
        if k in _REF_SKIP:
            continue
        if k == "serial" and isinstance(v, dict):
            for sk, sv in v.items():
                out[f"serial.{sk}"] = sv
        else:
            out[k] = v
    return out


def _fmt_default(v) -> str:
    """Render a default value for a table cell (escapes shown, containers short)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, dict):
        return "{...}"
    if isinstance(v, list):
        return "[]"
    if isinstance(v, str):
        return '""' if v == "" else v.encode("unicode_escape").decode("ascii")
    return str(v)


def _esc_cell(s: str) -> str:
    return s.replace("|", r"\|")


def _sync_reference_table(path, write: bool = True) -> str | None:
    text = path.read_text(encoding="utf-8")
    mstart = REF_START_RE.search(text)
    if not mstart or REF_END not in text:
        return None
    end_idx = text.index(REF_END)
    block = text[mstart.end() : end_idx]

    defaults = _flat_default_map()
    rows: list[tuple[str, str]] = []  # (field, description) in existing order
    seen: set[str] = set()
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in _ROW_SPLIT.split(line)]
        if len(cells) < 4:
            continue
        field_cell = cells[1]
        if field_cell == "Field" or set(field_cell) <= set("-: "):
            continue  # header / separator row
        field = field_cell.strip("`")
        if field in defaults:  # drop stale keys (renamed away / removed)
            rows.append((field, cells[3]))
            seen.add(field)
    # New keys get a flagged row so the author writes a description.
    for k in defaults:
        if k not in seen:
            rows.append((k, "TODO: describe this key"))

    field_cells = [f"`{f}`" for f, _ in rows]
    default_cells = [f"`{_esc_cell(_fmt_default(defaults[f]))}`" for f, _ in rows]
    descs = [d for _, d in rows]
    # Align the two mechanical columns (Field, Default); leave Description at
    # its natural width -- padding it to the longest cell (serial.port runs
    # ~250 chars) would make every row absurdly wide.
    w1 = max([len("Field")] + [len(c) for c in field_cells])
    w2 = max([len("Default")] + [len(c) for c in default_cells])
    out = [
        f"| {'Field':<{w1}} | {'Default':<{w2}} | Description |",
        f"| {'-' * w1} | {'-' * w2} | --- |",
    ]
    for fc, dc, desc in zip(field_cells, default_cells, descs):
        out.append(f"| {fc:<{w1}} | {dc:<{w2}} | {desc} |")

    new_text = text[: mstart.end()] + "\n" + "\n".join(out) + "\n" + text[end_idx:]
    if new_text != text:
        if write:
            path.write_text(new_text, encoding="utf-8")
        return path.name
    return None


def sync_reference_tables(check: bool = False) -> list:
    """Regenerate Field/Default/alignment in each marked reference table.

    With ``check=True`` nothing is written; the returned list names the files
    that WOULD change (i.e. have drifted).  Used by the freshness test.
    """
    updated = []
    for p in REF_FILES:
        if p.exists() and (r := _sync_reference_table(p, write=not check)):
            updated.append(f"{r} field-reference table")
    return updated


if __name__ == "__main__":
    print("Updating config examples in documentation...")
    results = update_doc_configs()
    results += sync_reference_tables()
    if results:
        print(f"\nUpdated {len(results)} item(s). Review the diffs.")
        for r in results:
            print(f"  - {r}")
    else:
        print("\nAll examples and reference tables are current.")
