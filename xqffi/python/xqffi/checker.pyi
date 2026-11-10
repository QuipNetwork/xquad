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

"""Type stubs for `xqffi.checker`, the bindings around `xqvm::checker`."""

__all__ = ["check", "check_source"]

def check(bytecode: bytes) -> None:
    """Check pre-assembled bytecode.

    Raises `ValueError` if `bytecode` does not decode, or on the first
    violation, with a message of the form `"<Variant>: <description>"`.
    """

def check_source(source: str) -> None:
    """Assemble `source`, then check it.

    Raises `ValueError` on assembly failure or on the first violation.
    """
