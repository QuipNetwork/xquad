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

"""Type stubs for `xqffi.vm`, the bindings around the Rust XQVM."""

from typing import ClassVar, Final, Self, final

__all__ = [
    "Vm",
    "Domain",
    "XqmxModel",
    "XqmxSample",
    "triu",
    "XqvmError",
    "StackUnderflow",
    "StackOverflow",
    "TypeMismatch",
    "UnsetRegister",
    "DivisionByZero",
    "IndexOutOfBounds",
    "NoActiveLoop",
    "UnmatchedLoop",
    "BadJumpTarget",
    "InvalidLabel",
    "BadOpcode",
    "TruncatedInstruction",
    "CallDataIndex",
    "OutputIndex",
    "SizeMismatch",
    "VecLengthMismatch",
    "ArithmeticOverflow",
    "StepLimitExceeded",
    "MemoryLimitExceeded",
    "InvalidShift",
    "InvalidGridDimensions",
    "InvalidIntegerK",
    "SampleOutOfDomain",
    "TraceFailed",
    "InvalidAllocation",
    "LoopStackOverflow",
    "DEFAULT_STEP_LIMIT",
    "DEFAULT_MEMORY_LIMIT",
    "MAX_ALLOCATION_SIZE",
]

DEFAULT_STEP_LIMIT: Final[int]
"""Step budget a fresh `Vm` starts with."""

DEFAULT_MEMORY_LIMIT: Final[int]
"""Allocation budget, in bytes, a fresh `Vm` starts with."""

MAX_ALLOCATION_SIZE: Final[int]
"""Largest size an allocating opcode accepts."""

type _Value = int | list[int] | XqmxModel | XqmxSample | None
"""A calldata element."""

type _Output = int | list[int] | list[XqmxModel] | XqmxModel | XqmxSample | None
"""An output slot; `None` for a slot the program never wrote."""

@final
class Domain:
    """A variable domain. Immutable, hashable, and compared by value."""

    BINARY: ClassVar[Domain]
    """The binary domain, `{0, 1}`."""
    SPIN: ClassVar[Domain]
    """The spin domain, `{-1, +1}`."""

    @staticmethod
    def integer(k: int) -> Domain:
        """The integer domain `{0, ..., k-1}`. Raises `ValueError` for `k < 2`."""

    @property
    def name(self) -> str:
        """`"binary"`, `"spin"`, or `"integer"`."""

    @property
    def k(self) -> int | None:
        """The number of values an integer domain holds; `None` otherwise."""

    @property
    def description(self) -> str:
        """The domain and its values, e.g. `"integer {0, ..., 2}"`."""

    def contains(self, value: int) -> bool:
        """Whether a sample variable in this domain may hold `value`."""

    def __eq__(self, value: object, /) -> bool: ...
    def __hash__(self) -> int: ...

@final
class XqmxModel:
    """A quadratic (QUBO, Ising or integer) optimisation model."""

    def __new__(cls, domain: Domain, size: int, rows: int = 0, cols: int = 0) -> Self: ...
    @staticmethod
    def binary(size: int, rows: int = 0, cols: int = 0) -> XqmxModel: ...
    @staticmethod
    def spin(size: int, rows: int = 0, cols: int = 0) -> XqmxModel: ...
    @staticmethod
    def integer(size: int, k: int, rows: int = 0, cols: int = 0) -> XqmxModel:
        """Raises `ValueError` for `k < 2`."""

    @property
    def domain(self) -> Domain: ...
    @property
    def k(self) -> int | None: ...
    @property
    def size(self) -> int: ...
    @property
    def rows(self) -> int: ...
    @property
    def cols(self) -> int: ...
    def set_linear(self, i: int, value: int) -> None:
        """Raises `IndexOutOfBounds` unless `0 <= i < size`."""

    def get_linear(self, i: int) -> int:
        """Raises `IndexOutOfBounds` unless `0 <= i < size`."""

    def add_linear(self, i: int, delta: int) -> None:
        """Raises `IndexOutOfBounds` unless `0 <= i < size`, and
        `ArithmeticOverflow` on overflow; either leaves the model unchanged."""

    def set_quad(self, i: int, j: int, value: int) -> None:
        """Raises `IndexOutOfBounds` unless both indices lie in `[0, size)`."""

    def get_quad(self, i: int, j: int) -> int:
        """Raises `IndexOutOfBounds` unless both indices lie in `[0, size)`."""

    def add_quad(self, i: int, j: int, delta: int) -> None:
        """Raises `IndexOutOfBounds` unless both indices lie in `[0, size)`, and
        `ArithmeticOverflow` on overflow; either leaves the model unchanged."""

    def linear_items(self) -> list[tuple[int, int]]:
        """The nonzero linear terms as `(index, coefficient)`, by index."""

    def quadratic_items(self) -> list[tuple[tuple[int, int], int]]:
        """The nonzero quadratic terms as `((i, j), coefficient)`, `i <= j`."""

    def energy(self, sample: XqmxSample) -> int:
        """The model's energy at `sample`, computed as `ENERGY` computes it.

        Raises `SizeMismatch` when the sizes differ and `ArithmeticOverflow`
        when a term or partial sum leaves the signed 64-bit range.
        """

@final
class XqmxSample:
    """A candidate solution for a model. Values are checked on construction."""

    def __new__(cls, domain: Domain, values: list[int], rows: int = 0, cols: int = 0) -> Self:
        """Raises `ValueError` if any value lies outside `domain`."""

    @staticmethod
    def binary(values: list[int], rows: int = 0, cols: int = 0) -> XqmxSample: ...
    @staticmethod
    def spin(values: list[int], rows: int = 0, cols: int = 0) -> XqmxSample: ...
    @staticmethod
    def integer(values: list[int], k: int, rows: int = 0, cols: int = 0) -> XqmxSample:
        """Raises `ValueError` for `k < 2` or a value outside the domain."""

    @property
    def domain(self) -> Domain: ...
    @property
    def k(self) -> int | None: ...
    @property
    def values(self) -> list[int]: ...
    @property
    def size(self) -> int: ...
    @property
    def rows(self) -> int: ...
    @property
    def cols(self) -> int: ...
    def __len__(self) -> int: ...

@final
class Vm:
    """The XQVM interpreter."""

    def __new__(cls) -> Self: ...
    def set_calldata(self, data: list[_Value]) -> None:
        """Raises `TypeError` for an element of any other type."""

    def set_output_slots(self, n: int) -> None: ...
    def set_step_limit(self, limit: int) -> None: ...
    def set_unlimited_steps(self) -> None: ...
    def set_memory_limit(self, bytes: int) -> None: ...
    def memory_used(self) -> int: ...
    def run(self, bytecode: bytes) -> None:
        """Raises the `XqvmError` subclass named for a fault, or `RuntimeError`
        if `bytecode` does not decode."""

    def outputs(self) -> list[_Output]: ...
    def stack(self) -> list[int]: ...
    def steps(self) -> int: ...
    def instructions(self) -> int: ...
    def reset(self) -> None: ...

def triu(i: int, j: int) -> int:
    """The index `IDXTRIU` pushes for the unordered pair `(i, j)`.

    Raises `ArithmeticOverflow` where the opcode would.
    """

class XqvmError(RuntimeError):
    """Base class of every fault the XQVM raises."""

    offset: int | None
    """Byte offset of the faulting instruction in the instruction stream, or
    `None` where the fault has no single instruction."""

class StackUnderflow(XqvmError):
    """A pop was attempted on an empty stack."""

class StackOverflow(XqvmError):
    """The value stack exceeded its depth limit."""

class TypeMismatch(XqvmError):
    """An operand had the wrong value kind."""

class UnsetRegister(XqvmError):
    """A register was read while unset."""

class DivisionByZero(XqvmError):
    """Division or modulo by zero."""

class IndexOutOfBounds(XqvmError):
    """An index fell outside the addressed container."""

class NoActiveLoop(XqvmError):
    """A loop instruction executed with no active loop."""

class UnmatchedLoop(XqvmError):
    """A RANGE or ITER had no matching NEXT."""

class BadJumpTarget(XqvmError):
    """A jump named a target the pre-scan did not register."""

class InvalidLabel(XqvmError):
    """A jump named a label the program does not define."""

class BadOpcode(XqvmError):
    """An unknown opcode byte was decoded."""

class TruncatedInstruction(XqvmError):
    """An instruction's operands ran past the end of the program."""

class CallDataIndex(XqvmError):
    """An INPUT addressed a calldata slot that does not exist."""

class OutputIndex(XqvmError):
    """An OUTPUT addressed an output slot that does not exist."""

class SizeMismatch(XqvmError):
    """A model and a sample disagreed on variable count."""

class VecLengthMismatch(XqvmError):
    """Two vector operands disagreed on length."""

class ArithmeticOverflow(XqvmError):
    """An operation produced a value outside the signed 64-bit range."""

class StepLimitExceeded(XqvmError):
    """Execution ran past its step budget."""

class MemoryLimitExceeded(XqvmError):
    """An allocating instruction ran past its allocation budget."""

class InvalidShift(XqvmError):
    """SHL or SHR was given a shift amount outside [0, 63]."""

class InvalidGridDimensions(XqvmError):
    """Grid dimensions were not positive, or did not fit the model."""

class InvalidIntegerK(XqvmError):
    """An XQMX or XSMX allocation used k < 2."""

class SampleOutOfDomain(XqvmError):
    """A SETLINE or ADDLINE write put a value outside a sample's domain."""

class TraceFailed(XqvmError):
    """A tracer refused a step."""

class InvalidAllocation(XqvmError):
    """An allocator was handed a size that is not an allocation."""

class LoopStackOverflow(XqvmError):
    """Loop nesting exceeded its depth limit."""
