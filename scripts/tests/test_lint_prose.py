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

"""Behavioural tests for the exit codes of scripts/lint-prose.sh.

The script documents three exits -- 0 pass, 1 prose findings, 2 setup
error -- and Vale exits non-zero for two unrelated reasons, so the tests
that matter are about which of Vale's failures maps to which of the
script's.

The rule-set guard is covered too. It is not about Vale's exit codes at
all, but it shares this script and fails the same way -- exit 2, before
Vale runs.

Vale is stubbed rather than invoked. `make test-py` runs in an image that
carries no Vale binary (it is installed only for the `docs:handwritten`
job), and the behaviour under test belongs to the script and not to Vale:
what the script does with a given exit code and a given stream is exactly
the thing that was wrong. Two cases pin the real Vale 3.15.1 contract the
script reads -- alerts on stdout, configuration failures on stderr -- and
run only where the binary is present.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "lint-prose.sh"
REPO_ROOT = Path(__file__).resolve().parents[2]
RULES_DIR = REPO_ROOT / ".vale" / "styles" / "XQuad"

ALERT_LINE = "page.md:3:12:XQuad.Dashes:Write `--` rather than —."
CONFIG_ERROR = "/repo/.vale.ini:0:E201:The path '/repo/.nope' does not exist."


def write_vale_stub(bin_dir: Path, *, stdout: str = "", stderr: str = "", code: int = 0) -> None:
    """Install a `vale` on PATH that emits fixed output and exits `code`."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "vale"
    stub.write_text(
        "#!/bin/sh\n"
        f"[ -n '{stdout}' ] && printf '%s\\n' {stdout!r}\n"
        f"[ -n '{stderr}' ] && printf '%s\\n' {stderr!r} >&2\n"
        f"exit {code}\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)


# The system directories every case needs for bash, grep and sort, and
# where no Vale is ever installed -- the stub cases must resolve `vale` to
# the stub and the missing-vale case must resolve it to nothing, so
# neither may inherit a PATH that happens to carry a real one.
SYSTEM_PATH = "/usr/bin:/bin"


def run_script(page: Path, path: str, **env: str) -> subprocess.CompletedProcess[str]:
    """Run the linter over one page with `path` as its PATH.

    A named page keeps the run off the 72-page book sweep, so a case turns
    on the stub's verdict rather than on the state of the tree.
    """
    return subprocess.run(
        ["bash", str(SCRIPT), str(page)],
        env={"PATH": path, "HOME": str(page.parent), **env},
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )


def stubbed(bin_dir: Path) -> str:
    """A PATH resolving `vale` to the stub in `bin_dir`."""
    return f"{bin_dir}:{SYSTEM_PATH}"


@pytest.fixture
def page(tmp_path: Path) -> Path:
    """A Markdown file to name on the command line."""
    target = tmp_path / "page.md"
    target.write_text("# Page\n\nBody.\n", encoding="utf-8")
    return target


def test_clean_run_passes(page: Path, tmp_path: Path) -> None:
    write_vale_stub(tmp_path / "bin")

    result = run_script(page, stubbed(tmp_path / "bin"))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "no findings" in result.stdout


def test_alerts_are_a_prose_failure(page: Path, tmp_path: Path) -> None:
    write_vale_stub(tmp_path / "bin", stdout=ALERT_LINE, code=1)

    result = run_script(page, stubbed(tmp_path / "bin"))

    assert result.returncode == 1
    assert ALERT_LINE in result.stdout
    assert "findings above" in result.stderr


def test_config_failure_is_a_setup_error(page: Path, tmp_path: Path) -> None:
    """A broken config must not be reported as a prose finding.

    This is the case the script got wrong: every non-zero exit was mapped
    to 1 and announced as "findings above", sending the reader to look for
    a prose problem that does not exist while Vale's own diagnostic named
    the config file.
    """
    write_vale_stub(tmp_path / "bin", stderr=CONFIG_ERROR, code=2)

    result = run_script(page, stubbed(tmp_path / "bin"))

    assert result.returncode == 2
    assert CONFIG_ERROR in result.stderr
    assert "configuration or runtime error" in result.stderr
    assert "findings above" not in result.stderr


def test_an_alert_count_exit_is_still_a_prose_failure(page: Path, tmp_path: Path) -> None:
    """The split is on the stream, not on the number.

    Vale has historically exited with the alert count. A discriminator
    keyed on the code alone would read a two-alert run as the exit-2 setup
    error, so the durable signal is alert text on stdout.
    """
    write_vale_stub(tmp_path / "bin", stdout=ALERT_LINE, code=3)

    result = run_script(page, stubbed(tmp_path / "bin"))

    assert result.returncode == 1
    assert "findings above" in result.stderr


def test_missing_vale_is_a_setup_error(page: Path) -> None:
    assert shutil.which("vale", path=SYSTEM_PATH) is None, "SYSTEM_PATH is meant to carry no vale"

    result = run_script(page, SYSTEM_PATH)

    assert result.returncode == 2
    assert "vale is not on PATH" in result.stderr


@pytest.mark.skipif(shutil.which("vale") is None, reason="vale is not installed")
def test_real_vale_reports_alerts_on_stdout(tmp_path: Path) -> None:
    """Pins the Vale contract the stubs above encode."""
    target = tmp_path / "page.md"
    target.write_text("# Page\n\nAn em-dash — here.\n", encoding="utf-8")

    result = run_script(target, os.environ.get("PATH", SYSTEM_PATH))

    assert result.returncode == 1
    assert "XQuad.Dashes" in result.stdout
    assert "findings above" in result.stderr


@pytest.mark.skipif(shutil.which("vale") is None, reason="vale is not installed")
def test_real_vale_reports_a_broken_config_on_stderr(tmp_path: Path) -> None:
    """The other half of the contract, without breaking the tree's config.

    `VALE_CONFIG_PATH` is how Vale is pointed at a config other than the
    one beside it, so a deliberately broken file can be used without the
    repository's own .vale.ini being touched.
    """
    broken = tmp_path / "broken.ini"
    broken.write_text("StylesPath = .nope/styles\nMinAlertLevel = suggestion\n", encoding="utf-8")
    target = tmp_path / "page.md"
    target.write_text("# Page\n\nBody.\n", encoding="utf-8")

    result = run_script(
        target,
        os.environ.get("PATH", SYSTEM_PATH),
        VALE_CONFIG_PATH=str(broken),
    )

    assert result.returncode == 2
    assert "configuration or runtime error" in result.stderr
    assert "findings above" not in result.stderr


def test_the_expected_rule_set_is_the_one_in_the_tree() -> None:
    """The list in the script names exactly the rule files that exist.

    Asserted from the outside so the guard cannot be satisfied by a stale
    list: a rule added to .vale/styles/XQuad without being named in
    EXPECTED_RULES fails here, and so does a name left behind by a rule
    that was removed.
    """
    declared = SCRIPT.read_text(encoding="utf-8")
    line = next(ln for ln in declared.splitlines() if ln.startswith("EXPECTED_RULES="))
    expected = sorted(line.split("=", 1)[1].strip().strip('"').split())
    present = sorted(path.stem for path in RULES_DIR.glob("*.yml"))

    assert expected == present


def test_a_missing_rule_file_is_a_setup_error(page: Path, tmp_path: Path) -> None:
    """A dropped rule must not read as a clean prose run.

    `BasedOnStyles = XQuad` enables whatever rule files are present and
    reports nothing about one that is absent. With all three gone Vale
    exits 0 having run no rule at all, which the script would otherwise
    announce as 72 pages checked with no findings -- a green run that
    checked nothing.
    """
    write_vale_stub(tmp_path / "bin")
    hidden = tmp_path / "Dashes.yml"
    shutil.move(str(RULES_DIR / "Dashes.yml"), str(hidden))
    try:
        result = run_script(page, stubbed(tmp_path / "bin"))
    finally:
        shutil.move(str(hidden), str(RULES_DIR / "Dashes.yml"))

    assert result.returncode == 2
    assert "rule set is not what this script expects" in result.stderr
    assert "Dashes" in result.stderr


@pytest.mark.skipif(shutil.which("vale") is None, reason="vale is not installed")
def test_real_vale_is_silent_about_a_missing_rule(tmp_path: Path) -> None:
    """Pins the Vale behaviour the guard above exists to compensate for.

    Run against a styles directory holding no rules at all, Vale exits 0
    on a page carrying both an em-dash and an emoji. Nothing in its output
    distinguishes that from a page that is genuinely clean, which is why
    the rule set has to be checked before Vale is asked anything.
    """
    styles = tmp_path / "styles" / "XQuad"
    styles.mkdir(parents=True)
    (styles / "vocab.txt").write_text("", encoding="utf-8")
    config = tmp_path / "empty.ini"
    config.write_text(
        f"StylesPath = {tmp_path / 'styles'}\nMinAlertLevel = error\n\n[*.md]\nBasedOnStyles = XQuad\n",
        encoding="utf-8",
    )
    target = tmp_path / "page.md"
    target.write_text("# Page\n\nAn em-dash — and an emoji 🎉.\n", encoding="utf-8")

    result = subprocess.run(
        ["vale", "--no-global", "--config", str(config), "--output", "line", str(target)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == ""
