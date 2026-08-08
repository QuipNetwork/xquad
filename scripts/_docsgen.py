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

"""Shared helpers for generated documentation files."""

from __future__ import annotations

import difflib
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent, indent
from typing import Any

import yaml


class SetupError(Exception):
    """Raised when generator inputs or outputs cannot be prepared."""


ExpectedType = type[Any] | tuple[type[Any], ...]
BANNER_PREFIX = "<!--\n  AUTO-GENERATED FILE. DO NOT EDIT."


@dataclass(frozen=True)
class Target:
    """A generated documentation target."""

    path: Path
    content: str


def banner(generator: str, source: str, instructions: str) -> str:
    """Render the standard generated-file banner.

    The banner is intentionally plain Markdown comment text so generated
    files remain renderer-neutral.
    """

    formatted_instructions = indent(dedent(instructions).strip(), "  ")
    return f"""{BANNER_PREFIX}
  This file is regenerated from `{source}` by
  `{generator}`.
{formatted_instructions}
-->"""


def read_text(path: Path) -> str:
    """Read a UTF-8 text file or raise a setup error."""

    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SetupError(f"cannot read {path}: {exc}") from exc


def load_yaml(path: Path) -> dict:
    """Load a YAML document or raise a setup error."""

    try:
        data = yaml.safe_load(read_text(path))
    except yaml.YAMLError as exc:
        raise SetupError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SetupError(f"{path} is empty or not a mapping")
    return data


def format_setup_error(exc: BaseException, repo_root: Path) -> str:
    """Render setup errors with repo-relative paths when possible."""

    message = str(exc)
    prefixes = [f"{repo_root}/"]
    try:
        prefixes.append(f"{repo_root.resolve()}/")
    except OSError:
        pass
    for prefix in dict.fromkeys(prefixes):
        message = message.replace(prefix, "")
    return message


def _type_name(expected_type: ExpectedType) -> str:
    if isinstance(expected_type, tuple):
        return " or ".join(t.__name__ for t in expected_type)
    return expected_type.__name__


def require_key(
    data: Mapping[str, object],
    path: str | Path,
    key: str,
    expected_type: ExpectedType = object,
) -> Any:
    """Return a required mapping key or raise a setup error.

    ``None`` is rejected even when ``expected_type`` is ``object`` because a
    key truncated to ``key:`` in YAML is present but unusable schema data.
    """

    if key not in data:
        raise SetupError(f"{path}: missing required key `{key}`")

    value = data[key]
    if value is None:
        raise SetupError(f"{path}: required key `{key}` must be {_type_name(expected_type)}, got null")
    if not isinstance(value, expected_type):
        actual_type = type(value).__name__
        raise SetupError(f"{path}: required key `{key}` must be {_type_name(expected_type)}, got {actual_type}")
    return value


def require_mapping(value: object, path: str | Path) -> Mapping[str, object]:
    """Return a mapping value or raise a setup error."""

    if not isinstance(value, Mapping):
        raise SetupError(f"{path}: expected mapping, got {type(value).__name__}")
    return value


def emit(targets: list[Target], *, repo_root: Path, check: bool) -> int:
    """Write or check generated files.

    Returns 0 when all targets are current or written, 1 when check mode
    finds stale content, and 2 for setup errors such as unreadable files.
    """

    try:
        if check:
            stale_targets: list[tuple[Target, str]] = []
            for target in targets:
                existing = read_text(target.path) if target.path.exists() else ""
                if existing != target.content:
                    diff = "".join(
                        difflib.unified_diff(
                            existing.splitlines(keepends=True),
                            target.content.splitlines(keepends=True),
                            fromfile=f"{target.path.relative_to(repo_root)} (committed)",
                            tofile=f"{target.path.relative_to(repo_root)} (regenerated)",
                        )
                    )
                    stale_targets.append((target, diff))
                else:
                    print(f"{target.path.relative_to(repo_root)}: up to date")

            if not stale_targets:
                return 0

            for target, diff in stale_targets:
                rel_path = target.path.relative_to(repo_root)
                sys.stderr.write(f"{rel_path} is stale; run `make docs-regen`.\n\n")
                sys.stderr.write(diff)
                if not diff.endswith("\n"):
                    sys.stderr.write("\n")
                sys.stderr.write("\n")
            return 1

        for target in targets:
            target.path.parent.mkdir(parents=True, exist_ok=True)
            target.path.write_text(target.content, encoding="utf-8")
            print(f"wrote {target.path.relative_to(repo_root)}")
        return 0
    except (OSError, SetupError) as exc:
        sys.stderr.write(f"docs generation setup error: {exc}\n")
        return 2
