# Releasing the xquad toolchain

Cutting a release publishes eleven artefacts in one shot from a single
`v<X.Y.Z>` git tag:

| # | Artefact | Registry | Ordering |
|---|----------|----------|----------|
| 1 | [`xqvm`](xqvm/) | crates.io | before xqasm, xqcli |
| 2 | [`xqasm`](xqasm/) | crates.io | before xqcli |
| 3 | [`xqcli`](xqcli/) | crates.io | last Rust crate |
| 4 | [`xqffi`](xqffi/) abi3 wheels + sdist | PyPI | before peers (2 abi3 wheels + sdist) |
| 5 | [`xqvm_py`](xqvm_py/) sdist | PyPI | before xquad |
| 6 | [`xqcp`](xqcp/) sdist | PyPI | before xquad |
| 7 | [`xqsa`](xqsa/) sdist | PyPI | before xquad |
| 8 | [`xquad`](xquad/) sdist | PyPI | last -- depends on 4-7 |
| 9 | GitLab Release notes | GitLab | last -- `release:notes` |

Triggered by `.gitlab/ci/release.yml`; see that file for the exact
ordering and rules. `xqffi` the Rust *crate* stays `publish = false`
(cdylib-only, consumed via PyPI). The release-notes step generates
notes from conventional-commit history via [git-cliff] (config in
[`cliff.toml`](cliff.toml)) and creates the GitLab Release page; no
in-tree `CHANGELOG.md` exists -- the release page is the canonical
view.

[git-cliff]: https://git-cliff.org/

## Prerequisites (one-time)

**Protected CI variable** (set once in **Settings → CI/CD →
Variables**; masked + protected):

- `CARGO_REGISTRY_TOKEN` -- crates.io API token from
  `ops@postquant.xyz`. Scope to `xqvm`, `xqasm`, `xqcli` publish-new
  + publish-update.

**PyPI Trusted Publishing (OIDC)** -- no long-lived token in CI. Each
of the 5 PyPI projects (`xqffi`, `xqvm_py`, `xqcp`, `xqsa`, `xquad`)
must have a GitLab Trusted Publisher configured at
`pypi.org/manage/project/<name>/settings/publishing/` pointing at:

    namespace = quip.network
    project   = xquad
    pipeline  = .gitlab-ci.yml
    env       = release

The matching `release` environment must exist in **Settings → CI/CD →
Environments**, restricted to protected tags `v*`.

## Pre-flight

Before cutting a tag:

1. **Watch `release:validate`.** It runs on every pipeline -- MR,
   push, and the tag pipeline itself -- using the same setup
   `release:pypi` uses. If it is red on `main`, do not merge the
   release MR: merging *is* the tagging action, and `release:auto-tag`
   pushes the tag within seconds of the merge, so there is no window
   between the two in which to decide not to tag.

   If a tag does get cut against a red tree, nothing is lost and
   nothing leaks. The tag pipeline runs `release:validate` again ahead
   of `release:crates`, so it fails at the same step with no registry
   touched. Delete the tag, fix the underlying issue, and re-cut.
2. **Verify Substrate pallet coordination.** Crate renames force
   major-version bumps downstream. Ping the pallet team before the
   first `xqvm 0.1.0` release so their pins move atomically; for
   non-breaking bumps (`0.1.x → 0.1.y`) a ping is courtesy.
3. **Verify workspace version is bumped.** Run
   `make check-version-sites TAG=vX.Y.Z`; it compares every version
   site in the tree against the tag and names the ones that disagree.
   `make list-version-sites` prints the full list. Between releases
   main carries the next version with a prerelease suffix, so this step
   is normally a matter of dropping that suffix rather than choosing a
   new number.

   The tag pipeline runs the same comparison inside `release:validate`,
   so a tag cut against an unbumped tree fails before `release:crates`
   and nothing reaches a registry. Running it here is how you find out
   before the tag exists rather than after.
4. **Preview release notes** with `make changelog-release VERSION=vX.Y.Z`.
   The output `CHANGELOG.md` is gitignored; it lets you sanity-check
   what the GitLab Release page will say before tagging. The render
   should contain exactly one `## [` section, for this release alone --
   `make changelog-release VERSION=vX.Y.Z STRIP=all OUTPUT=-` prints it
   to stdout so you can count. Zero sections means an rc tag in the
   range is being treated as a release boundary when it should not be;
   more than one means the range leaked an earlier release's commits
   in. `make check-release-notes` is the automated version of this same
   check, run against every past release in `verify:policy`. If a
   conventional-commit subject was poorly worded, fix it on the
   relevant feature branch and re-merge before cutting the tag.
5. **Run the release checks locally** before pushing with
   `make check-release` (or `make preflight-release`, the same thing
   under a preflight-shaped name; it is deliberately kept out of plain
   `make preflight`). It is the same `make -k check-release` that
   `release:validate` runs in CI, so it needs the same tooling that
   `.gitlab/ci/setup.yml` installs for that job:

       uv tool install maturin --with ziglang
       uv tool install 'twine>=6.1'
       bash scripts/install-cargo-tools.sh --only cargo-zigbuild
       rustup target add aarch64-unknown-linux-gnu
       export PATH="${HOME}/.local/share/uv/tools/maturin/bin:${PATH}"

   The `PATH` export is not optional. One of the three cdylib artefacts
   is an aarch64 wheel cross-compiled with zig as the linker, and
   `cargo-zigbuild` locates zig by running `python3 -m ziglang`, so the
   `python3` first on `PATH` has to be maturin's own venv python, which
   is where `--with ziglang` put the package. Without it the run fails
   at `Failed to find zig`, after the native wheel has already built.

## Cutting a release

### One-time setup (per GitLab project)

1. **Settings → Repository → Protected tags** -- add pattern `v*`,
   allowed to create: Maintainers. This restricts who can push tags
   manually and ensures the CI-created tag is protected.
2. **Settings → CI/CD → Variables** -- add `GITLAB_API_TOKEN` (project
   access token, `api` scope, masked). `write_repository` is the minimum
   needed to push tags; full `api` is simpler to configure. Used only by
   `release:auto-tag` to look up the merged MR and push the tag.
3. **Settings → Repository → Protected branches** -- protect `main`,
   `dev` and `release/*` (push and merge: Maintainers), force push off
   on all three. `main` takes direct pushes for the reopening bump only,
   which `scripts/check-main-direct-push.sh` enforces in CI. Protecting
   `release/*` is what runs the full CI tier, hardware included, on a
   release candidate. The table and the reasoning are in
   [`docs/guide/gitflow-protocol.md`](docs/guide/gitflow-protocol.md).
4. **Settings → Merge requests** -- squash commits when merging:
   "Allow, off by default". A release merge must not squash, and under
   "Require" it cannot be stopped. Merge request title pattern: the
   Conventional Commits grammar, so a bad title is rejected on the form
   before a pipeline has to catch it.

The project's approval rule is one approval from any member, and an
author cannot approve their own merge request. `.gitlab/CODEOWNERS` is
advisory -- code owner approval is off on every protected branch.

### Release MR flow (standard)

There are two long-lived lines, and the first decision is which one this
release comes from: a **patch** (`vX.Y.Z`, Z > 0) is cut from `main`, the
non-breaking line, and a **minor** (`vX.Y.0`) is cut from `dev`, the
breaking one. The source line freezes until the release merges. Both
kinds merge into `main`. The protocol behind this -- routing, the freeze,
the back-merge -- is in
[`docs/guide/gitflow-protocol.md`](docs/guide/gitflow-protocol.md) and is
not restated here.

```sh
# 1. Create a release branch from its line: origin/main for a patch,
#    origin/dev for a minor. The branch name must match release/vX.Y.Z
#    exactly -- the CI auto-tag job matches the merge SHA against the MR
#    API to find this branch name.
git fetch origin && git switch -c release/vX.Y.Z origin/main   # or origin/dev

# 2. Set every version site, then regenerate the lockfiles.
#    `make set-version` writes all of them, each in its own ecosystem's
#    spelling; `make list-version-sites` prints them. That list lives in
#    scripts/check-version-sites.py and is not repeated here, so the
#    prose cannot fall behind the check. In shape it is: every crate
#    manifest, the two workspace dependency aliases in Cargo.toml, every
#    pyproject [project] version, xqvm_py/__init__.py, every `==X.Y.Z`
#    peer pin including xquad's optional-dependencies, and the pallet
#    fixture's lock entry.
make set-version VERSION=X.Y.Z
#    Then regenerate: `cargo check` (Cargo.lock) and `uv lock` (uv.lock).
#    `uv lock` is not optional bookkeeping here -- `make check-uv-lock`
#    (`uv lock --check`) runs in `verify:python` and `preflight-py` and
#    fails the moment uv.lock is stale against any pyproject.toml, so a
#    version bump that forgets it fails CI on this branch rather than
#    drifting silently. Also run `cargo update -p xqvm
#    --manifest-path fixtures/pallet-xqvm/Cargo.toml` (standalone
#    workspace with its own lock, which check-uv-lock does not reach).
#    That one is enforced too, but by a different guard: the fixture
#    takes xqvm by path, and `make test-substrate-fixture` builds it with
#    `cargo test --locked`, so skipping this `cargo update` hard-fails
#    test:substrate in CI and `make preflight-rs` locally with a lockfile
#    error rather than drifting until the version-site guard notices.
#    Then confirm: `make check-version-sites TAG=vX.Y.Z`.
git commit -s -am "chore: bump workspace to X.Y.Z"

#    Both lines carry a `-dev` version between releases -- `main` the
#    next patch, `dev` the next minor -- so this step usually just drops
#    the suffix. The suffix no longer carries the resolution argument it
#    was introduced with: `check-crate-publish` packages every workspace
#    member into a scratch tree and resolves each against the locally
#    packaged siblings rather than against crates.io, so a workspace
#    version that names an already-published release can no longer pull
#    a sibling from crates.io in place of the local source. See the
#    `check-crate-publish` comment in the Makefile for what that target
#    does and why. It is now enforced rather than conventional:
#    `check-version-sites` fails on `main` or `dev` when either carries a
#    release version, which is what the reopening step below answers.
#
#    The two ecosystems spell prereleases differently and always have:
#    Cargo wants SemVer (`0.4.0-dev`, `0.3.0-rc1`), Python wants PEP 440
#    (`0.4.0.dev0`, `0.3.0rc1`). One version number, two spellings.

# 3. Push and open an MR using the "release" template.
git push -u origin release/vX.Y.Z
```

Open the MR with the `release` template and tick the release type:
patch from `main` or minor from `dev`. Title it `release: vX.Y.Z`. The
title is checked against the commit grammar twice: by the title pattern
on the merge request form, and by `verify:policy` through
`scripts/check-mr-title.sh`. `release` is a type in
`scripts/commit-grammar.sh`, and git-cliff drops it.

Because a release merges unsquashed, the title is not a commit subject.
It reaches `main` only inside the merge commit's message, below a
GitLab-generated `merge: branch 'release/vX.Y.Z' into 'main'` subject
that git-cliff skips, so a title edited after the last pipeline cannot
remove anything from the release notes.

`verify:policy` also gates the merge on containment: the release branch
must contain `origin/main`, or it would ship without everything `main`
fixed since it was cut. A minor release branch cut from a `dev` that is
missing a back-merge fails here, and the fix is to back-merge `main`
into it.

Open the MR targeting `main`. It needs the project's one approval; the
template's `/assign_reviewer` puts both @kleczkowski and
@meganathanmanish on it, and review from both is expected of a release
rather than enforced -- a two-of-two pool cannot be enforced while an
author cannot approve their own merge request.

**Merge it without squashing.** A squashed release loses every commit
behind it -- a minor loses everything from `dev`, a patch the fixes
that landed on the release branch -- and the release notes come out
empty with nothing reporting it. `release:auto-tag` would still find
the merge, since it matches `CI_COMMIT_SHA` against both
`squash_commit_sha` and `merge_commit_sha`; the automation tolerates
either, the policy does not.

The merge triggers `release:auto-tag` on `main`, which pushes tag
`vX.Y.Z`. The tag then fires the rest of the release pipeline.

After the tag, two follow-ups, and CI marks each one outstanding until
it lands:

1. **Reopen `main`** at the next patch's `-dev` version, by direct
   push, following step 5 of "Cutting a release" in
   [`docs/guide/gitflow-protocol.md`](docs/guide/gitflow-protocol.md).
   Until it lands, `release:validate` fails on `main`'s pipelines,
   because `main` carries a release version. The commit must change
   the version and nothing else, or `verify:policy` fails on `main`.
2. **Back-merge `main` into `dev`**, by direct push, following the
   recipe in [`docs/guide/gitflow-protocol.md`](docs/guide/gitflow-protocol.md)
   under "Back-merging". After a minor this is also where `dev` takes the
   next minor's `-dev` version. Until it lands, `dev`'s pipelines carry a
   standing red from `check-branch-containment`.

`release:auto-tag` carries `needs: []`, so it does not wait for the
merge commit's own `verify` / `test` / `hardware` / `docs` jobs -- the
tag lands within seconds of the merge, not an hour later. That is
deliberate: the publish gate is `release:validate` on the **tag**
pipeline, which every publishing job hangs off by `needs:`. Waiting
here would only add a third run of a check that has already passed on
this MR, while leaving the tag itself hostage to a flaky hardware job
on the merge commit -- a release that never happens and never goes
red. See the job's comment in `.gitlab/ci/release.yml`.

### Legacy manual flow (fallback)

If you need to tag without a release MR -- a release candidate, or a
recovery when auto-tagging failed:

```sh
# Bump every version site to X.Y.Z first -- step 2 of the release MR
# flow above. This path has no MR and no review, so nothing else will
# catch an unbumped tree before the tag exists.
make check-version-sites TAG=vX.Y.Z

git tag -s vX.Y.Z -m "xquad vX.Y.Z"
git push origin vX.Y.Z
```

The tag push triggers the release pipeline identically, including the
same version-site comparison inside `release:validate`. A tag cut
against an unbumped tree therefore fails ahead of `release:crates` with
nothing published: delete the tag, bump, and re-cut, as under "If a tag
does get cut against a red tree" above.

Release candidates take this path, and the bump is not optional for
them either: the published version comes from the tree, so the tagged
commit has to carry `X.Y.Z-rcN` (Cargo) / `X.Y.ZrcN` (Python). Cut one
the way a beta is cut -- a throwaway branch off `release/vX.Y.Z`,
`make set-version VERSION=X.Y.Z-rcN` with the lockfiles regenerated, the
tag, and the branch discarded unmerged. The release branch itself stays
at `X.Y.Z` throughout, so nothing has to be bumped back. The procedure
is in [`docs/guide/gitflow-protocol.md`](docs/guide/gitflow-protocol.md)
under "Pre-release snapshots".

A prerelease tag (`-rcN` or `-betaN`) runs the full pipeline through
`release:crates` and `release:pypi` -- both share the `.on-release-tag`
rule, so its artefacts publish the same as any other tag -- but
`release:notes` and `docs:publish` match `cliff.toml`'s `tag_pattern`
instead, three numeric fields and nothing else. A prerelease publishes
crates and wheels with no GitLab Release page and no documentation.
`tag_pattern` does not treat a prerelease as a release boundary either:
its commits fold into the following release's notes instead of getting
a page of their own that the real release would then have to absorb a
second time.

---

The tag push triggers a fully-automatic pipeline in stage `release`:

1. **`release:validate`** -- the same job that ran on the release MR's
   own pipeline, using the same setup `release:pypi` uses. `make -k
   check-release` checks every version site against `$CI_COMMIT_TAG`,
   dry-runs all three crates, and builds, `twine check`s and
   smoke-installs all five Python distributions against the tagged
   commit. The version check is the one part that does nothing on the
   MR pipeline, where there is no tag to compare against, so the tag
   pipeline is the first run that exercises it.
   Nothing is uploaded; this is the gate that catches manifest
   / license / metadata regressions before any registry sees them --
   and because it already ran green on the MR, a broken release setup
   fails there instead of mid-release. Its last step is a HEAD request
   against `RELEASE_CLI_URL`, the same pinned URL `release:notes`
   fetches `release-cli` from below -- see that job's entry for why.
2. **`release:crates`** -- `cargo publish` for `xqvm` → `xqasm` →
   `xqcli`, in topological order. Fires automatically once
   `release:validate` passes.
3. **`release:pypi`** -- `maturin build` + `twine upload` (OIDC) for
   `xqffi`, then `uv build` + `twine upload` for `xqvm_py` / `xqcp` /
   `xqsa` / `xquad`. Fires automatically once `release:crates` passes
   (`needs:` enforces ordering so PyPI cannot run before crates.io).
4. **`release:notes`** -- git-cliff renders the GitLab Release page
   from the conventional-commit history, scoped to the range between
   this tag and its nearest release predecessor, so the page carries
   exactly this release's own section rather than every release
   reachable from history. Skipped entirely on a prerelease tag (see
   "Release candidates" above). Runs after `release:pypi` so the announcement
   page goes live only once all artefacts are on the registries. It
   fetches `release-cli` from the same `RELEASE_CLI_URL` that
   `release:validate` probes on every pipeline, so a 404 or an
   unreachable registry now surfaces on a merge request instead of
   here, last in the tag-only publish chain. It is also the last job,
   so artefacts are already live and a re-run is safe.
5. **`docs:publish`** -- publishes the book this tag carries, on a
   release tag only. `main` is a working branch, so the published book
   always describes the latest release.

Watch the pipeline. If `release:pypi` fails, rerun only that job:
`twine upload --skip-existing` makes a re-run a no-op for anything
already on PyPI. If `release:crates` fails partway, do **not** simply
rerun it -- cargo has no `--skip-existing`, so the retry dies on the
first crate it already published; publish the remaining crates by
hand from the tagged commit instead. Do not retag unless the failure
was a version mistake.
If `release:validate` fails, no registry has been touched; fix the
underlying issue, force-push to the tag's commit (or move the tag),
and rerun the pipeline.

## Post-flight

1. **Verify on the registries.** All four crates (xqvm, xqasm, xqcli,
   and -- eventually, once we publish it -- xqffi's cdylib) should show
   `vX.Y.Z` within a minute of pipeline completion; all five Python
   distributions (`xqffi`, `xqvm_py`, `xqcp`, `xqsa`, `xquad`) on
   PyPI within seconds.
2. **Smoke-test the install.** In a fresh venv on your workstation:

   ```sh
   python3.13 -m venv /tmp/xquad-smoke
   source /tmp/xquad-smoke/bin/activate
   pip install "xquad==X.Y.Z"
   python -c "import xquad; from xquad import vm, asm; v = vm.VM(); print('ok')"
   ```

3. **Yank superseded broken releases.** If this release exists to
   replace an unusable one -- v0.3.1's Python wheels carried no
   importable package directory (QUI-1020) -- yank the old version once
   the smoke test above passes: the *Releases* tab under
   `pypi.org/manage/project/<name>/`, and `cargo yank --version X.Y.Z
   <crate>` for crates.io. A yank keeps a resolver from selecting the
   broken version for a fresh install. It does **not** move anyone who
   already installed it, and it does not affect an existing environment
   that already satisfies a requirement -- the exact peer pins in each
   `pyproject.toml` are what force those forward.
4. **Notify the pallet team** if this was a major bump they're
   blocked on.

## Trouble-shooting

- **`cargo publish` fails with "version already exists":** someone
  already published that version. Either bump to the next one or
  retag to the existing commit on the registry side (rare).
- **`twine upload` fails with 400 File already exists:** partial
  previous upload. The `--skip-existing` flag should make re-runs a
  no-op; if not, check PyPI and either bump the version or delete the
  uploaded file (within 24h) and retry.
- **`release:pypi` runs but `pip install xquad` still fails:** PyPI
  index propagation can take a few minutes for the *first* release of
  a new package name. Retry after 5 min before digging further.
- **`release:validate` is green but the publish jobs still fail on the
  tag:** `release:validate` runs in check mode and needs no registry
  token on any ref, so a green run there does not prove the publish
  jobs have what they need. `CARGO_REGISTRY_TOKEN` is a masked,
  **protected** project variable, so it is exposed only on protected
  refs -- an MR pipeline never sees it. The PyPI OIDC `id_tokens` are
  minted only for `release:pypi`, the one job carrying `environment:
  release`, which is itself restricted to protected `v*` tags. Check
  that `CARGO_REGISTRY_TOKEN` has `Protected: yes`, that the tag
  matches the protected `v*` pattern, and that each PyPI project's
  Trusted Publisher lists `env = release`.

## What this pipeline does *not* do yet

- **macOS and Windows prebuilt wheels.** `pip install xquad` ships
  prebuilt abi3 wheels for linux-x86_64 and linux-aarch64 (CPython
  >= 3.13). macOS and Windows users can still install from PyPI --
  pip falls back to the published sdist and builds `xqffi` from
  source, which requires a Rust toolchain (rustc >= 1.85). Native
  macOS/Windows wheels would eliminate that requirement but need
  platform-specific CI runners.
- **Automated version selection.** `make set-version` writes every site,
  but which version comes next, and regenerating the three lockfiles
  after it, are still by hand.
- **Signed tags + signed artefacts.** Tags are expected to be git-
  signed (`git tag -s`); crates.io / PyPI artefact signing (sigstore
  cosign, PEP 740) is not wired. Tracked separately.
