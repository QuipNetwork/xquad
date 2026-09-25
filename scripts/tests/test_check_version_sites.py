# Copyright (C) 2026 Postquant Labs Incorporated
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Behavioural tests for scripts/check-version-sites.py.

The cases that matter are whole-tree states: a release version bumped
everywhere but one site, a manifest that arrived without a table entry, a
tag whose two ecosystem spellings disagree. None of them is reachable by
calling a function, and none can be produced in the real repository
without editing it, so each test writes a small but complete manifest
tree under `tmp_path` and drives the script through `subprocess`.

Two tests do run against the real repository, because they are the ones a
fixture cannot express: that every path in the site table exists, and that
the tree as committed is self-consistent. They deliberately assert on
*paths*, never on the version string the repository currently carries --
otherwise every release MR would have to edit this file, which is exactly
the drift the single site table exists to prevent.

The pure functions (PEP 440 canonicalisation, SemVer validation, PEP 508
splitting) are imported and tested in-process instead. They carry the
"two spellings, one version" rule, so they earn table-driven cases rather
than a process launch each.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "check-version-sites.py"
REPO_ROOT = Path(__file__).resolve().parents[2]

CARGO_DEV = "0.4.0-dev"
PEP_DEV = "0.4.0.dev0"
CARGO_RC = "0.4.0-rc1"
PEP_RC = "0.4.0rc1"


def _load_guard():
    """Import the guard as a module, for the pure functions.

    A hyphenated filename with no `.py`-importable name, so it is loaded by
    path -- the same approach scripts/tests/test_gen_example_docs.py uses
    for the generators.
    """
    spec = importlib.util.spec_from_file_location("check_version_sites", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: @dataclass resolves annotations through
    # sys.modules[cls.__module__], which is None for a module loaded by path
    # alone, and raises while processing the first frozen dataclass.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


guard = _load_guard()


def run_guard(root: Path, *args: str, tag: str | None = None) -> subprocess.CompletedProcess[str]:
    """Invoke the guard against `root` with a cleared CI environment.

    CI_COMMIT_TAG is blanked rather than inherited: this suite's own job
    runs inside a pipeline that may set it, and a test that does not name a
    tag is asserting about its absence.
    """
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "CI_COMMIT_TAG": tag or ""}
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        capture_output=True,
        text=True,
        env=env,
    )


# --- Fixture tree ----------------------------------------------------------

_CRATE = '[package]\nname = "{name}"\nversion = "{cargo}"\nedition = "2024"\n'


def write_tree(root: Path, cargo: str = CARGO_DEV, pep: str = PEP_DEV) -> Path:
    """Write a complete, self-consistent manifest tree at `cargo` / `pep`.

    Small but structurally real: it carries the third-party dependency
    specifiers the guard must ignore, the two excluded fixtures, and the
    two dynamic-version packages, so a test can assert the guard leaves
    them alone rather than merely not tripping over them.
    """

    def write(rel: str, text: str) -> None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    write(
        "Cargo.toml",
        "[workspace]\n"
        'members = [ "xqvm", "xqasm", "xqcli", "xqffi", "fixtures/xqvm-wasm" ]\n'
        'exclude = [ "fixtures/pallet-xqvm" ]\n'
        "\n"
        "[workspace.dependencies]\n"
        f'xqvm  = {{ path = "./xqvm", version = "{cargo}" }}\n'
        f'xqasm = {{ path = "./xqasm", version = "{cargo}" }}\n'
        'clap      = { version = "4", features = [ "derive" ] }\n'
        'thiserror = { version = "2", default-features = false }\n',
    )
    for name in ("xqvm", "xqasm", "xqcli", "xqffi"):
        write(f"{name}/Cargo.toml", _CRATE.format(name=name, cargo=cargo))
    # The version-less path-only dev-dep that must never gain a version.
    write(
        "xqvm/Cargo.toml",
        _CRATE.format(name="xqvm", cargo=cargo) + '\n[dev-dependencies]\nxqasm = { path = "../xqasm" }\n',
    )

    write("fixtures/pallet-xqvm/Cargo.toml", _CRATE.format(name="pallet-xqvm", cargo="0.1.0"))
    write("fixtures/pallet-xqvm/Cargo.lock", f'[[package]]\nname = "xqvm"\nversion = "{cargo}"\n')
    write("fixtures/xqvm-wasm/Cargo.toml", _CRATE.format(name="xqvm-wasm", cargo="0.0.0"))

    write("pyproject.toml", '[tool.uv.workspace]\nmembers = [ "xqffi", "xqvm_py", "xqcp", "xqsa", "xquad" ]\n')
    write("xqffi/pyproject.toml", '[project]\nname = "xqffi"\ndynamic = [ "version" ]\n')
    write(
        "xqvm_py/pyproject.toml",
        f'[project]\nname = "xqvm_py"\ndynamic = [ "version" ]\ndependencies = [\n    "xqffi=={pep}",\n]\n',
    )
    write("xqvm_py/__init__.py", f'__version__ = "{pep}"\n')
    write(
        "xqcp/pyproject.toml",
        f'[project]\nname = "xqcp"\nversion = "{pep}"\ndependencies = [\n    "xqvm_py=={pep}",\n]\n',
    )
    write(
        "xqsa/pyproject.toml",
        f'[project]\nname = "xqsa"\nversion = "{pep}"\n'
        f'dependencies = [\n    "xqvm_py=={pep}",\n    "dwave-samplers>=1.0",\n]\n'
        "\n[project.optional-dependencies]\n"
        'quip = [ "substrate-interface>=1.7.4,<2", "quip-signer>=0.2.2" ]\n',
    )
    write(
        "xquad/pyproject.toml",
        f'[project]\nname = "xquad"\nversion = "{pep}"\n'
        f'dependencies = [\n    "xqffi=={pep}",\n    "xqcp=={pep}",\n    "xqsa=={pep}",\n    "xqvm_py=={pep}",\n]\n'
        "\n[project.optional-dependencies]\n"
        f'cuda  = [ "xqsa[cuda]=={pep}" ]\n'
        f'dwave = [ "xqsa[dwave]=={pep}" ]\n'
        f'metal = [ "xqsa[metal]=={pep}" ]\n'
        f'quip  = [ "xqsa[quip]=={pep}" ]\n',
    )

    # xqffi and xqvm-py carry no version: uv records none for a dynamic
    # package, and the guard asserts that absence.
    write(
        "uv.lock",
        f'[[package]]\nname = "xqcp"\nversion = "{pep}"\nsource = {{ editable = "xqcp" }}\n\n'
        f'[[package]]\nname = "xqsa"\nversion = "{pep}"\nsource = {{ editable = "xqsa" }}\n\n'
        f'[[package]]\nname = "xquad"\nversion = "{pep}"\nsource = {{ editable = "xquad" }}\n\n'
        '[[package]]\nname = "xqffi"\nsource = { editable = "xqffi" }\n\n'
        '[[package]]\nname = "xqvm-py"\nsource = { editable = "xqvm_py" }\n',
    )
    write("scripts/python-packages.sh", "CDYLIB=xqffi\nPEERS=(xqvm_py xqcp xqsa xquad)\n")
    return root


def patch(root: Path, rel: str, old: str, new: str) -> None:
    """Replace `old` with `new` in one fixture file, asserting it was there."""
    path = root / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{rel}: {old!r} not present"
    path.write_text(text.replace(old, new), encoding="utf-8")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A self-consistent tree at the -dev version main carries between releases."""
    return write_tree(tmp_path)


@pytest.fixture
def bumped(tmp_path: Path) -> Path:
    """A self-consistent tree bumped to the 0.4.0-rc1 release candidate."""
    return write_tree(tmp_path, cargo=CARGO_RC, pep=PEP_RC)


# --- The tag comparison ----------------------------------------------------


def test_no_tag_is_a_no_op(tree: Path) -> None:
    """With no tag the guard says so and passes, leaving MR pipelines alone."""
    result = run_guard(tree)
    assert result.returncode == 0
    assert "no tag to check" in result.stdout


def test_no_tag_ignores_a_disagreeing_site(tree: Path) -> None:
    """The version comparison is genuinely tag-gated, not merely quiet."""
    patch(tree, "xqvm_py/__init__.py", PEP_DEV, "9.9.9")
    assert run_guard(tree).returncode == 0


def test_release_candidate_passes_against_its_own_spellings(bumped: Path) -> None:
    """v0.4.0-rc1 against 0.4.0-rc1 / 0.4.0rc1 is the release candidate flow."""
    result = run_guard(bumped, tag="v0.4.0-rc1")
    assert result.returncode == 0, result.stderr
    assert "22 sites at 0.4.0-rc1 / 0.4.0rc1" in result.stdout


def test_python_site_in_cargo_spelling_fails(bumped: Path) -> None:
    """0.4.0-rc1 is PEP 440 equivalent to 0.4.0rc1 but is the wrong wheel filename."""
    patch(bumped, "xqcp/pyproject.toml", f'version = "{PEP_RC}"', f'version = "{CARGO_RC}"')
    result = run_guard(bumped, tag="v0.4.0-rc1")
    assert result.returncode == 1
    assert "disagrees with 1 of 22" in result.stderr
    assert f"xqcp/pyproject.toml:3: project version: found {CARGO_RC}, expected {PEP_RC}" in result.stderr


def test_release_tag_against_dev_tree_fails(tree: Path) -> None:
    """The live gap: tagging v0.4.0 against a -dev main would publish 0.4.0-dev."""
    result = run_guard(tree, tag="v0.4.0")
    assert result.returncode == 1
    assert "disagrees with 22 of 22" in result.stderr
    for expected in (
        "Cargo.toml:6: workspace dependency xqvm",
        "xqffi/Cargo.toml:3: package version",
        "xqvm_py/__init__.py:1: __version__",
        "xquad/pyproject.toml:12: peer pin xqsa[cuda]",
        "fixtures/pallet-xqvm/Cargo.lock:3: locked version xqvm",
    ):
        assert expected in result.stderr
    # The remediation block explains why, not only what: the irreversibility
    # is the fact that decides what a release engineer does next.
    assert "registry allows an unpublish" in result.stderr


@pytest.mark.parametrize(
    ("rel", "old", "new", "count", "site"),
    [
        ("xqvm_py/__init__.py", PEP_RC, PEP_DEV, 1, "xqvm_py/__init__.py:1: __version__"),
        ("xquad/pyproject.toml", f'"xqcp=={PEP_RC}"', f'"xqcp=={PEP_DEV}"', 1, "peer pin xqcp"),
        ("xquad/pyproject.toml", f"xqsa[cuda]=={PEP_RC}", f"xqsa[cuda]=={PEP_DEV}", 1, "peer pin xqsa[cuda]"),
        ("fixtures/pallet-xqvm/Cargo.lock", CARGO_RC, CARGO_DEV, 1, "fixtures/pallet-xqvm/Cargo.lock:3"),
    ],
)
def test_partial_bump_reports_exactly_the_stale_site(
    bumped: Path, rel: str, old: str, new: str, count: int, site: str
) -> None:
    """A site missed by the bump fails, and no other site is implicated.

    twine's --skip-existing makes a stale Python manifest a silent no-op at
    upload time, so a partial bump is the failure mode with no other guard.
    """
    patch(bumped, rel, old, new)
    result = run_guard(bumped, tag="v0.4.0-rc1")
    assert result.returncode == 1
    assert f"disagrees with {count} of 22" in result.stderr
    assert site in result.stderr


def test_every_xquad_pin_missed_reports_four_sites(bumped: Path) -> None:
    """The pins are one edit a bump can miss wholesale, separately from [project] version."""
    patch(
        bumped,
        "xquad/pyproject.toml",
        f'dependencies = [\n    "xqffi=={PEP_RC}"',
        f'dependencies = [\n    "xqffi=={PEP_DEV}"',
    )
    for dist in ("xqcp", "xqsa", "xqvm_py"):
        patch(bumped, "xquad/pyproject.toml", f'"{dist}=={PEP_RC}"', f'"{dist}=={PEP_DEV}"')
    result = run_guard(bumped, tag="v0.4.0-rc1")
    assert result.returncode == 1
    assert "disagrees with 4 of 22" in result.stderr
    assert "project version" not in result.stderr


def test_unrelated_stale_sites_are_all_reported(bumped: Path) -> None:
    """Findings accumulate; the guard never stops at the first."""
    patch(bumped, "xqvm/Cargo.toml", f'version = "{CARGO_RC}"', f'version = "{CARGO_DEV}"')
    patch(bumped, "xqvm_py/__init__.py", PEP_RC, PEP_DEV)
    result = run_guard(bumped, tag="v0.4.0-rc1")
    assert result.returncode == 1
    assert "disagrees with 2 of 22" in result.stderr


def test_environment_tag_is_used_when_no_argument_is_given(bumped: Path) -> None:
    """CI supplies the tag through $CI_COMMIT_TAG, not through argv."""
    assert run_guard(bumped, tag="v0.4.0-rc1").returncode == 0
    assert run_guard(bumped, tag="v0.4.0").returncode == 1


def test_positional_tag_overrides_the_environment(bumped: Path) -> None:
    """The positional argument is what makes the guard runnable by hand."""
    assert run_guard(bumped, "v0.4.0-rc1", tag="v9.9.9").returncode == 0


def test_empty_positional_falls_back_to_the_environment(bumped: Path) -> None:
    """The Makefile spelling: `check-version-sites` passes a quoted "$(TAG)" always.

    With TAG unset that is one empty argument, not none, so argparse sees ""
    rather than None. Reading it as a supplied-but-empty tag made the guard a
    no-op on every tag pipeline -- the one place it exists to run. The case
    above passes no positional at all and so stayed green throughout.
    """
    assert run_guard(bumped, "", tag="v0.4.0-rc1").returncode == 0
    assert run_guard(bumped, "", tag="v0.4.0").returncode == 1


def test_bare_version_without_the_v_prefix_is_accepted(bumped: Path) -> None:
    """Local ergonomics: `make check-version-sites TAG=0.4.0-rc1` behaves the same."""
    assert run_guard(bumped, "0.4.0-rc1").returncode == 0


def test_non_release_tag_is_skipped(tree: Path) -> None:
    """release:validate sees every tag, but only v<digit> tags can publish."""
    result = run_guard(tree, tag="docs-2026-08")
    assert result.returncode == 0
    assert "is not a release tag" in result.stdout


@pytest.mark.parametrize("tag", ["2026.08.27", "1.0-freeze", "1.2.3"])
def test_bare_digit_tag_from_the_environment_is_skipped(bumped: Path, tag: str) -> None:
    """A pushed ref publishes only if it matches `.on-release-tag` (/^v\\d/).

    Date-shaped, milestone-shaped and even well-formed `1.2.3` tags all fail
    that pattern, so nothing publishes from them and release:validate -- which
    carries no `rules:` and therefore sees them -- must not go red. The bare
    spelling stays accepted on the explicit argument, one case above: that is
    a releaser typing, not a ref.
    """
    result = run_guard(bumped, "", tag=tag)
    assert result.returncode == 0, result.stderr
    assert "is not a release tag" in result.stdout


@pytest.mark.parametrize(
    ("tag", "blocker"),
    [
        ("v0.4.0-SNAPSHOT", "not expressible as a PEP 440 version"),
        ("v0.4.0-rc.beta", "not expressible as a PEP 440 version"),
        ("v0.4.0.post1", "not a valid SemVer version"),
        ("v0.4.0+build.3", "local version segment"),
    ],
)
def test_tag_with_no_single_version_is_a_setup_error(bumped: Path, tag: str, blocker: str) -> None:
    """A tag matching ^v<digit> triggers the publish chain, so it must stop the pipeline."""
    result = run_guard(bumped, tag=tag)
    assert result.returncode == 2
    assert blocker in result.stderr


# --- The table sweeps ------------------------------------------------------


def test_new_manifest_must_be_declared_or_excluded(tree: Path) -> None:
    """A crate added without a table entry is caught on its own MR, not at tag time."""
    (tree / "xqnew").mkdir()
    (tree / "xqnew/Cargo.toml").write_text(_CRATE.format(name="xqnew", cargo=CARGO_DEV), encoding="utf-8")
    result = run_guard(tree)
    assert result.returncode == 2
    assert "xqnew/Cargo.toml: neither declared in SITES nor listed in EXCLUDED" in result.stderr


@pytest.mark.parametrize(
    ("cache_dir", "manifest"),
    [
        (".cargo", "registry/src/index.crates.io-1949cf8c6b5b557f/serde-1.0.228/Cargo.toml"),
        (".uv-cache", "sdists-v9/pypi/some-dist/1.2.3/src/pyproject.toml"),
        (".orca", "worktrees/xquad/qui-1146/Cargo.toml"),
        (".claude", "worktrees/qui-1146/Cargo.toml"),
    ],
)
def test_in_tree_dependency_caches_are_not_swept(tree: Path, cache_dir: str, manifest: str) -> None:
    """CI and the worktree tools both put foreign manifests inside the tree.

    CARGO_HOME and UV_CACHE_DIR point under ${CI_PROJECT_DIR} so the caches
    survive as job artefacts, and per-ticket worktrees land under .orca/ or
    .claude/ depending on which tool cut them -- each a full checkout, and so
    a second copy of every declared site. All four are absent from a clean
    clone, so the sweep passes locally in a bare checkout and fails in exactly
    the environments that run this guard until they are pruned.
    """
    path = tree / cache_dir / manifest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_CRATE.format(name="vendored", cargo="1.0.0"), encoding="utf-8")
    result = run_guard(tree)
    assert result.returncode == 0, result.stderr


def test_new_peer_pin_must_be_declared(tree: Path) -> None:
    """A pin on a workspace distribution is a version site by definition."""
    patch(tree, "xqsa/pyproject.toml", f'"xqvm_py=={PEP_DEV}",', f'"xqvm_py=={PEP_DEV}",\n    "xqcp=={PEP_DEV}",')
    result = run_guard(tree)
    assert result.returncode == 2
    assert "is not declared in SITES" in result.stderr


def test_third_party_specifiers_are_not_treated_as_pins(tree: Path) -> None:
    """The pin sweep filters by distribution name, which is what spares dwave-samplers."""
    result = run_guard(tree, tag="v0.4.0-dev")
    assert result.returncode == 0, result.stderr
    listing = run_guard(tree, "--list")
    for foreign in ("dwave-samplers", "quip-signer", "substrate-interface", "clap", "thiserror"):
        assert foreign not in listing.stdout


def test_excluded_fixtures_are_left_alone(tree: Path) -> None:
    """The pallet and wasm fixtures carry their own versions and are not release sites."""
    listing = run_guard(tree, "--list")
    assert "fixtures/pallet-xqvm/Cargo.toml" not in listing.stdout
    assert "fixtures/xqvm-wasm/Cargo.toml" not in listing.stdout
    assert run_guard(tree, tag="v0.4.0-dev").returncode == 0


def test_missing_declared_site_is_a_setup_error(tree: Path) -> None:
    """Deleting a site is as much a table change as adding one."""
    (tree / "xqcli/Cargo.toml").unlink()
    result = run_guard(tree)
    assert result.returncode == 2
    assert "xqcli/Cargo.toml: declared in the site table but missing" in result.stderr


def test_literal_version_on_a_dynamic_package_is_a_setup_error(tree: Path) -> None:
    """maturin ignores a literal in xqffi/pyproject.toml, so it would drift forever."""
    patch(tree, "xqffi/pyproject.toml", 'dynamic = [ "version" ]', f'dynamic = [ "version" ]\nversion = "{PEP_DEV}"')
    result = run_guard(tree)
    assert result.returncode == 2
    assert "version is set on a dynamic package" in result.stderr


def test_lock_version_for_a_dynamic_package_is_a_setup_error(tree: Path) -> None:
    """If uv starts recording one, it becomes a version site nothing checks."""
    patch(tree, "uv.lock", 'name = "xqffi"\n', f'name = "xqffi"\nversion = "{PEP_DEV}"\n')
    result = run_guard(tree)
    assert result.returncode == 2
    assert "now records version" in result.stderr


# --- Reporting modes -------------------------------------------------------


def test_list_prints_every_site(tree: Path) -> None:
    result = run_guard(tree, "--list")
    assert result.returncode == 0
    assert result.stdout.rstrip().endswith("22 version sites")


def test_print_version_reports_the_canonical_spelling(tree: Path) -> None:
    """smoke-wheels.sh consumes this to assert the built artefact's version."""
    result = run_guard(tree, "--print-version")
    assert result.returncode == 0
    assert result.stdout.strip() == PEP_DEV


def test_print_version_refuses_a_tree_that_disagrees(tree: Path) -> None:
    patch(tree, "xqvm_py/__init__.py", PEP_DEV, "9.9.9")
    result = run_guard(tree, "--print-version")
    assert result.returncode == 2
    assert "do not agree on one version" in result.stderr


# --- The real repository ---------------------------------------------------


def test_site_table_matches_the_repository() -> None:
    """Every declared path exists here.

    Asserts on paths, never on the version the repository currently
    carries: a value assertion would have to be edited by every release MR,
    which is the drift the single site table exists to prevent.
    """
    result = run_guard(REPO_ROOT, "--list")
    assert result.returncode == 0, result.stderr
    for site in guard.SITES:
        assert (REPO_ROOT / site.path).is_file(), f"{site.path} is declared but missing"
    for path in guard.EXCLUDED:
        assert (REPO_ROOT / path).is_file(), f"{path} is excluded but missing"


def test_repository_passes_with_no_tag() -> None:
    """Goes red the moment a half-bump or an undeclared manifest lands on a branch."""
    result = run_guard(REPO_ROOT)
    assert result.returncode == 0, result.stderr


# --- Pure functions --------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.4.0", "0.4.0"),
        ("0.4.0-rc1", "0.4.0rc1"),
        ("0.4.0rc1", "0.4.0rc1"),
        ("0.4.0-dev", "0.4.0.dev0"),
        ("0.4.0.dev0", "0.4.0.dev0"),
        ("0.4.0.dev", "0.4.0.dev0"),
        ("0.4.0-alpha.1", "0.4.0a1"),
        ("0.4.0a1", "0.4.0a1"),
        ("0.4.0-beta2", "0.4.0b2"),
        ("0.4.0.post1", "0.4.0.post1"),
        ("1!0.4.0", "1!0.4.0"),
        ("0.4.0-SNAPSHOT", None),
        ("nightly", None),
    ],
)
def test_canon(raw: str, expected: str | None) -> None:
    """One canonicaliser, PEP 440's, because that is the parse maturin performs."""
    assert guard.canon(raw) == expected


@pytest.mark.parametrize(
    ("raw", "valid"),
    [
        ("0.4.0", True),
        ("0.4.0-rc1", True),
        ("0.4.0-alpha.1", True),
        ("0.4.0.dev0", False),
        ("0.4.0.post1", False),
        ("v0.4.0", False),
    ],
)
def test_is_semver(raw: str, valid: bool) -> None:
    """Four dotted components are valid PEP 440 and invalid SemVer; a Cargo.toml must fail on them."""
    assert guard.is_semver(raw) is valid


@pytest.mark.parametrize(
    ("raw", "name", "extras", "spec"),
    [
        ("xqvm_py==0.4.0.dev0", "xqvm_py", (), "==0.4.0.dev0"),
        ("xqsa[cuda]==0.4.0.dev0", "xqsa", ("cuda",), "==0.4.0.dev0"),
        ("dwave-samplers>=1.0", "dwave-samplers", (), ">=1.0"),
        ("pyobjc-framework-Metal>=11.0; sys_platform == 'darwin'", "pyobjc-framework-Metal", (), ">=11.0"),
    ],
)
def test_parse_requirement(raw: str, name: str, extras: tuple[str, ...], spec: str) -> None:
    assert guard.parse_requirement(raw) == (name, extras, spec)


def test_distribution_names_are_normalised() -> None:
    """uv.lock writes xqvm-py where the manifests write xqvm_py."""
    assert guard.normalise_dist("xqvm_py") == guard.normalise_dist("xqvm-py") == "xqvm-py"
