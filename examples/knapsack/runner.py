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
Knapsack end-to-end XQuad pipeline example.

Given N items with integer weights and values, find the subset maximising
total value subject to a weight capacity constraint.  The constraint
`sum(w_i * x_i) <= W` is encoded as a weighted equality with SLACK + EQUALITY.

Usage:
    uv run python examples/knapsack/runner.py --seed 42
    uv run python examples/knapsack/runner.py --n 6 --interpreter rust
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

from xquad.cp import Domain, Problem, Types
from xquad.sa import DEFAULT_SOLVER, SOLVERS, build_solver
from xquad.types import XQMX, Vec
from xquad.vm import VM, VMBackend


def build_problem(n: int, weights: list[int], values: list[int], capacity: int) -> Problem:
    """Construct a Knapsack QUBO via SLACK + EQUALITY.

    Decision variables x_i in {0,1}: x_i=1 means item i is selected.
    Objective: maximise sum(v_i * x_i)  =>  minimise -sum(v_i * x_i).
    Constraint: sum(w_i * x_i) <= W  via SLACK + EQUALITY.
    """
    problem = Problem("Knapsack")

    num_items = problem.input("num_items", type=Types.Int)
    weights_in = problem.input("weights", type=Types.Vec)
    values_in = problem.input("values", type=Types.Vec)
    capacity_in = problem.input("capacity", type=Types.Int)

    problem.define_model(size=num_items, domain=Domain.BINARY)

    # Objective: minimise -sum(v_i * x_i)
    with problem.range(0, num_items) as i:
        vi = problem.stow("vi", values_in.get(i))
        problem.model.linear[i].add(-vi)

    # Constraint: sum(w_i * x_i) <= W
    # Build index vec [0..N-1] and weight coefficient vec
    indices = problem.vec()
    coeffs = problem.vec()
    with problem.range(0, num_items) as i:
        indices.push(i)
        coeffs.push(weights_in.get(i))

    # SLACK appends binary slack variables starting at model.size (= num_items)
    problem.slack(indices, coeffs, num_items, capacity_in)

    # EQUALITY: P * (sum(w_i*x_i + s_j * 2^j) - W)^2
    problem.model.apply_equality(indices, coeffs, capacity_in, 100)

    # Output: selected items
    selected = problem.output("selected", type=Types.Vec)
    with problem.range(0, num_items) as i:
        selected.append(problem.sample.getline(i))

    return problem


def total_value(selection: list[int], values: list[int]) -> int:
    return sum(v for x, v in zip(selection, values) if x)


def total_weight(selection: list[int], weights: list[int]) -> int:
    return sum(w for x, w in zip(selection, weights) if x)


def run(
    programs: Any,
    n: int,
    weights: list[int],
    values: list[int],
    capacity: int,
    seed: int,
    backend: VMBackend,
    solver_name: str,
) -> tuple[int, int, list[int]]:
    """Full pipeline on the selected VM backend."""
    vm = VM(backend=backend)
    vm.set_calldata([n, weights, values, capacity])
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
    vm.set_calldata([n, weights, values, capacity, model, sample])
    vm.set_output_slots(2)
    vm.run(programs.verifier)
    outs = vm.outputs()
    energy, valid = outs[0], outs[1]

    vm = VM(backend=backend)
    vm.set_calldata([sample, n])
    vm.set_output_slots(1)
    vm.run(programs.decoder)
    sel_out = vm.outputs()[0]
    if isinstance(sel_out, Vec):
        selection = [sel_out.get(i) for i in range(n)]
    else:
        selection = list(sel_out)

    return energy, valid, selection


def main() -> int:
    parser = argparse.ArgumentParser(description="Knapsack end-to-end XQuad pipeline example")
    parser.add_argument("--n", type=int, default=5, help="Number of items (default: 5)")
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
    weights = [rng.randint(1, 10) for _ in range(args.n)]
    values = [rng.randint(1, 20) for _ in range(args.n)]
    capacity = rng.randint(args.n, args.n * 5)

    problem = build_problem(args.n, weights, values, capacity)
    programs = problem.compile()

    backend = VMBackend.PYTHON if args.interpreter == "python" else VMBackend.RUST
    energy, valid, selection = run(programs, args.n, weights, values, capacity, args.seed, backend, args.solver)

    result = {
        "_seed": args.seed,
        "_note": "canonical CI golden",
        "n": args.n,
        "weights": weights,
        "values": values,
        "capacity": capacity,
        "selection": selection,
        "total_value": total_value(selection, values),
        "total_weight": total_weight(selection, weights),
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
