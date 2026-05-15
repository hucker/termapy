"""Guard the markdown -> credits.py sync.

The ``/credits`` plugin embeds the acknowledgments page as a Python
string constant for runtime use (so the wheel ships zero markdown).
The canonical source is ``src/termapy/help/acknowledgments.md``;
``scripts/sync_acknowledgments.py`` generates the embedded string
from it.  This test asserts the two are in sync so a stale embed
can never reach CI.

If this test fails: edit the markdown (or undo your accidental edit
to the generated block in credits.py) and run::

    python scripts/sync_acknowledgments.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "scripts"

# Make ``scripts/`` importable so we can call the sync's --check entry
# point directly.  This is the same path the release pipeline uses.
sys.path.insert(0, str(_SCRIPTS_DIR))


class TestCreditsSync:
    def test_acknowledgments_md_matches_credits_constant(self):
        """credits.py's embedded constant must equal the markdown text."""
        # Arrange
        md_path = (
            _REPO_ROOT / "src" / "termapy" / "help" / "acknowledgments.md"
        )
        expected = md_path.read_text(encoding="utf-8")

        # Act -- read the constant directly from the live module
        from termapy.builtins.commands.credits import _ACKNOWLEDGMENTS
        actual = _ACKNOWLEDGMENTS

        # Assert
        assert actual == expected, (
            "credits._ACKNOWLEDGMENTS drifted from "
            "src/termapy/help/acknowledgments.md.  Run: "
            "python scripts/sync_acknowledgments.py"
        )

    def test_sync_script_check_mode_passes(self):
        """The --check mode of the sync script must report clean."""
        # Arrange
        from sync_acknowledgments import main

        # Act -- main returns the process exit code (0 == in sync)
        rc = main(["--check"])

        # Assert
        assert rc == 0, (
            "sync_acknowledgments.py --check reports drift; "
            "run the script without --check to fix."
        )

    def test_credits_plugin_outputs_full_markdown(self, tmp_path):
        """/credits prints every line of the embedded constant."""
        # Arrange -- minimal fake ctx that captures io.output() calls
        from termapy.builtins.commands.credits import (
            _ACKNOWLEDGMENTS,
            _handler,
        )

        captured: list[str] = []

        class _IO:
            def output(self, text, *_a, **_kw):
                captured.append(text)

        class _Ctx:
            io = _IO()

        # Act
        result = _handler(_Ctx(), "")  # type: ignore[arg-type]

        # Assert
        assert result.success is True, "/credits handler reports success"
        assert "\n".join(captured) + "\n" == _ACKNOWLEDGMENTS or "\n".join(
            captured
        ) == _ACKNOWLEDGMENTS.rstrip("\n"), (
            "captured output must reconstruct the embedded constant "
            "(modulo a single trailing newline from splitlines)"
        )


if __name__ == "__main__":  # pragma: no cover -- manual debugging only
    pytest.main([__file__, "-v"])
