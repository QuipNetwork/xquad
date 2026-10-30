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
Errors raised when running a solver binary.

Provisional: the names and bases may change before they are exported from
:mod:`xqsa`.
"""

from __future__ import annotations


class SolverError(Exception):
    """A solver binary could not produce a result.

    ``path`` is the binary, ``exit_code`` its exit status (``None`` when the
    error was raised before or without one), and ``detail`` what went wrong.
    """

    def __init__(self, path: str, *, exit_code: int | None = None, detail: str) -> None:
        self.path = path
        self.exit_code = exit_code
        self.detail = detail
        status = "" if exit_code is None else f" (exit {exit_code})"
        super().__init__(f"solver binary {path}{status}: {detail}")


class SolverUnavailableError(SolverError, RuntimeError):
    """The binary cannot be used: it does not start, or does not speak the protocol, or its device does not open."""


class ModelRejectedError(SolverError, ValueError):
    """The model exceeds what the binary accepts."""


class SolverProtocolError(SolverError):
    """The binary refused the request as malformed."""


class SolverFailedError(SolverError, RuntimeError):
    """The binary failed while sampling, or its output could not be read."""
