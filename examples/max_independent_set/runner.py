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
Max Independent Set end-to-end XQuad pipeline example.

Find the largest subset of nodes in a graph such that no two selected nodes
share an edge.  Each edge (i,j) enforces x_i + x_j <= 1 via SLACK + EQUALITY:
the pair is penalised whenever both endpoints are selected.

Usage:
    uv run python examples/max_independent_set/runner.py --seed 42
    uv run python examples/max_independent_set/runner.py --n 6 --interpreter rust
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


def build_problem(n: int, edges: list[tuple[int, int]]) -> Problem:
    """Construct a Max Independent Set QUBO via SLACK + EQUALITY per edge.

    Decision variables x_i in {0,1}: x_i=1 means node i is in the set.
    Objective: maximise sum(x_i)  =>  minimise -sum(x_i).
    Constraint per edge (i,j): x_i + x_j <= 1.

    The inequality x_i + x_j <= 1 is encoded as SLACK + EQUALITY.
    A slack variable s (binary, capacity=1) is added so that
    x_i + x_j + s = 1 becomes the equality.  EQUALITY adds penalty
    P*(x_i + x_j + s - 1)^2.
    """
    problem = Problem("MaxIndependentSet")

    num_nodes = problem.input("num_nodes", type=Types.Int)
    num_edges = problem.input("num_edges", type=Types.Int)
    edges_in = problem.input("edges", type=Types.Vec)

    problem.define_model(size=num_nodes, domain=XQMXDomain.BINARY)

    # Objective: minimise -sum(x_i)
    with problem.range(0, num_nodes) as i:
        problem.model.linear[i].add(-1)

    # Per-edge constraint: x_i + x_j <= 1
    # Encoded as x_i + x_j + s = 1 with slack s in {0,1}
    # Slack variables start at model.size = num_nodes
    with problem.range(0, num_edges) as e:
        offset = e * 2
        ni = problem.stow("ni", edges_in.get(offset))
        nj = problem.stow("nj", edges_in.get(offset + 1))

        edge_indices = problem.vec()
        edge_coeffs = problem.vec()
        edge_indices.push(ni)
        edge_indices.push(nj)
        edge_coeffs.push(1)
        edge_coeffs.push(1)

        # SLACK appends 1 binary slack starting at num_nodes + e
        problem.slack(edge_indices, edge_coeffs, num_nodes + e, 1)
        problem.model.apply_equality(edge_indices, edge_coeffs, 1, 200)

    # Output: independent set membership
    in_set = problem.output("in_set", type=Types.Vec)
    with problem.range(0, num_nodes) as i:
        in_set.append(problem.sample.getline(i))

    return problem


def is_independent(selection: list[int], edges: list[tuple[int, int]]) -> bool:
    """Check that no two selected nodes share an edge."""
    return all(not (selection[i] and selection[j]) for i, j in edges)


def flatten_edges(edges: list[tuple[int, int]]) -> list[int]:
    out: list[int] = []
    for i, j in edges:
        out.extend((i, j))
    return out


def run(
    programs: Any,
    n: int,
    edges: list[tuple[int, int]],
    seed: int,
    backend: VMBackend,
) -> tuple[int, int, list[int]]:
    """Full pipeline on the selected VM backend."""
    flat = flatten_edges(edges)
    m = len(edges)

    vm = VM(backend=backend)
    vm.set_calldata([n, m, flat])
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
    set_out = vm.outputs()[0]
    if isinstance(set_out, Vec):
        selection = [set_out.get(i) for i in range(n)]
    else:
        selection = list(set_out)

    return energy, valid, selection


def main() -> int:
    parser = argparse.ArgumentParser(description="Max Independent Set end-to-end XQuad pipeline example")
    parser.add_argument("--n", type=int, default=5, help="Number of nodes (default: 5)")
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
    # Generate a random sparse graph (each edge included with 50% probability)
    edges = [(i, j) for i in range(args.n) for j in range(i + 1, args.n) if rng.random() < 0.5]

    problem = build_problem(args.n, edges)
    programs = problem.compile()

    backend = VMBackend.PYTHON if args.interpreter == "python" else VMBackend.RUST
    energy, valid, selection = run(programs, args.n, edges, args.seed, backend)

    result = {
        "_seed": args.seed,
        "_note": "canonical CI golden",
        "n": args.n,
        "edges": [list(e) for e in edges],
        "in_set": selection,
        "set_size": sum(selection),
        "is_independent": is_independent(selection, edges),
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
