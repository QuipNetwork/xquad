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

"""The `xquad` command in a real process: the installed script, `python -m xquad`, startup imports, fd-2 trace."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

XQUAD = shutil.which("xquad", path=str(Path(sys.executable).parent))


def _run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False, **kwargs)


@pytest.mark.skipif(XQUAD is None, reason="the xquad console script is not installed beside this interpreter")
def test_installed_script_matches_python_m(tmp_path: Path) -> None:
    src = tmp_path / "prog.xqasm"
    src.write_text("PUSH 42\nHALT\n")
    script = _run([str(XQUAD), "vm", "run", "--text", str(src)])
    module = _run([sys.executable, "-m", "xquad", "vm", "run", "--text", str(src)])
    assert script.returncode == module.returncode == 0, script.stderr
    assert script.stdout == module.stdout == "stack (bottom to top):\n  42\n"
    assert _run([str(XQUAD), "--version"]).stdout == _run([sys.executable, "-m", "xquad", "--version"]).stdout


@pytest.mark.parametrize("args", [["--help"], ["vm", "asm", "--stdout"]], ids=["help", "vm-asm"])
def test_startup_leaves_solvers_and_dsl_unimported(tmp_path: Path, args: list[str]) -> None:
    src = tmp_path / "prog.xqasm"
    src.write_text("PUSH 42\nHALT\n")
    argv = [*args, str(src)] if args[0] == "vm" else args
    code = (
        "import sys\n"
        "from xquad.cli import main\n"
        f"sys.argv = ['xquad', *{argv!r}]\n"
        "try:\n"
        "    main()\n"
        "except SystemExit as e:\n"
        "    assert not e.code, e.code\n"
        "sys.stderr.write(' '.join(m for m in ('xqsa', 'xqcp') if m in sys.modules))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, check=True)
    assert result.stderr.decode().strip() == ""


def test_run_trace_without_file_writes_to_stderr(tmp_path: Path) -> None:
    src = tmp_path / "prog.xqasm"
    src.write_text("PUSH 42\nHALT\n")
    result = _run([sys.executable, "-m", "xquad", "vm", "run", "--text", "--trace", str(src)])
    assert result.returncode == 0, result.stderr
    assert result.stderr.splitlines()[0].split()[:3] == ["step", "offset", "instruction"]
    assert result.stdout == "stack (bottom to top):\n  42\n"
