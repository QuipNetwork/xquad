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

"""Unit tests for the pure helpers in scripts/scale_sweep.py."""

from __future__ import annotations

import scale_sweep


def test_adapters_registry_has_maxcut_and_tsp():
    adapters = scale_sweep.make_adapters()
    assert set(adapters) == {"maxcut", "tsp"}
    mc, tsp = adapters["maxcut"], adapters["tsp"]
    assert mc.ladder == (8, 16, 32, 64, 128, 256, 512)
    assert mc.gpu_ladder == (8, 16, 32, 64, 128, 256, 512, 1024)
    assert tsp.ladder == (4, 6, 8, 12, 16, 24, 32, 48)
    assert tsp.gpu_ladder == (4, 6, 8, 12, 16, 24, 32, 48, 64)


def test_maxcut_structural_check():
    mc = scale_sweep.make_adapters()["maxcut"]
    assert mc.structurally_valid([0, 1, 1, 0], 4)
    assert not mc.structurally_valid([0, 1, 2, 0], 4)  # non-binary
    assert not mc.structurally_valid([0, 1, 1], 4)  # wrong length


def test_tsp_structural_check():
    tsp = scale_sweep.make_adapters()["tsp"]
    assert tsp.structurally_valid([2, 0, 3, 1], 4)
    assert not tsp.structurally_valid([0, 0, 3, 1], 4)  # repeated city
    assert not tsp.structurally_valid([0, 1, 2], 4)  # wrong length


def test_model_shape_formulas():
    adapters = scale_sweep.make_adapters()
    assert adapters["maxcut"].model_vars(8) == 8
    assert adapters["maxcut"].approx_quad_terms(8) == 28  # n(n-1)/2
    assert adapters["tsp"].model_vars(6) == 36  # n^2
    assert adapters["tsp"].approx_quad_terms(6) == 2 * 36 * 5  # 2 n^2 (n-1)


def test_build_matrix_cpu_only():
    adapters = scale_sweep.make_adapters()
    lanes = scale_sweep.build_matrix(adapters, solvers=["dwave-cpu"], smoke=False)
    keys = {(lane.problem, lane.backend, lane.solver) for lane in lanes}
    assert keys == {
        ("maxcut", "python", "dwave-cpu"),
        ("maxcut", "rust", "dwave-cpu"),
        ("tsp", "python", "dwave-cpu"),
        ("tsp", "rust", "dwave-cpu"),
    }
    assert all(lane.ladder == adapters[lane.problem].ladder for lane in lanes)


def test_build_matrix_gpu_lanes_rust_only_with_extended_ladder():
    adapters = scale_sweep.make_adapters()
    lanes = scale_sweep.build_matrix(adapters, solvers=["dwave-cpu", "cuda-gpu"], smoke=False)
    gpu = [lane for lane in lanes if lane.solver == "cuda-gpu"]
    assert {(lane.problem, lane.backend) for lane in gpu} == {("maxcut", "rust"), ("tsp", "rust")}
    assert all(lane.ladder == adapters[lane.problem].gpu_ladder for lane in gpu)


def test_build_matrix_smoke_truncates_ladders():
    adapters = scale_sweep.make_adapters()
    lanes = scale_sweep.build_matrix(adapters, solvers=["dwave-cpu"], smoke=True)
    assert all(len(lane.ladder) == 1 for lane in lanes)
    assert all(lane.ladder[0] == adapters[lane.problem].ladder[0] for lane in lanes)


def _rec(
    problem="maxcut",
    backend="rust",
    solver="dwave-cpu",
    n=8,
    seed=42,
    status="ok",
    energy=-10,
    solution=None,
    solve_s=0.5,
):
    rec = {
        "schema": 1,
        "problem": problem,
        "backend": backend,
        "solver": solver,
        "n": n,
        "seed": seed,
        "status": status,
    }
    if status == "ok":
        rec.update(
            stages={"compile": 0.1, "encode": 0.1, "solve": solve_s, "verify": 0.1, "decode": 0.1},
            model={"vars": n, "approx_quad_terms": n * (n - 1) // 2},
            energy=energy,
            valid=1,
            structural_ok=True,
            objective=-energy,
            solution=solution or [0, 1] * (n // 2),
            peak_rss_kib=1000,
        )
    return rec


def test_lane_ceilings_report_max_ok_n():
    records = [_rec(n=8), _rec(n=16), _rec(n=32, status="timeout")]
    ceilings = scale_sweep.lane_ceilings(records)
    assert ceilings[("maxcut", "rust", "dwave-cpu")] == 16


def test_lane_ceilings_excludes_partially_completed_rung():
    records = [
        _rec(n=8, seed=42),
        _rec(n=16, seed=42),
        _rec(n=16, seed=43, status="timeout"),
    ]
    ceilings = scale_sweep.lane_ceilings(records)
    assert ceilings[("maxcut", "rust", "dwave-cpu")] == 8


def test_solution_divergences_detect_cross_vm_difference():
    match = [_rec(backend="python"), _rec(backend="rust")]
    assert scale_sweep.solution_divergences(match) == []
    diverged = [
        _rec(backend="python", solution=[0, 0, 0, 0, 1, 1, 1, 1]),
        _rec(backend="rust", solution=[0, 1, 0, 1, 0, 1, 0, 1]),
    ]
    divergences = scale_sweep.solution_divergences(diverged)
    assert len(divergences) == 1 and divergences[0]["n"] == 8 and divergences[0]["seed"] == 42


def test_parse_point_stdout_takes_last_nonempty_line():
    noisy = 'runner says hi\n\n{"status": "ok", "n": 8}\n'
    assert scale_sweep.parse_point_stdout(noisy) == {"status": "ok", "n": 8}
    assert scale_sweep.parse_point_stdout("garbage\nnot json") is None
    assert scale_sweep.parse_point_stdout("") is None
    assert scale_sweep.parse_point_stdout("[1, 2]") is None  # a record must be a dict


def test_median_averages_the_two_central_values_when_even():
    assert scale_sweep._median([4.0, 1.0, 3.0, 2.0]) == 2.5
    assert scale_sweep._median([3.0, 1.0, 2.0]) == 2.0


def test_solver_deltas_pair_gpu_against_cpu():
    records = [_rec(solver="dwave-cpu", energy=-10), _rec(solver="cuda-gpu", energy=-12)]
    deltas = scale_sweep.solver_deltas(records)
    assert len(deltas) == 1
    assert deltas[0]["energy_delta"] == -2  # GPU found a lower (better) energy


def test_render_report_mentions_ceilings_and_failures():
    records = [_rec(n=8), _rec(n=16, status="crash")]
    report = scale_sweep.render_report(records)
    assert "maxcut" in report and "16" in report and "crash" in report


def test_render_report_crash_with_no_stderr_does_not_say_timeout():
    oom = _rec(n=16, status="crash")
    oom["exit_code"] = -9
    oom["stderr_tail"] = ""
    report = scale_sweep.render_report([oom])
    assert "timeout" not in report
    assert "crash" in report
    assert "exit code -9, no stderr" in report
