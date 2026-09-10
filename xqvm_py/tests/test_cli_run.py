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

"""Tests for the ``python -m xqvm_py run`` subcommand.

The conformance harness consumes this CLI's stdout, so a faulting program
has to report the fault's identity as JSON rather than as a traceback.
"""

import json

import pytest

from xqvm_py.cli.run import main
from xqvm_py.executor import DEFAULT_MEMORY_LIMIT, DEFAULT_STEP_LIMIT

DIVIDE_BY_ZERO = """
PUSH 1
PUSH 0
DIV
HALT
"""

INFINITE_LOOP = """
TARGET .0
NOP
JUMP .0
"""

SUM = """
PUSH 3
PUSH 4
ADD
STOW r0
PUSH 0
OUTPUT r0
HALT
"""


def _run(tmp_path, source, extra_args=None):
    program = tmp_path / "program.xqasm"
    program.write_text(source, encoding="utf-8")
    argv = ["run", str(program), *(extra_args or [])]
    return main(argv)


class TestRunReporting:
    def test_successful_run_reports_outputs(self, tmp_path, capsys):
        code = _run(tmp_path, SUM)
        payload = json.loads(capsys.readouterr().out)

        assert code == 0
        assert payload["outputs"][0] == 7
        assert "error" not in payload

    def test_vm_fault_reports_the_exception_class(self, tmp_path, capsys):
        code = _run(tmp_path, DIVIDE_BY_ZERO)
        payload = json.loads(capsys.readouterr().out)

        assert code != 0
        assert payload["error"]["type"] == "DivisionByZero"
        assert payload["error"]["message"]
        assert "outputs" not in payload

    def test_step_limit_flag_bounds_execution(self, tmp_path, capsys):
        code = _run(tmp_path, INFINITE_LOOP, ["--step-limit", "50"])
        payload = json.loads(capsys.readouterr().out)

        assert code != 0
        assert payload["error"]["type"] == "StepLimitExceeded"

    def test_the_default_budget_bounds_a_runaway_program(self, tmp_path, capsys):
        """No flag at all still stops. The default used to be unbounded.

        `xqvm_py` was the last host without a bounded default: the Rust VM,
        `xqcli run` and `xquad` all installed `DEFAULT_STEP_LIMIT`, so this
        program raised on those in a fraction of a second and hung here
        until the caller killed it.
        """
        code = _run(tmp_path, INFINITE_LOOP, [])
        payload = json.loads(capsys.readouterr().out)

        assert code != 0
        assert payload["error"]["type"] == "StepLimitExceeded"
        assert str(DEFAULT_STEP_LIMIT) in payload["error"]["message"]

    def test_unlimited_steps_conflicts_with_an_explicit_limit(self, tmp_path):
        """Opting out of the bound is said, not defaulted into.

        Mirrors `xqcli run`, where the two flags conflict for the same
        reason.
        """
        with pytest.raises(SystemExit):
            _run(tmp_path, INFINITE_LOOP, ["--unlimited-steps", "--step-limit", "5"])

    def test_the_default_matches_the_rust_vm(self):
        """One constant, four hosts.

        `xqvm::DEFAULT_STEP_LIMIT` is public and re-exported through
        `xqffi.vm`, so `xquad` reads it rather than restating it. This VM
        cannot import it without taking an `xqffi` dependency -- the
        executor stays pure Python so it remains an independent conformance
        oracle -- so the equality is asserted here instead.
        """
        from xqffi.vm import DEFAULT_STEP_LIMIT as RUST_DEFAULT

        assert DEFAULT_STEP_LIMIT == RUST_DEFAULT

    def test_the_memory_default_matches_the_rust_vm(self):
        """The same equality for the allocation budget.

        `--memory-limit` defaults to `DEFAULT_MEMORY_LIMIT`, which restates
        `xqvm::DEFAULT_MEMORY_LIMIT` in Python. Without this assertion,
        moving the Rust constant would move `xquad run` and leave
        `xqvm_py run` on the old budget with nothing failing -- the drift
        that made the Rust constant public in the first place.
        """
        from xqffi.vm import DEFAULT_MEMORY_LIMIT as RUST_DEFAULT

        assert DEFAULT_MEMORY_LIMIT == RUST_DEFAULT
