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
SolverQuip: submit XQMX Ising models to the Quip Network mempool.

``SolverQuip`` proposes a job to the ``QuantumComputeMempool`` pallet via
``propose_job``, waits for a miner to solve it, and reads the solution back as
an XQMX sample. It is a synchronous :class:`~xqsa.solver.Solver`; the
``substrate-interface`` client is sync and the Quip chain is hybrid-signed, so
there is no async surface here.

Configuration is resolved from constructor arguments first, then environment:

    =====================  =======================================================================
    Argument               Environment variable
    =====================  =======================================================================
    ``url``                ``QUIP_RPC_URL``      websocket RPC endpoint
    ``seed``               ``QUIP_SIGNER_SEED``  32-byte hex master seed
    ``keystore``           ``QUIP_KEYSTORE``     keystore path (else ~/.quip/keystore.json)
    ``reward``             ``QUIP_REWARD``       reward in planck (else MinReward)
    ``topology``           ``QUIP_TOPOLOGY``     topology hash, or "native" (else DefaultTopology)
    ``faucet``             ``QUIP_FAUCET_URL``   faucet base URL (env read only without url=)
    ``autoconfirm``        ``QUIP_AUTOCONFIRM``  submit without asking (default true)
    ``autofund``           ``QUIP_AUTOFUND``     fund from the faucet without asking
    =====================  =======================================================================

A seed wins over a keystore. With neither configured, the keystore at
``~/.quip/keystore.json`` is loaded, or generated on first use. Every :meth:`SolverQuip.solve` first quotes the job
(:class:`JobQuote`: reward plus the chain-reported fee, against the balance)
and passes two consent gates. ``autoconfirm`` decides whether to submit at that
price; ``autofund`` decides whether an account short of it is topped up with
one faucet drip first. A shortfall larger than one drip is never requested; it
raises :class:`QuipSubmissionError`, since it is more often a mistyped reward.
Each gate is ``True`` (proceed, the default), ``False`` (ask on the terminal;
raises :class:`QuipCancelledError` when stdin is not one, which includes a
Jupyter kernel), or a callable taking the quote and returning a verdict. The
environment variables take ``1/true/yes/on`` or ``0/false/no/off``. A short
account with no faucet configured raises :class:`QuipSubmissionError`.

Named networks skip the URL: ``SolverQuip.for_network("aglais")``
builds a solver against a preset, ``aglais`` (the public testnet) or ``devnet``
(the localdev stack). The coordinates, and how often they move, are in
:mod:`xqsa.quip.networks`.

Signing is hybrid (sr25519 + FN-DSA-512): the chain's ``Signature`` is
``HybridTxSignature``, which ``substrate-interface`` cannot produce, so all
crypto is delegated to the ``quip_signer`` extension and extrinsic assembly to
:mod:`xqsa.quip.signing`. Install both with ``pip install xqsa[quip]`` (or
``uv sync --extra quip``); ``quip_signer`` resolves a wheel on Linux and builds
from its sdist with a local Rust toolchain elsewhere. It is lazy-imported, so
every other xqsa solver installs and runs without it; only constructing
``SolverQuip`` requires the extra.

The chain client is built by :func:`xqsa.quip.metadata.connect`, not by
``substrateinterface.SubstrateInterface`` directly: Quip runtimes serve Metadata
V16 and ``scalecodec`` decodes at most V14, so the stock client cannot read the
chain at all. The shim pulls V14 through the versioned runtime API and is
confined to the instances built here.

Beta-testnet notice: the Quip devnet/testnet is pre-release. Pallet metadata,
the signed-extension layout, and economic parameters can change between
releases; pin a node image and re-validate after upgrades.
"""

from .chain import (
    FEE_HEADROOM_PLANCK,
    JOB_ORDERS_STORAGE,
    JOB_PROPOSED_EVENT,
    MEMPOOL_PALLET,
    MINEABLE_TOPOLOGIES_STORAGE,
    ORDER_SOLUTIONS_STORAGE,
    PROPOSE_JOB_CALL,
    RECLAIM_ORDER_CALL,
)
from .client import (
    DEFAULT_BLOCK_WAIT,
    DEFAULT_DEADLINE_BLOCKS,
    DEFAULT_KEYSTORE,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_TIMEOUT,
    FUND_WAIT_SECONDS,
    NATIVE_TOPOLOGY,
    SolverQuip,
)
from .errors import (
    EncodingError,
    PlacementError,
    QuipCancelledError,
    QuipConnectionError,
    QuipError,
    QuipFaucetError,
    QuipJobFailedError,
    QuipMetadataError,
    QuipOrderOptionError,
    QuipSigningError,
    QuipSubmissionError,
    QuipTimeoutError,
    QuipTopologyError,
)
from .faucet import fund_from_faucet
from .networks import NETWORKS
from .quote import JobQuote

__all__ = [
    "SolverQuip",
    "JobQuote",
    "QuipError",
    "QuipCancelledError",
    "QuipConnectionError",
    "QuipMetadataError",
    "QuipSubmissionError",
    "QuipOrderOptionError",
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
