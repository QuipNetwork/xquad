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
Portfolio Rebalance end-to-end XQuad pipeline example.

Choose an integer weight x_i in [-5, 5] for each of N assets, trading expected
return off against a risk matrix, subject to the weights summing to a budget B.
Negative weights are short positions, which is what the ranged integer domain
buys over a binary select-or-not model.

The weights are declared with `lo=`/`hi=`, so coefficients are written over
x in [-5, 5] while the model holds y = x - lo in {0, ..., 10}, and
`sample.value()` shifts back on the way out.

No solver samples an integer model yet -- that is XQSA v0.5.0 -- so `--solver`
is accepted and ignored, and the pipeline runs against a hand-picked assignment.
What that proves is stated in the README: the domain check, the shift and the
decode compose. It does not prove anything optimises.

Usage:
    uv run python examples/portfolio_rebalance/runner.py --seed 42
    uv run python examples/portfolio_rebalance/runner.py --n 6 --interpreter rust
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

from xquad.cp import Domain, Problem, Types, xq_grid
from xquad.sa import DEFAULT_SOLVER, SOLVERS
from xquad.types import XQMX, Vec
from xquad.vm import VM, VMBackend

# The weight domain, as literals: a runtime lo would want the decoder's one
# calldata scalar, which the output loop bound already spends.
_LO = -5
_HI = 5
_K = _HI - _LO + 1

# Budget the weights must sum to, and the penalty enforcing it.
_BUDGET = 1
_PENALTY = 50


def build_problem() -> Problem:
    """Construct a Portfolio Rebalance model over integer weights in [-5, 5].

    Decision variables x_i in [-5, 5]: the signed weight of asset i.
    Objective: -sum(r_i * x_i) + sum_{i <= j} C_ij * x_i * x_j
    Constraint: sum(x_i) = B, written by hand as P*(sum_i x_i - B)^2.

    The budget square is expanded here rather than handed to apply_equality,
    because every HLF expansion is derived under x^2 = x and xqcp refuses
    them off a binary model.  Expanded, P*(sum x - B)^2 is P on each
    diagonal, 2P on each off-diagonal pair, and -2PB on each linear
    coefficient; the P*B^2 constant is dropped, as EQUALITY drops its own.
    """
    problem = Problem("PortfolioRebalance")

    num_assets = problem.input("num_assets", type=Types.Int)
    returns_in = problem.input("returns", type=Types.Vec)
    cov_in = problem.input("cov", type=Types.Vec)

    problem.define_model(size=num_assets, domain=Domain.INTEGER, lo=_LO, hi=_HI)

    # Return objective, and the budget's linear term: -r_i * x_i - 2*P*B * x_i
    with problem.range(0, num_assets) as i:
        ri = problem.stow("ri", returns_in.get(i))
        problem.model.linear[i].add(-ri - 2 * _PENALTY * _BUDGET)

    # Risk objective on the upper triangle, and the budget's square
    with problem.range(0, num_assets) as i:
        problem.model.quadratic[i, i].add(cov_in.get(xq_grid(i, i, num_assets)) + _PENALTY)

        with problem.range(i + 1, num_assets) as j:
            cij = problem.stow("cij", cov_in.get(xq_grid(i, j, num_assets)))
            problem.model.quadratic[i, j].add(cij + 2 * _PENALTY)

    # Output: the signed weight per asset, shifted back out of the model
    weights = problem.output("weights", type=Types.Vec)
    with problem.range(0, num_assets) as i:
        weights.append(problem.sample.value(i))

    return problem


def pick_assignment(n: int) -> list[int]:
    """A weight vector inside [-5, 5] that sums to the budget.

    Hand-picked rather than solved: no solver samples an integer model yet.
    It carries a short position, so the decoded output exercises the whole
    domain and not just its non-negative half.
    """
    weights = [0] * n
    if n == 1:
        weights[0] = _BUDGET
        return weights
    weights[0] = _BUDGET + 1
    weights[1] = -1
    return weights


def run(
    programs: Any,
    n: int,
    returns: list[int],
    cov: list[int],
    weights: list[int],
    backend: VMBackend,
) -> tuple[int, int, list[int]]:
    """Full pipeline on the selected VM backend."""
    vm = VM(backend=backend)
    vm.set_calldata([n, returns, cov])
    vm.set_output_slots(1)
    vm.run(programs.encoder)
    model = vm.outputs()[0]
    assert isinstance(model, XQMX)

    sample = XQMX.integer_sample(n, _K)
    for index, weight in enumerate(weights):
        sample.linear[index] = weight - _LO

    vm = VM(backend=backend)
    vm.set_calldata([n, returns, cov, model, sample])
    vm.set_output_slots(2)
    vm.run(programs.verifier)
    outs = vm.outputs()
    energy, valid = outs[0], outs[1]

    vm = VM(backend=backend)
    vm.set_calldata([sample, n])
    vm.set_output_slots(1)
    vm.run(programs.decoder)
    weights_out = vm.outputs()[0]
    if isinstance(weights_out, Vec):
        decoded = [weights_out.get(i) for i in range(n)]
    else:
        decoded = list(weights_out)

    return energy, valid, decoded


def main() -> int:
    parser = argparse.ArgumentParser(description="Portfolio Rebalance end-to-end XQuad pipeline example")
    parser.add_argument("--n", type=int, default=5, help="Number of assets (default: 5)")
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
        help="Accepted for interface parity and ignored; no solver samples an integer model yet",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Write the decoded result as JSON to this path; stdout otherwise",
    )
    args = parser.parse_args()

    print(
        f"note: --solver {args.solver} is ignored; no solver samples an integer model yet, "
        "so the pipeline runs against a hand-picked assignment",
        file=sys.stderr,
    )

    rng = random.Random(args.seed)
    returns = [rng.randint(1, 20) for _ in range(args.n)]
    cov = [0] * (args.n * args.n)
    for i in range(args.n):
        for j in range(i, args.n):
            value = rng.randint(0, 6)
            cov[i * args.n + j] = value
            cov[j * args.n + i] = value

    weights = pick_assignment(args.n)
    problem = build_problem()
    programs = problem.compile()

    backend = VMBackend.PYTHON if args.interpreter == "python" else VMBackend.RUST
    energy, valid, decoded = run(programs, args.n, returns, cov, weights, backend)

    result = {
        "_seed": args.seed,
        "_note": "canonical CI golden",
        "n": args.n,
        "lo": _LO,
        "hi": _HI,
        "budget": _BUDGET,
        "returns": returns,
        "weights": decoded,
        "sums_to_budget": sum(decoded) == _BUDGET,
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
