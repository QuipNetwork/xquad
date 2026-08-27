#!/usr/bin/env python3
# Copyright (C) 2026 Postquant Labs Incorporated
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
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

"""Regenerate book example pages from examples/manifest.yaml and README files."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from _docsgen import (
    BANNER_PREFIX,
    SetupError,
    Target,
    banner,
    emit,
    format_setup_error,
    load_yaml,
    read_text,
    require_key,
    require_mapping,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_ROOT = REPO_ROOT / "examples"
MANIFEST_PATH = EXAMPLES_ROOT / "manifest.yaml"
BOOK_EXAMPLES_ROOT = REPO_ROOT / "docs" / "book" / "src" / "examples"
SUMMARY_PATH = REPO_ROOT / "docs" / "book" / "src" / "SUMMARY.md"
GITLAB_BLOB_URL = "https://gitlab.com/quip.network/xquad/-/blob/main"
SOURCE_SCHEMES = ("http://", "https://", "mailto:", "#")
# Keys are the repo-relative links as written in `examples/*/README.md`, where
# `README.md` is correct: those pages are browsed on GitLab. Values are the
# book-relative rewrites, where a section index must be named by its directory
# -- mdBook renames `<dir>/README.md` to `<dir>/index.html` but rewrites links
# by swapping `.md` for `.html`, so a `README.md` target renders as a dead
# `README.html` (QUI-1040).
LINK_MAP = {
    "../../README.md#gpuqpu-support": "../start/",
    "../../xqsa/README.md": "../solving/",
    "../../docs/book/src/concepts/three-programs.md": "../concepts/three-programs.md",
    (
        "../../docs/book/src/modelling/constraints.md#why-the-reported-energy-is-not-just--total_value"
    ): "../modelling/constraints.md#why-the-reported-energy-is-not-just--total_value",
    (
        "../../docs/book/src/modelling/constraints.md#choosing-a-penalty-weight"
    ): "../modelling/constraints.md#choosing-a-penalty-weight",
}
SOLVER_SECTION_REPLACEMENT = [
    "Solver selection and install extras are the same for every example: see",
    "[Using the Examples](using-examples.md#running-one) and",
    "[Solving Overview](../solving/). The default is `dwave-cpu`, and a",
    "non-default solver will not reproduce the output shown here.",
]
INLINE_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
REFERENCE_LINK_RE = re.compile(r"^\s*\[[^\]]+\]:")


@dataclass(frozen=True)
class ExampleEntry:
    """One example page described by the manifest."""

    directory: str
    title: str
    blurb: str
    hardware: bool = False


@dataclass(frozen=True)
class ExampleGroup:
    """One gallery group described by the manifest."""

    identifier: str
    title: str
    intro: str
    examples: list[ExampleEntry]


@dataclass(frozen=True)
class ExampleManifest:
    """The validated example documentation manifest."""

    groups: list[ExampleGroup]
    preserved_pages: set[str]


def _manifest_path(*parts: object) -> str:
    suffix = "".join(str(part) for part in parts)
    return f"{MANIFEST_PATH}: {suffix}"


def load_manifest() -> ExampleManifest:
    """Load and validate the example manifest."""

    data = load_yaml(MANIFEST_PATH)
    groups_value = require_key(data, MANIFEST_PATH, "groups", list)
    preserved_pages_value = data.get("preserved_pages", [])
    if preserved_pages_value is None:
        raise SetupError(f"{MANIFEST_PATH}: optional key `preserved_pages` must be list, got null")
    if not isinstance(preserved_pages_value, list):
        raise SetupError(
            f"{MANIFEST_PATH}: optional key `preserved_pages` must be list, got {type(preserved_pages_value).__name__}"
        )

    preserved_pages: set[str] = set()
    groups: list[ExampleGroup] = []
    seen_group_ids: set[str] = set()
    seen_dirs: set[str] = set()
    errors: list[str] = []

    for page_index, page_value in enumerate(preserved_pages_value):
        page_path = _manifest_path(f"preserved_pages[{page_index}]")
        if not isinstance(page_value, str):
            errors.append(f"{page_path}: expected str, got {type(page_value).__name__}")
            continue
        if Path(page_value).name != page_value or not page_value.endswith(".md"):
            errors.append(f"{page_path}: expected a Markdown filename in docs/book/src/examples")
            continue
        preserved_pages.add(page_value)

    for group_index, group_value in enumerate(groups_value):
        group_path = _manifest_path(f"groups[{group_index}]")
        group = require_mapping(group_value, group_path)
        identifier = require_key(group, group_path, "id", str)
        if identifier in seen_group_ids:
            errors.append(f"{group_path}: duplicate group id `{identifier}`")
        seen_group_ids.add(identifier)
        examples_value = require_key(group, group_path, "examples", list)
        entries: list[ExampleEntry] = []

        for example_index, example_value in enumerate(examples_value):
            example_path = _manifest_path(f"groups[{group_index}].examples[{example_index}]")
            example = require_mapping(example_value, example_path)
            directory = require_key(example, example_path, "dir", str)
            title = require_key(example, example_path, "title", str)
            blurb = require_key(example, example_path, "blurb", str)
            hardware = example.get("hardware", False)
            if not isinstance(hardware, bool):
                errors.append(f"{example_path}: optional key `hardware` must be bool, got {type(hardware).__name__}")
                hardware = False
            if directory in seen_dirs:
                errors.append(f"{example_path}: duplicate example dir `{directory}`")
            seen_dirs.add(directory)
            entries.append(ExampleEntry(directory=directory, title=title, blurb=blurb, hardware=hardware))

        groups.append(
            ExampleGroup(
                identifier=identifier,
                title=require_key(group, group_path, "title", str),
                intro=require_key(group, group_path, "intro", str),
                examples=entries,
            )
        )

    if errors:
        raise SetupError("\n".join(errors))
    return ExampleManifest(groups=groups, preserved_pages=preserved_pages)


def discover_example_dirs() -> set[str]:
    """Return example directories using the same discovery rule as example-smoke."""

    return {runner.parent.name for runner in sorted(EXAMPLES_ROOT.glob("*/runner.py"))}


def _all_entries(groups: list[ExampleGroup]) -> list[ExampleEntry]:
    return [entry for group in groups for entry in group.examples]


def validate_manifest_dirs(groups: list[ExampleGroup]) -> None:
    """Check manifest entries against discovered example directories."""

    discovered = discover_example_dirs()
    manifest_dirs = {entry.directory for entry in _all_entries(groups)}
    errors: list[str] = []

    for directory in sorted(discovered - manifest_dirs):
        errors.append(f"{MANIFEST_PATH}: example directory `{directory}` has no manifest entry")
    for directory in sorted(manifest_dirs - discovered):
        errors.append(f"{MANIFEST_PATH}: manifest entry `{directory}` has no examples/{directory}/runner.py")
    for entry in _all_entries(groups):
        readme = EXAMPLES_ROOT / entry.directory / "README.md"
        if not readme.exists():
            errors.append(f"{MANIFEST_PATH}: manifest entry `{entry.directory}` has no README.md")

    if errors:
        raise SetupError("\n".join(errors))


def _is_fence(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("```") or stripped.startswith("~~~")


def iter_prose_lines(lines: list[str]):
    """Yield lines with a flag indicating fenced-code context."""

    in_fence = False
    for line_number, line in enumerate(lines, start=1):
        if _is_fence(line):
            in_fence = not in_fence
            yield line_number, line, True
            continue
        yield line_number, line, in_fence


def _validate_link(path: Path, line_number: int, link: str, errors: list[str]) -> None:
    if link.startswith(SOURCE_SCHEMES):
        return
    if link in LINK_MAP:
        return
    errors.append(f"{path}:{line_number}: unmapped relative link `{link}`")


def readme_link_errors(path: Path, lines: list[str]) -> list[str]:
    """Return reference-style links and unmapped relative links."""

    errors: list[str] = []
    for line_number, line, in_fence in iter_prose_lines(lines):
        if in_fence:
            continue
        if REFERENCE_LINK_RE.match(line):
            errors.append(f"{path}:{line_number}: reference-style links are not supported")
        for match in INLINE_LINK_RE.finditer(line):
            _validate_link(path, line_number, match.group(1), errors)
    return errors


def validate_readmes(groups: list[ExampleGroup]) -> None:
    """Validate every source README before any generated page is written."""

    errors: list[str] = []
    for entry in _all_entries(groups):
        source_path = EXAMPLES_ROOT / entry.directory / "README.md"
        lines = read_text(source_path).splitlines()
        if not lines or not lines[0].startswith("# "):
            errors.append(f"{source_path}: expected first line to be an H1 heading")
        errors.extend(readme_link_errors(source_path, lines))

    if errors:
        raise SetupError("\n".join(errors))


def rewrite_links(lines: list[str]) -> list[str]:
    """Rewrite known repo-relative links for the book."""

    def replace(match: re.Match[str]) -> str:
        link = match.group(1)
        rewritten = LINK_MAP.get(link, link)
        return match.group(0).replace(f"({link})", f"({rewritten})")

    return [line if in_fence else INLINE_LINK_RE.sub(replace, line) for _, line, in_fence in iter_prose_lines(lines)]


def replace_section(lines: list[str], heading: str, replacement: list[str] | None) -> tuple[list[str], bool]:
    """Drop a whole `## ` section, or substitute a new body under its heading.

    Sections run from their heading to the next `#` or `##` heading, so an
    `###` subheading stays inside. Passing `None` removes the heading too.
    Returns the rewritten lines and whether the section was found.
    """

    kept: list[str] = []
    skipping = False
    found = False
    for _, line, in_fence in iter_prose_lines(lines):
        if not in_fence and line.strip() == heading:
            skipping = True
            found = True
            if replacement is not None:
                kept.append(line)
                kept.append("")
                kept.extend(replacement)
            continue
        if skipping and not in_fence and line.startswith("#") and not line.startswith("###"):
            skipping = False
        if not skipping:
            kept.append(line)
    while kept and kept[-1] == "":
        kept.pop()
    return kept, found


def transform_readme(entry: ExampleEntry) -> str:
    """Render one book example page from its source README."""

    source_path = EXAMPLES_ROOT / entry.directory / "README.md"
    lines = read_text(source_path).splitlines()
    if not lines or not lines[0].startswith("# "):
        raise SetupError(f"{source_path}: expected first line to be an H1 heading")

    body = lines[1:]
    if body and body[0] == "":
        body = body[1:]
    body, stripped_canonical_output = replace_section(body, "## Canonical output", None)
    # The solver table is identical in all fourteen source READMEs, where each
    # one is a standalone page. Inside the book it would be the same eighteen
    # lines fourteen times over, one click from the chapter that owns them.
    body, _ = replace_section(body, "## Choosing a solver", SOLVER_SECTION_REPLACEMENT)
    rewritten_body = rewrite_links(body)
    source_url = f"{GITLAB_BLOB_URL}/examples/{entry.directory}/README.md"

    output = [
        banner(
            "scripts/gen-example-docs.py",
            "examples/manifest.yaml and examples/*/README.md",
            "Edit the source README or manifest, then run `make regen-docs`.",
        ),
        "",
        f"# {entry.title}",
        "",
        f"Source: [examples/{entry.directory}/README.md]({source_url})",
        "",
        *rewritten_body,
    ]
    if stripped_canonical_output:
        output.extend(
            [
                "",
                f"The canonical output and its invariants are defined in the [source README]({source_url}).",
            ]
        )
    return "\n".join(output).rstrip() + "\n"


def render_gallery(groups: list[ExampleGroup]) -> str:
    """Render the generated examples gallery."""

    lines = [
        banner(
            "scripts/gen-example-docs.py",
            "examples/manifest.yaml",
            "Edit the manifest, then run `make regen-docs`.",
        ),
        "",
        "# Examples",
        "",
    ]
    for group in groups:
        lines.append(f'<a id="{group.identifier}"></a>')
        lines.append("")
        lines.append(f"## {group.title}")
        lines.append("")
        lines.append(group.intro)
        lines.append("")
        for entry in group.examples:
            lines.append(f"- [{entry.title}]({entry.directory}.md) -- {entry.blurb}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_repo_index(groups: list[ExampleGroup]) -> str:
    """Render the repository-side examples index.

    The book gallery and this page list the same examples from the same
    manifest and differ only in where their links point: the gallery links
    to book pages, this one to the source directory a reader browsing the
    repository is already standing in. Generating both from `manifest.yaml`
    is what stops the two lists disagreeing.
    """

    lines = [
        banner(
            "scripts/gen-example-docs.py",
            "examples/manifest.yaml",
            "Edit the manifest, then run `make regen-docs`.",
        ),
        "",
        "# XQuad Examples",
        "",
        "Fourteen self-contained optimisation problems, one directory each. Every",
        "directory holds a `README.md` describing the formulation and a `runner.py`",
        "that builds the model, solves it, and verifies the result end to end.",
        "",
        "Run one from the repository root:",
        "",
        "```sh",
        "uv run python examples/maxcut/runner.py --seed 42",
        "```",
        "",
        "Every runner accepts `--seed`, `--interpreter` (`python` or `rust`),",
        "`--solver`, and `-o`/`--output`. Problem size flags vary; each directory's",
        "`README.md` carries the exact table.",
        "",
    ]
    for group in groups:
        lines.append(f"## {group.title}")
        lines.append("")
        lines.append(group.intro)
        lines.append("")
        for entry in group.examples:
            lines.append(f"- [{entry.title}]({entry.directory}/README.md) -- {entry.blurb}")
        lines.append("")

    lines.extend(
        [
            "## Further reading",
            "",
            "- [Using the Examples](../docs/book/src/examples/using-examples.md) -- what every",
            "  directory has in common, how to run one, and how to adapt one into a problem",
            "  of your own",
            "- [Modelling with XQCP](../docs/book/src/modelling/README.md) -- the DSL these",
            "  runners are written in",
            "- [Cookbook](../docs/book/src/cookbook/README.md) -- the recurring encoding",
            "  patterns these examples are built from",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def validate_summary_examples(groups: list[ExampleGroup]) -> None:
    """Check SUMMARY.md keeps example titles in manifest order."""

    manifest_lines = [f"  - [{entry.title}](examples/{entry.directory}.md)" for entry in _all_entries(groups)]
    manifest_dirs = {entry.directory for entry in _all_entries(groups)}
    summary_lines = [
        line
        for line in read_text(SUMMARY_PATH).splitlines()
        if any(f"](examples/{directory}.md)" in line for directory in manifest_dirs)
    ]
    if summary_lines != manifest_lines:
        raise SetupError(f"{SUMMARY_PATH}: example entries must match examples/manifest.yaml titles and order")


def build_targets(groups: list[ExampleGroup]) -> list[Target]:
    """Render every generated example target."""

    targets = [
        Target(BOOK_EXAMPLES_ROOT / "README.md", render_gallery(groups)),
        Target(EXAMPLES_ROOT / "README.md", render_repo_index(groups)),
    ]
    targets.extend(
        Target(BOOK_EXAMPLES_ROOT / f"{entry.directory}.md", transform_readme(entry)) for entry in _all_entries(groups)
    )
    return targets


def find_orphan_pages(targets: list[Target], preserved_pages: set[str]) -> list[Path]:
    """Find generated book example pages no longer produced by the manifest."""

    expected = {target.path for target in targets}
    preserved = {BOOK_EXAMPLES_ROOT / page for page in preserved_pages}
    return sorted(path for path in BOOK_EXAMPLES_ROOT.glob("*.md") if path not in expected and path not in preserved)


def handle_orphan_pages(targets: list[Target], preserved_pages: set[str], *, check: bool) -> int:
    """Report or delete stale generated pages, guarded by the banner interlock."""

    orphans = find_orphan_pages(targets, preserved_pages)
    status = 0
    for path in orphans:
        content = read_text(path)
        rel_path = path.relative_to(REPO_ROOT)
        if not content.startswith(BANNER_PREFIX):
            raise SetupError(
                f"{rel_path}: refusing to remove non-generated example page; add it to "
                "`preserved_pages` in examples/manifest.yaml or move it out of docs/book/src/examples"
            )
        if check:
            sys.stderr.write(f"{rel_path} is stale; run `make regen-docs`.\n\n")
            status = 1
        else:
            path.unlink()
            print(f"removed {rel_path}")
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the committed files match the generator output.",
    )
    args = parser.parse_args()

    try:
        manifest = load_manifest()
        groups = manifest.groups
        validate_manifest_dirs(groups)
        validate_readmes(groups)
        validate_summary_examples(groups)
        targets = build_targets(groups)
        orphan_status = handle_orphan_pages(targets, manifest.preserved_pages, check=args.check)
    except (OSError, SetupError) as exc:
        sys.stderr.write(f"docs generation setup error: {format_setup_error(exc, REPO_ROOT)}\n")
        return 2

    emit_status = emit(targets, repo_root=REPO_ROOT, check=args.check)
    return max(orphan_status, emit_status)


if __name__ == "__main__":
    sys.exit(main())
