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
Metal GPU solver for XQMX quadratic models.

Custom Metal compute shaders for simulated annealing (``strategy="sa"``)
and block Gibbs sampling (``strategy="gibbs"``) on Apple Silicon GPUs.
macOS-only; bypasses the D-Wave SDK entirely.

The kernels are adapted from the quip-protocol ``MetalMiner``
(``GPU/metal_kernels.metal``, ``GPU/metal_gibbs.metal``), simplified for
XQMX models: float32 coefficients, dense topology, single-model dispatch.

This solver intentionally mirrors :class:`xqsa.cuda_gpu.SolverCudaGPU`. Two
deliberate divergences from the protocol simplify the port and match the
CUDA solver's representation:

* Spins are stored as one ``int`` per variable (not bit-packed). XQMX models
  are small; the dense ``int`` layout matches the CUDA solver exactly and
  keeps the kernels and host-side unpacking trivial.
* Acceptance randomness uses an on-device xorshift32 RNG seeded per replica,
  so no ``(num_reads, num_sweeps, n)`` random buffer is allocated (avoiding
  the memory pressure that the CUDA solver guards against).

Requires the optional ``metal`` extra::

    pip install xqsa[metal]
"""

from __future__ import annotations

import functools
import time
from typing import TYPE_CHECKING, Any

import numpy as np

from xqvm_py.xqmx import XQMX, XQMXDomain

from .solver import Solver, SolverResult

if TYPE_CHECKING:
    import numpy.typing as npt

_SUPPORTED_STRATEGIES = frozenset({"sa", "gibbs"})
_SUPPORTED_SCHEDULES = frozenset({"geometric", "linear"})

# ---------------------------------------------------------------------------
# Metal kernel sources (Metal Shading Language)
#
# Buffer ABI (shared by the host dispatch and the mocked test harness):
#   sa_metal:    0:h 1:J 2:beta 3:samples 4:energies
#                5:n 6:num_sweeps 7:base_seed 8:is_spin
#   gibbs_metal: 0:h 1:J 2:beta 3:samples 4:energies
#                5:col_starts 6:col_counts 7:col_nodes
#                8:n 9:num_sweeps 10:base_seed 11:num_colors 12:is_spin
# Each threadgroup (one thread) owns one replica via threadgroup_position_in_grid.
# ---------------------------------------------------------------------------

_RNG_PRELUDE = r"""
#include <metal_stdlib>
using namespace metal;

inline uint xorshift32(thread uint& state) {
    uint x = state;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    state = x;
    return x;
}

inline float rand_unit(thread uint& state) {
    // 24-bit mantissa worth of uniform [0, 1).
    return (float)(xorshift32(state) & 0x00FFFFFFu) / (float)0x01000000u;
}

inline uint seed_for_replica(uint base_seed, int rid) {
    uint s = base_seed ^ (0x9E3779B9u * (uint)(rid + 1));
    return (s == 0u) ? 1u : s;
}
"""

_SA_KERNEL = (
    _RNG_PRELUDE
    + r"""
kernel void sa_metal(
    device const float* h          [[buffer(0)]],
    device const float* J          [[buffer(1)]],
    device const float* beta_sched [[buffer(2)]],
    device int*         samples    [[buffer(3)]],
    device float*       energies   [[buffer(4)]],
    constant int&       n          [[buffer(5)]],
    constant int&       num_sweeps [[buffer(6)]],
    constant uint&      base_seed  [[buffer(7)]],
    constant int&       is_spin    [[buffer(8)]],
    uint3 tgid [[threadgroup_position_in_grid]]
) {
    int rid = (int)tgid.x;
    device int* x = samples + rid * n;
    uint rng = seed_for_replica(base_seed, rid);

    /* initial energy */
    float energy = 0.0f;
    for (int i = 0; i < n; i++) {
        float xi = (float)x[i];
        energy += h[i] * xi;
        for (int j = i + 1; j < n; j++) {
            energy += J[i * n + j] * xi * (float)x[j];
        }
    }

    for (int sweep = 0; sweep < num_sweeps; sweep++) {
        float beta = beta_sched[sweep];
        for (int i = 0; i < n; i++) {
            float local = h[i];
            for (int j = 0; j < n; j++) {
                if (j == i) continue;
                local += J[i * n + j] * (float)x[j];
            }
            float delta_E = (is_spin != 0)
                ? (-2.0f * (float)x[i] * local)
                : (local * (float)(1 - 2 * x[i]));

            float r = rand_unit(rng);
            if (delta_E <= 0.0f || r < exp(-delta_E * beta)) {
                x[i] = (is_spin != 0) ? -x[i] : (1 - x[i]);
                energy += delta_E;
            }
        }
    }
    energies[rid] = energy;
}
"""
)

_GIBBS_KERNEL = (
    _RNG_PRELUDE
    + r"""
kernel void gibbs_metal(
    device const float* h           [[buffer(0)]],
    device const float* J           [[buffer(1)]],
    device const float* beta_sched  [[buffer(2)]],
    device int*         samples     [[buffer(3)]],
    device float*       energies    [[buffer(4)]],
    device const int*   col_starts  [[buffer(5)]],
    device const int*   col_counts  [[buffer(6)]],
    device const int*   col_nodes   [[buffer(7)]],
    constant int&       n           [[buffer(8)]],
    constant int&       num_sweeps  [[buffer(9)]],
    constant uint&      base_seed   [[buffer(10)]],
    constant int&       num_colors  [[buffer(11)]],
    constant int&       is_spin     [[buffer(12)]],
    uint3 tgid [[threadgroup_position_in_grid]]
) {
    int rid = (int)tgid.x;
    device int* x = samples + rid * n;
    uint rng = seed_for_replica(base_seed, rid);

    for (int sweep = 0; sweep < num_sweeps; sweep++) {
        float beta = beta_sched[sweep];
        for (int c = 0; c < num_colors; c++) {
            int start = col_starts[c];
            int count = col_counts[c];
            for (int k = 0; k < count; k++) {
                int i = col_nodes[start + k];
                float h_eff = h[i];
                for (int j = 0; j < n; j++) {
                    if (j == i) continue;
                    h_eff += J[i * n + j] * (float)x[j];
                }
                float r = rand_unit(rng);
                if (is_spin != 0) {
                    float p_plus = 1.0f / (1.0f + exp(2.0f * beta * h_eff));
                    x[i] = (r < p_plus) ? 1 : -1;
                } else {
                    float p_one = 1.0f / (1.0f + exp(beta * h_eff));
                    x[i] = (r < p_one) ? 1 : 0;
                }
            }
        }
    }

    /* final energy */
    float energy = 0.0f;
    for (int i = 0; i < n; i++) {
        float xi = (float)x[i];
        energy += h[i] * xi;
        for (int j = i + 1; j < n; j++) {
            energy += J[i * n + j] * xi * (float)x[j];
        }
    }
    energies[rid] = energy;
}
"""
)


def _error_text(error: Any) -> str:
    """Extract a readable message from a Metal ``NSError`` (or ``None``).

    ``NSError.__str__`` yields only ``Error Domain=... Code=...``; the actual
    shader-compiler diagnostic lives in ``localizedDescription()``.
    """
    if error is None:
        return "unknown error"
    described = getattr(error, "localizedDescription", None)
    return str(described()) if callable(described) else str(error)


class SolverMetalGPU(Solver):
    """Simulated annealing / Gibbs solver using custom Metal shaders.

    Runs parallel-replica sampling on an Apple Silicon GPU. Each replica
    (controlled by ``num_reads``) executes independently in its own
    threadgroup. ``strategy`` selects the kernel:

    * ``"sa"`` -- simulated annealing with Metropolis acceptance.
    * ``"gibbs"`` -- block Gibbs sampling over a greedy graph colouring.

    Examples:

    ```python
    from xqsa import SolverMetalGPU
    from xqvm_py.xqmx import XQMX

    model = XQMX.binary_model(4)
    model.set_linear(0, -1.0)
    model.set_quadratic(0, 1, 2.0)

    solver = SolverMetalGPU(strategy="sa")
    result = solver.solve(model)
    print(result.energy, result.timing)
    ```

    Raises:
        ImportError: if ``pyobjc-framework-Metal`` is not installed.
        RuntimeError: if no Metal GPU is detected.
        ValueError: if ``strategy`` or ``beta_schedule_type`` is unsupported.
    """

    def __init__(
        self,
        strategy: str = "sa",
        num_reads: int = 100,
        num_sweeps: int = 1000,
        beta_range: tuple[float, float] | None = None,
        beta_schedule_type: str = "geometric",
        seed: int | None = None,
    ) -> None:
        try:
            import Metal as _metal
        except ImportError as exc:
            raise ImportError("Metal is not installed. Install xqsa with: pip install xqsa[metal]") from exc

        device = _metal.MTLCreateSystemDefaultDevice()
        if device is None:
            raise RuntimeError("No Metal GPU detected. SolverMetalGPU requires a Mac with an Apple Metal GPU.")

        if strategy not in _SUPPORTED_STRATEGIES:
            raise ValueError(f"Unsupported strategy {strategy!r}. Supported: {sorted(_SUPPORTED_STRATEGIES)}")
        if beta_schedule_type not in _SUPPORTED_SCHEDULES:
            raise ValueError(
                f"Unsupported beta_schedule_type {beta_schedule_type!r}. Supported: {sorted(_SUPPORTED_SCHEDULES)}"
            )

        self._metal = _metal
        self._device = device
        self.strategy = strategy
        self.num_reads = num_reads
        self.num_sweeps = num_sweeps
        self.beta_range = beta_range
        self.beta_schedule_type = beta_schedule_type
        self.seed = seed

    # -- lazy kernel compilation ------------------------------------------

    def _compile(self, source: str, function_name: str):
        """Compile a Metal kernel source into a compute pipeline state."""
        library, error = self._device.newLibraryWithSource_options_error_(source, None, None)
        if library is None:
            raise RuntimeError(f"Metal shader compilation failed for {function_name!r}: {_error_text(error)}")
        function = library.newFunctionWithName_(function_name)
        if function is None:
            raise RuntimeError(f"Metal library has no kernel named {function_name!r}.")
        pipeline, error = self._device.newComputePipelineStateWithFunction_error_(function, None)
        if pipeline is None:
            raise RuntimeError(f"Metal pipeline creation failed for {function_name!r}: {_error_text(error)}")
        return pipeline

    @functools.cached_property
    def _sa_pipeline(self):
        """Compile the SA kernel on first use."""
        return self._compile(_SA_KERNEL, "sa_metal")

    @functools.cached_property
    def _gibbs_pipeline(self):
        """Compile the Gibbs kernel on first use."""
        return self._compile(_GIBBS_KERNEL, "gibbs_metal")

    # -- public API -------------------------------------------------------

    def solve(self, model: XQMX, **kwargs: Any) -> SolverResult:
        """Solve using parallel-replica SA or Gibbs sampling on the GPU.

        Raises:
            ValueError: if the model is not in BINARY or SPIN domain, or if
                parameter values are invalid.
        """
        self._validate_model(model)

        strategy = kwargs.get("strategy", self.strategy)
        num_reads = kwargs.get("num_reads", self.num_reads)
        num_sweeps = kwargs.get("num_sweeps", self.num_sweeps)
        beta_range = kwargs.get("beta_range", self.beta_range)
        beta_schedule_type = kwargs.get("beta_schedule_type", self.beta_schedule_type)
        seed = kwargs.get("seed", self.seed)

        if strategy not in _SUPPORTED_STRATEGIES:
            raise ValueError(f"Unsupported strategy {strategy!r}. Supported: {sorted(_SUPPORTED_STRATEGIES)}")
        if beta_schedule_type not in _SUPPORTED_SCHEDULES:
            raise ValueError(
                f"Unsupported beta_schedule_type {beta_schedule_type!r}. Supported: {sorted(_SUPPORTED_SCHEDULES)}"
            )
        if num_reads < 1:
            raise ValueError("num_reads must be >= 1")
        if num_sweeps < 1:
            raise ValueError("num_sweeps must be >= 1")

        h, j_matrix = self._to_dense_arrays(model)
        if beta_range is None:
            beta_range = self._auto_beta_range(h, j_matrix)
        beta_schedule = self._compute_beta_schedule(num_sweeps, beta_range, beta_schedule_type)

        base_seed = self._resolve_base_seed(seed)
        is_spin = model.domain == XQMXDomain.SPIN
        rng = np.random.default_rng(seed)
        samples = self._init_samples(rng, num_reads, model.size, is_spin)

        t0 = time.perf_counter()
        if strategy == "sa":
            best_sample_dict, raw_energy = self._run_sa(
                model, h, j_matrix, beta_schedule, samples, num_sweeps, base_seed, is_spin
            )
        else:
            best_sample_dict, raw_energy = self._run_gibbs(
                model, h, j_matrix, beta_schedule, samples, num_sweeps, base_seed, is_spin
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
                    "beta_range": beta_range,
                    "beta_schedule_type": beta_schedule_type,
                    "raw_energy": raw_energy,
                },
            },
        )

    # -- model conversion / schedule --------------------------------------

    def _to_dense_arrays(self, model: XQMX) -> tuple[npt.NDArray[np.float32], npt.NDArray[np.float32]]:
        """Convert a sparse XQMX model to dense float32 arrays.

        Returns:
            (h, J) where h is shape (n,) and J is a symmetric (n, n) matrix,
            both float32. XQMX coefficients are float64; downcasting to
            float32 trades a little precision for Metal throughput.
        """
        n = model.size
        h_np = np.zeros(n, dtype=np.float32)
        j_np = np.zeros((n, n), dtype=np.float32)

        for idx, coeff in model.linear.items():
            h_np[idx] = coeff

        for (i, j), coeff in model.quadratic.items():
            j_np[i, j] = coeff
            j_np[j, i] = coeff

        return h_np, j_np

    def _auto_beta_range(self, h: npt.NDArray[np.float32], j_matrix: npt.NDArray[np.float32]) -> tuple[float, float]:
        """Compute a reasonable beta range from model coefficients.

        Uses the maximum absolute coefficient magnitude to set the
        temperature window: beta_start (high temperature) allows free
        exploration; beta_end (low temperature) freezes into a basin.
        """
        max_h = float(np.max(np.abs(h))) if h.size else 0.0
        max_j = float(np.max(np.abs(j_matrix))) if j_matrix.size else 0.0
        max_coeff = max(max_h, max_j, 1e-8)
        beta_start = 1.0 / (max_coeff * 10.0)
        beta_end = 10.0 / max_coeff
        return (beta_start, beta_end)

    def _compute_beta_schedule(
        self, num_sweeps: int, beta_range: tuple[float, float], schedule_type: str
    ) -> npt.NDArray[np.float32]:
        """Build a per-sweep inverse-temperature schedule.

        ``"geometric"`` spends more sweeps at low temperature where the
        landscape is frozen; ``"linear"`` ramps uniformly.
        """
        beta_start, beta_end = beta_range
        if num_sweeps == 1:
            # Single sweep anneals at beta_start (hottest), matching
            # SolverCudaGPU and the sweep-0 value of the multi-sweep schedule.
            return np.array([beta_start], dtype=np.float32)
        if schedule_type == "geometric":
            start = max(beta_start, 1e-12)
            schedule = np.geomspace(start, beta_end, num_sweeps)
        else:
            schedule = np.linspace(beta_start, beta_end, num_sweeps)
        return schedule.astype(np.float32)

    def _compute_graph_coloring(
        self, model: XQMX
    ) -> tuple[npt.NDArray[np.int32], npt.NDArray[np.int32], npt.NDArray[np.int32], int]:
        """Greedy graph colouring of the XQMX coupling graph.

        Returns ``(starts, counts, node_indices, num_colors)`` describing,
        for each colour, the slice of ``node_indices`` holding its members.
        Nodes in the same colour share no edge, so they can be Gibbs-updated
        independently within a sweep.
        """
        n = model.size
        adjacency: list[set[int]] = [set() for _ in range(n)]
        for i, j in model.quadratic:
            adjacency[i].add(j)
            adjacency[j].add(i)

        colors = [-1] * n
        for v in range(n):
            used = {colors[u] for u in adjacency[v] if colors[u] >= 0}
            color = 0
            while color in used:
                color += 1
            colors[v] = color

        num_colors = max(colors) + 1 if n else 0
        node_indices: list[int] = []
        starts: list[int] = []
        counts: list[int] = []
        for color in range(num_colors):
            members = [v for v in range(n) if colors[v] == color]
            starts.append(len(node_indices))
            counts.append(len(members))
            node_indices.extend(members)

        return (
            np.asarray(starts, dtype=np.int32),
            np.asarray(counts, dtype=np.int32),
            np.asarray(node_indices, dtype=np.int32),
            num_colors,
        )

    def _resolve_base_seed(self, seed: int | None) -> int:
        """Pick a concrete 32-bit seed for the on-device RNG."""
        if seed is not None:
            return int(seed) & 0xFFFFFFFF
        return int(np.random.SeedSequence().generate_state(1)[0])

    def _init_samples(self, rng: np.random.Generator, num_reads: int, n: int, is_spin: bool) -> npt.NDArray[np.int32]:
        """Initialise one random replica per read as a (num_reads, n) array."""
        raw = rng.integers(0, 2, size=(num_reads, n), dtype=np.int32)
        if is_spin:
            return (raw * 2 - 1).astype(np.int32)
        return raw

    # -- Metal buffer helpers ---------------------------------------------

    def _make_buffer(self, array: np.ndarray):
        """Create a shared Metal buffer initialised from a numpy array."""
        contiguous = np.ascontiguousarray(array)
        return self._device.newBufferWithBytes_length_options_(
            contiguous.tobytes(),
            int(contiguous.nbytes),
            self._metal.MTLResourceStorageModeShared,
        )

    def _empty_buffer(self, nbytes: int):
        """Create a zero-initialised shared Metal buffer."""
        return self._device.newBufferWithLength_options_(int(nbytes), self._metal.MTLResourceStorageModeShared)

    def _scalar_bytes(self, value: int, dtype: type) -> bytes:
        """Pack a scalar argument for ``setBytes_length_atIndex_``."""
        return np.asarray(value, dtype=dtype).tobytes()

    def _buffer_to_numpy(self, buffer, dtype: type, count: int) -> np.ndarray:
        """Read a Metal buffer back into a numpy array (a fresh copy)."""
        itemsize = np.dtype(dtype).itemsize
        view = buffer.contents().as_buffer(count * itemsize)
        return np.frombuffer(view, dtype=dtype, count=count).copy()

    def _dispatch(self, pipeline, encoder, num_reads: int) -> None:
        """Encode and dispatch one threadgroup per replica."""
        encoder.setComputePipelineState_(pipeline)
        grid = self._metal.MTLSizeMake(num_reads, 1, 1)
        per_group = self._metal.MTLSizeMake(1, 1, 1)
        encoder.dispatchThreadgroups_threadsPerThreadgroup_(grid, per_group)
        encoder.endEncoding()

    def _commit_and_wait(self, command_buffer) -> None:
        """Commit the command buffer and raise if the GPU reports an error.

        A failed dispatch (out-of-bounds access, GPU timeout, device loss)
        does not raise on its own -- it is reported via the command buffer's
        status. Without this check, ``energies`` would stay zero-initialised
        and the solver would silently return an initial random sample.
        """
        command_buffer.commit()
        command_buffer.waitUntilCompleted()
        if command_buffer.status() == self._metal.MTLCommandBufferStatusError:
            raise RuntimeError(f"Metal command buffer failed: {_error_text(command_buffer.error())}")

    # -- strategy dispatch ------------------------------------------------

    def _run_sa(
        self,
        model: XQMX,
        h: npt.NDArray[np.float32],
        j_matrix: npt.NDArray[np.float32],
        beta_schedule: npt.NDArray[np.float32],
        samples: npt.NDArray[np.int32],
        num_sweeps: int,
        base_seed: int,
        is_spin: bool,
    ) -> tuple[dict[int, int], float]:
        """Dispatch the SA kernel and return the best replica."""
        n = model.size
        num_reads = samples.shape[0]

        h_buf = self._make_buffer(h)
        j_buf = self._make_buffer(j_matrix.reshape(-1))
        beta_buf = self._make_buffer(beta_schedule)
        samples_buf = self._make_buffer(samples.reshape(-1))
        energies_buf = self._empty_buffer(num_reads * np.dtype(np.float32).itemsize)

        queue = self._device.newCommandQueue()
        command_buffer = queue.commandBuffer()
        encoder = command_buffer.computeCommandEncoder()
        encoder.setBuffer_offset_atIndex_(h_buf, 0, 0)
        encoder.setBuffer_offset_atIndex_(j_buf, 0, 1)
        encoder.setBuffer_offset_atIndex_(beta_buf, 0, 2)
        encoder.setBuffer_offset_atIndex_(samples_buf, 0, 3)
        encoder.setBuffer_offset_atIndex_(energies_buf, 0, 4)
        encoder.setBytes_length_atIndex_(self._scalar_bytes(n, np.int32), 4, 5)
        encoder.setBytes_length_atIndex_(self._scalar_bytes(num_sweeps, np.int32), 4, 6)
        encoder.setBytes_length_atIndex_(self._scalar_bytes(base_seed, np.uint32), 4, 7)
        encoder.setBytes_length_atIndex_(self._scalar_bytes(int(is_spin), np.int32), 4, 8)
        self._dispatch(self._sa_pipeline, encoder, num_reads)
        self._commit_and_wait(command_buffer)

        return self._collect_best(samples_buf, energies_buf, num_reads, n)

    def _run_gibbs(
        self,
        model: XQMX,
        h: npt.NDArray[np.float32],
        j_matrix: npt.NDArray[np.float32],
        beta_schedule: npt.NDArray[np.float32],
        samples: npt.NDArray[np.int32],
        num_sweeps: int,
        base_seed: int,
        is_spin: bool,
    ) -> tuple[dict[int, int], float]:
        """Dispatch the Gibbs kernel and return the best replica."""
        n = model.size
        num_reads = samples.shape[0]
        starts, counts, node_indices, num_colors = self._compute_graph_coloring(model)

        h_buf = self._make_buffer(h)
        j_buf = self._make_buffer(j_matrix.reshape(-1))
        beta_buf = self._make_buffer(beta_schedule)
        samples_buf = self._make_buffer(samples.reshape(-1))
        energies_buf = self._empty_buffer(num_reads * np.dtype(np.float32).itemsize)
        starts_buf = self._make_buffer(starts)
        counts_buf = self._make_buffer(counts)
        nodes_buf = self._make_buffer(node_indices)

        queue = self._device.newCommandQueue()
        command_buffer = queue.commandBuffer()
        encoder = command_buffer.computeCommandEncoder()
        encoder.setBuffer_offset_atIndex_(h_buf, 0, 0)
        encoder.setBuffer_offset_atIndex_(j_buf, 0, 1)
        encoder.setBuffer_offset_atIndex_(beta_buf, 0, 2)
        encoder.setBuffer_offset_atIndex_(samples_buf, 0, 3)
        encoder.setBuffer_offset_atIndex_(energies_buf, 0, 4)
        encoder.setBuffer_offset_atIndex_(starts_buf, 0, 5)
        encoder.setBuffer_offset_atIndex_(counts_buf, 0, 6)
        encoder.setBuffer_offset_atIndex_(nodes_buf, 0, 7)
        encoder.setBytes_length_atIndex_(self._scalar_bytes(n, np.int32), 4, 8)
        encoder.setBytes_length_atIndex_(self._scalar_bytes(num_sweeps, np.int32), 4, 9)
        encoder.setBytes_length_atIndex_(self._scalar_bytes(base_seed, np.uint32), 4, 10)
        encoder.setBytes_length_atIndex_(self._scalar_bytes(num_colors, np.int32), 4, 11)
        encoder.setBytes_length_atIndex_(self._scalar_bytes(int(is_spin), np.int32), 4, 12)
        self._dispatch(self._gibbs_pipeline, encoder, num_reads)
        self._commit_and_wait(command_buffer)

        return self._collect_best(samples_buf, energies_buf, num_reads, n)

    def _collect_best(self, samples_buf, energies_buf, num_reads: int, n: int) -> tuple[dict[int, int], float]:
        """Read back replicas, returning the lowest-energy sample dict."""
        energies = self._buffer_to_numpy(energies_buf, np.float32, num_reads)
        samples = self._buffer_to_numpy(samples_buf, np.int32, num_reads * n).reshape(num_reads, n)
        # float32 accumulation can overflow to inf/nan on degenerate replicas;
        # never let argmin pick one (it would silently return a wrong sample).
        finite = np.isfinite(energies)
        if not finite.any():
            raise RuntimeError(
                "All Metal replicas produced non-finite energies (float32 overflow). "
                "Reduce beta_range/num_sweeps or rescale model coefficients."
            )
        best_idx = int(np.argmin(np.where(finite, energies, np.inf)))
        raw_energy = float(energies[best_idx])
        best_row = samples[best_idx]
        best_sample_dict = {i: int(best_row[i]) for i in range(n)}
        return best_sample_dict, raw_energy
