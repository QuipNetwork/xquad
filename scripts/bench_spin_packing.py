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
QUI-704 benchmark: dense int32 vs bit-packed spin storage for SolverCudaGPU.

Runs the shipped solver and a bench-local bit-packed variant of the same
CUDA kernel on identical seeded instances with an identical RNG stream and
fixed beta range, so trajectories match bit-for-bit. Reports per-buffer
analytic sizes, measured mempool peaks, and median wall times.

Requires an NVIDIA GPU + the xqsa[cuda] extra. Results are written as JSON
to scratch/spin_packing/ (not committed); see the QUI-704 findings doc.

Usage:
    uv run --no-sync python scripts/bench_spin_packing.py
    uv run --no-sync python scripts/bench_spin_packing.py --sizes 512
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Any

import numpy as np

from xqsa.cuda_gpu import SolverCudaGPU
from xqvm_py.xqmx import XQMX

REPO_ROOT = Path(__file__).resolve().parent.parent

NUM_READS = 64
NUM_SWEEPS = 500
SEED = 42
BETA_RANGE = (0.1, 10.0)
SIZES = (512, 1024, 2048, 4096)
REPS = 3
COUPLINGS_PER_NODE = 8  # sparse couplings keep XQMX construction fast; J stays dense n^2


def build_instance(n: int, seed: int) -> tuple[XQMX, np.ndarray, np.ndarray]:
    """Seeded spin instance: each node couples to its next COUPLINGS_PER_NODE
    neighbours with random +/-1. Returns the XQMX (for the stock solver) and
    the matching dense (h, J) arrays (for the packed prototype). J is stored
    symmetric-full, mirroring the layout SolverCudaGPU builds internally —
    verified end-to-end by Task 2's exact-trajectory check."""
    rng = random.Random(seed)
    model = XQMX.spin_model(n)
    h = np.zeros(n, dtype=np.float64)
    j_matrix = np.zeros((n, n), dtype=np.float64)
    for i in range(n):
        for k in range(1, COUPLINGS_PER_NODE + 1):
            j = i + k
            if j >= n:
                break
            coupling = rng.choice((-1.0, 1.0))
            model.set_quadratic(i, j, coupling)
            j_matrix[i, j] = coupling
            j_matrix[j, i] = coupling
    return model, h, j_matrix


def _mempool_peak(cupy) -> int:
    return int(cupy.get_default_memory_pool().total_bytes())


def run_stock(model: XQMX, n: int) -> dict:
    """Run the shipped SolverCudaGPU REPS times; record wall times, the
    (deterministic) best energy/sample, and the mempool peak."""
    import cupy

    solver = SolverCudaGPU(num_reads=NUM_READS, num_sweeps=NUM_SWEEPS, beta_range=BETA_RANGE, seed=SEED)
    cupy.get_default_memory_pool().free_all_blocks()
    walls: list[float] = []
    result = None
    for _ in range(REPS):
        t0 = time.perf_counter()
        result = solver.solve(model)
        walls.append(time.perf_counter() - t0)
    sample = [int(result.sample.get_linear(i)) for i in range(n)]
    return {
        "variant": "stock",
        "n": n,
        "wall_s": walls,
        "energy": int(result.energy),
        "sample": sample,
        "mempool_peak_bytes": _mempool_peak(cupy),
    }


def analytic_bytes(n: int) -> dict:
    """Per-buffer device bytes for both variants at the bench parameters."""
    return {
        "j_matrix": n * n * 8,
        "h": n * 8,
        "randoms": NUM_READS * NUM_SWEEPS * n * 8,
        "samples_dense_int32": NUM_READS * n * 4,
        "samples_packed_uint32": NUM_READS * ((n + 31) // 32) * 4,
    }


PACKED_KERNEL = r"""
extern "C" __global__
void sa_spin_packed(
    const double*  __restrict__ h,
    const double*  __restrict__ J,
    unsigned int*  __restrict__ words,   /* packed spins: bit set -> +1 */
    double*        __restrict__ energies,
    const double*  __restrict__ randoms,
    int    n,
    int    n_words,
    int    num_sweeps,
    double beta_start,
    double beta_end
) {
    int rid = blockIdx.x;
    unsigned int* w = words + (long long)rid * n_words;
    const double* rand_ptr = randoms + (long long)rid * num_sweeps * n;

#define SPIN(i) ((double)(((w[(i) >> 5] >> ((i) & 31)) & 1u) ? 1 : -1))

    double energy = 0.0;
    for (int i = 0; i < n; i++) {
        double si = SPIN(i);
        energy += h[i] * si;
        for (int j = i + 1; j < n; j++) {
            energy += J[i * n + j] * si * SPIN(j);
        }
    }

    for (int sweep = 0; sweep < num_sweeps; sweep++) {
        double beta;
        if (num_sweeps <= 1) {
            beta = beta_start;
        } else {
            beta = beta_start
                + (beta_end - beta_start)
                    * ((double)sweep / (double)(num_sweeps - 1));
        }

        for (int i = 0; i < n; i++) {
            double local = h[i];
            for (int j = 0; j < n; j++) {
                if (j == i) continue;
                local += J[i * n + j] * SPIN(j);
            }
            double delta_E = -2.0 * SPIN(i) * local;

            double r = rand_ptr[(long long)sweep * n + i];
            if (delta_E <= 0.0 || r < exp(-delta_E * beta)) {
                w[i >> 5] ^= (1u << (i & 31));
                energy += delta_E;
            }
        }
    }
    energies[rid] = energy;
#undef SPIN
}
"""


def _pack_spins(cupy: Any, spins: Any) -> Any:
    """Pack a (num_reads, n) +/-1 int32 array into (num_reads, n_words) uint32
    words, bit set for +1. Padding bits beyond n are zero and never read."""
    bits = (spins > 0).astype(cupy.uint32)
    num_reads, n = spins.shape
    n_words = (n + 31) // 32
    padded = cupy.zeros((num_reads, n_words * 32), dtype=cupy.uint32)
    padded[:, :n] = bits
    shifts = cupy.arange(32, dtype=cupy.uint32)
    return (padded.reshape(num_reads, n_words, 32) << shifts).sum(axis=2, dtype=cupy.uint32)


def _unpack_row(row_words: np.ndarray, n: int) -> list[int]:
    """Unpack one uint32 word row back to a +/-1 spin list of length n."""
    out: list[int] = []
    for i in range(n):
        bit = (int(row_words[i >> 5]) >> (i & 31)) & 1
        out.append(1 if bit else -1)
    return out


def run_packed(h: np.ndarray, j_matrix: np.ndarray, n: int) -> dict:
    """Bit-packed variant with the identical RNG stream, init, and betas as
    the stock solver, so trajectories are bit-identical."""
    import cupy

    kernel = cupy.RawKernel(PACKED_KERNEL, "sa_spin_packed")
    n_words = (n + 31) // 32
    d_h = cupy.asarray(h)
    d_j = cupy.asarray(j_matrix)
    cupy.get_default_memory_pool().free_all_blocks()

    walls: list[float] = []
    best_energy = 0.0
    best_row: np.ndarray | None = None
    for _ in range(REPS):
        t0 = time.perf_counter()
        rng = cupy.random.default_rng(SEED)
        raw = rng.integers(0, 2, size=(NUM_READS, n), dtype=cupy.int32)
        spins = (raw * 2 - 1).astype(cupy.int32)
        words = cupy.ascontiguousarray(_pack_spins(cupy, spins))
        randoms = rng.random(size=(NUM_READS, NUM_SWEEPS, n), dtype=cupy.float64)
        energies = cupy.zeros(NUM_READS, dtype=cupy.float64)

        kernel(
            (NUM_READS,),
            (1,),
            (
                d_h,
                d_j,
                words,
                energies,
                randoms,
                np.int32(n),
                np.int32(n_words),
                np.int32(NUM_SWEEPS),
                np.float64(BETA_RANGE[0]),
                np.float64(BETA_RANGE[1]),
            ),
        )
        cupy.cuda.Device().synchronize()
        walls.append(time.perf_counter() - t0)

        energies_np = cupy.asnumpy(energies)
        best_idx = int(np.argmin(energies_np))
        best_energy = float(energies_np[best_idx])
        best_row = cupy.asnumpy(words[best_idx])

    assert best_row is not None
    return {
        "variant": "packed",
        "n": n,
        "wall_s": walls,
        "energy": best_energy,
        "sample": _unpack_row(best_row, n),
        "mempool_peak_bytes": _mempool_peak(cupy),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="QUI-704 spin-packing benchmark")
    parser.add_argument("--sizes", type=int, nargs="*", default=list(SIZES))
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "scratch" / "spin_packing" / "results.json")
    args = parser.parse_args(argv)

    rows: list[dict] = []
    for n in args.sizes:
        model, h, j_matrix = build_instance(n, SEED)
        stock = run_stock(model, n)
        packed = run_packed(h, j_matrix, n)
        if packed["sample"] != stock["sample"]:
            mismatch = next(i for i, (a, b) in enumerate(zip(packed["sample"], stock["sample"])) if a != b)
            raise SystemExit(
                f"TRAJECTORY DIVERGENCE at n={n}: packed prototype is not faithful (first mismatch at index {mismatch})"
            )
        analytic = analytic_bytes(n)
        rows.append({**stock, "analytic": analytic})
        rows.append({**packed, "analytic": analytic})
        med_s = sorted(stock["wall_s"])[1]
        med_p = sorted(packed["wall_s"])[1]
        print(
            f"n={n}: stock {med_s:.2f}s / packed {med_p:.2f}s "
            f"(x{med_p / med_s:.2f}), energy {stock['energy']}, samples identical"
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, indent=1))
    print(f"{len(rows)} rows -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
