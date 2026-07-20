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
CUDA GPU simulated annealing solver.

Custom CUDA kernels via CuPy RawKernel for solving XQMX quadratic
models on NVIDIA GPUs. Bypasses the D-Wave SDK entirely.

Requires the optional ``cuda`` extra::

    pip install xqsa[cuda]
"""

from __future__ import annotations

import functools
import time
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt

from xqvm_py.xqmx import XQMX, XQMXDomain

from .solver import Solver, SolverResult

if TYPE_CHECKING:
    import cupy as cp

_SUPPORTED_STRATEGIES = frozenset({"sa"})
_SUPPORTED_SCHEDULES = frozenset({"geometric", "linear"})

# One CUDA block per replica; this many threads cooperate on the local-field
# reduction (QUI-852). Must be a power of two (tree reduction) and match the
# kernels' `__shared__ double sdata[256]`.
_THREADS_PER_REPLICA = 256

# ---------------------------------------------------------------------------
# CUDA kernel sources
# ---------------------------------------------------------------------------

_SA_BINARY_KERNEL = r"""
extern "C" __global__
void sa_binary(
    const double* __restrict__ h,
    const double* __restrict__ J,
    int*          __restrict__ samples,
    double*       __restrict__ energies,
    const double* __restrict__ randoms,
    const double* __restrict__ betas,
    int    n,
    int    num_sweeps
) {
    /* One block per replica; blockDim.x threads cooperate on the O(n)
       local-field reduction. Spin updates stay strictly sequential on
       thread 0: same proposal order, same acceptance rule, same RNG
       consumption order as the single-thread kernel (QUI-852). Only the
       floating-point summation order of the local field changes. */
    __shared__ double sdata[256];
    int rid = blockIdx.x;
    int tid = threadIdx.x;
    int nthreads = blockDim.x;
    int* x = samples + rid * n;
    const double* rand_ptr = randoms + (long long)rid * num_sweeps * n;

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
                double r = rand_ptr[(long long)sweep * n + i];
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

_SA_SPIN_KERNEL = r"""
extern "C" __global__
void sa_spin(
    const double* __restrict__ h,
    const double* __restrict__ J,
    int*          __restrict__ samples,
    double*       __restrict__ energies,
    const double* __restrict__ randoms,
    const double* __restrict__ betas,
    int    n,
    int    num_sweeps
) {
    /* See sa_binary: cooperative local-field reduction, sequential updates. */
    __shared__ double sdata[256];
    int rid = blockIdx.x;
    int tid = threadIdx.x;
    int nthreads = blockDim.x;
    int* s = samples + rid * n;
    const double* rand_ptr = randoms + (long long)rid * num_sweeps * n;

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
                double r = rand_ptr[(long long)sweep * n + i];
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


class SolverCudaGPU(Solver):
    """Simulated annealing solver using custom CUDA kernels via CuPy.

    Runs parallel replica simulated annealing on an NVIDIA GPU. Each
    replica (controlled by ``num_reads``) executes independently in its
    own CUDA thread block. Within a block, ``_THREADS_PER_REPLICA``
    threads cooperate on the local-field reduction while spin updates
    remain sequential (QUI-852).

    ``num_sweeps_per_beta`` holds each inverse-temperature (beta) for that
    many consecutive sweeps, decoupling the number of temperature levels
    from the total sweep count: ``num_betas = num_sweeps //
    num_sweeps_per_beta`` (``num_sweeps`` must be divisible by it). The
    default of 1 keeps one beta per sweep, bit-identical to the previous
    behaviour for ``num_sweeps >= 2``. When ``num_betas == 1`` the whole run
    is a cold greedy quench at ``beta_range[-1]``, matching
    ``SolverDWaveCPU`` and ``SolverMetalGPU`` (QUI-685); the previous CUDA
    kernel used ``beta_start`` for this degenerate case.

    ``beta_schedule_type`` selects the schedule shape: ``"linear"`` (the
    default, preserving the historical CUDA ramp) or ``"geometric"``.
    Note: ``SolverMetalGPU`` defaults to ``"geometric"``.

    Examples:

    ```python
    from xqsa import SolverCudaGPU
    from xqvm_py.xqmx import XQMX

    model = XQMX.binary_model(4)
    model.set_linear(0, -1.0)
    model.set_quadratic(0, 1, 2.0)

    solver = SolverCudaGPU()
    result = solver.solve(model)
    print(result.energy, result.timing)
    ```

    Raises:
        ImportError: if ``cupy-cuda12x`` is not installed.
        RuntimeError: if no NVIDIA CUDA GPU is detected.
        ValueError: if ``strategy`` or ``beta_schedule_type`` is not supported.
    """

    def __init__(
        self,
        strategy: str = "sa",
        num_reads: int = 100,
        num_sweeps: int = 1000,
        num_sweeps_per_beta: int = 1,
        beta_range: tuple[float, float] | None = None,
        beta_schedule_type: str = "linear",
        seed: int | None = None,
    ) -> None:
        try:
            import cupy as _cupy
        except ImportError as exc:
            raise ImportError("cupy is not installed. Install xqsa with: pip install xqsa[cuda]") from exc

        if _cupy.cuda.runtime.getDeviceCount() == 0:
            raise RuntimeError("No NVIDIA CUDA GPU detected. SolverCudaGPU requires an NVIDIA GPU with CUDA support.")

        if strategy not in _SUPPORTED_STRATEGIES:
            raise ValueError(f"Unsupported strategy {strategy!r}. Supported: {sorted(_SUPPORTED_STRATEGIES)}")

        if beta_schedule_type not in _SUPPORTED_SCHEDULES:
            raise ValueError(
                f"Unsupported beta_schedule_type {beta_schedule_type!r}. Supported: {sorted(_SUPPORTED_SCHEDULES)}"
            )

        self._cp = _cupy
        self.strategy = strategy
        self.num_reads = num_reads
        self.num_sweeps = num_sweeps
        self.num_sweeps_per_beta = num_sweeps_per_beta
        self.beta_range = beta_range
        self.beta_schedule_type = beta_schedule_type
        self.seed = seed

    @functools.cached_property
    def _binary_kernel(self):
        """Compile the binary-domain SA kernel on first use."""
        return self._cp.RawKernel(_SA_BINARY_KERNEL, "sa_binary")

    @functools.cached_property
    def _spin_kernel(self):
        """Compile the spin-domain SA kernel on first use."""
        return self._cp.RawKernel(_SA_SPIN_KERNEL, "sa_spin")

    def solve(self, model: XQMX, **kwargs: Any) -> SolverResult:
        """Solve using parallel-replica simulated annealing on GPU.

        Raises:
            ValueError: if the model is not in BINARY or SPIN domain,
                or if parameter values are invalid.
        """
        self._validate_model(model)

        strategy = kwargs.get("strategy", self.strategy)
        num_reads = kwargs.get("num_reads", self.num_reads)
        num_sweeps = kwargs.get("num_sweeps", self.num_sweeps)
        num_sweeps_per_beta = kwargs.get("num_sweeps_per_beta", self.num_sweeps_per_beta)
        beta_range = kwargs.get("beta_range", self.beta_range)
        beta_schedule_type = kwargs.get("beta_schedule_type", self.beta_schedule_type)
        seed = kwargs.get("seed", self.seed)

        if strategy not in _SUPPORTED_STRATEGIES:
            raise ValueError(f"Unsupported strategy {strategy!r}. Supported: {sorted(_SUPPORTED_STRATEGIES)}")
        if num_reads < 1:
            raise ValueError(f"num_reads must be >= 1, got {num_reads}")
        if num_sweeps < 1:
            raise ValueError(f"num_sweeps must be >= 1, got {num_sweeps}")
        if beta_schedule_type not in _SUPPORTED_SCHEDULES:
            raise ValueError(
                f"Unsupported beta_schedule_type {beta_schedule_type!r}. Supported: {sorted(_SUPPORTED_SCHEDULES)}"
            )
        if not isinstance(num_sweeps_per_beta, int):
            raise ValueError(f"num_sweeps_per_beta must be an int, got {type(num_sweeps_per_beta).__name__}")
        if num_sweeps_per_beta < 1:
            raise ValueError(f"num_sweeps_per_beta must be >= 1, got {num_sweeps_per_beta}")
        num_betas, rem = divmod(num_sweeps, num_sweeps_per_beta)
        if rem != 0:
            raise ValueError(
                f"num_sweeps ({num_sweeps}) must be divisible by num_sweeps_per_beta ({num_sweeps_per_beta})"
            )

        h, j_matrix = self._to_dense_arrays(model)

        t0 = time.perf_counter()
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
        elapsed = time.perf_counter() - t0

        sample = self._sample_to_xqmx(model, best_sample_dict)

        return SolverResult(
            sample=sample,
            energy=self._recompute_energy(model, sample),
            timing=elapsed,
            metadata={
                "seed": seed,
                "reads": num_reads,
                "params": {
                    "strategy": strategy,
                    "num_sweeps": num_sweeps,
                    "num_sweeps_per_beta": num_sweeps_per_beta,
                    "num_betas": num_betas,
                    "beta_range": beta_range,
                    "beta_schedule_type": beta_schedule_type,
                    "raw_energy": raw_energy,
                },
            },
        )

    def _to_dense_arrays(self, model: XQMX) -> tuple[cp.ndarray, cp.ndarray]:
        """Convert sparse XQMX model to dense GPU arrays.

        Returns:
            (h, J) where h is shape (n,) and J is shape (n, n),
            both float64 on GPU.
        """
        n = model.size
        h_np = np.zeros(n, dtype=np.float64)
        j_np = np.zeros((n, n), dtype=np.float64)

        for idx, coeff in model.linear.items():
            h_np[idx] = coeff

        for (i, j), coeff in model.quadratic.items():
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
        ``const double*`` in the kernels), unlike ``SolverMetalGPU``, whose
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
            # greedy quench matching SolverDWaveCPU and SolverMetalGPU
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
        model: XQMX,
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
        is_binary = model.domain == XQMXDomain.BINARY
        if is_binary:
            samples = rng.integers(0, 2, size=(num_reads, n), dtype=cupy.int32)
        else:
            raw = rng.integers(0, 2, size=(num_reads, n), dtype=cupy.int32)
            samples = raw * 2 - 1  # map {0,1} -> {-1,+1}
            samples = samples.astype(cupy.int32)

        # Pre-generate all random acceptance thresholds.
        # Memory: num_reads * num_sweeps * n * 8 bytes (float64).
        random_bytes = num_reads * num_sweeps * n * 8
        gpu_free = cupy.cuda.Device().mem_info[0]
        if random_bytes > gpu_free * 0.8:
            raise ValueError(
                f"Random buffer requires {random_bytes / 1e9:.1f} GB "
                f"GPU memory ({gpu_free / 1e9:.1f} GB free). "
                f"Reduce num_reads or num_sweeps."
            )
        randoms = rng.random(size=(num_reads, num_sweeps, n), dtype=cupy.float64)

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
                randoms,
                beta_schedule,
                np.int32(n),
                np.int32(num_sweeps),
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
