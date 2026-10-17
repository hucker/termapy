# Release scripts

Two-stage release automation. Stdlib only, no third-party deps. Fail loud, never force-push, never auto-merge without `--yes`.

## Files

- `release_common.py` - shared helpers (subprocess, git checks, version validation, ANSI output)
- `release_prep.py` - cuts the release branch and prepares the commits
- `audit_lock.py` - audits `uv.lock` for known vulnerabilities (a `release_prep` gate; CI runs it too)
- `release_publish.py` - merges to main, tags, pushes, creates the GitHub release

## Workflow

From clean main, in sync with origin:

```sh
python scripts/release_prep.py 0.53.2
```

This will:

1. Sanity-check git state (on main, clean tree, in sync with origin, tag doesn't exist) and the gates: ruff and ty at zero, no undisciplined suppression added since the last tag
2. Cut `release/v0.53.2` from main
3. Bump version in `pyproject.toml` and `mkdocs.yml`; `uv lock --upgrade` so every dependency the manifest does not pin moves to its newest allowed version; re-run the ruff/ty gate against that environment
4. Audit `uv.lock` for known vulnerabilities (`audit_lock.py`); a finding reviewed as unreachable is passed with `--ignore-vuln <ID>`
5. Run the hw_loopback suite (`tests/test_hw_loopback.py`) with `--require-hw-loopback`, serially and without coverage; it refuses unless the suite passes on the attached TX-RX-jumpered adapter (`TERMAPY_HW_LOOPBACK_SN`, default `BG03U7VTA`). `--skip-hw-loopback` releases without it, on the command line on purpose
6. Refresh the release-updated counts: per-module line counts in `ARCHITECTURE.md`, the test count and ty badge in `README.md`, the suppression count. Coverage is not one of them: the Codecov badge, measured by CI on every push, is the one coverage number
7. Regenerate tagged config examples (`update_doc_configs.py`) and check `credits.py` is in sync with `help/acknowledgments.md`
8. Refresh the USB vendor table from upstream `usb.ids`
9. Insert a CHANGELOG stub populated from `git log <last-tag>..HEAD --oneline --no-merges`
10. Run `pytest`
11. Run `tox`
12. Build HTML help with `uvx zensical build`
13. Commit the HTML rebuild
14. Commit the version bump + lock + CHANGELOG + counts as `Release v0.53.2`

Then review the diff and rewrite the CHANGELOG stub into a real user-facing summary:

```sh
git log -p main..HEAD
# edit CHANGELOG.md, replace the TODO bullets with real notes
git add CHANGELOG.md
git commit --amend --no-edit
```

Then publish:

```sh
python scripts/release_publish.py --yes
```

This will:

1. Verify you're on `release/vN.N.N` (parses version from branch name)
2. Verify the tree is clean and CHANGELOG has no TODO markers left
3. Verify `HEAD` subject is `Release v<version>`
4. Verify the tag does not yet exist on origin
5. Checkout main, merge release branch with `--no-ff`
6. Tag `v<version>`
7. Push main, tag, and release branch to origin
8. Create the GitHub release with notes pulled from the CHANGELOG section

The `--yes` flag is required. There is no interactive prompt - review the diff first, then re-run with `--yes`.

## Conventions enforced

- Versions must be `N.N.N`. No release candidates, no leading `v`, no suffixes.
- Releases are always cut from `main`.
- Merges to main are always `--no-ff` (preserves branch history in the graph).
- Release branches are pushed and kept, never deleted.
- HTML help rebuild is its own commit, separate from the version bump commit, so the diff stays readable.

## Recovering from a failed prep run

If `release_prep.py` fails partway through (e.g. tox fails on one Python version), the release branch is left in whatever state it reached. To start over:

```sh
git checkout main
git branch -D release/v<version>
# then re-run prep
```

If only the test step failed and the version files are already bumped correctly, you can also fix the underlying problem and finish manually - see the script source for the exact commit ordering (HTML rebuild commit must come *before* the `Release v<version>` commit, since publish validates `HEAD`'s subject).

## Other scripts

Helpers that aren't part of the release pipeline but live here
because they share the stdlib-only / fail-loud convention.

- `update_doc_configs.py` - regenerate tagged JSON config blocks
  in `README.md` and `help/config.md` so every example matches the
  current `DEFAULT_CFG`.  Run after adding or renaming a config
  key; `test_help_images.py` will point you at it on failure.

- `sync_format_tables.py` - regenerate the format-spec type tables
  in `README.md` and `help/protocol-testing.md` from
  `protocol/type_codes.py`, decoding every example through the real
  parser.  Not stdlib-only (it runs the decoder), so use
  `uv run python scripts/sync_format_tables.py`;
  `test_format_tables_sync.py` fails on drift.

- `sync_architecture.py` - regenerate ARCHITECTURE.md's module tree
  and test table from the module and test docstrings (first
  paragraph each), keeping the line counts `release_prep` owns.
  Imports `release_prep` for `ARCHITECTURE_LINE_COUNTS`; run it as
  `uv run python scripts/sync_architecture.py` after adding a module
  or test file; `test_architecture_sync.py` fails on drift.

- `check_dep_updates.py` - query PyPI for the latest release of
  every direct dependency and flag which ones have moved past
  our upper bound.  Run before a release (or whenever you want)
  to see if `tree-sitter`, `textual`, etc. have new minors
  worth testing.  Output is a sorted table with `BUMP AVAILABLE`
  rows at the top.  It complements the release's `uv lock
  --upgrade`, which moves everything the manifest allows: a
  direct dependency held under a cap is the one thing that step
  cannot move, and this table is where you see it.  Not a gate,
  because raising a cap is a decision, not a check.
