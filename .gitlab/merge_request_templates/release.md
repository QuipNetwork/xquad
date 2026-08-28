## Release vX.Y.Z

<!-- Replace X.Y.Z with the version being released. -->

/assign_reviewer @kleczkowski @meganathanmanish

## Pre-flight checklist

- [ ] Branch is named `release/vX.Y.Z` (required for auto-tagging)
- [ ] MR title is `release: vX.Y.Z` -- squash-on-merge makes the title the
      subject of the commit that lands on `main`, so it is checked against
      the commit grammar by `verify:policy`
- [ ] Title set before the final push -- GitLab does not start a pipeline
      on a title edit, so a title changed afterwards is not rechecked here
- [ ] Every version site bumped -- `make check-version-sites TAG=vX.Y.Z` is
      clean locally. One box rather than one per file type: splitting it that
      way is what left `xqvm_py/__init__.py` and the `==X.Y.Z` peer pins
      belonging to neither. `make list-version-sites` prints the full list
- [ ] Lockfiles regenerated -- `cargo check`, `uv lock`, and
      `cargo update -p xqvm --manifest-path fixtures/pallet-xqvm/Cargo.toml`
- [ ] `release:validate` is green on this MR's pipeline
- [ ] Release notes previewed with `make changelog-release VERSION=vX.Y.Z`
- [ ] Substrate pallet team notified (if this is a major or breaking bump)

## What merging this MR will do

Merging triggers `release:auto-tag` on `main`, which pushes the tag
`vX.Y.Z`. The tag fires the full release pipeline automatically:
`release:validate` → `release:crates` (crates.io) → `release:pypi`
(PyPI) → `release:notes` (GitLab Release page). The registry uploads
are **not** gated behind a manual click -- once this MR merges,
publishing is automatic and irreversible.

## Notes

<!-- Anything unusual about this release — RC, hotfix, coordination required. -->
