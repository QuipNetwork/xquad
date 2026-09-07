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

"""Behavioural tests for scripts/ensure-rust-toolchain.sh.

The script exists to make hardware:metal survive landing on any Apple
Silicon runner in the shared `[macos, arm64]` pool, so the cases worth
testing are the ones a developer's own machine cannot show: a host with
no Rust at all, and a host whose rustup proxies resolve to a toolchain
that is no longer there. Both are reached by handing the script a PATH
built entirely out of stubs, which also keeps the suite offline -- a test
that really fetched sh.rustup.rs would be testing the network.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "ensure-rust-toolchain.sh"

# The stub PATH deliberately excludes the system directories, so the
# script sees exactly the executables a test puts in front of it. Only
# the interpreters and coreutils the script -- or a stub standing in for
# rustup -- actually invokes are borrowed from the host.
BORROWED = ("sh", "bash", "uname", "cat", "mkdir", "chmod", "dirname")

# A stand-in for the rustup installer that sh.rustup.rs serves. Writing
# working proxies into ${CARGO_HOME}/bin is the only part of its
# behaviour this script depends on, so it is the only part reproduced.
FAKE_INSTALLER = """\
mkdir -p "${CARGO_HOME}/bin"
for name in rustc cargo rustup; do
    printf '#!/bin/sh\\necho "%s 1.99.0 (installed)"\\n' "${name}" > "${CARGO_HOME}/bin/${name}"
    chmod +x "${CARGO_HOME}/bin/${name}"
done
"""


# A stand-in for the surviving `rustup` proxy in the stale-cache case:
# `rustup toolchain install` puts a working `rustc` back where the dead
# proxy was. `dirname "$0"` keeps it independent of the temporary
# directory pytest hands the test.
RUSTUP_REPAIR_STUB = r"""dir=$(dirname "$0")
case "$1" in
toolchain)
    printf '#!/bin/sh\necho "rustc 1.99.0 (repaired)"\n' > "$dir/rustc"
    chmod +x "$dir/rustc"
    ;;
esac
exit 0"""


def _stub(directory: Path, name: str, body: str) -> None:
    """Write an executable shell stub called `name` into `directory`."""
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


@pytest.fixture
def stub_path(tmp_path: Path) -> Path:
    """A PATH directory holding only what a test explicitly installs."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in BORROWED:
        for root in ("/bin", "/usr/bin"):
            candidate = Path(root) / name
            if candidate.exists():
                (bindir / name).symlink_to(candidate)
                break
    return bindir


def _run(bindir: Path, cargo_home: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PATH"] = str(bindir)
    env["CARGO_HOME"] = str(cargo_home)
    return subprocess.run(
        ["/bin/bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_accepts_a_host_toolchain_without_installing(stub_path: Path, tmp_path: Path) -> None:
    """A host that already has rustc and cargo is left alone.

    This is the tesla-arm path and the one that must not regress: the
    common case is a runner that is already provisioned, and reinstalling
    rustup there would burn a minute per pipeline for nothing. `curl` is
    stubbed to fail loudly so that an install attempt cannot pass
    silently.
    """
    _stub(stub_path, "rustc", 'echo "rustc 1.90.0 (stub)"')
    _stub(stub_path, "cargo", 'echo "cargo 1.90.0 (stub)"')
    _stub(stub_path, "curl", 'echo "curl must not run" >&2; exit 97')

    result = _run(stub_path, tmp_path / "cargo-home")

    assert result.returncode == 0, result.stderr
    assert "found rustc at" in result.stdout
    assert "curl must not run" not in result.stderr
    assert "rustc 1.90.0 (stub)" in result.stdout


def test_reports_what_it_searched_when_provisioning_fails(stub_path: Path, tmp_path: Path) -> None:
    """A failed install names CARGO_HOME and PATH, and exits 1.

    The whole point of running this ahead of maturin is that maturin's
    own message ("rustc, the rust compiler, is not installed or not in
    PATH") says neither where it looked nor which failure it hit. If this
    script ever degrades to the same output it has stopped earning its
    place in the job.
    """
    cargo_home = tmp_path / "cargo-home"
    _stub(stub_path, "curl", "exit 42")

    result = _run(stub_path, cargo_home)

    assert result.returncode == 1
    assert "rustup installation failed" in result.stderr
    assert "no usable Rust toolchain after provisioning" in result.stderr
    assert str(cargo_home) in result.stderr
    assert "PATH:" in result.stderr


def test_rejects_a_proxy_whose_toolchain_is_gone(stub_path: Path, tmp_path: Path) -> None:
    """`command -v` finding rustc is not enough; it has to run.

    A rustup proxy stays on PATH after its RUSTUP_HOME is cleared, and
    `.cargo/bin/` is restored from cache independently of ${HOME}/.rustup
    -- so a location-only check would hand a broken toolchain to maturin
    and blame the compile.
    """
    _stub(stub_path, "rustc", 'echo "error: toolchain is not installed" >&2; exit 1')
    _stub(stub_path, "cargo", 'echo "cargo 1.90.0 (stub)"')

    result = _run(stub_path, tmp_path / "cargo-home")

    # Deterministically 1 under `set -e` plus the script's explicit exit,
    # so pin the value rather than merely asserting failure.
    assert result.returncode == 1
    assert "toolchain is not installed" in result.stderr
    assert "rustup:      <not found>" in result.stderr


def test_installs_when_the_host_has_no_toolchain(stub_path: Path, tmp_path: Path) -> None:
    """The branch this script exists for, driven end to end offline.

    `curl` is stubbed to emit a stand-in installer, so the real
    `curl | sh` pipeline, the `export PATH="${CARGO_HOME}/bin:${PATH}"`
    that follows it, and the verification after that all execute. Without
    this case the only evidence for the install path is a manual run on
    one machine, which is exactly the kind of evidence this MR is about
    not relying on.
    """
    cargo_home = tmp_path / "cargo-home"
    installer = tmp_path / "rustup-init.sh"
    installer.write_text(FAKE_INSTALLER)
    _stub(stub_path, "curl", f'cat "{installer}"')

    result = _run(stub_path, cargo_home)

    assert result.returncode == 0, result.stderr
    assert "installing 'stable'" in result.stdout
    assert "rustc 1.99.0 (installed)" in result.stdout
    assert "cargo 1.99.0 (installed)" in result.stdout
    assert (cargo_home / "bin" / "rustc").exists()


def test_repairs_a_stale_proxy_through_rustup(stub_path: Path, tmp_path: Path) -> None:
    """A proxy whose toolchain is gone is repaired, not merely reported.

    This is the case the `.cargo/bin/` cache entry creates: proxies are
    restored onto a host whose ${HOME}/.rustup has since been cleared. If
    the script only detected it, the job would fail identically on every
    retry until someone emptied that runner's cache by hand -- the
    single-special-machine failure the whole change exists to remove.

    `curl` is stubbed to fail loudly, because repairing through the
    surviving `rustup` proxy must not re-download the installer, and
    because reinstalling would first require deleting ${CARGO_HOME}/bin
    -- which is a developer's whole cargo-tools directory when this script
    is run by hand.
    """
    _stub(stub_path, "rustc", 'echo "error: toolchain is not installed" >&2; exit 1')
    _stub(stub_path, "cargo", 'echo "cargo 1.90.0 (stub)"')
    _stub(stub_path, "curl", 'echo "curl must not run" >&2; exit 97')
    _stub(stub_path, "rustup", RUSTUP_REPAIR_STUB)

    result = _run(stub_path, tmp_path / "cargo-home")

    assert result.returncode == 0, result.stderr
    assert "repairing" in result.stdout
    assert "rustc 1.99.0 (repaired)" in result.stdout
    assert "curl must not run" not in result.stderr
