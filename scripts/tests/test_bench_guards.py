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

"""Behavioural tests for the `compare` setup-error path of two bench scripts.

`scripts/bench_occupancy.py compare` and `scripts/validate_rng_parity.py
compare` both load two JSON files supplied by the caller. Before QUI-1033
that load was a bare `json.loads(path.read_text())`, so a missing or
malformed input file surfaced as an uncaught Python traceback instead of a
clean exit. Each test launches the real script through `subprocess`
against a throwaway `tmp_path`, the same way
`scripts/tests/test_check_commit_messages.py` drives its shell guard, and
asserts on the exit code and the absence of a traceback.

Three shapes of bad input, not one. A file that is missing or is not JSON
at all fails at the read. A file that parses cleanly but carries the wrong
shape -- a stale run output written before a key existed, or a document
from another tool entirely -- reaches the comparison and used to traceback
out of a subscript there, reporting a setup problem with the exit code
reserved for a real regression. That last shape is the one an actual stale
baseline produces, so it is the one worth having covered.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

BENCH_OCCUPANCY_SCRIPT = Path(__file__).resolve().parents[1] / "bench_occupancy.py"
VALIDATE_RNG_PARITY_SCRIPT = Path(__file__).resolve().parents[1] / "validate_rng_parity.py"


def run_script(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke `script` with the running interpreter, capturing output.

    `sys.executable` is the interpreter pytest itself runs under, so the
    subprocess sees the same virtualenv (numpy, xqvm_py, xqsa) without any
    extra environment plumbing.
    """
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin"},
    )


def compare_args(script: Path, baseline: str, current: str) -> tuple[str, ...]:
    """Build the `compare` argv for `script`, which differ in flag shape."""
    if script is BENCH_OCCUPANCY_SCRIPT:
        return ("compare", "--mode", "timing", baseline, current)
    return ("compare", baseline, current)


SCRIPTS = (BENCH_OCCUPANCY_SCRIPT, VALIDATE_RNG_PARITY_SCRIPT)


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.stem)
def test_compare_missing_input_is_a_setup_error(script: Path, tmp_path: Path) -> None:
    baseline = tmp_path / "missing-baseline.json"
    current = tmp_path / "missing-current.json"

    result = run_script(script, *compare_args(script, str(baseline), str(current)))

    assert result.returncode == 2
    assert "cannot read" in result.stderr
    assert "missing-baseline.json" in result.stderr
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.stem)
def test_compare_malformed_input_is_a_setup_error(script: Path, tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    baseline.write_text("not valid json", encoding="utf-8")
    current = tmp_path / "current.json"
    current.write_text("{}", encoding="utf-8")

    result = run_script(script, *compare_args(script, str(baseline), str(current)))

    assert result.returncode == 2
    assert "cannot read" in result.stderr
    assert "baseline.json" in result.stderr
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout


def wrong_shape_inputs(script: Path) -> tuple[str, str, str]:
    """Return (baseline text, candidate text, the key each script must name).

    Valid JSON of the wrong shape: an empty object, which is what a run
    output written before the key existed looks like from here.
    """
    if script is BENCH_OCCUPANCY_SCRIPT:
        # `--mode timing` reads `seconds` out of each per-key record.
        return ('{"maxcut/n8": {}}', '{"maxcut/n8": {}}', "seconds")
    return ("{}", '{"params": {}, "cells": {}}', "params")


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.stem)
def test_compare_wrong_shape_input_is_a_setup_error(script: Path, tmp_path: Path) -> None:
    """Valid JSON that carries the wrong shape exits 2, not 1.

    The read succeeded, so `read_json` has nothing to say; the defect only
    surfaces where the comparison reaches for a key. Exiting 1 there would
    report a stale baseline as a parity or performance regression.
    """
    baseline_text, current_text, missing_key = wrong_shape_inputs(script)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(baseline_text, encoding="utf-8")
    current = tmp_path / "current.json"
    current.write_text(current_text, encoding="utf-8")

    result = run_script(script, *compare_args(script, str(baseline), str(current)))

    assert result.returncode == 2, result.stdout + result.stderr
    assert missing_key in result.stderr
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.stem)
def test_compare_non_mapping_input_is_a_setup_error(script: Path, tmp_path: Path) -> None:
    """A JSON array parses, and is not a run output either.

    Both scripts iterate the baseline as a mapping of record keys. Given a
    list they subscript it with a string, which is a TypeError rather than
    a missing key -- a different exception on the same path, so it gets its
    own case.
    """
    baseline = tmp_path / "baseline.json"
    baseline.write_text('["not", "a", "run", "output"]', encoding="utf-8")
    current = tmp_path / "current.json"
    current.write_text('["not", "a", "run", "output"]', encoding="utf-8")

    result = run_script(script, *compare_args(script, str(baseline), str(current)))

    assert result.returncode == 2, result.stdout + result.stderr
    assert "expected mapping, got list" in result.stderr
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout
