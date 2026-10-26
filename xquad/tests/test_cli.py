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

"""`xquad` command parity with the retired `xqcli` binary, through click's `CliRunner`.

Every case runs under the deprecated top-level names (`asm`, `dism`,
`run`, `verify`), which keep `xqcli`'s spelling, and again under
`xquad vm ...`. The Rust tracer writes to fd 2, which `CliRunner` cannot
see, so the trace cases read it through `capfd`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from xquad.cli import main

# Push integer 42 and halt. Leaves 42 on the stack.
PUSH_42 = "PUSH 42\nHALT\n"
# Read calldata slot 0 into r0, push it, halt. Stack = [calldata[0]].
CALLDATA_R0 = "PUSH 0\nINPUT r0\nLOAD r0\nHALT\n"
# Loop 1 000 000 times: always exceeds a single-digit step limit.
LOOP_1M = "PUSH 0\nPUSH 1000000\nRANGE\n  LVAL r0\nNEXT\nHALT\n"
# Source that is not valid XQASM.
INVALID_SRC = "NOTANOPCODE\n"

OLD = {"asm": ["asm"], "dis": ["dism"], "run": ["run"], "check": ["verify"]}
NEW = {"asm": ["vm", "asm"], "dis": ["vm", "dis"], "run": ["vm", "run"], "check": ["vm", "check"]}


@pytest.fixture(params=["old", "vm"])
def cmd(request: pytest.FixtureRequest) -> dict[str, list[str]]:
    return OLD if request.param == "old" else NEW


def invoke(args: list[str], stdin: bytes | None = None) -> Result:
    return CliRunner().invoke(main, args, input=stdin)


def write(tmp_path: Path, name: str, content: str | bytes) -> Path:
    path = tmp_path / name
    if isinstance(content, str):
        path.write_text(content)
    else:
        path.write_bytes(content)
    return path


def assemble_bytes(tmp_path: Path, src: str) -> bytes:
    result = invoke(["vm", "asm", "--stdout", str(write(tmp_path, "prog.xqasm", src))])
    assert result.exit_code == 0, result.output
    return result.stdout_bytes


# -- asm --


def test_asm_writes_default_xqb_file(tmp_path: Path, cmd: dict[str, list[str]]) -> None:
    result = invoke([*cmd["asm"], str(write(tmp_path, "prog.xqasm", PUSH_42))])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "prog.xqb").exists()
    assert "assembled 2 instructions" in result.stderr
    assert f"-> {tmp_path / 'prog.xqb'}" in result.stderr


def test_asm_stdout_flag_emits_nonempty_bytes(tmp_path: Path, cmd: dict[str, list[str]]) -> None:
    result = invoke([*cmd["asm"], "--stdout", str(write(tmp_path, "prog.xqasm", PUSH_42))])
    assert result.exit_code == 0, result.output
    assert result.stdout_bytes


def test_asm_custom_output_path(tmp_path: Path, cmd: dict[str, list[str]]) -> None:
    out = tmp_path / "custom.xqb"
    result = invoke([*cmd["asm"], "-o", str(out), str(write(tmp_path, "prog.xqasm", PUSH_42))])
    assert result.exit_code == 0, result.output
    assert out.exists()


def test_asm_invalid_source_exits_nonzero(tmp_path: Path, cmd: dict[str, list[str]]) -> None:
    result = invoke([*cmd["asm"], str(write(tmp_path, "bad.xqasm", INVALID_SRC))])
    assert result.exit_code == 1
    assert "assembly failed" in result.stderr


def test_asm_output_and_stdout_conflict(tmp_path: Path, cmd: dict[str, list[str]]) -> None:
    src = write(tmp_path, "prog.xqasm", PUSH_42)
    result = invoke([*cmd["asm"], "-o", str(tmp_path / "x.xqb"), "--stdout", str(src)])
    assert result.exit_code == 2


def test_missing_file_exits_one(tmp_path: Path, cmd: dict[str, list[str]]) -> None:
    result = invoke([*cmd["run"], str(tmp_path / "missing.xqb")])
    assert result.exit_code == 1
    assert "failed to read" in result.stderr


# -- dis --


def test_dis_file_listing_contains_push(tmp_path: Path, cmd: dict[str, list[str]]) -> None:
    xqb = write(tmp_path, "prog.xqb", assemble_bytes(tmp_path, PUSH_42))
    result = invoke([*cmd["dis"], str(xqb)])
    assert result.exit_code == 0, result.output
    assert "PUSH" in result.stdout


def test_dis_reads_stdin_when_no_file(tmp_path: Path, cmd: dict[str, list[str]]) -> None:
    result = invoke(cmd["dis"], stdin=assemble_bytes(tmp_path, PUSH_42))
    assert result.exit_code == 0, result.output
    assert "PUSH" in result.stdout


# -- run --


def test_run_binary_file_prints_stack(tmp_path: Path, cmd: dict[str, list[str]]) -> None:
    xqb = write(tmp_path, "prog.xqb", assemble_bytes(tmp_path, PUSH_42))
    result = invoke([*cmd["run"], str(xqb)])
    assert result.exit_code == 0, result.output
    assert result.stdout == "stack (bottom to top):\n  42\n"


def test_run_text_flag_assembles_before_running(tmp_path: Path, cmd: dict[str, list[str]]) -> None:
    result = invoke([*cmd["run"], "--text", str(write(tmp_path, "prog.xqasm", PUSH_42))])
    assert result.exit_code == 0, result.output
    assert "42" in result.stdout


def test_run_calldata_reaches_input_instruction(tmp_path: Path, cmd: dict[str, list[str]]) -> None:
    src = write(tmp_path, "prog.xqasm", CALLDATA_R0)
    result = invoke([*cmd["run"], "--text", "--calldata", "99", str(src)])
    assert result.exit_code == 0, result.output
    assert "99" in result.stdout


def test_run_repeated_calldata_appends(tmp_path: Path, cmd: dict[str, list[str]]) -> None:
    # clap's `Vec<i64>` appended across repeats, so `--calldata 7 --calldata 9` is `[7, 9]`.
    src = write(tmp_path, "prog.xqasm", "PUSH 0\nINPUT r0\nPUSH 1\nINPUT r1\nLOAD r0\nLOAD r1\nHALT\n")
    result = invoke([*cmd["run"], "--text", "--calldata", "7", "--calldata", "8,9", str(src)])
    assert result.exit_code == 0, result.output
    assert result.stdout == "stack (bottom to top):\n  7\n  8\n"


@pytest.mark.parametrize(
    "flags",
    [
        ["--calldata", str(2**63)],
        ["--calldata", "1,x"],
        ["--step-limit", str(2**64)],
        ["--memory-limit", "-1"],
        ["--outputs", str(2**64)],
        # Spellings Python's int() takes and Rust's FromStr does not.
        ["--calldata", "1_000"],
        ["--calldata", " 7"],
        ["--calldata", "\u0663"],
        ["--outputs", "1_6"],
        ["--step-limit", "-0"],
        # An empty token, as clap's value_delimiter split it.
        ["--calldata", "1,"],
        ["--calldata="],
    ],
)
def test_run_out_of_range_values_are_usage_errors(tmp_path: Path, cmd: dict[str, list[str]], flags: list[str]) -> None:
    result = invoke([*cmd["run"], "--text", *flags, str(write(tmp_path, "prog.xqasm", PUSH_42))])
    assert result.exit_code == 2, result.output


def test_run_accepts_signed_and_plus_prefixed_integers(tmp_path: Path) -> None:
    src = write(tmp_path, "prog.xqasm", CALLDATA_R0)
    # `=` form: clap read a separate `-5` token as a flag, so only this spelling worked in both.
    result = invoke(["vm", "run", "--text", "--calldata=-5", "--outputs", "+16", str(src)])
    assert result.exit_code == 0, result.output
    assert result.stdout == "stack (bottom to top):\n  -5\n"


def test_run_trace_produces_stderr_output(
    tmp_path: Path, cmd: dict[str, list[str]], capfd: pytest.CaptureFixture[str]
) -> None:
    result = invoke([*cmd["run"], "--text", "--trace", str(write(tmp_path, "prog.xqasm", PUSH_42))])
    assert result.exit_code == 0, result.output
    assert capfd.readouterr().err


def test_run_trace_json_format_emits_json_objects(
    tmp_path: Path, cmd: dict[str, list[str]], capfd: pytest.CaptureFixture[str]
) -> None:
    src = write(tmp_path, "prog.xqasm", PUSH_42)
    result = invoke([*cmd["run"], "--text", "--trace", "--trace-format", "json", str(src)])
    assert result.exit_code == 0, result.output
    assert "{" in capfd.readouterr().err


def test_run_trace_file_creates_nonempty_file(tmp_path: Path, cmd: dict[str, list[str]]) -> None:
    trace = tmp_path / "trace.txt"
    src = write(tmp_path, "prog.xqasm", PUSH_42)
    result = invoke([*cmd["run"], "--text", "--trace", "--trace-file", str(trace), str(src)])
    assert result.exit_code == 0, result.output
    assert trace.stat().st_size > 0


def test_run_step_limit_exceeded_exits_nonzero(tmp_path: Path, cmd: dict[str, list[str]]) -> None:
    src = write(tmp_path, "prog.xqasm", LOOP_1M)
    result = invoke([*cmd["run"], "--text", "--step-limit", "10", str(src)])
    assert result.exit_code == 1


def test_run_step_limit_zero_exits_nonzero(tmp_path: Path, cmd: dict[str, list[str]]) -> None:
    # The limit is exact, so 0 is a real budget and not a sentinel.
    src = write(tmp_path, "prog.xqasm", PUSH_42)
    result = invoke([*cmd["run"], "--text", "--step-limit", "0", str(src)])
    assert result.exit_code == 1


def test_run_unlimited_steps_removes_the_bound(tmp_path: Path, cmd: dict[str, list[str]]) -> None:
    # A million-iteration loop is past the default budget, so it completes only if the flag removes the bound.
    src = write(tmp_path, "prog.xqasm", LOOP_1M)
    result = invoke([*cmd["run"], "--text", "--unlimited-steps", str(src)])
    assert result.exit_code == 0, result.output


def test_run_step_limit_and_unlimited_steps_conflict(tmp_path: Path, cmd: dict[str, list[str]]) -> None:
    # A usage error (exit 2), not a program that faulted (exit 1).
    src = write(tmp_path, "prog.xqasm", PUSH_42)
    result = invoke([*cmd["run"], "--text", "--step-limit", "10", "--unlimited-steps", str(src)])
    assert result.exit_code == 2


@pytest.mark.parametrize("flags", [["--trace-format", "json"], ["--trace-file", "t.txt"]])
def test_run_trace_options_require_trace(tmp_path: Path, cmd: dict[str, list[str]], flags: list[str]) -> None:
    result = invoke([*cmd["run"], "--text", *flags, str(write(tmp_path, "prog.xqasm", PUSH_42))])
    assert result.exit_code == 2


def test_run_prints_outputs_in_rust_debug_form(tmp_path: Path) -> None:
    src = write(
        tmp_path,
        "prog.xqasm",
        "PUSH 42\nSTOW r0\nPUSH 0\nOUTPUT r0\n"
        "PUSH 3\nVECI r1\nPUSH 7\nVECPUSH r1\nPUSH 1\nOUTPUT r1\n"
        "PUSH 2\nBQMX r2\nPUSH 2\nOUTPUT r2\nHALT\n",
    )
    result = invoke(["vm", "run", "--text", str(src)])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[:4] == [
        "outputs:",
        "  [0] = Int(42)",
        "  [1] = VecInt([7])",
        "  [2] = Model(XqmxModel { domain: Binary, size: 2, linear: {}, quadratic: {}, rows: 0, cols: 0 })",
    ]


def test_run_prints_samples_domains_coefficients_and_empty_vecxqmx(tmp_path: Path) -> None:
    src = write(
        tmp_path,
        "prog.xqasm",
        "PUSH 2\nSQMX r0\nPUSH 0\nPUSH 3\nSETLINE r0\nPUSH 0\nPUSH 1\nPUSH -2\nSETQUAD r0\nPUSH 0\nOUTPUT r0\n"
        "PUSH 2\nPUSH 3\nXSMX r1\nPUSH 1\nPUSH 2\nSETLINE r1\nPUSH 1\nOUTPUT r1\n"
        "VECX r2\nPUSH 2\nOUTPUT r2\nHALT\n",
    )
    result = invoke(["vm", "run", "--text", str(src)])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "outputs:",
        "  [0] = Model(XqmxModel { domain: Spin, size: 2, linear: {0: 3}, quadratic: {(0, 1): -2}, rows: 0, cols: 0 })",
        "  [1] = Sample(XqmxSample { domain: Integer(3), values: [0, 2], rows: 0, cols: 0 })",
        "  [2] = VecXqmx([])",
    ]


def test_outputs_debug_leaves_unset_slots_none() -> None:
    from xqffi.asm import assemble_source
    from xqffi.vm import Vm

    vm = Vm()
    vm.set_output_slots(2)
    vm.run(assemble_source("PUSH 7\nSTOW r0\nPUSH 1\nOUTPUT r0\nHALT\n"))
    assert vm.outputs_debug() == [None, "Int(7)"]


# -- check --


def test_check_bytecode(tmp_path: Path, cmd: dict[str, list[str]]) -> None:
    xqb = write(tmp_path, "prog.xqb", assemble_bytes(tmp_path, PUSH_42))
    result = invoke([*cmd["check"], str(xqb)])
    assert result.exit_code == 0, result.output
    assert result.stdout == f"ok: {xqb} (2 instructions)\n"


def test_check_text_violation_exits_one(tmp_path: Path, cmd: dict[str, list[str]]) -> None:
    result = invoke([*cmd["check"], "--text", str(write(tmp_path, "bad.xqasm", "NEXT\nHALT\n"))])
    assert result.exit_code == 1
    assert "NoActiveLoop" in result.stderr


# -- aliases --


@pytest.mark.parametrize(("old", "new"), [("asm", "asm"), ("dism", "dis"), ("run", "run"), ("verify", "check")])
def test_alias_warns_on_stderr_and_matches_stdout(tmp_path: Path, old: str, new: str) -> None:
    src = write(tmp_path, "prog.xqasm", PUSH_42)
    xqb = write(tmp_path, "prog.xqb", assemble_bytes(tmp_path, PUSH_42))
    args = {"asm": ["--stdout", str(src)], "dis": [str(xqb)], "run": [str(xqb)], "check": [str(xqb)]}[new]
    old_result = invoke([old, *args])
    new_result = invoke(["vm", new, *args])
    assert old_result.exit_code == new_result.exit_code == 0
    assert old_result.stdout_bytes == new_result.stdout_bytes
    warning = f"warning: 'xquad {old}' is deprecated and will be removed in v0.5.0; use 'xquad vm {new}'\n"
    assert old_result.stderr == warning + new_result.stderr


def test_help_lists_only_vm() -> None:
    result = invoke(["--help"])
    assert result.exit_code == 0
    assert "vm" in result.stdout
    for old in ("asm", "dism", "verify"):
        assert old not in result.stdout


def test_version() -> None:
    from importlib.metadata import version

    result = invoke(["--version"])
    assert result.stdout == f"xquad {version('xquad')}\n"
