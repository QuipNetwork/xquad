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
Quartic Optimization end-to-end XQuad pipeline example.

Minimise a degree-4 pseudo-Boolean objective via two-stage REDUCE chaining.
Each quartic term c*x_i*x_j*x_k*x_l is reduced in two steps:
    w = REDUCE(i, j)       -- w represents x_i*x_j
    v = REDUCE(w, k)       -- v represents x_i*x_j*x_k
    ADDQUAD(v, l, c)       -- adds c*v*x_l = c*x_i*x_j*x_k*x_l

Usage:
    uv run python examples/quartic_opt/runner.py --seed 42
    uv run python examples/quartic_opt/runner.py --n 6 --m 3 --interpreter rust
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

# Rosenberg penalty: must exceed the max absolute coefficient of any quartic term.
_P_AUX = 100


def build_problem(n: int, m: int, terms: list[tuple[int, int, int, int, int]]) -> Problem:
    """Construct a quartic binary optimisation QUBO via chained REDUCE.

    Objective: sum(c * x_i * x_j * x_k * x_l) - sum(x_v)
    Each quartic term (i, j, k, l, c) is degree-reduced in two stages:
        w = REDUCE(i, j, P_AUX)    -- w approximates x_i*x_j
        v = REDUCE(w, k, P_AUX)    -- v approximates x_i*x_j*x_k
        ADDQUAD(v, l, c)            -- c*v*x_l = c*x_i*x_j*x_k*x_l
    Each term introduces 2 auxiliary variables.
    Terms are passed as a flat vec of stride 5: [i, j, k, l, c, ...].
    """
    problem = Problem("QuarticOpt")

    num_vars = problem.input("num_vars", type=Types.Int)
    num_terms = problem.input("num_terms", type=Types.Int)
    terms_in = problem.input("terms", type=Types.Vec)

    problem.define_model(size=num_vars, domain=XQMXDomain.BINARY)

    # Linear bias: -1 per variable to reward selection
    with problem.range(0, num_vars) as v:
        problem.model.linear[v].add(-1)

    # Quartic terms via two-stage REDUCE
    with problem.range(0, num_terms) as t:
        offset = problem.stow("offset", t * 5)
        ti = problem.stow("ti", terms_in.get(offset))
        tj = problem.stow("tj", terms_in.get(offset + 1))
        tk = problem.stow("tk", terms_in.get(offset + 2))
        tm = problem.stow("tm", terms_in.get(offset + 3))
        coeff = problem.stow("coeff", terms_in.get(offset + 4))

        # Stage 1: w = x_i * x_j
        w = problem.model.reduce(ti, tj, _P_AUX)
        # Stage 2: v = w * x_k = x_i * x_j * x_k
        v = problem.model.reduce(w, tk, _P_AUX)
        # Quadratic term: coeff * v * x_m = coeff * x_i * x_j * x_k * x_m
        problem.model.quadratic[v, tm].add(coeff)

    # Output: variable assignment
    assignment = problem.output("assignment", type=Types.Vec)
    with problem.range(0, num_vars) as v:
        assignment.append(problem.sample.getline(v))

    return problem


def eval_objective(assignment: list[int], terms: list[tuple[int, int, int, int, int]]) -> int:
    quartic = sum(c * assignment[i] * assignment[j] * assignment[k] * assignment[m] for i, j, k, m, c in terms)
    linear = -sum(assignment)
    return quartic + linear


def run(
    programs: Any,
    n: int,
    m: int,
    terms: list[tuple[int, int, int, int, int]],
    seed: int,
    backend: VMBackend,
    solver_name: str,
) -> tuple[int, int, list[int]]:
    """Full pipeline on the selected VM backend."""
    flat = [v for term in terms for v in term]

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
    parser = argparse.ArgumentParser(description="Quartic Optimization end-to-end XQuad pipeline example")
    parser.add_argument("--n", type=int, default=5, help="Number of variables (default: 5)")
    parser.add_argument("--m", type=int, default=2, help="Number of quartic terms (default: 2)")
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
    terms: list[tuple[int, int, int, int, int]] = []
    for _ in range(args.m):
        i, j, k, m = sorted(rng.sample(range(args.n), 4))
        coeff = rng.randint(5, 20)
        terms.append((i, j, k, m, coeff))

    problem = build_problem(args.n, args.m, terms)
    programs = problem.compile()

    backend = VMBackend.PYTHON if args.interpreter == "python" else VMBackend.RUST
    energy, valid, assignment = run(programs, args.n, args.m, terms, args.seed, backend, args.solver)

    result = {
        "_seed": args.seed,
        "_note": "canonical CI golden",
        "n": args.n,
        "m": args.m,
        "terms": [list(t) for t in terms],
        "assignment": assignment,
        "objective": eval_objective(assignment, terms),
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
