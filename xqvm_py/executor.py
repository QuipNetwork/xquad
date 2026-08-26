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
XQVM Executor: Fetch-Decode-Execute loop with dispatch table.

The Executor processes XQVM programs by fetching instructions,
decoding opcodes, and dispatching to handler methods.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from .errors import (
    DivisionByZero,
    IndexOutOfBounds,
    InvalidAllocation,
    InvalidDiscreteK,
    InvalidGridDimensions,
    InvalidOpcode,
    InvalidShift,
    MemoryLimitExceeded,
    OutputIndex,
    RegisterNotFound,
    StepLimitExceeded,
    TargetNotFound,
    TypeMismatch,
    VecLengthMismatch,
)
from .metering import (
    BASE_STEPS,
    COEFF_WRITE_STEPS,
    ELEMENT_COPY_STEPS,
    GRID_CELL_STEPS,
    SAMPLE_COPY_STEPS,
    _saturating,
    equality_expansion_steps,
    model_eval_steps,
    value_copy_steps,
)
from .opcodes import Opcode
from .program import Instruction, Program
from .state import MachineState, check_i64
from .vector import Vec, VecElem
from .xqmx import (
    XQMX,
    col_find,
    col_indices,
    compute_energy,
    expand_equality,
    expand_exclude,
    expand_implies,
    expand_onehot,
    expand_reduce,
    grid_col_extent,
    grid_row_extent,
    require_model_mode,
    require_sample_mode,
    row_find,
    row_indices,
)
from .xqmx import (
    col_sum as xqmx_col_sum,
)
from .xqmx import (
    row_sum as xqmx_row_sum,
)

# --------------------------------------------------------------------------
# Allocation budget
#
# The charge schedule is defined over program-visible quantities -- variables
# declared, elements appended, coefficients written -- and not over either
# interpreter's internal representation, so both reject exactly the same
# programs at exactly the same instruction. The rates below must stay
# byte-for-byte identical to the constants in `xqvm/src/vm.rs`.
# --------------------------------------------------------------------------

#: Default step budget. Unbounded execution has to be asked for rather than
#: stumbled into: with no default, `TARGET .0 / NOP / JUMP .0` ran forever
#: here while the Rust VM, `xquad` and `xqcli` all stopped it at this
#: number. `None` still means unbounded, but a caller has to write it.
DEFAULT_STEP_LIMIT = 10_000_000

#: Default allocation budget in bytes. Generous for off-chain use; embedders
#: running untrusted bytecode should pass a much smaller `memory_limit`.
DEFAULT_MEMORY_LIMIT = 1 << 30

#: Bytes charged per XQMX variable (one `i64` in a sample buffer). Model
#: allocators are charged at the same rate: both interpreters store model
#: coefficients sparsely, but a declared size is an obligation every consumer
#: of the model has to materialise.
VARIABLE_BYTES = 8

#: Bytes charged per element appended to a vec. Rust's `Vec` doubles its
#: capacity as it grows, so it holds between one and two elements' worth of
#: buffer per live element; the budget charges the upper end.
VEC_ELEMENT_BYTES = 16

#: Bytes charged per nonzero linear coefficient (key plus value, doubled for
#: container overhead).
LINEAR_ENTRY_BYTES = 32

#: Bytes charged per nonzero quadratic coefficient; the key is an index pair.
QUAD_ENTRY_BYTES = 48


def equality_expansion_bytes(n: int) -> int:
    """Worst-case bytes for an equality expansion over `n` terms.

    The expansion writes one linear term per index and one quadratic term per
    unordered pair. It is a worst case: repeated indices collide on the same
    key and cancelling coefficients are removed again, so an expansion can
    write fewer entries than it is charged for.
    """
    pairs = n * (n - 1) // 2
    return n * LINEAR_ENTRY_BYTES + pairs * QUAD_ENTRY_BYTES


@runtime_checkable
class TracerProtocol(Protocol):
    """
    Optional tracer interface for debugging/monitoring execution.

    Implement this protocol to receive callbacks during execution.
    """

    def on_step_begin(self, executor: Executor, instr: Instruction) -> None:
        """Called before each instruction executes."""
        ...

    def on_step_end(self, executor: Executor, instr: Instruction) -> None:
        """Called after each instruction executes."""
        ...

    def on_error(self, executor: Executor, instr: Instruction, error: Exception) -> None:
        """Called when an error occurs during execution."""
        ...

    def on_halt(self, executor: Executor) -> None:
        """Called when execution halts normally."""
        ...


class Executor:
    """
    XQVM execution engine.

    The Executor maintains machine state and processes programs through
    a fetch-decode-execute loop. Each opcode is dispatched to a handler
    method via a dispatch table.

    Usage:
        executor = Executor()
        result = executor.execute(program, input_data)

    Attributes:
        state: The current machine state
        program: The program being executed (None if not executing)
        tracer: Optional tracer for debugging
    """

    def __init__(self, tracer: TracerProtocol | None = None) -> None:
        self.state = MachineState()
        self.program: Program | None = None
        self.tracer = tracer
        self._memory_limit = DEFAULT_MEMORY_LIMIT
        self._memory_used = 0
        # `execute()` overwrites this, but `step()` is public and can be
        # driven directly, and any opcode that charges reads it. Unlimited
        # until a caller says otherwise.
        self._step_limit: int | None = None
        self._dispatch = self._build_dispatch_table()

    def _build_dispatch_table(self) -> dict[Opcode, Callable[[Instruction], None]]:
        """Build the opcode -> handler dispatch table."""
        return {
            # Control flow
            Opcode.TARGET: self._runner_TARGET,
            Opcode.JUMP1: self._runner_JUMP1,
            Opcode.JUMPI1: self._runner_JUMPI1,
            Opcode.JUMP2: self._runner_JUMP2,
            Opcode.JUMPI2: self._runner_JUMPI2,
            Opcode.LIDX: self._runner_LIDX,
            Opcode.LVAL: self._runner_LVAL,
            Opcode.NEXT: self._runner_NEXT,
            Opcode.RANGE: self._runner_RANGE,
            Opcode.ITER: self._runner_ITER,
            Opcode.NOP: self._runner_NOP,
            Opcode.HALT: self._runner_HALT,
            # Stack & Register I/O
            Opcode.PUSH1: self._runner_PUSH,
            Opcode.PUSH2: self._runner_PUSH,
            Opcode.PUSH3: self._runner_PUSH,
            Opcode.PUSH4: self._runner_PUSH,
            Opcode.PUSH5: self._runner_PUSH,
            Opcode.PUSH6: self._runner_PUSH,
            Opcode.PUSH7: self._runner_PUSH,
            Opcode.PUSH8: self._runner_PUSH,
            Opcode.POP: self._runner_POP,
            Opcode.COPY: self._runner_COPY,
            Opcode.SWAP: self._runner_SWAP,
            Opcode.SCLR: self._runner_SCLR,
            Opcode.LOAD: self._runner_LOAD,
            Opcode.STOW: self._runner_STOW,
            Opcode.DROP: self._runner_DROP,
            Opcode.INPUT: self._runner_INPUT,
            Opcode.OUTPUT: self._runner_OUTPUT,
            # Arithmetic
            Opcode.ADD: self._runner_ADD,
            Opcode.SUB: self._runner_SUB,
            Opcode.MUL: self._runner_MUL,
            Opcode.DIV: self._runner_DIV,
            Opcode.MOD: self._runner_MOD,
            Opcode.SQR: self._runner_SQR,
            Opcode.ABS: self._runner_ABS,
            Opcode.NEG: self._runner_NEG,
            Opcode.MIN: self._runner_MIN,
            Opcode.MAX: self._runner_MAX,
            Opcode.INC: self._runner_INC,
            Opcode.DEC: self._runner_DEC,
            Opcode.BITLEN: self._runner_BITLEN,
            # Comparison
            Opcode.EQ: self._runner_EQ,
            Opcode.LT: self._runner_LT,
            Opcode.GT: self._runner_GT,
            Opcode.LTE: self._runner_LTE,
            Opcode.GTE: self._runner_GTE,
            # Boolean
            Opcode.NOT: self._runner_NOT,
            Opcode.AND: self._runner_AND,
            Opcode.OR: self._runner_OR,
            Opcode.XOR: self._runner_XOR,
            # Bitwise
            Opcode.BAND: self._runner_BAND,
            Opcode.BOR: self._runner_BOR,
            Opcode.BXOR: self._runner_BXOR,
            Opcode.BNOT: self._runner_BNOT,
            Opcode.SHL: self._runner_SHL,
            Opcode.SHR: self._runner_SHR,
            # Allocators
            Opcode.VEC: self._runner_VEC,
            Opcode.VECI: self._runner_VECI,
            Opcode.VECX: self._runner_VECX,
            Opcode.BQMX: self._runner_BQMX,
            Opcode.SQMX: self._runner_SQMX,
            Opcode.XQMX: self._runner_XQMX,
            Opcode.BSMX: self._runner_BSMX,
            Opcode.SSMX: self._runner_SSMX,
            Opcode.XSMX: self._runner_XSMX,
            # Vec Access
            Opcode.VECPUSH: self._runner_VECPUSH,
            Opcode.VECGET: self._runner_VECGET,
            Opcode.VECSET: self._runner_VECSET,
            Opcode.VECLEN: self._runner_VECLEN,
            Opcode.SLACK: self._runner_SLACK,
            # XQMX Access
            Opcode.GETLINE: self._runner_GETLINE,
            Opcode.SETLINE: self._runner_SETLINE,
            Opcode.ADDLINE: self._runner_ADDLINE,
            Opcode.GETQUAD: self._runner_GETQUAD,
            Opcode.SETQUAD: self._runner_SETQUAD,
            Opcode.ADDQUAD: self._runner_ADDQUAD,
            # Vector Math
            Opcode.IDXGRID: self._runner_IDXGRID,
            Opcode.IDXTRIU: self._runner_IDXTRIU,
            # XQMX Grid
            Opcode.RESIZE: self._runner_RESIZE,
            Opcode.ROWFIND: self._runner_ROWFIND,
            Opcode.COLFIND: self._runner_COLFIND,
            Opcode.ROWSUM: self._runner_ROWSUM,
            Opcode.COLSUM: self._runner_COLSUM,
            # XQMX High-level
            Opcode.ONEHOTR: self._runner_ONEHOTR,
            Opcode.ONEHOTC: self._runner_ONEHOTC,
            Opcode.EXCLUDE: self._runner_EXCLUDE,
            Opcode.IMPLIES: self._runner_IMPLIES,
            Opcode.EQUALITY: self._runner_EQUALITY,
            Opcode.ATLEAST: self._runner_ATLEAST,
            Opcode.ATLEASTW: self._runner_ATLEASTW,
            Opcode.REDUCE: self._runner_REDUCE,
            Opcode.ENERGY: self._runner_ENERGY,
        }

    def execute(
        self,
        program: Program,
        input_data: dict[int, Any] | None = None,
        step_limit: int | None = DEFAULT_STEP_LIMIT,
        memory_limit: int = DEFAULT_MEMORY_LIMIT,
        output_slots: int = 0,
    ) -> dict[int, Any]:
        """
        Execute a program to completion.

        Args:
            program: The program to execute
            input_data: Optional input data keyed by slot number
            step_limit: Maximum steps before raising StepLimitExceeded.
                Defaults to `DEFAULT_STEP_LIMIT`, matching `xqvm::Vm::new`.
                `None` is unlimited and has to be written rather than
                defaulted into; the limit is otherwise exact, so `0` permits
                no instructions at all.
            memory_limit: Allocation budget in bytes, charged against every
                allocating instruction before it allocates. Unlike step_limit
                there is no "unlimited" sentinel -- pass a large value.
            output_slots: Number of output slots reserved for OUTPUT. Writing
                past them raises OutputIndex, matching
                `xqvm::Vm::set_output_slots`.

        Returns:
            Output data keyed by slot number

        Raises:
            XQVMError: On execution errors
        """
        self.state.reset()
        self.state.output_slots = output_slots
        self.program = program
        self._memory_limit = memory_limit
        self._memory_used = 0
        self._step_limit = step_limit

        if input_data:
            for slot, value in input_data.items():
                # A raw list becomes a Vec at the boundary, the way
                # `xquad.vm._prepare_calldata_python` already does it, so
                # `Vm::set_calldata`'s `RegVal::VecInt` and this VM's
                # calldata are the same kind of thing. Stored unconverted,
                # a list charged 8 bytes an element against Rust's 16 for
                # the same value, so a program tuned near the memory limit
                # passed here and faulted there.
                if isinstance(value, list):
                    value = Vec.from_list(value)
                self.state.set_input(slot, value)
        # The calldata slot count the host fixed, mirroring `output_slots`
        # above and `Vm::set_calldata`'s vec length. `input_data` is keyed by
        # slot for convenience, but the space it stands for is dense, so the
        # count is its size rather than its largest key. A slot the host
        # left unset is still a slot and still carries a key here, the way
        # `RegVal::Unset` still occupies a place in the Rust VM's calldata
        # vec; a caller that drops one shortens the space and moves the
        # bound, which is the divergence `xquad.vm._prepare_calldata_python`
        # used to introduce.
        self.state.input_slots = len(input_data) if input_data else 0

        for target_id, pc in program.jump_targets.items():
            self.state.jc.define_target(target_id, pc)

        while not self.state.halted and self.state.pc < len(program):
            self._charge_steps(BASE_STEPS, step_limit)
            self.state.instructions += 1
            self.step()

        return dict(self.state.output)

    @property
    def steps(self) -> int:
        """Total steps executed since the last `execute()` call."""
        return self.state.steps

    @property
    def instructions(self) -> int:
        """Total instructions dispatched since the last `execute()` call."""
        return self.state.instructions

    @property
    def memory_used(self) -> int:
        """Bytes charged against the allocation budget by the last `execute()`."""
        return self._memory_used

    @property
    def memory_limit(self) -> int:
        """The allocation budget in bytes."""
        return self._memory_limit

    # -- allocation accounting ------------------------------------------------

    def _charge(self, nbytes: int) -> None:
        """Charge `nbytes` against the allocation budget.

        Callers charge *before* they allocate, so an oversized request is
        rejected rather than handed to the allocator. Charges are cumulative
        and never refunded, so an allocate-and-discard loop cannot spend more
        than the budget in total.
        """
        total = self._memory_used + nbytes
        if total > self._memory_limit:
            raise MemoryLimitExceeded(nbytes, self._memory_used, self._memory_limit)
        self._memory_used = total

    def _charge_steps(self, units: int, limit: int | None = None) -> None:
        """Charge `units` of execution against the step budget.

        Callers charge *before* they do the work, so an instruction that
        cannot pay does none of it -- the same discipline as `_charge`.

        The running total is clamped the way Rust's `u64` counter saturates
        (`charge_steps_at`'s `self.steps.saturating_add(units)`): Python
        integers do not overflow on their own, so without this an
        unbounded `step_limit` would let the two VMs' `steps` diverge on a
        program large enough to cross `u64::MAX`.
        """
        limit = self._step_limit if limit is None else limit
        total = _saturating(self.state.steps + units)
        if limit is not None and total > limit:
            raise StepLimitExceeded(limit, requested=units, used=self.state.steps)
        self.state.steps = total

    def _charge_variables(self, count: int) -> None:
        """Charge for `count` XQMX variables. Negative counts clamp to zero."""
        self._charge(max(count, 0) * VARIABLE_BYTES)

    def _allocation_size(self, size: int) -> int:
        """Validate and charge for an allocator's size operand.

        Mirrors Rust's `Vm::allocation_size`: reject a size that is not an
        allocation, then charge the budget. Rust's third step -- narrowing to
        `usize` -- has no counterpart here, because Python integers are
        unbounded; charging before that narrowing is what makes the two agree
        on a 32-bit target as well as on a 64-bit one.
        """
        if size < 0:
            raise InvalidAllocation(size)
        self._charge(size * VARIABLE_BYTES)
        return size

    def _charge_equality_expansion(self, n: int) -> None:
        """Charge the worst-case cost of an equality expansion over `n` terms."""
        self._charge(equality_expansion_bytes(max(n, 0)))

    def _value_bytes(self, value: Any) -> int:
        """Bytes one whole register value costs to duplicate.

        The rates are the schedule's own, applied to the same
        program-visible quantities the allocating opcodes charge for, so a
        value costs the same to copy as it cost to build. `None` -- an unset
        slot -- is free because there is nothing to copy.

        Mirrors Rust's `regval_bytes`, including its split over vec element
        type: a `vec<int>` is priced per element, but a `vec<xqmx>` holds
        whole models whose coefficient maps travel with the copy, so it is
        priced as the sum of its elements. Charging a two-model vec the
        flat element rate valued it at 32 bytes where Rust valued it at
        hundreds.
        """
        if value is None:
            return 0
        if isinstance(value, XQMX):
            if not value.is_model():
                return value.size * VARIABLE_BYTES
            return (
                value.size * VARIABLE_BYTES
                + len(value.linear) * LINEAR_ENTRY_BYTES
                + len(value.quadratic) * QUAD_ENTRY_BYTES
            )
        if isinstance(value, Vec):
            if value.element_type.kind == "xqmx":
                return sum(self._value_bytes(item) for item in value)
            return len(value) * VEC_ELEMENT_BYTES
        if isinstance(value, int) and not isinstance(value, bool):
            return VARIABLE_BYTES
        # Exhaustive, matching `regval_bytes`, whose match over `RegVal` has
        # no wildcard arm and so fails to compile when a variant is added.
        # The arms above cover every type a register can legitimately hold;
        # anything else is a host that installed a value the VM has no rate
        # for, and pricing it silently is how a raw `list[int]` came to cost
        # 8 bytes an element here against Rust's 16 for the same `vec<int>`.
        raise TypeMismatch(
            "int, Vec or XQMX",
            type(value).__name__,
            "a register value the charge schedule has no rate for",
        )

    def _charge_clone(self, value: Any) -> None:
        """Charge for one copy of `value` crossing the host boundary.

        OUTPUT and INPUT both duplicate a whole register: OUTPUT copies it
        into an output slot the host keeps after the run, INPUT copies a
        calldata entry into a register. Neither had a charge site, so the
        only guards were a slot bound and an unset check.

        The copy is charged rather than the overwritten slot refunded, which
        keeps the budget cumulative.
        """
        self._charge(self._value_bytes(value))

    def _charge_coefficient(self, xqmx: XQMX, nbytes: int) -> None:
        """Charge for one coefficient written into `xqmx`, if it is a model.

        A sample writes into storage that was charged when the sample was
        allocated, so it costs nothing further. Writing a coefficient that
        already exists is charged too: the step limit already bounds how many
        of these a program can run.
        """
        if xqmx.is_model():
            self._charge(nbytes)

    def _charge_coefficient_steps(self, xqmx: XQMX) -> None:
        """Charge the step cost of one coefficient write into `xqmx`, if it
        is a model. The byte twin is `_charge_coefficient`; the reasoning
        about samples and repeated writes is identical.
        """
        if xqmx.is_model():
            self._charge_steps(COEFF_WRITE_STEPS)

    def step(self) -> bool:
        """
        Execute a single instruction.

        Returns:
            True if execution should continue, False if halted or at end

        Raises:
            XQVMError: On execution errors
        """
        if self.program is None:
            return False

        if self.state.halted or self.state.pc >= len(self.program):
            return False

        instr = self.program[self.state.pc]

        if self.tracer:
            self.tracer.on_step_begin(self, instr)

        try:
            handler = self._dispatch.get(instr.opcode)
            if handler is None:
                raise InvalidOpcode(instr.opcode)

            old_pc = self.state.pc
            handler(instr)

            # Advance PC only if handler didn't modify it (via jump or halt)
            if not self.state.halted and self.state.pc == old_pc:
                self.state.advance_pc()

        except Exception as e:
            if self.tracer:
                self.tracer.on_error(self, instr, e)
            raise

        if self.tracer:
            if self.state.halted:
                self.tracer.on_halt(self)
            else:
                self.tracer.on_step_end(self, instr)

        return not self.state.halted

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _get_register_as_int(self, slot: int) -> int:
        """Get a register value, ensuring it's an int."""
        value = self.state.get_register(slot)
        if not isinstance(value, int):
            raise TypeMismatch("int", type(value).__name__, f"register r{slot}")
        return value

    def _get_register_as_vec(self, slot: int) -> Vec:
        """Get a register value, ensuring it's a vec."""
        value = self.state.get_register(slot)
        if not isinstance(value, Vec):
            raise TypeMismatch("Vec", type(value).__name__, f"register r{slot}")
        return value

    def _get_register_as_xqmx(self, slot: int) -> XQMX:
        """Get a register value, ensuring it's an xqmx"""
        value = self.state.get_register(slot)
        if not isinstance(value, XQMX):
            raise TypeMismatch("XQMX", type(value).__name__, f"register r{slot}")
        return value

    # =========================================================================
    # Instruction Set Runners
    # =========================================================================

    def _runner_NOP(self, instr: Instruction) -> None:
        """NOP: No operation."""
        pass

    def _runner_HALT(self, instr: Instruction) -> None:
        """HALT: Stop execution."""
        self.state.halt()

    def _runner_TARGET(self, instr: Instruction) -> None:
        """TARGET: No-op (targets pre-scanned before execution)."""
        pass

    def _runner_JUMP1(self, instr: Instruction) -> None:
        """JUMP1: Unconditional jump to target (u8)."""
        target_id = instr.operands[0]
        pc = self.state.jc.resolve_target(target_id)
        if pc is None:
            raise TargetNotFound(target_id)
        self.state.jump_to(pc)

    def _runner_JUMPI1(self, instr: Instruction) -> None:
        """JUMPI1: Jump to target if top of stack is non-zero (u8)."""
        target_id = instr.operands[0]
        condition = self.state.pop()

        if condition != 0:
            pc = self.state.jc.resolve_target(target_id)
            if pc is None:
                raise TargetNotFound(target_id)
            self.state.jump_to(pc)

    def _runner_JUMP2(self, instr: Instruction) -> None:
        """JUMP2: Unconditional jump to target (u16)."""
        target_id = (instr.operands[0] << 8) | instr.operands[1]
        pc = self.state.jc.resolve_target(target_id)
        if pc is None:
            raise TargetNotFound(target_id)
        self.state.jump_to(pc)

    def _runner_JUMPI2(self, instr: Instruction) -> None:
        """JUMPI2: Jump to target if top of stack is non-zero (u16)."""
        target_id = (instr.operands[0] << 8) | instr.operands[1]
        condition = self.state.pop()

        if condition != 0:
            pc = self.state.jc.resolve_target(target_id)
            if pc is None:
                raise TargetNotFound(target_id)
            self.state.jump_to(pc)

    def _runner_RANGE(self, instr: Instruction) -> None:
        """RANGE: Start range loop. Pop count, start -> iterate [start, start+count)."""
        count, start = self.state.pop_n(2)
        if count <= 0:
            self._skip_to_matching_next()
            return
        # The exclusive bound is range-checked, matching Rust's `checked_add`.
        # spec/xqvm/SPEC.md ranges its overflow rule over every i64 operation
        # the VM performs on a program's behalf, and loop control is not
        # carved out of it.
        check_i64(start + count, "RANGE end")
        self.state.jc.push_loop_range(self.state.pc + 1, start, count)

    def _skip_to_matching_next(self) -> None:
        """Scan forward to the matching NEXT, skipping the loop body.

        Every instruction the scan consumes pays `BASE_STEPS`, exactly as a
        dispatched one does: the scan is O(body length) work the program
        chose to buy by opening an empty loop, and leaving it free lets a
        hot loop rent an unmetered walk over the bytecode (QUI-1056). The
        skipped instructions are metered but not dispatched, so they do not
        count towards `instructions`.
        """
        from .errors import LoopError

        depth = 1
        scan_pc = self.state.pc + 1
        while scan_pc < len(self.program):
            self._charge_steps(BASE_STEPS)
            scan_instr = self.program[scan_pc]
            if scan_instr.opcode in (Opcode.RANGE, Opcode.ITER):
                depth += 1
            elif scan_instr.opcode == Opcode.NEXT:
                depth -= 1
                if depth == 0:
                    self.state.jump_to(scan_pc + 1)
                    return
            scan_pc += 1
        raise LoopError("unmatched RANGE: no matching NEXT found")

    def _runner_ITER(self, instr: Instruction) -> None:
        """ITER: Start vec iteration. Pop end_idx, start_idx -> iterate vec[start:end]."""
        reg = instr.operands[0]
        # Pops precede the register read. spec/xqvm/ISA.md's ITER row orders
        # the steps "Pop `end_idx`, then `start_idx`. Read vec from `reg`",
        # and spec/xqvm/SPEC.md's error-precedence rule makes operand pops
        # first within every instruction. Resolving the register first made
        # an int register plus a short stack raise TypeMismatch here and
        # StackUnderflow on the Rust VM.
        end_idx, start_idx = self.state.pop_n(2)
        vec = self._get_register_as_vec(reg)
        # An empty slice skips the body, matching RANGE with count <= 0 and
        # the empty-loop-skip clause in spec/xqvm/ISA.md.
        if start_idx >= end_idx:
            self._skip_to_matching_next()
            return
        # The frame copies the slice, and a frame is only popped by NEXT, so a
        # back-edge that re-enters an ITER piles up one copy per execution.
        # Priced over the element type rather than per element: cloning a
        # model clones its coefficient maps, so a slice of a `vec<xqmx>`
        # costs what its models hold. One model copy is measured the same
        # way wherever it happens -- here, at OUTPUT, and at INPUT.
        self._charge(sum(self._value_bytes(vec.get(i)) for i in range(start_idx, end_idx)))
        if vec.element_type.kind == "xqmx":
            # Cloning a model clones its coefficient maps, so charge for
            # what each one actually holds rather than per element. Rust's
            # twin fold saturates at every accumulation, not just the
            # total, so the running sum is clamped here the same way.
            step_cost = 0
            for i in range(start_idx, end_idx):
                step_cost = _saturating(step_cost + value_copy_steps(vec.get(i)))
        else:
            step_cost = (end_idx - start_idx) * ELEMENT_COPY_STEPS
        self._charge_steps(step_cost)
        # Store current PC + 1 as the loop target (next instruction)
        self.state.jc.push_loop_iter(self.state.pc + 1, vec, start_idx, end_idx)

    def _runner_NEXT(self, instr: Instruction) -> None:
        """NEXT: Advance loop index, jump back if more, else pop frame."""
        frame = self.state.jc.current_loop()
        if frame is None:
            from .errors import LoopError

            raise LoopError("NEXT outside of loop")

        if self.state.jc.advance_loop():
            # More iterations: jump back to loop start
            self.state.jump_to(frame.target)
        # else: loop finished, frame popped, continue normally

    def _runner_LVAL(self, instr: Instruction) -> None:
        """LVAL: Copy current loop value to register."""
        reg = instr.operands[0]
        value = self.state.jc.current_loop_value()
        # Cloning an XQMX loop value clones its coefficient maps -- O(model)
        # work for one dispatch (QUI-1056). Charge before the copy below.
        self._charge_steps(value_copy_steps(value))
        self.state.set_register(reg, value)

    def _runner_LIDX(self, instr: Instruction) -> None:
        """LIDX: Copy current loop index to register."""
        reg = instr.operands[0]
        index = self.state.jc.current_loop_index()
        self.state.set_register(reg, index)

    def _runner_PUSH(self, instr: Instruction) -> None:
        """PUSH1-8: Push N-byte constant (big-endian signed two's complement)."""
        value = int.from_bytes(bytes(instr.operands), byteorder="big", signed=True)
        self.state.push(value)

    def _runner_POP(self, instr: Instruction) -> None:
        """POP: Pop and discard top of stack."""
        self.state.pop()

    def _runner_COPY(self, instr: Instruction) -> None:
        """COPY: Duplicate top of stack."""
        value = self.state.peek()
        self.state.push(value)

    def _runner_SWAP(self, instr: Instruction) -> None:
        """SWAP: Swap top two stack values."""
        a, b = self.state.pop_n(2)
        self.state.push(a)
        self.state.push(b)

    def _runner_SCLR(self, instr: Instruction) -> None:
        """SCLR: Clear entire stack."""
        self.state.stack.clear()

    def _runner_LOAD(self, instr: Instruction) -> None:
        """LOAD: Load register value onto stack."""
        reg = instr.operands[0]
        value = self._get_register_as_int(reg)
        self.state.push(value)

    def _runner_STOW(self, instr: Instruction) -> None:
        """STOW: Store top of stack into register."""
        reg = instr.operands[0]
        value = self.state.pop()
        self.state.set_register(reg, value)

    def _runner_DROP(self, instr: Instruction) -> None:
        """DROP: Clear register (reset to unset)."""
        reg = instr.operands[0]
        self.state.clear_register(reg)

    def _runner_INPUT(self, instr: Instruction) -> None:
        """INPUT: Load input slot into register."""
        reg = instr.operands[0]
        slot = self.state.pop()
        value = self.state.get_input(slot)
        # Validate externally-supplied integers at the boundary so that
        # ill-formed inputs fail early rather than deferring to the first LOAD.
        # Vec and XQMX values pass through unchecked; their integer contents
        # are validated when they reach the stack via GETLINE/GETQUAD/VECGET.
        if isinstance(value, int) and not isinstance(value, bool):
            check_i64(value, f"INPUT slot {slot}")
        self._charge_clone(value)
        # Cloning a model clones its coefficient maps -- O(model) work for
        # one dispatch, so the step meter pays for the same copy the byte
        # budget does (QUI-1056).
        self._charge_steps(value_copy_steps(value))
        if value is None:
            # An in-range slot the host left unset copies unset into the
            # register, which is what `exec_input` does when the calldata
            # entry is `RegVal::Unset`. Storing None instead left the
            # register set to a value no opcode accepts, so the next read
            # raised `TypeMismatch` where the Rust VM raised
            # `UnsetRegister`. Absence from `registers` is this VM's
            # spelling of unset, so the copy is a clear rather than a set.
            self.state.clear_register(reg)
            return
        self.state.set_register(reg, value)

    def _runner_OUTPUT(self, instr: Instruction) -> None:
        """OUTPUT: Write register to output slot."""
        reg = instr.operands[0]
        slot = self.state.pop()
        # Charge before the write, and before both validations. This runner
        # aliases rather than deep-copies, so the resident-memory
        # amplification is Rust-only; the missing charge is the defect both
        # implementations shared, and `xquad/tests/test_memory_parity.py`
        # pins the two against each other.
        #
        # The order is pop, charge, then validate, per spec/xqvm/SPEC.md's
        # error-precedence rule. `_value_bytes` of an unset register is 0, so
        # reading it here to size the charge cannot itself fault, which is
        # what lets the charge precede the unset check.
        # Membership, not `is None`: `get_register` treats an absent slot as
        # unset and a slot holding None as set, and the charge for either is
        # 0, so sizing the charge from `.get` before the membership test
        # cannot change what is charged.
        value = self.state.registers.get(reg)
        self._charge_clone(value)
        # Cloning a model clones its coefficient maps -- O(model) work for
        # one dispatch, so the step meter pays for the same copy the byte
        # budget does (QUI-1056).
        self._charge_steps(value_copy_steps(value))
        if reg not in self.state.registers:
            raise RegisterNotFound(reg)
        # The slot bound is checked here rather than left to `set_output`, so
        # that all three of OUTPUT's faults are ordered at one site. Charging
        # inside `set_output` instead would have put the bound ahead of the
        # charge and inverted the precedence again.
        if slot < 0 or slot >= self.state.output_slots:
            raise OutputIndex(slot, self.state.output_slots)
        self.state.set_output(slot, value)

    def _runner_ADD(self, instr: Instruction) -> None:
        """ADD: push(pop() + pop())."""
        b, a = self.state.pop_n(2)
        self.state.push(a + b)

    def _runner_SUB(self, instr: Instruction) -> None:
        """SUB: push(second - top)."""
        b, a = self.state.pop_n(2)
        self.state.push(a - b)

    def _runner_MUL(self, instr: Instruction) -> None:
        """MUL: push(pop() * pop())."""
        b, a = self.state.pop_n(2)
        self.state.push(a * b)

    def _runner_DIV(self, instr: Instruction) -> None:
        """DIV: push(second / top)."""
        b, a = self.state.pop_n(2)
        if b == 0:
            raise DivisionByZero()
        self.state.push(a // b)

    def _runner_MOD(self, instr: Instruction) -> None:
        """MOD: push(second % top)."""
        b, a = self.state.pop_n(2)
        if b == 0:
            raise DivisionByZero()
        self.state.push(a % b)

    def _runner_NEG(self, instr: Instruction) -> None:
        """NEG: Negate top value."""
        value = self.state.pop()
        self.state.push(-value)

    def _runner_SQR(self, instr: Instruction) -> None:
        """SQR: Square top value."""
        value = self.state.pop()
        self.state.push(value * value)

    def _runner_ABS(self, instr: Instruction) -> None:
        """ABS: Absolute value."""
        value = self.state.pop()
        self.state.push(abs(value))

    def _runner_MIN(self, instr: Instruction) -> None:
        """MIN: push(min(second, top))."""
        b, a = self.state.pop_n(2)
        self.state.push(min(a, b))

    def _runner_MAX(self, instr: Instruction) -> None:
        """MAX: push(max(second, top))."""
        b, a = self.state.pop_n(2)
        self.state.push(max(a, b))

    def _runner_INC(self, instr: Instruction) -> None:
        """INC: Increment top value."""
        value = self.state.pop()
        self.state.push(value + 1)

    def _runner_DEC(self, instr: Instruction) -> None:
        """DEC: Decrement top value."""
        value = self.state.pop()
        self.state.push(value - 1)

    def _runner_BITLEN(self, instr: Instruction) -> None:
        """BITLEN: Push bit length of top value."""
        a = self.state.pop()
        self.state.push(a.bit_length() if a > 0 else 0)

    def _runner_EQ(self, instr: Instruction) -> None:
        """EQ: push(1 if second == top else 0)."""
        b, a = self.state.pop_n(2)
        self.state.push(1 if a == b else 0)

    def _runner_LT(self, instr: Instruction) -> None:
        """LT: push(1 if second < top else 0)."""
        b, a = self.state.pop_n(2)
        self.state.push(1 if a < b else 0)

    def _runner_GT(self, instr: Instruction) -> None:
        """GT: push(1 if second > top else 0)."""
        b, a = self.state.pop_n(2)
        self.state.push(1 if a > b else 0)

    def _runner_LTE(self, instr: Instruction) -> None:
        """LTE: push(1 if second <= top else 0)."""
        b, a = self.state.pop_n(2)
        self.state.push(1 if a <= b else 0)

    def _runner_GTE(self, instr: Instruction) -> None:
        """GTE: push(1 if second >= top else 0)."""
        b, a = self.state.pop_n(2)
        self.state.push(1 if a >= b else 0)

    def _runner_NOT(self, instr: Instruction) -> None:
        """NOT: push(1 if top == 0 else 0)."""
        value = self.state.pop()
        self.state.push(1 if value == 0 else 0)

    def _runner_AND(self, instr: Instruction) -> None:
        """AND: push(1 if both non-zero else 0)."""
        b, a = self.state.pop_n(2)
        self.state.push(1 if (a != 0 and b != 0) else 0)

    def _runner_OR(self, instr: Instruction) -> None:
        """OR: push(1 if either non-zero else 0)."""
        b, a = self.state.pop_n(2)
        self.state.push(1 if (a != 0 or b != 0) else 0)

    def _runner_XOR(self, instr: Instruction) -> None:
        """XOR: push(1 if exactly one non-zero else 0)."""
        b, a = self.state.pop_n(2)
        self.state.push(1 if ((a != 0) != (b != 0)) else 0)

    def _runner_BAND(self, instr: Instruction) -> None:
        """BAND: Bitwise AND."""
        b, a = self.state.pop_n(2)
        self.state.push(a & b)

    def _runner_BOR(self, instr: Instruction) -> None:
        """BOR: Bitwise OR."""
        b, a = self.state.pop_n(2)
        self.state.push(a | b)

    def _runner_BXOR(self, instr: Instruction) -> None:
        """BXOR: Bitwise XOR."""
        b, a = self.state.pop_n(2)
        self.state.push(a ^ b)

    def _runner_BNOT(self, instr: Instruction) -> None:
        """BNOT: Bitwise NOT (complement)."""
        value = self.state.pop()
        self.state.push(~value)

    def _runner_SHL(self, instr: Instruction) -> None:
        """SHL: push(second << top)."""
        b, a = self.state.pop_n(2)
        # Rust guards the shift amount with `(0..64)` before shifting. Python's
        # unbounded integers reach neither end of that guard on their own: a
        # negative count raises the operator's own `ValueError`, and a count of
        # 64 or more either overflows the push check or completes with a value
        # Rust refuses to produce. Both halves are checked here so the two
        # implementations admit the same programs.
        if not 0 <= b < 64:
            raise InvalidShift(b)
        self.state.push(a << b)

    def _runner_SHR(self, instr: Instruction) -> None:
        """SHR: push(second >> top)."""
        b, a = self.state.pop_n(2)
        # See `_runner_SHL`: the same `(0..64)` guard, for the same reason.
        if not 0 <= b < 64:
            raise InvalidShift(b)
        self.state.push(a >> b)

    def _runner_VEC(self, instr: Instruction) -> None:
        """VEC: Create empty vec (type inferred on first push)."""
        reg = instr.operands[0]
        self.state.set_register(reg, Vec())

    def _runner_VECI(self, instr: Instruction) -> None:
        """VECI: Create empty vec<int>."""
        reg = instr.operands[0]
        vec = Vec()
        vec.element_type = VecElem("int")
        self.state.set_register(reg, vec)

    def _runner_VECX(self, instr: Instruction) -> None:
        """VECX: Create empty vec<xqmx>."""
        reg = instr.operands[0]
        vec = Vec()
        vec.element_type = VecElem("xqmx")
        self.state.set_register(reg, vec)

    # The allocators take their size from the value stack, where any 64-bit
    # value is reachable in a single PUSH. Each routes that value through
    # _allocation_size, which rejects a size that is not an allocation and
    # then charges the budget for what it is about to declare, so neither a
    # negative size nor a request that does not fit reaches the register.

    def _runner_BQMX(self, instr: Instruction) -> None:
        """BQMX: Create binary model XQMX."""
        reg = instr.operands[0]
        size = self._allocation_size(self.state.pop())
        xqmx = XQMX.binary_model(size)
        self.state.set_register(reg, xqmx)

    def _runner_SQMX(self, instr: Instruction) -> None:
        """SQMX: Create spin model XQMX."""
        reg = instr.operands[0]
        size = self._allocation_size(self.state.pop())
        xqmx = XQMX.spin_model(size)
        self.state.set_register(reg, xqmx)

    def _runner_XQMX(self, instr: Instruction) -> None:
        """XQMX: Create discrete model XQMX."""
        reg = instr.operands[0]
        k, size = self.state.pop_n(2)
        # Rust's exec_xqmx rejects k < 2 before it validates or charges for
        # the size, so raise here rather than deferring to the XQMX
        # constructor: __post_init__ tests the size first, which would report
        # InvalidAllocation where Rust reports InvalidDiscreteK.
        if k < 2:
            raise InvalidDiscreteK(k)
        size = self._allocation_size(size)
        xqmx = XQMX.discrete_model(size, k)
        self.state.set_register(reg, xqmx)

    def _runner_BSMX(self, instr: Instruction) -> None:
        """BSMX: Create binary sample XQMX."""
        reg = instr.operands[0]
        size = self._allocation_size(self.state.pop())
        self._charge_steps(max(size, 0) * SAMPLE_COPY_STEPS)
        xqmx = XQMX.binary_sample(size)
        self.state.set_register(reg, xqmx)

    def _runner_SSMX(self, instr: Instruction) -> None:
        """SSMX: Create spin sample XQMX."""
        reg = instr.operands[0]
        size = self._allocation_size(self.state.pop())
        self._charge_steps(max(size, 0) * SAMPLE_COPY_STEPS)
        xqmx = XQMX.spin_sample(size)
        self.state.set_register(reg, xqmx)

    def _runner_XSMX(self, instr: Instruction) -> None:
        """XSMX: Create discrete sample XQMX."""
        reg = instr.operands[0]
        k, size = self.state.pop_n(2)
        # Same fault order as _runner_XQMX, mirroring Rust's exec_xsmx.
        if k < 2:
            raise InvalidDiscreteK(k)
        size = self._allocation_size(size)
        self._charge_steps(max(size, 0) * SAMPLE_COPY_STEPS)
        xqmx = XQMX.discrete_sample(size, k)
        self.state.set_register(reg, xqmx)

    def _runner_VECPUSH(self, instr: Instruction) -> None:
        """VECPUSH: Push value onto vec (infers/validates type)."""
        reg = instr.operands[0]
        vec = self._get_register_as_vec(reg)
        value = self.state.pop()
        self._charge(VEC_ELEMENT_BYTES)
        vec.push(value)

    def _runner_VECGET(self, instr: Instruction) -> None:
        """VECGET: Get vec[index]."""
        reg = instr.operands[0]
        vec = self._get_register_as_vec(reg)
        index = self.state.pop()
        value = vec.get(index)
        if isinstance(value, int):
            self.state.push(value)
        else:
            raise TypeMismatch("int", type(value).__name__, "VECGET result")

    def _runner_VECSET(self, instr: Instruction) -> None:
        """VECSET: Set vec[index] = value."""
        reg = instr.operands[0]
        vec = self._get_register_as_vec(reg)
        value, index = self.state.pop_n(2)
        vec.set(index, value)

    def _runner_VECLEN(self, instr: Instruction) -> None:
        """VECLEN: Push vec length onto stack."""
        reg = instr.operands[0]
        vec = self._get_register_as_vec(reg)
        self.state.push(vec.length)

    def _runner_SLACK(self, instr: Instruction) -> None:
        """SLACK: Append slack variable indices and power-of-two coefficients."""
        indices_reg = instr.operands[0]
        coeffs_reg = instr.operands[1]
        # Pops precede the register reads, matching exec_slack and
        # spec/xqvm/SPEC.md's error-precedence rule that operand pops come
        # first within every instruction. Resolving first made a short stack
        # raise TypeMismatch here and StackUnderflow on the Rust VM.
        capacity, start_index = self.state.pop_n(2)
        # Both registers are discriminated before the empty-capacity return
        # below, because SLACK carries a mutate effect on both and skipping
        # the append without resolving them discards it. Rust returned before
        # touching either, so a verifier-clean program built from INPUT
        # registers halted Ok there and faulted here.
        indices_vec = self._get_register_as_vec(indices_reg)
        coeffs_vec = self._get_register_as_vec(coeffs_reg)
        if capacity <= 0:
            return
        # Both loops below run once per bit position in `capacity`; charge
        # for the entries before creating them.
        self._charge(capacity.bit_length() * 2 * VEC_ELEMENT_BYTES)
        self._charge_steps(capacity.bit_length() * 2 * ELEMENT_COPY_STEPS)
        # Two passes, not one interleaved pass, matching `exec_slack`: the
        # whole index sequence is appended before the whole coefficient
        # sequence. The distinction is only observable when `indices` and
        # `coeffs` name the same register, where interleaving produced
        # [start, 1, start+1, 2, ...] against Rust's [start, start+1, 1, 2].
        # spec/xqvm/ISA.md's SLACK derivation pins the two-pass order.
        power = 1
        i = 0
        while power <= capacity:
            # Range-checked, matching Rust: `start_index` comes straight off
            # the value stack, so `start_index + i` leaves the range for any
            # start within 63 of i64::MAX. Raising here leaves the
            # coefficient pass unrun, which is also what Rust does.
            indices_vec.push(check_i64(start_index + i, "SLACK index"))
            power *= 2
            i += 1
        power = 1
        while power <= capacity:
            coeffs_vec.push(power)
            power *= 2

    def _runner_GETLINE(self, instr: Instruction) -> None:
        """GETLINE: Get linear coefficient."""
        reg = instr.operands[0]
        xqmx = self._get_register_as_xqmx(reg)
        index = self.state.pop()
        value = xqmx.get_linear(index)
        self.state.push(value)

    def _runner_SETLINE(self, instr: Instruction) -> None:
        """SETLINE: Set linear coefficient."""
        reg = instr.operands[0]
        xqmx = self._get_register_as_xqmx(reg)
        value, index = self.state.pop_n(2)
        self._charge_coefficient(xqmx, LINEAR_ENTRY_BYTES)
        self._charge_coefficient_steps(xqmx)
        xqmx.set_linear(index, value)

    def _runner_ADDLINE(self, instr: Instruction) -> None:
        """ADDLINE: Add to linear coefficient."""
        reg = instr.operands[0]
        xqmx = self._get_register_as_xqmx(reg)
        delta, index = self.state.pop_n(2)
        self._charge_coefficient(xqmx, LINEAR_ENTRY_BYTES)
        self._charge_coefficient_steps(xqmx)
        xqmx.add_linear(index, delta)

    def _runner_GETQUAD(self, instr: Instruction) -> None:
        """GETQUAD: Get quadratic coefficient."""
        reg = instr.operands[0]
        xqmx = self._get_register_as_xqmx(reg)
        j, i = self.state.pop_n(2)
        value = xqmx.get_quadratic(i, j)
        self.state.push(value)

    def _runner_SETQUAD(self, instr: Instruction) -> None:
        """SETQUAD: Set quadratic coefficient."""
        reg = instr.operands[0]
        xqmx = self._get_register_as_xqmx(reg)
        value, j, i = self.state.pop_n(3)
        self._charge_coefficient(xqmx, QUAD_ENTRY_BYTES)
        self._charge_coefficient_steps(xqmx)
        xqmx.set_quadratic(i, j, value)

    def _runner_ADDQUAD(self, instr: Instruction) -> None:
        """ADDQUAD: Add to quadratic coefficient."""
        reg = instr.operands[0]
        xqmx = self._get_register_as_xqmx(reg)
        delta, j, i = self.state.pop_n(3)
        self._charge_coefficient(xqmx, QUAD_ENTRY_BYTES)
        self._charge_coefficient_steps(xqmx)
        xqmx.add_quadratic(i, j, delta)

    def _runner_IDXGRID(self, instr: Instruction) -> None:
        """IDXGRID: Convert (row, col) to flat index using cols."""
        cols, j, i = self.state.pop_n(3)  # pop cols, col, row
        # Every intermediate is range-checked, not just the pushed result.
        # Rust chains `checked_mul`/`checked_add` here, so a row * cols that
        # leaves the range raises there; computing in unbounded integers and
        # checking only the final push made
        # `PUSH 2^62 / PUSH -1 / PUSH 2 / IDXGRID` raise on Rust and push
        # i64::MAX here.
        offset = check_i64(i * cols, "IDXGRID row * cols")
        index = check_i64(offset + j, "IDXGRID index")
        self.state.push(index)

    def _runner_IDXTRIU(self, instr: Instruction) -> None:
        """IDXTRIU: Convert (i, j) to upper triangular index."""
        j, i = self.state.pop_n(2)
        # The pair is unordered: swap so the two orderings address the same
        # cell.
        if i > j:
            i, j = j, i
        # Range-checked per intermediate, matching Rust's checked chain. The
        # halving is exact and cannot leave the range: j * (j - 1) is a
        # product of consecutive integers, so it is non-negative and even for
        # every operand, and truncating and flooring division agree on it.
        jm1 = check_i64(j - 1, "IDXTRIU j - 1")
        product = check_i64(j * jm1, "IDXTRIU j * (j - 1)")
        idx = check_i64(product // 2 + i, "IDXTRIU index")
        self.state.push(idx)

    def _runner_RESIZE(self, instr: Instruction) -> None:
        """RESIZE: Set grid dimensions."""
        reg = instr.operands[0]
        xqmx = self._get_register_as_xqmx(reg)
        cols, rows = self.state.pop_n(2)
        # A non-positive extent is not a grid. Assigning it unconditionally
        # left the model degenerate, which is how a grid reached the state
        # ONEHOTR and ONEHOTC reject.
        if rows <= 0 or cols <= 0:
            raise InvalidGridDimensions(rows, cols)
        # A grid is a reinterpretation of variables the program already
        # declared and already paid for at allocation, so it cannot describe
        # cells that do not exist. Without this bound ROWSUM, COLSUM, ROWFIND
        # and COLFIND scan an arbitrary extent for one metered step, charging
        # nothing: a size-4 model resized to `1 x 2^62` never returns.
        #
        # The rule is `rows * cols <= size`, not `==`: EQUALITY, ATLEAST,
        # ATLEASTW and REDUCE append slack and auxiliary variables past the
        # grid, and nothing ever shrinks `size`.
        if rows * cols > xqmx.size:
            raise InvalidGridDimensions(rows, cols)
        xqmx.rows = rows
        xqmx.cols = cols

    def _runner_ROWFIND(self, instr: Instruction) -> None:
        """ROWFIND: Find first col where row has value."""
        reg = instr.operands[0]
        xqmx = self._get_register_as_xqmx(reg)
        value, row = self.state.pop_n(2)
        # The scan is one lookup per cell over a program-controlled extent,
        # so charge for the whole row before walking it (QUI-1056). Charged
        # after the operand check, so an out-of-range row still faults for
        # the reason it did before metering existed.
        self._charge_steps(grid_row_extent(xqmx, row) * GRID_CELL_STEPS)
        col = row_find(xqmx, row, value)
        self.state.push(col)

    def _runner_COLFIND(self, instr: Instruction) -> None:
        """COLFIND: Find first row where col has value."""
        reg = instr.operands[0]
        xqmx = self._get_register_as_xqmx(reg)
        value, col = self.state.pop_n(2)
        # One lookup per cell over a program-controlled extent. See ROWFIND.
        self._charge_steps(grid_col_extent(xqmx, col) * GRID_CELL_STEPS)
        row = col_find(xqmx, col, value)
        self.state.push(row)

    def _runner_ROWSUM(self, instr: Instruction) -> None:
        """ROWSUM: Sum all values in row."""
        reg = instr.operands[0]
        xqmx = self._get_register_as_xqmx(reg)
        row = self.state.pop()
        # One lookup per cell over a program-controlled extent. See ROWFIND.
        self._charge_steps(grid_row_extent(xqmx, row) * GRID_CELL_STEPS)
        total = xqmx_row_sum(xqmx, row)
        self.state.push(total)

    def _runner_COLSUM(self, instr: Instruction) -> None:
        """COLSUM: Sum all values in column."""
        reg = instr.operands[0]
        xqmx = self._get_register_as_xqmx(reg)
        col = self.state.pop()
        # One lookup per cell over a program-controlled extent. See ROWFIND.
        self._charge_steps(grid_col_extent(xqmx, col) * GRID_CELL_STEPS)
        total = xqmx_col_sum(xqmx, col)
        self.state.push(total)

    def _runner_ONEHOTR(self, instr: Instruction) -> None:
        """ONEHOTR: Add one-hot constraint for row."""
        reg = instr.operands[0]
        model = self._get_register_as_xqmx(reg)
        penalty, row = self.state.pop_n(2)

        if model.rows == 0 or model.cols == 0:
            raise InvalidGridDimensions(model.rows, model.cols)

        # The expansion writes one linear term per column and one quadratic
        # term per pair of columns, so ONEHOTR costs O(cols^2) entries in one
        # step -- and RESIZE takes cols straight off the value stack.
        self._charge_equality_expansion(model.cols)
        self._charge_steps(equality_expansion_steps(model.cols))
        indices = row_indices(model, row)
        expand_onehot(model, indices, penalty)

    def _runner_ONEHOTC(self, instr: Instruction) -> None:
        """ONEHOTC: Add one-hot constraint for column."""
        reg = instr.operands[0]
        model = self._get_register_as_xqmx(reg)
        penalty, col = self.state.pop_n(2)

        if model.rows == 0 or model.cols == 0:
            raise InvalidGridDimensions(model.rows, model.cols)

        # O(rows^2) entries in one step; see ONEHOTR.
        self._charge_equality_expansion(model.rows)
        self._charge_steps(equality_expansion_steps(model.rows))
        indices = col_indices(model, col)
        expand_onehot(model, indices, penalty)

    def _runner_EXCLUDE(self, instr: Instruction) -> None:
        """EXCLUDE: Add exclusion constraint with penalty."""
        reg = instr.operands[0]
        model = self._get_register_as_xqmx(reg)
        penalty, j, i = self.state.pop_n(3)
        self._charge_coefficient(model, QUAD_ENTRY_BYTES)
        # One quadratic coefficient written, priced like any other write.
        self._charge_steps(COEFF_WRITE_STEPS)
        expand_exclude(model, i, j, penalty)

    def _runner_IMPLIES(self, instr: Instruction) -> None:
        """IMPLIES: Add implication constraint with penalty."""
        reg = instr.operands[0]
        model = self._get_register_as_xqmx(reg)
        penalty, j, i = self.state.pop_n(3)
        self._charge_coefficient(model, LINEAR_ENTRY_BYTES + QUAD_ENTRY_BYTES)
        # One linear and one quadratic coefficient written.
        self._charge_steps(2 * COEFF_WRITE_STEPS)
        expand_implies(model, i, j, penalty)

    def _runner_EQUALITY(self, instr: Instruction) -> None:
        """EQUALITY: Expand weighted equality constraint into QUBO terms."""
        model_reg = instr.operands[0]
        indices_reg = instr.operands[1]
        coeffs_reg = instr.operands[2]
        model = self._get_register_as_xqmx(model_reg)
        indices_vec = self._get_register_as_vec(indices_reg)
        coeffs_vec = self._get_register_as_vec(coeffs_reg)
        penalty, target = self.state.pop_n(2)
        indices = [indices_vec.get(i) for i in range(indices_vec.length)]
        coeffs = [coeffs_vec.get(i) for i in range(coeffs_vec.length)]
        # The expansion is quadratic in the number of terms, and EQUALITY
        # also grows the model to cover the largest index it was handed --
        # both from vec contents the program controls.
        if indices:
            needed = max(indices) + 1
            self._charge_variables(needed - model.size)
            model.size = max(model.size, needed)
        self._charge_equality_expansion(len(indices))
        self._charge_steps(equality_expansion_steps(len(indices)))
        expand_equality(model, indices, coeffs, target, penalty)

    def _runner_ATLEAST(self, instr: Instruction) -> None:
        """ATLEAST: At-least-k constraint with slack variables."""
        model_reg = instr.operands[0]
        indices_reg = instr.operands[1]
        model = self._get_register_as_xqmx(model_reg)
        indices_vec = self._get_register_as_vec(indices_reg)
        penalty, k = self.state.pop_n(2)
        require_model_mode(model, "ATLEAST")
        n = indices_vec.length
        if k <= 0 or k > n:
            raise IndexOutOfBounds(k, n)
        orig_indices = [indices_vec.get(i) for i in range(n)]
        max_excess = n - k
        num_slacks = max_excess.bit_length() if max_excess > 0 else 0
        # The slack variables grow the model, and the expansion is quadratic
        # in the total term count. Charge for both before either happens.
        self._charge_variables(num_slacks)
        self._charge_equality_expansion(n + num_slacks)
        self._charge_steps(equality_expansion_steps(n + num_slacks))
        if num_slacks == 0:
            expand_equality(model, orig_indices, [1] * n, k, penalty)
            return
        slack_start = model.size
        model.size += num_slacks
        combined_indices = orig_indices + [slack_start + i for i in range(num_slacks)]
        combined_coeffs = [1] * n + [-(1 << i) for i in range(num_slacks)]
        expand_equality(model, combined_indices, combined_coeffs, k, penalty)

    def _runner_ATLEASTW(self, instr: Instruction) -> None:
        """ATLEASTW: Weighted at-least-k constraint with slack variables."""
        model_reg = instr.operands[0]
        indices_reg = instr.operands[1]
        coeffs_reg = instr.operands[2]
        model = self._get_register_as_xqmx(model_reg)
        indices_vec = self._get_register_as_vec(indices_reg)
        coeffs_vec = self._get_register_as_vec(coeffs_reg)
        penalty, k = self.state.pop_n(2)
        require_model_mode(model, "ATLEASTW")
        n = indices_vec.length
        if n != coeffs_vec.length:
            raise VecLengthMismatch("indices", n, "coeffs", coeffs_vec.length)
        if k <= 0:
            raise IndexOutOfBounds(k, n)
        orig_indices = [indices_vec.get(i) for i in range(n)]
        weights = [coeffs_vec.get(i) for i in range(n)]
        # Accumulate in index order with every partial sum checked, matching
        # the Rust VM: the slack count must derive from a range-checked
        # excess, never a wrapped one (spec/xqvm/HLF.md).
        weight_sum = 0
        for w in weights:
            weight_sum = check_i64(weight_sum + w, "ATLEASTW weight sum")
        max_excess = check_i64(weight_sum - k, "ATLEASTW excess")
        num_slacks = max_excess.bit_length() if max_excess > 0 else 0
        self._charge_variables(num_slacks)
        self._charge_equality_expansion(n + num_slacks)
        self._charge_steps(equality_expansion_steps(n + num_slacks))
        if num_slacks == 0:
            expand_equality(model, orig_indices, weights, k, penalty)
            return
        slack_start = model.size
        model.size += num_slacks
        combined_indices = orig_indices + [slack_start + i for i in range(num_slacks)]
        combined_coeffs = weights + [-(1 << i) for i in range(num_slacks)]
        expand_equality(model, combined_indices, combined_coeffs, k, penalty)

    def _runner_REDUCE(self, instr: Instruction) -> None:
        """REDUCE: Rosenberg degree reduction."""
        model_reg = instr.operands[0]
        model = self._get_register_as_xqmx(model_reg)
        p_aux, var_b, var_a = self.state.pop_n(3)
        # Rosenberg reduction adds one auxiliary variable, three quadratic
        # terms and one linear term.
        self._charge_variables(1)
        self._charge(3 * QUAD_ENTRY_BYTES + LINEAR_ENTRY_BYTES)
        # Three quadratic and one linear coefficient written.
        self._charge_steps(4 * COEFF_WRITE_STEPS)
        w = expand_reduce(model, var_a, var_b, p_aux)
        self.state.push(w)

    def _runner_ENERGY(self, instr: Instruction) -> None:
        """ENERGY: Compute energy of sample against model."""
        model_reg = instr.operands[0]
        sample_reg = instr.operands[1]
        # Each register is validated completely -- XQMX-ness and then mode
        # -- before the next one is looked at, in operand order. Rust's
        # `RegVal` makes model and sample distinct variants, so its single
        # match per register decides both questions at once; here they are
        # two calls and the interleaving is what makes the two VMs agree.
        # Validating XQMX-ness of both registers first would fault on the
        # sample register for `ENERGY r0 r1` with a sample-mode XQMX in `r0`
        # and an int in `r1`, where the model register's mode error is the
        # conforming outcome. The order is normative -- see
        # `spec/xqvm/METERING.md` (Conformance).
        model = self._get_register_as_xqmx(model_reg)
        require_model_mode(model, "ENERGY (model)")
        sample = self._get_register_as_xqmx(sample_reg)
        require_sample_mode(sample, "ENERGY (sample)")
        # Both halves of this are O(model): the sample is copied out of its
        # register and every coefficient is accumulated. Charge for both
        # before either happens (QUI-1056), and only after both registers
        # have passed validation.
        terms = len(model.linear) + len(model.quadratic)
        self._charge_steps(model_eval_steps(sample.size, terms))
        energy = compute_energy(model, sample)
        self.state.push(energy)
