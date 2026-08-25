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
XQVM Vector Types: Vec, VecElem
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .errors import IndexOutOfBounds, TypeMismatch


@dataclass
class VecElem:
    """
    Describes vec element type, supports nesting.

    Examples:
      VecElem("int")                      -> vec<int>
      VecElem("xqmx")                     -> vec<xqmx>
      VecElem("vec", VecElem("int"))      -> vec<vec<int>>
      VecElem("vec", VecElem("unset"))    -> vec<vec<?>> (inner TBD)

    UNSET is only valid as innermost type.
    """

    kind: str  # "int" | "xqmx" | "vec" | "unset"
    inner: VecElem | None = None  # Required if kind="vec"

    def __post_init__(self) -> None:
        if self.kind == "vec" and self.inner is None:
            raise ValueError("vec type requires inner type")
        if self.kind != "vec" and self.inner is not None:
            raise ValueError(f"{self.kind} type cannot have inner type")

    def is_unset(self) -> bool:
        """Check if this type or any nested type is unset."""
        if self.kind == "unset":
            return True
        return self.inner.is_unset() if self.inner else False

    def __str__(self) -> str:
        if self.kind == "vec":
            return f"vec<{self.inner}>"
        return self.kind


@dataclass
class Vec:
    """
    Homogeneous dynamic array with type inference.

    Vec stores elements that can be int, Vec, or XQMX.
    It tracks both length (current elements) and capacity (allocated space).
    Type is inferred on first push, or set explicitly via VECI/VECX opcodes.
    """

    element_type: VecElem = field(default_factory=lambda: VecElem("unset"))
    _elements: list[Any] = field(default_factory=list)
    _capacity: int = 0

    def __post_init__(self) -> None:
        if self._capacity < len(self._elements):
            self._capacity = len(self._elements)

    @classmethod
    def with_capacity(cls, capacity: int, element_type: VecElem | None = None) -> Vec:
        """Create an empty Vec with pre-allocated capacity and optional type."""
        vec = cls()
        vec._capacity = capacity
        if element_type is not None:
            vec.element_type = element_type
        return vec

    @classmethod
    def from_list(cls, elements: list[Any]) -> Vec:
        """Create a Vec from a list of elements. Type inferred from first element."""
        vec = cls(_elements=list(elements), _capacity=len(elements))
        if elements:
            vec.element_type = vec._infer_type(elements[0])
        return vec

    @property
    def length(self) -> int:
        """Current number of elements."""
        return len(self._elements)

    @property
    def capacity(self) -> int:
        """Allocated capacity."""
        return self._capacity

    def push(self, element: Any) -> None:
        """Append an element to the vector. Infers type on first push."""
        if self.element_type.kind == "unset":
            self.element_type = self._infer_type(element)
        else:
            self._validate_element(element)

        self._elements.append(element)
        if len(self._elements) > self._capacity:
            self._capacity = len(self._elements)

    def get(self, index: int) -> Any:
        """Get element at index. Raises IndexOutOfBounds if out of range."""
        if index < 0 or index >= len(self._elements):
            raise IndexOutOfBounds(index, len(self._elements))
        return self._elements[index]

    def set(self, index: int, value: Any) -> None:
        """Set element at index. Raises IndexOutOfBounds if out of range."""
        if index < 0 or index >= len(self._elements):
            raise IndexOutOfBounds(index, len(self._elements))
        self._validate_element(value)
        self._elements[index] = value

    def _infer_type(self, element: Any) -> VecElem:
        """Infer the VecElem type from an element."""
        # Import here to avoid circular import
        from .xqmx import XQMX

        if isinstance(element, int):
            return VecElem("int")
        elif isinstance(element, XQMX):
            return VecElem("xqmx")
        elif isinstance(element, Vec):
            return VecElem("vec", element.element_type)
        else:
            raise TypeMismatch("int|xqmx|vec", type(element).__name__, "vec element")

    def _validate_element(self, element: Any) -> None:
        """Validate that an element matches the vec's type."""
        inferred = self._infer_type(element)
        if not self._types_compatible(self.element_type, inferred):
            raise TypeMismatch(str(self.element_type), str(inferred), "vec element")

    def _types_compatible(self, expected: VecElem, actual: VecElem) -> bool:
        """Check if actual type is compatible with expected type."""
        if expected.kind == "unset":
            return True
        if expected.kind != actual.kind:
            return False
        if expected.kind == "vec":
            return self._types_compatible(expected.inner, actual.inner)
        return True

    def __len__(self) -> int:
        return len(self._elements)

    def __repr__(self) -> str:
        return f"Vec<{self.element_type}>({self._elements})"

    def __iter__(self):
        return iter(self._elements)
