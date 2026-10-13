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

"""The deprecated ``xqsa.quip_*`` paths warn and alias the ``xqsa.quip`` modules."""

from __future__ import annotations

import importlib
import sys

import pytest


@pytest.mark.parametrize("name", ["codec", "signing", "metadata", "faucet", "networks"])
def test_shim_warns_and_aliases_new_module(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    if name == "signing":
        pytest.importorskip("quip_signer", reason="quip_signer extension not installed")
    new = importlib.import_module(f"xqsa.quip.{name}")
    monkeypatch.delitem(sys.modules, f"xqsa.quip_{name}", raising=False)
    with pytest.warns(DeprecationWarning, match=f"xqsa.quip_{name}"):
        old = importlib.import_module(f"xqsa.quip_{name}")
    assert old is new
