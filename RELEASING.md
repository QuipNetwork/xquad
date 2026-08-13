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
| 9 | GitLab Release notes | GitLab | last — `release:changelog` + `release:notes` |

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

1. **Watch the dry-run jobs.** Every MR pipeline runs
   `release:dry-run:crates` and `release:dry-run:pypi` in the `release`
   stage. If either is red on `main`, do not tag — the real release
   will fail the same way, just with partial artefacts already
   uploaded.
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

# 2. Bump versions in every manifest.
#    Touchpoints: Cargo.toml workspace.package.version, each crate's
#    Cargo.toml, each pyproject.toml [project] version.
git commit -s -am "chore: bump workspace to X.Y.Z"

# 3. Push and open an MR using the "release" template.
git push -u origin release/vX.Y.Z
```

Open the MR targeting `main`. Both @kleczkowski and @meganathanmanish
must approve. After approval, merge using any strategy — squash and
merge commit are both supported. `release:auto-tag` detects the merged
MR by matching `CI_COMMIT_SHA` against both `squash_commit_sha` and
`merge_commit_sha` in the GitLab MR API.

The merge triggers `release:auto-tag` on `main`, which pushes tag
`vX.Y.Z`. The tag then fires the rest of the release pipeline.

### Legacy manual flow (fallback)

If you need to tag without a release MR (e.g., hotfix or RC):

```sh
git tag -s vX.Y.Z -m "xquad vX.Y.Z"
git push origin vX.Y.Z
```

The tag push triggers the release pipeline identically.

---

The tag push triggers a fully-automatic pipeline in stage `release`:

1. **`release:validate`** -- packaging dry-run for all three crates and
   all five Python distributions against the tagged commit. Nothing
   is uploaded; this is the gate that catches manifest / license /
   metadata regressions before any registry sees them.
2. **`release:publish-crates`** -- `cargo publish` for `xqvm` →
   `xqasm` → `xqcli`, in topological order. Fires automatically once
   `release:validate` passes.
3. **`release:publish-pypi`** -- `maturin build` + `twine upload`
   (OIDC) for `xqffi`, then `uv build` + `twine upload` for
   `xqvm_py` / `xqcp` / `xqsa` / `xquad`. Fires automatically
   once `release:publish-crates` passes
   (`needs:` enforces ordering so PyPI cannot run before crates.io).
4. **`release:changelog` → `release:notes`** -- git-cliff renders the
   GitLab Release page from the conventional-commit history. Runs
   after `release:publish-pypi` so the announcement page goes live
   only once all artefacts are on the registries.

Watch the pipeline. If a publish job fails, rerun **only the failed
job** -- both pass `--skip-existing` / `--locked` so re-runs are
idempotent. Do not retag unless the failure was a version mistake.
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
   python -c "import xquad; from xquad import vm, asm; v = vm.Vm(); print('ok')"
   ```

3. **Notify the pallet team** if this was a major bump they're
   blocked on.

## Trouble-shooting

- **`cargo publish` fails with "version already exists":** someone
  already published that version. Either bump to the next one or
  retag to the existing commit on the registry side (rare).
- **`twine upload` fails with 400 File already exists:** partial
  previous upload. The `--skip-existing` flag should make re-runs a
  no-op; if not, check PyPI and either bump the version or delete the
  uploaded file (within 24h) and retry.
- **`release:publish-pypi` runs but `pip install xquad` still fails:** PyPI
  index propagation can take a few minutes for the *first* release of
  a new package name. Retry after 5 min before digging further.
- **Dry-run passes on MR but real release fails:** usually means the
  release job has access to a token the MR pipeline doesn't. Check
  that the token variables have `Protected: yes` and that the tag is
  on a protected tag pattern (`v*`). Protected variables only expose
  to protected refs.

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
