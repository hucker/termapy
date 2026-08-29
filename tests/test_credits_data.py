"""The dependency registry must agree with pyproject.toml, the vendored tree,
and the surfaces that render it.

``credits_data.CREDITS`` is the one table; this file is the guard that
makes "added a dependency, forgot to credit it" a red suite instead of a
reader's discovery.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement

from termapy.credits_data import CREDITS, credits_of, tooltip_credits
from termapy.help_tooltip import build_help_tooltip

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_VENDOR = _REPO_ROOT / "src" / "termapy" / "vendor"


def _project() -> dict:
    return tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]


def _names(requirements: list[str]) -> set[str]:
    return {Requirement(spec).name.lower() for spec in requirements}


class TestRegistryMatchesPyproject:
    def test_every_runtime_dependency_has_a_credit(self):
        # Arrange
        declared = _names(_project()["dependencies"])
        credited = {name.lower() for credit in credits_of("runtime") for name in credit.names}

        # Assert -- the failure that started this: a dependency nobody credited
        missing = declared - credited
        assert not missing, f"pyproject dependencies with no credits_data record: {sorted(missing)}"

    def test_every_runtime_credit_is_a_declared_dependency(self):
        # Arrange
        declared = _names(_project()["dependencies"])
        credited = {name.lower() for credit in credits_of("runtime") for name in credit.names}

        # Assert -- a dependency that was dropped must lose its credit too
        stale = credited - declared
        assert not stale, f"credits_data runtime records not in pyproject: {sorted(stale)}"

    def test_optional_credits_match_the_extras(self):
        # Arrange -- the self-referential ``all`` extra is a bundle, not a package
        extras = _project()["optional-dependencies"]
        declared = {
            (extra, Requirement(spec).name.lower())
            for extra, specs in extras.items()
            for spec in specs
            if not Requirement(spec).name.lower().startswith("termapy")
        }
        credited = {(credit.extra, credit.package.lower()) for credit in credits_of("optional")}

        # Assert
        assert credited == declared, (
            f"optional credits vs extras differ: missing={sorted(declared - credited)}, "
            f"stale={sorted(credited - declared)}"
        )


class TestRegistryMatchesTheTree:
    def test_vendored_credits_exist_under_vendor(self):
        # Arrange -- pyserial vendors as ``serial/``; the others as their own name
        aliases = {"pyserial": "serial"}
        for credit in credits_of("vendored"):
            folder = aliases.get(credit.package, credit.package)
            candidates = (_VENDOR / folder, _VENDOR / f"{folder}.py")

            # Assert
            assert any(path.exists() for path in candidates), (
                f"vendored credit {credit.package!r} has no {folder}/ or {folder}.py under vendor/"
            )

    def test_every_vendored_package_has_a_credit(self):
        # Arrange -- every top-level entry under vendor/ except metadata files
        present = {
            path.stem for path in _VENDOR.iterdir()
            if not path.name.startswith(("_", ".")) and path.suffix in ("", ".py")
        }
        credited = {
            {"pyserial": "serial"}.get(credit.package, credit.package)
            for credit in credits_of("vendored")
        }

        # Assert
        assert present <= credited, f"vendored code with no credit: {sorted(present - credited)}"


class TestRegistryRenders:
    def test_tooltip_shows_every_flagged_credit(self):
        # Act
        from rich.console import Console

        console = Console(record=True, width=120, force_terminal=False)
        console.print(build_help_tooltip("1.0.0"))
        out = console.export_text()

        # Assert
        for credit in tooltip_credits():
            assert credit.display_name in out, f"{credit.display_name} missing from the tooltip"
            assert credit.author in out, f"{credit.author} missing from the tooltip"

    def test_records_are_well_formed(self):
        for credit in CREDITS:
            assert credit.package and credit.role and credit.author and credit.url, credit
            if credit.kind in ("optional", "tooling"):
                assert credit.note, f"{credit.package}: optional/tooling records need a note"
            if credit.kind == "optional":
                assert credit.extra, f"{credit.package}: optional records name their extra"

    def test_prose_credited_records_appear_on_the_page(self):
        # Arrange -- a runtime record with no note must be credited in prose instead
        page = (_REPO_ROOT / "src" / "termapy" / "help" / "acknowledgments.md").read_text(
            encoding="utf-8"
        )

        # Assert
        for credit in credits_of("runtime"):
            if not credit.note:
                assert credit.display_name.split(" /")[0] in page, (
                    f"{credit.package} has no generated bullet and no prose mention either"
                )
