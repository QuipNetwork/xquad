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

"""QUI-167 scale sweep: MaxCut & TSP at growing sizes across VMs and solvers.

Orchestrator mode (default) fans out over (problem, backend, solver, n,
seed), running each point as a child process of this same script
(``--point``) for peak-RSS accounting and crash isolation. Point mode
re-runs the shipped example encoding through the full
compile -> encode -> solve -> verify -> decode pipeline with per-stage
timers.

Design: docs/design/specs/2026-07-13-qui-167-scale-sweep-design.md

Usage:
    uv run --no-sync python scripts/scale_sweep.py --smoke
    uv run --no-sync python scripts/scale_sweep.py --out target/scale-sweep/results.jsonl
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import resource
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

CPU_SOLVER = "dwave-cpu"
GPU_SOLVER = "cuda-gpu"
SEEDS = (42, 43, 44)


def load_runner(example: str) -> ModuleType:
    """Import ``examples/<example>/runner.py`` as a module, byte-identical
    to what ships — the sweep must exercise the real encodings."""
    path = REPO_ROOT / "examples" / example / "runner.py"
    spec = importlib.util.spec_from_file_location(f"scale_sweep_{example}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load example runner: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class Adapter:
    """Everything the harness needs to know about one example problem."""

    name: str
    ladder: tuple[int, ...]
    gpu_ladder: tuple[int, ...]
    runner_module: str
    # (n, aux) -> encoder calldata, where aux is build_problem()'s 2nd return
    build_calldata: Callable[[int, Any], list[Any]]
    structurally_valid: Callable[[list[int], int], bool]
    # (solution, aux, n) -> host-side objective value
    objective: Callable[[list[int], Any, int], int]
    canonicalize_attr: str  # runner attr canonicalizing a solution
    model_vars: Callable[[int], int]
    approx_quad_terms: Callable[[int], int]

    def runner(self) -> ModuleType:
        return load_runner(self.runner_module)


def _maxcut_valid(solution: list[int], n: int) -> bool:
    return len(solution) == n and all(bit in (0, 1) for bit in solution)


def _tsp_valid(solution: list[int], n: int) -> bool:
    return sorted(solution) == list(range(n))


def make_adapters() -> dict[str, Adapter]:
    """Registry of swept problems. Adding a problem is one entry here,
    provided its runner exposes build_problem(n, seed) and a canonicalize
    helper."""

    def maxcut_calldata(n: int, edges: Any) -> list[Any]:
        flat: list[int] = []
        for i, j, w in edges:
            flat.extend((i, j, w))
        return [n, flat]

    def maxcut_objective(solution: list[int], edges: Any, n: int) -> int:
        return sum(w for i, j, w in edges if solution[i] != solution[j])

    def tsp_objective(solution: list[int], distances: Any, n: int) -> int:
        return load_runner("tsp").tour_distance(solution, distances, n)

    return {
        "maxcut": Adapter(
            name="maxcut",
            ladder=(8, 16, 32, 64, 128, 256, 512),
            gpu_ladder=(8, 16, 32, 64, 128, 256, 512, 1024),
            runner_module="maxcut",
            build_calldata=maxcut_calldata,
            structurally_valid=_maxcut_valid,
            objective=maxcut_objective,
            canonicalize_attr="canonicalize_partition",
            model_vars=lambda n: n,
            approx_quad_terms=lambda n: n * (n - 1) // 2,
        ),
        "tsp": Adapter(
            name="tsp",
            ladder=(4, 6, 8, 12, 16, 24, 32, 48),
            gpu_ladder=(4, 6, 8, 12, 16, 24, 32, 48, 64),
            runner_module="tsp",
            build_calldata=lambda n, distances: [n, distances],
            structurally_valid=_tsp_valid,
            objective=tsp_objective,
            canonicalize_attr="canonicalize_tour",
            model_vars=lambda n: n * n,
            approx_quad_terms=lambda n: 2 * n * n * (n - 1),
        ),
    }


STAGES = ("compile", "encode", "solve", "verify", "decode")


def _run_vm(backend: Any, calldata: list[Any], slots: int, program: Any) -> list[Any]:
    from xquad.vm import VM

    vm = VM(backend=backend)
    vm.set_calldata(calldata)
    vm.set_output_slots(slots)
    vm.run(program)
    return vm.outputs()


def _vec_to_list(value: Any, n: int) -> list[int]:
    from xquad.types import Vec

    if isinstance(value, Vec):
        return [value.get(i) for i in range(n)]
    return list(value)


def _peak_rss_kib() -> int:
    """Peak RSS of this process in KiB (ru_maxrss is KiB on Linux, bytes
    on macOS)."""
    maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return maxrss // 1024 if sys.platform == "darwin" else maxrss


def measure_point(adapter: Adapter, backend_name: str, solver_name: str, n: int, seed: int) -> dict:
    """Run one full pipeline pass, timing each stage. Never raises: stage
    and post-processing failures come back as ``status: error`` records so
    the orchestrator can log them and stop the lane."""
    from xquad.sa import build_solver
    from xquad.vm import VMBackend

    record: dict[str, Any] = {
        "schema": 1,
        "problem": adapter.name,
        "backend": backend_name,
        "solver": solver_name,
        "n": n,
        "seed": seed,
        "status": "ok",
    }
    backend = VMBackend.PYTHON if backend_name == "python" else VMBackend.RUST
    runner = adapter.runner()
    stages: dict[str, float] = {}
    stage = "compile"
    try:
        problem, aux = runner.build_problem(n, seed)
        t0 = time.perf_counter()
        programs = problem.compile()
        stages["compile"] = time.perf_counter() - t0

        stage = "encode"
        t0 = time.perf_counter()
        model = _run_vm(backend, adapter.build_calldata(n, aux), 1, programs.encoder)[0]
        stages["encode"] = time.perf_counter() - t0

        stage = "solve"
        t0 = time.perf_counter()
        sample = build_solver(solver_name, seed=seed).solve(model).sample
        stages["solve"] = time.perf_counter() - t0

        stage = "verify"
        t0 = time.perf_counter()
        energy, valid = _run_vm(backend, [model, sample, n], 2, programs.verifier)
        stages["verify"] = time.perf_counter() - t0

        stage = "decode"
        t0 = time.perf_counter()
        decoded = _run_vm(backend, [sample, n], 1, programs.decoder)[0]
        stages["decode"] = time.perf_counter() - t0

        stage = "postprocess"
        solution = getattr(runner, adapter.canonicalize_attr)(_vec_to_list(decoded, n))
        record.update(
            stages=stages,
            model={"vars": adapter.model_vars(n), "approx_quad_terms": adapter.approx_quad_terms(n)},
            energy=int(energy),
            valid=int(valid),
            structural_ok=adapter.structurally_valid(solution, n),
            objective=adapter.objective(solution, aux, n),
            solution=solution,
            peak_rss_kib=_peak_rss_kib(),
        )
    except Exception as exc:  # noqa: BLE001 - boundary: report, don't crash
        record.update(status="error", stage=stage, message=f"{type(exc).__name__}: {exc}")
    return record


def _point_main(args: argparse.Namespace) -> int:
    adapter = make_adapters()[args.problem]
    record = measure_point(adapter, args.backend, args.solver, args.n, args.seed)
    json.dump(record, sys.stdout)
    sys.stdout.write("\n")
    return 0 if record["status"] == "ok" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="QUI-167 MaxCut/TSP scale sweep")
    parser.add_argument("--point", action="store_true", help="run a single measurement (internal)")
    parser.add_argument("--problem", choices=sorted(make_adapters()))
    parser.add_argument("--backend", choices=("python", "rust"))
    parser.add_argument("--solver", default=CPU_SOLVER)
    parser.add_argument("--n", type=int)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--timeout", type=float, default=300.0, help="per-point timeout (s)")
    parser.add_argument("--smoke", action="store_true", help="tiny ladder, one seed")
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "target" / "scale-sweep" / "results.jsonl",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="markdown summary (default: alongside --out)",
    )
    return parser


@dataclass(frozen=True)
class Lane:
    """One escalation lane: fixed (problem, backend, solver), growing n."""

    problem: str
    backend: str
    solver: str
    ladder: tuple[int, ...]


def cuda_available() -> bool:
    """True when cupy is installed and a CUDA device is present.
    Mirrors scripts/example-smoke.py's probe."""
    try:
        import cupy

        return bool(cupy.cuda.runtime.getDeviceCount() > 0)
    except Exception:  # noqa: BLE001 - any import/driver failure means "no"
        return False


def build_matrix(adapters: dict[str, Adapter], solvers: list[str], smoke: bool) -> list[Lane]:
    """CPU lanes on both VMs; GPU lanes on the Rust VM only (solver choice
    affects only the solve stage — cross-VM comparison is covered by the
    CPU lanes)."""
    lanes: list[Lane] = []
    for adapter in adapters.values():
        for solver in solvers:
            backends = ("python", "rust") if solver == CPU_SOLVER else ("rust",)
            ladder = adapter.ladder if solver == CPU_SOLVER else adapter.gpu_ladder
            if smoke:
                ladder = ladder[:1]
            for backend in backends:
                lanes.append(Lane(adapter.name, backend, solver, ladder))
    return lanes


def parse_point_stdout(stdout: str) -> dict | None:
    """Extract the child's JSON record from the last non-empty stdout line.

    Point mode prints the record as its final line, so incidental prints
    from imported runners or solvers earlier in the stream cannot corrupt
    the machine-readable channel. Returns None when no parseable record
    is present (a genuine crash)."""
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        return None
    try:
        record = json.loads(lines[-1])
    except json.JSONDecodeError:
        return None
    return record if isinstance(record, dict) else None


def run_point_child(lane: Lane, n: int, seed: int, timeout: float) -> dict:
    """Execute one point in a child process. Timeouts and crashes become
    records, never exceptions — a lane ceiling is a finding, not an error."""
    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--point",
        "--problem",
        lane.problem,
        "--backend",
        lane.backend,
        "--solver",
        lane.solver,
        "--n",
        str(n),
        "--seed",
        str(seed),
    ]
    base = {
        "schema": 1,
        "problem": lane.problem,
        "backend": lane.backend,
        "solver": lane.solver,
        "n": n,
        "seed": seed,
    }
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=REPO_ROOT, env=os.environ)
    except subprocess.TimeoutExpired:
        return {**base, "status": "timeout", "timeout_s": timeout}
    if proc.returncode != 0 and not proc.stdout.strip():
        return {**base, "status": "crash", "exit_code": proc.returncode, "stderr_tail": proc.stderr[-500:]}
    record = parse_point_stdout(proc.stdout)
    if record is None:
        return {
            **base,
            "status": "crash",
            "exit_code": proc.returncode,
            "stderr_tail": (proc.stderr or proc.stdout)[-500:],
        }
    return record


def _sweep_lane(lane: Lane, seeds: tuple[int, ...], timeout: float, sink: Any) -> list[dict]:
    """Climb one lane's ladder, three seeds per rung, stop at first non-ok."""
    records: list[dict] = []
    for n in lane.ladder:
        rung_ok = True
        for seed in seeds:
            record = run_point_child(lane, n, seed, timeout)
            records.append(record)
            sink.write(json.dumps(record) + "\n")
            sink.flush()
            if record["status"] != "ok":
                rung_ok = False
                print(f"  {lane.problem}/{lane.backend}/{lane.solver} n={n}: {record['status']}")
                break
        if not rung_ok:
            break
        median_solve = sorted(r["stages"]["solve"] for r in records if r["n"] == n and r["status"] == "ok")[
            len(seeds) // 2
        ]
        lane_id = f"{lane.problem}/{lane.backend}/{lane.solver}"
        print(f"  {lane_id} n={n}: ok (solve {median_solve:.2f}s)")
    return records


def _ok(records: list[dict]) -> list[dict]:
    return [r for r in records if r["status"] == "ok"]


def lane_ceilings(records: list[dict]) -> dict[tuple[str, str, str], int]:
    """Max n per (problem, backend, solver) lane where every record at that
    n is ok. An n with a partially-completed rung (some seeds ok, others
    not) is excluded, so the ceiling never claims an n that didn't fully
    pass."""
    ok_ns: dict[tuple[str, str, str], set[int]] = {}
    bad_ns: dict[tuple[str, str, str], set[int]] = {}
    for r in records:
        key = (r["problem"], r["backend"], r["solver"])
        target = ok_ns if r["status"] == "ok" else bad_ns
        target.setdefault(key, set()).add(r["n"])
    ceilings: dict[tuple[str, str, str], int] = {}
    for key, ns in ok_ns.items():
        clean_ns = ns - bad_ns.get(key, set())
        if clean_ns:
            ceilings[key] = max(clean_ns)
    return ceilings


def solution_divergences(records: list[dict]) -> list[dict]:
    """Python-VM vs Rust-VM canonical-solution differences on the CPU solver.

    A divergence is not necessarily a VM defect: model term-ordering can
    differ between VMs and feed the seeded annealer differently, yielding
    distinct valid solutions (see the QUI-167 findings doc). It is a lead
    to investigate, not a parity verdict."""
    by_point: dict[tuple[str, int, int], dict[str, list[int]]] = {}
    for r in _ok(records):
        if r["solver"] != CPU_SOLVER:
            continue
        by_point.setdefault((r["problem"], r["n"], r["seed"]), {})[r["backend"]] = r["solution"]
    return [
        {"problem": problem, "n": n, "seed": seed, "python": sols["python"], "rust": sols["rust"]}
        for (problem, n, seed), sols in sorted(by_point.items())
        if len(sols) == 2 and sols["python"] != sols["rust"]
    ]


def solver_deltas(records: list[dict]) -> list[dict]:
    """GPU-minus-CPU energy per (problem, n, seed) on the Rust VM.
    Negative delta means the GPU found a lower (better) energy."""
    by_point: dict[tuple[str, int, int], dict[str, int]] = {}
    for r in _ok(records):
        if r["backend"] != "rust":
            continue
        by_point.setdefault((r["problem"], r["n"], r["seed"]), {})[r["solver"]] = r["energy"]
    return [
        {"problem": problem, "n": n, "seed": seed, "energy_delta": es[GPU_SOLVER] - es[CPU_SOLVER]}
        for (problem, n, seed), es in sorted(by_point.items())
        if CPU_SOLVER in es and GPU_SOLVER in es
    ]


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def render_report(records: list[dict]) -> str:
    """Markdown summary: ceilings, per-rung stage medians, divergences,
    deltas, failures. Pure function of the record list so it is
    unit-testable and re-runnable on any results.jsonl."""
    lines = ["# QUI-167 scale sweep report", ""]

    lines += ["## Lane ceilings (max n completed)", "", "| problem | backend | solver | max n |", "|---|---|---|---|"]
    for (problem, backend, solver), n in sorted(lane_ceilings(records).items()):
        lines.append(f"| {problem} | {backend} | {solver} | {n} |")

    lines += [
        "",
        "## Median stage seconds by rung",
        "",
        "| problem | backend | solver | n | vars | compile | encode | solve | verify | decode | peak MiB |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    rungs: dict[tuple[str, str, str, int], list[dict]] = {}
    for r in _ok(records):
        rungs.setdefault((r["problem"], r["backend"], r["solver"], r["n"]), []).append(r)
    for (problem, backend, solver, n), rs in sorted(rungs.items()):
        med = {s: _median([r["stages"][s] for r in rs]) for s in STAGES}
        rss = _median([r["peak_rss_kib"] for r in rs]) / 1024
        cells = " | ".join(f"{med[s]:.3f}" for s in STAGES)
        lines.append(f"| {problem} | {backend} | {solver} | {n} | {rs[0]['model']['vars']} | {cells} | {rss:.0f} |")

    failures = [r for r in records if r["status"] != "ok"]
    lines += ["", f"## Failures ({len(failures)})", ""]
    for r in failures:
        if r["status"] == "timeout":
            detail = f"exceeded {r.get('timeout_s')}s"
        elif r["status"] == "error":
            detail = r.get("message", "")
        else:  # crash
            stderr_tail = r.get("stderr_tail", "")
            detail = stderr_tail[-120:] if stderr_tail else f"exit code {r.get('exit_code')}, no stderr"
        lines.append(
            f"- {r['problem']}/{r['backend']}/{r['solver']} n={r['n']} seed={r['seed']}: **{r['status']}** {detail}"
        )

    diverged = solution_divergences(records)
    lines += [
        "",
        f"## Cross-VM solution divergence (python vs rust, {CPU_SOLVER}): "
        f"{'NONE' if not diverged else f'{len(diverged)} points'}",
        "",
        "_Divergence can be solver-stochastic (term-ordering feeding a seeded annealer), not necessarily a VM defect._",
        "",
    ]
    for p in diverged:
        lines.append(f"- {p['problem']} n={p['n']} seed={p['seed']}: python={p['python']} rust={p['rust']}")

    deltas = solver_deltas(records)
    if deltas:
        lines += [
            "",
            "## Energy delta: cuda-gpu minus dwave-cpu (negative = GPU better)",
            "",
            "| problem | n | seed | delta |",
            "|---|---|---|---|",
        ]
        lines += [f"| {d['problem']} | {d['n']} | {d['seed']} | {d['energy_delta']} |" for d in deltas]

    lines += [
        "",
        f"_{len(records)} records; structural check failed on "
        f"{sum(1 for r in _ok(records) if not r['structural_ok'])}; "
        f"verifier invalid on {sum(1 for r in _ok(records) if not r['valid'])}._",
        "",
    ]
    return "\n".join(lines)


def _orchestrate(args: argparse.Namespace) -> int:
    adapters = make_adapters()
    solvers = [CPU_SOLVER]
    if cuda_available():
        solvers.append(GPU_SOLVER)
    else:
        print("skip cuda-gpu lanes: no cupy / CUDA device")
    lanes = build_matrix(adapters, solvers, args.smoke)
    seeds = SEEDS[:1] if args.smoke else SEEDS

    args.out.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    with args.out.open("w") as sink:
        for lane in lanes:
            print(f"lane {lane.problem}/{lane.backend}/{lane.solver} ladder={list(lane.ladder)}")
            records.extend(_sweep_lane(lane, seeds, args.timeout, sink))

    report_path = args.report or args.out.with_name("report.md")
    report_path.write_text(render_report(records))
    print(f"\n{len(records)} records -> {args.out}\nreport -> {report_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.point:
        required = ("problem", "backend", "n", "seed")
        missing = [name for name in required if getattr(args, name) is None]
        if missing:
            build_parser().error(f"--point requires --{', --'.join(missing)}")
        return _point_main(args)
    return _orchestrate(args)


if __name__ == "__main__":
    sys.exit(main())
