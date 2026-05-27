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
Set Cover end-to-end XQuad pipeline example.

Given a universe of elements and a collection of sets, find the minimum
sub-collection that covers every element.  For each element, an ATLEAST
constraint enforces that at least one covering set is selected.

The coverage matrix (covers[e][s] = 1 if set s covers element e) is passed
as a flat runtime input.  A branch records a conditional VECPUSH so that
only covering set indices are pushed into each element's index vector.

Usage:
    uv run python examples/set_cover/runner.py --seed 42
    uv run python examples/set_cover/runner.py --num-sets 5 --interpreter rust
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


def build_problem(num_elements: int, num_sets: int, covers: list[list[int]]) -> Problem:
    """Construct a Set Cover QUBO via ATLEAST per element.

    Decision variables x_s in {0,1}: x_s=1 if set s is selected.
    Objective: minimise sum(x_s).
    Constraint per element e: sum_{s: covers[e][s]=1} x_s >= 1.

    The coverage membership matrix is passed as a flat vec of size E*S.
    A branch conditionally pushes set index s only when covers[e][s] != 0.
    """
    problem = Problem("SetCover")

    num_elements_in = problem.input("num_elements", type=Types.Int)
    num_sets_in = problem.input("num_sets", type=Types.Int)
    covers_in = problem.input("covers", type=Types.Vec)

    problem.define_model(size=num_sets_in, domain=XQMXDomain.BINARY)

    # Objective: minimise number of selected sets
    with problem.range(0, num_sets_in) as s:
        problem.model.linear[s].add(1)

    # Constraint per element: at least one covering set selected
    with problem.range(0, num_elements_in) as e:
        elem_indices = problem.vec()
        with problem.range(0, num_sets_in) as s:
            covers_es = problem.stow("covers_es", covers_in.get(e * num_sets_in + s))
            problem.branch(
                covers_es,
                lambda: elem_indices.push(s),
                None,
            )
        problem.model.apply_atleast(elem_indices, 1, 200)

    # Output: set selection
    selected = problem.output("selected", type=Types.Vec)
    with problem.range(0, num_sets_in) as s:
        selected.append(problem.sample.getline(s))

    return problem


def is_valid_cover(selected: list[int], covers: list[list[int]]) -> bool:
    """Check that every element is covered by at least one selected set."""
    return all(any(selected[s] and covers[e][s] for s in range(len(selected))) for e in range(len(covers)))


def run(
    programs: Any,
    num_elements: int,
    num_sets: int,
    covers: list[list[int]],
    seed: int,
    backend: VMBackend,
) -> tuple[int, int, list[int]]:
    """Full pipeline on the selected VM backend."""
    flat_covers = [covers[e][s] for e in range(num_elements) for s in range(num_sets)]

    vm = VM(backend=backend)
    vm.set_calldata([num_elements, num_sets, flat_covers])
    vm.set_output_slots(1)
    vm.run(programs.encoder)
    model = vm.outputs()[0]
    assert isinstance(model, XQMX)

    solver = SolverDWaveCPU(seed=seed)
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
    parser = argparse.ArgumentParser(description="Set Cover end-to-end XQuad pipeline example")
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
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Write the decoded result as JSON to this path; stdout otherwise",
    )
    args = parser.parse_args()

    rng = random.Random(args.seed)
    # Generate coverage matrix ensuring every element is covered by at least one set
    covers: list[list[int]] = [[0] * args.num_sets for _ in range(args.num_elements)]
    for e in range(args.num_elements):
        covers[e][rng.randrange(args.num_sets)] = 1
    for e in range(args.num_elements):
        for s in range(args.num_sets):
            if rng.random() < 0.4:
                covers[e][s] = 1

    problem = build_problem(args.num_elements, args.num_sets, covers)
    programs = problem.compile()

    backend = VMBackend.PYTHON if args.interpreter == "python" else VMBackend.RUST
    energy, valid, selected = run(programs, args.num_elements, args.num_sets, covers, args.seed, backend)

    result = {
        "_seed": args.seed,
        "_note": "canonical CI golden",
        "num_elements": args.num_elements,
        "num_sets": args.num_sets,
        "covers": covers,
        "selected": selected,
        "num_selected": sum(selected),
        "is_valid": is_valid_cover(selected, covers),
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
