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
Compiler for the constraint programming DSL.

Generates three .xqasm program strings (encoder, verifier, decoder)
from a Problem's recorded action list.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from xqvm_py import XQMXDomain

from .expression import (
    BinOp,
    BitLenExpr,
    ColFindExpr,
    ColSumExpr,
    CompareOp,
    Expr,
    GetLineExpr,
    GetQuadExpr,
    GridExpr,
    Literal,
    RegLoad,
    RowFindExpr,
    RowSumExpr,
    SqrExpr,
    TriuExpr,
    Types,
    UnaryOp,
    VecGetExpr,
    coerce,
    emit_flat_index,
    expr_reg,
    fmt_int,
    line,
    resolve_coord,
)
from .symbols import CoefficientRef, InputRef, LoopVar, ModelRef, OutputRef, VecRef

if TYPE_CHECKING:
    from .problem import Action, Problem

# ---------------------------------------------------------------------------
# Target ID allocator (for branch compilation)
# ---------------------------------------------------------------------------


class _TargetAllocator:
    """Allocates sequential target label IDs for JUMP/JUMPI/TARGET."""

    def __init__(self, start: int = 0) -> None:
        self._next = start

    def alloc(self) -> int:
        tid = self._next
        self._next += 1
        return tid


# ---------------------------------------------------------------------------
# Emission mode
# ---------------------------------------------------------------------------

_OpEmitter = Callable[[dict[str, Any], list[str], int], None]


class _EmitMode:
    """Per-target emission policy for the shared body walker.

    ``ops`` maps a model-touching action kind to the emitter that handles
    it; a kind absent from the table is skipped entirely, which is how the
    verifier drops the encoder's model mutations.  ``xf`` rewrites every
    expression before it is emitted -- the identity for the encoder, and
    the sample-sentinel rewrite plus guard pass for the verifier.
    """

    def __init__(
        self,
        ops: dict[str, _OpEmitter],
        xf: Callable[[Expr], Expr] | None = None,
    ) -> None:
        self.ops = ops
        self._xf = xf

    def xf(self, expr: Expr) -> Expr:
        """Rewrite an expression for this target."""
        return expr if self._xf is None else self._xf(expr)


# ---------------------------------------------------------------------------
# Compiler: encoder
# ---------------------------------------------------------------------------


def _partition_program(
    actions: list[Action],
) -> tuple[list[Action], Action | None, list[Action]]:
    """Split a problem's action list into inputs, the model, and the body.

    Output-section actions are dropped: neither the encoder nor the
    verifier emits them.
    """
    input_actions: list[Action] = []
    model_action: Action | None = None
    body_actions: list[Action] = []

    in_output_section = False
    for action in actions:
        if action.kind == "input":
            input_actions.append(action)
        elif action.kind == "define_model":
            model_action = action
        elif action.kind == "output_decl":
            in_output_section = True
        elif not in_output_section:
            body_actions.append(action)

    return input_actions, model_action, body_actions


def compile_encoder(prob: Problem) -> str:
    """Generate the encoder .xqasm program."""
    lines: list[str] = []
    indent = 0
    targets = _TargetAllocator()

    input_actions, model_action, body_actions = _partition_program(prob._actions)

    # --- Inputs ---
    lines.append("; === Inputs ===")
    for action in input_actions:
        ref: InputRef = action.data["ref"]
        lines.append(f"PUSH {ref.reg}")
        lines.append(f"INPUT r{ref.reg}")

    # --- Allocations ---
    lines.append("")
    lines.append("; === Allocations ===")
    if model_action is not None:
        _emit_model_allocation(model_action.data, lines, indent)

    # --- Body (objective + constraints) ---
    obj_actions, con_actions = _partition_body(body_actions)

    if obj_actions:
        lines.append("")
        lines.append("; === Objective ===")
        _emit_body_actions(obj_actions, lines, 0, targets)

    if con_actions:
        lines.append("")
        lines.append("; === Constraints ===")
        _emit_body_actions(con_actions, lines, 0, targets)

    # --- Output ---
    lines.append("")
    lines.append("; === Output ===")
    lines.append("PUSH 0")
    if model_action is not None:
        lines.append(f"OUTPUT r{model_action.data['model_reg']}")
    lines.append("HALT")

    return "\n".join(lines) + "\n"


def _emit_size_expr(size_expr: Expr, lines: list[str], indent: int) -> None:
    """Emit a model's size expression, collapsing ``N * N`` to LOAD + SQR."""
    if isinstance(size_expr, BinOp) and size_expr.op == "MUL":
        left_reg = expr_reg(size_expr.left) or _input_reg(size_expr.left)
        right_reg = expr_reg(size_expr.right) or _input_reg(size_expr.right)

        if left_reg is not None and left_reg == right_reg:
            lines.append(line(f"LOAD r{left_reg}", indent))
            lines.append(line("SQR", indent))
            return

    size_expr.emit(lines, indent)


def _emit_model_allocation(d: dict[str, Any], lines: list[str], indent: int) -> None:
    """Emit model size computation, allocation, and optional grid resize."""
    size_expr: Expr = d["size_expr"]
    model_reg: int = d["model_reg"]
    domain: XQMXDomain = d["domain"]
    is_2d: bool = d["is_2d"]
    cols_reg: int | None = d["cols_reg"]

    _emit_size_expr(size_expr, lines, indent)

    opcode = "BQMX" if domain == XQMXDomain.BINARY else "SQMX"
    lines.append(line(f"{opcode} r{model_reg}", indent))

    if is_2d:
        rows_expr: Expr = d["rows_expr"]
        cols_expr_val: Expr = d["cols_expr"]

        cols_expr_val.emit(lines, indent)
        lines.append(line(f"STOW r{cols_reg}", indent))

        rows_expr.emit(lines, indent)
        lines.append(line(f"LOAD r{cols_reg}", indent))
        lines.append(line(f"RESIZE r{model_reg}", indent))


def _input_reg(expr: Expr) -> int | None:
    """Extract register from an InputRef expression."""
    if isinstance(expr, InputRef):
        return expr.reg
    return None


def _partition_body(body_actions: list[Action]) -> tuple[list[Action], list[Action]]:
    """Split body actions into objective and constraint sections by top-level block."""
    constraint_kinds = {
        "onehot_row",
        "onehot_col",
        "exclude",
        "implies",
        "equality",
        "atleast",
        "atleastw",
        "inequality",
    }
    blocks: list[list[Action]] = []
    current_block: list[Action] = []
    depth = 0

    for action in body_actions:
        current_block.append(action)
        if action.kind in ("range_start", "iter_start"):
            depth += 1
        elif action.kind in ("range_end", "iter_end"):
            depth -= 1
            if depth == 0:
                blocks.append(current_block)
                current_block = []
        elif depth == 0:
            blocks.append(current_block)
            current_block = []

    if current_block:
        blocks.append(current_block)

    obj_actions: list[Action] = []
    con_actions: list[Action] = []
    for block in blocks:
        has_constraint = any(a.kind in constraint_kinds for a in block)
        if has_constraint:
            con_actions.extend(block)
        else:
            obj_actions.extend(block)

    return obj_actions, con_actions


def _emit_body_actions(
    actions: list[Action],
    lines: list[str],
    indent: int,
    targets: _TargetAllocator,
    mode: _EmitMode | None = None,
) -> None:
    """Emit a sequence of body actions (loops, operations) as assembly.

    Structural actions -- loops, stows, vector construction, branches --
    emit identically for every target, so replaying them in the verifier
    reconstructs exactly the register and vector state the encoder had.
    Model-touching actions dispatch through ``mode.ops``.
    """
    if mode is None:
        mode = _ENCODER_MODE

    for action in actions:
        kind = action.kind
        d = action.data

        if kind == "range_start":
            _emit_range_start(d, lines, indent, mode)
            indent += 1

        elif kind == "range_end":
            indent -= 1
            lines.append(line("NEXT", indent))

        elif kind == "iter_start":
            _emit_iter_start(d, lines, indent, mode)
            indent += 1

        elif kind == "iter_end":
            indent -= 1
            lines.append(line("NEXT", indent))

        elif kind == "stow":
            mode.xf(d["expr"]).emit(lines, indent)
            lines.append(line(f"STOW r{d['reg']}", indent))

        elif kind == "vec_alloc":
            ref: VecRef = d["ref"]
            lines.append(line(f"VEC r{ref.reg}", indent))

        elif kind == "vec_push":
            mode.xf(d["value_expr"]).emit(lines, indent)
            lines.append(line(f"VECPUSH r{d['vec'].reg}", indent))

        elif kind == "branch":
            _emit_branch(d, lines, indent, targets, mode)

        else:
            emitter = mode.ops.get(kind)
            if emitter is not None:
                emitter(d, lines, indent)


def _emit_range_start(d: dict[str, Any], lines: list[str], indent: int, mode: _EmitMode) -> None:
    """Emit RANGE preamble: start value and count."""
    var: LoopVar = d["var"]
    start_expr: Expr = mode.xf(d["start_expr"])
    end_expr: Expr = mode.xf(d["end_expr"])

    start_expr.emit(lines, indent)

    if isinstance(start_expr, Literal) and start_expr.value == 0:
        end_expr.emit(lines, indent)
    else:
        end_expr.emit(lines, indent)
        start_expr.emit(lines, indent)
        lines.append(line("SUB", indent))

    lines.append(line("RANGE", indent))
    lines.append(line(f"LVAL r{var.reg}", indent + 1))


def _emit_iter_start(d: dict[str, Any], lines: list[str], indent: int, mode: _EmitMode) -> None:
    """Emit ITER preamble: start_idx, end_idx, ITER, LIDX, LVAL."""
    vec_ref: InputRef = d["vec_ref"]
    idx_var: LoopVar = d["idx_var"]
    val_var: LoopVar = d["val_var"]

    mode.xf(d["start_expr"]).emit(lines, indent)
    mode.xf(d["end_expr"]).emit(lines, indent)
    lines.append(line(f"ITER r{vec_ref.reg}", indent))
    lines.append(line(f"LIDX r{idx_var.reg}", indent + 1))
    lines.append(line(f"LVAL r{val_var.reg}", indent + 1))


# ---------------------------------------------------------------------------
# Encoder: model-touching emitters
# ---------------------------------------------------------------------------


def _emit_onehot_row(d: dict[str, Any], lines: list[str], indent: int) -> None:
    """Emit ONEHOTR instruction."""
    d["row"].emit(lines, indent)
    lines.append(line(f"PUSH {fmt_int(d['penalty'])}", indent))
    lines.append(line(f"ONEHOTR r{d['model'].reg}", indent))


def _emit_onehot_col(d: dict[str, Any], lines: list[str], indent: int) -> None:
    """Emit ONEHOTC instruction."""
    d["col"].emit(lines, indent)
    lines.append(line(f"PUSH {fmt_int(d['penalty'])}", indent))
    lines.append(line(f"ONEHOTC r{d['model'].reg}", indent))


def _emit_slack(d: dict[str, Any], lines: list[str], indent: int) -> None:
    """Emit SLACK instruction appending slack entries to both vecs."""
    d["start_index"].emit(lines, indent)
    d["capacity"].emit(lines, indent)
    lines.append(line(f"SLACK r{d['indices'].reg} r{d['coeffs'].reg}", indent))


def _emit_equality(d: dict[str, Any], lines: list[str], indent: int) -> None:
    """Emit EQUALITY instruction."""
    d["target"].emit(lines, indent)
    lines.append(line(f"PUSH {fmt_int(d['penalty'])}", indent))
    lines.append(line(f"EQUALITY r{d['model'].reg} r{d['indices'].reg} r{d['coeffs'].reg}", indent))


def _emit_atleast(d: dict[str, Any], lines: list[str], indent: int) -> None:
    """Emit ATLEAST instruction."""
    d["k"].emit(lines, indent)
    lines.append(line(f"PUSH {fmt_int(d['penalty'])}", indent))
    lines.append(line(f"ATLEAST r{d['model'].reg} r{d['indices'].reg}", indent))


def _emit_atleastw(d: dict[str, Any], lines: list[str], indent: int) -> None:
    """Emit ATLEASTW instruction."""
    d["k"].emit(lines, indent)
    lines.append(line(f"PUSH {fmt_int(d['penalty'])}", indent))
    lines.append(line(f"ATLEASTW r{d['model'].reg} r{d['indices'].reg} r{d['coeffs'].reg}", indent))


def _emit_reduce(d: dict[str, Any], lines: list[str], indent: int) -> None:
    """Emit REDUCE instruction and stow the allocated auxiliary index."""
    d["var_a"].emit(lines, indent)
    d["var_b"].emit(lines, indent)
    d["p_aux"].emit(lines, indent)
    lines.append(line(f"REDUCE r{d['model'].reg}", indent))
    lines.append(line(f"STOW r{d['stow_reg']}", indent))


def _emit_inequality(d: dict[str, Any], lines: list[str], indent: int) -> None:
    """Emit the composed SLACK + EQUALITY pair for an inequality.

    ``target`` is the slack start index; ``capacity`` is both the slack
    bound and the equality target.
    """
    d["target"].emit(lines, indent)
    d["capacity"].emit(lines, indent)
    lines.append(line(f"SLACK r{d['indices'].reg} r{d['coeffs'].reg}", indent))
    d["capacity"].emit(lines, indent)
    lines.append(line(f"PUSH {fmt_int(d['penalty'])}", indent))
    lines.append(line(f"EQUALITY r{d['model'].reg} r{d['indices'].reg} r{d['coeffs'].reg}", indent))


def _emit_add_linear(d: dict[str, Any], lines: list[str], indent: int) -> None:
    """Emit ADDLINE instruction with coordinate and weight."""
    model: ModelRef = d["model"]

    if model.is_2d:
        row_expr, col_expr = resolve_coord(d["coord"])
        emit_flat_index(row_expr, col_expr, model.cols_reg, lines, indent)
    else:
        coerce(d["coord"]).emit(lines, indent)

    d["weight"].emit(lines, indent)
    lines.append(line(f"ADDLINE r{model.reg}", indent))


def _emit_add_quadratic(d: dict[str, Any], lines: list[str], indent: int) -> None:
    """Emit ADDQUAD instruction with two coordinates and weight."""
    model: ModelRef = d["model"]

    if model.is_2d:
        row_a, col_a = resolve_coord(d["coord_a"])
        emit_flat_index(row_a, col_a, model.cols_reg, lines, indent)
    else:
        coerce(d["coord_a"]).emit(lines, indent)

    if model.is_2d:
        row_b, col_b = resolve_coord(d["coord_b"])
        emit_flat_index(row_b, col_b, model.cols_reg, lines, indent)
    else:
        coerce(d["coord_b"]).emit(lines, indent)

    d["weight"].emit(lines, indent)
    lines.append(line(f"ADDQUAD r{model.reg}", indent))


def _emit_set_linear(d: dict[str, Any], lines: list[str], indent: int) -> None:
    """Emit SETLINE instruction with coordinate and weight."""
    model: ModelRef = d["model"]

    if model.is_2d:
        row_expr, col_expr = resolve_coord(d["coord"])
        emit_flat_index(row_expr, col_expr, model.cols_reg, lines, indent)
    else:
        coerce(d["coord"]).emit(lines, indent)

    d["weight"].emit(lines, indent)
    lines.append(line(f"SETLINE r{model.reg}", indent))


def _emit_set_quadratic(d: dict[str, Any], lines: list[str], indent: int) -> None:
    """Emit SETQUAD instruction with two coordinates and weight."""
    model: ModelRef = d["model"]

    if model.is_2d:
        row_a, col_a = resolve_coord(d["coord_a"])
        emit_flat_index(row_a, col_a, model.cols_reg, lines, indent)
    else:
        coerce(d["coord_a"]).emit(lines, indent)

    if model.is_2d:
        row_b, col_b = resolve_coord(d["coord_b"])
        emit_flat_index(row_b, col_b, model.cols_reg, lines, indent)
    else:
        coerce(d["coord_b"]).emit(lines, indent)

    d["weight"].emit(lines, indent)
    lines.append(line(f"SETQUAD r{model.reg}", indent))


def _emit_exclude(d: dict[str, Any], lines: list[str], indent: int) -> None:
    """Emit EXCLUDE instruction."""
    model: ModelRef = d["model"]

    if model.is_2d:
        row_a, col_a = resolve_coord(d["coord_a"])
        emit_flat_index(row_a, col_a, model.cols_reg, lines, indent)
    else:
        coerce(d["coord_a"]).emit(lines, indent)

    if model.is_2d:
        row_b, col_b = resolve_coord(d["coord_b"])
        emit_flat_index(row_b, col_b, model.cols_reg, lines, indent)
    else:
        coerce(d["coord_b"]).emit(lines, indent)

    lines.append(line(f"PUSH {fmt_int(d['penalty'])}", indent))
    lines.append(line(f"EXCLUDE r{model.reg}", indent))


def _emit_implies(d: dict[str, Any], lines: list[str], indent: int) -> None:
    """Emit IMPLIES instruction."""
    model: ModelRef = d["model"]

    if model.is_2d:
        row_a, col_a = resolve_coord(d["coord_a"])
        emit_flat_index(row_a, col_a, model.cols_reg, lines, indent)
    else:
        coerce(d["coord_a"]).emit(lines, indent)

    if model.is_2d:
        row_b, col_b = resolve_coord(d["coord_b"])
        emit_flat_index(row_b, col_b, model.cols_reg, lines, indent)
    else:
        coerce(d["coord_b"]).emit(lines, indent)

    lines.append(line(f"PUSH {fmt_int(d['penalty'])}", indent))
    lines.append(line(f"IMPLIES r{model.reg}", indent))


def _emit_branch(
    d: dict[str, Any],
    lines: list[str],
    indent: int,
    targets: _TargetAllocator,
    mode: _EmitMode,
) -> None:
    """Emit a multi-arm branch as JUMPI/JUMP/TARGET chain."""
    arms: list[dict[str, Any]] = d["arms"]
    end_target = targets.alloc()

    for arm in arms:
        cond = arm["condition"]
        arm_actions = arm["actions"]

        if cond is None:
            # Default arm — emit body directly
            if arm_actions:
                _emit_body_actions(arm_actions, lines, indent, targets, mode)
        else:
            # Conditional arm — emit condition, skip if false
            skip_target = targets.alloc()
            mode.xf(cond).emit(lines, indent)
            lines.append(line("NOT", indent))
            lines.append(line(f"JUMPI .{skip_target}", indent))

            if arm_actions:
                _emit_body_actions(arm_actions, lines, indent + 1, targets, mode)

            lines.append(line(f"JUMP .{end_target}", indent + 1))
            lines.append(line(f"TARGET .{skip_target}", indent))

    lines.append(line(f"TARGET .{end_target}", indent))


_ENCODER_OPS: dict[str, _OpEmitter] = {
    "add_linear": _emit_add_linear,
    "add_quadratic": _emit_add_quadratic,
    "set_linear": _emit_set_linear,
    "set_quadratic": _emit_set_quadratic,
    "onehot_row": _emit_onehot_row,
    "onehot_col": _emit_onehot_col,
    "exclude": _emit_exclude,
    "implies": _emit_implies,
    "slack": _emit_slack,
    "equality": _emit_equality,
    "atleast": _emit_atleast,
    "atleastw": _emit_atleastw,
    "reduce": _emit_reduce,
    "inequality": _emit_inequality,
}

_ENCODER_MODE = _EmitMode(_ENCODER_OPS)


# ---------------------------------------------------------------------------
# Compiler: verifier
# ---------------------------------------------------------------------------


# Registers the verifier claims above the encoder's high-water mark.
_VERIFIER_REG_COUNT = 8

# Constraint kinds whose user-level meaning is defined only over 0/1 variables.
_BINARY_ONLY_KINDS = ("onehot_row", "onehot_col", "exclude", "implies")

# Constraint kinds that append variables past the model's declared size.
_GROWING_KINDS = ("atleast", "atleastw", "inequality")

_MODEL_READ_ERROR = (
    "xqcp: reading a model coefficient (model.linear[...] or "
    "model.quadratic[...]) outside an assignment is not supported when a "
    "verifier is generated: the encoder reads a partially built model where "
    "the verifier reads the finished one, so the two programs would silently "
    "disagree"
)


def compile_verifier(prob: Problem) -> str:
    """Generate the verifier .xqasm program.

    The verifier replays the encoder's action stream -- inputs, loops,
    stows, branches and vector construction -- so that its register and
    vector state at each constraint site is identical to the encoder's.
    Model mutations are dropped; each constraint is replaced in place by a
    check of the solver's sample against the constraint the user wrote.

    Calldata is the encoder's inputs in declaration order, followed by the
    model and then the sample.  Outputs are the sample's energy in slot 0
    and the validity flag in slot 1.

    # Errors

    Raises ``RuntimeError`` if the problem has no model, if the checks
    would not fit in the register file, if a model coefficient is read as
    an expression, if a binary-only constraint is applied to a non-binary
    model, if a ``slack()`` and the equality consuming it sit in different
    scopes, or if a model-growing constraint could execute before a
    ``reduce()``.
    """
    input_actions, model_action, body_actions = _partition_program(prob._actions)
    if model_action is None:
        raise RuntimeError("xqcp: cannot generate a verifier for a problem with no model")

    base = prob._alloc._next
    if base + _VERIFIER_REG_COUNT - 1 > 255:
        raise RuntimeError(
            f"xqcp: the verifier needs {_VERIFIER_REG_COUNT} registers above r{base - 1}, which would pass r255"
        )

    # Analyse in the order the programs execute, not source order:
    # _partition_body hoists every objective block above every constraint
    # block, and both compilers emit the two sections in that order.
    obj_actions, con_actions = _partition_body(body_actions)
    replay_actions = obj_actions + con_actions

    slack_equalities = _scan_slack_equalities(replay_actions)
    _check_reduce_ordering(replay_actions, slack_equalities)

    emitter = _VerifierEmitter(model_action.data, base, slack_equalities)
    emitter.reject_unsupported(replay_actions)

    lines: list[str] = []
    targets = _TargetAllocator()

    # --- Inputs: the encoder's own, then the model and the sample ---
    lines.append("; === Inputs ===")
    for action in input_actions:
        ref: InputRef = action.data["ref"]
        lines.append(f"PUSH {ref.reg}")
        lines.append(f"INPUT r{ref.reg}")

    model_slot = len(input_actions)
    lines.append(f"PUSH {model_slot}")
    lines.append(f"INPUT r{emitter.model_reg}")
    lines.append(f"PUSH {model_slot + 1}")
    lines.append(f"INPUT r{emitter.sample_reg}")

    # --- Model shape: replayed, never allocated ---
    lines.append("")
    lines.append("; === Model shape ===")
    _emit_size_expr(model_action.data["size_expr"], lines, 0)
    lines.append(f"STOW r{emitter.size_reg}")
    if model_action.data["is_2d"]:
        model_action.data["cols_expr"].emit(lines, 0)
        lines.append(f"STOW r{model_action.data['cols_reg']}")

    # --- Validity checks ---
    lines.append("")
    lines.append("; === Validity checks ===")
    lines.append("PUSH 1")
    lines.append(f"STOW r{emitter.valid_reg}")
    if any(a.kind == "reduce" for a, _ in _walk_scoped(replay_actions, (), [0])):
        lines.append("; REDUCE auxiliaries start at the model's declared size")
        lines.append(f"LOAD r{emitter.size_reg}")
        lines.append(f"STOW r{emitter.aux_reg}")

    lines.append("")
    lines.append("; Check every declared variable is in the model's domain")
    emitter.emit_domain_check(lines, 0)

    # --- Body replay, in the encoder's order ---
    if obj_actions:
        lines.append("")
        lines.append("; === Objective (replayed for state, not for energy) ===")
        _emit_body_actions(obj_actions, lines, 0, targets, emitter.mode)

    if con_actions:
        lines.append("")
        lines.append("; === Constraints ===")
        _emit_body_actions(con_actions, lines, 0, targets, emitter.mode)

    # --- Energy ---
    lines.append("")
    lines.append("; === Energy ===")
    lines.append(f"ENERGY r{emitter.model_reg} r{emitter.sample_reg}")
    lines.append(f"STOW r{emitter.energy_reg}")

    # --- Output ---
    lines.append("")
    lines.append("; === Output ===")
    lines.append("PUSH 0")
    lines.append(f"OUTPUT r{emitter.energy_reg}")
    lines.append("PUSH 1")
    lines.append(f"OUTPUT r{emitter.valid_reg}")
    lines.append("HALT")

    return "\n".join(lines) + "\n"


def verifier_calldata_layout(prob: Problem) -> list[str]:
    """Name the verifier's calldata slots, in order.

    Hosts pass the encoder's own inputs in declaration order, then the
    model, then the sample.  This returns the corresponding names so a
    caller does not have to reconstruct the order by hand.
    """
    input_actions, _, _ = _partition_program(prob._actions)
    return [a.data["ref"].name for a in input_actions] + ["model", "sample"]


# ---------------------------------------------------------------------------
# Verifier: action-stream analysis
# ---------------------------------------------------------------------------


def _walk_scoped(
    actions: list[Action],
    scope: tuple[int, ...],
    counter: list[int],
) -> Any:
    """Yield ``(action, scope)`` for every leaf action, in execution order.

    ``scope`` identifies the chain of enclosing loops and branch arms, so
    two actions share an enclosing block exactly when their scopes share a
    non-empty prefix.
    """
    stack: list[tuple[int, ...]] = [scope]

    for action in actions:
        kind = action.kind

        if kind in ("range_end", "iter_end"):
            if len(stack) > 1:
                stack.pop()
            continue

        current = stack[-1]

        if kind in ("range_start", "iter_start"):
            counter[0] += 1
            stack.append((*current, counter[0]))
            continue

        if kind == "branch":
            for arm in action.data["arms"]:
                counter[0] += 1
                yield from _walk_scoped(arm["actions"], (*current, counter[0]), counter)
            continue

        yield action, current


def _shares_block(a: tuple[int, ...], b: tuple[int, ...]) -> bool:
    """Whether two scopes sit inside a common loop or branch arm."""
    return bool(a) and bool(b) and a[0] == b[0]


def _scan_slack_equalities(body_actions: list[Action]) -> set[int]:
    """Identify equalities whose vectors carry SLACK-appended entries.

    Those check as ``sum <= target`` over the real variables only; the
    slack action itself is not replayed, so the vectors the verifier
    builds hold no slack entries.  A pair is live from its ``slack()``
    until the next ``vec()`` on either register -- never cleared on
    consume, because two equalities over one slack-extended pair are both
    inequalities in the encoder.

    ``inequality()`` seeds the same liveness.  ``_emit_inequality``
    composes a real ``SLACK`` over the pair before its ``EQUALITY``, so it
    leaves the vectors extended exactly as a bare ``slack()`` would, and an
    ``apply_equality()`` over the same registers afterwards is an
    inequality too.  ``_GROWING_KINDS`` already groups the two kinds; this
    scan is where they had come apart.

    # Errors

    Raises ``RuntimeError`` if the ``slack()`` and the equality consuming
    it sit in different loop or branch scopes.
    """
    live: dict[tuple[int, int], tuple[int, ...]] = {}
    slack_equalities: set[int] = set()

    for action, scope in _walk_scoped(body_actions, (), [0]):
        d = action.data
        kind = action.kind

        if kind == "vec_alloc":
            reg = d["ref"].reg
            for key in [k for k in live if reg in k]:
                del live[key]

        elif kind in ("slack", "inequality"):
            live[(d["indices"].reg, d["coeffs"].reg)] = scope

        elif kind == "equality":
            key = (d["indices"].reg, d["coeffs"].reg)
            slack_scope = live.get(key)
            if slack_scope is None:
                continue
            if slack_scope != scope:
                raise RuntimeError(
                    "xqcp: slack() and the apply_equality() consuming it must sit in "
                    "the same loop or branch scope; the verifier cannot otherwise tell "
                    "which entries of the index vector are slack variables"
                )
            slack_equalities.add(id(d))

    return slack_equalities


def _check_reduce_ordering(body_actions: list[Action], slack_equalities: set[int]) -> None:
    """Reject a problem where a growing constraint can precede a REDUCE.

    The verifier tracks REDUCE's auxiliary index in a shadow counter
    seeded from the model's declared size.  That counter is only correct
    while nothing else grows the model first.

    # Errors

    Raises ``RuntimeError`` naming the offending constraint.
    """
    reduces: list[tuple[int, tuple[int, ...]]] = []
    growing: list[tuple[int, str, tuple[int, ...]]] = []

    for position, (action, scope) in enumerate(_walk_scoped(body_actions, (), [0])):
        kind = action.kind
        if kind == "reduce":
            reduces.append((position, scope))
        elif kind in _GROWING_KINDS or (kind == "equality" and id(action.data) in slack_equalities):
            growing.append((position, kind, scope))

    if not reduces:
        return

    for position, kind, scope in growing:
        for reduce_position, reduce_scope in reduces:
            if position < reduce_position or _shares_block(scope, reduce_scope):
                raise RuntimeError(
                    f"xqcp: a '{kind}' constraint grows the model before or alongside a "
                    "reduce(), so the verifier cannot track which variable each REDUCE "
                    "allocated; move every growing constraint after all reduce() calls "
                    "and out of any block they share"
                )


# ---------------------------------------------------------------------------
# Verifier: expression rewriting
# ---------------------------------------------------------------------------


def _rewrite_expr(expr: Expr, sentinel: int, sample_reg: int, model_reg: int) -> Expr:
    """Rebuild an expression for the verifier's register file.

    Reads through the ``problem.sample`` sentinel are retargeted at the
    verifier's own sample register.  Everything else is returned
    structurally unchanged.

    # Errors

    Raises ``RuntimeError`` if the expression reads a model coefficient.
    """

    def rw(inner: Expr) -> Expr:
        return _rewrite_expr(inner, sentinel, sample_reg, model_reg)

    def retarget(reg: int) -> int:
        if reg == model_reg:
            raise RuntimeError(_MODEL_READ_ERROR)
        return sample_reg if reg == sentinel else reg

    if isinstance(expr, CoefficientRef):
        raise RuntimeError(_MODEL_READ_ERROR)
    if isinstance(expr, GetQuadExpr):
        if expr.model_reg == model_reg:
            raise RuntimeError(_MODEL_READ_ERROR)
        return GetQuadExpr(expr.model_reg, rw(expr.i_expr), rw(expr.j_expr))
    if isinstance(expr, GetLineExpr):
        return GetLineExpr(retarget(expr.sample_reg), rw(expr.index_expr))
    if isinstance(expr, RowSumExpr):
        return RowSumExpr(retarget(expr.sample_reg), rw(expr.row_expr))
    if isinstance(expr, ColSumExpr):
        return ColSumExpr(retarget(expr.sample_reg), rw(expr.col_expr))
    if isinstance(expr, RowFindExpr):
        return RowFindExpr(retarget(expr.sample_reg), rw(expr.row_expr), expr.value)
    if isinstance(expr, ColFindExpr):
        return ColFindExpr(retarget(expr.sample_reg), rw(expr.col_expr), expr.value)
    if isinstance(expr, BinOp):
        return BinOp(expr.op, rw(expr.left), rw(expr.right))
    if isinstance(expr, CompareOp):
        return CompareOp(expr.op, rw(expr.left), rw(expr.right))
    if isinstance(expr, UnaryOp):
        return UnaryOp(expr.op, rw(expr.inner))
    if isinstance(expr, SqrExpr):
        return SqrExpr(rw(expr.inner))
    if isinstance(expr, BitLenExpr):
        return BitLenExpr(rw(expr.inner))
    if isinstance(expr, GridExpr):
        return GridExpr(rw(expr.row_expr), rw(expr.col_expr), rw(expr.cols_expr))
    if isinstance(expr, TriuExpr):
        return TriuExpr(rw(expr.i_expr), rw(expr.j_expr))
    if isinstance(expr, VecGetExpr):
        return VecGetExpr(expr.vec_reg, rw(expr.index_expr))

    # Literal, RegLoad, InputRef, LoopVar, VecLenExpr: no sub-expressions.
    return expr


# ---------------------------------------------------------------------------
# Verifier: constraint check emitters
# ---------------------------------------------------------------------------


class _VerifierEmitter:
    """Emits a sample check in place of each of the encoder's constraints.

    Every emitter is net-zero on the stack and initialises its accumulator
    before the loop that feeds it, so the bytecode verifier accepts a
    check emitted inside a loop body or a branch arm.
    """

    def __init__(self, model_data: dict[str, Any], base: int, slack_equalities: set[int]) -> None:
        self.model_reg: int = model_data["model_reg"]
        self.domain: XQMXDomain = model_data["domain"]
        self.sentinel = self.model_reg + 100
        self.slack_equalities = slack_equalities

        self.sample_reg = base
        self.valid_reg = base + 1
        self.energy_reg = base + 2
        self.size_reg = base + 3
        self.acc_reg = base + 4
        self.pos_reg = base + 5
        self.elem_reg = base + 6
        self.aux_reg = base + 7

        self.mode = _EmitMode(
            {
                "onehot_row": self._check_onehot_row,
                "onehot_col": self._check_onehot_col,
                "exclude": self._check_exclude,
                "implies": self._check_implies,
                "equality": self._check_equality,
                "inequality": self._check_inequality,
                "atleast": self._check_atleast,
                "atleastw": self._check_atleastw,
                "reduce": self._check_reduce,
            },
            xf=self.xf,
        )

    # -- helpers ---------------------------------------------------------

    def xf(self, expr: Expr) -> Expr:
        """Rewrite an expression for the verifier's register file."""
        return _rewrite_expr(expr, self.sentinel, self.sample_reg, self.model_reg)

    def reject_unsupported(self, body_actions: list[Action]) -> None:
        """Reject constraints whose user-level meaning is binary-only.

        # Errors

        Raises ``RuntimeError`` if a binary-only constraint is applied to a
        model whose domain is not ``BINARY``.
        """
        if self.domain == XQMXDomain.BINARY:
            return
        for action, _ in _walk_scoped(body_actions, (), [0]):
            if action.kind in _BINARY_ONLY_KINDS:
                raise RuntimeError(
                    f"xqcp: '{action.kind}' has no checkable meaning on a "
                    f"{self.domain.name.lower()} model, whose variables are not 0/1"
                )

    def _index(self, coord: Any, model: ModelRef, lines: list[str], indent: int) -> None:
        """Push a variable's flat index, resolving a 2D coordinate."""
        if model.is_2d:
            row_expr, col_expr = resolve_coord(coord)
            emit_flat_index(self.xf(row_expr), self.xf(col_expr), model.cols_reg, lines, indent)
        else:
            self.xf(coerce(coord)).emit(lines, indent)

    def _commit(self, lines: list[str], indent: int) -> None:
        """AND the boolean on top of the stack into the validity flag."""
        lines.append(line(f"LOAD r{self.valid_reg}", indent))
        lines.append(line("AND", indent))
        lines.append(line(f"STOW r{self.valid_reg}", indent))

    def _sum_into_acc(
        self,
        indices_reg: int,
        coeffs_reg: int | None,
        lines: list[str],
        indent: int,
    ) -> None:
        """Accumulate ``sum(coeffs[k] * sample[indices[k]])`` into the accumulator.

        With ``coeffs_reg`` unset the weights are all 1.  An empty index
        vector leaves the accumulator at zero: ITER skips a body whose
        slice is empty.
        """
        lines.append(line("PUSH 0", indent))
        lines.append(line(f"STOW r{self.acc_reg}", indent))
        lines.append(line("PUSH 0", indent))
        lines.append(line(f"VECLEN r{indices_reg}", indent))
        lines.append(line(f"ITER r{indices_reg}", indent))

        body = indent + 1
        if coeffs_reg is not None:
            lines.append(line(f"LIDX r{self.pos_reg}", body))
        lines.append(line(f"LVAL r{self.elem_reg}", body))

        if coeffs_reg is not None:
            lines.append(line(f"LOAD r{self.pos_reg}", body))
            lines.append(line(f"VECGET r{coeffs_reg}", body))

        lines.append(line(f"LOAD r{self.elem_reg}", body))
        lines.append(line(f"GETLINE r{self.sample_reg}", body))

        if coeffs_reg is not None:
            lines.append(line("MUL", body))

        lines.append(line(f"LOAD r{self.acc_reg}", body))
        lines.append(line("ADD", body))
        lines.append(line(f"STOW r{self.acc_reg}", body))
        lines.append(line("NEXT", indent))

    def _check_sum(
        self,
        indices_reg: int,
        coeffs_reg: int | None,
        bound: Expr,
        op: str,
        lines: list[str],
        indent: int,
    ) -> None:
        """Emit a weighted-sum check and fold the result into the flag."""
        self._sum_into_acc(indices_reg, coeffs_reg, lines, indent)
        lines.append(line(f"LOAD r{self.acc_reg}", indent))
        self.xf(bound).emit(lines, indent)
        lines.append(line(op, indent))
        self._commit(lines, indent)

    # -- per-constraint checks -------------------------------------------

    def _check_onehot_row(self, d: dict[str, Any], lines: list[str], indent: int) -> None:
        """Check that the sample's row sums to exactly one."""
        self.xf(d["row"]).emit(lines, indent)
        lines.append(line(f"ROWSUM r{self.sample_reg}", indent))
        lines.append(line("PUSH 1", indent))
        lines.append(line("EQ", indent))
        self._commit(lines, indent)

    def _check_onehot_col(self, d: dict[str, Any], lines: list[str], indent: int) -> None:
        """Check that the sample's column sums to exactly one."""
        self.xf(d["col"]).emit(lines, indent)
        lines.append(line(f"COLSUM r{self.sample_reg}", indent))
        lines.append(line("PUSH 1", indent))
        lines.append(line("EQ", indent))
        self._commit(lines, indent)

    def _check_exclude(self, d: dict[str, Any], lines: list[str], indent: int) -> None:
        """Check that at most one of the two variables is set."""
        model: ModelRef = d["model"]
        self._index(d["coord_a"], model, lines, indent)
        lines.append(line(f"GETLINE r{self.sample_reg}", indent))
        self._index(d["coord_b"], model, lines, indent)
        lines.append(line(f"GETLINE r{self.sample_reg}", indent))
        lines.append(line("MUL", indent))
        lines.append(line("PUSH 0", indent))
        lines.append(line("EQ", indent))
        self._commit(lines, indent)

    def _check_implies(self, d: dict[str, Any], lines: list[str], indent: int) -> None:
        """Check ``sample[a] <= sample[b]``."""
        model: ModelRef = d["model"]
        self._index(d["coord_a"], model, lines, indent)
        lines.append(line(f"GETLINE r{self.sample_reg}", indent))
        self._index(d["coord_b"], model, lines, indent)
        lines.append(line(f"GETLINE r{self.sample_reg}", indent))
        lines.append(line("LTE", indent))
        self._commit(lines, indent)

    def _check_equality(self, d: dict[str, Any], lines: list[str], indent: int) -> None:
        """Check the weighted sum against the target.

        A SLACK-extended vector pair encodes ``<=``, not ``==``: the slack
        bits absorb the difference, and they are the encoding's own
        variables rather than the user's constraint.
        """
        op = "LTE" if id(d) in self.slack_equalities else "EQ"
        self._check_sum(d["indices"].reg, d["coeffs"].reg, d["target"], op, lines, indent)

    def _check_inequality(self, d: dict[str, Any], lines: list[str], indent: int) -> None:
        """Check the weighted sum against the capacity.

        The bound is ``capacity``; ``target`` is the slack start index.
        """
        self._check_sum(d["indices"].reg, d["coeffs"].reg, d["capacity"], "LTE", lines, indent)

    def _check_atleast(self, d: dict[str, Any], lines: list[str], indent: int) -> None:
        """Check that at least k of the indexed variables are set."""
        self._check_sum(d["indices"].reg, None, d["k"], "GTE", lines, indent)

    def _check_atleastw(self, d: dict[str, Any], lines: list[str], indent: int) -> None:
        """Check that the weighted sum reaches k."""
        self._check_sum(d["indices"].reg, d["coeffs"].reg, d["k"], "GTE", lines, indent)

    def _check_reduce(self, d: dict[str, Any], lines: list[str], indent: int) -> None:
        """Check the Rosenberg auxiliary equals the product it stands for.

        The auxiliary's index is tracked in a shadow counter rather than
        allocated: the verifier must not run REDUCE, which would mutate
        the model it was handed.  Stowing the index into the encoder's own
        register is what makes a chained ``reduce()`` resolve.
        """
        lines.append(line(f"LOAD r{self.aux_reg}", indent))
        lines.append(line(f"STOW r{d['stow_reg']}", indent))
        lines.append(line(f"LOAD r{self.aux_reg}", indent))
        lines.append(line("INC", indent))
        lines.append(line(f"STOW r{self.aux_reg}", indent))

        lines.append(line(f"LOAD r{d['stow_reg']}", indent))
        lines.append(line(f"GETLINE r{self.sample_reg}", indent))
        self.xf(d["var_a"]).emit(lines, indent)
        lines.append(line(f"GETLINE r{self.sample_reg}", indent))
        self.xf(d["var_b"]).emit(lines, indent)
        lines.append(line(f"GETLINE r{self.sample_reg}", indent))
        lines.append(line("MUL", indent))
        lines.append(line("EQ", indent))
        self._commit(lines, indent)

    # -- domain check ----------------------------------------------------

    def emit_domain_check(self, lines: list[str], indent: int) -> None:
        """Check every declared variable against the model's domain.

        The bound is the model's *declared* size, replayed from
        ``define_model``, so slack and auxiliary variables past that size
        are not domain-checked.
        """
        low, high = ("0", "1") if self.domain == XQMXDomain.BINARY else ("-1", "1")

        lines.append(line("PUSH 0", indent))
        lines.append(line(f"LOAD r{self.size_reg}", indent))
        lines.append(line("RANGE", indent))

        body = indent + 1
        lines.append(line(f"LVAL r{self.pos_reg}", body))
        lines.append(line(f"LOAD r{self.pos_reg}", body))
        lines.append(line(f"GETLINE r{self.sample_reg}", body))
        lines.append(line("COPY", body))
        lines.append(line(f"PUSH {low}", body))
        lines.append(line("EQ", body))
        lines.append(line("SWAP", body))
        lines.append(line(f"PUSH {high}", body))
        lines.append(line("EQ", body))
        lines.append(line("OR", body))
        self._commit(lines, body)
        lines.append(line("NEXT", indent))


# ---------------------------------------------------------------------------
# Compiler: decoder
# ---------------------------------------------------------------------------


def compile_decoder(prob: Problem) -> str:
    """Generate the decoder .xqasm program."""
    lines: list[str] = []

    R_SAMPLE = 0
    R_N = 1
    next_reg = 2
    R_LOOP = 10

    # --- Inputs ---
    lines.append("; === Inputs ===")
    lines.append("PUSH 0")
    lines.append(f"INPUT r{R_SAMPLE}")
    lines.append("PUSH 1")
    lines.append(f"INPUT r{R_N}")

    # --- Collect output blocks ---
    output_blocks = _collect_output_blocks(prob._actions)

    for output_ref, block in output_blocks:
        out_reg = next_reg
        next_reg += 1

        if output_ref.type_ == Types.Vec:
            lines.append("")
            lines.append(f"; === Decode {output_ref.name} ===")
            lines.append(f"VECI r{out_reg}")
            _emit_decoder_block(block, lines, 0, R_SAMPLE, R_N, R_LOOP, out_reg)

        lines.append("")
        lines.append("; === Output ===")
        lines.append(f"PUSH {output_ref.slot}")
        lines.append(f"OUTPUT r{out_reg}")

    lines.append("HALT")
    return "\n".join(lines) + "\n"


def _collect_output_blocks(actions: list[Action]) -> list[tuple[OutputRef, list[Action]]]:
    """Group actions into (output_ref, computation_block) pairs."""
    blocks: list[tuple[OutputRef, list[Action]]] = []
    current_output: OutputRef | None = None
    current_block: list[Action] = []
    in_output_section = False

    for action in actions:
        if action.kind == "output_decl":
            if current_output is not None:
                blocks.append((current_output, current_block))
            current_output = action.data["ref"]
            current_block = []
            in_output_section = True
        elif in_output_section:
            current_block.append(action)

    if current_output is not None:
        blocks.append((current_output, current_block))

    return blocks


def _emit_decoder_block(
    actions: list[Action],
    lines: list[str],
    indent: int,
    sample_reg: int,
    n_reg: int,
    loop_reg: int,
    out_reg: int,
) -> None:
    """Emit decoder computation block actions."""
    for action in actions:
        kind = action.kind
        d = action.data

        if kind == "range_start":
            start_expr: Expr = d["start_expr"]
            end_expr: Expr = d["end_expr"]

            _emit_decoder_expr(start_expr, lines, indent, n_reg)

            if isinstance(start_expr, Literal) and start_expr.value == 0:
                _emit_decoder_expr(end_expr, lines, indent, n_reg)
            else:
                _emit_decoder_expr(end_expr, lines, indent, n_reg)
                _emit_decoder_expr(start_expr, lines, indent, n_reg)
                lines.append(line("SUB", indent))

            lines.append(line("RANGE", indent))
            indent += 1
            lines.append(line(f"LVAL r{loop_reg}", indent))

        elif kind == "range_end":
            indent -= 1
            lines.append(line("NEXT", indent))

        elif kind == "iter_start":
            iter_vec_ref: InputRef = d["vec_ref"]
            idx_var: LoopVar = d["idx_var"]
            val_var: LoopVar = d["val_var"]
            start_expr_i: Expr = d["start_expr"]
            end_expr_i: Expr = d["end_expr"]

            _emit_decoder_expr(start_expr_i, lines, indent, n_reg)
            _emit_decoder_expr(end_expr_i, lines, indent, n_reg)
            lines.append(line(f"ITER r{iter_vec_ref.reg}", indent))
            indent += 1
            lines.append(line(f"LIDX r{idx_var.reg}", indent))
            lines.append(line(f"LVAL r{val_var.reg}", indent))

        elif kind == "iter_end":
            indent -= 1
            lines.append(line("NEXT", indent))

        elif kind == "output_append":
            _emit_decoder_value_expr(d["value_expr"], lines, indent, sample_reg, loop_reg)
            lines.append(line(f"VECPUSH r{out_reg}", indent))

        elif kind == "output_setitem":
            _emit_decoder_expr(d["index_expr"], lines, indent, n_reg)
            _emit_decoder_value_expr(d["value_expr"], lines, indent, sample_reg, loop_reg)
            lines.append(line(f"VECSET r{out_reg}", indent))


def _emit_decoder_expr(expr: Expr, lines: list[str], indent: int, n_reg: int) -> None:
    """Emit an expression in decoder context, remapping InputRef to N register."""
    if isinstance(expr, Literal):
        expr.emit(lines, indent)
    elif isinstance(expr, (InputRef, RegLoad)):
        lines.append(line(f"LOAD r{n_reg}", indent))
    elif isinstance(expr, BinOp):
        _emit_decoder_expr(expr.left, lines, indent, n_reg)
        _emit_decoder_expr(expr.right, lines, indent, n_reg)
        lines.append(line(expr.op, indent))
    else:
        expr.emit(lines, indent)


def _emit_decoder_value_expr(
    expr: Expr,
    lines: list[str],
    indent: int,
    sample_reg: int,
    loop_reg: int,
) -> None:
    """Emit a value expression in decoder context (inside loop)."""
    if isinstance(expr, ColFindExpr):
        col = expr.col_expr
        if isinstance(col, (LoopVar, RegLoad)):
            lines.append(line(f"LOAD r{loop_reg}", indent))
        else:
            col.emit(lines, indent)
        lines.append(line(f"PUSH {fmt_int(expr.value)}", indent))
        lines.append(line(f"COLFIND r{sample_reg}", indent))
    elif isinstance(expr, GetLineExpr):
        idx = expr.index_expr
        if isinstance(idx, (LoopVar, RegLoad)):
            lines.append(line(f"LOAD r{loop_reg}", indent))
        else:
            idx.emit(lines, indent)
        lines.append(line(f"GETLINE r{sample_reg}", indent))
    else:
        expr.emit(lines, indent)
