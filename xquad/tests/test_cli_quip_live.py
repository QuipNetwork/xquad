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

"""`xquad quip` against a live network, with the account in effect on this machine.

Opt-in: marked ``quip`` and skipped unless ``QUIP_RPC_URL`` is set. The
reclaims sign and pay fees, so run them on aglais with an account that has
an unanswered final order::

    QUIP_RPC_URL=wss://... uv run --no-sync pytest xquad/tests/test_cli_quip_live.py -m quip -v
"""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner, Result

pytest.importorskip("substrateinterface", reason="designed: the quip extra is not installed")
pytest.importorskip("quip_signer", reason="designed: the quip extra is not installed")

from xquad.cli import main  # noqa: E402

pytestmark = [
    pytest.mark.quip,
    pytest.mark.skipif(not os.environ.get("QUIP_RPC_URL"), reason="designed: QUIP_RPC_URL unset"),
]


def quip(*args: str) -> Result:
    return CliRunner().invoke(main, ["quip", "--json", *args])


def test_account_show() -> None:
    result = quip("account", "show")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["order_slots"]["used"] >= 0


def test_orders_and_status() -> None:
    result = quip("orders")
    assert result.exit_code == 0, result.output
    orders = json.loads(result.stdout)["orders"]
    if not orders:
        pytest.skip("designed: the account has no open orders to read")
    order_id = orders[0]["order_id"]
    status = json.loads(quip("status", str(order_id)).stdout)
    assert status["summary"]["order_id"] == order_id
    assert quip("status", str(order_id), "--short").exit_code == 0


def test_reclaim_one_then_all() -> None:
    unanswered = json.loads(quip("orders", "--state", "unanswered").stdout)["orders"]
    if not unanswered:
        pytest.skip("designed: the account has no unanswered final order to reclaim")
    first = unanswered[0]["order_id"]
    result = quip("reclaim", str(first))
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["order_id"] == first
    result = quip("reclaim", "--all", "--yes")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["refused"] == []
