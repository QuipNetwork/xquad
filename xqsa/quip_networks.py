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
Named Quip Network presets for :meth:`xqsa.quip.SolverQuip.for_network`.

A preset names the coordinates of one Quip network, so a caller writes
``SolverQuip.for_network("aglais")`` instead of carrying an RPC address around.

The values move. The ``aglais`` entry was verified live on 2026-09-22 at runtime
``specVersion 117``; testnet endpoints change between releases and are corrected
here in a patch release. Verify before a run: any node can lag the chain tip and
serve a stale view, and bootnode-2 and bootnode-3 serve the same chain as
bootnode-1. https://aglais.quip.network publishes the testnet's current
endpoints; check ``aglais`` against it when a connection fails. ``devnet`` is the
default localdev stack, fronted by Caddy on port 20049.

There is no mainnet entry because there is no Quip mainnet yet.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class QuipNetwork:
    """Coordinates of one Quip network.

    Attributes:
        rpc: Websocket RPC endpoint, passed to ``SolverQuip`` as ``url``.
        faucet: Base URL of the network's faucet, passed to ``SolverQuip`` as ``faucet``.
    """

    rpc: str
    faucet: str


NETWORKS: Mapping[str, QuipNetwork] = MappingProxyType(
    {
        "aglais": QuipNetwork(
            rpc="wss://bootnode-1.aglais.quip.network:20049/rpc",
            faucet="https://faucet.aglais.quip.network",
        ),
        "devnet": QuipNetwork(
            rpc="ws://localhost:20049/rpc",
            faucet="http://localhost:20049/api/faucet",
        ),
    }
)
"""Known networks by name. Read-only."""
