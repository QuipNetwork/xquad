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

"""
Invariant-based smoke test for example programs.

Runs each example on both the Python and Rust XQVM interpreters and
verifies that both produce a valid solution (valid == 1).  Does NOT
require byte-for-byte output parity — SA is sensitive to BQM
construction order, so the two paths may find different (but equally
valid) optima.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from xqsa import DEFAULT_SOLVER

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
SEED = 42

# Representative subset exercised against hardware backends: a binary
# (maxcut), a permutation (tsp), and a constrained problem (knapsack).
# Kept small to bound GPU time and the monthly D-Wave QPU quota; the full
# suite still runs on the CPU annealer on both interpreters.
HARDWARE_EXAMPLES = ("maxcut", "tsp", "knapsack")


def run_example(runner: Path, interpreter: str, solver: str = DEFAULT_SOLVER) -> dict:
    result = subprocess.run(
        ["python", str(runner), "--seed", str(SEED), "--interpreter", interpreter, "--solver", solver],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  FAIL ({interpreter}/{solver}): runner exited {result.returncode}", file=sys.stderr)
        print(result.stderr[-500:], file=sys.stderr)
        sys.exit(1)
    return json.loads(result.stdout)


def _cuda_available() -> bool:
    """True when cupy is installed and a CUDA device is present."""
    try:
        import cupy

        return bool(cupy.cuda.runtime.getDeviceCount() > 0)
    except Exception:
        return False


def _metal_available() -> bool:
    """True when running on macOS with a usable Metal device."""
    try:
        import Metal

        return Metal.MTLCreateSystemDefaultDevice() is not None
    except Exception:
        return False


def _qpu_available() -> bool:
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


def available_hardware_solvers() -> list[str]:
    """Resolve which hardware solver backends can run here.

    Logs each backend that is skipped and why, so an absent GPU/QPU reads
    as a deliberate skip rather than silent non-coverage.
    """
    probes = (("cuda-gpu", _cuda_available), ("dwave-qpu", _qpu_available), ("metal-gpu", _metal_available))
    reasons = {
        "cuda-gpu": "no cupy / CUDA device",
        "dwave-qpu": "no dwave-system extra / DWAVE_API_TOKEN not set",
        "metal-gpu": "no Metal device",
    }
    available: list[str] = []
    for solver, probe in probes:
        if probe():
            available.append(solver)
        else:
            print(f"  skip {solver}: {reasons[solver]}")
    return available


def main() -> int:
    runners = sorted(EXAMPLES_DIR.glob("*/runner.py"))
    if not runners:
        print("ERROR: no examples found", file=sys.stderr)
        return 1

    failures = 0
    for runner in runners:
        name = runner.parent.name
        print(f"==> {name}")

        for interp in ("python", "rust"):
            out = run_example(runner, interp)
            valid = out.get("valid")
            energy = out.get("energy")

            if valid != 1:
                print(f"  FAIL ({interp}): valid={valid}, energy={energy}")
                failures += 1
            else:
                print(f"  ok   ({interp}): energy={energy}")

    # Hardware-backed solver runs over the subset, gated by availability.
    print("\n==> hardware solvers")
    hw_solvers = available_hardware_solvers()
    for solver in hw_solvers:
        for name in HARDWARE_EXAMPLES:
            runner = EXAMPLES_DIR / name / "runner.py"
            out = run_example(runner, "rust", solver=solver)
            valid, energy = out.get("valid"), out.get("energy")
            if valid != 1:
                print(f"  FAIL ({solver}/{name}): valid={valid}, energy={energy}")
                failures += 1
            else:
                print(f"  ok   ({solver}/{name}): energy={energy}")

    if failures:
        print(f"\n{failures} failure(s)")
        return 1

    hw_note = f", {len(hw_solvers)} hardware solver(s)" if hw_solvers else ""
    print(f"\nAll {len(runners)} examples passed on both interpreters{hw_note}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
