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

"""Tests for BinaryImplementation, the --solve runner for solver-core binaries.

Most tests run against a stub binary: a Python script that answers
``--capabilities`` and ``--solve`` as configured and saves the request it was
sent. The ``quip_cpu`` tests run the real quip-cpu-sa binary named by
``XQSA_QUIP_CPU_SA``.
"""

from __future__ import annotations

import itertools
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from xqffi.vm import Domain, XqmxModel, XqmxSample
from xqsa.binary import BinaryImplementation
from xqsa.errors import (
    ModelRejectedError,
    SolverError,
    SolverFailedError,
    SolverProtocolError,
    SolverUnavailableError,
)
from xqsa.solvers import SolverCPU

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the stub binary relies on a shebang")

QUIP_CPU_SA_CAPS: dict[str, Any] = {
    "supportedKinds": ["ISING_SAMPLE", "ISING_GENERATE"],
    "maxNodes": 100000,
    "maxEdges": 1000000,
    "features": ["streaming", "linear-schedule"],
    "protocolVersion": 2,
    "streamWidth": 14,
    "encodings": ["COEFFICIENT_ENCODING_I32", "COEFFICIENT_ENCODING_F64"],
    "generators": ["GENERATOR_ALGORITHM_BLAKE3_CHACHA8_V1"],
    "backend": "cpu",
    "algorithm": "sa",
    "maxDegree": 0,
    "maxLocalFieldMilli": 0,
    "exactResolutionMilli": 1,
}

_STUB = """#!{python}
import json, pathlib, sys

here = pathlib.Path(__file__).parent
cfg = json.loads((here / "stub.json").read_text())
if sys.argv[1] == "--capabilities":
    if cfg["caps_exit"]:
        sys.exit(cfg["caps_exit"])
    caps = cfg["caps"]
    print(caps if isinstance(caps, str) else json.dumps(caps))
    sys.exit(0)
req = json.loads(sys.stdin.read())
(here / "request.json").write_text(json.dumps(req))
if cfg["exit_code"]:
    print(cfg["stderr"], file=sys.stderr)
    sys.exit(cfg["exit_code"])
if cfg["stdout"] is not None:
    print(cfg["stdout"])
    sys.exit(0)
sampler = {{
    "backend": "cpu",
    "algorithm": "sa",
    "num_reads": req["num_reads"],
    "num_sweeps": req["num_sweeps"],
    "sweeps_per_beta": req["sweeps_per_beta"],
    "beta_range": req["beta_range"],
    "beta_schedule_type": req.get("beta_schedule_type", "geometric"),
    "seed": req["seed"],
}}
reads = cfg["reads"] or [{{"spins": [1] * len(req["h"]), "energy_milli": 0}}] * req["num_reads"]
print(json.dumps([{{**r, "timing": {{"sample_us": 1, "total_us": 2}}, "sampler": sampler}} for r in reads]))
"""


def _tracing(detail: str) -> str:
    return f"2026-10-20T04:24:51.191345Z ERROR quip_solver_core::session: [stub] {detail}"


def make_stub(
    tmp_path: Path,
    *,
    caps: dict[str, Any] | str = QUIP_CPU_SA_CAPS,
    caps_exit: int = 0,
    exit_code: int = 0,
    stderr: str | None = None,
    reads: list[dict[str, Any]] | None = None,
    stdout: str | None = None,
) -> Path:
    """Write a stub solver binary into ``tmp_path`` and return its path."""
    config = {
        "caps": caps,
        "caps_exit": caps_exit,
        "exit_code": exit_code,
        "stderr": stderr if stderr is not None else _tracing(f"stub failure {exit_code}"),
        "stdout": stdout,
        "reads": reads,
    }
    (tmp_path / "stub.json").write_text(json.dumps(config))
    path = tmp_path / "solver"
    path.write_text(_STUB.format(python=sys.executable))
    path.chmod(0o755)
    return path


def _request(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / "request.json").read_text())


def _ground_energy(model: XqmxModel) -> int:
    """The model's minimum energy, by brute force."""
    values = (0, 1) if model.domain == Domain.BINARY else (-1, 1)
    return min(model.energy(XqmxSample(model.domain, list(v))) for v in itertools.product(values, repeat=model.size))


def _model(domain: str = "binary") -> XqmxModel:
    model = XqmxModel.binary(3) if domain == "binary" else XqmxModel.spin(3)
    model.set_linear(0, 2)
    model.set_linear(2, -1)
    model.set_quad(0, 1, 1)
    model.set_quad(1, 2, -3)
    return model


class TestConstruction:
    """--capabilities is read once and checked at construction."""

    def test_good_binary_caches_capabilities(self, tmp_path: Path) -> None:
        impl = BinaryImplementation(make_stub(tmp_path))
        assert impl.capabilities == QUIP_CPU_SA_CAPS

    @pytest.mark.parametrize(
        ("stub", "match"),
        [
            ({"caps_exit": 3}, "--capabilities failed"),
            ({"caps": "not json"}, "JSON object"),
            ({"caps": "[1, 2]"}, "JSON object"),
            ({"caps": QUIP_CPU_SA_CAPS | {"protocolVersion": 1}}, "protocol version 1"),
            ({"caps": QUIP_CPU_SA_CAPS | {"algorithm": "gibbs"}}, "runs algorithm 'gibbs'"),
            ({"caps": QUIP_CPU_SA_CAPS | {"supportedKinds": ["ISING_GENERATE"]}}, "ISING_SAMPLE"),
            ({"caps": QUIP_CPU_SA_CAPS | {"supportedKinds": "ISING_SAMPLE"}}, "supportedKinds is not a list"),
            ({"caps": QUIP_CPU_SA_CAPS | {"features": None}}, "features is not a list"),
            ({"caps": QUIP_CPU_SA_CAPS | {"maxNodes": "100000"}}, "maxNodes is not a non-negative integer"),
            ({"caps": QUIP_CPU_SA_CAPS | {"maxDegree": -1}}, "maxDegree is not a non-negative integer"),
            ({"caps": QUIP_CPU_SA_CAPS | {"exactResolutionMilli": True}}, "exactResolutionMilli is not"),
        ],
    )
    def test_unusable_binary(self, tmp_path: Path, stub: dict[str, Any], match: str) -> None:
        with pytest.raises(SolverUnavailableError, match=match):
            BinaryImplementation(make_stub(tmp_path, **stub))

    def test_missing_binary(self, tmp_path: Path) -> None:
        with pytest.raises(SolverUnavailableError, match="cannot start") as excinfo:
            BinaryImplementation(tmp_path / "absent")
        assert excinfo.value.exit_code is None


class TestWire:
    """What the runner sends on stdin."""

    def test_geometric_omits_schedule_and_maps_names(self, tmp_path: Path) -> None:
        solver = SolverCPU._binary(make_stub(tmp_path), num_reads=3, num_sweeps=20, num_sweeps_per_beta=4, seed=7)
        solver.solve(_model())
        req = _request(tmp_path)
        assert "beta_schedule_type" not in req
        assert req["sweeps_per_beta"] == 4
        assert "num_sweeps_per_beta" not in req
        assert (req["num_reads"], req["num_sweeps"], req["seed"]) == (3, 20, 7)
        assert req["beta_range"] is None
        assert req["edges"] == [[0, 1], [1, 2]]

    def test_linear_and_beta_range_are_sent(self, tmp_path: Path) -> None:
        solver = SolverCPU._binary(make_stub(tmp_path), beta_schedule_type="linear", beta_range=(0.1, 5.0))
        solver.solve(_model())
        req = _request(tmp_path)
        assert req["beta_schedule_type"] == "linear"
        assert req["beta_range"] == [0.1, 5.0]

    def test_coefficients_are_ints_when_whole_and_quarters_otherwise(self, tmp_path: Path) -> None:
        solver = SolverCPU._binary(make_stub(tmp_path))
        solver.solve(_model("spin"))
        spin = _request(tmp_path)
        assert spin["h"] == [2, 0, -1] and all(type(v) is int for v in spin["h"] + spin["j"])

        solver.solve(_model("binary"))
        binary = _request(tmp_path)
        # h' = h/2 + sum J/4, J' = J/4
        assert binary["h"] == [1.25, -0.5, -1.25]
        assert binary["j"] == [0.25, -0.75]

    def test_drawn_seed_is_reported(self, tmp_path: Path) -> None:
        result = SolverCPU._binary(make_stub(tmp_path)).solve(_model())
        seed = _request(tmp_path)["seed"]
        assert 0 <= seed < 2**64
        assert result.metadata["seed"] == seed

    def test_given_seed_passes_through(self, tmp_path: Path) -> None:
        solver = SolverCPU._binary(make_stub(tmp_path))
        result = solver.solve(_model(), seed=2**64 - 1)
        assert _request(tmp_path)["seed"] == 2**64 - 1
        assert result.metadata["seed"] == 2**64 - 1


class TestPreChecks:
    """Refusals made before the binary is spawned."""

    @pytest.mark.parametrize(
        ("caps", "params", "match"),
        [
            ({"features": []}, {"beta_schedule_type": "linear"}, "linear beta schedule"),
            ({"maxNodes": 2}, {}, "3 nodes exceed the maximum of 2"),
            ({"maxEdges": 1}, {}, "2 edges exceed the maximum of 1"),
            ({"maxDegree": 1}, {}, "node degree of 2"),
            # node 2 under BINARY: |h'| = 1250 milli, plus |J'| = 750
            ({"maxLocalFieldMilli": 1999}, {}, "local field of 2000 milli"),
            ({"features": ["exact-coefficients"], "exactResolutionMilli": 500}, {}, "multiple of 500 milli"),
        ],
    )
    def test_capability_refusals(
        self, tmp_path: Path, caps: dict[str, Any], params: dict[str, Any], match: str
    ) -> None:
        solver = SolverCPU._binary(make_stub(tmp_path, caps=QUIP_CPU_SA_CAPS | caps), **params)
        with pytest.raises(ModelRejectedError, match=match) as excinfo:
            solver.solve(_model())
        assert excinfo.value.exit_code is None
        assert not (tmp_path / "request.json").exists()

    def test_i32_overflow(self, tmp_path: Path) -> None:
        model = XqmxModel.spin(2)
        model.set_linear(0, 2**31 // 1000 + 1)
        with pytest.raises(ModelRejectedError, match="32-bit"):
            SolverCPU._binary(make_stub(tmp_path)).solve(model)
        assert not (tmp_path / "request.json").exists()

    @pytest.mark.parametrize(
        "params",
        [
            {"num_reads": 0},
            {"num_sweeps": 10, "num_sweeps_per_beta": 3},
            {"beta_schedule_type": "cosine"},
            {"seed": -1},
            {"seed": 2**64},
            {"seed": True},
        ],
    )
    def test_parameter_refusals(self, tmp_path: Path, params: dict[str, Any]) -> None:
        with pytest.raises(ValueError):
            SolverCPU._binary(make_stub(tmp_path)).solve(_model(), **params)
        assert not (tmp_path / "request.json").exists()


class TestFailures:
    """The binary's exit codes and malformed output."""

    @pytest.mark.parametrize(
        ("exit_code", "error"),
        [
            (64, SolverProtocolError),
            (65, ModelRejectedError),
            (69, SolverUnavailableError),
            (70, SolverFailedError),
            (3, SolverFailedError),
        ],
    )
    def test_exit_codes(self, tmp_path: Path, exit_code: int, error: type[SolverError]) -> None:
        stderr = "noise\n" + _tracing("solve failed: model exceeds this solver: too big") + "\n\n"
        solver = SolverCPU._binary(make_stub(tmp_path, exit_code=exit_code, stderr=stderr))
        with pytest.raises(error) as excinfo:
            solver.solve(_model())
        assert type(excinfo.value) is error
        assert excinfo.value.exit_code == exit_code
        assert excinfo.value.detail == "solve failed: model exceeds this solver: too big"

    def test_untraced_stderr_is_kept_whole(self, tmp_path: Path) -> None:
        solver = SolverCPU._binary(make_stub(tmp_path, exit_code=70, stderr="  segfault  "))
        with pytest.raises(SolverFailedError) as excinfo:
            solver.solve(_model())
        assert excinfo.value.detail == "segfault"

    @pytest.mark.parametrize(
        "stdout",
        [
            "[]",
            "garbage",
            json.dumps([{"spins": [1, 1], "energy_milli": 0, "timing": {}, "sampler": {}}]),
            json.dumps([{"spins": [1, 0, 1], "energy_milli": 0, "timing": {}, "sampler": {}}]),
            json.dumps([{"spins": [1, 1, 1], "energy_milli": 0, "timing": {}, "sampler": {}}]),
        ],
    )
    def test_bad_output(self, tmp_path: Path, stdout: str) -> None:
        with pytest.raises(SolverFailedError):
            SolverCPU._binary(make_stub(tmp_path, stdout=stdout)).solve(_model())


class TestResult:
    """The best read becomes the SolverResult."""

    def test_best_read_first_on_ties(self, tmp_path: Path) -> None:
        reads = [
            {"spins": [1, 1, 1], "energy_milli": 5},
            {"spins": [-1, 1, -1], "energy_milli": -3},
            {"spins": [1, -1, 1], "energy_milli": -3},
            {"spins": [-1, -1, -1], "energy_milli": 2},
        ]
        result = SolverCPU._binary(make_stub(tmp_path, reads=reads)).solve(_model())
        assert result.sample.values == [0, 1, 0]
        assert result.metadata["reads"] == 4

    def test_metadata(self, tmp_path: Path) -> None:
        solver = SolverCPU._binary(make_stub(tmp_path), num_sweeps=30, num_sweeps_per_beta=3, seed=11)
        result = solver.solve(_model())
        assert result.metadata == {
            "seed": 11,
            "reads": 100,
            "params": {
                "num_sweeps": 30,
                "num_sweeps_per_beta": 3,
                "beta_range": None,
                "beta_schedule_type": "geometric",
                "timing": {"sample_us": 1, "total_us": 2},
                "algorithm": "sa",
            },
        }

    @pytest.mark.parametrize("domain", ["binary", "spin"])
    def test_energy_is_the_models(self, tmp_path: Path, domain: str) -> None:
        model = _model(domain)
        result = SolverCPU._binary(make_stub(tmp_path)).solve(model)
        assert result.energy == model.energy(result.sample)
        assert result.sample.domain == model.domain


_REAL = os.environ.get("XQSA_QUIP_CPU_SA")


@pytest.mark.quip_cpu
@pytest.mark.skipif(not _REAL, reason="XQSA_QUIP_CPU_SA is not set")
class TestQuipCpuSa:
    """Against the real quip-cpu-sa binary."""

    def test_construction(self) -> None:
        impl = BinaryImplementation(str(_REAL))
        assert impl.capabilities["protocolVersion"] == 2
        assert impl.capabilities["algorithm"] == "sa"

    @pytest.mark.parametrize("domain", ["binary", "spin"])
    def test_round_trip(self, domain: str) -> None:
        model = _model(domain)
        result = SolverCPU._binary(str(_REAL), num_reads=20, seed=5).solve(model)
        assert result.energy == _ground_energy(model)
        assert result.metadata["reads"] == 20
        assert result.metadata["params"]["beta_schedule_type"] == "geometric"

    def test_same_seed_same_sample(self) -> None:
        solver = SolverCPU._binary(str(_REAL), num_reads=5, num_sweeps=50)
        model = _model()
        first, second = solver.solve(model, seed=99), solver.solve(model, seed=99)
        assert first.sample.values == second.sample.values

    def test_drawn_seed_reproduces(self) -> None:
        solver = SolverCPU._binary(str(_REAL), num_reads=5, num_sweeps=50)
        model = _model()
        drawn = solver.solve(model)
        again = solver.solve(model, seed=drawn.metadata["seed"])
        assert again.sample.values == drawn.sample.values

    def test_linear_schedule(self) -> None:
        result = SolverCPU._binary(str(_REAL), beta_schedule_type="linear", num_reads=5).solve(_model())
        assert result.metadata["params"]["beta_schedule_type"] == "linear"

    def test_i32_overflow_refused_before_spawn(self) -> None:
        model = XqmxModel.spin(2)
        model.set_linear(0, 2**31 // 1000 + 1)
        with pytest.raises(ModelRejectedError) as excinfo:
            SolverCPU._binary(str(_REAL)).solve(model)
        assert excinfo.value.exit_code is None

    def test_node_limit_refused_before_spawn(self) -> None:
        with pytest.raises(ModelRejectedError, match="100001 nodes") as excinfo:
            SolverCPU._binary(str(_REAL)).solve(XqmxModel.spin(100001))
        assert excinfo.value.exit_code is None

    def test_binary_refuses_too_many_nodes(self) -> None:
        """With the node pre-check lifted, the binary's own exit 65 is the backstop."""
        impl = BinaryImplementation(str(_REAL), num_reads=1, num_sweeps=1)
        impl.capabilities["maxNodes"] = 0
        model = XqmxModel.spin(100001)
        with pytest.raises(ModelRejectedError) as excinfo:
            impl.run(model, **impl.defaults)
        assert excinfo.value.exit_code == 65
