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
import signal
import subprocess
import sys
import time
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
    usage = _run([str(XQUAD), "vm", "run"])
    assert usage.returncode == 2
    assert usage.stderr == _run([sys.executable, "-m", "xquad", "vm", "run"]).stderr
    assert usage.stderr.startswith("Usage: xquad vm run")


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


def test_sigint_stops_an_unbounded_run(tmp_path: Path) -> None:
    src = tmp_path / "forever.xqasm"
    src.write_text(".0: NOP\nJUMP .0\n")
    trace = tmp_path / "trace.txt"
    args = ["vm", "run", "--text", "--unlimited-steps", "--trace", "--trace-file", str(trace), str(src)]
    # SIG_DFL in the child, so Python installs its own handler there even if pytest inherited an ignore.
    proc = subprocess.Popen(
        [sys.executable, "-m", "xquad", *args],
        preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL),
    )
    try:
        # A trace on disk means the VM is running, so the CLI has already swapped the handler.
        deadline = time.monotonic() + 30
        while not (trace.exists() and trace.stat().st_size) and time.monotonic() < deadline:
            time.sleep(0.01)
        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=5) == -signal.SIGINT
    finally:
        proc.kill()
        proc.wait()


def test_run_trace_without_file_writes_to_stderr(tmp_path: Path) -> None:
    src = tmp_path / "prog.xqasm"
    src.write_text("PUSH 42\nHALT\n")
    result = _run([sys.executable, "-m", "xquad", "vm", "run", "--text", "--trace", str(src)])
    assert result.returncode == 0, result.stderr
    assert result.stderr.splitlines()[0].split()[:3] == ["step", "offset", "instruction"]
    assert result.stdout == "stack (bottom to top):\n  42\n"


def test_quip_without_the_extra_prints_the_hint() -> None:
    code = (
        "import sys\n"
        "sys.modules['substrateinterface'] = None\n"
        "from xquad.cli import main\n"
        "sys.argv = ['xquad', 'quip', 'account', 'list']\n"
        "main()\n"
    )
    result = _run([sys.executable, "-c", code])
    assert result.returncode == 1
    assert result.stderr == "`xquad quip` needs the Quip extra: `pip install 'xquad[quip]'`\n"
    assert result.stdout == ""
