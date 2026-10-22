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

"""Command-line interface for the XQVM reference implementation.

Thin shim around the in-process Executor API so that xqvm-py can be
driven with the same I/O contract as `xquad vm run`. Used
by the xquad-conformance harness to validate identical outputs across
implementations.

Invocation:

    python -m xqvm_py run [--text] [--inputs inputs.json | --calldata 1,2,3]
                          [--outputs N] PROGRAM
"""

from .run import main

__all__ = ["main"]
