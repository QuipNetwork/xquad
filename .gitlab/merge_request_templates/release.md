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

Merging triggers a CI job (`release:auto-tag`) on `main` that pushes
the tag `vX.Y.Z` and creates the GitLab Release page. The
`release:validate`, `release:changelog`, and `release:notes` jobs then
fire automatically; `release:publish-crates` and `release:publish-pypi`
sit as manual gates.

## Notes

<!-- Anything unusual about this release — RC, hotfix, coordination required. -->
