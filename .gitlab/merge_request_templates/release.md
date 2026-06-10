## Release vX.Y.Z

<!-- Replace X.Y.Z with the version being released. -->

/assign_reviewer @kleczkowski @meganathanmanish

## Pre-flight checklist

- [ ] Branch is named `release/vX.Y.Z` (required for auto-tagging)
- [ ] Workspace version bumped in all `Cargo.toml` manifests
- [ ] Workspace version bumped in all `pyproject.toml` manifests
- [ ] `release:dry-run:crates` and `release:dry-run:pypi` are green on this MR's pipeline
- [ ] Release notes previewed with `make changelog-release VERSION=vX.Y.Z`
- [ ] Substrate pallet team notified (if this is a major or breaking bump)

## What merging this MR will do

Merging triggers `release:auto-tag` on `main`, which pushes the tag
`vX.Y.Z`. The tag fires the full release pipeline automatically:
`release:validate` → `release:publish-crates` (crates.io) →
`release:publish-pypi` (PyPI) → `release:changelog` → `release:notes`
(GitLab Release page). The registry uploads are **not** gated behind a
manual click — once this MR merges, publishing is automatic and
irreversible.

## Notes

<!-- Anything unusual about this release — RC, hotfix, coordination required. -->
