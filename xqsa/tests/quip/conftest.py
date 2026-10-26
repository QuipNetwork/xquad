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

"""Shared fixtures for the Quip unit tests."""

from __future__ import annotations

import hashlib
import sys
import time
import types
from pathlib import Path

import pytest

# Every live skip reason starts with one of these: the network failed to do
# something on the day, or config, chain or runtime makes the test inapplicable.
SKIP_LABELS = ("network:", "designed:")


def pytest_terminal_summary(terminalreporter) -> None:
    """Count the live module's skips by label and list any skip with no label."""
    reasons = [
        (report.nodeid, _skip_reason(report))
        for report in terminalreporter.stats.get("skipped", [])
        if "test_live" in report.nodeid
    ]
    if not reasons:
        return
    terminalreporter.section("live skips")
    terminalreporter.write_line(
        ", ".join(f"{label} {sum(reason.startswith(label) for _, reason in reasons)}" for label in SKIP_LABELS)
    )
    for nodeid, reason in reasons:
        if not reason.startswith(SKIP_LABELS):
            terminalreporter.write_line(f"unlabelled: {nodeid}: {reason}")


def _skip_reason(report) -> str:
    """The reason a skip report carries, without pytest's ``Skipped: `` prefix."""
    reason = report.longrepr[-1] if isinstance(report.longrepr, tuple) else str(report.longrepr)
    return reason.removeprefix("Skipped: ")


@pytest.fixture(autouse=True)
def virtual_clock(request, monkeypatch):
    """Make ``time.sleep`` advance ``time.monotonic`` instantly instead of waiting.

    A submit settles its proposal by polling the chain until a deadline, so a
    test whose fake chain never settles would otherwise sleep for minutes.
    The live tier keeps the real clock. Returns the list of sleeps taken.
    """
    if request.module.__name__.endswith("test_live"):
        yield []
        return
    real_monotonic = time.monotonic
    offset = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        offset[0] += seconds

    monkeypatch.setattr(time, "sleep", sleep)
    monkeypatch.setattr(time, "monotonic", lambda: real_monotonic() + offset[0])
    yield sleeps


@pytest.fixture(autouse=True)
def isolated_home(request, monkeypatch, tmp_path_factory):
    """Keep every unit test away from the real ``~/.quip`` and any ``.quip/`` above the repo.

    Signer resolution lists the keystores under the home directory and walks
    up from the working directory, so both move to an empty temporary tree.
    The live tier keeps the real ones.
    """
    if request.module.__name__.endswith("test_live"):
        return
    root = tmp_path_factory.mktemp("isolated")
    monkeypatch.setenv("HOME", str(root / "home"))
    monkeypatch.chdir(root)


def _seed_bytes(seed) -> bytes:
    return bytes.fromhex(seed.removeprefix("0x")) if isinstance(seed, str) else bytes(seed)


class _SeedSigner:
    """A stand-in ``HybridSigner`` whose identity is derived from its seed."""

    def __init__(self, seed) -> None:
        self.seed = _seed_bytes(seed)
        self.account_id = hashlib.sha256(b"account" + self.seed).digest()
        self.public_key = hashlib.sha256(b"public" + self.seed).digest()


@pytest.fixture
def seed_signer(monkeypatch):
    """Install a fake ``quip_signer`` whose signers derive a distinct account per seed.

    Returns the fake module; ``signer.seed`` on any signer it built names the
    seed it came from.
    """
    module = types.ModuleType("quip_signer")
    module.HybridSigner = types.SimpleNamespace(from_seed=_SeedSigner)  # type: ignore[attr-defined]
    module.verify_envelope = lambda *args, **kwargs: True  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "quip_signer", module)
    from xqsa.quip import signing

    # signing binds quip_signer at import, possibly to the real module.
    monkeypatch.setattr(signing, "quip_signer", module)
    return module


@pytest.fixture
def quip_home(monkeypatch, tmp_path):
    """Point ``HOME`` at a temporary directory and work in a project below it.

    Returns ``(home, project)``; the working directory is ``project``, which
    has no ``.quip/`` yet.
    """
    home = tmp_path / "home"
    project = home / "work" / "proj"
    project.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    assert Path.home() == home
    monkeypatch.chdir(project)
    for name in ("QUIP_SIGNER_SEED", "QUIP_KEYSTORE"):
        monkeypatch.delenv(name, raising=False)
    return home, project
