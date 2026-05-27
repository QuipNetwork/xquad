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
Number Partitioning end-to-end XQuad pipeline example.

Given N positive integers, partition them into two subsets of equal sum.
The equality constraint sum(a_i*(2*x_i - 1)) = 0 is re-expressed as a
weighted EQUALITY: sum(a_i * x_i) = S/2, where S = sum(a_i).

Usage:
    uv run python examples/number_partition/runner.py --seed 42
    uv run python examples/number_partition/runner.py --n 6 --interpreter rust
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

from xquad.cp import Problem, Types
from xquad.sa import SolverDWaveCPU
from xquad.types import XQMX, Vec, XQMXDomain
from xquad.vm import VM, VMBackend


def build_problem(n: int, numbers: list[int]) -> Problem:
    """Construct a Number Partitioning QUBO via EQUALITY.

    Decision variables x_i in {0,1}: x_i=1 puts number a_i in subset A.
    Constraint: sum(a_i * x_i) = S/2 where S = sum(a_i).
    Minimising the penalty P*(sum(a_i*x_i) - S/2)^2 drives the partition
    toward balance.
    """
    problem = Problem("NumberPartition")

    num_items = problem.input("num_items", type=Types.Int)
    numbers_in = problem.input("numbers", type=Types.Vec)

    # Compute total sum S over the input vec
    total = problem.stow("total", 0)
    with problem.range(0, num_items) as i:
        total = problem.stow(total, total + numbers_in.get(i))

    # target = S // 2
    target = problem.stow("target", total // 2)

    problem.define_model(size=num_items, domain=XQMXDomain.BINARY)

    # Build index and coefficient vecs
    indices = problem.vec()
    coeffs = problem.vec()
    with problem.range(0, num_items) as i:
        indices.push(i)
        coeffs.push(numbers_in.get(i))

    # EQUALITY: P * (sum(a_i * x_i) - S/2)^2
    problem.model.apply_equality(indices, coeffs, target, 100)

    # Output: partition assignment (0 = subset B, 1 = subset A)
    assignment = problem.output("assignment", type=Types.Vec)
    with problem.range(0, num_items) as i:
        assignment.append(problem.sample.getline(i))

    return problem


def partition_sums(assignment: list[int], numbers: list[int]) -> tuple[int, int]:
    """Return (sum_A, sum_B) for the two subsets."""
    sum_a = sum(v for x, v in zip(assignment, numbers) if x)
    sum_b = sum(v for x, v in zip(assignment, numbers) if not x)
    return sum_a, sum_b


def run(
    programs: Any,
    n: int,
    numbers: list[int],
    seed: int,
    backend: VMBackend,
) -> tuple[int, int, list[int]]:
    """Full pipeline on the selected VM backend."""
    vm = VM(backend=backend)
    vm.set_calldata([n, numbers])
    vm.set_output_slots(1)
    vm.run(programs.encoder)
    model = vm.outputs()[0]
    assert isinstance(model, XQMX)

    solver = SolverDWaveCPU(seed=seed)
    sample = solver.solve(model).sample

    vm = VM(backend=backend)
    vm.set_calldata([model, sample, n])
    vm.set_output_slots(2)
    vm.run(programs.verifier)
    outs = vm.outputs()
    energy, valid = outs[0], outs[1]

    vm = VM(backend=backend)
    vm.set_calldata([sample, n])
    vm.set_output_slots(1)
    vm.run(programs.decoder)
    assign_out = vm.outputs()[0]
    if isinstance(assign_out, Vec):
        assignment = [assign_out.get(i) for i in range(n)]
    else:
        assignment = list(assign_out)

    return energy, valid, assignment


def main() -> int:
    parser = argparse.ArgumentParser(description="Number Partitioning end-to-end XQuad pipeline example")
    parser.add_argument("--n", type=int, default=6, help="Number of integers (default: 6)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    parser.add_argument(
        "--interpreter",
        choices=("python", "rust"),
        default="python",
        help="XQVM interpreter to run the compiled programs on",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Write the decoded result as JSON to this path; stdout otherwise",
    )
    args = parser.parse_args()

    rng = random.Random(args.seed)
    numbers = [rng.randint(1, 20) for _ in range(args.n)]

    problem = build_problem(args.n, numbers)
    programs = problem.compile()

    backend = VMBackend.PYTHON if args.interpreter == "python" else VMBackend.RUST
    energy, valid, assignment = run(programs, args.n, numbers, args.seed, backend)

    sum_a, sum_b = partition_sums(assignment, numbers)
    result = {
        "_seed": args.seed,
        "_note": "canonical CI golden",
        "n": args.n,
        "numbers": numbers,
        "assignment": assignment,
        "sum_a": sum_a,
        "sum_b": sum_b,
        "energy": int(energy),
        "valid": int(valid),
    }

    body = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        sys.stdout.write(body)
    else:
        args.output.write_text(body)

    return 0


if __name__ == "__main__":
    sys.exit(main())
