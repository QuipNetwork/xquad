# QUI-167 Scale Sweep Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `scripts/scale_sweep.py`, a two-mode harness that runs the shipped MaxCut/TSP example encodings at growing sizes across VM backends and SA solvers, recording per-stage timing, memory, correctness, and parity — then run it and report findings to QUI-167.

**Architecture:** One self-contained script. Orchestrator mode builds a pruned (problem × backend × solver × n × seed) matrix and spawns itself in point mode as a child process per point (clean peak-RSS, crash isolation). Point mode re-runs the example's exact compile → encode → solve → verify → decode sequence with per-stage timers. Pure analysis/report helpers get a small pytest file.

**Tech Stack:** Python 3.13 (uv-managed venv), `xquad` workspace packages (`xquad.cp/.sa/.vm/.types`), `importlib` loading of `examples/*/runner.py`, `subprocess`, `resource`. No new dependencies.

**Spec:** `docs/design/specs/2026-07-13-qui-167-scale-sweep-design.md` (approved 2026-07-13).

## Global Constraints

- Branch `feature/qui-167` (worktree at `.claude/worktrees/feature+qui-167`); never touch `release/v0.3.0`.
- Run everything with `uv run --no-sync` (plain `uv run` reverts the maturin-built `xqffi`).
- Solver names are exactly `dwave-cpu` and `cuda-gpu` (keys of `xquad.sa.SOLVERS`).
- CPU lanes: both VM backends. GPU lanes: Rust VM only.
- Ladders — CPU: MaxCut `8,16,32,64,128,256,512`, TSP `4,6,8,12,16,24,32,48`. GPU adds MaxCut `1024`, TSP `64`.
- Seeds `42,43,44`; per-point timeout default 300 s; a lane stops escalating at the first non-ok point.
- The orchestrator never aborts the sweep on a lane failure; every outcome becomes a JSONL record.
- ≤100 lines/function, ≤5 positional params, 100-char lines, absolute imports, ruff clean (`make lint-python` equivalent: `uv run --no-sync ruff check scripts/`).
- Commit after each task (imperative, ≤72-char subject, no co-author bylines).

## File Structure

- Create `scripts/scale_sweep.py` — the harness (adapters, point mode, orchestrator, analysis, report, CLI).
- Create `scripts/tests/test_scale_sweep.py` — pytest for the pure helpers (matrix building, structural checks, analysis, report rendering). Measurement plumbing (subprocess, GPU) is verified by running the harness, not mocked.

---

### Task 1: Adapter registry and example-module loader

**Files:**
- Create: `scripts/scale_sweep.py`
- Create: `scripts/tests/test_scale_sweep.py`

**Interfaces:**
- Produces: `load_runner(example: str) -> ModuleType`; `Adapter` frozen dataclass with fields `name, ladder, gpu_ladder, runner_module, build_calldata, structurally_valid, objective, canonicalize, model_vars, approx_quad_terms`; `make_adapters() -> dict[str, Adapter]` with keys `"maxcut"`, `"tsp"`.

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_scale_sweep.py`:

```python
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the pure helpers in scripts/scale_sweep.py."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest scripts/tests/test_scale_sweep.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scale_sweep'`

- [ ] **Step 3: Write the implementation**

Create `scripts/scale_sweep.py`:

```python
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
    uv run --no-sync python scripts/scale_sweep.py --out scratch/sweep/results.jsonl
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent

CPU_SOLVER = "dwave-cpu"
GPU_SOLVER = "cuda-gpu"
SEEDS = (42, 43, 44)


def load_runner(example: str) -> ModuleType:
    """Import ``examples/<example>/runner.py`` as a module, byte-identical
    to what ships — the sweep must exercise the real encodings."""
    path = REPO_ROOT / "examples" / example / "runner.py"
    spec = importlib.util.spec_from_file_location(f"scale_sweep_{example}", path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
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
    """Registry of swept problems. Adding a problem is one entry here."""

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
```

Note: `maxcut_objective` re-implements the two-line `cut_weight` instead of
loading the runner module (an import of the full xquad stack) inside a hot
helper; `tsp_objective` must delegate because `tour_distance` depends on
`triu` indexing. Both are exercised against runner goldens in Task 2's
verification.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync pytest scripts/tests/test_scale_sweep.py -v`
Expected: 4 passed

- [ ] **Step 5: Ruff check and commit**

```bash
uv run --no-sync ruff check scripts/scale_sweep.py scripts/tests/test_scale_sweep.py
git add scripts/scale_sweep.py scripts/tests/test_scale_sweep.py
git commit -m "feat(scripts): add scale sweep adapter registry (QUI-167)"
```

---

### Task 2: Point mode — measure one (problem, backend, solver, n, seed)

**Files:**
- Modify: `scripts/scale_sweep.py` (append; also extend imports)

**Interfaces:**
- Consumes: `Adapter`, `make_adapters()`, `load_runner()` from Task 1.
- Produces: `measure_point(adapter: Adapter, backend_name: str, solver_name: str, n: int, seed: int) -> dict` returning a record with keys `schema, problem, backend, solver, n, seed, status` and, when `status == "ok"`: `stages` (dict of 5 stage-seconds), `model` (`{"vars": int, "approx_quad_terms": int}`), `energy: int`, `valid: int`, `structural_ok: bool`, `objective: int`, `solution: list[int]`, `peak_rss_kib: int`. When a stage raises: `status="error"`, `stage`, `message`. Also `main(argv) -> int` handling `--point` CLI.

- [ ] **Step 1: Write the implementation**

Append to `scripts/scale_sweep.py` (add `import argparse, json, resource, sys, time` to the imports block):

```python
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


def measure_point(adapter: Adapter, backend_name: str, solver_name: str, n: int, seed: int) -> dict:
    """Run one full pipeline pass, timing each stage. Never raises: stage
    failures come back as ``status: error`` records so the orchestrator can
    log them and stop the lane."""
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
    except Exception as exc:  # noqa: BLE001 - boundary: report, don't crash
        record.update(status="error", stage=stage, message=f"{type(exc).__name__}: {exc}")
        return record

    solution = getattr(runner, adapter.canonicalize_attr)(_vec_to_list(decoded, n))
    record.update(
        stages=stages,
        model={"vars": adapter.model_vars(n), "approx_quad_terms": adapter.approx_quad_terms(n)},
        energy=int(energy),
        valid=int(valid),
        structural_ok=adapter.structurally_valid(solution, n),
        objective=adapter.objective(solution, aux, n),
        solution=solution,
        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    )
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
    parser.add_argument("--problem", choices=("maxcut", "tsp"))
    parser.add_argument("--backend", choices=("python", "rust"))
    parser.add_argument("--solver", default=CPU_SOLVER)
    parser.add_argument("--n", type=int)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--timeout", type=float, default=300.0, help="per-point timeout (s)")
    parser.add_argument("--smoke", action="store_true", help="tiny ladder, one seed")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "scratch" / "scale_sweep" / "results.jsonl")
    parser.add_argument("--report", type=Path, default=None, help="markdown summary (default: alongside --out)")
    return parser


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
```

Add a temporary stub so point mode is runnable before Task 3 (replaced there):

```python
def _orchestrate(args: argparse.Namespace) -> int:
    raise NotImplementedError("orchestrator lands in Task 3")
```

- [ ] **Step 2: Verify point mode against the runner goldens**

```bash
uv run --no-sync python scripts/scale_sweep.py --point --problem maxcut --backend rust --n 8 --seed 42 | python3 -m json.tool
uv run --no-sync python examples/maxcut/runner.py --n 8 --seed 42 --interpreter rust
```
Expected: point record has `status: "ok"`, `valid: 1`, `structural_ok: true`, five positive stage timings, `peak_rss_kib > 0`, and its `energy`/`objective`/`solution` equal the runner's `energy`/`cut_weight`/`partition` for the same n and seed. Repeat for TSP:

```bash
uv run --no-sync python scripts/scale_sweep.py --point --problem tsp --backend python --n 4 --seed 42 | python3 -m json.tool
uv run --no-sync python examples/tsp/runner.py --n 4 --seed 42 --interpreter python
```
Expected: `tour`/`tour_distance` match `solution`/`objective`.

- [ ] **Step 3: Verify the error path**

```bash
uv run --no-sync python scripts/scale_sweep.py --point --problem maxcut --backend rust --solver dwave-qpu --n 8 --seed 42; echo "exit=$?"
```
Expected: JSON with `status: "error"`, `stage: "solve"`, non-empty `message`; `exit=1`.

- [ ] **Step 4: Ruff check and commit**

```bash
uv run --no-sync ruff check scripts/scale_sweep.py
git add scripts/scale_sweep.py
git commit -m "feat(scripts): add scale sweep point measurement mode (QUI-167)"
```

---

### Task 3: Orchestrator — matrix, child processes, lane escalation

**Files:**
- Modify: `scripts/scale_sweep.py` (replace the `_orchestrate` stub; extend imports with `import os, subprocess`)

**Interfaces:**
- Consumes: `make_adapters()`, `build_parser()`, point-mode CLI from Task 2; `CPU_SOLVER`, `GPU_SOLVER`, `SEEDS` from Task 1.
- Produces: `cuda_available() -> bool`; `build_matrix(adapters: dict[str, Adapter], solvers: list[str], smoke: bool) -> list[Lane]` where `Lane` is a dataclass `(problem: str, backend: str, solver: str, ladder: tuple[int, ...])`; `run_point_child(lane: Lane, n: int, seed: int, timeout: float) -> dict`; `_orchestrate(args) -> int` writing JSONL to `args.out` and a report via Task 4's `render_report(records) -> str`.

- [ ] **Step 1: Write the failing test**

Append to `scripts/tests/test_scale_sweep.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest scripts/tests/test_scale_sweep.py -v -k matrix`
Expected: 3 FAIL with `AttributeError: module 'scale_sweep' has no attribute 'build_matrix'`

- [ ] **Step 3: Write the implementation**

Replace the Task 2 `_orchestrate` stub with:

```python
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
    affects only the solve stage — VM parity is covered by the CPU lanes)."""
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


def run_point_child(lane: Lane, n: int, seed: int, timeout: float) -> dict:
    """Execute one point in a child process. Timeouts and crashes become
    records, never exceptions — a lane ceiling is a finding, not an error."""
    cmd = [
        sys.executable, str(Path(__file__).resolve()), "--point",
        "--problem", lane.problem, "--backend", lane.backend,
        "--solver", lane.solver, "--n", str(n), "--seed", str(seed),
    ]
    base = {
        "schema": 1, "problem": lane.problem, "backend": lane.backend,
        "solver": lane.solver, "n": n, "seed": seed,
    }
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=REPO_ROOT, env=os.environ
        )
    except subprocess.TimeoutExpired:
        return {**base, "status": "timeout", "timeout_s": timeout}
    if proc.returncode != 0 and not proc.stdout.strip():
        return {**base, "status": "crash", "exit_code": proc.returncode,
                "stderr_tail": proc.stderr[-500:]}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {**base, "status": "crash", "exit_code": proc.returncode,
                "stderr_tail": (proc.stderr or proc.stdout)[-500:]}


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
        median_solve = sorted(
            r["stages"]["solve"] for r in records if r["n"] == n and r["status"] == "ok"
        )[len(seeds) // 2]
        print(f"  {lane.problem}/{lane.backend}/{lane.solver} n={n}: ok (solve {median_solve:.2f}s)")
    return records


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
```

Task 4 defines `render_report`; until then add this stub right above `_orchestrate` (replaced in Task 4):

```python
def render_report(records: list[dict]) -> str:
    return f"# Scale sweep\n\n{len(records)} records (report lands in Task 4)\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-sync pytest scripts/tests/test_scale_sweep.py -v`
Expected: 7 passed

- [ ] **Step 5: Verify end-to-end with the smoke sweep**

Run: `uv run --no-sync python scripts/scale_sweep.py --smoke --out scratch/scale_sweep/smoke.jsonl`
Expected: 6 lanes print (4 CPU + 2 GPU on this machine), every point `ok`; `scratch/scale_sweep/smoke.jsonl` holds 6 records with `status: "ok"`. Spot-check one:
`python3 -c "import json; rs=[json.loads(l) for l in open('scratch/scale_sweep/smoke.jsonl')]; print(len(rs), all(r['status']=='ok' for r in rs))"`

- [ ] **Step 6: Ruff check and commit**

```bash
uv run --no-sync ruff check scripts/
git add scripts/scale_sweep.py scripts/tests/test_scale_sweep.py
git commit -m "feat(scripts): add scale sweep orchestrator with lane escalation (QUI-167)"
```

---

### Task 4: Analysis and markdown report

**Files:**
- Modify: `scripts/scale_sweep.py` (replace `render_report` stub; add helpers)
- Modify: `scripts/tests/test_scale_sweep.py` (append tests)

**Interfaces:**
- Consumes: record dicts as produced by Tasks 2–3 (`status`, `stages`, `energy`, `solution`, `peak_rss_kib`, ...).
- Produces: `lane_ceilings(records) -> dict[tuple[str, str, str], int]` (max ok n per lane); `parity_failures(records) -> list[dict]` (CPU-solver python-vs-rust solution mismatches, keyed by problem/n/seed); `solver_deltas(records) -> list[dict]` (rust-VM cuda-vs-cpu energy deltas per problem/n/seed); `render_report(records: list[dict]) -> str` (markdown).

- [ ] **Step 1: Write the failing tests**

Append to `scripts/tests/test_scale_sweep.py`:

```python
def _rec(problem="maxcut", backend="rust", solver="dwave-cpu", n=8, seed=42, status="ok",
         energy=-10, solution=None, solve_s=0.5):
    rec = {"schema": 1, "problem": problem, "backend": backend, "solver": solver,
           "n": n, "seed": seed, "status": status}
    if status == "ok":
        rec.update(stages={"compile": 0.1, "encode": 0.1, "solve": solve_s,
                           "verify": 0.1, "decode": 0.1},
                   model={"vars": n, "approx_quad_terms": n * (n - 1) // 2},
                   energy=energy, valid=1, structural_ok=True, objective=-energy,
                   solution=solution or [0, 1] * (n // 2), peak_rss_kib=1000)
    return rec


def test_lane_ceilings_report_max_ok_n():
    records = [_rec(n=8), _rec(n=16), _rec(n=32, status="timeout")]
    ceilings = scale_sweep.lane_ceilings(records)
    assert ceilings[("maxcut", "rust", "dwave-cpu")] == 16


def test_parity_failures_detect_solution_mismatch():
    match = [_rec(backend="python"), _rec(backend="rust")]
    assert scale_sweep.parity_failures(match) == []
    mismatch = [_rec(backend="python", solution=[0, 0, 0, 0, 1, 1, 1, 1]),
                _rec(backend="rust", solution=[0, 1, 0, 1, 0, 1, 0, 1])]
    failures = scale_sweep.parity_failures(mismatch)
    assert len(failures) == 1 and failures[0]["n"] == 8 and failures[0]["seed"] == 42


def test_solver_deltas_pair_gpu_against_cpu():
    records = [_rec(solver="dwave-cpu", energy=-10), _rec(solver="cuda-gpu", energy=-12)]
    deltas = scale_sweep.solver_deltas(records)
    assert len(deltas) == 1
    assert deltas[0]["energy_delta"] == -2  # GPU found a lower (better) energy


def test_render_report_mentions_ceilings_and_failures():
    records = [_rec(n=8), _rec(n=16, status="crash")]
    report = scale_sweep.render_report(records)
    assert "maxcut" in report and "16" in report and "crash" in report
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-sync pytest scripts/tests/test_scale_sweep.py -v -k "ceilings or parity or deltas or report"`
Expected: 4 FAIL with `AttributeError` (`lane_ceilings` undefined)

- [ ] **Step 3: Write the implementation**

Replace the `render_report` stub with:

```python
def _ok(records: list[dict]) -> list[dict]:
    return [r for r in records if r["status"] == "ok"]


def lane_ceilings(records: list[dict]) -> dict[tuple[str, str, str], int]:
    """Max n that completed ok, per (problem, backend, solver) lane."""
    ceilings: dict[tuple[str, str, str], int] = {}
    for r in _ok(records):
        key = (r["problem"], r["backend"], r["solver"])
        ceilings[key] = max(ceilings.get(key, 0), r["n"])
    return ceilings


def parity_failures(records: list[dict]) -> list[dict]:
    """Python-VM vs Rust-VM canonical-solution mismatches on the CPU solver."""
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
        {"problem": problem, "n": n, "seed": seed,
         "energy_delta": es[GPU_SOLVER] - es[CPU_SOLVER]}
        for (problem, n, seed), es in sorted(by_point.items())
        if CPU_SOLVER in es and GPU_SOLVER in es
    ]


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def render_report(records: list[dict]) -> str:
    """Markdown summary: ceilings, per-rung stage medians, parity, deltas,
    failures. Pure function of the record list so it is unit-testable and
    re-runnable on any results.jsonl."""
    lines = ["# QUI-167 scale sweep report", ""]

    lines += ["## Lane ceilings (max n completed)", "",
              "| problem | backend | solver | max n |", "|---|---|---|---|"]
    for (problem, backend, solver), n in sorted(lane_ceilings(records).items()):
        lines.append(f"| {problem} | {backend} | {solver} | {n} |")

    lines += ["", "## Median stage seconds by rung", "",
              "| problem | backend | solver | n | vars | compile | encode | solve | verify | decode | peak MiB |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    rungs: dict[tuple[str, str, str, int], list[dict]] = {}
    for r in _ok(records):
        rungs.setdefault((r["problem"], r["backend"], r["solver"], r["n"]), []).append(r)
    for (problem, backend, solver, n), rs in sorted(rungs.items()):
        med = {s: _median([r["stages"][s] for r in rs]) for s in STAGES}
        rss = _median([r["peak_rss_kib"] for r in rs]) / 1024
        cells = " | ".join(f"{med[s]:.3f}" for s in STAGES)
        lines.append(f"| {problem} | {backend} | {solver} | {n} | {rs[0]['model']['vars']} "
                     f"| {cells} | {rss:.0f} |")

    failures = [r for r in records if r["status"] != "ok"]
    lines += ["", f"## Failures ({len(failures)})", ""]
    for r in failures:
        detail = r.get("message") or r.get("stderr_tail", "")[-120:] or f"timeout {r.get('timeout_s')}s"
        lines.append(f"- {r['problem']}/{r['backend']}/{r['solver']} n={r['n']} seed={r['seed']}: "
                     f"**{r['status']}** {detail}")

    parity = parity_failures(records)
    lines += ["", f"## VM parity (python vs rust, {CPU_SOLVER}): "
              f"{'OK' if not parity else f'{len(parity)} MISMATCHES'}", ""]
    for p in parity:
        lines.append(f"- {p['problem']} n={p['n']} seed={p['seed']}: python={p['python']} "
                     f"rust={p['rust']}")

    deltas = solver_deltas(records)
    if deltas:
        lines += ["", "## Energy delta: cuda-gpu minus dwave-cpu (negative = GPU better)", "",
                  "| problem | n | seed | delta |", "|---|---|---|---|"]
        lines += [f"| {d['problem']} | {d['n']} | {d['seed']} | {d['energy_delta']} |" for d in deltas]

    lines += ["", f"_{len(records)} records; structural check failed on "
              f"{sum(1 for r in _ok(records) if not r['structural_ok'])}; "
              f"verifier invalid on {sum(1 for r in _ok(records) if not r['valid'])}._", ""]
    return "\n".join(lines)
```

- [ ] **Step 4: Run all tests**

Run: `uv run --no-sync pytest scripts/tests/test_scale_sweep.py -v`
Expected: 11 passed

- [ ] **Step 5: Re-run the smoke sweep and read the real report**

Run: `uv run --no-sync python scripts/scale_sweep.py --smoke --out scratch/scale_sweep/smoke.jsonl`
Expected: `scratch/scale_sweep/report.md` renders all sections; parity section says OK; energy-delta table appears (GPU lane present on this machine).

- [ ] **Step 6: Ruff check and commit**

```bash
uv run --no-sync ruff check scripts/
git add scripts/scale_sweep.py scripts/tests/test_scale_sweep.py
git commit -m "feat(scripts): add scale sweep analysis and markdown report (QUI-167)"
```

---

### Task 5: Full sweep run and findings

**Files:**
- Create: `scratch/scale_sweep/results.jsonl` (run output; scratch/ is not committed)
- Create: `docs/design/specs/2026-07-13-qui-167-findings.md` (committed findings summary)

**Interfaces:**
- Consumes: the complete harness from Tasks 1–4.

- [ ] **Step 1: Sanity-check runtime bounds, then launch the full sweep**

```bash
uv run --no-sync python scripts/scale_sweep.py --timeout 300 --out scratch/scale_sweep/results.jsonl
```
Run in the background (it may take an hour+ if CPU lanes climb high). Expected: 6 lanes, each climbing until timeout/failure/ladder-end; JSONL grows incrementally, so progress is checkable mid-run.

- [ ] **Step 2: Review the report for anomalies**

Read `scratch/scale_sweep/report.md`. Check specifically: (a) parity section OK — any mismatch is a QUI-465-class bug and blocks findings; (b) verifier-invalid count 0 at small n (invalid TSP solutions at large n are expected annealer behavior, worth noting, not a harness bug); (c) ceilings are timeout-shaped, not crash-shaped — crashes need a follow-up ticket.

- [ ] **Step 3: Write the findings doc**

Create `docs/design/specs/2026-07-13-qui-167-findings.md` containing: the lane-ceilings table, the largest-common-rung stage breakdown, the parity verdict, the GPU-vs-CPU energy/throughput comparison, and a short "implications for v0.3.1+" section (candidate follow-ups: bit-packing QUI-704 relevance, encoder hot spots). Copy tables from the generated report; do not hand-edit numbers.

- [ ] **Step 4: Commit**

```bash
git add docs/design/specs/2026-07-13-qui-167-findings.md
git commit -m "docs: record QUI-167 scale sweep findings"
```

- [ ] **Step 5: Close the loop on Linear**

Post a comment on QUI-167 with the findings summary (ceilings table + parity verdict + GPU delta headline), tick the ticket's MaxCut and TSP checkboxes, and move the issue to In Review after the MR is opened (finishing-a-development-branch skill handles the MR).

---

## Self-Review

- **Spec coverage:** adapters/registry (Task 1), five instrumented stages + structural/objective/valid checks + peak RSS (Task 2), matrix pruning + child isolation + timeout/crash/error records + lane stop + JSONL (Task 3), parity + cross-solver deltas + ceilings + markdown report (Task 4), smoke flag (Tasks 3/5 via `--smoke`), findings + Linear closeout (Task 5). GPU env setup already done in-session and documented in the spec.
- **Placeholder scan:** the two intentional stubs (`_orchestrate` in Task 2, `render_report` in Task 3) are each replaced by a named later task — acceptable forward references, both explicit.
- **Type consistency:** record schema keys used by Task 4 match Task 2/3 producers (`stages`, `solution`, `energy`, `peak_rss_kib`, `status`, `stderr_tail`, `timeout_s`); `Lane` fields match `build_matrix` tests; `STAGES` tuple shared.
