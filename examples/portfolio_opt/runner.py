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
Portfolio Optimization end-to-end XQuad pipeline example.

Select a portfolio of exactly B assets from N candidates to maximise expected
return while penalising higher-order risk interactions.

Objective: -sum(r_i * x_i) + sum(sigma_ijk * x_i * x_j * x_k)
    return objective (linear) + cubic risk cross-terms (REDUCE)

Constraint: sum(x_i) = B  (budget: select exactly B assets, EQUALITY)

Usage:
    uv run python examples/portfolio_opt/runner.py --seed 42
    uv run python examples/portfolio_opt/runner.py --n 6 --budget 2 --interpreter rust
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

# Rosenberg penalty for REDUCE: must exceed max |sigma| among risk terms.
_P_AUX = 100


def build_problem(
    n: int,
    returns: list[int],
    budget: int,
    risk_terms: list[tuple[int, int, int, int]],
) -> Problem:
    """Construct a Portfolio Optimization QUBO via REDUCE + EQUALITY.

    Decision variables x_i in {0,1}: x_i=1 if asset i is selected.
    Objective: -sum(r_i * x_i) + sum(sigma * x_i * x_j * x_k)
    Constraint: sum(x_i) = budget  (EQUALITY with unit coefficients).

    Cubic risk terms (i, j, k, sigma) are degree-reduced via REDUCE(i, j) -> w,
    then ADDQUAD(w, k, sigma) encodes sigma*w*x_k.
    Budget constraint uses EQUALITY with uniform-coefficient index/coeff vecs.
    Risk terms are passed as a flat vec of stride 4: [i, j, k, sigma, ...].
    """
    problem = Problem("PortfolioOpt")

    num_assets = problem.input("num_assets", type=Types.Int)
    returns_in = problem.input("returns", type=Types.Vec)
    budget_in = problem.input("budget", type=Types.Int)
    num_risk = problem.input("num_risk", type=Types.Int)
    risk_in = problem.input("risk", type=Types.Vec)

    problem.define_model(size=num_assets, domain=XQMXDomain.BINARY)

    # Return objective: minimise -sum(r_i * x_i)
    with problem.range(0, num_assets) as i:
        ri = problem.stow("ri", returns_in.get(i))
        problem.model.linear[i].add(-ri)

    # Cubic risk cross-terms via REDUCE
    with problem.range(0, num_risk) as t:
        offset = problem.stow("offset", t * 4)
        ti = problem.stow("ti", risk_in.get(offset))
        tj = problem.stow("tj", risk_in.get(offset + 1))
        tk = problem.stow("tk", risk_in.get(offset + 2))
        sigma = problem.stow("sigma", risk_in.get(offset + 3))

        w = problem.model.reduce(ti, tj, _P_AUX)
        problem.model.quadratic[w, tk].add(sigma)

    # Budget constraint: sum(x_i) = B  (EQUALITY with unit coefficients)
    indices = problem.vec()
    coeffs = problem.vec()
    with problem.range(0, num_assets) as i:
        indices.push(i)
        coeffs.push(1)
    problem.model.apply_equality(indices, coeffs, budget_in, 200)

    # Output: portfolio selection
    portfolio = problem.output("portfolio", type=Types.Vec)
    with problem.range(0, num_assets) as i:
        portfolio.append(problem.sample.getline(i))

    return problem


def total_return(portfolio: list[int], returns: list[int]) -> int:
    return sum(r for x, r in zip(portfolio, returns) if x)


def run(
    programs: Any,
    n: int,
    returns: list[int],
    budget: int,
    risk_terms: list[tuple[int, int, int, int]],
    seed: int,
    backend: VMBackend,
) -> tuple[int, int, list[int]]:
    """Full pipeline on the selected VM backend."""
    flat_risk = [v for term in risk_terms for v in term]
    num_risk = len(risk_terms)

    vm = VM(backend=backend)
    vm.set_calldata([n, returns, budget, num_risk, flat_risk])
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
    port_out = vm.outputs()[0]
    if isinstance(port_out, Vec):
        portfolio = [port_out.get(i) for i in range(n)]
    else:
        portfolio = list(port_out)

    return energy, valid, portfolio


def main() -> int:
    parser = argparse.ArgumentParser(description="Portfolio Optimization end-to-end XQuad pipeline example")
    parser.add_argument("--n", type=int, default=5, help="Number of assets (default: 5)")
    parser.add_argument("--budget", type=int, default=2, help="Number of assets to select (default: 2)")
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
    returns = [rng.randint(5, 20) for _ in range(args.n)]

    # Generate a few cubic risk terms (distinct variable triples)
    num_risk = max(1, args.n - 2)
    risk_terms: list[tuple[int, int, int, int]] = []
    for _ in range(num_risk):
        i, j, k = sorted(rng.sample(range(args.n), 3))
        sigma = rng.randint(1, 10)
        risk_terms.append((i, j, k, sigma))

    problem = build_problem(args.n, returns, args.budget, risk_terms)
    programs = problem.compile()

    backend = VMBackend.PYTHON if args.interpreter == "python" else VMBackend.RUST
    energy, valid, portfolio = run(programs, args.n, returns, args.budget, risk_terms, args.seed, backend)

    result = {
        "_seed": args.seed,
        "_note": "canonical CI golden",
        "n": args.n,
        "budget": args.budget,
        "returns": returns,
        "risk_terms": [list(t) for t in risk_terms],
        "portfolio": portfolio,
        "total_return": total_return(portfolio, returns),
        "num_selected": sum(portfolio),
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
