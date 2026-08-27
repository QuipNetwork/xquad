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
TSP end-to-end XQuad pipeline example.

Build a random Travelling-Salesman-Problem instance, compile it to
XQVM assembly via xqcp, run the encoder on the chosen VM (Python
reference or Rust), sample the resulting QUBO with xqsa's
SolverDWaveCPU, run the verifier and decoder on the sampled
solution, and print the decoded tour.

The runner is the showcase: a single file exercises every layer of
the toolchain. No pre-authored .xqasm files, no JSON inputs — just
CP in, decoded tour out.

Usage:
    uv run python examples/tsp/runner.py --seed 42
    uv run python examples/tsp/runner.py --n 5 --interpreter rust
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

from xquad.cp import Problem, Types, xq_triu
from xquad.sa import DEFAULT_SOLVER, SOLVERS, build_solver
from xquad.types import XQMX, Vec, XQMXDomain, triu
from xquad.vm import VM, VMBackend


def build_problem(n: int, seed: int) -> tuple[Problem, list[int]]:
    """Construct a TSP problem with a random symmetric distance matrix.

    Distances live in the upper triangle only (`n*(n-1)/2` entries)
    and are fed to the VM as a flat Vec indexed by xq_triu(i, j).
    """
    rng = random.Random(seed)
    distances = [rng.randint(1, 100) for _ in range(n * (n - 1) // 2)]

    problem = Problem("TSP")

    num_cities = problem.input("num_cities", type=Types.Int)
    distance_matrix = problem.input("distance_matrix", type=Types.Vec)

    problem.define_model(
        size=num_cities * num_cities,
        domain=XQMXDomain.BINARY,
        rows=num_cities,
        cols=num_cities,
    )

    with problem.range(0, num_cities - 1) as city_i:
        with problem.range(city_i + 1, num_cities) as city_j:
            dist = problem.stow("dist", distance_matrix.get(xq_triu(city_i, city_j)))
            with problem.range(0, num_cities) as position:
                next_position = (position + 1) % num_cities
                problem.model.quadratic[(city_i, position), (city_j, next_position)].add(dist)
                problem.model.quadratic[(city_j, position), (city_i, next_position)].add(dist)

    with problem.range(0, num_cities) as city:
        problem.model.apply_onehot_row(city, penalty=100)
    with problem.range(0, num_cities) as position:
        problem.model.apply_onehot_col(position, penalty=100)

    tour = problem.output("tour", type=Types.Vec)
    with problem.range(0, num_cities) as position:
        tour.append(problem.sample.colfind(col=position, value=1))

    return problem, distances


def tour_distance(tour: list[int], distances: list[int], n: int) -> int:
    """Sum the cost of a tour (distances indexed by upper-triangle triu)."""
    total = 0
    for p in range(n):
        i, j = tour[p], tour[(p + 1) % n]
        if i == j:
            continue
        total += distances[triu(i, j)]
    return total


def canonicalize_tour(tour: list[int]) -> list[int]:
    """Collapse a TSP tour to its canonical representation.

    A Hamiltonian cycle on `n` cities has 2n equivalent orderings: `n`
    rotations (the starting city is arbitrary) times 2 directions
    (forward vs reverse traversal). Both equivalences are semantic
    no-ops — same set of edges, same total distance, same energy —
    but naive sampler output can emit any of the 2n representations.

    This function picks the canonical one:

    1. Rotate so city 0 leads (handles the n rotations).
    2. Between the forward tour and its reverse, pick the lex-smaller
       one (handles the 2 directions).

    Rust and Python interpreters on different host platforms have been
    observed to disagree on direction alone (QUI-465) due to
    iteration-order differences in the decoder's XQMX scan; both
    invariants (energy / tour_distance / validity) stay identical.
    Lex-min canonicalisation closes that gap and lets
    `make example-smoke` diff byte-for-byte against `golden.json`.
    """
    if 0 not in tour:
        return tour
    idx = tour.index(0)
    rotated = tour[idx:] + tour[:idx]
    if len(rotated) <= 2:
        return rotated
    # Reverse the tail (all cities after city 0) so city 0 still leads.
    reversed_direction = [rotated[0]] + rotated[:0:-1]
    return min(rotated, reversed_direction)


def run(
    programs: Any, n: int, distances: list[int], seed: int, backend: VMBackend, solver_name: str
) -> tuple[int, int, list[int]]:
    """Full pipeline on the selected VM backend."""
    vm = VM(backend=backend)
    vm.set_calldata([n, distances])
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
    vm.set_calldata([n, distances, model, sample])
    vm.set_output_slots(2)
    vm.run(programs.verifier)
    outs = vm.outputs()
    energy, valid = outs[0], outs[1]

    vm = VM(backend=backend)
    vm.set_calldata([sample, n])
    vm.set_output_slots(1)
    vm.run(programs.decoder)
    tour_out = vm.outputs()[0]
    if isinstance(tour_out, Vec):
        tour_list = [tour_out.get(i) for i in range(n)]
    else:
        tour_list = list(tour_out)

    return energy, valid, tour_list


def main() -> int:
    parser = argparse.ArgumentParser(description="TSP end-to-end XQuad pipeline example")
    parser.add_argument("--n", type=int, default=4, help="Number of cities (default: 4)")
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

    problem, distances = build_problem(args.n, args.seed)
    programs = problem.compile()

    backend = VMBackend.PYTHON if args.interpreter == "python" else VMBackend.RUST
    energy, valid, tour = run(programs, args.n, distances, args.seed, backend, args.solver)
    tour = canonicalize_tour(tour)

    result = {
        "_seed": args.seed,
        "_note": "canonical CI golden",
        "n": args.n,
        "tour": tour,
        "tour_distance": tour_distance(tour, distances, args.n),
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
