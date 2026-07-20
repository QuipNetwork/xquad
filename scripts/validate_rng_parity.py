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

"""QUI-705 RNG-swap validation: energy-distribution parity for SolverCudaGPU.

Swapping the acceptance RNG (pre-generated float64 buffer -> on-device
xorshift64) changes the sampling stream, so trajectories cannot match
bit-for-bit. This harness checks the swap distributionally instead:

  run      solve seeded spin-glass instances (the bench_spin_packing
           generator) across many solver seeds; dump per-cell energies
           to JSON.
  compare  baseline vs candidate per cell: two-sample KS test
           (alpha=0.01), mean shift <= 2%, best-energy regression <= 2%.

Usage (run once on the pre-change commit, once on the candidate):
    uv run --no-sync python scripts/validate_rng_parity.py run \
        --out scratch/qui-705/baseline.json
    uv run --no-sync python scripts/validate_rng_parity.py run \
        --out scratch/qui-705/candidate.json
    uv run --no-sync python scripts/validate_rng_parity.py compare \
        scratch/qui-705/baseline.json scratch/qui-705/candidate.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_spin_packing import build_instance  # noqa: E402

from xqsa.cuda_gpu import SolverCudaGPU  # noqa: E402

SIZES = (256, 1024)
INSTANCE_SEEDS = (7, 11, 13)
SOLVER_SEEDS = tuple(range(100, 124))  # 24 independent streams per cell
NUM_READS = 64
NUM_SWEEPS = 500
BETA_RANGE = (0.1, 10.0)
KS_ALPHA = 0.01
MEAN_TOLERANCE = 0.02  # mirrors GLASS_TOLERANCE in test_gpu_validation.py
BEST_TOLERANCE = 0.02

SMOKE_SIZES = (64,)
SMOKE_INSTANCE_SEEDS = (7,)
SMOKE_SOLVER_SEEDS = tuple(range(100, 106))


def run(out_path: Path, smoke: bool) -> int:
    """Solve every (size, instance) cell across the solver seeds; dump JSON."""
    sizes = SMOKE_SIZES if smoke else SIZES
    instance_seeds = SMOKE_INSTANCE_SEEDS if smoke else INSTANCE_SEEDS
    solver_seeds = SMOKE_SOLVER_SEEDS if smoke else SOLVER_SEEDS

    cells: dict[str, list[float]] = {}
    for n in sizes:
        for iseed in instance_seeds:
            model, _h, _j = build_instance(n, iseed)
            energies = [
                float(
                    SolverCudaGPU(
                        num_reads=NUM_READS,
                        num_sweeps=NUM_SWEEPS,
                        beta_range=BETA_RANGE,
                        seed=sseed,
                    )
                    .solve(model)
                    .energy
                )
                for sseed in solver_seeds
            ]
            cells[f"n{n}_i{iseed}"] = energies
            print(f"n={n} instance={iseed}: best={min(energies):.0f} mean={np.mean(energies):.2f}")

    payload = {
        "params": {
            "sizes": list(sizes),
            "instance_seeds": list(instance_seeds),
            "solver_seeds": list(solver_seeds),
            "num_reads": NUM_READS,
            "num_sweeps": NUM_SWEEPS,
            "beta_range": list(BETA_RANGE),
        },
        "cells": cells,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"wrote {out_path}")
    return 0


def ks_statistic(a: np.ndarray, b: np.ndarray) -> float:
    """Two-sample Kolmogorov-Smirnov D statistic."""
    a, b = np.sort(a), np.sort(b)
    grid = np.concatenate([a, b])
    cdf_a = np.searchsorted(a, grid, side="right") / a.size
    cdf_b = np.searchsorted(b, grid, side="right") / b.size
    return float(np.max(np.abs(cdf_a - cdf_b)))


def ks_critical(m: int, n: int, alpha: float) -> float:
    """Asymptotic two-sample KS rejection threshold at the given alpha."""
    return math.sqrt(-math.log(alpha / 2.0) / 2.0) * math.sqrt((m + n) / (m * n))


def compare_cell(name: str, base: list[float], cand: list[float]) -> list[str]:
    """Return failure messages for one cell (empty list = cell passes)."""
    failures: list[str] = []
    d = ks_statistic(np.asarray(base), np.asarray(cand))
    d_crit = ks_critical(len(base), len(cand), KS_ALPHA)
    if d > d_crit:
        failures.append(f"{name}: KS D={d:.3f} > {d_crit:.3f} (alpha={KS_ALPHA})")
    base_mean = float(np.mean(base))
    shift = abs(float(np.mean(cand)) - base_mean)
    if shift > MEAN_TOLERANCE * abs(base_mean):
        failures.append(f"{name}: mean shift {shift:.2f} > {MEAN_TOLERANCE:.0%} of |{base_mean:.2f}|")
    if min(cand) > min(base) + BEST_TOLERANCE * abs(min(base)):
        failures.append(f"{name}: best energy regressed {min(base):.0f} -> {min(cand):.0f}")
    return failures


def compare(base_path: Path, cand_path: Path) -> int:
    """Compare two run outputs; print a verdict and return a process code."""
    base = json.loads(base_path.read_text())
    cand = json.loads(cand_path.read_text())
    if base["params"] != cand["params"]:
        print(f"FAIL: parameter mismatch\n  base: {base['params']}\n  cand: {cand['params']}")
        return 1
    failures: list[str] = []
    for name in sorted(base["cells"]):
        failures.extend(compare_cell(name, base["cells"][name], cand["cells"][name]))
    if failures:
        print("FAIL: distributional parity violated")
        for f in failures:
            print(f"  {f}")
        return 1
    print(f"PASS: {len(base['cells'])} cells within KS/mean/best-energy thresholds")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="QUI-705 RNG distributional parity check")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="solve the cell grid and dump energies to JSON")
    p_run.add_argument("--out", type=Path, required=True)
    p_run.add_argument(
        "--smoke",
        action="store_true",
        help="tiny grid for a quick sanity pass",
    )
    p_cmp = sub.add_parser("compare", help="compare a baseline run against a candidate run")
    p_cmp.add_argument("baseline", type=Path)
    p_cmp.add_argument("candidate", type=Path)
    args = parser.parse_args(argv)
    if args.cmd == "run":
        return run(args.out, args.smoke)
    return compare(args.baseline, args.candidate)


if __name__ == "__main__":
    sys.exit(main())
