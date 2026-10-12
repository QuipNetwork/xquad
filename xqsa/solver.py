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
Solver base class, implementation interface and result types for XQMX
quadratic models.

A :class:`Solver` is the public face of a backend. Its :meth:`Solver.solve`
is a template: it validates the model, rejects unknown per-call keywords, runs
the composed :class:`Implementation`, recomputes the energy on the original
model, and builds the ``{seed, reads, params}`` metadata. An implementation
only turns a model into a sample; swapping it changes how a solver runs
without changing its API.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import dimod

from xqffi.vm import Domain, XqmxModel, XqmxSample

_SUPPORTED_SCHEDULES = frozenset({"geometric", "linear"})


@dataclass(frozen=True)
class SolverResult:
    """Result from a solver."""

    sample: XqmxSample
    energy: int
    timing: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunOutput:
    """What an :class:`Implementation` returns from one run.

    ``seed`` is the seed the run used (``None`` when unseeded), ``reads`` the
    number of reads taken, and ``params`` everything else that describes the
    run, including the selectors that picked the implementation.
    """

    sample: XqmxSample
    seed: int | None
    reads: int
    params: dict[str, Any]


class Implementation(ABC):
    """How a :class:`Solver` turns a model into a sample.

    ``accepts`` names the per-call keywords :meth:`run` takes; the solver
    rejects any other keyword before calling it.
    """

    accepts: frozenset[str] = frozenset()

    @abstractmethod
    def run(self, model: XqmxModel, **kwargs: Any) -> RunOutput:
        """Sample a validated model, returning the best sample found."""
        ...


class Solver(ABC):
    """Solver base class for XQMX quadratic models.

    Subclasses pass the :class:`Implementation` they run to ``__init__``.
    ``SolverQuip`` overrides :meth:`solve` and builds its own metadata.
    """

    def __init__(self, impl: Implementation) -> None:
        self._impl = impl

    def solve(self, model: XqmxModel, **kwargs: Any) -> SolverResult:
        """Solve a quadratic model, returning the best solution found.

        Raises:
            ValueError: if the model is not a BINARY or SPIN ``XqmxModel``.
            TypeError: if a keyword is not one the implementation accepts.
        """
        self._validate_model(model)
        unknown = kwargs.keys() - self._impl.accepts
        if unknown:
            raise TypeError(
                f"{type(self).__name__}.solve() got unexpected keyword argument(s) "
                f"{', '.join(repr(k) for k in sorted(unknown))}; accepted: {', '.join(sorted(self._impl.accepts))}"
            )

        t0 = time.perf_counter()
        out = self._impl.run(model, **kwargs)
        elapsed = time.perf_counter() - t0

        return SolverResult(
            sample=out.sample,
            energy=self._recompute_energy(model, out.sample),
            timing=elapsed,
            metadata={"seed": out.seed, "reads": out.reads, "params": out.params},
        )

    def _validate_model(self, model: XqmxModel) -> None:
        """Validate that the model is solvable."""
        if not isinstance(model, XqmxModel):
            raise ValueError(f"Expected an XqmxModel, got {type(model).__name__}")
        if model.domain not in (Domain.BINARY, Domain.SPIN):
            raise ValueError(f"Unsupported domain for solving: {model.domain.name}")

    def _recompute_energy(self, model: XqmxModel, sample: XqmxSample) -> int:
        """Compute authoritative integer energy for a model-sample pair."""
        return model.energy(sample)


def model_to_bqm(model: XqmxModel) -> dimod.BinaryQuadraticModel:
    """Convert an XQMX model to a dimod BQM."""
    vartype = dimod.BINARY if model.domain == Domain.BINARY else dimod.SPIN
    return dimod.BinaryQuadraticModel(
        dict(model.linear_items()),
        dict(model.quadratic_items()),
        0.0,
        vartype,
    )


def sample_to_xqmx(model: XqmxModel, raw_sample: dict[int, int]) -> XqmxSample:
    """Convert a dimod sample dict to an XQMX sample.

    dimod omits variables with no terms; those keep the domain default.
    """
    sample = XqmxSample.default(model.domain, model.size, model.rows, model.cols)
    for var_idx, value in raw_sample.items():
        sample.set_linear(int(var_idx), int(value))

    return sample


def _check_algorithm(algorithm: str, supported: frozenset[str], where: str) -> None:
    """Reject an algorithm the in-process implementation does not run."""
    if algorithm not in supported:
        raise ValueError(
            f"Algorithm {algorithm!r} is not available in-process on {where} (supported: {sorted(supported)}); "
            "further algorithms arrive with the solver binaries."
        )


def _check_anneal_params(num_reads: int, num_sweeps: int, num_sweeps_per_beta: int) -> int:
    """Validate the shared annealing parameters, returning ``num_betas``."""
    if num_reads < 1:
        raise ValueError(f"num_reads must be >= 1, got {num_reads}")
    if num_sweeps < 1:
        raise ValueError(f"num_sweeps must be >= 1, got {num_sweeps}")
    if not isinstance(num_sweeps_per_beta, int):
        raise ValueError(f"num_sweeps_per_beta must be an int, got {type(num_sweeps_per_beta).__name__}")
    if num_sweeps_per_beta < 1:
        raise ValueError(f"num_sweeps_per_beta must be >= 1, got {num_sweeps_per_beta}")
    num_betas, rem = divmod(num_sweeps, num_sweeps_per_beta)
    if rem != 0:
        raise ValueError(f"num_sweeps ({num_sweeps}) must be divisible by num_sweeps_per_beta ({num_sweeps_per_beta})")
    return num_betas


def _check_beta_schedule(beta_schedule_type: str) -> None:
    """Reject a beta schedule shape the GPU engines do not build."""
    if beta_schedule_type not in _SUPPORTED_SCHEDULES:
        raise ValueError(
            f"Unsupported beta_schedule_type {beta_schedule_type!r}. Supported: {sorted(_SUPPORTED_SCHEDULES)}"
        )
