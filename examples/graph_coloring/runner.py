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
Graph Coloring end-to-end XQuad pipeline example.

Assign one of C colors to each node so that no two adjacent nodes share a
color (proper graph coloring).  The model is a 2D grid of N*C binary variables
x[v,c] = 1 if node v gets color c.

ONEHOTR enforces that each node receives exactly one color (one-hot per row).
EXCLUDE enforces that adjacent nodes do not share a color (per edge per color).

Usage:
    uv run python examples/graph_coloring/runner.py --seed 42
    uv run python examples/graph_coloring/runner.py --n 6 --colors 3 --interpreter rust
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


def build_problem(n: int, num_colors: int, edges: list[tuple[int, int]]) -> Problem:
    """Construct a Graph Coloring QUBO via ONEHOTR + EXCLUDE on a 2D grid model.

    Variables x[v,c] in {0,1}: x[v,c]=1 if node v is assigned color c.
    Model layout: N rows (nodes) x C cols (colors), size = N*C.

    ONEHOTR per node: sum_c x[v,c] = 1 (each node gets exactly one color).
    EXCLUDE per (edge, color): x[u,c] and x[v,c] cannot both be 1
      (adjacent nodes may not share a color).
    """
    problem = Problem("GraphColoring")

    num_nodes = problem.input("num_nodes", type=Types.Int)
    num_colors_in = problem.input("num_colors", type=Types.Int)
    num_edges = problem.input("num_edges", type=Types.Int)
    edges_in = problem.input("edges", type=Types.Vec)

    problem.define_model(
        size=num_nodes * num_colors_in,
        domain=XQMXDomain.BINARY,
        rows=num_nodes,
        cols=num_colors_in,
    )

    # One-hot constraint per node: each node gets exactly one color
    with problem.range(0, num_nodes) as node:
        problem.model.apply_onehot_row(node, 200)

    # Exclusion constraint per edge per color: adjacent nodes cannot share a color
    with problem.range(0, num_edges) as e:
        offset = problem.stow("offset", e * 2)
        u = problem.stow("u", edges_in.get(offset))
        v = problem.stow("v", edges_in.get(offset + 1))

        with problem.range(0, num_colors_in) as c:
            problem.model.apply_exclude((u, c), (v, c), 200)

    # Stow total variable count so the decoder sees a single N reference
    total_vars = problem.stow("total_vars", num_nodes * num_colors_in)

    # Output: flat 2D assignment [x[0,0], x[0,1], ..., x[N-1,C-1]]
    assignment = problem.output("assignment", type=Types.Vec)
    with problem.range(0, total_vars) as k:
        assignment.append(problem.sample.getline(k))

    return problem


def decode_coloring(flat: list[int], n: int, num_colors: int) -> list[int]:
    """Return the color index assigned to each node, or -1 if uncolored."""
    colors = []
    for v in range(n):
        assigned = -1
        for c in range(num_colors):
            if flat[v * num_colors + c]:
                assigned = c
                break
        colors.append(assigned)
    return colors


def is_valid_coloring(colors: list[int], edges: list[tuple[int, int]]) -> bool:
    """Check no two adjacent nodes share a color and every node is colored."""
    return all(colors[u] >= 0 and colors[v] >= 0 and colors[u] != colors[v] for u, v in edges)


def flatten_edges(edges: list[tuple[int, int]]) -> list[int]:
    out: list[int] = []
    for u, v in edges:
        out.extend((u, v))
    return out


def run(
    programs: Any,
    n: int,
    num_colors: int,
    edges: list[tuple[int, int]],
    seed: int,
    backend: VMBackend,
    solver_name: str,
) -> tuple[int, int, list[int]]:
    """Full pipeline on the selected VM backend."""
    flat_edges = flatten_edges(edges)
    m = len(edges)
    total_vars = n * num_colors

    vm = VM(backend=backend)
    vm.set_calldata([n, num_colors, m, flat_edges])
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
    vm.set_calldata([n, num_colors, m, flat_edges, model, sample])
    vm.set_output_slots(2)
    vm.run(programs.verifier)
    outs = vm.outputs()
    energy, valid = outs[0], outs[1]

    vm = VM(backend=backend)
    vm.set_calldata([sample, total_vars])
    vm.set_output_slots(1)
    vm.run(programs.decoder)
    assign_out = vm.outputs()[0]
    if isinstance(assign_out, Vec):
        flat = [assign_out.get(i) for i in range(total_vars)]
    else:
        flat = list(assign_out)

    return energy, valid, flat


def main() -> int:
    parser = argparse.ArgumentParser(description="Graph Coloring end-to-end XQuad pipeline example")
    parser.add_argument("--n", type=int, default=5, help="Number of nodes (default: 5)")
    parser.add_argument("--colors", type=int, default=4, help="Number of colors (default: 4)")
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
    # A G(n, 0.5) graph on 5 nodes routinely contains a K4 -- the default seed's
    # does -- so 3 colours would make the canonical instance unsatisfiable and
    # the verifier would correctly report valid = 0.
    edges = [(i, j) for i in range(args.n) for j in range(i + 1, args.n) if rng.random() < 0.5]

    problem = build_problem(args.n, args.colors, edges)
    programs = problem.compile()

    backend = VMBackend.PYTHON if args.interpreter == "python" else VMBackend.RUST
    energy, valid, flat = run(programs, args.n, args.colors, edges, args.seed, backend, args.solver)
    colors = decode_coloring(flat, args.n, args.colors)

    result = {
        "_seed": args.seed,
        "_note": "canonical CI golden",
        "n": args.n,
        "num_colors": args.colors,
        "edges": [list(e) for e in edges],
        "colors": colors,
        "is_valid": is_valid_coloring(colors, edges),
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
