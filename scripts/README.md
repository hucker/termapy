# Release scripts

Two-stage release automation. Stdlib only, no third-party deps. Fail loud, never force-push, never auto-merge without `--yes`.

## Files

- `release_common.py` — shared helpers (subprocess, git checks, version validation, ANSI output)
- `release_prep.py` — cuts the release branch and prepares the commits
- `release_publish.py` — merges to main, tags, pushes, creates the GitHub release

## Workflow

From clean main, in sync with origin:

```sh
python scripts/release_prep.py 0.53.2
```

This will:

1. Sanity-check git state (on main, clean tree, in sync with origin, tag doesn't exist)
2. Cut `release/v0.53.2` from main
3. Bump version in `pyproject.toml` and `mkdocs.yml`
4. Refresh `uv.lock`
5. Update tracked file line counts in `ARCHITECTURE.md`
6. Update test count in `ARCHITECTURE.md` and `README.md`, plus rounded UI line counts in `README.md`
7. Insert a CHANGELOG stub populated from `git log <last-tag>..HEAD --oneline --no-merges`
8. Run `pytest` and `tox`
9. Build HTML help with `uvx zensical build`
10. Commit the HTML rebuild
11. Commit the version bump + CHANGELOG as `Release v0.53.2`

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

The `--yes` flag is required. There is no interactive prompt — review the diff first, then re-run with `--yes`.

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

If only the test step failed and the version files are already bumped correctly, you can also fix the underlying problem and finish manually — see the script source for the exact commit ordering (HTML rebuild commit must come *before* the `Release v<version>` commit, since publish validates `HEAD`'s subject).
