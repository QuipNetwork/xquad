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

"""`import xquad` refuses to run next to the 0.5 xqffi, xqcp and xqsa distributions."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from xquad import _legacy


def _site(tmp_path: Path, *dist_infos: str) -> Path:
    site = tmp_path / "site-packages"
    site.mkdir()
    for name in dist_infos:
        (site / name).mkdir()
    return site


def test_clean_environment_passes(tmp_path: Path) -> None:
    site = _site(tmp_path, "xquad-0.6.0.dist-info", "numpy-2.3.0.dist-info")
    assert _legacy.installed([str(site)]) == []
    _legacy.check([str(site)])


def test_current_environment_passes() -> None:
    # The dev install is the one editable `xquad` distribution; nothing legacy.
    assert _legacy.installed() == []


def test_each_legacy_distribution_is_reported(tmp_path: Path) -> None:
    site = _site(tmp_path, "xqsa-0.5.2.dist-info", "xqffi-0.5.2.dist-info", "xqcp-0.4.5.dist-info")
    assert _legacy.installed([str(site)]) == ["xqffi 0.5.2", "xqcp 0.4.5", "xqsa 0.5.2"]


def test_first_path_entry_wins(tmp_path: Path) -> None:
    first = _site(tmp_path, "xqsa-0.5.2.dist-info")
    second = tmp_path / "other"
    second.mkdir()
    (second / "xqsa-0.5.1.dist-info").mkdir()
    assert _legacy.installed([str(first), str(second)]) == ["xqsa 0.5.2"]


def test_missing_and_unreadable_entries_are_skipped(tmp_path: Path) -> None:
    site = _site(tmp_path, "xqsa-0.5.2.dist-info")
    archive = tmp_path / "python313.zip"
    archive.write_bytes(b"")
    paths = [str(tmp_path / "missing"), str(archive), str(site)]
    assert _legacy.installed(paths) == ["xqsa 0.5.2"]


def test_check_names_the_distributions_and_the_fix(tmp_path: Path) -> None:
    site = _site(tmp_path, "xqsa-0.5.2.dist-info")
    with pytest.raises(ImportError) as excinfo:
        _legacy.check([str(site)])
    message = str(excinfo.value)
    assert message.startswith("xqsa 0.5.2 is installed next to xquad.")
    assert "pip uninstall -y xqffi xqcp xqsa" in message
    assert "pip install --force-reinstall xquad" in message


def test_import_xquad_fails_next_to_a_legacy_distribution(tmp_path: Path) -> None:
    site = _site(tmp_path, "xqffi-0.5.2.dist-info")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(site), os.environ.get("PYTHONPATH")]))}
    result = subprocess.run(
        [sys.executable, "-c", "import xquad"], capture_output=True, text=True, env=env, check=False
    )
    assert result.returncode != 0
    assert "ImportError: xqffi 0.5.2 is installed next to xquad." in result.stderr
