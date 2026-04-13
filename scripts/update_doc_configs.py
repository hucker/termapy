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
import re
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
            print(f"  WARN: {file_path.name}:{tag_line + 1} — no JSON block found after tag")
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
        desc = f"{file_path.name}:{tag_line + 1} — {', '.join(changes)}"
        print(f"  FIXED {desc}")
        updated.append(desc)

    return updated


if __name__ == "__main__":
    print("Updating config examples in documentation...")
    results = update_doc_configs()
    if results:
        print(f"\nUpdated {len(results)} example(s). Review the diffs.")
    else:
        print("\nAll examples are current.")
