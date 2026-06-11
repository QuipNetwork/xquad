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
Bin Packing end-to-end XQuad pipeline example.

Pack N items with given sizes into the minimum number of bins of fixed capacity C.
Variable x[i,b] = 1 if item i is placed in bin b. EQUALITY enforces each item
goes into exactly one bin; SLACK + EQUALITY enforces capacity per bin.

Usage:
    uv run python examples/bin_packing/runner.py --seed 42
    uv run python examples/bin_packing/runner.py --n 4 --bins 3 --interpreter rust
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

from xquad.cp import Problem, Types
from xquad.sa import DEFAULT_SOLVER, SOLVERS, build_solver
from xquad.types import XQMX, Vec, XQMXDomain
from xquad.vm import VM, VMBackend


def build_problem(n: int, num_bins: int, sizes: list[int], capacity: int) -> Problem:
    """Construct a Bin Packing QUBO via EQUALITY and SLACK + EQUALITY.

    Decision variables x[i,b] = x[i*B + b] in {0,1}: item i in bin b.
    Model size = N * B (2D layout with N rows and B columns).

    Assignment constraint per item i: sum_b x[i,b] = 1  (EQUALITY, coeffs=1)
    Capacity constraint per bin b: sum_i s_i * x[i,b] <= C  (SLACK + EQUALITY)
    Objective: minimise total bins used = sum_b y_b, where y_b = max_i x[i,b].
    A soft proxy: add a small positive bias to linear[i*B+b] to penalise
    unnecessary bin usage.
    """
    problem = Problem("BinPacking")

    num_items = problem.input("num_items", type=Types.Int)
    num_bins_in = problem.input("num_bins", type=Types.Int)
    sizes_in = problem.input("sizes", type=Types.Vec)
    capacity_in = problem.input("capacity", type=Types.Int)

    # Model size = N * B  (2D: rows=items, cols=bins)
    problem.define_model(
        size=num_items * num_bins_in,
        domain=XQMXDomain.BINARY,
        rows=num_items,
        cols=num_bins_in,
    )

    # Small penalty to prefer fewer bins (objective proxy)
    with problem.range(0, num_items) as i:
        with problem.range(0, num_bins_in) as b:
            problem.model.linear[i, b].add(1)

    # Assignment constraint: each item i must go in exactly one bin
    # sum_b x[i,b] = 1  for each i
    with problem.range(0, num_items) as i:
        row_indices = problem.vec()
        row_coeffs = problem.vec()
        with problem.range(0, num_bins_in) as b:
            row_indices.push(i * num_bins_in + b)
            row_coeffs.push(1)
        problem.model.apply_equality(row_indices, row_coeffs, 1, 200)

    # Capacity constraint: sum_i s_i * x[i,b] <= C  for each bin b
    with problem.range(0, num_bins_in) as b:
        col_indices = problem.vec()
        col_coeffs = problem.vec()
        with problem.range(0, num_items) as i:
            col_indices.push(i * num_bins_in + b)
            col_coeffs.push(sizes_in.get(i))
        # Slack variables start at current model size (N*B)
        problem.slack(col_indices, col_coeffs, num_items * num_bins_in, capacity_in)
        problem.model.apply_equality(col_indices, col_coeffs, capacity_in, 100)

    # Stow total variable count so the decoder sees a single N reference
    total_vars = problem.stow("total_vars", num_items * num_bins_in)

    # Output: assignment matrix as flat vec [x[0,0], x[0,1], ..., x[N-1,B-1]]
    assignment = problem.output("assignment", type=Types.Vec)
    with problem.range(0, total_vars) as k:
        assignment.append(problem.sample.getline(k))

    return problem


def decode_assignment(flat: list[int], n: int, num_bins: int) -> list[int]:
    """Return bin index for each item (-1 if unassigned)."""
    result = []
    for i in range(n):
        assigned = -1
        for b in range(num_bins):
            if flat[i * num_bins + b]:
                assigned = b
                break
        result.append(assigned)
    return result


def run(
    programs: Any,
    n: int,
    num_bins: int,
    sizes: list[int],
    capacity: int,
    seed: int,
    backend: VMBackend,
    solver_name: str,
) -> tuple[int, int, list[int]]:
    """Full pipeline on the selected VM backend."""
    vm = VM(backend=backend)
    vm.set_calldata([n, num_bins, sizes, capacity])
    vm.set_output_slots(1)
    vm.run(programs.encoder)
    model = vm.outputs()[0]
    assert isinstance(model, XQMX)

    try:
        solver = build_solver(solver_name, seed=seed)
        sample = solver.solve(model).sample
    except (ImportError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    vm = VM(backend=backend)
    vm.set_calldata([model, sample, n * num_bins])
    vm.set_output_slots(2)
    vm.run(programs.verifier)
    outs = vm.outputs()
    energy, valid = outs[0], outs[1]

    vm = VM(backend=backend)
    vm.set_calldata([sample, n * num_bins])
    vm.set_output_slots(1)
    vm.run(programs.decoder)
    assign_out = vm.outputs()[0]
    total = n * num_bins
    if isinstance(assign_out, Vec):
        flat = [assign_out.get(i) for i in range(total)]
    else:
        flat = list(assign_out)

    return energy, valid, flat


def main() -> int:
    parser = argparse.ArgumentParser(description="Bin Packing end-to-end XQuad pipeline example")
    parser.add_argument("--n", type=int, default=4, help="Number of items (default: 4)")
    parser.add_argument("--bins", type=int, default=3, help="Number of bins (default: 3)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    parser.add_argument(
        "--interpreter",
        choices=("python", "rust"),
        default="python",
        help="XQVM interpreter to run the compiled programs on",
    )
    parser.add_argument(
        "--solver",
        choices=sorted(SOLVERS),
        default=DEFAULT_SOLVER,
        help="XQSA solver backend to sample the model with",
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
    capacity = rng.randint(args.n, args.n * 3)
    sizes = [rng.randint(1, capacity // 2) for _ in range(args.n)]

    problem = build_problem(args.n, args.bins, sizes, capacity)
    programs = problem.compile()

    backend = VMBackend.PYTHON if args.interpreter == "python" else VMBackend.RUST
    energy, valid, flat = run(programs, args.n, args.bins, sizes, capacity, args.seed, backend, args.solver)
    bin_for_item = decode_assignment(flat, args.n, args.bins)

    result = {
        "_seed": args.seed,
        "_note": "canonical CI golden",
        "n": args.n,
        "num_bins": args.bins,
        "sizes": sizes,
        "capacity": capacity,
        "assignment": bin_for_item,
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
