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

from pathlib import Path

import click

from xquad.cli._common import load_program


def _calldata(ctx: click.Context, param: click.Parameter, value: str | None) -> list[int]:
    """Parse `--calldata a,b,c` into integers."""
    if value is None:
        return []
    try:
        return [int(item) for item in value.split(",")]
    except ValueError as e:
        raise click.BadParameter(f"expected comma-separated integers, got {value!r}") from e


def _debug(value: object) -> str:
    """Render an output slot the way Rust's `{:?}` prints the `RegVal` it came from."""
    from xqffi.vm import XqmxModel, XqmxSample

    def domain(v: XqmxModel | XqmxSample) -> str:
        return f"Integer({v.k})" if v.domain == "integer" else v.domain.capitalize()

    def model(m: XqmxModel) -> str:
        linear = dict(sorted(m.linear_items()))
        quadratic = dict(sorted(m.quadratic_items()))
        return (
            f"XqmxModel {{ domain: {domain(m)}, size: {m.size}, linear: {linear}, "
            f"quadratic: {quadratic}, rows: {m.rows}, cols: {m.cols} }}"
        )

    if isinstance(value, XqmxModel):
        return f"Model({model(value)})"
    if isinstance(value, XqmxSample):
        return (
            f"Sample(XqmxSample {{ domain: {domain(value)}, values: {value.values}, "
            f"rows: {value.rows}, cols: {value.cols} }})"
        )
    if isinstance(value, list):
        # shortcut: an empty VecXqmx comes back as [] and prints as VecInt([]); the binding does not tell them apart.
        if value and isinstance(value[0], XqmxModel):
            return f"VecXqmx([{', '.join(model(m) for m in value)}])"
        return f"VecInt({value})"
    return f"Int({value})"


@click.command()
@click.option("--text", is_flag=True, help="Treat FILE as assembly text and assemble before running.")
@click.option(
    "--calldata",
    callback=_calldata,
    metavar="A,B,...",
    help="Comma-separated calldata integers passed to INPUT instructions.",
)
@click.option(
    "--outputs",
    type=click.IntRange(min=0),
    default=16,
    show_default=True,
    help="Number of output slots available for OUTPUT instructions.",
)
@click.option(
    "--step-limit",
    type=click.IntRange(min=0),
    help="Maximum number of steps to execute. Exact: 0 executes nothing. Defaults to the VM's DEFAULT_STEP_LIMIT.",
)
@click.option(
    "--unlimited-steps",
    is_flag=True,
    help="Run without a step limit. Conflicts with --step-limit. A program that never halts will not return.",
)
@click.option(
    "--memory-limit",
    type=click.IntRange(min=0),
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
    try:
        vm.run(bytecode, trace=(trace_format or "text") if trace else None, trace_file=trace_file)
    except OSError as e:
        raise click.ClickException(str(e)) from e
    except RuntimeError as e:
        raise click.ClickException(f"run failed: {e}") from e

    slots = [(i, v) for i, v in enumerate(vm.outputs()) if v is not None]
    if slots:
        click.echo("outputs:")
        for i, v in slots:
            click.echo(f"  [{i}] = {_debug(v)}")
    stack = vm.stack()
    if stack:
        click.echo("stack (bottom to top):")
        for v in stack:
            click.echo(f"  {v}")
