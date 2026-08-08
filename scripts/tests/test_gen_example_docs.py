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

"""Unit tests for generated example book pages."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from _docsgen import SetupError

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "gen-example-docs.py"


def load_generator_module():
    spec = importlib.util.spec_from_file_location("gen_example_docs_under_test", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_readme_link_validation_skips_fenced_code():
    module = load_generator_module()
    lines = [
        "# Example",
        "",
        "```sh",
        "# [scratch](../../CONTRIBUTING.md)",
        "```",
        "",
        "See [GPU/QPU installation](../../README.md#gpuqpu-support).",
    ]

    assert module.readme_link_errors(Path("README.md"), lines) == []


def test_rewrite_links_skips_fenced_code():
    module = load_generator_module()
    lines = [
        "```sh",
        "echo ../../xqsa/README.md",
        "```",
        "See [xqsa solver quick-starts](../../xqsa/README.md).",
    ]

    assert module.rewrite_links(lines) == [
        "```sh",
        "echo ../../xqsa/README.md",
        "```",
        "See [xqsa solver quick-starts](../solving/README.md).",
    ]


def test_readme_link_validation_rejects_unmapped_relative_link():
    module = load_generator_module()
    lines = ["See [contributing](../../CONTRIBUTING.md)."]

    errors = module.readme_link_errors(Path("README.md"), lines)

    assert errors == ["README.md:1: unmapped relative link `../../CONTRIBUTING.md`"]


def test_transform_readme_preserves_headings_and_strips_canonical_output(tmp_path, monkeypatch):
    module = load_generator_module()
    readme = tmp_path / "examples" / "graph_coloring" / "README.md"
    readme.parent.mkdir(parents=True)
    readme.write_text(
        "\n".join(
            [
                "# Graph Coloring",
                "",
                "Assign colours.",
                "",
                "## QUBO formulation",
                "",
                "### Encoding strategy",
                "",
                "See [GPU/QPU installation](../../README.md#gpuqpu-support) and",
                "[xqsa solver quick-starts](../../xqsa/README.md).",
                "",
                "## Canonical output",
                "",
                "```sh",
                "# fenced heading does not end the section",
                "```",
                "Do not publish this.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "EXAMPLES_ROOT", tmp_path / "examples")
    entry = module.ExampleEntry(directory="graph_coloring", title="Graph Coloring", blurb="Colour a graph.")

    rendered = module.transform_readme(entry)

    assert rendered.startswith("<!--\n  AUTO-GENERATED FILE. DO NOT EDIT.")
    assert "# Graph Coloring\n" in rendered
    assert "Source: [examples/graph_coloring/README.md]" in rendered
    assert "## QUBO formulation" in rendered
    assert "### Encoding strategy" in rendered
    assert "../start/README.md" in rendered
    assert "../solving/README.md" in rendered
    assert "## Canonical output" not in rendered
    assert "# fenced heading does not end the section" not in rendered
    assert "Do not publish this." not in rendered
    assert "The canonical output and its invariants are defined in the [source README]" in rendered


def test_validate_summary_examples_matches_manifest_order(tmp_path, monkeypatch):
    module = load_generator_module()
    summary_path = tmp_path / "SUMMARY.md"
    summary_path.write_text(
        "\n".join(
            [
                "# Summary",
                "",
                "- [Examples](examples/README.md)",
                "  - [Using the Examples](examples/using-examples.md)",
                "  - [Max-Cut](examples/maxcut.md)",
                "  - [Graph Coloring](examples/graph_coloring.md)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    groups = [
        module.ExampleGroup(
            identifier="graph-problems",
            title="Graph problems",
            intro="Intro.",
            examples=[
                module.ExampleEntry(directory="maxcut", title="Max-Cut", blurb="Cut."),
                module.ExampleEntry(directory="graph_coloring", title="Graph Coloring", blurb="Color."),
            ],
        )
    ]
    monkeypatch.setattr(module, "SUMMARY_PATH", summary_path)

    module.validate_summary_examples(groups)


def test_validate_summary_examples_rejects_title_drift(tmp_path, monkeypatch):
    module = load_generator_module()
    summary_path = tmp_path / "SUMMARY.md"
    summary_path.write_text(
        "\n".join(
            [
                "# Summary",
                "",
                "- [Examples](examples/README.md)",
                "  - [Old Max-Cut](examples/maxcut.md)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    groups = [
        module.ExampleGroup(
            identifier="graph-problems",
            title="Graph problems",
            intro="Intro.",
            examples=[module.ExampleEntry(directory="maxcut", title="Max-Cut", blurb="Cut.")],
        )
    ]
    monkeypatch.setattr(module, "SUMMARY_PATH", summary_path)

    with pytest.raises(SetupError, match="example entries must match"):
        module.validate_summary_examples(groups)


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("{}\n", "missing required key `groups`"),
        ("groups:\n", "required key `groups` must be list, got null"),
        ("groups: {}\n", "required key `groups` must be list, got dict"),
    ],
)
def test_generator_main_returns_setup_error_for_bad_manifest(tmp_path, monkeypatch, capsys, source, message):
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(source, encoding="utf-8")
    module = load_generator_module()
    monkeypatch.setattr(module, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(sys, "argv", ["gen-example-docs.py", "--check"])

    assert module.main() == 2
    captured = capsys.readouterr()
    assert "docs generation setup error:" in captured.err
    assert message in captured.err
    assert "Traceback" not in captured.err


def test_generator_main_returns_setup_error_for_non_utf8_readme(tmp_path, monkeypatch, capsys):
    examples_root = tmp_path / "examples"
    book_root = tmp_path / "book"
    readme = examples_root / "maxcut" / "README.md"
    runner = examples_root / "maxcut" / "runner.py"
    manifest_path = examples_root / "manifest.yaml"
    summary_path = tmp_path / "SUMMARY.md"
    readme.parent.mkdir(parents=True)
    book_root.mkdir()
    runner.write_text("# runner\n", encoding="utf-8")
    readme.write_bytes(b"# Max-Cut\n\ncaf\xe9\n")
    manifest_path.write_text(
        "\n".join(
            [
                "groups:",
                "  - id: graph-problems",
                "    title: Graph problems",
                "    intro: Intro.",
                "    examples:",
                "      - dir: maxcut",
                "        title: Max-Cut",
                "        blurb: Cut.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    summary_path.write_text("  - [Max-Cut](examples/maxcut.md)\n", encoding="utf-8")
    module = load_generator_module()
    monkeypatch.setattr(module, "EXAMPLES_ROOT", examples_root)
    monkeypatch.setattr(module, "BOOK_EXAMPLES_ROOT", book_root)
    monkeypatch.setattr(module, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(module, "SUMMARY_PATH", summary_path)
    monkeypatch.setattr(sys, "argv", ["gen-example-docs.py", "--check"])

    assert module.main() == 2
    captured = capsys.readouterr()
    assert "docs generation setup error:" in captured.err
    assert "cannot read" in captured.err
    assert "README.md" in captured.err
    assert "Traceback" not in captured.err


def test_manifest_validation_reports_missing_entry(tmp_path, monkeypatch):
    module = load_generator_module()
    examples_root = tmp_path / "examples"
    runner = examples_root / "maxcut" / "runner.py"
    runner.parent.mkdir(parents=True)
    runner.write_text("# runner\n", encoding="utf-8")
    monkeypatch.setattr(module, "EXAMPLES_ROOT", examples_root)
    groups = [module.ExampleGroup(identifier="g", title="G", intro="Intro.", examples=[])]

    with pytest.raises(SetupError, match="example directory `maxcut` has no manifest entry"):
        module.validate_manifest_dirs(groups)


def test_handle_orphan_pages_unlinks_generated_page_in_write_mode(tmp_path, monkeypatch):
    module = load_generator_module()
    examples_root = tmp_path / "examples"
    book_root = tmp_path / "book"
    book_root.mkdir()
    orphan = book_root / "old.md"
    orphan.write_text(module.banner("scripts/gen-example-docs.py", "source", "Run.") + "\n", encoding="utf-8")
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "EXAMPLES_ROOT", examples_root)
    monkeypatch.setattr(module, "BOOK_EXAMPLES_ROOT", book_root)

    assert module.handle_orphan_pages([module.Target(book_root / "README.md", "")], set(), check=False) == 0
    assert not orphan.exists()


def test_handle_orphan_pages_keeps_non_generated_page(tmp_path, monkeypatch):
    module = load_generator_module()
    examples_root = tmp_path / "examples"
    book_root = tmp_path / "book"
    book_root.mkdir()
    orphan = book_root / "advanced.md"
    orphan.write_text("# Advanced\n", encoding="utf-8")
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "EXAMPLES_ROOT", examples_root)
    monkeypatch.setattr(module, "BOOK_EXAMPLES_ROOT", book_root)

    with pytest.raises(SetupError, match="preserved_pages"):
        module.handle_orphan_pages([module.Target(book_root / "README.md", "")], set(), check=False)
    assert orphan.exists()
