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

from xqvm_py.cli.run import main

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
