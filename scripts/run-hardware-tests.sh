#!/usr/bin/env bash
# Copyright (C) 2026 Postquant Labs Incorporated
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Run the real-hardware xqsa solver tests (CUDA, D-Wave QPU, Metal)
# against an actual device, exercising the encode -> solve -> verify ->
# decode pipeline rather than the mocked unit tests in `make test-py`.
#
# Two things this script does that a plain `uv sync --extra X && pytest
# -m X` would not:
#
#   1. Preflight via scripts/_hwprobe.py's require(): a missing GPU or an
#      unset DWAVE_API_TOKEN fails the job immediately with a specific
#      reason, rather than letting the tests skip themselves quietly and
#      report a hollow green pytest run.
#   2. Treat pytest exit code 5 ("no tests collected") as a failure. A
#      marker typo in a test file, or a test suite that resolves to
#      nothing under the given marker, would otherwise produce a green
#      job that ran nothing.
#
# Usage:
#   scripts/run-hardware-tests.sh <cuda|qpu|metal>
#
# Exit codes:
#   0  -- tests ran and passed
#   1  -- hardware/token preflight failed, tests failed, or pytest
#         collected zero tests
#   2  -- usage error (no argument, or an unknown kind)

set -euo pipefail

usage() {
    echo "usage: $0 <cuda|qpu|metal>" >&2
    exit 2
}

[[ $# -eq 1 ]] || usage

kind="$1"

case "${kind}" in
    cuda) extra="cuda" marker="cuda" ;;
    qpu) extra="dwave" marker="qpu" ;;
    metal) extra="metal" marker="metal" ;;
    *) usage ;;
esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "${REPO_ROOT}"

echo ">> uv sync --extra ${extra}"
uv sync --extra "${extra}"

# Run after the sync above: the probe for `cuda`/`metal` imports the
# hardware-specific package (cupy / Metal) that only exists once the
# matching extra is installed.
echo ">> preflight: ${kind} hardware probe"
uv run --no-sync python scripts/_hwprobe.py "${kind}"

echo ">> pytest xqsa/tests/ -m ${marker} -v"
set +e
uv run --no-sync pytest xqsa/tests/ -m "${marker}" -v
status=$?
set -e

if [[ "${status}" -eq 5 ]]; then
    echo "error: pytest collected zero tests for marker '${marker}' (exit 5) -- treating as a failure" >&2
    exit 1
fi

exit "${status}"
