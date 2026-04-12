"""Verify that every image reference in help markdown files points to an existing file."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

HELP_DIR = Path(__file__).resolve().parent.parent / "src" / "termapy" / "help"
IMG_RE = re.compile(r"!\[.*?\]\((img/[^)]+)\)")


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
