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
    def set_linear(self, i: int, value: int) -> None: ...
    def get_linear(self, i: int) -> int: ...
    def add_linear(self, i: int, delta: int) -> None:
        """Raises `ArithmeticOverflow`, leaving the model unchanged, on overflow."""

    def set_quad(self, i: int, j: int, value: int) -> None: ...
    def get_quad(self, i: int, j: int) -> int: ...
    def add_quad(self, i: int, j: int, delta: int) -> None:
        """Raises `ArithmeticOverflow`, leaving the model unchanged, on overflow."""

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

class StackUnderflow(XqvmError): ...
class StackOverflow(XqvmError): ...
class TypeMismatch(XqvmError): ...
class UnsetRegister(XqvmError): ...
class DivisionByZero(XqvmError): ...
class IndexOutOfBounds(XqvmError): ...
class NoActiveLoop(XqvmError): ...
class UnmatchedLoop(XqvmError): ...
class BadJumpTarget(XqvmError): ...
class InvalidLabel(XqvmError): ...
class BadOpcode(XqvmError): ...
class TruncatedInstruction(XqvmError): ...
class CallDataIndex(XqvmError): ...
class OutputIndex(XqvmError): ...
class SizeMismatch(XqvmError): ...
class VecLengthMismatch(XqvmError): ...
class ArithmeticOverflow(XqvmError): ...
class StepLimitExceeded(XqvmError): ...
class MemoryLimitExceeded(XqvmError): ...
class InvalidShift(XqvmError): ...
class InvalidGridDimensions(XqvmError): ...
class InvalidIntegerK(XqvmError): ...
class SampleOutOfDomain(XqvmError): ...
class TraceFailed(XqvmError): ...
class InvalidAllocation(XqvmError): ...
class LoopStackOverflow(XqvmError): ...
