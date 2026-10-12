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
Access rules for a Quip order: which solvers may answer it.

:class:`AccessOpen` lets any registered solver answer. :class:`AccessAllow`
restricts the order to an allowlist: a solver may answer if its account is
listed, or if the solver type it declared at registration is listed. The chain
refuses anyone else's answer with ``NotEligibleSolver``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from xqsa.quip.chain import account_bytes, wrap_bounded

# The most solver types an allowlist can name. Fixed in the pallet and not
# published as a constant; over the cap traps the runtime with no readable error.
MAX_BID_TYPES = 8

# How many entries of each list the text form shows before it summarises the rest.
_SHOWN_ENTRIES = 3

# The SS58 prefix for display: the generic Substrate format, which Quip chains use.
_SS58_FORMAT = 42

_ACCOUNT_BYTES = 32


class Access:
    """Base class of the access rules; use one of its subclasses."""

    def to_chain(self) -> Any:
        """Return the rule as the ``JobMode`` value ``propose_job`` takes."""
        raise NotImplementedError


@dataclass(frozen=True)
class AccessOpen(Access):
    """Any registered solver may answer."""

    def to_chain(self) -> Any:
        """Return ``"Open"``."""
        return "Open"

    def __str__(self) -> str:
        return "open"


@dataclass(frozen=True)
class AccessAllow(Access):
    """Only the listed solvers may answer: a listed account, or a solver of a listed type.

    ``accounts`` takes SS58 addresses, ``0x`` hex or raw bytes, and keeps them
    as ``0x`` hex. ``types`` takes the chain's solver type names, such as
    ``"QpuDwave"``. Duplicates are dropped, keeping the first. The protocol does
    not verify solver types: each solver declares its own at
    ``register_solver``, so a type entry admits any solver that says it is one.

    Encodes as ``JobMode::Bid { miners, miner_types }``. The chain refuses an
    order with both lists empty (``EmptyBidCriteria``, after taking the fee), so
    at least one entry is required.

    Raises:
        ValueError: for a malformed account, no entries at all, or more than
            :data:`MAX_BID_TYPES` types.
        TypeError: for a type name that is not a string.
    """

    accounts: tuple[str, ...] = ()
    types: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.accounts, (str, bytes)) or isinstance(self.types, str):
            raise TypeError("AccessAllow: accounts and types take lists, not a single value")
        accounts = tuple(dict.fromkeys(_account_hex(account) for account in self.accounts))
        for name in self.types:
            if not isinstance(name, str):
                raise TypeError(f"AccessAllow: a solver type is a name such as 'QpuDwave', got {name!r}")
        types = tuple(dict.fromkeys(self.types))
        if not accounts and not types:
            raise ValueError("AccessAllow: name at least one account or solver type; the chain refuses an empty list")
        if len(types) > MAX_BID_TYPES:
            raise ValueError(f"AccessAllow: at most {MAX_BID_TYPES} solver types, got {len(types)}")
        object.__setattr__(self, "accounts", accounts)
        object.__setattr__(self, "types", types)

    def to_chain(self) -> Any:
        """Return ``{"Bid": {"miners": ..., "miner_types": ...}}``, an empty list as ``None``."""
        return {
            "Bid": {
                "miners": wrap_bounded(list(self.accounts)) if self.accounts else None,
                "miner_types": wrap_bounded(list(self.types)) if self.types else None,
            }
        }

    def __str__(self) -> str:
        parts = []
        if self.accounts:
            parts.append("accounts " + _listed([_short_account(account) for account in self.accounts]))
        if self.types:
            parts.append("types " + _listed(list(self.types)))
        return f"allowlist ({'; '.join(parts)})"


def _account_hex(account: Any) -> str:
    """Return ``account`` as ``0x`` hex, refusing anything that is not a 32-byte account."""
    if not isinstance(account, (str, bytes, bytearray, memoryview)):
        raise ValueError(f"AccessAllow: {account!r} is not an SS58 address, 0x hex or 32 raw bytes")
    try:
        raw = account_bytes(account)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"AccessAllow: {account!r} is not an SS58 address or 0x hex account: {exc}") from exc
    if len(raw) != _ACCOUNT_BYTES:
        raise ValueError(f"AccessAllow: {account!r} is {len(raw)} bytes, an account is {_ACCOUNT_BYTES}")
    return "0x" + raw.hex()


def _short_account(account: str) -> str:
    """Return ``account`` as a shortened SS58 address, or shortened hex without ``scalecodec``."""
    try:
        from scalecodec.utils.ss58 import ss58_encode

        text = ss58_encode(bytes.fromhex(account[2:]), _SS58_FORMAT)
    except Exception:  # noqa: BLE001 -- display only.
        text = account
    return f"{text[:4]}..{text[-4:]}"


def _listed(entries: list[str]) -> str:
    """Join ``entries``, naming at most :data:`_SHOWN_ENTRIES` and counting the rest."""
    shown = ", ".join(entries[:_SHOWN_ENTRIES])
    rest = len(entries) - _SHOWN_ENTRIES
    return f"{shown} and {rest} more" if rest > 0 else shown


def from_chain(value: Any) -> Access:
    """Decode a ``JobMode`` as an order reads it back.

    Accepts ``"Open"`` (or ``{"Open": None}``) and
    ``{"Bid": {"miners": [...] or None, "miner_types": [...] or None}}``,
    each list also as the 1-tuple :meth:`AccessAllow.to_chain` sends.

    Raises:
        ValueError: for any other value.
    """
    if value == "Open" or value == {"Open": None}:
        return AccessOpen()
    if isinstance(value, Mapping) and len(value) == 1 and isinstance(value.get("Bid"), Mapping):
        fields = value["Bid"]
        return AccessAllow(accounts=_entries(fields.get("miners")), types=_entries(fields.get("miner_types")))
    raise ValueError(f"unknown JobMode {value!r}")


def _entries(value: Any) -> tuple[Any, ...]:
    """Return a decoded ``Option<BoundedVec>`` as a tuple, ``None`` as empty, unwrapping a ``wrap_bounded`` 1-tuple."""
    if value is None:
        return ()
    if isinstance(value, tuple) and len(value) == 1 and isinstance(value[0], (list, tuple)):
        value = value[0]
    return tuple(value)


def coerce_access(value: Any) -> Any:
    """Return ``value`` as an :class:`Access` where it names one.

    ``"Open"`` becomes :class:`AccessOpen`, and an :class:`Access` passes. A
    raw chain dict passes unchanged for the option checks to judge.

    Raises:
        ValueError: for anything else, including ``None``.
    """
    if value == "Open":
        return AccessOpen()
    if not isinstance(value, (Access, Mapping)):
        raise ValueError(
            f"access={value!r} is not an access rule; pass 'Open' or one of AccessOpen(), "
            "AccessAllow(accounts=[...], types=[...])"
        )
    return value
