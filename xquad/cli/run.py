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

"""`xquad vm run`: run XQVM bytecode or assembly."""

from __future__ import annotations

import re
import signal
import threading
from pathlib import Path

import click

from xquad.cli._common import load_program

# Rust's integer `FromStr` takes an optional sign and ASCII digits only; Python's `int()` also takes
# underscores, surrounding whitespace and non-ASCII digits, which xqcli rejected as usage errors.
_SIGNED = re.compile(r"[+-]?[0-9]+")
_UNSIGNED = re.compile(r"\+?[0-9]+")
_I64 = range(-(2**63), 2**63)
_U64_MAX = 2**64 - 1


class _U64(click.ParamType):
    """A `u64` option value, parsed as strictly as clap parsed it."""

    name = "integer"

    def convert(self, value: object, param: click.Parameter | None, ctx: click.Context | None) -> int:
        if isinstance(value, int):
            return value
        if not _UNSIGNED.fullmatch(str(value)):
            self.fail(f"{value!r} is not a non-negative integer", param, ctx)
        n = int(str(value))
        if n > _U64_MAX:
            self.fail(f"{value} does not fit in a 64-bit unsigned integer", param, ctx)
        return n


def _calldata(ctx: click.Context, param: click.Parameter, value: tuple[str, ...]) -> list[int]:
    """Parse `--calldata a,b,c` into integers; repeating the flag appends, as `xqcli` did."""
    items = [item for chunk in value for item in chunk.split(",")]
    if bad := [item for item in items if not _SIGNED.fullmatch(item)]:
        raise click.BadParameter(f"{bad[0]!r} is not an integer")
    ints = [int(item) for item in items]
    if out_of_range := [n for n in ints if n not in _I64]:
        raise click.BadParameter(f"{out_of_range[0]} does not fit in a 64-bit signed integer")
    return ints


@click.command()
@click.option("--text", is_flag=True, help="Treat FILE as assembly text and assemble before running.")
@click.option(
    "--calldata",
    callback=_calldata,
    multiple=True,
    metavar="A,B,...",
    help="Comma-separated calldata integers passed to INPUT instructions.",
)
@click.option(
    "--outputs",
    type=_U64(),
    default=16,
    show_default=True,
    help="Number of output slots available for OUTPUT instructions.",
)
@click.option(
    "--step-limit",
    type=_U64(),
    help="Maximum number of steps to execute. Exact: 0 executes nothing. Defaults to the VM's DEFAULT_STEP_LIMIT.",
)
@click.option(
    "--unlimited-steps",
    is_flag=True,
    help="Run without a step limit. Conflicts with --step-limit. A program that never halts will not return.",
)
@click.option(
    "--memory-limit",
    type=_U64(),
    help="Allocation budget in bytes for models, samples and vectors. Defaults to the VM's DEFAULT_MEMORY_LIMIT.",
)
@click.option("--trace", is_flag=True, help="Enable step-by-step execution tracing.")
@click.option(
    "--trace-format",
    type=click.Choice(["text", "json"]),
    help="Trace output format (requires --trace).  [default: text]",
)
@click.option(
    "--trace-file",
    type=click.Path(path_type=Path),
    help="Write trace output to a file instead of stderr (requires --trace).",
)
@click.argument("file", type=click.Path(path_type=Path))
def run(
    text: bool,
    calldata: list[int],
    outputs: int,
    step_limit: int | None,
    unlimited_steps: bool,
    memory_limit: int | None,
    trace: bool,
    trace_format: str | None,
    trace_file: Path | None,
    file: Path,
) -> None:
    """Run XQVM bytecode or assembly.

    Reads a program from FILE and executes it. Use --text to accept
    assembly source instead of a binary bytecode file. Add --trace for a
    step-by-step execution log.
    """
    if step_limit is not None and unlimited_steps:
        raise click.UsageError("--step-limit cannot be used with --unlimited-steps")
    if not trace and (trace_format is not None or trace_file is not None):
        raise click.UsageError("--trace-format and --trace-file require --trace")
    from xqffi.vm import Vm

    bytecode = load_program(file, text=text)
    vm = Vm()
    vm.set_calldata(calldata)
    vm.set_output_slots(outputs)
    if unlimited_steps:
        vm.set_unlimited_steps()
    elif step_limit is not None:
        vm.set_step_limit(step_limit)
    if memory_limit is not None:
        vm.set_memory_limit(memory_limit)
    # The VM runs with the GIL released and never returns to Python's SIGINT handler, so Ctrl-C
    # would be ignored until the run ended; the default action kills the process, as xqcli did.
    # Only Python's own handler is swapped: an inherited ignore stays, and off the main thread
    # signal.signal would raise.
    sigint = signal.getsignal(signal.SIGINT)
    swap = sigint is signal.default_int_handler and threading.current_thread() is threading.main_thread()
    if swap:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
    try:
        vm.run(bytecode, trace=(trace_format or "text") if trace else None, trace_file=trace_file)
    except OSError as e:
        raise click.ClickException(str(e)) from e
    except RuntimeError as e:
        raise click.ClickException(f"run failed: {e}") from e
    finally:
        if swap:
            signal.signal(signal.SIGINT, sigint)

    slots = [(i, v) for i, v in enumerate(vm.outputs_debug()) if v is not None]
    if slots:
        click.echo("outputs:")
        for i, v in slots:
            click.echo(f"  [{i}] = {v}")
    stack = vm.stack()
    if stack:
        click.echo("stack (bottom to top):")
        for v in stack:
            click.echo(f"  {v}")
