#!/usr/bin/env python3
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later
"""QUI-852 occupancy benchmark: trajectory gate, parity stats, and timing.

Captures reference outputs of ``SolverCudaGPU`` so a kernel change can be
verified against the shipped behaviour:

* ``trajectories`` -- integer-coefficient instances (h=0, J in {0,1}). All
  local-field sums are integer-valued, hence exact in float64 in ANY
  summation order, so a reduction-order change must reproduce these results
  bit-for-bit. Run with ``num_reads=1`` so the best read IS the trajectory.
* ``parity`` -- gaussian-coefficient instances, where summation order may
  legitimately flip Metropolis decisions at the float boundary. Compared
  distributionally (mean/std of best energies across seeds).
* ``timing`` -- wall-clock of representative solves (n=512 MaxCut-style,
  n=4096 gaussian), for the before/after speedup measurement.

Usage:
    uv run --extra cuda python scripts/bench_occupancy.py trajectories --out FILE
    uv run --extra cuda python scripts/bench_occupancy.py parity --out FILE
    uv run --extra cuda python scripts/bench_occupancy.py timing --out FILE
    uv run --extra cuda python scripts/bench_occupancy.py compare --mode trajectories BASE CUR
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np

from _scriptio import SetupError, format_setup_error, read_json, require_key, require_mapping
from xqvm_py.xqmx import XQMX

REPO_ROOT = Path(__file__).resolve().parent.parent

TRAJ_SIZES = (64, 256)
TRAJ_SEEDS = tuple(range(30))
TRAJ_SWEEPS = 400
PARITY_SIZES = (256, 1024)
PARITY_SEEDS = tuple(range(10))
PARITY_READS = 64
PARITY_SWEEPS = 1000
TIMING_CASES = (
    {"kind": "maxcut", "n": 512, "num_reads": 200, "num_sweeps": 2000, "seed": 42},
    {"kind": "gaussian", "n": 4096, "num_reads": 200, "num_sweeps": 2000, "seed": 42},
)


def build_maxcut(n: int, seed: int, domain: str) -> XQMX:
    """Integer-coefficient instance: h=0, J[i,j] in {0.0, 1.0} at density 0.5."""
    rng = np.random.default_rng(seed)
    model = XQMX.spin_model(n) if domain == "spin" else XQMX.binary_model(n)
    for i in range(n):
        for j in range(i + 1, n):
            if rng.random() < 0.5:
                model.set_quadratic(i, j, 1.0)
    return model


def build_gaussian(n: int, seed: int) -> XQMX:
    """Float-coefficient spin instance: h, J ~ N(0, 1) at density 0.25."""
    rng = np.random.default_rng(seed)
    model = XQMX.spin_model(n)
    for i in range(n):
        model.set_linear(i, float(rng.normal()))
        for j in range(i + 1, n):
            if rng.random() < 0.25:
                model.set_quadratic(i, j, float(rng.normal()))
    return model


def _sample_hash(sample: XQMX, n: int) -> str:
    bits = ",".join(str(sample.get_linear(i)) for i in range(n))
    return hashlib.sha256(bits.encode()).hexdigest()


def _solve(model: XQMX, num_reads: int, num_sweeps: int, seed: int) -> tuple[dict, float]:
    from xqsa.cuda_gpu import SolverCudaGPU

    solver = SolverCudaGPU(num_reads=num_reads, num_sweeps=num_sweeps, seed=seed)
    t0 = time.perf_counter()
    result = solver.solve(model)
    elapsed = time.perf_counter() - t0
    record = {
        "energy": int(result.energy),
        "raw_energy": float(result.metadata["params"]["raw_energy"]),
        "sample_sha256": _sample_hash(result.sample, model.size),
    }
    return record, elapsed


def cmd_trajectories(out: Path) -> None:
    data: dict = {}
    for domain in ("binary", "spin"):
        for n in TRAJ_SIZES:
            for seed in TRAJ_SEEDS:
                model = build_maxcut(n, seed, domain)
                record, _ = _solve(model, num_reads=1, num_sweeps=TRAJ_SWEEPS, seed=seed)
                data[f"{domain}/n{n}/s{seed}"] = record
    out.write_text(json.dumps(data, indent=1, sort_keys=True))
    print(f"wrote {len(data)} trajectory records -> {out}")


def cmd_parity(out: Path) -> None:
    data: dict = {}
    for n in PARITY_SIZES:
        energies = []
        for seed in PARITY_SEEDS:
            model = build_gaussian(n, seed)
            record, _ = _solve(model, num_reads=PARITY_READS, num_sweeps=PARITY_SWEEPS, seed=seed)
            energies.append(record["raw_energy"])
        data[f"n{n}"] = {"best_energies": energies}
    out.write_text(json.dumps(data, indent=1, sort_keys=True))
    print(f"wrote parity records -> {out}")


def cmd_timing(out: Path) -> None:
    data: dict = {}
    for case in TIMING_CASES:
        n, seed = case["n"], case["seed"]
        model = build_maxcut(n, seed, "spin") if case["kind"] == "maxcut" else build_gaussian(n, seed)
        record, elapsed = _solve(model, case["num_reads"], case["num_sweeps"], seed)
        data[f"{case['kind']}/n{n}"] = {"seconds": elapsed, "raw_energy": record["raw_energy"]}
        print(f"{case['kind']}/n{n}: {elapsed:.1f} s")
    out.write_text(json.dumps(data, indent=1, sort_keys=True))
    print(f"wrote timing records -> {out}")


def record_field(
    data: Mapping[str, object],
    path: Path,
    key: str,
    field: str,
    expected_type: type | tuple[type, ...],
) -> Any:
    """Return `data[key][field]`, or raise a setup error naming what is wrong.

    Both `parity` and `timing` read one named field out of one per-key
    record, in either document, so the mapping check and the field check
    are worth having in one place rather than eight.
    """
    where = f"{path}: {key}"
    return require_key(require_mapping(require_key(data, path, key), where), where, field, expected_type)


def cmd_compare(mode: str, baseline: Path, current: Path) -> int:
    """Compare two run outputs; print a verdict and return a process code.

    Every read of either document goes through the `_scriptio` require
    helpers, so a stale or foreign JSON file is a setup error rather than
    a traceback out of a subscript wearing the exit code reserved for a
    real regression.
    """
    base = require_mapping(read_json(baseline), baseline)
    cur = require_mapping(read_json(current), current)
    if mode == "trajectories":
        # `cur.get` is total, and an absent key reads as a mismatch, which
        # is what a trajectory record missing from the candidate is.
        mismatches = [k for k in sorted(base) if base[k] != cur.get(k)]
        if mismatches:
            for k in mismatches[:20]:
                print(f"MISMATCH {k}: base={base[k]} cur={cur.get(k)}")
            print(f"FAIL: {len(mismatches)}/{len(base)} trajectory records differ")
            return 1
        print(f"OK: all {len(base)} trajectory records bit-identical")
        return 0
    if mode == "parity":
        for key in sorted(base):
            b = np.asarray(record_field(base, baseline, key, "best_energies", list))
            c = np.asarray(record_field(cur, current, key, "best_energies", list))
            db, dc = b.mean(), c.mean()
            rel = abs(dc - db) / max(abs(db), 1e-12)
            print(
                f"{key}: base mean={db:.2f} std={b.std():.2f} | new mean={dc:.2f} std={c.std():.2f} | dmean={rel:.2%}"
            )
        return 0
    for key in sorted(base):
        b = record_field(base, baseline, key, "seconds", (int, float))
        c = record_field(cur, current, key, "seconds", (int, float))
        print(f"{key}: base={b:.1f} s -> new={c:.1f} s ({b / max(c, 1e-9):.1f}x)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="QUI-852 occupancy benchmark")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("trajectories", "parity", "timing"):
        p = sub.add_parser(name)
        p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("compare")
    p.add_argument("--mode", choices=["trajectories", "parity", "timing"], required=True)
    p.add_argument("baseline", type=Path)
    p.add_argument("current", type=Path)
    args = parser.parse_args(argv)
    if args.cmd == "compare":
        try:
            return cmd_compare(args.mode, args.baseline, args.current)
        except (OSError, SetupError) as exc:
            sys.stderr.write(f"bench_occupancy setup error: {format_setup_error(exc, REPO_ROOT)}\n")
            return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    {"trajectories": cmd_trajectories, "parity": cmd_parity, "timing": cmd_timing}[args.cmd](args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
