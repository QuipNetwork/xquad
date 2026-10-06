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
Quip Network backend: :class:`SolverQuip` and its supporting modules.

The public surface re-exported here is what ``from xqsa.quip import X`` has
always resolved. :mod:`xqsa.quip.signing` is not imported, because it needs
the ``quip_signer`` extension from the ``[quip]`` extra.
"""

from .client import (
    DEFAULT_BLOCK_WAIT,
    DEFAULT_DEADLINE_BLOCKS,
    DEFAULT_KEYSTORE,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_TIMEOUT,
    FEE_HEADROOM_PLANCK,
    FUND_WAIT_SECONDS,
    JOB_ORDERS_STORAGE,
    JOB_PROPOSED_EVENT,
    MEMPOOL_PALLET,
    MINEABLE_TOPOLOGIES_STORAGE,
    NATIVE_TOPOLOGY,
    ORDER_SOLUTIONS_STORAGE,
    PROPOSE_JOB_CALL,
    RECLAIM_ORDER_CALL,
    JobQuote,
    QuipCancelledError,
    QuipConnectionError,
    QuipJobFailedError,
    QuipSubmissionError,
    QuipTimeoutError,
    QuipTopologyError,
    SolverQuip,
)
from .codec import EncodingError, PlacementError, QuipError, QuipMetadataError, QuipSigningError
from .faucet import QuipFaucetError, fund_from_faucet
from .networks import NETWORKS

__all__ = [
    "SolverQuip",
    "JobQuote",
    "QuipError",
    "QuipCancelledError",
    "QuipConnectionError",
    "QuipMetadataError",
    "QuipSubmissionError",
    "QuipTimeoutError",
    "QuipTopologyError",
    "QuipJobFailedError",
    "QuipSigningError",
    "QuipFaucetError",
    "PlacementError",
    "EncodingError",
    "NETWORKS",
    "fund_from_faucet",
    "DEFAULT_BLOCK_WAIT",
    "DEFAULT_DEADLINE_BLOCKS",
    "DEFAULT_KEYSTORE",
    "DEFAULT_POLL_INTERVAL",
    "DEFAULT_TIMEOUT",
    "FEE_HEADROOM_PLANCK",
    "FUND_WAIT_SECONDS",
    "NATIVE_TOPOLOGY",
    "MEMPOOL_PALLET",
    "PROPOSE_JOB_CALL",
    "RECLAIM_ORDER_CALL",
    "JOB_PROPOSED_EVENT",
    "JOB_ORDERS_STORAGE",
    "ORDER_SOLUTIONS_STORAGE",
    "MINEABLE_TOPOLOGIES_STORAGE",
]
