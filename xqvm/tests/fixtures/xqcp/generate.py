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

"""Regenerate the xqcp-compiled control-flow fixtures (W1-011).

Each fixture is real compiler output for a control-flow shape the jump-span
checker rule must keep accepting: a conditional inside a loop, a loop inside
a conditional, and a conditional inside a loop whose arm itself loops. The
encoder, verifier and decoder programs of each problem are written as
``<shape>.<program>.xqasm``; ``checker_regressions/jump_span.rs`` assembles
every one and requires both the baseline and the production checker to
accept it.

Run from the repository root after a compiler change, then review the diff:

    uv run --no-sync python xqvm/tests/fixtures/xqcp/generate.py
"""

from pathlib import Path

from xqcp import Domain, Problem, Types

HERE = Path(__file__).resolve().parent


def _base(name: str):
    problem = Problem(name)
    n = problem.input("n", type=Types.Int)
    problem.define_model(size=n, domain=Domain.BINARY)
    return problem, n


def if_inside_loop() -> Problem:
    problem, n = _base("IfInsideLoop")
    out = problem.output("selected", type=Types.Vec)
    with problem.range(0, n) as i:
        problem.branch(problem.sample.getline(i) == 1, lambda: out.append(i), None)
    return problem


def loop_inside_if() -> Problem:
    problem, n = _base("LoopInsideIf")
    out = problem.output("o", type=Types.Vec)

    def fill() -> None:
        with problem.range(0, n) as j:
            out.append(problem.sample.getline(j))

    problem.branch(problem.sample.getline(0) == 1, fill, lambda: out.append(-1))
    return problem


def if_inside_loop_with_looping_arm() -> Problem:
    problem, n = _base("IfInsideLoopWithLoopingArm")
    out = problem.output("o", type=Types.Vec)
    with problem.range(0, n) as i:

        def arm() -> None:
            with problem.range(0, n) as j:
                out.append(i + j)

        problem.branch(problem.sample.getline(i) == 1, arm, None)
    return problem


SHAPES = {
    "if_inside_loop": if_inside_loop,
    "loop_inside_if": loop_inside_if,
    "if_inside_loop_with_looping_arm": if_inside_loop_with_looping_arm,
}


def main() -> None:
    for shape, build in SHAPES.items():
        programs = build().compile()
        for kind in ("encoder", "verifier", "decoder"):
            path = HERE / f"{shape}.{kind}.xqasm"
            path.write_text(getattr(programs, kind))
            print(f"wrote {path.relative_to(HERE.parents[2])}")


if __name__ == "__main__":
    main()
