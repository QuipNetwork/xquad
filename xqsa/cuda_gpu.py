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
In-process CUDA GPU simulated annealing implementation.

Custom CUDA kernels via CuPy RawKernel for solving XQMX quadratic
models on NVIDIA GPUs. Bypasses the D-Wave SDK entirely. Run through
:class:`xqsa.SolverGPU` with ``engine="cuda"``.

Requires the optional ``cuda`` extra::

    pip install xqsa[cuda]
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt

from xqffi.vm import Domain, XqmxModel

from .solver import (
    Implementation,
    RunOutput,
    _check_algorithm,
    _check_anneal_params,
    _check_beta_schedule,
    sample_to_xqmx,
)

if TYPE_CHECKING:
    import cupy as cp

_SUPPORTED_ALGORITHMS = frozenset({"sa"})


# One CUDA block per replica; this many threads cooperate on the local-field
# reduction (QUI-852). Must be a power of two (tree reduction) and match the
# kernels' `__shared__ double sdata[256]`.
_THREADS_PER_REPLICA = 256

# ---------------------------------------------------------------------------
# CUDA kernel sources
# ---------------------------------------------------------------------------

# Device RNG (QUI-705): mirrors InProcessMetal's on-device scheme
# (xqsa/metal_gpu.py _RNG_PRELUDE) with 64-bit state so acceptance draws
# stay float64 (53-bit uniform). Metal's float32 is an MSL platform limit,
# not a precision decision. Python mirrors live in tests/test_xqsa.py;
# keep constants in sync bit-for-bit.
_RNG_PRELUDE = r"""
typedef unsigned long long u64;

__device__ __forceinline__ u64 splitmix64(u64 x) {
    x += 0x9E3779B97F4A7C15ULL;
    x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9ULL;
    x = (x ^ (x >> 27)) * 0x94D049BB133111EBULL;
    return x ^ (x >> 31);
}

/* Per-replica stream seed: Metal's seed_for_replica shape (base ^ odd
   constant * (rid+1)) widened to 64 bits, finalized through splitmix64
   for stream decorrelation. xorshift64 has a zero fixed point, so never
   return 0. */
__device__ __forceinline__ u64 seed_for_replica(u64 base_seed, int rid) {
    u64 s = splitmix64(base_seed ^ (0x9E3779B97F4A7C15ULL * (u64)(rid + 1)));
    return (s == 0ULL) ? 1ULL : s;
}

__device__ __forceinline__ u64 xorshift64(u64 &state) {
    u64 x = state;
    x ^= x << 13;
    x ^= x >> 7;
    x ^= x << 17;
    state = x;
    return x;
}

/* Uniform double in [0, 1): top 53 bits / 2^53. */
__device__ __forceinline__ double rand_unit(u64 &state) {
    return (double)(xorshift64(state) >> 11) * (1.0 / 9007199254740992.0);
}
"""

_SA_BINARY_KERNEL = (
    _RNG_PRELUDE
    + r"""
extern "C" __global__
void sa_binary(
    const double* __restrict__ h,
    const double* __restrict__ J,
    int*          __restrict__ samples,
    double*       __restrict__ energies,
    const double* __restrict__ betas,
    int    n,
    int    num_sweeps,
    unsigned long long base_seed
) {
    /* One block per replica; blockDim.x threads cooperate on the O(n)
       local-field reduction. Spin updates stay strictly sequential on
       thread 0: same proposal order, same acceptance rule (QUI-852).
       Acceptance randomness is drawn on-device (QUI-705): thread 0 owns
       the per-replica RNG stream. Only the floating-point summation
       order of the local field changes. */
    __shared__ double sdata[256];
    int rid = blockIdx.x;
    int tid = threadIdx.x;
    int nthreads = blockDim.x;
    int* x = samples + rid * n;
    u64 rng = seed_for_replica(base_seed, rid);

    /* initial energy: one-time O(n^2), thread 0 only */
    double energy = 0.0;
    if (tid == 0) {
        for (int i = 0; i < n; i++) {
            if (x[i] == 0) continue;
            energy += h[i];
            for (int j = i + 1; j < n; j++) {
                if (x[j] == 0) continue;
                energy += J[i * n + j];
            }
        }
    }
    __syncthreads();

    for (int sweep = 0; sweep < num_sweeps; sweep++) {
        double beta = betas[sweep];

        for (int i = 0; i < n; i++) {
            /* strided partial sums of the local field for spin i */
            double partial = 0.0;
            for (int j = tid; j < n; j += nthreads) {
                if (j == i) continue;
                partial += J[i * n + j] * (double)x[j];
            }
            sdata[tid] = partial;
            __syncthreads();

            /* power-of-two tree reduction into sdata[0] */
            for (int s = nthreads / 2; s > 0; s >>= 1) {
                if (tid < s) sdata[tid] += sdata[tid + s];
                __syncthreads();
            }

            if (tid == 0) {
                double local = h[i] + sdata[0];
                double delta_E = local * (double)(1 - 2 * x[i]);
                double r = rand_unit(rng);
                if (delta_E <= 0.0 || r < exp(-delta_E * beta)) {
                    x[i] = 1 - x[i];
                    energy += delta_E;
                }
            }
            __syncthreads();
        }
    }
    if (tid == 0) energies[rid] = energy;
}
"""
)

_SA_SPIN_KERNEL = (
    _RNG_PRELUDE
    + r"""
extern "C" __global__
void sa_spin(
    const double* __restrict__ h,
    const double* __restrict__ J,
    int*          __restrict__ samples,
    double*       __restrict__ energies,
    const double* __restrict__ betas,
    int    n,
    int    num_sweeps,
    unsigned long long base_seed
) {
    /* See sa_binary: cooperative local-field reduction, sequential updates. */
    __shared__ double sdata[256];
    int rid = blockIdx.x;
    int tid = threadIdx.x;
    int nthreads = blockDim.x;
    int* s = samples + rid * n;
    u64 rng = seed_for_replica(base_seed, rid);

    double energy = 0.0;
    if (tid == 0) {
        for (int i = 0; i < n; i++) {
            energy += h[i] * (double)s[i];
            for (int j = i + 1; j < n; j++) {
                energy += J[i * n + j] * (double)s[i] * (double)s[j];
            }
        }
    }
    __syncthreads();

    for (int sweep = 0; sweep < num_sweeps; sweep++) {
        double beta = betas[sweep];

        for (int i = 0; i < n; i++) {
            double partial = 0.0;
            for (int j = tid; j < n; j += nthreads) {
                if (j == i) continue;
                partial += J[i * n + j] * (double)s[j];
            }
            sdata[tid] = partial;
            __syncthreads();

            for (int st = nthreads / 2; st > 0; st >>= 1) {
                if (tid < st) sdata[tid] += sdata[tid + st];
                __syncthreads();
            }

            if (tid == 0) {
                double local = h[i] + sdata[0];
                double delta_E = -2.0 * (double)s[i] * local;
                double r = rand_unit(rng);
                if (delta_E <= 0.0 || r < exp(-delta_E * beta)) {
                    s[i] = -s[i];
                    energy += delta_E;
                }
            }
            __syncthreads();
        }
    }
    if (tid == 0) energies[rid] = energy;
}
"""
)


class InProcessCuda(Implementation):
    """Simulated annealing using custom CUDA kernels via CuPy.

    Runs parallel replica simulated annealing on an NVIDIA GPU. Each
    replica (controlled by ``num_reads``) executes independently in its
    own CUDA thread block. Within a block, ``_THREADS_PER_REPLICA``
    threads cooperate on the local-field reduction while spin updates
    remain sequential (QUI-852).

    Acceptance randomness is generated on-device per replica (xorshift64
    seeded via splitmix64 from a 64-bit base seed), so no
    ``(num_reads, num_sweeps, n)`` buffer is allocated (QUI-705).

    ``num_sweeps_per_beta`` holds each inverse-temperature (beta) for that
    many consecutive sweeps, decoupling the number of temperature levels
    from the total sweep count: ``num_betas = num_sweeps //
    num_sweeps_per_beta`` (``num_sweeps`` must be divisible by it). The
    default of 1 keeps one beta per sweep, bit-identical to the previous
    behaviour for ``num_sweeps >= 2``. When ``num_betas == 1`` the whole run
    is a cold greedy quench at ``beta_range[-1]``, matching
    ``InProcessCPU`` and ``InProcessMetal`` (QUI-685); the previous CUDA
    kernel used ``beta_start`` for this degenerate case.

    ``beta_schedule_type`` selects the schedule shape: ``"linear"`` (the
    default, preserving the historical CUDA ramp) or ``"geometric"``.
    Note: ``InProcessMetal`` defaults to ``"geometric"``.

    Raises:
        ImportError: if ``cupy-cuda12x`` is not installed.
        RuntimeError: if no NVIDIA CUDA GPU is detected.
        ValueError: if ``algorithm`` or ``beta_schedule_type`` is not supported.
    """

    def __init__(
        self,
        algorithm: str = "sa",
        num_reads: int = 100,
        num_sweeps: int = 1000,
        num_sweeps_per_beta: int = 1,
        beta_range: tuple[float, float] | None = None,
        beta_schedule_type: str = "linear",
        seed: int | None = None,
    ) -> None:
        _check_algorithm(algorithm, _SUPPORTED_ALGORITHMS, "the CUDA engine")
        _check_beta_schedule(beta_schedule_type)

        try:
            import cupy as _cupy
        except ImportError as exc:
            raise ImportError("cupy is not installed. Install xqsa with: pip install xqsa[cuda]") from exc

        if _cupy.cuda.runtime.getDeviceCount() == 0:
            raise RuntimeError("No NVIDIA CUDA GPU detected. The CUDA engine requires an NVIDIA GPU with CUDA support.")

        self._cp = _cupy
        self.algorithm = algorithm
        super().__init__(
            num_reads=num_reads,
            num_sweeps=num_sweeps,
            num_sweeps_per_beta=num_sweeps_per_beta,
            beta_range=beta_range,
            beta_schedule_type=beta_schedule_type,
            seed=seed,
        )

    @functools.cached_property
    def _binary_kernel(self):
        """Compile the binary-domain SA kernel on first use."""
        return self._cp.RawKernel(_SA_BINARY_KERNEL, "sa_binary")

    @functools.cached_property
    def _spin_kernel(self):
        """Compile the spin-domain SA kernel on first use."""
        return self._cp.RawKernel(_SA_SPIN_KERNEL, "sa_spin")

    def run(
        self,
        model: XqmxModel,
        *,
        num_reads: int,
        num_sweeps: int,
        num_sweeps_per_beta: int,
        beta_range: tuple[float, float] | None,
        beta_schedule_type: str,
        seed: int | None,
    ) -> RunOutput:
        """Sample using parallel-replica simulated annealing on GPU.

        Raises:
            ValueError: if parameter values are invalid.
        """
        num_betas = _check_anneal_params(num_reads, num_sweeps, num_sweeps_per_beta)
        _check_beta_schedule(beta_schedule_type)

        h, j_matrix = self._to_dense_arrays(model)

        best_sample_dict, raw_energy = self._run_sa(
            model=model,
            h=h,
            j_matrix=j_matrix,
            num_reads=num_reads,
            num_sweeps=num_sweeps,
            num_sweeps_per_beta=num_sweeps_per_beta,
            num_betas=num_betas,
            beta_schedule_type=beta_schedule_type,
            beta_range=beta_range,
            seed=seed,
        )

        return RunOutput(
            sample=sample_to_xqmx(model, best_sample_dict),
            seed=seed,
            reads=num_reads,
            params={
                "num_sweeps": num_sweeps,
                "num_sweeps_per_beta": num_sweeps_per_beta,
                "num_betas": num_betas,
                "beta_range": beta_range,
                "beta_schedule_type": beta_schedule_type,
                "raw_energy": raw_energy,
            },
        )

    def _to_dense_arrays(self, model: XqmxModel) -> tuple[cp.ndarray, cp.ndarray]:
        """Convert sparse XQMX model to dense GPU arrays.

        Returns:
            (h, J) where h is shape (n,) and J is shape (n, n),
            both float64 on GPU.
        """
        n = model.size
        h_np = np.zeros(n, dtype=np.float64)
        j_np = np.zeros((n, n), dtype=np.float64)

        for idx, coeff in model.linear_items():
            h_np[idx] = coeff

        for (i, j), coeff in model.quadratic_items():
            j_np[i, j] = coeff
            j_np[j, i] = coeff

        return self._cp.asarray(h_np), self._cp.asarray(j_np)

    def _auto_beta_range(self, h: cp.ndarray, j_matrix: cp.ndarray) -> tuple[float, float]:
        """Compute a reasonable beta range from model coefficients.

        Uses the maximum absolute coefficient magnitude to set the
        temperature window. beta_start (high temperature) allows free
        exploration; beta_end (low temperature) freezes into a basin.
        """
        max_h = float(self._cp.max(self._cp.abs(h)))
        max_j = float(self._cp.max(self._cp.abs(j_matrix)))
        max_coeff = max(max_h, max_j, 1e-8)
        beta_start = 1.0 / (max_coeff * 10.0)
        beta_end = 10.0 / max_coeff
        return (beta_start, beta_end)

    def _compute_beta_schedule(
        self, num_betas: int, beta_range: tuple[float, float], schedule_type: str
    ) -> npt.NDArray[np.float64]:
        """Build the per-temperature-level inverse-temperature schedule.

        Returns ``num_betas`` beta points; ``_run_sa`` expands this to the
        per-sweep kernel buffer via ``np.repeat(..., num_sweeps_per_beta)``.

        The buffer is float64 because the CUDA pipeline is float64
        end-to-end (``h``, ``J``, ``randoms``, and ``betas`` are all
        ``const double*`` in the kernels), unlike ``InProcessMetal``, whose
        pipeline -- including its float32 schedule -- trades precision for
        Metal throughput. The bit-identical-default guarantee below also
        requires float64.

        The ``"linear"`` branch reproduces the retired in-kernel ramp
        ``beta_start + (beta_end - beta_start) * (sweep / (num_sweeps - 1))``
        term-for-term in float64, so for ``num_sweeps >= 2`` the default
        configuration (``num_sweeps_per_beta=1``) is bit-identical to the
        pre-buffer kernel output. Do not replace it with ``np.linspace``,
        which computes linear spacing differently in the last ulp.

        The degenerate ``num_betas == 1`` case deliberately departs from the
        retired kernel, whose ``num_sweeps <= 1`` branch used ``beta_start``;
        see the branch comment below.
        """
        beta_start, beta_end = beta_range
        if num_betas == 1:
            # A single temperature level anneals at beta_end (coldest), a
            # greedy quench matching InProcessCPU and InProcessMetal
            # (QUI-685). This intentionally differs from the retired kernel's
            # num_sweeps <= 1 branch, which used beta_start: cross-backend
            # consistency wins over bit-identity on a level that performs no
            # annealing at all.
            return np.array([beta_end], dtype=np.float64)
        if schedule_type == "geometric":
            start = max(beta_start, 1e-12)
            return np.geomspace(start, beta_end, num_betas).astype(np.float64)
        steps = np.arange(num_betas, dtype=np.float64) / np.float64(num_betas - 1)
        return np.float64(beta_start) + (np.float64(beta_end) - np.float64(beta_start)) * steps

    def _run_sa(
        self,
        model: XqmxModel,
        *,
        h: cp.ndarray,
        j_matrix: cp.ndarray,
        num_reads: int,
        num_sweeps: int,
        num_sweeps_per_beta: int,
        num_betas: int,
        beta_schedule_type: str,
        beta_range: tuple[float, float] | None,
        seed: int | None,
    ) -> tuple[dict[int, int], float]:
        """Run simulated annealing on GPU.

        Returns:
            (best_sample_dict, raw_energy) -- the best sample found
            across all replicas, and its float energy.
        """
        cupy = self._cp
        n = model.size

        if beta_range is None:
            beta_range = self._auto_beta_range(h, j_matrix)

        # Host-built per-sweep schedule: num_betas levels, each held for
        # num_sweeps_per_beta consecutive sweeps (both validated in solve).
        # Replaces the retired in-kernel linear ramp.
        betas_host = self._compute_beta_schedule(num_betas, beta_range, beta_schedule_type)
        beta_schedule = cupy.asarray(np.repeat(betas_host, num_sweeps_per_beta))

        rng = cupy.random.default_rng(seed)

        # Initialise replica samples
        is_binary = model.domain == Domain.BINARY
        if is_binary:
            samples = rng.integers(0, 2, size=(num_reads, n), dtype=cupy.int32)
        else:
            raw = rng.integers(0, 2, size=(num_reads, n), dtype=cupy.int32)
            samples = raw * 2 - 1  # map {0,1} -> {-1,+1}
            samples = samples.astype(cupy.int32)

        # Acceptance randomness is generated on-device per replica
        # (QUI-705); only a 64-bit base seed crosses the host boundary.
        # Derived from an independent numpy stream so the cupy `rng` used
        # for replica init keeps its historical consumption order.
        base_seed = np.uint64(np.random.default_rng(seed).integers(0, 2**64, dtype=np.uint64))

        energies = cupy.zeros(num_reads, dtype=cupy.float64)

        kernel = self._binary_kernel if is_binary else self._spin_kernel
        kernel(
            (num_reads,),  # grid: one block per replica
            (_THREADS_PER_REPLICA,),  # block: cooperative local-field reduction
            (
                h,
                j_matrix,
                samples,
                energies,
                beta_schedule,
                np.int32(n),
                np.int32(num_sweeps),
                base_seed,
            ),
        )
        cupy.cuda.Device().synchronize()

        # Find best replica
        energies_np = cupy.asnumpy(energies)
        best_idx = int(np.argmin(energies_np))
        raw_energy = float(energies_np[best_idx])
        best_row = cupy.asnumpy(samples[best_idx])

        best_sample_dict: dict[int, int] = {i: int(best_row[i]) for i in range(n)}
        return best_sample_dict, raw_energy
