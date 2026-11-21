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

"""The jump-span verifier rule reaches Python (W1-011).

`xqffi.verifier.verify` delegates to the Rust default verifier and surfaces
every rejection as ``ValueError("<Variant>: <description>")``. The auditor's
22-byte reproduction -- a conditional jump from loop A's body into loop B's
body, which the pre-fix verifier accepted -- must therefore raise a
``ValueError`` naming ``JumpAcrossLoopBoundary``. If the phase were dropped
from the default verifier, this program would verify and the test would fail.
"""

import pytest

from xquad.verifier import verify

# Program::new(<auditor PoC>).encode(): the XQBC container around
#   PUSH1 0; PUSH2 0x2328; RANGE; PUSH1 1; PUSH1 1; JUMPI1 .0;
#   PUSH1 0; PUSH1 1; RANGE; TARGET .0; NEXT; POP; NEXT; HALT
CROSS_LOOP_JUMP = bytes.fromhex("5851424301000000000016ff64b509110012232808110111010200110011010800071007ff")


def test_jump_into_another_loop_body_is_rejected():
    with pytest.raises(ValueError, match=r"^JumpAcrossLoopBoundary: "):
        verify(CROSS_LOOP_JUMP)
