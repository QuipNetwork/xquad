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

"""
Problem container and action recording for the constraint programming DSL.

The Problem class accumulates declarations (inputs, model, loops,
operations, outputs) as an ordered action list. Calling compile()
delegates to the compiler module to emit three .xqasm programs.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from xqvm_py import XQMXDomain

from .expression import Expr, RegLoad, Types, coerce
from .symbols import InputRef, LoopVar, ModelRef, OutputRef, SampleRef, VecRef

# ---------------------------------------------------------------------------
# Register allocator
# ---------------------------------------------------------------------------


class _RegisterAllocator:
    """Simple incrementing register allocator."""

    def __init__(self, start: int = 0) -> None:
        self._next = start

    def alloc(self) -> int:
        """Allocate the next register."""
        if self._next > 255:
            raise RuntimeError("Register overflow: exceeded r255")
        reg = self._next
        self._next += 1
        return reg


# ---------------------------------------------------------------------------
# Action recording
# ---------------------------------------------------------------------------


@dataclass
class Action:
    """A single recorded declaration in the problem."""

    kind: str
    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Compiled output
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompiledPrograms:
    """Three compiled .xqasm program strings."""

    encoder: str
    verifier: str
    decoder: str


# ---------------------------------------------------------------------------
# Post-compilation verification
# ---------------------------------------------------------------------------

# xqcp output programs (not the bytecode verifier)
_PROGRAM_LABELS = ("encoder", "verifier", "decoder")


def _verify_compiled(programs: CompiledPrograms) -> None:
    """Run the bytecode verifier on each generated program.

    Silently skips verification if ``xqffi`` is not installed so that
    xqcp remains usable in source-only environments.
    """
    try:
        from xqffi.verifier import verify_source
    except ImportError:
        return

    for label in _PROGRAM_LABELS:
        source = getattr(programs, label)
        try:
            verify_source(source)
        except ValueError as exc:
            raise ValueError(f"{label} verification failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Problem
# ---------------------------------------------------------------------------


class Problem:
    """Container for a constraint programming problem description."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._alloc = _RegisterAllocator()
        self._actions: list[Action] = []
        self._constraints: list[Action] = []
        self._outputs: list[OutputRef] = []
        self._output_slot = 0
        self._model: ModelRef | None = None
        self._sample: SampleRef | None = None
        self._indent = 0
        self._rows_expr: Expr | None = None
        self._cols_expr: Expr | None = None

    @property
    def model(self) -> ModelRef:
        """The declared model reference."""
        if self._model is None:
            raise RuntimeError("No model defined — call define_model() first")
        return self._model

    @property
    def sample(self) -> SampleRef:
        """The implicit sample reference (counterpart of the model)."""
        if self._sample is None:
            raise RuntimeError("No model defined — call define_model() first")
        return self._sample

    def input(self, name: str, type: Types) -> InputRef:
        """Declare a runtime input. Returns a symbolic InputRef."""
        if self._model is not None:
            raise RuntimeError("input() must be called before define_model()")
        reg = self._alloc.alloc()
        ref = InputRef(reg, name, type)
        self._actions.append(Action("input", {"ref": ref}))
        return ref

    def define_model(
        self,
        size: Expr | int,
        domain: XQMXDomain,
        rows: Expr | int | None = None,
        cols: Expr | int | None = None,
    ) -> None:
        """Declare the XQMX model the encoder will build."""
        if domain == XQMXDomain.INTEGER:
            raise NotImplementedError("Integer domain (XQMX/XSMX) is not yet supported in the CP layer")

        if (rows is None) != (cols is None):
            raise ValueError(
                f"define_model() requires both rows= and cols= for a 2D model; got rows={rows!r} cols={cols!r}"
            )

        size_expr = coerce(size)
        is_2d = rows is not None and cols is not None
        cols_reg: int | None = None

        if is_2d:
            self._rows_expr = coerce(rows)
            self._cols_expr = coerce(cols)
            cols_reg = self._alloc.alloc()

        model_reg = self._alloc.alloc()
        self._model = ModelRef(self, model_reg, domain, cols_reg, is_2d)
        self._sample = SampleRef(model_reg + 100, is_2d)

        self._actions.append(
            Action(
                "define_model",
                {
                    "model_reg": model_reg,
                    "domain": domain,
                    "size_expr": size_expr,
                    "is_2d": is_2d,
                    "cols_reg": cols_reg,
                    "rows_expr": self._rows_expr,
                    "cols_expr": self._cols_expr,
                },
            )
        )

    @contextmanager
    def range(self, start: Expr | int, end: Expr | int) -> Iterator[LoopVar]:
        """Declare a RANGE loop. Yields a symbolic LoopVar."""
        reg = self._alloc.alloc()
        start_expr = coerce(start)
        end_expr = coerce(end)
        var = LoopVar(reg, f"v{reg}")

        self._actions.append(
            Action(
                "range_start",
                {
                    "var": var,
                    "start_expr": start_expr,
                    "end_expr": end_expr,
                },
            )
        )
        self._indent += 1
        try:
            yield var
        finally:
            self._indent -= 1
            self._actions.append(Action("range_end", {}))

    @contextmanager
    def iter(self, vec_input: InputRef, start: Expr | int, end: Expr | int) -> Iterator[tuple[LoopVar, LoopVar]]:
        """Declare an ITER loop over a vec. Yields (idx_var, val_var)."""
        idx_reg = self._alloc.alloc()
        val_reg = self._alloc.alloc()
        start_expr = coerce(start)
        end_expr = coerce(end)
        idx_var = LoopVar(idx_reg, f"idx{idx_reg}")
        val_var = LoopVar(val_reg, f"val{val_reg}")

        self._actions.append(
            Action(
                "iter_start",
                {
                    "vec_ref": vec_input,
                    "idx_var": idx_var,
                    "val_var": val_var,
                    "start_expr": start_expr,
                    "end_expr": end_expr,
                },
            )
        )
        self._indent += 1
        try:
            yield (idx_var, val_var)
        finally:
            self._indent -= 1
            self._actions.append(Action("iter_end", {}))

    def stow(self, target: str | RegLoad, expr: Expr | int) -> RegLoad:
        """Evaluate an expression and stow into a register.

        If target is a string, allocates a new register.
        If target is a RegLoad, reuses the existing register.
        """
        if isinstance(target, RegLoad):
            reg = target.reg
            name = f"r{reg}"
        else:
            reg = self._alloc.alloc()
            name = target
        self._actions.append(
            Action(
                "stow",
                {
                    "reg": reg,
                    "name": name,
                    "expr": coerce(expr),
                },
            )
        )
        return RegLoad(reg)

    def vec(self) -> VecRef:
        """Allocate an untyped vector register. Returns a symbolic VecRef."""
        reg = self._alloc.alloc()
        ref = VecRef(self, reg)
        self._actions.append(Action("vec_alloc", {"ref": ref}))
        return ref

    def slack(
        self,
        indices: VecRef,
        coeffs: VecRef,
        start_index: Expr | int,
        capacity: Expr | int,
    ) -> None:
        """Generate slack variable entries and append to index/coefficient vectors."""
        self._record_slack(indices, coeffs, start_index, capacity)

    def branch(self, *args: Any) -> None:
        """Multi-arm conditional branch.

        Args: (cond1, callable1, cond2, callable2, ..., default_callable_or_None)
        - Variadic (condition, callable) pairs
        - Final arg is the mandatory default (callable or None)
        - Minimum 3 args: (cond, callable, default)
        - First-match semantics
        """
        if len(args) < 3:
            raise ValueError("branch() requires at least 3 arguments: (cond, callable, default)")
        if len(args) % 2 == 0:
            raise ValueError("branch() requires odd number of arguments: (cond, callable, ..., default)")

        arms: list[dict[str, Any]] = []

        # Parse (condition, callable) pairs
        for i in range(0, len(args) - 1, 2):
            cond = coerce(args[i])
            body_fn: Callable[[], Any] | None = args[i + 1]
            body_actions = self._capture_actions(body_fn)
            arms.append({"condition": cond, "actions": body_actions})

        # Parse default arm
        default_fn: Callable[[], Any] | None = args[-1]
        default_actions = self._capture_actions(default_fn)
        arms.append({"condition": None, "actions": default_actions})

        self._actions.append(Action("branch", {"arms": arms}))

    def _capture_actions(self, fn: Callable[[], Any] | None) -> list[Action]:
        """Call fn while capturing any actions it records into a separate buffer."""
        if fn is None:
            return []
        saved = self._actions
        self._actions = []
        fn()
        captured = self._actions
        self._actions = saved
        return captured

    def output(self, name: str, type: Types = Types.Vec) -> OutputRef:
        """Declare a pipeline output. Returns a symbolic OutputRef."""
        if type != Types.Vec:
            raise TypeError(f"Output '{name}' must be Types.Vec; every pipeline output is a vector")
        reg = self._alloc.alloc()
        slot = self._output_slot
        self._output_slot += 1
        ref = OutputRef(self, slot, reg, name, type)
        self._outputs.append(ref)
        self._actions.append(Action("output_decl", {"ref": ref}))
        return ref

    # -- Internal recording methods called by ModelRef / OutputRef ----------

    def _record_add_linear(
        self,
        model: ModelRef,
        coord: Any,
        weight: Expr | int,
    ) -> None:
        self._actions.append(
            Action(
                "add_linear",
                {
                    "model": model,
                    "coord": coord,
                    "weight": coerce(weight),
                },
            )
        )

    def _record_add_quadratic(
        self,
        model: ModelRef,
        coord_a: Any,
        coord_b: Any,
        weight: Expr | int,
    ) -> None:
        self._actions.append(
            Action(
                "add_quadratic",
                {
                    "model": model,
                    "coord_a": coord_a,
                    "coord_b": coord_b,
                    "weight": coerce(weight),
                },
            )
        )

    def _record_set_linear(
        self,
        model: ModelRef,
        coord: Any,
        weight: Expr | int,
    ) -> None:
        self._actions.append(
            Action(
                "set_linear",
                {
                    "model": model,
                    "coord": coord,
                    "weight": coerce(weight),
                },
            )
        )

    def _record_set_quadratic(
        self,
        model: ModelRef,
        coord_a: Any,
        coord_b: Any,
        weight: Expr | int,
    ) -> None:
        self._actions.append(
            Action(
                "set_quadratic",
                {
                    "model": model,
                    "coord_a": coord_a,
                    "coord_b": coord_b,
                    "weight": coerce(weight),
                },
            )
        )

    def _record_onehot_row(
        self,
        model: ModelRef,
        row: Expr | int,
        penalty: int,
    ) -> None:
        action = Action(
            "onehot_row",
            {
                "model": model,
                "row": coerce(row),
                "penalty": penalty,
            },
        )
        self._actions.append(action)
        self._constraints.append(action)

    def _record_onehot_col(
        self,
        model: ModelRef,
        col: Expr | int,
        penalty: int,
    ) -> None:
        action = Action(
            "onehot_col",
            {
                "model": model,
                "col": coerce(col),
                "penalty": penalty,
            },
        )
        self._actions.append(action)
        self._constraints.append(action)

    def _record_exclude(
        self,
        model: ModelRef,
        coord_a: Any,
        coord_b: Any,
        penalty: int,
    ) -> None:
        action = Action(
            "exclude",
            {
                "model": model,
                "coord_a": coord_a,
                "coord_b": coord_b,
                "penalty": penalty,
            },
        )
        self._actions.append(action)
        self._constraints.append(action)

    def _record_implies(
        self,
        model: ModelRef,
        coord_a: Any,
        coord_b: Any,
        penalty: int,
    ) -> None:
        action = Action(
            "implies",
            {
                "model": model,
                "coord_a": coord_a,
                "coord_b": coord_b,
                "penalty": penalty,
            },
        )
        self._actions.append(action)
        self._constraints.append(action)

    def _record_equality(
        self,
        model: ModelRef,
        indices: VecRef,
        coeffs: VecRef,
        target: Expr | int,
        penalty: int,
    ) -> None:
        action = Action(
            "equality",
            {
                "model": model,
                "indices": indices,
                "coeffs": coeffs,
                "target": coerce(target),
                "penalty": penalty,
            },
        )
        self._actions.append(action)
        self._constraints.append(action)

    def _record_atleast(
        self,
        model: ModelRef,
        indices: VecRef,
        k: Expr | int,
        penalty: int,
    ) -> None:
        action = Action(
            "atleast",
            {
                "model": model,
                "indices": indices,
                "k": coerce(k),
                "penalty": penalty,
            },
        )
        self._actions.append(action)
        self._constraints.append(action)

    def _record_atleastw(
        self,
        model: ModelRef,
        indices: VecRef,
        coeffs: VecRef,
        k: Expr | int,
        penalty: int,
    ) -> None:
        action = Action(
            "atleastw",
            {
                "model": model,
                "indices": indices,
                "coeffs": coeffs,
                "k": coerce(k),
                "penalty": penalty,
            },
        )
        self._actions.append(action)
        self._constraints.append(action)

    def _record_reduce(
        self,
        model: ModelRef,
        var_a: Expr | int,
        var_b: Expr | int,
        p_aux: Expr | int,
        stow_reg: int,
    ) -> None:
        # REDUCE is not added to _constraints -- it is a structural
        # transformation, not a domain constraint.
        self._actions.append(
            Action(
                "reduce",
                {
                    "model": model,
                    "var_a": coerce(var_a),
                    "var_b": coerce(var_b),
                    "p_aux": coerce(p_aux),
                    "stow_reg": stow_reg,
                },
            )
        )

    def _record_inequality(
        self,
        model: ModelRef,
        indices: VecRef,
        coeffs: VecRef,
        target: Expr | int,
        capacity: Expr | int,
        penalty: int,
    ) -> None:
        action = Action(
            "inequality",
            {
                "model": model,
                "indices": indices,
                "coeffs": coeffs,
                "target": coerce(target),
                "capacity": coerce(capacity),
                "penalty": penalty,
            },
        )
        self._actions.append(action)
        self._constraints.append(action)

    def _record_vec_push(self, vec: VecRef, value: Expr | int) -> None:
        self._actions.append(
            Action(
                "vec_push",
                {
                    "vec": vec,
                    "value_expr": coerce(value),
                },
            )
        )

    def _record_slack(
        self,
        indices: VecRef,
        coeffs: VecRef,
        start_index: Expr | int,
        capacity: Expr | int,
    ) -> None:
        self._actions.append(
            Action(
                "slack",
                {
                    "indices": indices,
                    "coeffs": coeffs,
                    "start_index": coerce(start_index),
                    "capacity": coerce(capacity),
                },
            )
        )

    def _record_output_append(self, output: OutputRef, value_expr: Expr | int) -> None:
        self._actions.append(
            Action(
                "output_append",
                {
                    "output": output,
                    "value_expr": coerce(value_expr),
                },
            )
        )

    # -- Compilation --------------------------------------------------------

    def verifier_calldata(self) -> list[str]:
        """Name the verifier program's calldata slots, in order.

        The verifier replays the encoder, so it needs the encoder's own
        inputs -- in declaration order -- followed by the model and the
        sample.  A host can zip this against its own values rather than
        reconstructing the order from the problem definition.

        # Examples

        ```python
        problem = Problem("knapsack")
        n = problem.input("n", Types.Int)
        weights = problem.input("weights", Types.Vec)
        problem.define_model(size=n, domain=XQMXDomain.BINARY)

        assert problem.verifier_calldata() == ["n", "weights", "model", "sample"]
        ```
        """
        from .compiler import verifier_calldata_layout

        return verifier_calldata_layout(self)

    def compile(self) -> CompiledPrograms:
        """Compile the problem into three .xqasm program strings.

        After generating the three programs, runs the bytecode verifier
        (Phase 1 + Phase 2) on each one to catch code-generation bugs at
        compile time.  The verifier requires ``xqffi``; if that extension is
        not available the verification step is silently skipped.

        # Errors

        Raises ``ValueError`` if any generated program fails verification,
        with a message of the form
        ``"<program> verification failed: <VerifierError>"``.
        """
        from .compiler import compile_decoder, compile_encoder, compile_verifier

        programs = CompiledPrograms(
            encoder=compile_encoder(self),
            verifier=compile_verifier(self),
            decoder=compile_decoder(self),
        )
        _verify_compiled(programs)
        return programs
