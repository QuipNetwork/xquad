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

"""`xquad vm dis`: disassemble XQVM bytecode."""

from __future__ import annotations

from pathlib import Path

import click

from xquad.cli._common import read_bytes


@click.command()
@click.argument("file", type=click.Path(path_type=Path), required=False)
def dis(file: Path | None) -> None:
    """Disassemble XQVM bytecode into a human-readable listing.

    Reads raw bytecode from FILE, or from stdin when FILE is omitted, and
    prints a listing to stdout. Jump targets are labelled .0, .1, ... from
    the jump table.
    """
    from xqffi.asm import disassemble

    bytecode = read_bytes(file) if file is not None else click.get_binary_stream("stdin").read()
    try:
        listing = disassemble(bytecode)
    except ValueError as e:
        raise click.ClickException(f"failed to decode program: {e}") from e
    click.echo(listing, nl=False)
