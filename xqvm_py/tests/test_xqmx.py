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
Tests for XQMX class and operations.
"""

import pytest

from xqvm_py.errors import (
    ArithmeticOverflow,
    IndexOutOfBounds,
    InvalidGridDimensions,
    XQMXModeError,
)
from xqvm_py.limits import I64_MAX, I64_MIN
from xqvm_py.xqmx import (
    XQMX,
    XQMXDomain,
    XQMXMode,
    col_find,
    col_indices,
    col_sum,
    compute_energy,
    expand_equality,
    expand_exclude,
    expand_implies,
    expand_onehot,
    expand_reduce,
    require_model_mode,
    row_find,
    row_indices,
    row_sum,
)


class TestXQMXConstruction:
    """Tests for XQMX factory methods and construction."""

    def test_binary_model(self):
        """binary_model creates correct XQMX."""
        x = XQMX.binary_model(size=10)
        assert x.mode == XQMXMode.MODEL
        assert x.domain == XQMXDomain.BINARY
        assert x.size == 10
        assert x.rows == 0
        assert x.cols == 0

    def test_spin_model(self):
        """spin_model creates correct XQMX."""
        x = XQMX.spin_model(size=15)
        assert x.mode == XQMXMode.MODEL
        assert x.domain == XQMXDomain.SPIN
        assert x.size == 15

    def test_discrete_model(self):
        """discrete_model creates correct XQMX."""
        x = XQMX.discrete_model(size=20, k=4)
        assert x.mode == XQMXMode.MODEL
        assert x.domain == XQMXDomain.DISCRETE
        assert x.size == 20
        assert x.discrete_k == 4

    def test_binary_sample(self):
        """binary_sample creates correct XQMX."""
        x = XQMX.binary_sample(size=10)
        assert x.mode == XQMXMode.SAMPLE
        assert x.domain == XQMXDomain.BINARY
        assert x.size == 10

    def test_spin_sample(self):
        """spin_sample creates correct XQMX with every position at -1 (QUI-453)."""
        x = XQMX.spin_sample(size=12)
        assert x.mode == XQMXMode.SAMPLE
        assert x.domain == XQMXDomain.SPIN
        assert x.size == 12
        assert all(x.get_linear(i) == -1 for i in range(12))
        assert len(x.linear) == 12

    def test_discrete_sample(self):
        """discrete_sample creates correct XQMX."""
        x = XQMX.discrete_sample(size=8, k=3)
        assert x.mode == XQMXMode.SAMPLE
        assert x.domain == XQMXDomain.DISCRETE
        assert x.discrete_k == 3

    def test_grid_dimensions(self):
        """Factory methods accept grid dimensions."""
        x = XQMX.binary_model(size=25, rows=5, cols=5)
        assert x.rows == 5
        assert x.cols == 5

    def test_negative_size_raises(self):
        """Negative size should raise ValueError."""
        with pytest.raises(ValueError):
            XQMX.binary_model(size=-1)

    def test_discrete_k_validation(self):
        """Discrete k < 2 should raise ValueError."""
        with pytest.raises(ValueError):
            XQMX.discrete_model(size=10, k=1)


class TestXQMXModeChecks:
    """Tests for mode checking methods."""

    def test_is_model_true(self, binary_model):
        """is_model returns True for MODEL mode."""
        assert binary_model.is_model() is True

    def test_is_model_false(self, binary_sample):
        """is_model returns False for SAMPLE mode."""
        assert binary_sample.is_model() is False

    def test_is_sample_true(self, binary_sample):
        """is_sample returns True for SAMPLE mode."""
        assert binary_sample.is_sample() is True

    def test_is_sample_false(self, binary_model):
        """is_sample returns False for MODEL mode."""
        assert binary_model.is_sample() is False


class TestLinearCoefficients:
    """Tests for linear coefficient operations."""

    def test_get_linear_default(self, binary_model):
        """get_linear returns 0 for unset index."""
        assert binary_model.get_linear(0) == 0
        assert binary_model.get_linear(5) == 0

    def test_set_linear(self, binary_model):
        """set_linear stores coefficient."""
        binary_model.set_linear(3, 5)
        assert binary_model.get_linear(3) == 5

    def test_set_linear_zero_removes(self, binary_model):
        """set_linear with 0 removes key (sparse)."""
        binary_model.set_linear(0, 5)
        binary_model.set_linear(0, 0)
        assert 0 not in binary_model.linear

    def test_add_linear(self, binary_model):
        """add_linear adds to existing coefficient."""
        binary_model.set_linear(0, 1)
        binary_model.add_linear(0, 2)
        assert binary_model.get_linear(0) == 3

    def test_add_linear_to_unset(self, binary_model):
        """add_linear to unset index creates it."""
        binary_model.add_linear(5, 3)
        assert binary_model.get_linear(5) == 3

    def test_linear_index_bounds(self, binary_model):
        """Out of bounds index raises IndexError."""
        with pytest.raises(IndexError):
            binary_model.set_linear(100, 1)

    def test_linear_negative_index(self, binary_model):
        """Negative index raises IndexError."""
        with pytest.raises(IndexError):
            binary_model.set_linear(-1, 1)


class TestQuadraticCoefficients:
    """Tests for quadratic coefficient operations."""

    def test_get_quadratic_default(self, binary_model):
        """get_quadratic returns 0 for unset pair."""
        assert binary_model.get_quadratic(0, 1) == 0

    def test_set_quadratic(self, binary_model):
        """set_quadratic stores coefficient."""
        binary_model.set_quadratic(0, 1, 7)
        assert binary_model.get_quadratic(0, 1) == 7

    def test_quadratic_index_normalization(self, binary_model):
        """Indices are normalized so i < j."""
        binary_model.set_quadratic(5, 2, 4)
        # Should be stored as (2, 5)
        assert binary_model.get_quadratic(2, 5) == 4
        assert binary_model.get_quadratic(5, 2) == 4

    def test_set_quadratic_zero_removes(self, binary_model):
        """set_quadratic with 0 removes key (sparse)."""
        binary_model.set_quadratic(0, 1, 5)
        binary_model.set_quadratic(0, 1, 0)
        assert (0, 1) not in binary_model.quadratic

    def test_add_quadratic(self, binary_model):
        """add_quadratic adds to existing coefficient."""
        binary_model.set_quadratic(0, 1, 1)
        binary_model.add_quadratic(0, 1, 2)
        assert binary_model.get_quadratic(0, 1) == 3

    def test_add_quadratic_to_unset(self, binary_model):
        """add_quadratic to unset pair creates it."""
        binary_model.add_quadratic(3, 4, 2)
        assert binary_model.get_quadratic(3, 4) == 2

    def test_quadratic_index_bounds(self, binary_model):
        """Out of bounds indices raise IndexError."""
        with pytest.raises(IndexError):
            binary_model.set_quadratic(0, 100, 1)

    def test_quadratic_same_index_allowed(self, binary_model):
        """Same index (i == j) may be allowed depending on implementation."""
        # Some implementations allow self-couplings, some don't
        # Test whatever the actual behavior is
        try:
            binary_model.set_quadratic(0, 0, 1)
            # If it doesn't raise, check it stored correctly
            assert binary_model.get_quadratic(0, 0) == 1
        except (IndexError, ValueError):
            # If it raises, that's also valid behavior
            pass


class TestGridOperations:
    """Tests for grid-based operations."""

    def test_grid_index(self, grid_model):
        """grid_index converts row/col to linear index."""
        # For 5x5 grid: index = row * cols + col
        assert grid_model.grid_index(0, 0) == 0
        assert grid_model.grid_index(0, 4) == 4
        assert grid_model.grid_index(1, 0) == 5
        assert grid_model.grid_index(4, 4) == 24

    def test_row_indices(self, grid_model):
        """row_indices returns all indices in row."""
        indices = row_indices(grid_model, 0)
        assert indices == [0, 1, 2, 3, 4]

        indices = row_indices(grid_model, 2)
        assert indices == [10, 11, 12, 13, 14]

    def test_col_indices(self, grid_model):
        """col_indices returns all indices in column."""
        indices = col_indices(grid_model, 0)
        assert indices == [0, 5, 10, 15, 20]

        indices = col_indices(grid_model, 2)
        assert indices == [2, 7, 12, 17, 22]

    def test_row_sum(self, grid_model):
        """row_sum sums linear values in row."""
        grid_model.set_linear(0, 1)
        grid_model.set_linear(1, 2)
        grid_model.set_linear(2, 3)

        assert row_sum(grid_model, 0) == 6
        assert row_sum(grid_model, 1) == 0  # No values set

    def test_col_sum(self, grid_model):
        """col_sum sums linear values in column."""
        grid_model.set_linear(0, 1)
        grid_model.set_linear(5, 2)
        grid_model.set_linear(10, 3)

        assert col_sum(grid_model, 0) == 6
        assert col_sum(grid_model, 1) == 0

    def test_row_sum_overflow_raises(self, grid_model):
        """row_sum with a partial sum past i64::MAX raises ArithmeticOverflow."""
        grid_model.set_linear(0, I64_MAX)
        grid_model.set_linear(1, 1)

        with pytest.raises(ArithmeticOverflow):
            row_sum(grid_model, 0)

    def test_col_sum_overflow_raises(self, grid_model):
        """col_sum with a partial sum past i64::MIN raises ArithmeticOverflow."""
        grid_model.set_linear(0, I64_MIN)
        grid_model.set_linear(5, -1)

        with pytest.raises(ArithmeticOverflow):
            col_sum(grid_model, 0)

    def test_row_sum_row_out_of_range_raises(self, grid_model):
        """row_sum with a row at the extent raises IndexOutOfBounds."""
        with pytest.raises(IndexOutOfBounds):
            row_sum(grid_model, 5)

    def test_col_find_col_out_of_range_raises(self, grid_model):
        """col_find with a column past the extent raises IndexOutOfBounds."""
        with pytest.raises(IndexOutOfBounds):
            col_find(grid_model, 7, 1)

    def test_row_sum_ungridded_raises(self):
        """row_sum on an ungridded model raises InvalidGridDimensions."""
        model = XQMX.binary_model(size=4)
        with pytest.raises(InvalidGridDimensions):
            row_sum(model, 0)

    def test_row_find_ungridded_raises(self):
        """row_find on an ungridded model raises instead of returning -1."""
        model = XQMX.binary_model(size=4)
        with pytest.raises(InvalidGridDimensions):
            row_find(model, 0, 1)

    def test_row_find(self, grid_model):
        """row_find finds first column with value."""
        grid_model.set_linear(2, 1)  # Row 0, Col 2
        assert row_find(grid_model, 0, 1) == 2

    def test_row_find_not_found(self, grid_model):
        """row_find returns -1 if value not found."""
        assert row_find(grid_model, 0, 1) == -1

    def test_col_find(self, grid_model):
        """col_find finds first row with value."""
        grid_model.set_linear(7, 1)  # Row 1, Col 2
        assert col_find(grid_model, 2, 1) == 1

    def test_col_find_not_found(self, grid_model):
        """col_find returns -1 if value not found."""
        assert col_find(grid_model, 0, 1) == -1

    def test_grid_without_dimensions_raises(self):
        """Grid ops on non-grid XQMX raise InvalidGridDimensions."""
        x = XQMX.binary_model(size=10)  # No grid dimensions
        with pytest.raises(InvalidGridDimensions):
            row_indices(x, 0)

    def test_row_out_of_bounds(self, grid_model):
        """Row out of bounds raises IndexOutOfBounds."""
        with pytest.raises(IndexOutOfBounds):
            row_indices(grid_model, 10)

    def test_col_out_of_bounds(self, grid_model):
        """Column out of bounds raises IndexOutOfBounds."""
        with pytest.raises(IndexOutOfBounds):
            col_indices(grid_model, 10)


class TestHLFExpandOnehot:
    """Tests for expand_onehot high-level function."""

    def test_expand_onehot_linear_terms(self):
        """expand_onehot adds linear terms."""
        model = XQMX.binary_model(size=5)
        expand_onehot(model, [0, 1, 2], penalty=1)

        # Linear terms should be set
        assert model.get_linear(0) != 0
        assert model.get_linear(1) != 0
        assert model.get_linear(2) != 0

    def test_expand_onehot_quadratic_terms(self):
        """expand_onehot adds quadratic terms."""
        model = XQMX.binary_model(size=5)
        expand_onehot(model, [0, 1, 2], penalty=1)

        # Quadratic terms for all pairs
        assert model.get_quadratic(0, 1) != 0
        assert model.get_quadratic(0, 2) != 0
        assert model.get_quadratic(1, 2) != 0

    def test_expand_onehot_requires_model(self):
        """expand_onehot requires MODEL mode."""
        sample = XQMX.binary_sample(size=5)
        with pytest.raises(XQMXModeError):
            expand_onehot(sample, [0, 1], penalty=1)


class TestHLFExpandExclude:
    """Tests for expand_exclude high-level function."""

    def test_expand_exclude_quadratic(self):
        """expand_exclude adds quadratic term."""
        model = XQMX.binary_model(size=5)
        expand_exclude(model, 0, 1, penalty=2)

        assert model.get_quadratic(0, 1) == 2

    def test_expand_exclude_requires_model(self):
        """expand_exclude requires MODEL mode."""
        sample = XQMX.binary_sample(size=5)
        with pytest.raises(XQMXModeError):
            expand_exclude(sample, 0, 1, penalty=1)


class TestHLFExpandImplies:
    """Tests for expand_implies high-level function."""

    def test_expand_implies_linear_and_quadratic(self):
        """expand_implies adds linear and quadratic terms."""
        model = XQMX.binary_model(size=5)
        expand_implies(model, 0, 1, penalty=1)

        # Should set both linear and quadratic
        assert model.get_linear(0) != 0 or model.get_quadratic(0, 1) != 0

    def test_expand_implies_requires_model(self):
        """expand_implies requires MODEL mode."""
        sample = XQMX.binary_sample(size=5)
        with pytest.raises(XQMXModeError):
            expand_implies(sample, 0, 1, penalty=1)


class TestHLFExpandEquality:
    """Tests for expand_equality high-level function."""

    def test_unit_coeffs_matches_onehot(self):
        """Unit coefficients with target=1 produces same terms as expand_onehot."""
        model_eq = XQMX.binary_model(size=5)
        model_oh = XQMX.binary_model(size=5)
        indices = [0, 1, 2]
        expand_equality(model_eq, indices, [1, 1, 1], target=1, penalty=10)
        expand_onehot(model_oh, indices, penalty=10)
        assert model_eq.linear == model_oh.linear
        assert model_eq.quadratic == model_oh.quadratic

    def test_linear_terms(self):
        """Verify linear term formula: P * a_k * (a_k - 2*b)."""
        model = XQMX.binary_model(size=3)
        # indices=[0,1], coeffs=[2,3], target=5, penalty=1
        # linear[0] = 1 * 2 * (2 - 10) = -16
        # linear[1] = 1 * 3 * (3 - 10) = -21
        expand_equality(model, [0, 1], [2, 3], target=5, penalty=1)
        assert model.get_linear(0) == -16
        assert model.get_linear(1) == -21

    def test_quadratic_terms(self):
        """Verify quadratic term formula: P * 2 * a_k * a_m for k < m."""
        model = XQMX.binary_model(size=3)
        # indices=[0,1], coeffs=[2,3], target=5, penalty=1
        # quad[0,1] = 1 * 2 * 2 * 3 = 12
        expand_equality(model, [0, 1], [2, 3], target=5, penalty=1)
        assert model.get_quadratic(0, 1) == 12

    def test_single_variable(self):
        """Single variable produces only linear term, no quadratic."""
        model = XQMX.binary_model(size=3)
        # linear[0] = 10 * 3 * (3 - 2*2) = 10 * 3 * -1 = -30
        expand_equality(model, [0], [3], target=2, penalty=10)
        assert model.get_linear(0) == -30
        assert len(model.quadratic) == 0

    def test_zero_penalty(self):
        """Zero penalty adds no terms."""
        model = XQMX.binary_model(size=3)
        expand_equality(model, [0, 1], [1, 1], target=1, penalty=0)
        assert len(model.linear) == 0
        assert len(model.quadratic) == 0

    def test_length_mismatch_raises(self):
        """Mismatched indices/coeffs lengths raise ValueError."""
        model = XQMX.binary_model(size=5)
        with pytest.raises(ValueError, match="indices length"):
            expand_equality(model, [0, 1, 2], [1, 1], target=1, penalty=1)

    def test_requires_model_mode(self):
        """expand_equality requires MODEL mode."""
        sample = XQMX.binary_sample(size=5)
        with pytest.raises(XQMXModeError):
            expand_equality(sample, [0, 1], [1, 1], target=1, penalty=1)

    def test_three_vars_weighted(self):
        """Three variables with distinct weights — verify all terms."""
        model = XQMX.binary_model(size=5)
        # indices=[0,1,2], coeffs=[1,2,3], target=3, penalty=2
        # linear[0] = 2 * 1 * (1 - 6) = -10
        # linear[1] = 2 * 2 * (2 - 6) = -16
        # linear[2] = 2 * 3 * (3 - 6) = -18
        # quad[0,1] = 2 * 2 * 1 * 2 = 8
        # quad[0,2] = 2 * 2 * 1 * 3 = 12
        # quad[1,2] = 2 * 2 * 2 * 3 = 24
        expand_equality(model, [0, 1, 2], [1, 2, 3], target=3, penalty=2)
        assert model.get_linear(0) == -10
        assert model.get_linear(1) == -16
        assert model.get_linear(2) == -18
        assert model.get_quadratic(0, 1) == 8
        assert model.get_quadratic(0, 2) == 12
        assert model.get_quadratic(1, 2) == 24


class TestHLFExpandReduce:
    """Tests for expand_reduce high-level function."""

    def test_allocates_auxiliary(self):
        """expand_reduce grows model.size by 1 and returns old size."""
        model = XQMX.binary_model(size=3)
        w = expand_reduce(model, 0, 1, p_aux=10)
        assert w == 3
        assert model.size == 4

    def test_rosenberg_terms(self):
        """Verify Rosenberg enforcement terms."""
        model = XQMX.binary_model(size=3)
        w = expand_reduce(model, 0, 1, p_aux=10)
        # quad[0,1] += 10
        assert model.get_quadratic(0, 1) == 10
        # quad[0,w] += -20
        assert model.get_quadratic(0, w) == -20
        # quad[1,w] += -20
        assert model.get_quadratic(1, w) == -20
        # linear[w] += 30
        assert model.get_linear(w) == 30

    def test_var_a_out_of_range_raises(self):
        """var_a out of range raises ValueError."""
        model = XQMX.binary_model(size=3)
        with pytest.raises(ValueError, match="var_a"):
            expand_reduce(model, 5, 1, p_aux=10)

    def test_var_b_out_of_range_raises(self):
        """var_b out of range raises ValueError."""
        model = XQMX.binary_model(size=3)
        with pytest.raises(ValueError, match="var_b"):
            expand_reduce(model, 0, 5, p_aux=10)

    def test_negative_var_raises(self):
        """Negative variable index raises ValueError."""
        model = XQMX.binary_model(size=3)
        with pytest.raises(ValueError):
            expand_reduce(model, -1, 1, p_aux=10)

    def test_requires_model_mode(self):
        """expand_reduce requires MODEL mode."""
        sample = XQMX.binary_sample(size=5)
        with pytest.raises(XQMXModeError):
            expand_reduce(sample, 0, 1, p_aux=10)

    def test_chaining(self):
        """Two successive REDUCE calls allocate distinct auxiliaries."""
        model = XQMX.binary_model(size=4)
        w1 = expand_reduce(model, 0, 1, p_aux=10)
        w2 = expand_reduce(model, 2, 3, p_aux=10)
        assert w1 == 4
        assert w2 == 5
        assert model.size == 6

    def test_brute_force_cubic(self):
        """Brute-force: minimum QUBO energy at w = x_a * x_b for all assignments."""
        model = XQMX.binary_model(size=2)
        w = expand_reduce(model, 0, 1, p_aux=10)
        assert w == 2

        for x_a in (0, 1):
            for x_b in (0, 1):
                expected_w = x_a * x_b
                best_energy = None
                best_w_val = None
                for w_val in (0, 1):
                    sample = XQMX.binary_sample(size=3)
                    sample.set_linear(0, x_a)
                    sample.set_linear(1, x_b)
                    sample.set_linear(2, w_val)
                    e = compute_energy(model, sample)
                    if best_energy is None or e < best_energy:
                        best_energy = e
                        best_w_val = w_val
                assert best_w_val == expected_w, f"x_a={x_a}, x_b={x_b}: expected w={expected_w}, got w={best_w_val}"


class TestComputeEnergy:
    """Tests for compute_energy function."""

    def test_energy_linear_only(self):
        """Energy from linear terms only."""
        model = XQMX.binary_model(size=3)
        model.set_linear(0, 1)
        model.set_linear(1, 2)
        model.set_linear(2, 3)

        sample = XQMX.binary_sample(size=3)
        sample.set_linear(0, 1)  # x0 = 1
        sample.set_linear(1, 1)  # x1 = 1
        sample.set_linear(2, 0)  # x2 = 0

        energy = compute_energy(model, sample)
        # Energy = 1*1 + 2*1 + 3*0 = 3
        assert energy == 3

    def test_energy_quadratic_only(self):
        """Energy from quadratic terms only."""
        model = XQMX.binary_model(size=3)
        model.set_quadratic(0, 1, 2)

        sample = XQMX.binary_sample(size=3)
        sample.set_linear(0, 1)
        sample.set_linear(1, 1)
        sample.set_linear(2, 0)

        energy = compute_energy(model, sample)
        # Energy = 2 * 1 * 1 = 2
        assert energy == 2

    def test_energy_combined(self):
        """Energy from both linear and quadratic terms."""
        model = XQMX.binary_model(size=2)
        model.set_linear(0, 1)
        model.set_linear(1, 2)
        model.set_quadratic(0, 1, 3)

        sample = XQMX.binary_sample(size=2)
        sample.set_linear(0, 1)
        sample.set_linear(1, 1)

        energy = compute_energy(model, sample)
        # Energy = 1*1 + 2*1 + 3*1*1 = 6
        assert energy == 6

    def test_energy_partial_sum_overflow_raises(self):
        """A partial sum past the i64 range raises even if the total fits."""
        model = XQMX.binary_model(size=3)
        model.set_linear(0, I64_MAX)
        model.set_linear(1, 5)
        model.set_linear(2, -10)

        sample = XQMX.binary_sample(size=3)
        sample.set_linear(0, 1)
        sample.set_linear(1, 1)
        sample.set_linear(2, 1)

        # Sorted order: I64_MAX, then +5 overflows before -10 could bring
        # the exact total (I64_MAX - 5) back into range.
        with pytest.raises(ArithmeticOverflow):
            compute_energy(model, sample)

    def test_energy_term_product_overflow_raises(self):
        """A term product past the i64 range raises before the sum begins."""
        model = XQMX.spin_model(size=1)
        model.set_linear(0, I64_MIN)

        sample = XQMX.spin_sample(size=1)
        sample.set_linear(0, -1)

        with pytest.raises(ArithmeticOverflow):
            compute_energy(model, sample)

    def test_energy_size_mismatch_raises(self):
        """Size mismatch raises ValueError."""
        model = XQMX.binary_model(size=5)
        sample = XQMX.binary_sample(size=3)

        with pytest.raises(ValueError):
            compute_energy(model, sample)

    def test_energy_zero_for_empty(self):
        """Empty model and sample have zero energy."""
        model = XQMX.binary_model(size=5)
        sample = XQMX.binary_sample(size=5)

        assert compute_energy(model, sample) == 0


class TestRequireModeValidators:
    """Tests for mode validation functions."""

    def test_require_model_mode_passes(self, binary_model):
        """require_model_mode passes for MODEL."""
        require_model_mode(binary_model, "test")  # Should not raise

    def test_require_model_mode_fails(self, binary_sample):
        """require_model_mode raises for SAMPLE."""
        with pytest.raises(XQMXModeError):
            require_model_mode(binary_sample, "test")


class TestXQMXDomain:
    """Tests for XQMXDomain enum."""

    def test_domain_values(self):
        """Domain enum has expected values."""
        assert XQMXDomain.BINARY.value is not None
        assert XQMXDomain.SPIN.value is not None
        assert XQMXDomain.DISCRETE.value is not None

    def test_domain_count(self):
        """Should have exactly 3 domains."""
        assert len(XQMXDomain) == 3


class TestXQMXMode:
    """Tests for XQMXMode enum."""

    def test_mode_values(self):
        """Mode enum has expected values."""
        assert XQMXMode.MODEL.value is not None
        assert XQMXMode.SAMPLE.value is not None

    def test_mode_count(self):
        """Should have exactly 2 modes."""
        assert len(XQMXMode) == 2


class TestAccumulationOrder:
    """Model terms are visited in sorted key order, not insertion order.

    Wrapping addition is associative, so today every order reaches the
    same total and the choice is invisible. Once overflow raises instead
    of wrapping (QUI-998), order decides *whether a program errors at
    all*: a partial sum can exceed the i64 range in one order and stay
    inside it in the other. Sorted key order is normative because it is
    reproducible from the model alone, independent of the program history
    that happened to build it.
    """

    def test_iter_linear_yields_sorted_keys(self):
        m = XQMX.binary_model(size=8)
        for i in (5, 0, 3, 1):
            m.set_linear(i, i + 1)

        assert [i for i, _ in m.iter_linear()] == [0, 1, 3, 5]

    def test_iter_quadratic_yields_sorted_keys(self):
        m = XQMX.binary_model(size=8)
        for i, j in ((2, 3), (0, 1), (1, 2)):
            m.set_quadratic(i, j, 1)

        assert [key for key, _ in m.iter_quadratic()] == [(0, 1), (1, 2), (2, 3)]

    def test_iterators_are_independent_of_insertion_order(self):
        forward = XQMX.binary_model(size=4)
        reverse = XQMX.binary_model(size=4)
        for i in (0, 1, 2, 3):
            forward.set_linear(i, i + 1)
        for i in (3, 2, 1, 0):
            reverse.set_linear(i, i + 1)

        assert list(forward.iter_linear()) == list(reverse.iter_linear())
