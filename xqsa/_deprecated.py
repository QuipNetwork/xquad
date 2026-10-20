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

"""Flat Quip names kept on ``xqsa`` and ``xquad.sa`` behind a ``DeprecationWarning`` until v0.5.0."""

from __future__ import annotations

import importlib
import sys
import warnings
from collections.abc import Callable

FLAT_QUIP_NAMES = frozenset(
    {
        "Access",
        "AccessAllow",
        "AccessOpen",
        "EncodingError",
        "JobOrder",
        "JobOrderReceipt",
        "JobQuote",
        "PlacementError",
        "QuipCancelledError",
        "QuipConnectionError",
        "QuipError",
        "QuipFaucetError",
        "QuipJobFailedError",
        "QuipMetadataError",
        "QuipOrderOptionError",
        "QuipReclaimRefusedError",
        "QuipSigningError",
        "QuipSubmissionError",
        "QuipTimeoutError",
        "QuipTopologyError",
        "QuipUnconfirmedError",
        "Resolution",
        "ResolutionSingleBest",
        "ResolutionTopNEqual",
        "ResolutionTopNWeighted",
        "SolverQuip",
        "fund_from_faucet",
    }
)


def flat_quip_getattr(flat: str, home: str) -> Callable[[str], object]:
    """Return a module ``__getattr__`` (PEP 562) serving the flat Quip names of ``flat`` from ``home``.

    Each access warns at the caller's line and returns the object ``home`` exports,
    so ``xqsa.SolverQuip is xqsa.quip.SolverQuip``. Any other name raises ``AttributeError``.
    """

    def __getattr__(name: str) -> object:
        if name not in FLAT_QUIP_NAMES:
            raise AttributeError(f"module {flat!r} has no attribute {name!r}")
        message = f"{flat}.{name} is deprecated; import it from {home} (removed in v0.5.0)"
        # A name xqsa.quip retires too also says why, which the flat path would otherwise hide.
        retired = importlib.import_module("xqsa.quip")._DEPRECATED.get(name)
        warnings.warn(f"{message}. {retired}" if retired else message, DeprecationWarning, stacklevel=2)
        # One warning per access: a name ``home`` deprecates too would warn again.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            return getattr(importlib.import_module(home), name)

    return __getattr__


def flat_quip_dir(flat: str) -> Callable[[], list[str]]:
    """Return a module ``__dir__`` (PEP 562) that lists the flat Quip names of ``flat`` beside its globals.

    Keeps the deprecated names in REPL and IDE completion for the deprecation window;
    only access warns.
    """

    def __dir__() -> list[str]:
        return sorted(vars(sys.modules[flat]).keys() | FLAT_QUIP_NAMES)

    return __dir__
