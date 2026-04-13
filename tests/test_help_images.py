"""Verify help content: image references exist and tagged config examples stay current."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

HELP_DIR = Path(__file__).resolve().parent.parent / "src" / "termapy" / "help"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
IMG_RE = re.compile(r"!\[.*?\]\((img/[^)]+)\)")
CONFIG_TAG = "<!-- validate-config-keys -->"


def _collect_image_refs() -> list[tuple[str, str]]:
    """Return (markdown_filename, img_path) for every image reference."""
    refs = []
    for md in sorted(HELP_DIR.glob("*.md")):
        for match in IMG_RE.finditer(md.read_text(encoding="utf-8")):
            refs.append((md.name, match.group(1)))
    return refs


class TestHelpImages:
    @pytest.mark.parametrize(
        "md_file, img_path",
        _collect_image_refs(),
        ids=[f"{md}:{img}" for md, img in _collect_image_refs()],
    )
    def test_image_exists(self, md_file, img_path):
        """Every image referenced in help markdown must exist in help/img/."""
        # Act
        full_path = HELP_DIR / img_path

        # Assert
        assert full_path.exists(), (
            f"{md_file} references {img_path} but file does not exist"
        )


# ── Config example validation ────────────────────────────────────────────────


def _find_tagged_json_blocks() -> list[tuple[Path, int, str]]:
    """Find JSON code blocks preceded by <!-- validate-config-keys -->.

    Returns list of (file_path, line_number, json_text).
    """
    md_files = list(HELP_DIR.glob("*.md")) + [PROJECT_ROOT / "README.md"]
    results = []
    json_block_re = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)
    for md_path in sorted(md_files):
        text = md_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if CONFIG_TAG in line:
                rest = "\n".join(lines[i + 1 :])
                m = json_block_re.search(rest)
                if m:
                    results.append((md_path, i + 1, m.group(1)))
    return results


class TestHelpConfigExamples:
    """Validate that tagged JSON config examples contain all DEFAULT_CFG keys.

    This test is read-only -- it detects drift but does not modify files.
    Run ``python scripts/update_doc_configs.py`` to fix stale examples.
    """

    @pytest.mark.parametrize(
        "file_path, tag_line, json_text",
        _find_tagged_json_blocks(),
        ids=[
            f"{p.name}:{line}"
            for p, line, _ in _find_tagged_json_blocks()
        ],
    )
    def test_config_example_keys(self, file_path, tag_line, json_text):
        """Tagged config examples must have all keys from DEFAULT_CFG."""
        from termapy.defaults import DEFAULT_CFG
        from termapy.migration import CURRENT_CONFIG_VERSION

        # Arrange
        example_cfg = json.loads(json_text)
        expected_keys = set(DEFAULT_CFG.keys())
        actual_keys = set(example_cfg.keys())

        # Act
        missing_keys = expected_keys - actual_keys
        unknown_keys = actual_keys - expected_keys
        version_ok = example_cfg.get("config_version") == CURRENT_CONFIG_VERSION

        # Assert
        assert not unknown_keys, (
            f"{file_path.name}:{tag_line} has unknown config keys: "
            f"{sorted(unknown_keys)}"
        )
        assert not missing_keys, (
            f"{file_path.name}:{tag_line} is missing config keys: "
            f"{sorted(missing_keys)}. "
            f"Run: python scripts/update_doc_configs.py"
        )
        assert version_ok, (
            f"{file_path.name}:{tag_line} has config_version "
            f"{example_cfg.get('config_version')} but current is "
            f"{CURRENT_CONFIG_VERSION}. "
            f"Run: python scripts/update_doc_configs.py"
        )
