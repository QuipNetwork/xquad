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

"""`xquad vm asm`: assemble XQVM source into bytecode."""

from __future__ import annotations

from pathlib import Path

import click

from xquad.cli._common import assemble, read_text


@click.command()
@click.argument("input", type=click.Path(path_type=Path))
@click.option("-o", "--output", type=click.Path(path_type=Path), help="Output file. Defaults to <INPUT>.xqb.")
@click.option("--stdout", "to_stdout", is_flag=True, help="Write bytecode to stdout instead of a file.")
def asm(input: Path, output: Path | None, to_stdout: bool) -> None:
    """Assemble XQVM source into bytecode."""
    if output is not None and to_stdout:
        raise click.UsageError("--output cannot be used with --stdout")
    from xqffi.asm import instruction_count

    bytecode = assemble(read_text(input))
    if to_stdout:
        click.get_binary_stream("stdout").write(bytecode)
        return
    out = output if output is not None else input.with_suffix(".xqb")
    try:
        out.write_bytes(bytecode)
    except OSError as e:
        raise click.ClickException(f"failed to write '{out}': {e.strerror}") from e
    click.echo(f"assembled {instruction_count(bytecode)} instructions ({len(bytecode)} bytes) -> {out}", err=True)
