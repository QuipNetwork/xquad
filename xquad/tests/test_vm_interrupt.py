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

"""A signal stops `xqffi.vm.Vm.run` with the exception its handler raises.

A handler that returns lets the run go on, and a run on a worker thread is
never stopped. Each case runs in a child process: the program never halts, so
a run that missed the signal, or a deadlock, would hang the test session, and
the child's timeout turns that into a failure instead.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Delivered from a timer thread once the run is under way. `raise_signal`
# sends a real SIGINT; `interrupt_main` trips Python's flag without one. The
# child installs the handler itself, so an ignored SIGINT inherited from CI
# does not turn either into a no-op.
CHILD = """
import _thread, json, signal, sys, threading, time
from xqffi.asm import assemble_source
from xqffi.vm import Vm

delivery, handler, trace_file = sys.argv[1:]

class Stop(Exception):
    pass

def custom(signum, frame):
    raise Stop

signal.signal(signal.SIGINT, signal.default_int_handler if handler == "default" else custom)
expected = KeyboardInterrupt if handler == "default" else Stop

sent = []
def deliver():
    sent.append(time.monotonic())
    if delivery == "raise_signal":
        signal.raise_signal(signal.SIGINT)
    else:
        _thread.interrupt_main()

vm = Vm()
vm.set_unlimited_steps()
threading.Timer(0.3, deliver).start()
trace = dict(trace="json", trace_file=trace_file) if trace_file else {}
try:
    vm.run(assemble_source("TARGET .0\\nNOP\\nJUMP .0\\n"), **trace)
except expected:
    latency = time.monotonic() - sent[0]
else:
    sys.exit("the run returned")
steps, instructions = vm.steps(), vm.instructions()
vm.reset()
vm.run(assemble_source("PUSH 42\\nHALT\\n"))
print(json.dumps(dict(latency=latency, steps=steps, instructions=instructions, after_reset=vm.stack())))
"""

#: Generous next to the 100 ms check period, so a loaded CI runner passes.
LATENCY_BOUND_S = 2.0


@pytest.mark.parametrize(
    ("delivery", "handler", "traced"),
    [
        ("raise_signal", "default", False),
        ("interrupt_main", "default", False),
        ("raise_signal", "default", True),
        ("raise_signal", "custom", False),
    ],
    ids=["sigint", "interrupt-main", "sigint-traced", "custom-handler"],
)
def test_signal_stops_an_unbounded_run(tmp_path: Path, delivery: str, handler: str, traced: bool) -> None:
    trace_file = str(tmp_path / "trace.jsonl") if traced else ""
    result = subprocess.run(
        [sys.executable, "-c", CHILD, delivery, handler, trace_file],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert 0 <= report["latency"] < LATENCY_BOUND_S
    # Every instruction here costs one step, and the run made progress.
    assert report["steps"] == report["instructions"] > 0
    assert report["after_reset"] == [42]
    if traced:
        # The trace was flushed and ends on a whole line at the stopping point.
        lines = Path(trace_file).read_text().splitlines()
        assert len(lines) == report["instructions"]
        assert json.loads(lines[-1])["step"] == report["instructions"]


# A run that a signal must not stop: either the handler returns, or the run is
# on a worker thread, where Python never runs handlers. The program never
# halts, so the step limit is what ends the run. The limit is sized from a
# timed calibration run so the run lasts about RUN_S in whatever build xqffi
# has, which leaves the 0.2 s timer and the 100 ms check period well inside it.
CHILD_UNSTOPPED = """
import json, signal, sys, threading, time
from xqffi.asm import assemble_source
from xqffi.vm import StepLimitExceeded, Vm

where, run_s = sys.argv[1], float(sys.argv[2])
program = assemble_source("TARGET .0\\nNOP\\nJUMP .0\\n")

def run_to_limit(limit):
    vm = Vm()
    vm.set_step_limit(limit)
    try:
        vm.run(program)
    except StepLimitExceeded:
        return dict(steps=vm.steps(), ended=time.monotonic())
    raise AssertionError("the run ended without reaching its step limit")

calibration = 1_000_000
started = time.monotonic()
run_to_limit(calibration)
limit = max(calibration, int(calibration * run_s / (time.monotonic() - started)))

handled = []
signal.signal(signal.SIGINT, lambda signum, frame: handled.append((threading.current_thread().name, time.monotonic())))
sent = []
def deliver():
    sent.append(time.monotonic())
    signal.raise_signal(signal.SIGINT)
threading.Timer(0.2, deliver).start()

result = {}
if where == "main":
    result.update(run_to_limit(limit))
else:
    worker = threading.Thread(target=lambda: result.update(run_to_limit(limit)), name="worker")
    worker.start()
    worker.join()
print(json.dumps(dict(limit=limit, result=result, sent=sent, handled=handled)))
"""

#: How long the unstopped run is sized to last.
RUN_S = 1.0


@pytest.mark.parametrize("where", ["main", "worker"])
def test_signal_does_not_stop_a_run_it_should_not(where: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", CHILD_UNSTOPPED, where, str(RUN_S)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    # The run went on to its step limit rather than stopping at the signal.
    assert report["result"]["steps"] == report["limit"]
    # The signal went out while the run was still going, and the handler ran
    # once, on the main thread.
    [sent_at] = report["sent"]
    assert sent_at < report["result"]["ended"]
    [(thread, handled_at)] = report["handled"]
    assert thread == "MainThread"
    if where == "main":
        # Only the run's own signal check can run a handler before it ends.
        assert handled_at < report["result"]["ended"]
