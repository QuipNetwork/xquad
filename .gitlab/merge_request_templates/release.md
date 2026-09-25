## Release vX.Y.Z

<!-- Replace X.Y.Z with the version being released. -->

/assign_reviewer @kleczkowski @meganathanmanish

## Release Type

Pick one.

- [ ] **Patch** `vX.Y.Z` -- cut from `main`, which is frozen for the window
- [ ] **Minor** `vX.Y.0` -- cut from `dev`, which is frozen for the window. At
      the back-merge `dev` takes `x.(y+1).0-dev` and `main` becomes the new
      line

Both paths merge into `main`. `docs/guide/gitflow-protocol.md` has the
sequence for each.

## Pre-flight checklist

- [ ] **Do not squash this merge.** `release/*` into `main` never squashes, on
      either path: a squashed minor loses every commit from `dev`, a squashed
      patch loses the fixes that landed on the release branch, and both fail
      silently as empty release notes. Leave the box on the merge form clear
- [ ] Branch is named `release/vX.Y.Z` (required for auto-tagging)
- [ ] MR title is `release: vX.Y.Z` -- checked against the commit grammar by
      `verify:policy` (`scripts/check-mr-title.sh`) and by the title pattern
      on the MR form
- [ ] Title set before the final push -- GitLab does not start a pipeline
      on a title edit, so a title changed afterwards is not rechecked here
- [ ] Every version site is at `X.Y.Z` -- `make set-version VERSION=X.Y.Z`
      writes all of them, and `make check-version-sites TAG=vX.Y.Z` is clean
      locally. One box rather than one per file type: splitting it that way is
      what left `xqvm_py/__init__.py` and the `==X.Y.Z` peer pins belonging to
      neither. `make list-version-sites` prints the full list
- [ ] Lockfiles regenerated -- `cargo check`, `uv lock`, and
      `cargo update -p xqvm --manifest-path fixtures/pallet-xqvm/Cargo.toml`
- [ ] This branch contains `origin/main` -- `make check-branch-containment` is
      clean. `verify:policy` gates this merge on it, because a candidate that
      does not contain `main` ships without everything `main` has fixed
- [ ] `release:validate` is green on this MR's pipeline
- [ ] Release notes previewed with `make changelog-release VERSION=vX.Y.Z`
- [ ] Substrate pallet team notified (if this is a major or breaking bump)

## What merging this MR will do

Merging triggers `release:auto-tag` on `main`, which pushes the tag
`vX.Y.Z`. The tag fires the full release pipeline automatically:
`release:validate` → `release:crates` (crates.io) → `release:pypi`
(PyPI) → `release:notes` (GitLab Release page) → `docs:publish`. The
registry uploads are **not** gated behind a manual click -- once this MR
merges, publishing is automatic and irreversible.

A prerelease tag (`-rc`, `-beta`) still publishes to both registries, but
gets no Release page and publishes no documentation: those two jobs match
`cliff.toml`'s tag pattern, which is three numeric fields and nothing else.

## After the tag

- [ ] Reopen `main` at the next patch `-dev` version (`make set-version`),
      by direct push -- recipe in `docs/guide/gitflow-protocol.md`. The commit
      changes the version and nothing else, or `verify:policy` fails. Until
      it lands, `release:validate` fails on `main`'s pipelines, which is the
      check that exists because this step has been skipped before
- [ ] Back-merge `main` into `dev` -- recipe in `docs/guide/gitflow-protocol.md`.
      This one is a direct push, and after a minor it also carries `dev` to
      `x.(y+1).0-dev`. `dev` carries a standing red until it lands

## Notes

<!-- Anything unusual about this release -- coordination required, a
     prerelease, a line that needs to stay open. -->
