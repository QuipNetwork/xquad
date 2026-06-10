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
Weighted Set Cover end-to-end XQuad pipeline example.

A generalisation of Set Cover where each set s has a coverage capacity cap[s]
and each element e has a demand demand[e].  The constraint per element requires
the total capacity of selected covering sets to meet the demand:
sum_{s: covers[e][s]=1} cap[s] * x_s >= demand[e].

This uses ATLEASTW, the weighted at-least-k constraint.  Objective: minimise
total cost sum(cost[s] * x_s).

Usage:
    uv run python examples/weighted_set_cover/runner.py --seed 42
    uv run python examples/weighted_set_cover/runner.py --num-sets 5 --interpreter rust
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


def build_problem(
    num_elements: int,
    num_sets: int,
    covers: list[list[int]],
    caps: list[int],
    demands: list[int],
    costs: list[int],
) -> Problem:
    """Construct a Weighted Set Cover QUBO via ATLEASTW per element.

    Decision variables x_s in {0,1}: x_s=1 if set s is selected.
    Objective: minimise sum(cost[s] * x_s).
    Constraint per element e:
        sum_{s: covers[e][s]=1} cap[s] * x_s >= demand[e]  (ATLEASTW).

    Coverage, capacities, demands, and costs are all runtime inputs.
    A branch conditionally pushes (index s, capacity cap[s]) pairs into
    per-element index/coefficient vectors, building the ATLEASTW operands.
    """
    problem = Problem("WeightedSetCover")

    num_elements_in = problem.input("num_elements", type=Types.Int)
    num_sets_in = problem.input("num_sets", type=Types.Int)
    covers_in = problem.input("covers", type=Types.Vec)
    caps_in = problem.input("caps", type=Types.Vec)
    demands_in = problem.input("demands", type=Types.Vec)
    costs_in = problem.input("costs", type=Types.Vec)

    problem.define_model(size=num_sets_in, domain=XQMXDomain.BINARY)

    # Objective: minimise total cost
    with problem.range(0, num_sets_in) as s:
        cost_s = problem.stow("cost_s", costs_in.get(s))
        problem.model.linear[s].add(cost_s)

    # Constraint per element: weighted coverage >= demand
    with problem.range(0, num_elements_in) as e:
        elem_indices = problem.vec()
        elem_coeffs = problem.vec()
        with problem.range(0, num_sets_in) as s:
            covers_es = problem.stow("covers_es", covers_in.get(e * num_sets_in + s))
            cap_s = problem.stow("cap_s", caps_in.get(s))
            problem.branch(
                covers_es,
                lambda: (elem_indices.push(s), elem_coeffs.push(cap_s)),
                None,
            )
        demand_e = problem.stow("demand_e", demands_in.get(e))
        problem.model.apply_atleastw(elem_indices, elem_coeffs, demand_e, 200)

    # Output: set selection
    selected = problem.output("selected", type=Types.Vec)
    with problem.range(0, num_sets_in) as s:
        selected.append(problem.sample.getline(s))

    return problem


def is_valid_cover(
    selected: list[int],
    covers: list[list[int]],
    caps: list[int],
    demands: list[int],
) -> bool:
    """Check that every element's demand is met by selected covering sets."""
    return all(
        sum(selected[s] * caps[s] for s in range(len(selected)) if covers[e][s]) >= demands[e]
        for e in range(len(demands))
    )


def total_cost(selected: list[int], costs: list[int]) -> int:
    return sum(c for x, c in zip(selected, costs) if x)


def run(
    programs: Any,
    num_elements: int,
    num_sets: int,
    covers: list[list[int]],
    caps: list[int],
    demands: list[int],
    costs: list[int],
    seed: int,
    backend: VMBackend,
    solver_name: str,
) -> tuple[int, int, list[int]]:
    """Full pipeline on the selected VM backend."""
    flat_covers = [covers[e][s] for e in range(num_elements) for s in range(num_sets)]

    vm = VM(backend=backend)
    vm.set_calldata([num_elements, num_sets, flat_covers, caps, demands, costs])
    vm.set_output_slots(1)
    vm.run(programs.encoder)
    model = vm.outputs()[0]
    assert isinstance(model, XQMX)

    solver = build_solver(solver_name, seed=seed)
    sample = solver.solve(model).sample

    vm = VM(backend=backend)
    vm.set_calldata([model, sample, num_sets])
    vm.set_output_slots(2)
    vm.run(programs.verifier)
    outs = vm.outputs()
    energy, valid = outs[0], outs[1]

    vm = VM(backend=backend)
    vm.set_calldata([sample, num_sets])
    vm.set_output_slots(1)
    vm.run(programs.decoder)
    sel_out = vm.outputs()[0]
    if isinstance(sel_out, Vec):
        selected = [sel_out.get(i) for i in range(num_sets)]
    else:
        selected = list(sel_out)

    return energy, valid, selected


def main() -> int:
    parser = argparse.ArgumentParser(description="Weighted Set Cover end-to-end XQuad pipeline example")
    parser.add_argument("--num-elements", type=int, default=4, help="Number of elements (default: 4)")
    parser.add_argument("--num-sets", type=int, default=5, help="Number of sets (default: 5)")
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

    # Coverage matrix -- guarantee every element has at least one covering set
    covers: list[list[int]] = [[0] * args.num_sets for _ in range(args.num_elements)]
    for e in range(args.num_elements):
        covers[e][rng.randrange(args.num_sets)] = 1
    for e in range(args.num_elements):
        for s in range(args.num_sets):
            if rng.random() < 0.4:
                covers[e][s] = 1

    caps = [rng.randint(1, 3) for _ in range(args.num_sets)]
    demands = [1] * args.num_elements
    costs = [rng.randint(1, 10) for _ in range(args.num_sets)]

    problem = build_problem(args.num_elements, args.num_sets, covers, caps, demands, costs)
    programs = problem.compile()

    backend = VMBackend.PYTHON if args.interpreter == "python" else VMBackend.RUST
    energy, valid, selected = run(
        programs, args.num_elements, args.num_sets, covers, caps, demands, costs, args.seed, backend, args.solver
    )

    result = {
        "_seed": args.seed,
        "_note": "canonical CI golden",
        "num_elements": args.num_elements,
        "num_sets": args.num_sets,
        "covers": covers,
        "caps": caps,
        "demands": demands,
        "costs": costs,
        "selected": selected,
        "total_cost": total_cost(selected, costs),
        "is_valid": is_valid_cover(selected, covers, caps, demands),
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
