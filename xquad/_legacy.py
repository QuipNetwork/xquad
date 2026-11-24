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

"""
Refuse to run next to the 0.5 `xqffi`, `xqcp` and `xqsa` distributions.

Up to 0.5 those three were separate PyPI distributions. From 0.6 the
`xquad` wheel ships their import packages itself, into the same
directories. pip tracks files per distribution and does not see the
overlap, so an environment that holds both is broken without saying so:

- An upgrade with `pip install -U xquad` leaves the 0.5 distributions
  installed, and a later `pip uninstall xqsa` deletes files `xquad` needs.
- `pip install xqsa` next to `xquad` 0.6 writes 0.5 code over it, and
  `pip list` still shows `xquad` 0.6.

`check()` runs when `xquad` is imported and raises with the fix instead.
It lives in `xquad/` because no 0.5 distribution other than `xquad`
itself ships a file there. It is a transition aid, to be removed before
1.0.0.

The scan reads `*.dist-info` directory names on `sys.path` instead of
calling `importlib.metadata`, whose import alone costs about ten times
`import xquad` and would slow every `xquad` command.
"""

from __future__ import annotations

import os
import sys

LEGACY = ("xqffi", "xqcp", "xqsa")


def installed(paths: list[str] | None = None) -> list[str]:
    """Return `"<name> <version>"` for each legacy distribution found on `paths`."""
    found: dict[str, str] = {}
    for entry in sys.path if paths is None else paths:
        try:
            names = os.listdir(entry or ".")
        except OSError:
            continue
        for name in names:
            if not name.endswith(".dist-info"):
                continue
            dist, _, version = name.removesuffix(".dist-info").partition("-")
            dist = dist.lower()
            if dist in LEGACY and dist not in found:
                found[dist] = version
    return [f"{dist} {found[dist]}" for dist in LEGACY if dist in found]


def check(paths: list[str] | None = None) -> None:
    """Raise `ImportError` when a legacy distribution is installed next to `xquad`."""
    legacy = installed(paths)
    if not legacy:
        return
    raise ImportError(
        f"{', '.join(legacy)} is installed next to xquad. From 0.6, xquad ships the "
        "xqffi, xqcp and xqsa packages itself, and the old distributions overwrite "
        "its files. Remove them, then reinstall xquad:\n"
        "    pip uninstall -y xqffi xqcp xqsa\n"
        "    pip install --force-reinstall xquad"
    )
