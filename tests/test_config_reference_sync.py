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

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "scripts"

# Make scripts/ importable -- same path the release pipeline uses.
sys.path.insert(0, str(_SCRIPTS_DIR))


class TestConfigReferenceSync:
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
