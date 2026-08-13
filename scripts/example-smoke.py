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
import subprocess
import sys
from pathlib import Path

from _docsgen import load_yaml
from _hwprobe import available_hardware_solvers
from xqsa import DEFAULT_SOLVER

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
MANIFEST_PATH = EXAMPLES_DIR / "manifest.yaml"
SEED = 42


def _hardware_examples() -> tuple[str, ...]:
    """Load the `hardware: true` example directories from the manifest.

    Kept deliberately small (a binary problem, a permutation, a constrained
    problem) to bound GPU time and the monthly D-Wave QPU quota; every
    example still runs on the CPU annealer on both interpreters regardless
    of this flag. This is an independent, minimal read of
    examples/manifest.yaml rather than a call into
    scripts/gen-example-docs.py's stricter schema validation -- that
    script's hyphenated filename blocks import (see scripts/_hwprobe.py for
    the same constraint on example-smoke.py itself).
    """
    data = load_yaml(MANIFEST_PATH)
    return tuple(
        example["dir"]
        for group in data.get("groups", [])
        for example in group.get("examples", [])
        if example.get("hardware", False)
    )


# Representative subset exercised against hardware backends, sourced from
# the `hardware: true` examples in examples/manifest.yaml so this list and
# the manifest cannot drift.
HARDWARE_EXAMPLES = _hardware_examples()


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
