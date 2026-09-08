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

"""Shared input loading and setup errors for repository scripts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml


class SetupError(Exception):
    """Raised when generator inputs or outputs cannot be prepared."""


ExpectedType = type[Any] | tuple[type[Any], ...]


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


def read_json(path: Path) -> Any:
    """Read and parse a JSON file, raising SetupError on any failure."""

    try:
        data = json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        raise SetupError(f"cannot read {path}: {exc}") from exc
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
