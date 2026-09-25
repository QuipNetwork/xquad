# xquad git protocol

Two long-lived branches, `main` and `dev`. Every change lands on one of them
according to a single routing question, and every release is cut on a branch
that merges into `main`.

What the two branches mean depends on whether the project has reached 1.0. The
topology does not change at 1.0: two lines, release branches that merge to
`main`, one back-merge direction, one invariant. Only the routing question
changes.

## The invariant

`main` is never synchronised with `dev`. It is contained in it:

```sh
git merge-base --is-ancestor origin/main origin/dev
```

That command must exit 0, and CI checks it rather than leaving it to
convention, because the failure it catches is silent: two long-lived branches
drift apart commit by commit, and nothing reports it until a release is cut
from the branch that fell behind.

[`scripts/check-branch-containment.sh`](../../scripts/check-branch-containment.sh)
runs in `verify:policy` and judges two kinds of branch:

- **`dev`, as a signal.** Every push to `dev` runs it. While a back-merge is
  outstanding, `dev`'s pipelines carry a standing red until it lands. Merge
  requests into `dev` are not judged, so work continues in parallel.
- **`release/*`, as a gate.** A merge request from a release branch is judged
  on the branch's own tip, so a candidate that does not contain `main` cannot
  merge -- which is the case that would ship a release missing everything
  `main` had fixed.

`main` is never judged. It is the ref being checked against.

## Before 1.0

Cargo treats the leftmost non-zero field as the major, so `0.4.0` to `0.5.0` is
breaking and `0.4.0` to `0.4.1` is compatible. The minor is the breaking bump
and the patch carries everything else, fixes and features alike.

The routing question at merge-request time is **does this break?** Conventional
commits already force an answer to it with `!`, and the default merge request
template's Compatibility block asks it.

| Branch | Receives | Version |
| --- | --- | --- |
| `main` | every non-breaking change | `0.4.1-dev` |
| `dev` | every breaking change | `0.5.0-dev` |
| `release/v0.4.1` | cut from `main` | `0.4.1` |
| `release/v0.5.0` | cut from `dev` | `0.5.0` |

### Rules

- **No hotfix branch exists.** A bug fix is a non-breaking change landing on
  `main` like any other, and it ships in the next patch release.
- **`main` is a working branch** carrying a `-dev` version between releases.
  What shipped is read from the tag range, never from `main`'s log.
- **`main` takes merge requests, plus the reopening bump.** The one direct
  push `main` accepts is the version bump that reopens it after a release. CI
  fails any other commit that lands on `main` without a merge request; see
  [Protected branches](#protected-branches).
- **`dev` takes direct pushes for back-merges and version bumps, and nothing
  else.** Anything authored goes through a merge request. A direct push skips
  the guards scoped to a merge request -- the title check and the atomic
  spec-MR rule -- and breaking work is the work most likely to change VM
  semantics.
- **Both release branches merge to `main`**, so `release:auto-tag`, which
  looks for merge requests targeting `main`, needs no change.
- **Each release freezes its own source.** A patch freezes `main`, a minor
  freezes `dev`. The other line keeps moving.
- **Betas can come off either line.**

### Edge cases

- **At the cutover, bump `main` first, then branch `dev` from it.** Branch them
  as siblings off the tag and `main`'s bump is a commit `dev` cannot reach, so
  containment fails before any work lands.
- **When the minor ships, `main` becomes the new line.** It reopens at
  `x.y.1-dev` and `dev` takes `x.(y+1).0-dev` at its back-merge.
- **A fix landing while a release is open goes to the release branch directly.**
  Cascading it through `dev` reaches the release too, but drags every
  out-of-scope change on `dev` into the candidate with it.
- **Documentation publishes from tags, not from `main`.** `main` is unreleased,
  so `docs:publish` runs on a release tag and nothing else. A reader on an older
  line builds that tag's book locally; see the book's Stability appendix.
- **The old line's end of life is a downstream pin, not a date.** `0.4.x` stays
  patchable until every consumer that pins it has moved on.

## After 1.0

Breaking changes take the major, and the axis returns to the usual one: the
minor is features and the patch is fixes. The routing question becomes **is this
a fix for what shipped?**

| Branch | Receives | Version |
| --- | --- | --- |
| `main` | release and hotfix merges only | `1.0.0` |
| `dev` | every feature merge | `1.1.0-dev` |
| `release/v1.1.0` | cut from `dev` | `1.1.0` |
| `hotfix/v1.0.1` | cut from `main` | `1.0.1` |

### Rules

- **`main` carries exactly the last tag** and takes only release and hotfix
  merges. Features never target it.
- **Hotfixes squash.** The branch is cut from `main` and holds a few commits,
  and the squash subject becomes that patch's only changelog line. Write it as
  the fix, not as `fix: 1.0.1`.
- **Releases do not squash.** See below.
- **Feature branches merge to `dev` unsquashed**, so every commit reaches the
  changelog.

### Edge cases

- **"`main` is sparse" is a `--first-parent` property.** `git log main` still
  reaches every commit `dev` ever had. Only the first-parent view is one commit
  per release.
- **A missed back-merge reverts nothing.** A true merge keeps changes `main`
  made that the branch never touched. A missed back-merge costs divergence,
  which is slower and quieter than a revert.
- **`release:auto-tag` reads only `release/vX.Y.Z`.** A `hotfix/*` branch is not
  tagged automatically; widen the job or tag by hand when the first one ships.
- **A second release candidate cannot be tagged automatically** on a long-lived
  release branch either, because the version comes out of the branch name. Tag
  by hand, or give each candidate its own branch.
- **A hotfix during a release window needs two back-merges**, one into the open
  candidate and one into `dev`. CI checks both: the candidate's merge request is
  gated, and `dev` goes red.

## Merge and squash policy

| Merge | Squash? |
| --- | --- |
| feature or fix into `main` or `dev` | no |
| feature or fix into a `release/*` or `hotfix/*` branch | no |
| `release/*` into `main` | **no** |
| `hotfix/*` into `main` | yes |

The project setting is "Allow, off by default", which is what makes both rows
possible: the checkbox starts clear and a maintainer ticks it for the one merge
that squashes.

A release branch carries the whole history of the line it was cut from. Squash
it into `main` and none of those commits are ancestors of `main`, so the range
between two tags holds only the merge commit and one `release:` subject. A
changelog generator drops merge commits, and a `release: vX.Y.Z` entry on the
vX.Y.Z page is a tautology worth dropping too. The release notes come out empty,
and nothing reports it.

A true merge costs nothing here. `main`'s first-parent view stays exactly as
sparse, one commit per release, with the history behind it intact.

A hotfix branch is the opposite case. It is cut from `main`, so squashing it
discards nothing `main` did not already have, and one clean subject is the right
granularity for a patch.

### Merge commits

A merge commit is made in three places and nowhere else: a merge request
merging into `main` or `dev`, a release branch merging into `main`, and the
back-merge of `main` into `dev`. A feature branch never contains one. Every
merge commit a branch carries lands on the line behind the merge's second
parent, so a merge made inside a branch becomes permanent history on `dev` or
`main`.

- **Update a branch by rebasing it, never by merging its target in.**
  `git rebase origin/dev`, not `git merge origin/dev`, and the merge request's
  **Rebase** button rather than a merge.
- **Stack branches on each other's tips.** A branch stacked on another is
  rebased onto that branch's current tip, never synced by merging it in.
  `git rebase --update-refs` rebases a whole stack in one pass and moves every
  branch in it.
- **Merge a stack from the bottom up.** Merge the lowest merge request into its
  line first, with "Delete source branch" ticked, and GitLab retargets the next
  one onto the line. Never merge a stacked merge request into the branch below
  it: that merge commit lands on the lower branch and reaches the line with it.

Release branches are the exception. A minor's release branch carries every
merge `dev` made, and a fix merge request onto a release branch adds its own,
and both reach `main` through the release merge.

`verify:policy` enforces this on merge request pipelines:
`scripts/check-commit-messages.sh` fails a merge request whose branch carries a
merge commit, unless the branch is a `release/*` branch.

## Cutting a release

Cut the release branch when the milestone's scope is done, not when someone
wants something testable. A branch cut early is not a release candidate; it is a
snapshot that keeps moving, and the freeze it is supposed to impose never
happens. Pre-freeze snapshots are what betas are for.

1. Cut `release/vX.Y.Z` from its line -- `main` for a patch, `dev` for a minor.
   The source line freezes.
2. Set the version and regenerate the lockfiles:

   ```sh
   make set-version VERSION=X.Y.Z
   cargo check && uv lock
   cargo update -p xqvm --manifest-path fixtures/pallet-xqvm/Cargo.toml
   git commit -sam "chore: bump workspace to X.Y.Z"
   ```

3. Cut release candidates as work stabilises, the way a beta is cut: a
   throwaway branch off the release branch, the version set to `X.Y.Z-rcN`,
   the tag, and the branch discarded. The release branch stays at `X.Y.Z`.
   Only fixes land on it; a feature arriving here is the rule being broken.
4. Open the merge request into `main` with the `release` template and merge it
   without squashing. `release:auto-tag` pushes `vX.Y.Z` and the tag pipeline
   publishes.
5. Reopen `main` at the next patch `-dev` version, by pushing the bump
   straight to `main`:

   ```sh
   git switch -c reopen origin/main                 # local only, never pushed
   make set-version VERSION=X.Y.(Z+1)-dev
   cargo check && uv lock
   cargo update -p xqvm --manifest-path fixtures/pallet-xqvm/Cargo.toml
   git commit -sam "chore: reopen main at X.Y.(Z+1)-dev"
   git push origin HEAD:main
   git switch - && git branch -D reopen
   ```

   Until it lands, `release:validate` fails on `main`'s pipelines.
6. Back-merge `main` into `dev`, below. Until it lands, `dev` carries a
   standing red.

[`RELEASING.md`](../../RELEASING.md) has the full checklist.

## Pre-release snapshots

Use `-betaN` for anything published before the freeze.

Not `-devN`: that suffix already means unreleased and never published, and
reusing it makes the same string mean two opposite things.

Not `-previewN` or `-preN`: PEP 440 normalises both to `rc`. A Rust toolchain
keeps `0.5.0-preview1` and `0.5.0-rc1` apart, a Python one renders both as
`0.5.0rc1`. Publish a preview and then a candidate and the second upload is
rejected as a duplicate, after the first has already published elsewhere and
cannot be withdrawn.

A beta is cut on a throwaway branch, tagged, and never merged:

```sh
git switch -c beta/v0.5.0-beta1 origin/dev     # or origin/main, for a 0.4.x beta
make set-version VERSION=0.5.0-beta1
cargo check && uv lock
cargo update -p xqvm --manifest-path fixtures/pallet-xqvm/Cargo.toml
git commit -sam "chore: cut 0.5.0-beta1"
git tag v0.5.0-beta1 && git push origin v0.5.0-beta1
git switch - && git branch -D beta/v0.5.0-beta1
```

The published version comes from the tree, not from the tag, so the tagged
commit has to carry the beta version -- which is the whole reason a commit
exists at all here. The lockfiles have to agree with it too: `release:crates`
publishes with `--locked`. The line itself never moves, so there is no second
bump to remember and nothing to undo, and the procedure is identical off either
line.

The trade is that the beta's commit is reachable only through its tag. A `v*`
tag is protected and only a Maintainer can delete one, and git does not collect
a tagged commit, so that is enough for something ephemeral by construction.

A beta publishes to crates.io and PyPI and gets no release page and no
documentation, exactly like a release candidate: `release:notes` and
`docs:publish` match `cliff.toml`'s tag pattern, which is three numeric fields
and nothing else.

## Back-merging

Back-merge `main` into `dev` after every tag on `main`.

The version sites always conflict, because the two branches hold different
versions by design and both have moved since the last back-merge. A plain
`git merge` stops with every manifest carrying conflict markers, and
`make set-version` cannot repair that: it reads every site before writing any,
and a manifest with conflict markers does not parse.

So take the version out of the merge before it starts. Carry `dev`'s version
onto a scratch branch off `main`, and merge that instead. Both sides then made
the same change to every version line, git has nothing to reconcile there, and
whatever still conflicts is a real conflict:

```sh
git fetch origin
git switch -c backmerge/vX.Y.Z origin/main       # local only, never pushed
make set-version VERSION=0.5.0-dev               # dev's version, not main's
cargo check && uv lock
cargo update -p xqvm --manifest-path fixtures/pallet-xqvm/Cargo.toml
git commit -sam "chore: carry dev's version into the vX.Y.Z back-merge"

git switch dev && git pull --ff-only
git merge --no-ff backmerge/vX.Y.Z               # only real conflicts remain
git push origin dev
git branch -d backmerge/vX.Y.Z
```

Everything else `main` changed in those manifests -- a new dependency, a
changed constraint -- merges in normally, because only the version lines were
taken out of play. If `Cargo.lock` or `uv.lock` still conflicts, both sides
changed dependencies: resolve the manifests, take either lockfile, and
regenerate it with `cargo check` and `uv lock`.

Before 1.0 expect real conflicts. `main` is a working line carrying every
non-breaking feature and fix, not a line that only receives releases, so the
two branches diverge in code between tags. A back-merge here is an ordinary
merge with a mechanical prologue, not a rubber stamp.

This stays manual on purpose. The auto-resolvable case is the one where
*nothing but* the version sites conflict, which is the post-1.0 shape, and
before 1.0 it is the exception. Machinery whose fast path rarely fires is wrong
more often than it is right, and a back-merge that silently resolves a real code
conflict is the worst outcome available here.

Avoid a `.gitattributes` `merge=ours` driver too. It needs per-clone
configuration and fails open when someone has not set it up, silently taking the
wrong side.

## Protected branches

Protection and push access are separate settings, and the distinction is what
makes this work. *Protected* is what makes `test:wasm`, `test:substrate` and the
`hardware:*` tier run unconditionally rather than path-gated. *Push access* is
who may write without a merge request.

| Branch | Protected | Push | Merge | Why |
| --- | --- | --- | --- | --- |
| `main` | yes | Maintainers | Maintainers | The line consumers pin. Pushes are open for the reopening bump only, and CI enforces that. |
| `dev` | yes | Maintainers | Maintainers | The back-merge is a push. Closing it would tax the one routine operation. |
| `release/*` | yes | Maintainers | Maintainers | Protection alone is the point: a release candidate runs the full CI tier. |

Force push is off on all three, and code owner approval is off on all three.
`v*` tags are protected separately, Maintainers only.

Push access cannot say "version bumps only", so a CI check says it for `main`.
[`scripts/check-main-direct-push.sh`](../../scripts/check-main-direct-push.sh)
runs in `verify:policy` on every push to `main` and judges each commit the push
put on `main`'s first-parent line. A commit passes if it is a merge request's
merge commit, or if every file it touches is identical before and after once
the old and new versions are masked out. Anything else turns `main` red. The
check detects and does not prevent: the commit is already on `main` when it
runs, and the fix is a revert through a merge request.

Opening pushes to Maintainers was a trade. A release costs one merge request
fewer, and in exchange a stray `git push origin main` from any Maintainer's
clone now lands, where it used to be rejected. The check turns that into a red
pipeline rather than a silent change.
