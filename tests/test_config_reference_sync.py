"""Guard the config field-reference table against drift.

``src/termapy/help/config.md`` has a `<!-- config-reference -->` table whose
Field + Default columns and alignment are generated from ``DEFAULT_CFG`` by
``scripts/update_doc_configs.py`` (the Description column is hand-written and
preserved).  This test asserts the table is in sync so a renamed / added /
removed cfg key can't leave a stale table behind.

If this test fails: run ``python scripts/update_doc_configs.py`` and fill in
any ``TODO: describe this key`` row it adds for a new key.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "scripts"

# Make scripts/ importable -- same path the release pipeline uses.
sys.path.insert(0, str(_SCRIPTS_DIR))


class TestConfigReferenceSync:
    @pytest.mark.rule("demo-cfg-mirrors-defaults")
    def test_reference_table_in_sync(self):
        """The generated Field/Default columns must match DEFAULT_CFG."""
        # Arrange / Act -- check mode writes nothing; returns drifted files.
        from update_doc_configs import sync_reference_tables

        drift = sync_reference_tables(check=True)

        # Assert
        assert drift == [], (
            "config reference table drifted from DEFAULT_CFG "
            f"({drift}); run: python scripts/update_doc_configs.py"
        )

    @pytest.mark.rule("baud-standard-unless-custom")
    def test_baud_rate_row_lists_the_standard_rates(self):
        """The baud_rate row's hand-written list is STANDARD_BAUD_RATES, no more, no fewer."""
        import re

        from termapy.defaults import STANDARD_BAUD_RATES

        # Arrange
        config_md = _REPO_ROOT / "src" / "termapy" / "help" / "config.md"
        row = next(
            line
            for line in config_md.read_text(encoding="utf-8").splitlines()
            if line.startswith("| `serial.baud_rate`")
        )
        description = row.split("|")[3]

        # Act
        listed = tuple(int(number) for number in re.findall(r"\b\d{3,}\b", description))

        # Assert
        assert listed == (*STANDARD_BAUD_RATES, 300), (
            "the row lists the standard rates in order, then the custom floor of 300"
        )

    def test_no_todo_rows_left(self):
        """A new key adds a 'TODO: describe' row -- none may ship."""
        # Arrange
        config_md = _REPO_ROOT / "src" / "termapy" / "help" / "config.md"

        # Act
        text = config_md.read_text(encoding="utf-8")

        # Assert
        assert "TODO: describe this key" not in text, (
            "config.md has a placeholder row for a new cfg key -- "
            "write its Description in the field-reference table"
        )
