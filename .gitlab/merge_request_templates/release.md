## Release vX.Y.Z

<!-- release:prepare opens this merge request when `release/vX.Y.Z` is
     pushed: it fills in the version, ticks the release type and requests
     review from both maintainers. Opening one by hand instead, replace
     X.Y.Z with the version, tick the type and add both reviewers. -->

## Release Type

- [ ] **Patch** -- cut from `main`, which is frozen for the window
- [ ] **Minor** -- cut from `dev`, which is frozen for the window. At
      the back-merge `dev` takes `x.(y+1).0-dev` and `main` becomes the new
      line

Both paths merge into `main`. `docs/guide/gitflow-protocol.md` has the
sequence for each.

## Checked by CI

Each box names the job that proves it. Tick it from that job's result,
not from a local run.

- [ ] The branch is named `release/vX.Y.Z` and every version site and
      lockfile is at `X.Y.Z`
      <!-- Evidence: release:prepare passed on this branch, reporting
           "already at X.Y.Z" or "pushed chore: bump workspace to X.Y.Z".
           It runs only on a branch named exactly release/vX.Y.Z, the name
           release:auto-tag reads, and checks `make check-version-sites
           TAG=vX.Y.Z`, `cargo update --workspace --locked` and
           `uv lock --check`. The tag pipeline's release:validate checks
           the version sites again before anything publishes. -->
- [ ] The branch contains `origin/main`, and the title passes the commit
      grammar
      <!-- Evidence: verify:policy green on this merge request's latest
           pipeline. It runs `make check-branch-containment` and
           `scripts/check-mr-title.sh`, and gates the merge on both:
           a candidate that does not contain `main` ships without
           everything `main` has fixed. -->
- [ ] `release:validate` is green
      <!-- Evidence: the job itself, on this merge request's latest
           pipeline. -->

## Checked by a person

An agent never ticks these. Each is a judgement, or an action taken at
merge time.

- [ ] **Do not squash this merge.** `release/*` into `main` never squashes, on
      either path: a squashed minor loses every commit from `dev`, a squashed
      patch loses the fixes that landed on the release branch, and both fail
      silently as empty release notes. Leave the box on the merge form clear
      <!-- Evidence: the squash box on the merge form, at the moment the
           merge is clicked. Never ticked by an agent. -->
- [ ] The title is `release: vX.Y.Z` and was not edited after the last
      pipeline -- GitLab does not start a pipeline on a title edit, so an
      edited title is not rechecked
      <!-- Evidence: the title as shown, against the time of the latest
           pipeline. Remove the `Draft:` prefix before merging. Never
           ticked by an agent. -->
- [ ] The release notes preview below reads as this release
      <!-- Evidence: a maintainer read the preview. Never ticked by an
           agent. -->
- [ ] Substrate pallet team notified (if this is a breaking bump)
      <!-- Evidence: the notification was sent, or the release breaks
           nothing the pallet uses. Never ticked by an agent. -->
- [ ] Both @kleczkowski and @meganathanmanish approved
      <!-- Evidence: both approvals show on this merge request. The
           project rule asks for one; a release asks for both by
           convention (RELEASING.md). Approvals reset on push, so they
           are given after the last push. Never ticked by an agent. -->

## Release notes preview

<!-- Paste the output of `make changelog-release VERSION=vX.Y.Z` here.
     Anyone may fill this slot, an agent included; judging it is the box
     above. -->

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
