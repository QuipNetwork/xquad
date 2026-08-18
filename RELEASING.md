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
| 8 | [`xquad`](xquad/) sdist | PyPI | last — depends on 4-7 |
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

- `CARGO_REGISTRY_TOKEN` — crates.io API token from
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
3. **Verify workspace version is bumped.** Every crate's
   `Cargo.toml` and every Python package's `pyproject.toml` must
   agree on the version being tagged.
4. **Preview release notes** with `make changelog-release VERSION=vX.Y.Z`.
   The output `CHANGELOG.md` is gitignored; it lets you sanity-check
   what the GitLab Release page will say before tagging. If a
   conventional-commit subject was poorly worded, fix it on the
   relevant feature branch and re-merge before cutting the tag.
5. **Run the release checks locally** before pushing with
   `make check-release` (or `make preflight-release`, the same thing
   under a preflight-shaped name; it is deliberately kept out of plain
   `make preflight`). Needs `maturin`, `twine`, and `uv` on `PATH` --
   it is the same `make -k check-release` that `release:validate` runs
   in CI.

## Cutting a release

### One-time setup (per GitLab project)

1. **Settings → Repository → Protected tags** — add pattern `v*`,
   allowed to create: Maintainers. This restricts who can push tags
   manually and ensures the CI-created tag is protected.
2. **Settings → CI/CD → Variables** — add `GITLAB_API_TOKEN` (project
   access token, `api` scope, masked). `write_repository` is the minimum
   needed to push tags; full `api` is simpler to configure. Used only by
   `release:auto-tag` to look up the merged MR and push the tag.
3. **Settings → Merge requests → Approvals** — enable "Require code
   owner approval" for the `main` branch and set approvals required
   to 2. This enforces that all MRs go through both
   `@kleczkowski` and `@meganathanmanish`.

### Release MR flow (standard)

```sh
# 1. Create a release branch. The branch name must match release/vX.Y.Z
#    exactly — the CI auto-tag job matches the merge SHA against the MR
#    API to find this branch name.
git checkout -b release/vX.Y.Z main

# 2. Bump versions in every manifest, then regenerate the lockfiles.
#    Version sites:
#      - Cargo.toml [workspace.dependencies] -- the `version` alongside
#        `path` on xqvm and xqasm. `cargo publish --locked` fails without
#        it; the root has no workspace.package.version to bump.
#      - each crate's Cargo.toml: xqvm, xqasm, xqcli, xqffi, conformance.
#      - each pyproject.toml [project] version: xqcp, xqsa, xquad.
#      - xqvm_py/__init__.py __version__ -- xqvm_py and xqffi declare
#        `dynamic = ["version"]`, so their pyproject carries no version
#        line and hatch reads this file instead.
#      - the `==X.Y.Z` peer pins in xqcp, xqsa, xqvm_py and xquad
#        pyproject.toml, including xquad's optional-dependencies.
#    Then regenerate: `cargo check` (Cargo.lock), `uv lock` (uv.lock), and
#    `cargo update -p xqvm --manifest-path fixtures/pallet-xqvm/Cargo.toml`
#    (standalone workspace with its own lock; no job builds it with
#    --locked, so a stale xqvm entry there drifts silently for releases).
git commit -s -am "chore: bump workspace to X.Y.Z"

# 3. Push and open an MR using the "release" template.
git push -u origin release/vX.Y.Z
```

Title the MR `release: vX.Y.Z`. The project squashes on merge with
`squash_commit_template = %{title}`, so the title becomes a commit
subject and `verify:policy` checks it against the commit grammar --
`release` is a type in `scripts/commit-grammar.sh` for exactly this
reason, and git-cliff drops it. The check first sees that subject on
the merge-train ref, so a non-conforming title passes every pipeline on
the MR itself and fails only once the train has started.

Open the MR targeting `main`. Both @kleczkowski and @meganathanmanish
must approve. After approval, merge using any strategy — squash and
merge commit are both supported. `release:auto-tag` detects the merged
MR by matching `CI_COMMIT_SHA` against both `squash_commit_sha` and
`merge_commit_sha` in the GitLab MR API.

The merge triggers `release:auto-tag` on `main`, which pushes tag
`vX.Y.Z`. The tag then fires the rest of the release pipeline.

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

If you need to tag without a release MR (e.g., hotfix or RC):

```sh
git tag -s vX.Y.Z -m "xquad vX.Y.Z"
git push origin vX.Y.Z
```

The tag push triggers the release pipeline identically.

---

The tag push triggers a fully-automatic pipeline in stage `release`:

1. **`release:validate`** -- the same job that ran on the release MR's
   own pipeline, using the same setup `release:pypi` uses. `make -k
   check-release` dry-runs all three crates and builds, `twine check`s,
   and smoke-installs all five Python distributions against the tagged
   commit. Nothing is uploaded; this is the gate that catches manifest
   / license / metadata regressions before any registry sees them --
   and because it already ran green on the MR, a broken release setup
   fails there instead of mid-release.
2. **`release:crates`** -- `cargo publish` for `xqvm` → `xqasm` →
   `xqcli`, in topological order. Fires automatically once
   `release:validate` passes.
3. **`release:pypi`** -- `maturin build` + `twine upload` (OIDC) for
   `xqffi`, then `uv build` + `twine upload` for `xqvm_py` / `xqcp` /
   `xqsa` / `xquad`. Fires automatically once `release:crates` passes
   (`needs:` enforces ordering so PyPI cannot run before crates.io).
4. **`release:notes`** -- git-cliff renders the GitLab Release page
   from the conventional-commit history. Runs after `release:pypi` so
   the announcement page goes live only once all artefacts are on the
   registries. Its `release-cli` and git-cliff install path is the one
   part of the release chain no pre-tag pipeline exercises; it is also
   the last job, so artefacts are already live and a re-run is safe.

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
   and — eventually, once we publish it — xqffi's cdylib) should show
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
- **Automated version bumps.** No `cargo-release` / `hatch version`
  integration yet; versions are edited by hand per the step above.
- **Signed tags + signed artefacts.** Tags are expected to be git-
  signed (`git tag -s`); crates.io / PyPI artefact signing (sigstore
  cosign, PEP 740) is not wired. Tracked separately.
