#!/usr/bin/env python3
# Copyright (C) 2026 Postquant Labs Incorporated
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
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

"""Hardware-solver availability probes for CUDA, D-Wave QPU, and Metal.

Split out of scripts/example-smoke.py (matching the scripts/_docsgen.py
shared-helper convention) because that module's hyphenated filename is not
a valid Python module name and so cannot be imported. Two callers:

- scripts/example-smoke.py: probes availability and skips a backend with a
  reason when it is absent (``available_hardware_solvers``).
- scripts/run-hardware-tests.sh: the counterpart that treats absence as a
  hard failure rather than a skip (``require``).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

# Keyed by the hardware kind scripts/run-hardware-tests.sh takes on its
# command line, not by the xqsa solver name -- see _SOLVER_NAMES below for
# that mapping.
_REASONS: dict[str, str] = {
    "cuda": "no cupy / CUDA device",
    "qpu": "no dwave-system extra / DWAVE_API_TOKEN not set",
    "metal": "no Metal device",
}

# xqsa registry keys (xqsa/registry.py), in the order example-smoke.py has
# always printed them in.
_SOLVER_NAMES: dict[str, str] = {
    "cuda": "cuda-gpu",
    "qpu": "dwave-qpu",
    "metal": "metal-gpu",
}


def cuda_available() -> bool:
    """True when cupy is installed and a CUDA device is present."""
    try:
        import cupy

        return bool(cupy.cuda.runtime.getDeviceCount() > 0)
    except Exception:
        return False


def metal_available() -> bool:
    """True when running on macOS with a usable Metal device."""
    try:
        import Metal

        return Metal.MTLCreateSystemDefaultDevice() is not None
    except Exception:
        return False


def qpu_available() -> bool:
    """True when dwave-system is installed and a Leap token is configured.

    Both are required: the token alone (without the ``[dwave]`` extra) would
    let the run start and then crash in the solver, so it must be a skip.
    """
    if os.environ.get("DWAVE_API_TOKEN") is None:
        return False
    try:
        import dwave.system  # noqa: F401

        return True
    except ImportError:
        return False


_PROBES: dict[str, Callable[[], bool]] = {
    "cuda": cuda_available,
    "qpu": qpu_available,
    "metal": metal_available,
}


def available_hardware_solvers() -> list[str]:
    """Resolve which hardware solver backends can run here.

    Logs each backend that is skipped and why, so an absent GPU/QPU reads
    as a deliberate skip rather than silent non-coverage.
    """
    available: list[str] = []
    for kind, probe in _PROBES.items():
        solver = _SOLVER_NAMES[kind]
        if probe():
            available.append(solver)
        else:
            print(f"  skip {solver}: {_REASONS[kind]}")
    return available


def require(kind: str) -> None:
    """Exit non-zero with the specific reason when ``kind`` hardware is absent.

    The counterpart to the skip-and-continue behaviour in
    ``available_hardware_solvers``: a CI hardware job wants a missing GPU or
    an unset ``DWAVE_API_TOKEN`` to fail the job, not silently pass having
    run nothing.
    """
    probe = _PROBES.get(kind)
    if probe is None:
        print(f"error: unknown hardware kind '{kind}' (expected cuda, qpu, or metal)", file=sys.stderr)
        sys.exit(2)
    if not probe():
        print(f"error: {kind} hardware unavailable: {_REASONS[kind]}", file=sys.stderr)
        sys.exit(1)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: ``python scripts/_hwprobe.py <cuda|qpu|metal>``.

    Exits 0 when the named hardware is available so shell callers (notably
    scripts/run-hardware-tests.sh) can gate a job on it directly.
    """
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] not in _PROBES:
        print("usage: _hwprobe.py <cuda|qpu|metal>", file=sys.stderr)
        return 2
    require(args[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
