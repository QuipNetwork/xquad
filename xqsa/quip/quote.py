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

"""The price of one Quip job: :class:`JobQuote`."""

from __future__ import annotations

from dataclasses import dataclass

from xqsa.quip.display import box


@dataclass(frozen=True)
class JobQuote:
    """What proposing one job costs, and whether the account can pay for it.

    Built by :meth:`~xqsa.quip.SolverQuip.quote` (and by :meth:`~xqsa.quip.SolverQuip.solve` before it
    proposes) from the signed ``propose_job`` extrinsic the solve submits. The
    fee is the chain's own ``payment_queryInfo`` answer when ``fee_exact`` is
    true, else the :data:`~xqsa.quip.chain.FEE_HEADROOM_PLANCK` fallback. The reward is reserved
    at proposal and returned if the job closes unanswered; the fee is burned
    either way. Amounts are in planck; ``str()`` renders them in token units,
    in a 72-column box.

    The fields after ``token_decimals`` describe who pays and what is priced:
    the SS58 ``account``, the block the quote was taken at, the model's
    variables and the spins and couplings it was placed onto, and the
    ``placement`` (``"native"`` or the topology hash). Each defaults to
    ``None`` and its display row is left out while unknown.
    """

    network: str | None
    reward_planck: int
    fee_planck: int
    fee_exact: bool
    balance_planck: int
    token_symbol: str
    token_decimals: int
    account: str | None = None
    quoted_at_block: int | None = None
    num_variables: int | None = None
    num_spins: int | None = None
    num_couplings: int | None = None
    placement: str | None = None

    @property
    def total_planck(self) -> int:
        """Reward plus fee: what the account must hold to propose the job."""
        return self.reward_planck + self.fee_planck

    @property
    def shortfall_planck(self) -> int:
        """How far the balance falls short of the total, or 0."""
        return max(0, self.total_planck - self.balance_planck)

    @property
    def affordable(self) -> bool:
        """Whether the balance covers the total."""
        return self.shortfall_planck == 0

    def __str__(self) -> str:
        """Render the quote as a box: network, problem, cost, then balance, decimal points aligned."""
        planck = {
            "Reward": self.reward_planck,
            "Fee": self.fee_planck,
            "Total": self.total_planck,
            "Balance": self.balance_planck,
            "Shortfall": self.shortfall_planck,
        }
        texts = {label: _format_planck(value, self.token_decimals) for label, value in planck.items()}
        width = max(map(len, texts.values()))
        amount = {label: f"{text:>{width}} {self.token_symbol}" for label, text in texts.items()}

        network = [("Network", self.network or "custom endpoint")]
        if self.account is not None:
            network.append(("Account", self.account))
        if self.quoted_at_block is not None:
            network.append(("Quoted at", f"block {self.quoted_at_block}"))
        problem = []
        if None not in (self.num_variables, self.num_spins, self.num_couplings):
            problem.append(
                ("Problem", f"{self.num_variables} variables -> {self.num_spins} spins, {self.num_couplings} couplings")
            )
        if self.placement is not None:
            problem.append(("Placed on", "native" if self.placement == "native" else f"topology\n{self.placement}"))
        cost = [
            ("Reward", f"{amount['Reward']}  refunded if unanswered"),
            ("Fee", f"{amount['Fee']}  {'exact' if self.fee_exact else 'estimated'}"),
            ("Total", amount["Total"]),
        ]
        balance = [
            ("Balance", amount["Balance"]),
            ("Shortfall", amount["Shortfall"]),
            ("Affordable", "yes" if self.affordable else "no"),
        ]
        sections = [section for section in (network, problem, cost) if section]
        return box("Job quote", [*sections, balance], double_before=len(sections))


def _format_planck(planck: int, decimals: int) -> str:
    """Render ``planck`` in token units with exactly ``decimals`` fractional digits."""
    if decimals == 0:
        return str(planck)
    unit = 10**decimals
    return f"{planck // unit}.{planck % unit:0{decimals}d}"
