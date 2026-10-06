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


@dataclass(frozen=True)
class JobQuote:
    """What proposing one job costs, and whether the account can pay for it.

    Built by :meth:`~xqsa.quip.SolverQuip.quote` (and by :meth:`~xqsa.quip.SolverQuip.solve` before it
    proposes) from the signed ``propose_job`` extrinsic the solve submits. The
    fee is the chain's own ``payment_queryInfo`` answer when ``fee_exact`` is
    true, else the :data:`~xqsa.quip.chain.FEE_HEADROOM_PLANCK` fallback. The reward is reserved
    at proposal and returned if the job closes unanswered; the fee is burned
    either way. Amounts are in planck; ``str()`` renders them in token units.
    """

    network: str | None
    reward_planck: int
    fee_planck: int
    fee_exact: bool
    balance_planck: int
    token_symbol: str
    token_decimals: int

    @property
    def total_planck(self) -> int:
        """Reward plus fee: what the account must hold to propose the job."""
        return self.reward_planck + self.fee_planck

    @property
    def shortfall_planck(self) -> int:
        """How far the balance falls short of the total, or 0."""
        return max(0, self.total_planck - self.balance_planck)

    def __str__(self) -> str:
        """Render the quote as ``[quip]``-prefixed ASCII lines, decimal points aligned."""
        where = f"network {self.network}" if self.network else "custom endpoint"
        fee = "fee exact" if self.fee_exact else "fee estimated"
        rows = {
            "reward": self.reward_planck,
            "fee": self.fee_planck,
            "total": self.total_planck,
            "balance": self.balance_planck,
            "shortfall": self.shortfall_planck,
        }
        amounts = {label: _format_planck(value, self.token_decimals) for label, value in rows.items()}
        width = max(map(len, amounts.values()))
        lines = [f"[quip] job quote ({where}, {fee})"]
        lines += [f"[quip]   {label:<10} {amount:>{width}} {self.token_symbol}" for label, amount in amounts.items()]
        return "\n".join(lines)


def _format_planck(planck: int, decimals: int) -> str:
    """Render ``planck`` in token units with exactly ``decimals`` fractional digits."""
    if decimals == 0:
        return str(planck)
    unit = 10**decimals
    return f"{planck // unit}.{planck % unit:0{decimals}d}"
