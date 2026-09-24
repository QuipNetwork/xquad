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

"""Python bindings for the XQuad Rust runtime.

The compiled extension (`xqffi.xqffi`) registers three submodules,
`xqffi.asm`, `xqffi.vm` and `xqffi.verifier`, in `sys.modules` when it is
imported; this package imports it and re-exports them. Type stubs for each
submodule sit beside this file.
"""

from .xqffi import asm, verifier, vm

__all__ = ["asm", "verifier", "vm"]
