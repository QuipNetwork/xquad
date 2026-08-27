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

import pytest

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
        ver_ex.execute(ver_prog, {0: n, 1: dist_vec, 2: model, 3: sample}, output_slots=16)
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
        # The hand-written verifier keeps the old three-slot contract; the
        # generated one replays the encoder, so it takes the encoder's inputs.
        hw_s = run(hw_ver, {0: hw_model, 1: sample, 2: n})
        cp_s = run(cp_ver, {0: n, 1: dist_vec, 2: cp_model, 3: sample})
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
        ver_s = run(ver, {0: n, 1: edge_vec, 2: model, 3: sample})
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
        # The hand-written verifier keeps the old three-slot contract; the
        # generated one replays the encoder, so it takes the encoder's inputs.
        hw_s = run(hw_ver, {0: hw_model, 1: sample, 2: n})
        cp_s = run(cp_ver, {0: n, 1: edge_vec, 2: cp_model, 3: sample})
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


# ---------------------------------------------------------------------------
# Helpers: exercising a generated verifier against a chosen sample
# ---------------------------------------------------------------------------


def _run_program(source: str, inputs: dict[int, object]) -> object:
    """Execute an .xqasm program on the Python reference VM."""
    from xqvm_py import Executor, program_from_xqasm

    executor = Executor()
    executor.execute(program_from_xqasm(source), inputs, output_slots=16)
    return executor.state


def _encode(programs: CompiledPrograms, calldata: list[object]) -> object:
    """Run the encoder and return the model it built."""
    return _run_program(programs.encoder, dict(enumerate(calldata))).output[0]


def _verify(
    programs: CompiledPrograms,
    calldata: list[object],
    model: object,
    values: dict[int, int],
    *,
    spin: bool = False,
) -> int:
    """Run the verifier over a sample and return its validity flag.

    The sample inherits the model's size and grid, as a solver's would.
    """
    from xqvm_py import XQMX

    build = XQMX.spin_sample if spin else XQMX.binary_sample
    sample = build(model.size, rows=model.rows, cols=model.cols)
    for index, value in values.items():
        sample.linear[index] = value

    inputs = dict(enumerate([*calldata, model, sample]))
    return _run_program(programs.verifier, inputs).output[1]


def _ones(*indices: int) -> dict[int, int]:
    """A sample assignment setting exactly the given variables."""
    return {index: 1 for index in indices}


# ---------------------------------------------------------------------------
# Per-constraint-kind verifier checks
# ---------------------------------------------------------------------------


class TestVerifierChecksEveryConstraint:
    """Each constraint kind must reject a sample that violates it.

    Before QUI-1062 the generated verifier checked one-hot rows and
    columns only, so a sample violating any other constraint still
    reported ``valid = 1``.
    """

    def test_equality(self) -> None:
        """sum(x) == target, exactly."""
        problem = Problem("Equality")
        n = problem.input("n", type=Types.Int)
        target = problem.input("target", type=Types.Int)
        problem.define_model(size=n, domain=XQMXDomain.BINARY)

        indices = problem.vec()
        coeffs = problem.vec()
        with problem.range(0, n) as i:
            indices.push(i)
            coeffs.push(1)
        problem.model.apply_equality(indices, coeffs, target, 100)

        programs = problem.compile()
        calldata = [4, 2]
        model = _encode(programs, calldata)

        assert _verify(programs, calldata, model, _ones(0, 1)) == 1
        assert _verify(programs, calldata, model, _ones(0, 1, 2)) == 0
        assert _verify(programs, calldata, model, _ones(0)) == 0

    def test_equality_over_slack_checks_a_bound(self) -> None:
        """A SLACK-extended equality is an inequality over the real variables."""
        problem = Problem("SlackEquality")
        n = problem.input("n", type=Types.Int)
        capacity = problem.input("capacity", type=Types.Int)
        problem.define_model(size=n, domain=XQMXDomain.BINARY)

        indices = problem.vec()
        coeffs = problem.vec()
        with problem.range(0, n) as i:
            indices.push(i)
            coeffs.push(i + 1)
        problem.slack(indices, coeffs, n, capacity)
        problem.model.apply_equality(indices, coeffs, capacity, 100)

        programs = problem.compile()
        calldata = [4, 5]
        model = _encode(programs, calldata)

        # Weights are 1, 2, 3, 4 and the bound is 5.
        assert _verify(programs, calldata, model, _ones(0, 3)) == 1  # 1 + 4
        assert _verify(programs, calldata, model, _ones(0)) == 1  # under the bound
        assert _verify(programs, calldata, model, _ones(2, 3)) == 0  # 3 + 4

    def test_inequality_bounds_by_capacity_not_target(self) -> None:
        """apply_inequality's bound is its capacity; target is the slack start."""
        problem = Problem("Inequality")
        n = problem.input("n", type=Types.Int)
        capacity = problem.input("capacity", type=Types.Int)
        problem.define_model(size=n, domain=XQMXDomain.BINARY)

        indices = problem.vec()
        coeffs = problem.vec()
        with problem.range(0, n) as i:
            indices.push(i)
            coeffs.push(i + 1)
        problem.model.apply_inequality(indices, coeffs, n, capacity, 100)

        programs = problem.compile()
        calldata = [4, 5]
        model = _encode(programs, calldata)

        assert _verify(programs, calldata, model, _ones(0, 3)) == 1  # 1 + 4
        assert _verify(programs, calldata, model, _ones(2, 3)) == 0  # 3 + 4

    def test_equality_after_an_inequality_checks_a_bound(self) -> None:
        """An inequality's SLACK extends the pair for the equality after it.

        `_emit_inequality` composes SLACK and EQUALITY over one vec pair, so
        an `apply_equality` over the same two registers with no intervening
        `vec()` runs against slack-extended vectors in the encoder. The
        verifier rebuilds them without the slack entries, so it has to check
        that second constraint as `<=` too. Checking it as `==` rejected
        samples the encoder's model accepts.

        `_scan_slack_equalities` keyed only on `kind == "slack"` and never on
        `"inequality"`, so this composition was the one path it missed.
        """
        problem = Problem("InequalityThenEquality")
        n = problem.input("n", type=Types.Int)
        capacity = problem.input("capacity", type=Types.Int)
        problem.define_model(size=n, domain=XQMXDomain.BINARY)

        indices = problem.vec()
        coeffs = problem.vec()
        with problem.range(0, n) as i:
            indices.push(i)
            coeffs.push(i + 1)
        problem.model.apply_inequality(indices, coeffs, n, capacity, 100)
        problem.model.apply_equality(indices, coeffs, capacity, 100)

        programs = problem.compile()
        calldata = [4, 5]
        model = _encode(programs, calldata)

        # Weights are 1, 2, 3, 4 and the bound is 5. Both constraints are
        # bounds over the real variables, so anything at or under 5 is valid.
        assert _verify(programs, calldata, model, _ones(0, 3)) == 1  # 1 + 4
        assert _verify(programs, calldata, model, _ones(0)) == 1  # 1, under
        assert _verify(programs, calldata, model, _ones(2, 3)) == 0  # 3 + 4

    def test_atleast(self) -> None:
        """At least k of the indexed variables must be set."""
        problem = Problem("AtLeast")
        n = problem.input("n", type=Types.Int)
        k = problem.input("k", type=Types.Int)
        problem.define_model(size=n, domain=XQMXDomain.BINARY)

        indices = problem.vec()
        with problem.range(0, n) as i:
            indices.push(i)
        problem.model.apply_atleast(indices, k, 100)

        programs = problem.compile()
        calldata = [4, 2]
        model = _encode(programs, calldata)

        assert _verify(programs, calldata, model, _ones(0, 2)) == 1
        assert _verify(programs, calldata, model, _ones(0, 1, 2)) == 1
        assert _verify(programs, calldata, model, _ones(1)) == 0

    def test_atleastw(self) -> None:
        """The weighted sum must reach k."""
        problem = Problem("AtLeastW")
        n = problem.input("n", type=Types.Int)
        k = problem.input("k", type=Types.Int)
        problem.define_model(size=n, domain=XQMXDomain.BINARY)

        indices = problem.vec()
        coeffs = problem.vec()
        with problem.range(0, n) as i:
            indices.push(i)
            coeffs.push(i + 1)
        problem.model.apply_atleastw(indices, coeffs, k, 100)

        programs = problem.compile()
        calldata = [4, 5]
        model = _encode(programs, calldata)

        assert _verify(programs, calldata, model, _ones(1, 3)) == 1  # 2 + 4
        assert _verify(programs, calldata, model, _ones(0, 1)) == 0  # 1 + 2

    def test_exclude(self) -> None:
        """Two mutually exclusive variables cannot both be set."""
        problem = Problem("Exclude")
        n = problem.input("n", type=Types.Int)
        problem.define_model(size=n, domain=XQMXDomain.BINARY)
        problem.model.apply_exclude(0, 1, 100)

        programs = problem.compile()
        calldata = [4]
        model = _encode(programs, calldata)

        assert _verify(programs, calldata, model, _ones(0)) == 1
        assert _verify(programs, calldata, model, _ones(1, 2)) == 1
        assert _verify(programs, calldata, model, _ones(0, 1)) == 0

    def test_implies(self) -> None:
        """Setting a forces b."""
        problem = Problem("Implies")
        n = problem.input("n", type=Types.Int)
        problem.define_model(size=n, domain=XQMXDomain.BINARY)
        problem.model.apply_implies(0, 1, 100)

        programs = problem.compile()
        calldata = [4]
        model = _encode(programs, calldata)

        assert _verify(programs, calldata, model, _ones(0, 1)) == 1
        assert _verify(programs, calldata, model, _ones(1)) == 1
        assert _verify(programs, calldata, model, _ones(0)) == 0

    def test_onehot_row_and_col(self) -> None:
        """Every row and column of the grid must sum to one."""
        problem = Problem("OneHot")
        n = problem.input("n", type=Types.Int)
        problem.define_model(size=n * n, domain=XQMXDomain.BINARY, rows=n, cols=n)
        with problem.range(0, n) as i:
            problem.model.apply_onehot_row(i, 100)
        with problem.range(0, n) as j:
            problem.model.apply_onehot_col(j, 100)

        programs = problem.compile()
        calldata = [3]
        model = _encode(programs, calldata)

        assert _verify(programs, calldata, model, _ones(0, 4, 8)) == 1  # identity
        assert _verify(programs, calldata, model, _ones(0, 4)) == 0  # row 2 empty
        assert _verify(programs, calldata, model, _ones(0, 1, 4, 8)) == 0  # row 0 twice

    def test_reduce_auxiliary(self) -> None:
        """A Rosenberg auxiliary must equal the product it stands for."""
        problem = Problem("Reduce")
        n = problem.input("n", type=Types.Int)
        problem.define_model(size=n, domain=XQMXDomain.BINARY)
        aux = problem.model.reduce(0, 1, 100)
        problem.model.linear[aux].add(-1)

        programs = problem.compile()
        calldata = [3]
        model = _encode(programs, calldata)
        assert model.size == 4  # three declared plus one auxiliary

        assert _verify(programs, calldata, model, _ones(0, 1, 3)) == 1  # 1 * 1 == 1
        assert _verify(programs, calldata, model, _ones(0)) == 1  # 1 * 0 == 0
        assert _verify(programs, calldata, model, _ones(0, 1)) == 0  # aux left at 0
        assert _verify(programs, calldata, model, _ones(3)) == 0  # aux set for nothing

    def test_chained_reduce_tracks_each_auxiliary(self) -> None:
        """Chained reductions land on consecutive auxiliary indices."""
        problem = Problem("ChainedReduce")
        n = problem.input("n", type=Types.Int)
        problem.define_model(size=n, domain=XQMXDomain.BINARY)
        first = problem.model.reduce(0, 1, 100)
        second = problem.model.reduce(first, 2, 100)
        problem.model.linear[second].add(-1)

        programs = problem.compile()
        calldata = [3]
        model = _encode(programs, calldata)
        assert model.size == 5

        # x0 = x1 = x2 = 1, so both auxiliaries must be 1.
        assert _verify(programs, calldata, model, _ones(0, 1, 2, 3, 4)) == 1
        assert _verify(programs, calldata, model, _ones(0, 1, 2, 3)) == 0

    def test_binary_domain_is_always_checked(self) -> None:
        """A non-binary entry fails even when the problem declares one-hots."""
        problem = Problem("Domain")
        n = problem.input("n", type=Types.Int)
        problem.define_model(size=n * n, domain=XQMXDomain.BINARY, rows=n, cols=n)
        with problem.range(0, n) as i:
            problem.model.apply_onehot_row(i, 100)

        programs = problem.compile()
        calldata = [2]
        model = _encode(programs, calldata)

        assert _verify(programs, calldata, model, {0: 1, 3: 1}) == 1
        # Row sums still come to 1 each, but the entries are not 0/1.
        assert _verify(programs, calldata, model, {0: 2, 1: -1, 3: 1}) == 0

    def test_spin_domain_admits_plus_and_minus_one(self) -> None:
        """A spin model is checked against -1/+1, not 0/1."""
        problem = Problem("Spin")
        n = problem.input("n", type=Types.Int)
        problem.define_model(size=n, domain=XQMXDomain.SPIN)
        problem.model.linear[0].add(1)

        programs = problem.compile()
        calldata = [3]
        model = _encode(programs, calldata)

        assert _verify(programs, calldata, model, {0: 1, 1: -1}, spin=True) == 1
        assert _verify(programs, calldata, model, {0: 0}, spin=True) == 0

    def test_check_lands_inside_the_loop_that_declared_it(self) -> None:
        """A constraint declared in a loop is checked once per iteration."""
        problem = Problem("PerIteration")
        n = problem.input("n", type=Types.Int)
        problem.define_model(size=n * n, domain=XQMXDomain.BINARY, rows=n, cols=n)

        # One at-least-1 constraint per row, each over its own fresh vectors.
        with problem.range(0, n) as row:
            indices = problem.vec()
            with problem.range(0, n) as col:
                indices.push(row * n + col)
            problem.model.apply_atleast(indices, 1, 100)

        programs = problem.compile()
        calldata = [3]
        model = _encode(programs, calldata)

        assert _verify(programs, calldata, model, _ones(0, 3, 6)) == 1
        assert _verify(programs, calldata, model, _ones(0, 3)) == 0  # row 2 empty


# ---------------------------------------------------------------------------
# Verifier compile-time guards
# ---------------------------------------------------------------------------


class TestVerifierGuards:
    """Cases the verifier refuses to compile rather than mis-check."""

    def test_calldata_layout_is_the_encoder_inputs_then_model_and_sample(self) -> None:
        problem = Problem("Layout")
        problem.input("n", type=Types.Int)
        problem.input("weights", type=Types.Vec)
        problem.define_model(size=4, domain=XQMXDomain.BINARY)

        assert problem.verifier_calldata() == ["n", "weights", "model", "sample"]

    def test_model_coefficient_read_is_rejected(self) -> None:
        """The encoder reads a partial model where the verifier reads a whole one."""
        problem = Problem("CoefficientRead")
        n = problem.input("n", type=Types.Int)
        problem.define_model(size=n, domain=XQMXDomain.BINARY)
        problem.model.linear[0].add(5)
        problem.stow("bias", problem.model.linear[0])
        problem.model.apply_exclude(0, 1, 100)

        with pytest.raises(RuntimeError, match="model coefficient"):
            problem.compile()

    def test_inequality_slack_crossing_a_scope_is_rejected(self) -> None:
        """An inequality's slack binds its consumer to the same scope.

        The guard already covered a bare `slack()`; an `inequality()` leaves
        the vectors extended the same way, so an `apply_equality()` in an
        outer scope has the same problem: the verifier cannot tell how many
        entries of the rebuilt index vector the loop's slack accounted for.
        """
        problem = Problem("InequalityScope")
        n = problem.input("n", type=Types.Int)
        capacity = problem.input("capacity", type=Types.Int)
        problem.define_model(size=n, domain=XQMXDomain.BINARY)

        indices = problem.vec()
        coeffs = problem.vec()
        with problem.range(0, 2):
            problem.model.apply_inequality(indices, coeffs, n, capacity, 100)
        problem.model.apply_equality(indices, coeffs, capacity, 100)

        with pytest.raises(RuntimeError, match="same loop or branch scope"):
            problem.compile()

    def test_binary_only_constraint_on_a_spin_model_is_rejected(self) -> None:
        problem = Problem("SpinExclude")
        n = problem.input("n", type=Types.Int)
        problem.define_model(size=n, domain=XQMXDomain.SPIN)
        problem.model.apply_exclude(0, 1, 100)

        with pytest.raises(RuntimeError, match="no checkable meaning"):
            problem.compile()

    def test_growing_constraint_alongside_a_reduce_is_rejected(self) -> None:
        """ATLEAST grows the model, so the REDUCE shadow counter drifts."""
        problem = Problem("GrowBesideReduce")
        n = problem.input("n", type=Types.Int)
        problem.define_model(size=n, domain=XQMXDomain.BINARY)

        with problem.range(0, n) as i:
            indices = problem.vec()
            indices.push(i)
            problem.model.apply_atleast(indices, 1, 100)
            aux = problem.model.reduce(0, 1, 100)
            problem.model.linear[aux].add(-1)

        with pytest.raises(RuntimeError, match="grows the model"):
            problem.compile()

    def test_a_reduce_hoisted_above_a_growing_constraint_compiles(self) -> None:
        """Objective blocks are emitted first, so this reduce runs first."""
        problem = Problem("ReduceThenGrow")
        n = problem.input("n", type=Types.Int)
        problem.define_model(size=n, domain=XQMXDomain.BINARY)

        indices = problem.vec()
        with problem.range(0, n) as i:
            indices.push(i)
        problem.model.apply_atleast(indices, 1, 100)
        aux = problem.model.reduce(0, 1, 100)
        problem.model.linear[aux].add(-1)

        assert "ENERGY" in problem.compile().verifier
