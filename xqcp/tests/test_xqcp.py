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
Tests for the constraint programming DSL (xqvm.cp).
"""

from xqcp import CompiledPrograms, Problem, Types, xq_triu
from xqvm_py import XQMXDomain

# ---------------------------------------------------------------------------
# Helper: build the TSP problem
# ---------------------------------------------------------------------------


def build_tsp_problem() -> Problem:
    """Build the reference TSP problem using the CP DSL."""
    problem = Problem("TSP")

    num_cities = problem.input("num_cities", type=Types.Int)
    distance_matrix = problem.input("distance_matrix", type=Types.Vec)

    problem.define_model(
        size=num_cities * num_cities,
        domain=XQMXDomain.BINARY,
        rows=num_cities,
        cols=num_cities,
    )

    with problem.range(0, num_cities - 1) as city_i:
        with problem.range(city_i + 1, num_cities) as city_j:
            dist = problem.stow("dist", distance_matrix.get(xq_triu(city_i, city_j)))
            with problem.range(0, num_cities) as position:
                next_position = (position + 1) % num_cities
                problem.model.quadratic[(city_i, position), (city_j, next_position)].add(dist)
                problem.model.quadratic[(city_j, position), (city_i, next_position)].add(dist)

    with problem.range(0, num_cities) as city:
        problem.model.apply_onehot_row(city, penalty=100)

    with problem.range(0, num_cities) as position:
        problem.model.apply_onehot_col(position, penalty=100)

    tour = problem.output("tour", type=Types.Vec)
    with problem.range(0, num_cities) as position:
        tour.append(problem.sample.colfind(col=position, value=1))

    return problem


# ---------------------------------------------------------------------------
# Expression tests
# ---------------------------------------------------------------------------


class TestExpressions:
    """Test symbolic expression emission."""

    def test_literal(self) -> None:
        from xqcp import Literal

        lines: list[str] = []
        Literal(42).emit(lines, 0)
        assert lines == ["PUSH 42"]

    def test_literal_hex(self) -> None:
        from xqcp import Literal

        lines: list[str] = []
        Literal(100).emit(lines, 0)
        assert lines == ["PUSH 0x64"]

    def test_regload(self) -> None:
        from xqcp import RegLoad

        lines: list[str] = []
        RegLoad(3).emit(lines, 0)
        assert lines == ["LOAD r3"]

    def test_binop_add(self) -> None:
        from xqcp import BinOp, Literal

        lines: list[str] = []
        BinOp("ADD", Literal(3), Literal(2)).emit(lines, 0)
        assert lines == ["PUSH 3", "PUSH 2", "ADD"]

    def test_binop_add_inc(self) -> None:
        from xqcp import BinOp, Literal, RegLoad

        lines: list[str] = []
        BinOp("ADD", RegLoad(0), Literal(1)).emit(lines, 0)
        assert lines == ["LOAD r0", "INC"]

    def test_binop_sub_dec(self) -> None:
        from xqcp import BinOp, Literal, RegLoad

        lines: list[str] = []
        BinOp("SUB", RegLoad(0), Literal(1)).emit(lines, 0)
        assert lines == ["LOAD r0", "DEC"]

    def test_input_ref_arithmetic(self) -> None:
        from xqcp import InputRef

        n = InputRef(0, "n", Types.Int)
        expr = n - 1
        lines: list[str] = []
        expr.emit(lines, 0)
        assert lines == ["LOAD r0", "DEC"]

    def test_loop_var_arithmetic(self) -> None:
        from xqcp import LoopVar

        v = LoopVar(5, "v5")
        expr = (v + 1) % LoopVar(0, "n")
        lines: list[str] = []
        expr.emit(lines, 0)
        assert lines == ["LOAD r5", "INC", "LOAD r0", "MOD"]

    def test_triu_expr(self) -> None:
        from xqcp import LoopVar

        ci = LoopVar(10, "ci")
        cj = LoopVar(11, "cj")
        expr = xq_triu(ci, cj)
        lines: list[str] = []
        expr.emit(lines, 0)
        assert lines == ["LOAD r10", "LOAD r11", "IDXTRIU"]

    def test_vecget_expr(self) -> None:
        from xqcp import InputRef, LoopVar

        v = InputRef(1, "distances", Types.Vec)
        idx = LoopVar(5, "i")
        expr = v.get(idx)
        lines: list[str] = []
        expr.emit(lines, 0)
        assert lines == ["LOAD r5", "VECGET r1"]

    def test_indentation(self) -> None:
        from xqcp import Literal

        lines: list[str] = []
        Literal(1).emit(lines, 2)
        assert lines == ["    PUSH 1"]

    def test_bitlen_expr(self) -> None:
        from xqcp import BitLenExpr, Literal

        lines: list[str] = []
        BitLenExpr(Literal(7)).emit(lines, 0)
        assert lines == ["PUSH 7", "BITLEN"]


# ---------------------------------------------------------------------------
# Lifecycle guard tests
# ---------------------------------------------------------------------------


class TestLifecycleGuards:
    """Tests for Problem lifecycle ordering enforcement."""

    def test_input_after_define_model_raises(self) -> None:
        import pytest

        problem = Problem("guard")
        problem.define_model(size=4, domain=XQMXDomain.BINARY)
        with pytest.raises(RuntimeError, match="input.*before.*define_model"):
            problem.input("late", type=Types.Int)

    def test_input_before_define_model_ok(self) -> None:
        problem = Problem("guard")
        ref = problem.input("early", type=Types.Int)
        problem.define_model(size=4, domain=XQMXDomain.BINARY)
        assert ref is not None


# ---------------------------------------------------------------------------
# Compilation tests
# ---------------------------------------------------------------------------


class TestTSPCompilation:
    """Test TSP problem compilation."""

    def test_compiles_without_error(self) -> None:
        problem = build_tsp_problem()
        programs = problem.compile()
        assert isinstance(programs, CompiledPrograms)
        assert isinstance(programs.encoder, str)
        assert isinstance(programs.verifier, str)
        assert isinstance(programs.decoder, str)

    def test_encoder_assembles(self) -> None:
        from xqvm_py import program_from_xqasm

        problem = build_tsp_problem()
        programs = problem.compile()
        prog = program_from_xqasm(programs.encoder)
        assert prog is not None

    def test_verifier_assembles(self) -> None:
        from xqvm_py import program_from_xqasm

        problem = build_tsp_problem()
        programs = problem.compile()
        prog = program_from_xqasm(programs.verifier)
        assert prog is not None

    def test_decoder_assembles(self) -> None:
        from xqvm_py import program_from_xqasm

        problem = build_tsp_problem()
        programs = problem.compile()
        prog = program_from_xqasm(programs.decoder)
        assert prog is not None

    def test_encoder_has_sections(self) -> None:
        problem = build_tsp_problem()
        programs = problem.compile()
        assert "; === Inputs ===" in programs.encoder
        assert "; === Allocations ===" in programs.encoder
        assert "; === Objective ===" in programs.encoder
        assert "; === Constraints ===" in programs.encoder
        assert "; === Output ===" in programs.encoder
        assert "HALT" in programs.encoder

    def test_verifier_has_sections(self) -> None:
        problem = build_tsp_problem()
        programs = problem.compile()
        assert "; === Inputs ===" in programs.verifier
        assert "ROWSUM" in programs.verifier
        assert "COLSUM" in programs.verifier
        assert "ENERGY" in programs.verifier
        assert "HALT" in programs.verifier

    def test_decoder_has_colfind(self) -> None:
        problem = build_tsp_problem()
        programs = problem.compile()
        assert "COLFIND" in programs.decoder
        assert "VECPUSH" in programs.decoder
        assert "VECI" in programs.decoder
        assert "HALT" in programs.decoder


# ---------------------------------------------------------------------------
# Pipeline integration test
# ---------------------------------------------------------------------------


class TestTSPPipeline:
    """End-to-end pipeline test: compile, assemble, execute."""

    def test_full_pipeline_n3(self) -> None:
        from xqvm_py import (
            XQMX,
            Executor,
            Vec,
            XQMXMode,
            program_from_xqasm,
        )
        from xqvm_py import (
            XQMXDomain as D,
        )

        problem = build_tsp_problem()
        programs = problem.compile()

        n = 3
        # Distance matrix (upper triangle): d(0,1)=10, d(0,2)=20, d(1,2)=30
        distances = [10, 20, 30]
        dist_vec = Vec.from_list(distances)

        # --- Run encoder ---
        enc_prog = program_from_xqasm(programs.encoder)
        enc_ex = Executor()
        enc_ex.execute(enc_prog, {0: n, 1: dist_vec}, output_slots=16)
        model = enc_ex.state.output[0]

        assert isinstance(model, XQMX)
        assert model.size == 9
        assert model.rows == 3
        assert model.cols == 3

        # --- Build a known-good sample: identity permutation ---
        # city 0 at position 0, city 1 at position 1, city 2 at position 2
        sample = XQMX(
            mode=XQMXMode.SAMPLE,
            domain=D.BINARY,
            size=9,
            rows=3,
            cols=3,
        )
        for i in range(n):
            sample.linear[i * n + i] = 1

        # --- Run verifier ---
        ver_prog = program_from_xqasm(programs.verifier)
        ver_ex = Executor()
        ver_ex.execute(ver_prog, {0: model, 1: sample, 2: n}, output_slots=16)
        energy = ver_ex.state.output[0]
        valid = ver_ex.state.output[1]

        assert valid == 1, f"Expected valid=1, got {valid}"
        assert isinstance(energy, int)

        # --- Run decoder ---
        dec_prog = program_from_xqasm(programs.decoder)
        dec_ex = Executor()
        dec_ex.execute(dec_prog, {0: sample, 1: n}, output_slots=16)
        tour = dec_ex.state.output[0]

        assert isinstance(tour, Vec)
        tour_list = [tour.get(i) for i in range(n)]
        assert tour_list == [0, 1, 2], f"Expected [0, 1, 2], got {tour_list}"

    def test_matches_handwritten_tsp(self) -> None:
        """Verify CP-generated programs produce identical results to hand-written."""
        import pathlib

        from xqvm_py import (
            XQMX,
            Executor,
            Vec,
            XQMXMode,
            program_from_xqasm,
        )
        from xqvm_py import (
            XQMXDomain as D,
        )

        tsp_dir = pathlib.Path(__file__).parent / "fixtures" / "tsp"

        # Load hand-written programs
        hw_enc = program_from_xqasm((tsp_dir / "encoder.xqasm").read_text())
        hw_ver = program_from_xqasm((tsp_dir / "verifier.xqasm").read_text())
        hw_dec = program_from_xqasm((tsp_dir / "decoder.xqasm").read_text())

        # Load CP-generated programs
        problem = build_tsp_problem()
        programs = problem.compile()
        cp_enc = program_from_xqasm(programs.encoder)
        cp_ver = program_from_xqasm(programs.verifier)
        cp_dec = program_from_xqasm(programs.decoder)

        n = 4
        # Generate distance matrix for 4 cities
        distances = []
        for j in range(n):
            for i in range(j):
                distances.append((i + 1) * (j + 1) * 3)
        dist_vec = Vec.from_list(distances)

        # Identity sample
        sample = XQMX(
            mode=XQMXMode.SAMPLE,
            domain=D.BINARY,
            size=n * n,
            rows=n,
            cols=n,
        )
        for i in range(n):
            sample.linear[i * n + i] = 1

        def run(prog, inputs):
            ex = Executor()
            ex.execute(prog, inputs, output_slots=16)
            return ex.state

        # --- Run both encoders ---
        hw_s = run(hw_enc, {0: n, 1: dist_vec})
        hw_model = hw_s.output[0]
        cp_s = run(cp_enc, {0: n, 1: dist_vec})
        cp_model = cp_s.output[0]

        # Compare models
        assert hw_model.size == cp_model.size
        assert hw_model.rows == cp_model.rows
        assert hw_model.cols == cp_model.cols
        assert hw_model.linear == cp_model.linear
        assert hw_model.quadratic == cp_model.quadratic

        # --- Run both verifiers ---
        hw_s = run(hw_ver, {0: hw_model, 1: sample, 2: n})
        cp_s = run(cp_ver, {0: cp_model, 1: sample, 2: n})
        assert hw_s.output[0] == cp_s.output[0]  # energy
        assert hw_s.output[1] == cp_s.output[1]  # valid

        # --- Run both decoders ---
        hw_s = run(hw_dec, {0: sample, 1: n})
        cp_s = run(cp_dec, {0: sample, 1: n})
        hw_tour = hw_s.output[0]
        cp_tour = cp_s.output[0]
        assert [hw_tour.get(i) for i in range(n)] == [cp_tour.get(i) for i in range(n)]


# ---------------------------------------------------------------------------
# Helper: build the Max-Cut problem
# ---------------------------------------------------------------------------


def build_maxcut_problem() -> Problem:
    """Build the reference Max-Cut problem using the CP DSL."""
    problem = Problem("MaxCut")

    num_nodes = problem.input("num_nodes", type=Types.Int)
    edges = problem.input("edges", type=Types.Vec)

    problem.define_model(size=num_nodes, domain=XQMXDomain.BINARY)

    edge_count = problem.stow("edge_count", edges.veclen() // 3)

    with problem.range(0, edge_count) as e:
        offset = e * 3
        i = problem.stow("i", edges.get(offset))
        j = problem.stow("j", edges.get(offset + 1))
        w = problem.stow("w", edges.get(offset + 2))

        problem.model.linear[i].add(-w)
        problem.model.linear[j].add(-w)
        problem.model.quadratic[i, j].add(w * 2)

    partition = problem.output("partition", type=Types.Vec)
    with problem.range(0, num_nodes) as node:
        partition.append(problem.sample.getline(node))

    return problem


# ---------------------------------------------------------------------------
# Max-Cut compilation tests
# ---------------------------------------------------------------------------


class TestMaxCutCompilation:
    """Tests for Max-Cut XQCP compilation."""

    def test_compiles_without_error(self) -> None:
        """Max-Cut problem compiles to three programs."""
        problem = build_maxcut_problem()
        programs = problem.compile()
        assert programs.encoder
        assert programs.verifier
        assert programs.decoder

    def test_encoder_assembles(self) -> None:
        """Generated encoder assembles without error."""
        from xqvm_py import program_from_xqasm

        problem = build_maxcut_problem()
        programs = problem.compile()
        prog = program_from_xqasm(programs.encoder)
        assert prog.instructions

    def test_verifier_assembles(self) -> None:
        """Generated verifier assembles without error."""
        from xqvm_py import program_from_xqasm

        problem = build_maxcut_problem()
        programs = problem.compile()
        prog = program_from_xqasm(programs.verifier)
        assert prog.instructions

    def test_decoder_assembles(self) -> None:
        """Generated decoder assembles without error."""
        from xqvm_py import program_from_xqasm

        problem = build_maxcut_problem()
        programs = problem.compile()
        prog = program_from_xqasm(programs.decoder)
        assert prog.instructions

    def test_encoder_has_veclen(self) -> None:
        """Encoder uses VECLEN for edge count computation."""
        problem = build_maxcut_problem()
        programs = problem.compile()
        assert "VECLEN" in programs.encoder

    def test_verifier_has_binary_check(self) -> None:
        """Verifier uses GETLINE for binary domain check (no ROWSUM/COLSUM)."""
        problem = build_maxcut_problem()
        programs = problem.compile()
        assert "GETLINE" in programs.verifier
        assert "ROWSUM" not in programs.verifier
        assert "COLSUM" not in programs.verifier

    def test_decoder_has_getline(self) -> None:
        """Decoder uses GETLINE (not COLFIND) to read partition."""
        problem = build_maxcut_problem()
        programs = problem.compile()
        assert "GETLINE" in programs.decoder
        assert "COLFIND" not in programs.decoder


# ---------------------------------------------------------------------------
# Max-Cut pipeline tests
# ---------------------------------------------------------------------------


class TestMaxCutPipeline:
    """End-to-end pipeline tests for Max-Cut XQCP programs."""

    def test_full_pipeline_n4(self) -> None:
        """Full Max-Cut pipeline for N=4 with bisection sample."""
        from xqvm_py import XQMX, Executor, Vec, XQMXMode, program_from_xqasm
        from xqvm_py import XQMXDomain as D

        problem = build_maxcut_problem()
        programs = problem.compile()
        enc = program_from_xqasm(programs.encoder)
        ver = program_from_xqasm(programs.verifier)
        dec = program_from_xqasm(programs.decoder)

        n = 4
        # Triangle: edges (0,1,10), (0,2,20), (0,3,30), (1,2,15), (1,3,25), (2,3,35)
        edge_data = [0, 1, 10, 0, 2, 20, 0, 3, 30, 1, 2, 15, 1, 3, 25, 2, 3, 35]
        edge_vec = Vec.from_list(edge_data)

        def run(prog, inputs):
            ex = Executor()
            ex.execute(prog, inputs, output_slots=16)
            return ex.state

        # Encoder
        enc_s = run(enc, {0: n, 1: edge_vec})
        model = enc_s.output[0]
        assert isinstance(model, XQMX)
        assert model.size == n
        assert len(model.linear) > 0
        assert len(model.quadratic) > 0

        # Bisection sample: nodes 0,1 in set 0; nodes 2,3 in set 1
        sample = XQMX(mode=XQMXMode.SAMPLE, domain=D.BINARY, size=n)
        sample.linear[2] = 1
        sample.linear[3] = 1

        # Verifier
        ver_s = run(ver, {0: model, 1: sample, 2: n})
        _energy = ver_s.output[0]
        valid = ver_s.output[1]
        assert valid == 1

        # Decoder
        dec_s = run(dec, {0: sample, 1: n})
        part = dec_s.output[0]
        assert isinstance(part, Vec)
        partition = [part.get(i) for i in range(n)]
        assert partition == [0, 0, 1, 1]

    def test_matches_handwritten_maxcut(self) -> None:
        """Verify CP-generated programs produce identical results to hand-written."""
        import pathlib

        from xqvm_py import XQMX, Executor, Vec, XQMXMode, program_from_xqasm
        from xqvm_py import XQMXDomain as D

        maxcut_dir = pathlib.Path(__file__).parent / "fixtures" / "maxcut"

        # Load hand-written programs
        hw_enc = program_from_xqasm((maxcut_dir / "encoder.xqasm").read_text())
        hw_ver = program_from_xqasm((maxcut_dir / "verifier.xqasm").read_text())
        hw_dec = program_from_xqasm((maxcut_dir / "decoder.xqasm").read_text())

        # Load CP-generated programs
        problem = build_maxcut_problem()
        programs = problem.compile()
        cp_enc = program_from_xqasm(programs.encoder)
        cp_ver = program_from_xqasm(programs.verifier)
        cp_dec = program_from_xqasm(programs.decoder)

        n = 4
        edge_data = [0, 1, 10, 0, 2, 20, 0, 3, 30, 1, 2, 15, 1, 3, 25, 2, 3, 35]
        edge_vec = Vec.from_list(edge_data)

        # Bisection sample
        sample = XQMX(mode=XQMXMode.SAMPLE, domain=D.BINARY, size=n)
        sample.linear[2] = 1
        sample.linear[3] = 1

        def run(prog, inputs):
            ex = Executor()
            ex.execute(prog, inputs, output_slots=16)
            return ex.state

        # --- Compare encoders ---
        hw_s = run(hw_enc, {0: n, 1: edge_vec})
        hw_model = hw_s.output[0]
        cp_s = run(cp_enc, {0: n, 1: edge_vec})
        cp_model = cp_s.output[0]

        assert hw_model.size == cp_model.size
        assert hw_model.linear == cp_model.linear
        assert hw_model.quadratic == cp_model.quadratic

        # --- Compare verifiers ---
        hw_s = run(hw_ver, {0: hw_model, 1: sample, 2: n})
        cp_s = run(cp_ver, {0: cp_model, 1: sample, 2: n})
        assert hw_s.output[0] == cp_s.output[0]  # energy
        assert hw_s.output[1] == cp_s.output[1]  # valid

        # --- Compare decoders ---
        hw_s = run(hw_dec, {0: sample, 1: n})
        cp_s = run(cp_dec, {0: sample, 1: n})
        hw_part = hw_s.output[0]
        cp_part = cp_s.output[0]
        assert [hw_part.get(i) for i in range(n)] == [cp_part.get(i) for i in range(n)]


# ---------------------------------------------------------------------------
# Post-compilation verification tests
# ---------------------------------------------------------------------------


class TestPostCompilationVerification:
    """verify_source is called on every compiled program inside compile()."""

    def test_tsp_all_programs_pass_verification(self) -> None:
        # compile() should not raise -- verifier accepts all three programs.
        problem = build_tsp_problem()
        programs = problem.compile()
        assert programs is not None

    def test_maxcut_all_programs_pass_verification(self) -> None:
        problem = build_maxcut_problem()
        programs = problem.compile()
        assert programs is not None

    def test_verify_compiled_called_per_program(self) -> None:
        # Monkey-patch verify_source to record which sources were checked.

        captured: list[str] = []

        original = None
        try:
            import xqffi.verifier as _v

            original = _v.verify_source
            _v.verify_source = lambda src: captured.append(src)  # type: ignore[method-assign]

            problem = build_tsp_problem()
            programs = problem.compile()
        finally:
            if original is not None:
                import xqffi.verifier as _v2

                _v2.verify_source = original  # type: ignore[method-assign]

        # All three programs must have been verified.
        assert len(captured) == 3
        assert programs.encoder in captured
        assert programs.verifier in captured
        assert programs.decoder in captured

    def test_verification_error_names_failing_program(self) -> None:
        import pytest

        import xqffi.verifier as xfv

        original = xfv.verify_source

        def _always_fail(src: str) -> None:
            raise ValueError("NoActiveLoop: NEXT at byte 0x0000 outside a RANGE block")

        try:
            xfv.verify_source = _always_fail  # type: ignore[method-assign]
            with pytest.raises(ValueError, match="encoder verification failed"):
                build_tsp_problem().compile()
        finally:
            xfv.verify_source = original  # type: ignore[method-assign]
