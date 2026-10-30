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

"""Behavioural tests for scripts/check-fault-docstrings.py.

The comparison functions are tested in-process against small stub sources
and stand-in modules. `main` is driven against the built `xqffi.vm` with
the stub path pointed at a copy of `vm.pyi`, so a drifted docstring is
shown to fail the real check without editing the committed stub.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "check-fault-docstrings.py"


def _load_guard() -> ModuleType:
    """Import the guard by path; its hyphenated filename is not importable."""
    spec = importlib.util.spec_from_file_location("check_fault_docstrings", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load_guard()


def _module(**docs: str | None) -> ModuleType:
    """A stand-in for `xqffi.vm` exporting `XqvmError` and one subclass per entry."""
    module = ModuleType("fake_vm")
    base = type("XqvmError", (RuntimeError,), {"__doc__": "Base."})
    module.XqvmError = base
    module.Unrelated = type("Unrelated", (Exception,), {"__doc__": "Not a fault."})
    module.Vm = type("Vm", (), {"__doc__": "Not a fault either."})
    for name, doc in docs.items():
        setattr(module, name, type(name, (base,), {"__doc__": doc}))
    return module


STUB = '''
class Vm:
    """Not a fault."""

class XqvmError(RuntimeError):
    """Base."""

    offset: int | None

class StackUnderflow(XqvmError):
    """A pop was attempted
    on an empty stack."""

class Undocumented(XqvmError): ...
'''


def test_normalise_ignores_wrapping_and_indentation() -> None:
    wrapped = """A pop was attempted
        on an   empty stack."""
    assert guard.normalise(wrapped) == "A pop was attempted on an empty stack."


def test_normalise_keeps_paragraph_breaks() -> None:
    assert guard.normalise("One.\n\nTwo.") != guard.normalise("One. Two.")


def test_stub_docstrings_collects_the_base_and_its_subclasses_only() -> None:
    assert guard.stub_docstrings(STUB) == {
        "XqvmError": "Base.",
        "StackUnderflow": "A pop was attempted\non an empty stack.",
        "Undocumented": "",
    }


def test_stub_docstrings_follows_a_grouping_class_declared_after_its_child() -> None:
    stub = """
class StepLimitExceeded(LimitExceeded):
    \"\"\"Execution ran past its step budget.\"\"\"

class LimitExceeded(XqvmError):
    \"\"\"A budget ran out.\"\"\"

class XqvmError(RuntimeError):
    \"\"\"Base.\"\"\"
"""
    assert guard.stub_docstrings(stub) == {
        "XqvmError": "Base.",
        "LimitExceeded": "A budget ran out.",
        "StepLimitExceeded": "Execution ran past its step budget.",
    }


def test_a_grouped_fault_is_matched_on_both_sides() -> None:
    module = _module(LimitExceeded="A budget ran out.")
    module.StepLimitExceeded = type("StepLimitExceeded", (module.LimitExceeded,), {"__doc__": "Out of steps."})
    stub = """
class XqvmError(RuntimeError):
    \"\"\"Base.\"\"\"

class LimitExceeded(XqvmError):
    \"\"\"A budget ran out.\"\"\"

class StepLimitExceeded(LimitExceeded):
    \"\"\"Out of steps.\"\"\"
"""
    assert guard.mismatches(guard.stub_docstrings(stub), guard.runtime_docstrings(module)) == []


def test_stub_docstrings_raises_on_a_stub_that_does_not_parse() -> None:
    with pytest.raises(SyntaxError):
        guard.stub_docstrings("class XqvmError(RuntimeError:\n")


def test_runtime_docstrings_collects_the_base_and_its_subclasses_only() -> None:
    module = _module(StackUnderflow="A pop.", Undocumented=None)
    assert guard.runtime_docstrings(module) == {
        "XqvmError": "Base.",
        "StackUnderflow": "A pop.",
        "Undocumented": "",
    }


def test_matching_docstrings_report_nothing() -> None:
    stub = {"XqvmError": "Base.", "StackUnderflow": "A pop was attempted\non an empty stack."}
    runtime = {"XqvmError": "Base.", "StackUnderflow": "A pop was attempted on an empty stack."}
    assert guard.mismatches(stub, runtime) == []


@pytest.mark.parametrize(
    ("stub", "runtime", "expected"),
    [
        ({}, {"StackUnderflow": "A pop."}, "StackUnderflow: raised by xqffi.vm but has no class in vm.pyi"),
        ({"StackUnderflow": "A pop."}, {}, "StackUnderflow: declared in vm.pyi but not exported by xqffi.vm"),
        ({"StackUnderflow": "A pop."}, {"StackUnderflow": ""}, "StackUnderflow: has no docstring in"),
        ({"StackUnderflow": ""}, {"StackUnderflow": "A pop."}, "StackUnderflow: vm.pyi docstring differs"),
        ({"StackUnderflow": "A push."}, {"StackUnderflow": "A pop."}, "StackUnderflow: vm.pyi docstring differs"),
    ],
    ids=["missing-from-stub", "missing-at-runtime", "empty-at-runtime", "empty-in-stub", "reworded"],
)
def test_each_disagreement_is_reported(stub: dict[str, str], runtime: dict[str, str], expected: str) -> None:
    problems = guard.mismatches(stub, runtime)
    assert len(problems) == 1
    assert problems[0].startswith(expected)


def test_main_fails_on_a_reworded_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stub = tmp_path / "vm.pyi"
    original = guard.STUB.read_text(encoding="utf-8")
    drifted = original.replace('"""A pop was attempted on an empty stack."""', '"""A pop hit an empty stack."""')
    assert drifted != original
    stub.write_text(drifted, encoding="utf-8")
    monkeypatch.setattr(guard, "STUB", stub)

    assert guard.main() == 1
    assert "StackUnderflow: vm.pyi docstring differs" in capsys.readouterr().err


def test_main_fails_on_a_stub_that_does_not_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stub = tmp_path / "vm.pyi"
    stub.write_text("class XqvmError(RuntimeError:\n", encoding="utf-8")
    monkeypatch.setattr(guard, "STUB", stub)

    assert guard.main() == 1
    assert "cannot read" in capsys.readouterr().err
