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

"""`xquad vm check`: run the bytecode verifier over a program."""

from __future__ import annotations

from pathlib import Path

import click

from xquad.cli._common import load_program


@click.command()
@click.option("--text", is_flag=True, help="Treat FILE as assembly text and assemble before checking.")
@click.argument("file", type=click.Path(path_type=Path))
def check(text: bool, file: Path) -> None:
    """Check XQVM bytecode statically (all verifier phases).

    Reads FILE (binary bytecode, or assembly with --text) and runs every
    verifier phase: structural, jump-target, loop-nesting, register
    type-state and stack depth. Exits 0 on success, 1 on the first
    violation found.
    """
    from xqffi.asm import instruction_count
    from xqffi.verifier import verify

    bytecode = load_program(file, text=text)
    try:
        verify(bytecode)
    except ValueError as e:
        raise click.ClickException(f"check failed: {e}") from e
    count = instruction_count(bytecode)
    click.echo(f"ok: {file} ({count} instruction{'' if count == 1 else 's'})")
