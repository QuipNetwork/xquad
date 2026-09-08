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

"""Unit tests for shared input loading and setup error helpers."""

from __future__ import annotations

import pytest

from _scriptio import SetupError, format_setup_error, load_yaml, read_json, require_key


def test_load_yaml_reports_missing_source(tmp_path):
    with pytest.raises(SetupError, match="cannot read"):
        load_yaml(tmp_path / "missing.yaml")


def test_load_yaml_reports_empty_source(tmp_path):
    empty_yaml = tmp_path / "empty.yaml"
    empty_yaml.write_text("", encoding="utf-8")

    with pytest.raises(SetupError, match="empty or not a mapping"):
        load_yaml(empty_yaml)


def test_load_yaml_reports_non_utf8_source(tmp_path):
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_bytes(b"groups:\n  - caf\xe9\n")

    with pytest.raises(SetupError, match="cannot read"):
        load_yaml(bad_yaml)


def test_read_json_reports_missing_source(tmp_path):
    with pytest.raises(SetupError, match="cannot read"):
        read_json(tmp_path / "missing.json")


def test_read_json_reports_malformed_source(tmp_path):
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(SetupError, match="cannot read"):
        read_json(bad_json)


def test_format_setup_error_makes_repo_paths_relative(tmp_path):
    message = f"{tmp_path}/examples/manifest.yaml: missing required key `groups`"

    assert format_setup_error(SetupError(message), tmp_path) == "examples/manifest.yaml: missing required key `groups`"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "missing required key `opcodes`"),
        ({"opcodes": None}, "required key `opcodes` must be list, got null"),
        ({"opcodes": {}}, "required key `opcodes` must be list, got dict"),
    ],
)
def test_require_key_reports_bad_required_key(payload, message):
    with pytest.raises(SetupError, match=message):
        require_key(payload, "source.yaml", "opcodes", list)
