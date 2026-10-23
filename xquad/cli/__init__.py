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

"""The `xquad` command.

`xquad vm asm|dis|run|check` are the XQVM tools. The old top-level names
`xquad asm|dism|run|verify` stay as hidden aliases that warn on stderr
until v0.5.0. Each command module imports only `click` at module level
and its backing package inside the command, so `xquad --help` stays fast.
"""

from __future__ import annotations

from typing import Any

import click

from xquad.cli.asm import asm
from xquad.cli.check import check
from xquad.cli.dis import dis
from xquad.cli.run import run

__all__ = ["main"]


# Children inherit the help option names.
@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(None, "-V", "--version", package_name="xquad", prog_name="xquad", message="%(prog)s %(version)s")
def main() -> None:
    """XQuad toolchain driver."""


@main.group()
def vm() -> None:
    """XQVM tools: assemble, disassemble, run and check bytecode."""


for _command in (asm, dis, run, check):
    vm.add_command(_command)


def _deprecated_alias(old: str, command: click.Command) -> click.Command:
    """A hidden top-level `xquad <old>` that warns, then runs `xquad vm <command>`."""

    def callback(**kwargs: Any) -> Any:
        click.echo(
            f"warning: 'xquad {old}' is deprecated and will be removed in v0.5.0; use 'xquad vm {command.name}'",
            err=True,
        )
        assert command.callback is not None
        return command.callback(**kwargs)

    return click.Command(
        old,
        callback=callback,
        params=command.params,
        help=command.help,
        hidden=True,
    )


# Removed in v0.5.0.
for _old, _command in (("asm", asm), ("dism", dis), ("run", run), ("verify", check)):
    main.add_command(_deprecated_alias(_old, _command))
