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
An :class:`~xqsa.solver.Implementation` that runs a solver-core binary.

The binary is asked for its ``--capabilities`` once, at construction. Each
run converts the model to Ising in milli units (:mod:`xqsa.ising`), checks it
against those capabilities, sends it to ``<binary> --solve`` as one JSON
object on stdin, and keeps the lowest-energy read from the JSON array on
stdout. Nothing here is specific to one solver family.
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import subprocess
from collections import Counter
from typing import Any

from xqffi.vm import XqmxModel

from .errors import ModelRejectedError, SolverError, SolverFailedError, SolverProtocolError, SolverUnavailableError
from .ising import IsingMilli, spins_to_sample, to_ising_milli
from .solver import Implementation, RunOutput, _check_anneal_params, _check_beta_schedule

_PROTOCOL_VERSION = 2
_I32_MIN, _I32_MAX = -(2**31), 2**31 - 1
_SEED_LIMIT = 2**64
_CAPABILITIES_TIMEOUT = 10.0  # seconds; --solve has none, since its run time scales with the model
_STDERR_PREFIX = re.compile(r"\[quip-solver-[^\]]*\] ")
_LIMIT_KEYS = ("maxNodes", "maxEdges", "maxDegree", "maxLocalFieldMilli", "exactResolutionMilli")
_SAMPLER_KEYS = frozenset({"seed", "num_sweeps", "sweeps_per_beta", "beta_range", "beta_schedule_type"})

_EXIT_ERRORS: dict[int, type[SolverError]] = {
    64: SolverProtocolError,
    65: ModelRejectedError,
    69: SolverUnavailableError,
}


class BinaryImplementation(Implementation):
    """Simulated annealing (or another ``algorithm``) in a solver-core binary.

    Raises:
        SolverUnavailableError: at construction, if the binary does not start,
            fails ``--capabilities``, or does not offer protocol 2, the
            requested ``algorithm`` and ``ISING_SAMPLE``.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        algorithm: str = "sa",
        *,
        num_reads: int = 100,
        num_sweeps: int = 1000,
        num_sweeps_per_beta: int = 1,
        beta_range: tuple[float, float] | None = None,
        seed: int | None = None,
        beta_schedule_type: str = "geometric",
    ) -> None:
        self.path = os.fspath(path)
        self.capabilities = self._read_capabilities(algorithm)
        super().__init__(
            num_reads=num_reads,
            num_sweeps=num_sweeps,
            num_sweeps_per_beta=num_sweeps_per_beta,
            beta_range=beta_range,
            seed=seed,
            beta_schedule_type=beta_schedule_type,
        )

    def run(
        self,
        model: XqmxModel,
        *,
        num_reads: int,
        num_sweeps: int,
        num_sweeps_per_beta: int,
        beta_range: tuple[float, float] | None,
        seed: int | None,
        beta_schedule_type: str,
    ) -> RunOutput:
        """Sample a model in the binary, returning its lowest-energy read.

        Raises:
            ValueError: for an invalid parameter or seed.
            ModelRejectedError: if the model exceeds the binary's capabilities
                (checked before the binary is spawned) or the binary refuses it.
            SolverError: for any other failure of the binary.
        """
        _check_anneal_params(num_reads, num_sweeps, num_sweeps_per_beta)
        _check_beta_schedule(beta_schedule_type)
        if seed is not None and not (_is_int(seed) and 0 <= seed < _SEED_LIMIT):
            raise ValueError(f"seed must be an int in [0, 2**64), got {seed!r}")
        _check_beta_range(beta_range)

        ising = to_ising_milli(model)
        self._check_model(ising, beta_schedule_type)

        request: dict[str, Any] = {
            "h": [_wire(v) for v in ising.h],
            "j": [_wire(v) for v in ising.j],
            "edges": [list(e) for e in ising.edges],
            "num_reads": num_reads,
            "num_sweeps": num_sweeps,
            "sweeps_per_beta": num_sweeps_per_beta,
            "beta_range": None if beta_range is None else list(beta_range),
            "seed": secrets.randbits(64) if seed is None else seed,
        }
        if beta_schedule_type == "linear":
            request["beta_schedule_type"] = "linear"

        proc = self._spawn("--solve", json.dumps(request))
        if proc.returncode != 0:
            error = _EXIT_ERRORS.get(proc.returncode, SolverFailedError)
            raise error(self.path, exit_code=proc.returncode, detail=_stderr_detail(proc.stderr))

        reads = self._parse_reads(proc.stdout, len(ising.h))
        best = min(reads, key=lambda read: read["energy_milli"])  # min keeps the first on ties
        sampler = best["sampler"]
        return RunOutput(
            sample=spins_to_sample(model, best["spins"]),
            seed=sampler["seed"],
            reads=len(reads),
            params={
                "num_sweeps": sampler["num_sweeps"],
                "num_sweeps_per_beta": sampler["sweeps_per_beta"],
                "beta_range": None if sampler["beta_range"] is None else tuple(sampler["beta_range"]),
                "beta_schedule_type": sampler["beta_schedule_type"],
                "timing": best["timing"],
            },
        )

    def _spawn(
        self, flag: str, stdin: str | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [self.path, flag],
                input=stdin,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except OSError as e:
            raise SolverUnavailableError(self.path, detail=f"cannot start: {e}") from e
        except subprocess.TimeoutExpired as e:
            raise SolverUnavailableError(self.path, detail=f"{flag} did not finish within {timeout} s") from e

    def _read_capabilities(self, algorithm: str) -> dict[str, Any]:
        proc = self._spawn("--capabilities", timeout=_CAPABILITIES_TIMEOUT)

        def unavailable(detail: str) -> SolverUnavailableError:
            return SolverUnavailableError(self.path, exit_code=proc.returncode or None, detail=detail)

        if proc.returncode != 0:
            raise unavailable(f"--capabilities failed: {_stderr_detail(proc.stderr)}")
        try:
            caps = json.loads(proc.stdout)
        except json.JSONDecodeError:
            caps = None
        if not isinstance(caps, dict):
            raise unavailable("--capabilities did not print a JSON object")
        if caps.get("protocolVersion") != _PROTOCOL_VERSION:
            raise unavailable(f"protocol version {caps.get('protocolVersion')!r}, expected {_PROTOCOL_VERSION}")
        if caps.get("algorithm") != algorithm:
            raise unavailable(f"runs algorithm {caps.get('algorithm')!r}, not {algorithm!r}")
        for key in ("supportedKinds", "features"):
            if not isinstance(caps.get(key, []), list):
                raise unavailable(f"{key} is not a list: {caps[key]!r}")
        for key in _LIMIT_KEYS:
            if not (_is_int(caps.get(key, 0)) and caps.get(key, 0) >= 0):
                raise unavailable(f"{key} is not a non-negative integer: {caps[key]!r}")
        if "ISING_SAMPLE" not in caps.get("supportedKinds", []):
            raise unavailable("does not support ISING_SAMPLE")
        return caps

    def _check_model(self, ising: IsingMilli, beta_schedule_type: str) -> None:
        """Refuse, before spawning, what the binary's capabilities rule out."""
        caps = self.capabilities
        features = caps.get("features", [])

        def reject(detail: str) -> ModelRejectedError:
            return ModelRejectedError(self.path, detail=detail)

        if beta_schedule_type == "linear" and "linear-schedule" not in features:
            raise reject("the linear beta schedule is not supported")
        for count, what, key in ((len(ising.h), "nodes", "maxNodes"), (len(ising.edges), "edges", "maxEdges")):
            if caps.get(key) and count > caps[key]:
                raise reject(f"{count} {what} exceed the maximum of {caps[key]}")

        if caps.get("maxDegree") or caps.get("maxLocalFieldMilli"):
            degree: Counter[int] = Counter()
            field = [abs(v) for v in ising.h]
            for (a, b), j in zip(ising.edges, ising.j, strict=True):
                degree.update((a, b))
                field[a] += abs(j)
                field[b] += abs(j)
            if caps.get("maxDegree") and max(degree.values(), default=0) > caps["maxDegree"]:
                raise reject(f"a node degree of {max(degree.values())} exceeds the maximum of {caps['maxDegree']}")
            if caps.get("maxLocalFieldMilli") and max(field, default=0) > caps["maxLocalFieldMilli"]:
                raise reject(f"a local field of {max(field)} milli exceeds the maximum of {caps['maxLocalFieldMilli']}")

        values = ising.h + ising.j
        resolution = caps.get("exactResolutionMilli")
        if "exact-coefficients" in features and resolution and any(v % resolution for v in values):
            raise reject(f"a coefficient is not a multiple of {resolution} milli")
        if any(not _I32_MIN <= v <= _I32_MAX for v in values):
            raise reject("a coefficient is outside the 32-bit milli range")

    def _parse_reads(self, stdout: str, nodes: int) -> list[dict[str, Any]]:
        def failed(detail: str) -> SolverFailedError:
            return SolverFailedError(self.path, exit_code=0, detail=detail)

        try:
            reads = json.loads(stdout)
        except json.JSONDecodeError as e:
            raise failed(f"--solve output is not JSON: {e}") from e
        if not isinstance(reads, list) or not reads:
            raise failed("--solve output is not a non-empty array of reads")
        for read in reads:
            if not (
                isinstance(read, dict)
                and _is_int(read.get("energy_milli"))
                and isinstance(read.get("sampler"), dict)
                and _SAMPLER_KEYS <= read["sampler"].keys()
                and isinstance(read.get("timing"), dict)
            ):
                raise failed("a read lacks energy_milli, timing or a sampler field")
            spins = read.get("spins")
            if (
                not isinstance(spins, list)
                or len(spins) != nodes
                or any(not _is_int(s) or s not in (-1, 1) for s in spins)
            ):
                raise failed(f"a read's spins are not {nodes} values of -1 or +1")
        return reads


def _is_int(value: object) -> bool:
    """An int that is not a bool."""
    return isinstance(value, int) and not isinstance(value, bool)


def _check_beta_range(beta_range: object) -> None:
    """Refuse a beta_range that is not None or two finite positive numbers."""
    if beta_range is None:
        return
    if not (
        isinstance(beta_range, tuple | list)
        and len(beta_range) == 2
        and all(
            isinstance(b, int | float) and not isinstance(b, bool) and math.isfinite(b) and b > 0 for b in beta_range
        )
    ):
        raise ValueError(f"beta_range must be None or two finite positive numbers, got {beta_range!r}")


def _wire(milli: int) -> int | float:
    """A milli coefficient in units: an int when whole, else an exact quarter."""
    return milli // 1000 if milli % 1000 == 0 else milli / 1000


def _stderr_detail(stderr: str) -> str:
    """The last stderr line after solver-core's ``[quip-solver-<backend>]`` prefix, or all of stderr."""
    lines = [line for line in stderr.splitlines() if line.strip()]
    match = _STDERR_PREFIX.search(lines[-1]) if lines else None
    return lines[-1][match.end() :].strip() if match else stderr.strip()
