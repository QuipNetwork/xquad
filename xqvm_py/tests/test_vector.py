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
Tests for Vec and VecElem classes.
"""

import pytest

from xqvm_py.errors import IndexOutOfBounds, TypeMismatch
from xqvm_py.vector import Vec, VecElem
from xqvm_py.xqmx import XQMX


class TestVecElem:
    """Tests for VecElem type descriptor."""

    def test_valid_kinds(self):
        """Valid kinds should be accepted."""
        for kind in ("int", "xqmx", "vec", "unset"):
            if kind == "vec":
                elem = VecElem(kind, inner=VecElem("int"))
            else:
                elem = VecElem(kind)
            assert elem.kind == kind

    def test_kind_not_validated(self):
        """Kind is not validated - any string is accepted."""
        # Note: Implementation does not validate kind values
        elem = VecElem("custom")
        assert elem.kind == "custom"

    def test_vec_requires_inner_type(self):
        """Vec kind requires an inner type."""
        with pytest.raises(ValueError):
            VecElem("vec")

    def test_non_vec_rejects_inner_type(self):
        """Non-vec kinds cannot have inner type."""
        with pytest.raises(ValueError):
            VecElem("int", inner=VecElem("int"))

    def test_nested_types(self):
        """Nested vec types should work."""
        inner = VecElem("int")
        middle = VecElem("vec", inner=inner)
        outer = VecElem("vec", inner=middle)

        assert outer.kind == "vec"
        assert outer.inner.kind == "vec"
        assert outer.inner.inner.kind == "int"

    def test_is_unset_true_for_unset(self):
        """is_unset returns True for unset type."""
        elem = VecElem("unset")
        assert elem.is_unset() is True

    def test_is_unset_false_for_concrete_type(self):
        """is_unset returns False for concrete types."""
        assert VecElem("int").is_unset() is False
        assert VecElem("xqmx").is_unset() is False

    def test_is_unset_nested_unset(self):
        """is_unset returns True if inner type is unset."""
        elem = VecElem("vec", inner=VecElem("unset"))
        assert elem.is_unset() is True

    def test_string_representation_simple(self):
        """String representation for simple types."""
        assert str(VecElem("int")) == "int"
        assert str(VecElem("xqmx")) == "xqmx"
        assert str(VecElem("unset")) == "unset"

    def test_string_representation_vec(self):
        """String representation for vec types."""
        elem = VecElem("vec", inner=VecElem("int"))
        assert str(elem) == "vec<int>"

    def test_string_representation_nested_vec(self):
        """String representation for nested vec types."""
        inner = VecElem("vec", inner=VecElem("int"))
        outer = VecElem("vec", inner=inner)
        assert str(outer) == "vec<vec<int>>"


class TestVecCreation:
    """Tests for Vec creation and factory methods."""

    def test_empty_vec_creation(self):
        """Create an empty vec with default unset type."""
        v = Vec()
        assert v.length == 0
        assert v.element_type.is_unset()

    def test_with_capacity(self):
        """with_capacity creates vec with pre-allocated space."""
        v = Vec.with_capacity(100)
        assert v.length == 0
        assert v.capacity == 100
        assert v.element_type.is_unset()

    def test_with_capacity_and_type(self):
        """with_capacity can specify element type."""
        v = Vec.with_capacity(50, VecElem("int"))
        assert v.length == 0
        assert v.capacity == 50
        assert v.element_type.kind == "int"

    def test_from_list_integers(self):
        """from_list creates vec from list of integers."""
        v = Vec.from_list([1, 2, 3, 4, 5])
        assert v.length == 5
        assert v.element_type.kind == "int"
        assert v.get(0) == 1
        assert v.get(4) == 5

    def test_from_list_empty(self):
        """from_list with empty list creates unset vec."""
        v = Vec.from_list([])
        assert v.length == 0
        assert v.element_type.is_unset()

    def test_from_list_xqmx(self):
        """from_list with XQMX values."""
        x1 = XQMX.binary_model(5)
        x2 = XQMX.binary_model(5)
        v = Vec.from_list([x1, x2])
        assert v.length == 2
        assert v.element_type.kind == "xqmx"

    def test_from_list_nested_vecs(self):
        """from_list with nested Vec values."""
        inner1 = Vec.from_list([1, 2, 3])
        inner2 = Vec.from_list([4, 5, 6])
        v = Vec.from_list([inner1, inner2])
        assert v.length == 2
        assert v.element_type.kind == "vec"
        assert v.element_type.inner.kind == "int"


class TestVecTypeInference:
    """Tests for Vec type inference and validation."""

    def test_type_inference_on_first_push_int(self):
        """First push to unset vec infers type."""
        v = Vec()
        v.push(42)
        assert v.element_type.kind == "int"

    def test_type_inference_on_first_push_xqmx(self):
        """First push of XQMX infers xqmx type."""
        v = Vec()
        v.push(XQMX.binary_model(5))
        assert v.element_type.kind == "xqmx"

    def test_type_inference_on_first_push_vec(self):
        """First push of Vec infers vec type with inner."""
        v = Vec()
        inner = Vec.from_list([1, 2, 3])
        v.push(inner)
        assert v.element_type.kind == "vec"
        assert v.element_type.inner.kind == "int"

    def test_type_validation_on_subsequent_push(self):
        """Subsequent pushes must match type."""
        v = Vec()
        v.push(1)
        v.push(2)
        v.push(3)
        assert v.length == 3

    def test_invalid_type_raises_type_error(self):
        """Pushing wrong type raises TypeMismatch."""
        v = Vec()
        v.push(1)  # Now it's vec<int>
        with pytest.raises(TypeMismatch):
            v.push(XQMX.binary_model(5))

    def test_invalid_type_string_raises_type_error(self):
        """Pushing unsupported type raises TypeMismatch."""
        v = Vec()
        with pytest.raises(TypeMismatch):
            v.push("string")

    def test_typed_vec_rejects_wrong_type(self):
        """Vec with explicit type rejects wrong element."""
        v = Vec.with_capacity(10, VecElem("int"))
        with pytest.raises(TypeMismatch):
            v.push(XQMX.binary_model(5))


class TestVecAccess:
    """Tests for Vec get/set operations."""

    def test_get_valid_index(self):
        """Get at valid index returns element."""
        v = Vec.from_list([10, 20, 30])
        assert v.get(0) == 10
        assert v.get(1) == 20
        assert v.get(2) == 30

    def test_get_negative_index_raises(self):
        """Get with negative index raises IndexOutOfBounds."""
        v = Vec.from_list([1, 2, 3])
        with pytest.raises(IndexOutOfBounds):
            v.get(-1)

    def test_get_out_of_bounds_raises(self):
        """Get beyond length raises IndexOutOfBounds."""
        v = Vec.from_list([1, 2, 3])
        with pytest.raises(IndexOutOfBounds):
            v.get(3)

    def test_set_valid_index(self):
        """Set at valid index updates element."""
        v = Vec.from_list([1, 2, 3])
        v.set(1, 99)
        assert v.get(1) == 99

    def test_set_negative_index_raises(self):
        """Set with negative index raises IndexOutOfBounds."""
        v = Vec.from_list([1, 2, 3])
        with pytest.raises(IndexOutOfBounds):
            v.set(-1, 0)

    def test_set_out_of_bounds_raises(self):
        """Set beyond length raises IndexOutOfBounds."""
        v = Vec.from_list([1, 2, 3])
        with pytest.raises(IndexOutOfBounds):
            v.set(3, 99)

    def test_set_wrong_type_raises(self):
        """Set with wrong type raises TypeMismatch."""
        v = Vec.from_list([1, 2, 3])
        with pytest.raises(TypeMismatch):
            v.set(0, XQMX.binary_model(5))


class TestVecProperties:
    """Tests for Vec length and capacity properties."""

    def test_length_empty(self):
        """Empty vec has length 0."""
        assert Vec().length == 0

    def test_length_after_push(self):
        """Length increases after push."""
        v = Vec()
        v.push(1)
        assert v.length == 1
        v.push(2)
        assert v.length == 2

    def test_capacity_default(self):
        """Default capacity is 0 for empty vec."""
        v = Vec()
        assert v.capacity == 0

    def test_capacity_from_with_capacity(self):
        """with_capacity sets correct capacity."""
        v = Vec.with_capacity(100)
        assert v.capacity == 100

    def test_len_dunder(self):
        """__len__ returns element count."""
        v = Vec.from_list([1, 2, 3, 4])
        assert len(v) == 4


class TestVecIteration:
    """Tests for Vec iteration support."""

    def test_iteration_empty(self):
        """Iterating empty vec yields nothing."""
        v = Vec()
        result = list(v)
        assert result == []

    def test_iteration_with_elements(self):
        """Iterating vec yields all elements."""
        v = Vec.from_list([1, 2, 3, 4, 5])
        result = list(v)
        assert result == [1, 2, 3, 4, 5]

    def test_iteration_preserves_order(self):
        """Iteration preserves insertion order."""
        v = Vec()
        for i in [10, 20, 30]:
            v.push(i)
        assert list(v) == [10, 20, 30]


class TestVecNested:
    """Tests for nested Vec operations."""

    def test_nested_vec_creation(self):
        """Create vec of vecs."""
        outer = Vec()
        inner1 = Vec.from_list([1, 2])
        inner2 = Vec.from_list([3, 4])
        outer.push(inner1)
        outer.push(inner2)

        assert outer.length == 2
        assert outer.element_type.kind == "vec"
        assert outer.element_type.inner.kind == "int"

    def test_nested_vec_access(self):
        """Access elements in nested vec."""
        inner = Vec.from_list([10, 20, 30])
        outer = Vec.from_list([inner])

        retrieved = outer.get(0)
        assert retrieved.get(1) == 20

    def test_deeply_nested_vec(self):
        """Three levels of nesting."""
        level1 = Vec.from_list([1, 2])
        level2 = Vec.from_list([level1])
        level3 = Vec.from_list([level2])

        assert level3.element_type.kind == "vec"
        assert level3.element_type.inner.kind == "vec"
        assert level3.element_type.inner.inner.kind == "int"


class TestVecRepr:
    """Tests for Vec string representation."""

    def test_repr_empty(self):
        """Empty vec repr."""
        v = Vec()
        assert "Vec" in repr(v)

    def test_repr_with_elements(self):
        """Vec with elements includes type info."""
        v = Vec.from_list([1, 2, 3])
        r = repr(v)
        assert "int" in r or "Vec" in r
