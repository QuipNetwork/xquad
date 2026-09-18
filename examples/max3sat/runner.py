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
Max-3-SAT end-to-end XQuad pipeline example.

Given M clauses of 3 positive literals over N binary variables, find the
assignment maximising the number of satisfied clauses.

Each clause (i,j,k) is violated when x_i=0, x_j=0, x_k=0.  The violation
penalty P*(1-x_i)(1-x_j)(1-x_k) expands to a cubic QUBO with a cubic term
-P*x_i*x_j*x_k that is degree-reduced via REDUCE to a quadratic term.

Usage:
    uv run python examples/max3sat/runner.py --seed 42
    uv run python examples/max3sat/runner.py --n 8 --m 10 --interpreter rust
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

# Penalty per unsatisfied clause and Rosenberg auxiliary penalty.
# P_AUX must exceed the magnitude of the cubic term coefficient (P_CLAUSE).
_P_CLAUSE = 10
_P_AUX = 50


def build_problem(n: int, m: int, clauses: list[tuple[int, int, int]]) -> Problem:
    """Construct a Max-3-SAT QUBO via REDUCE for the cubic clause terms.

    Decision variables x_v in {0,1}.
    Objective: minimise sum over clauses of P*(1-x_i)(1-x_j)(1-x_k).

    Expanding (1-x_i)(1-x_j)(1-x_k) = 1 - x_i - x_j - x_k
      + x_i*x_j + x_i*x_k + x_j*x_k - x_i*x_j*x_k.
    Dropping the constant, the QUBO has linear, quadratic, and cubic terms.
    The cubic term -P*x_i*x_j*x_k is degree-reduced via REDUCE(i, j) -> w,
    yielding the quadratic term -P*w*x_k plus Rosenberg enforcement.
    """
    problem = Problem("Max3SAT")

    num_vars = problem.input("num_vars", type=Types.Int)
    num_clauses = problem.input("num_clauses", type=Types.Int)
    clauses_in = problem.input("clauses", type=Types.Vec)

    problem.define_model(size=num_vars, domain=Domain.BINARY)

    with problem.range(0, num_clauses) as c:
        offset = problem.stow("offset", c * 3)
        ci = problem.stow("ci", clauses_in.get(offset))
        cj = problem.stow("cj", clauses_in.get(offset + 1))
        ck = problem.stow("ck", clauses_in.get(offset + 2))

        # Linear terms from violation penalty expansion
        problem.model.linear[ci].add(-_P_CLAUSE)
        problem.model.linear[cj].add(-_P_CLAUSE)
        problem.model.linear[ck].add(-_P_CLAUSE)

        # Quadratic terms from violation penalty expansion
        problem.model.quadratic[ci, cj].add(_P_CLAUSE)
        problem.model.quadratic[ci, ck].add(_P_CLAUSE)
        problem.model.quadratic[cj, ck].add(_P_CLAUSE)

        # Cubic term -P*x_i*x_j*x_k: reduce (i,j) -> w, then add -P*w*x_k
        w = problem.model.reduce(ci, cj, _P_AUX)
        problem.model.quadratic[w, ck].add(-_P_CLAUSE)

    # Output: variable assignment
    assignment = problem.output("assignment", type=Types.Vec)
    with problem.range(0, num_vars) as v:
        assignment.append(problem.sample.getline(v))

    return problem


def count_satisfied(assignment: list[int], clauses: list[tuple[int, int, int]]) -> int:
    return sum(1 for i, j, k in clauses if assignment[i] or assignment[j] or assignment[k])


def run(
    programs: Any,
    n: int,
    m: int,
    clauses: list[tuple[int, int, int]],
    seed: int,
    backend: VMBackend,
    solver_name: str,
) -> tuple[int, int, list[int]]:
    """Full pipeline on the selected VM backend."""
    flat = [v for clause in clauses for v in clause]

    vm = VM(backend=backend)
    vm.set_calldata([n, m, flat])
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
    vm.set_calldata([n, m, flat, model, sample])
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
    parser = argparse.ArgumentParser(description="Max-3-SAT end-to-end XQuad pipeline example")
    parser.add_argument("--n", type=int, default=6, help="Number of variables (default: 6)")
    parser.add_argument("--m", type=int, default=8, help="Number of clauses (default: 8)")
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
    clauses = [tuple(sorted(rng.sample(range(args.n), 3))) for _ in range(args.m)]

    problem = build_problem(args.n, args.m, clauses)
    programs = problem.compile()

    backend = VMBackend.PYTHON if args.interpreter == "python" else VMBackend.RUST
    energy, valid, assignment = run(programs, args.n, args.m, clauses, args.seed, backend, args.solver)

    result = {
        "_seed": args.seed,
        "_note": "canonical CI golden",
        "n": args.n,
        "m": args.m,
        "clauses": [list(c) for c in clauses],
        "assignment": assignment,
        "satisfied": count_satisfied(assignment, clauses),
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
