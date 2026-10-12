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

"""The box frame the Quip displays share: 72 columns, label column, wrapped values."""

from __future__ import annotations

import textwrap
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, NamedTuple

WIDTH = 72
LABEL_WIDTH = 12
_INNER = WIDTH - 4  # "│ " and " │"
_VALUE_WIDTH = _INNER - LABEL_WIDTH

Row = tuple[str, str]


def box(title: str, sections: Sequence[Sequence[Row]], *, double_before: int | None = None) -> str:
    """Render ``sections`` of ``(label, value)`` rows in a titled frame, ``WIDTH`` columns wide.

    Sections are split by a single rule, or a double one before the section at
    index ``double_before``. A value longer than its column, or holding
    newlines, continues on lines with a blank label; a long unbroken value such
    as a hash is split mid-word.
    """
    lines = [f"╭─ {title} " + "─" * (WIDTH - len(title) - 5) + "╮"]
    for index, rows in enumerate(sections):
        if index:
            lines.append("╞" + "═" * (WIDTH - 2) + "╡" if index == double_before else "├" + "─" * (WIDTH - 2) + "┤")
        for label, value in rows:
            for offset, chunk in enumerate(_wrap(value)):
                lines.append(f"│ {(label if offset == 0 else ''):<{LABEL_WIDTH}}{chunk:<{_VALUE_WIDTH}} │")
    lines.append("╰" + "─" * (WIDTH - 2) + "╯")
    return "\n".join(lines)


def terms_rows(
    *, resolution: Any, mode: Any, floors: str, deadline_blocks: int, block_wait: int, reward: str
) -> list[Row]:
    """Return the Terms section an order and its receipt both show; ``floors`` and ``reward`` come formatted."""
    return [
        ("Payout", variant_text(resolution)),
        ("Access", variant_text(mode)),
        ("Floors", floors),
        ("Deadline", f"{deadline_blocks} blocks"),
        ("Block wait", f"{block_wait} blocks"),
        ("Reward", reward),
    ]


class Floors(NamedTuple):
    """An order's quality floors as the chain holds them, in milli where scaled; ``None`` is no floor."""

    max_energy_milli: int | None
    min_solutions: int | None
    min_diversity_milli: int | None


def floors_text(floors: Floors, model_ceiling: float | None = None, *, moved: bool = False) -> str:
    """Render the quality floors, or ``"none"``.

    ``model_ceiling`` is the ceiling in the model's own units, known only to
    the client that placed the order. When given, it leads, and the chain's
    spin-unit value follows if ``moved`` says the offset moved it:
    ``energy <= -2 (chain -1.25)`` for a BINARY model whose offset is -0.75.
    """
    parts = []
    if floors.max_energy_milli is not None:
        chain = _decimal_text(Decimal(floors.max_energy_milli) / 1000)
        if model_ceiling is None:
            parts.append(f"energy <= {chain}")
        else:
            differs = f" (chain {chain})" if moved else ""
            parts.append(f"energy <= {_decimal_text(Decimal(repr(float(model_ceiling))))}{differs}")
    if floors.min_solutions is not None:
        parts.append(f"{floors.min_solutions} solution{'' if floors.min_solutions == 1 else 's'}")
    if floors.min_diversity_milli is not None:
        parts.append(f"diversity >= {_decimal_text(Decimal(floors.min_diversity_milli) / 1000)}")
    return ", ".join(parts) or "none"


def _decimal_text(value: Decimal) -> str:
    """Render ``value`` in plain notation without trailing zeros (``-4.0`` -> ``-4``)."""
    return format(value.normalize(), "f")


def variant_text(value: Any) -> str:
    """Render a unit enum variant as words (``"SingleBest"`` -> ``"single best"``); anything else as ``repr``."""
    if not isinstance(value, str):
        return repr(value)
    words = "".join(f" {char}" if char.isupper() else char for char in value).split()
    return " ".join(word.lower() for word in words)


def _wrap(value: str) -> list[str]:
    """Split ``value`` into chunks that fit the value column."""
    chunks: list[str] = []
    for line in value.splitlines() or [""]:
        chunks += textwrap.wrap(line, _VALUE_WIDTH, break_long_words=True, break_on_hyphens=False) or [""]
    return chunks
