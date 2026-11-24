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

"""Every `pip install xquad[<extra>]` hint names an extra the project declares.

The `ImportError` hints in `xqsa` and the package READMEs tell users which
extra to install. If an extra is renamed in the root `pyproject.toml` and a
hint is not, pip only warns that the extra does not exist and installs
nothing, so the hint would send users nowhere.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HINT = re.compile(r"pip install xquad\[([^\]]+)\]")
SOURCES = ("xqcp", "xqsa", "xquad", "xqffi")


def _hints() -> dict[str, list[str]]:
    hints: dict[str, list[str]] = {}
    for package in SOURCES:
        for path in sorted((REPO_ROOT / package).rglob("*")):
            if path.suffix not in {".py", ".pyi", ".md"} or "tests" in path.relative_to(REPO_ROOT).parts:
                continue
            for match in HINT.finditer(path.read_text(encoding="utf-8")):
                for extra in match.group(1).split(","):
                    hints.setdefault(extra.strip(), []).append(str(path.relative_to(REPO_ROOT)))
    return hints


def test_hints_are_found() -> None:
    # Guards the scan itself: the solver modules carry hints for all four extras.
    assert {"cuda", "dwave", "metal", "quip"} <= set(_hints())


def test_every_hint_names_a_declared_extra() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = set(pyproject["project"]["optional-dependencies"])
    unknown = {extra: paths for extra, paths in _hints().items() if extra not in declared}
    assert not unknown, f"hints name extras the root pyproject.toml does not declare: {unknown}"
