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

"""Reading the files the commands take, with errors a user can act on."""

from __future__ import annotations

from pathlib import Path

import click


def read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as e:
        raise click.ClickException(f"failed to read '{path}': {e.strerror}") from e


def read_text(path: Path) -> str:
    try:
        return read_bytes(path).decode("utf-8")
    except UnicodeDecodeError as e:
        raise click.ClickException(f"'{path}' is not valid UTF-8: {e}") from e


def assemble(source: str) -> bytes:
    from xqffi import asm

    try:
        return asm.assemble_source(source)
    except ValueError as e:
        raise click.ClickException(f"assembly failed: {e}") from e


def load_program(path: Path, *, text: bool) -> bytes:
    """Bytecode from `path`: read as is, or assembled first when `text` is set."""
    return assemble(read_text(path)) if text else read_bytes(path)
