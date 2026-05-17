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
Tests for Opcode enum and metadata.
"""

from xqvm_py.opcodes import Opcode, OpcodeMeta, OperandType


class TestOpcodeCount:
    """Tests for opcode count and completeness."""

    def test_total_opcode_count(self):
        """Should have exactly 93 opcodes."""
        assert len(Opcode) == 93

    def test_all_opcodes_have_metadata(self):
        """Every opcode must have valid metadata."""
        for op in Opcode:
            meta = op.meta
            assert isinstance(meta, OpcodeMeta)
            assert isinstance(meta.code, int)
            assert isinstance(meta.description, str)
            assert len(meta.description) > 0


class TestOpcodeCodeLookup:
    """Tests for from_code lookup."""

    def test_from_code_valid(self):
        """from_code returns correct opcode for valid codes."""
        assert Opcode.from_code(0x00) == Opcode.TARGET
        assert Opcode.from_code(0x09) == Opcode.ITER
        assert Opcode.from_code(0x11) == Opcode.PUSH1
        assert Opcode.from_code(0x20) == Opcode.ADD
        assert Opcode.from_code(0xF0) == Opcode.NOP
        assert Opcode.from_code(0xFF) == Opcode.HALT

    def test_from_code_invalid(self):
        """from_code returns None for invalid codes."""
        assert Opcode.from_code(0xFE) is None
        assert Opcode.from_code(0x99) is None
        assert Opcode.from_code(-1) is None

    def test_from_code_all_opcodes(self):
        """Every opcode can be looked up by its code."""
        for op in Opcode:
            result = Opcode.from_code(op.code)
            assert result == op


class TestOpcodeNameLookup:
    """Tests for from_name lookup."""

    def test_from_name_exact(self):
        """from_name finds exact name match."""
        assert Opcode.from_name("NOP") == Opcode.NOP
        assert Opcode.from_name("HALT") == Opcode.HALT
        assert Opcode.from_name("PUSH1") == Opcode.PUSH1

    def test_from_name_case_insensitive(self):
        """from_name is case-insensitive."""
        assert Opcode.from_name("nop") == Opcode.NOP
        assert Opcode.from_name("Halt") == Opcode.HALT
        assert Opcode.from_name("pUsH1") == Opcode.PUSH1

    def test_from_name_invalid(self):
        """from_name returns None for invalid names."""
        assert Opcode.from_name("INVALID") is None
        assert Opcode.from_name("") is None
        assert Opcode.from_name("NOTANOP") is None


class TestOpcodeMetadata:
    """Tests for opcode metadata validity."""

    def test_stack_effects_non_negative(self):
        """Stack pop/push counts must be non-negative."""
        for op in Opcode:
            meta = op.meta
            assert meta.stack_pop >= 0, f"{op.name} has negative stack_pop"
            assert meta.stack_push >= 0, f"{op.name} has negative stack_push"

    def test_operand_count_matches_types(self):
        """operand_count must match operand_types length."""
        for op in Opcode:
            meta = op.meta
            assert meta.operand_count == len(meta.operand_types), (
                f"{op.name}: operand_count={meta.operand_count} but operand_types has {len(meta.operand_types)} items"
            )

    def test_operand_types_are_valid(self):
        """All operand types must be valid OperandType enum values."""
        for op in Opcode:
            meta = op.meta
            for t in meta.operand_types:
                assert isinstance(t, OperandType), f"{op.name} has invalid operand type: {t}"


class TestCodeUniqueness:
    """Tests for opcode code uniqueness."""

    def test_no_duplicate_codes(self):
        """Each opcode must have a unique code."""
        codes = [op.code for op in Opcode]
        assert len(codes) == len(set(codes)), "Duplicate opcode codes found"

    def test_no_duplicate_names(self):
        """Each opcode must have a unique name."""
        names = [op.name for op in Opcode]
        assert len(names) == len(set(names)), "Duplicate opcode names found"


class TestOpcodeGroupRanges:
    """Tests for opcode code ranges by group."""

    def test_control_flow_range(self):
        """Control flow opcodes in 0x00-0x09 range."""
        control_ops = [
            Opcode.TARGET,
            Opcode.JUMP1,
            Opcode.JUMPI1,
            Opcode.JUMP2,
            Opcode.JUMPI2,
            Opcode.LIDX,
            Opcode.LVAL,
            Opcode.NEXT,
            Opcode.RANGE,
            Opcode.ITER,
        ]
        for op in control_ops:
            assert 0x00 <= op.code <= 0x09, f"{op.name} not in control range"
        # NOP and HALT are in special range
        assert Opcode.NOP.code == 0xF0
        assert Opcode.HALT.code == 0xFF

    def test_register_manip_range(self):
        """Register manipulation opcodes in 0x0A-0x0F range."""
        reg_ops = [Opcode.LOAD, Opcode.STOW, Opcode.DROP, Opcode.INPUT, Opcode.OUTPUT]
        for op in reg_ops:
            assert 0x0A <= op.code <= 0x0F, f"{op.name} not in register range"

    def test_stack_manip_range(self):
        """Stack manipulation opcodes in 0x11-0x1E range."""
        stack_ops = [
            Opcode.PUSH1,
            Opcode.PUSH2,
            Opcode.PUSH3,
            Opcode.PUSH4,
            Opcode.PUSH5,
            Opcode.PUSH6,
            Opcode.PUSH7,
            Opcode.PUSH8,
            Opcode.POP,
            Opcode.SCLR,
            Opcode.SWAP,
            Opcode.COPY,
        ]
        for op in stack_ops:
            assert 0x10 <= op.code <= 0x1C, f"{op.name} not in stack range"

    def test_arithmetic_range(self):
        """Arithmetic opcodes in 0x20-0x2F range."""
        arith_ops = [
            Opcode.ADD,
            Opcode.SUB,
            Opcode.MUL,
            Opcode.DIV,
            Opcode.MOD,
            Opcode.SQR,
            Opcode.ABS,
            Opcode.NEG,
            Opcode.MIN,
            Opcode.MAX,
            Opcode.INC,
            Opcode.DEC,
        ]
        for op in arith_ops:
            assert 0x20 <= op.code <= 0x2F, f"{op.name} not in arithmetic range"

    def test_logical_range(self):
        """Logical opcodes (comparison, boolean, bitwise) in 0x30-0x3F range."""
        logical_ops = [
            Opcode.EQ,
            Opcode.LT,
            Opcode.GT,
            Opcode.LTE,
            Opcode.GTE,
            Opcode.NOT,
            Opcode.AND,
            Opcode.OR,
            Opcode.XOR,
            Opcode.BAND,
            Opcode.BOR,
            Opcode.BXOR,
            Opcode.BNOT,
            Opcode.SHL,
            Opcode.SHR,
        ]
        for op in logical_ops:
            assert 0x30 <= op.code <= 0x3F, f"{op.name} not in logical range"

    def test_allocator_range(self):
        """Allocator opcodes in 0x40-0x4F range."""
        alloc_ops = [
            Opcode.BQMX,
            Opcode.SQMX,
            Opcode.XQMX,
            Opcode.BSMX,
            Opcode.SSMX,
            Opcode.XSMX,
            Opcode.VEC,
            Opcode.VECI,
            Opcode.VECX,
        ]
        for op in alloc_ops:
            assert 0x40 <= op.code <= 0x4F, f"{op.name} not in allocator range"

    def test_vector_access_range(self):
        """Vector access opcodes in 0x50-0x5F range."""
        vec_ops = [Opcode.VECPUSH, Opcode.VECGET, Opcode.VECSET, Opcode.VECLEN, Opcode.IDXGRID, Opcode.IDXTRIU]
        for op in vec_ops:
            assert 0x50 <= op.code <= 0x5F, f"{op.name} not in vector range"

    def test_xqmx_range(self):
        """XQMX opcodes in 0x60-0x7F range."""
        xqmx_ops = [
            Opcode.GETLINE,
            Opcode.SETLINE,
            Opcode.ADDLINE,
            Opcode.GETQUAD,
            Opcode.SETQUAD,
            Opcode.ADDQUAD,
            Opcode.RESIZE,
            Opcode.ROWFIND,
            Opcode.COLFIND,
            Opcode.ROWSUM,
            Opcode.COLSUM,
            Opcode.ONEHOTR,
            Opcode.ONEHOTC,
            Opcode.EXCLUDE,
            Opcode.IMPLIES,
            Opcode.ENERGY,
        ]
        for op in xqmx_ops:
            assert 0x60 <= op.code <= 0x7F, f"{op.name} not in XQMX range"


class TestSpecificOpcodeMetadata:
    """Tests for specific opcode metadata values."""

    def test_nop_metadata(self):
        """NOP has no stack effect and no operands."""
        meta = Opcode.NOP.meta
        assert meta.stack_pop == 0
        assert meta.stack_push == 0
        assert meta.operand_count == 0

    def test_push_metadata(self):
        """PUSH takes immediate and pushes one value."""
        meta = Opcode.PUSH1.meta
        assert meta.stack_pop == 0
        assert meta.stack_push == 1
        assert meta.operand_count == 1
        assert meta.operand_types == (OperandType.IMMEDIATE,)

    def test_add_metadata(self):
        """ADD pops two and pushes one."""
        meta = Opcode.ADD.meta
        assert meta.stack_pop == 2
        assert meta.stack_push == 1
        assert meta.operand_count == 0

    def test_stow_metadata(self):
        """STOW pops one and takes register operand."""
        meta = Opcode.STOW.meta
        assert meta.stack_pop == 1
        assert meta.stack_push == 0
        assert meta.operand_count == 1
        assert meta.operand_types == (OperandType.REGISTER,)

    def test_load_metadata(self):
        """LOAD takes register operand and pushes value."""
        meta = Opcode.LOAD.meta
        assert meta.stack_pop == 0
        assert meta.stack_push == 1
        assert meta.operand_count == 1
        assert meta.operand_types == (OperandType.REGISTER,)

    def test_jump1_metadata(self):
        """JUMP1 takes target operand (u8)."""
        meta = Opcode.JUMP1.meta
        assert meta.operand_count == 1
        assert meta.operand_types == (OperandType.TARGET,)

    def test_jump2_metadata(self):
        """JUMP2 takes two target operands (u16)."""
        meta = Opcode.JUMP2.meta
        assert meta.operand_count == 2
        assert meta.operand_types == (OperandType.TARGET, OperandType.TARGET)

    def test_target_metadata(self):
        """TARGET has no operands."""
        meta = Opcode.TARGET.meta
        assert meta.operand_count == 0
        assert meta.operand_types == ()


class TestOpcodeCodeProperty:
    """Tests for opcode .code property."""

    def test_code_property_returns_int(self):
        """code property returns integer value."""
        for op in Opcode:
            assert isinstance(op.code, int)

    def test_code_matches_meta_code(self):
        """code property equals meta.code."""
        for op in Opcode:
            assert op.code == op.meta.code


class TestOperandType:
    """Tests for OperandType enum."""

    def test_operand_type_values(self):
        """OperandType has expected members."""
        assert hasattr(OperandType, "IMMEDIATE")
        assert hasattr(OperandType, "REGISTER")
        assert hasattr(OperandType, "TARGET")

    def test_operand_type_count(self):
        """Should have exactly 3 operand types."""
        assert len(OperandType) == 3
