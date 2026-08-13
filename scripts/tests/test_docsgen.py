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

"""Unit tests for shared documentation generation helpers."""

from __future__ import annotations

import dataclasses
import importlib.util
import sys
from pathlib import Path

import pytest

from _docsgen import BANNER_PREFIX, SetupError, Target, banner, emit, format_setup_error, load_yaml, require_key

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "gen-bytecode-docs.py"


def load_generator_module():
    spec = importlib.util.spec_from_file_location("gen_bytecode_docs_under_test", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_banner_renders_standard_interlock():
    rendered = banner(
        "scripts/gen.py",
        "source.yaml",
        """
        Run `make regen-docs`.
        Then commit the result.
        """,
    )
    assert "AUTO-GENERATED FILE. DO NOT EDIT." in rendered
    assert rendered.startswith(BANNER_PREFIX)
    assert "`source.yaml`" in rendered
    assert "`scripts/gen.py`" in rendered
    assert "  Run `make regen-docs`.\n  Then commit the result." in rendered


def test_emit_write_creates_parent_and_reports_path(tmp_path, capsys):
    target = Target(tmp_path / "nested" / "page.md", "content\n")

    assert emit([target], repo_root=tmp_path, check=False) == 0

    assert target.path.read_text(encoding="utf-8") == "content\n"
    assert capsys.readouterr().out == "wrote nested/page.md\n"


def test_emit_check_reports_every_stale_target(tmp_path, capsys):
    fresh = Target(tmp_path / "fresh.md", "fresh\n")
    stale = Target(tmp_path / "stale.md", "new\n")
    missing = Target(tmp_path / "missing.md", "created\n")
    fresh.path.write_text("fresh\n", encoding="utf-8")
    stale.path.write_text("old\n", encoding="utf-8")

    result = emit([fresh, stale, missing], repo_root=tmp_path, check=True)

    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == "fresh.md: up to date\n"
    assert "stale.md is stale; run `make regen-docs`." in captured.err
    assert "missing.md is stale; run `make regen-docs`." in captured.err
    assert "--- stale.md (committed)" in captured.err
    assert "--- missing.md (committed)" in captured.err


def test_emit_returns_setup_error_for_unwritable_parent(tmp_path, capsys):
    blocked_parent = tmp_path / "file.txt"
    blocked_parent.write_text("not a directory\n", encoding="utf-8")

    result = emit([Target(blocked_parent / "page.md", "content\n")], repo_root=tmp_path, check=False)

    assert result == 2
    assert "docs generation setup error:" in capsys.readouterr().err


def test_emit_check_returns_setup_error_for_non_utf8_target(tmp_path, capsys):
    target = Target(tmp_path / "page.md", "content\n")
    target.path.write_bytes(b"caf\xe9\n")

    assert emit([target], repo_root=tmp_path, check=True) == 2
    captured = capsys.readouterr()
    assert "docs generation setup error:" in captured.err
    assert "cannot read" in captured.err
    assert "Traceback" not in captured.err


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


def test_generator_main_returns_setup_error_for_missing_yaml(tmp_path, monkeypatch, capsys):
    module = load_generator_module()
    monkeypatch.setattr(module, "YAML_PATH", tmp_path / "missing.yaml")
    monkeypatch.setattr(sys, "argv", ["gen-bytecode-docs.py", "--check"])

    assert module.main() == 2
    assert "docs generation setup error:" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("reserved: []\n", "missing required key `opcodes`"),
        ("opcodes:\n", "required key `opcodes` must be list, got null"),
        ("opcodes: {}\n", "required key `opcodes` must be list, got dict"),
    ],
)
def test_generator_main_returns_setup_error_for_bad_opcode_key(tmp_path, monkeypatch, capsys, source, message):
    yaml_path = tmp_path / "opcodes.yaml"
    yaml_path.write_text(source, encoding="utf-8")
    module = load_generator_module()
    monkeypatch.setattr(module, "YAML_PATH", yaml_path)
    monkeypatch.setattr(sys, "argv", ["gen-bytecode-docs.py", "--check"])

    assert module.main() == 2
    captured = capsys.readouterr()
    assert "docs generation setup error:" in captured.err
    assert message in captured.err
    assert "Traceback" not in captured.err


def test_target_is_frozen():
    target = Target(Path("page.md"), "content\n")
    assert target.path == Path("page.md")
    with pytest.raises(dataclasses.FrozenInstanceError):
        target.path = Path("other.md")
