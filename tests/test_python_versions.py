"""The supported-Python list must agree everywhere it is written down.

The ``Programming Language :: Python :: 3.X`` classifiers in
``pyproject.toml`` are the one source (they are what PyPI shows); the tox
envlist, the CI matrix and the README badge are copies of it.  This guard
makes "added a Python version, forgot one of the copies" a red suite
instead of a stale badge.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

_CLASSIFIER = re.compile(r"^Programming Language :: Python :: (3\.\d+)$")
_REQUIRES_PYTHON = re.compile(r"^>=(3\.\d+)$")
_ENVLIST = re.compile(r"^envlist\s*=\s*(.+)$", re.MULTILINE)
_TOX_FACTOR = re.compile(r"\bpy3(\d+)\b")
_CI_MATRIX = re.compile(r"python-version:\s*\[([^\]]*)\]")
_CI_VERSION = re.compile(r'"(3\.\d+)"')
_BADGE = re.compile(r"badge/python-(3\.\d+)--(3\.\d+)-")


def _read(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


def _project() -> dict:
    return tomllib.loads(_read("pyproject.toml"))["project"]


def classifier_versions() -> list[str]:
    """The declared versions, in classifier order."""
    versions = []
    for classifier in _project()["classifiers"]:
        match = _CLASSIFIER.match(classifier)
        if match:
            versions.append(match.group(1))
    return versions


def tox_versions() -> list[str]:
    match = _ENVLIST.search(_read("tox.ini"))
    assert match, "tox.ini has no envlist line"
    return [f"3.{minor}" for minor in _TOX_FACTOR.findall(match.group(1))]


def ci_versions() -> list[str]:
    match = _CI_MATRIX.search(_read(".github/workflows/tests.yml"))
    assert match, "tests.yml has no python-version matrix"
    return _CI_VERSION.findall(match.group(1))


def badge_range() -> tuple[str, str]:
    match = _BADGE.search(_read("README.md"))
    assert match, "README.md has no python-X--Y badge"
    return match.group(1), match.group(2)


class TestSupportedVersionsAgree:
    def test_classifiers_start_at_requires_python(self):
        # Arrange
        match = _REQUIRES_PYTHON.match(_project()["requires-python"])
        assert match, "requires-python is expected to be a plain >=3.X floor"
        expected = match.group(1)
        # Act
        actual = classifier_versions()[0]
        # Assert
        assert actual == expected, "the first classifier must be the requires-python floor"

    def test_classifiers_are_consecutive_minors(self):
        # Arrange
        versions = classifier_versions()
        minors = [int(version.split(".")[1]) for version in versions]
        # Assert -- a gap means a version was skipped or mistyped
        expected = list(range(minors[0], minors[0] + len(minors)))
        assert minors == expected, f"classifiers must be consecutive minors, got {versions}"

    def test_tox_envlist_matches_classifiers(self):
        # Arrange
        expected = classifier_versions()
        # Act
        actual = tox_versions()
        # Assert
        assert actual == expected, "tox envlist must list exactly the classifier versions"

    def test_ci_matrix_matches_classifiers(self):
        # Arrange
        expected = classifier_versions()
        # Act
        actual = ci_versions()
        # Assert
        assert actual == expected, "the CI test matrix must list exactly the classifier versions"

    def test_readme_badge_spans_classifiers(self):
        # Arrange
        versions = classifier_versions()
        expected = (versions[0], versions[-1])
        # Act
        actual = badge_range()
        # Assert
        assert actual == expected, "the README python badge must span the classifier range"
