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
Vertex Cover end-to-end XQuad pipeline example.

Find the minimum subset of nodes such that every edge has at least one
endpoint in the subset.  Each edge (i,j) contributes an ATLEAST constraint:
sum(x_i, x_j) >= 1.

Usage:
    uv run python examples/vertex_cover/runner.py --seed 42
    uv run python examples/vertex_cover/runner.py --n 6 --interpreter rust
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


def build_problem(n: int, edges: list[tuple[int, int]]) -> Problem:
    """Construct a Vertex Cover QUBO via ATLEAST per edge.

    Decision variables x_v in {0,1}: x_v=1 if vertex v is in the cover.
    Objective: minimise sum(x_v).
    Constraint per edge (i,j): x_i + x_j >= 1  (ATLEAST with k=1).
    """
    problem = Problem("VertexCover")

    num_nodes = problem.input("num_nodes", type=Types.Int)
    num_edges = problem.input("num_edges", type=Types.Int)
    edges_in = problem.input("edges", type=Types.Vec)

    problem.define_model(size=num_nodes, domain=Domain.BINARY)

    # Objective: minimise cover size
    with problem.range(0, num_nodes) as v:
        problem.model.linear[v].add(1)

    # Constraint per edge: at least one endpoint in cover
    with problem.range(0, num_edges) as e:
        offset = problem.stow("offset", e * 2)
        ni = problem.stow("ni", edges_in.get(offset))
        nj = problem.stow("nj", edges_in.get(offset + 1))

        edge_indices = problem.vec()
        edge_indices.push(ni)
        edge_indices.push(nj)

        problem.model.apply_atleast(edge_indices, 1, 200)

    # Output: cover membership
    cover = problem.output("cover", type=Types.Vec)
    with problem.range(0, num_nodes) as v:
        cover.append(problem.sample.getline(v))

    return problem


def is_valid_cover(cover: list[int], edges: list[tuple[int, int]]) -> bool:
    """Check that every edge has at least one covered endpoint."""
    return all(cover[i] or cover[j] for i, j in edges)


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
    solver_name: str,
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
    cover_out = vm.outputs()[0]
    if isinstance(cover_out, Vec):
        cover = [cover_out.get(i) for i in range(n)]
    else:
        cover = list(cover_out)

    return energy, valid, cover


def main() -> int:
    parser = argparse.ArgumentParser(description="Vertex Cover end-to-end XQuad pipeline example")
    parser.add_argument("--n", type=int, default=5, help="Number of nodes (default: 5)")
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
    edges = [(i, j) for i in range(args.n) for j in range(i + 1, args.n) if rng.random() < 0.6]

    problem = build_problem(args.n, edges)
    programs = problem.compile()

    backend = VMBackend.PYTHON if args.interpreter == "python" else VMBackend.RUST
    energy, valid, cover = run(programs, args.n, edges, args.seed, backend, args.solver)

    result = {
        "_seed": args.seed,
        "_note": "canonical CI golden",
        "n": args.n,
        "edges": [list(e) for e in edges],
        "cover": cover,
        "cover_size": sum(cover),
        "is_valid": is_valid_cover(cover, edges),
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
