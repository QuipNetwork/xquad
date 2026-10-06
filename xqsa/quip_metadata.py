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
Deprecated alias for :mod:`xqsa.quip.metadata`.

Importing ``xqsa.quip_metadata`` warns and returns the ``xqsa.quip.metadata`` module
itself, so attribute access and patch targets keep working. QUI-1608 removes
this shim.
"""

import importlib
import sys
import warnings

warnings.warn(
    "xqsa.quip_metadata is deprecated; import xqsa.quip.metadata instead",
    DeprecationWarning,
    stacklevel=2,
)
sys.modules[__name__] = importlib.import_module("xqsa.quip.metadata")
