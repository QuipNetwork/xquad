# QUI-1178 Pop-Before-Register-Read Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `xqvm_py` raise the same fault identity as the Rust VM on the 22 opcodes where it resolves a register before popping its operands, and move `exec_equality`'s allocation charge ahead of its register reads.

**Architecture:** Each of the 22 Python runners is reordered to the spec's per-instruction sequence — pop operands, charge the budget, then validate types and ranges — mirroring the Rust handler line for line, including the relative order of multiple register operands. Two supporting pieces make the reorder expressible: a non-faulting register peek (`_peek_register`) so a charge can be sized from a register without type-checking it first, and a `_charge_coefficient` that takes a slot rather than an already-resolved `XQMX`. An AST-based invariant test with a shrinking allowlist ratchets the 22 sites down to zero and blocks a 23rd from appearing.

**Tech Stack:** Python 3.13+ (`xqvm_py`, pytest), Rust 2024 (`xqvm`, `xquad-conformance`), cargo-nextest, uv.

**Spec:** [QUI-1178](https://linear.app/quip-network/issue/QUI-1178/fixxqvm-py-the-register-read-precedes-the-operand-pops-on-22-opcodes), and the normative clause it enforces: `spec/xqvm/SPEC.md:176`, "Error precedence". The Design and Route-B Enumeration sections below carry this plan's argument from that clause. **No spec change** — the rule is already normative.

**Base:** `origin/main` at `6ece608`. Every line number in this plan is from that tree.

## Global Constraints

- The ordering is **pop operands → charge budget → validate types and ranges**. Every reordered runner must match this, and must match its Rust counterpart's fault sequence line for line.
- **No behaviour change on the success path.** Every reorder here is observable only when the instruction faults. If a passing test's expected output moves, the reorder is wrong.
- **Register read order is part of the fault identity.** Where a handler reads more than one register, the Python order must match the Rust order (`exec_equality`/`exec_at_least`/`exec_at_least_w` read `indices`, then `coeffs`, then `model` — *not* model first).
- Rust is the reference. When Python and Rust disagree and the spec is silent, Python moves.
- Commit after every task. Imperative subject, ≤72 chars, one logical change.
- Do not extend scope to the divergences listed under **Out of Scope** — file them, don't fix them.
- **Every unit test follows the Test Harness Conventions below.** Where a task step spells a test as `ex.execute(prog, input_data={0: 5})` or `memory_limit=0`, the conventions supersede that spelling — use `_load_int_register` and `_prologue_budget`.

---

## Test Harness Conventions

`Executor.execute` (`xqvm_py/executor.py:283`) calls `self.state.reset()` on entry, so a register set before the call does not survive it. Its `input_data` argument is **calldata**, not registers — `_runner_INPUT` (`xqvm_py/executor.py:722`) is what moves a calldata slot into a register. A test that needs `r0` to hold an int must therefore say so in the program, with the same `PUSH 0 / INPUT r0` prologue the conformance vectors use. That is not incidental: `INPUT` writing a register of unknown kind is exactly what makes route B verifier-clean, so the unit tests and the vectors exercise the same shape.

`_runner_INPUT` also charges the budget (`_charge_clone`, `xqvm_py/executor.py:450`), so `memory_limit=0` faults at the prologue rather than at the opcode under test. A memory-limit test derives its budget from a prologue-only run instead of hard-coding one.

Add both helpers to `TestFaultOrdering` in Task 1, before any task uses them:

```python
    #: Loads calldata slot 0 into r0 as an int. `INPUT` writes a register of
    #: unknown kind, which is what lets the verifier admit these programs --
    #: the same shape the route-B conformance vectors use.
    PROLOGUE = [Instruction(Opcode.PUSH1, (0,)), Instruction(Opcode.INPUT, (0,))]

    @staticmethod
    def _run(instructions, memory_limit=DEFAULT_MEMORY_LIMIT, registers=(0,)):
        """Run `PROLOGUE`-loaded registers plus `instructions` to completion.

        Every slot in `registers` is loaded from calldata with an int, so a
        runner that type-checks its register faults where a runner that pops
        first does not.
        """
        prologue = []
        for slot in registers:
            prologue += [
                Instruction(Opcode.PUSH1, (slot,)),
                Instruction(Opcode.INPUT, (slot,)),
            ]
        prog = make_program(prologue + list(instructions) + [Instruction(Opcode.HALT)])
        ex = Executor()
        ex.execute(
            prog,
            input_data={slot: 5 for slot in registers},
            memory_limit=memory_limit,
        )
        return ex

    @staticmethod
    def _prologue_budget(registers=(0,)):
        """Bytes the prologue alone costs.

        `INPUT` charges for the copy it makes, so a test that wants the
        *next* charge to fail sets the budget to exactly this. Derived from a
        run rather than hard-coded, so a change to the charge rate does not
        silently turn these tests into no-ops.
        """
        ex = Executor()
        prologue = []
        for slot in registers:
            prologue += [
                Instruction(Opcode.PUSH1, (slot,)),
                Instruction(Opcode.INPUT, (slot,)),
            ]
        ex.execute(
            make_program(prologue + [Instruction(Opcode.HALT)]),
            input_data={slot: 5 for slot in registers},
        )
        return ex.memory_used
```

A stack-underflow test then reads:

```python
        with pytest.raises(StackUnderflow):
            self._run([Instruction(Opcode.VECGET, (0,))])
```

and a budget test reads:

```python
        with pytest.raises(MemoryLimitExceeded):
            self._run(
                [Instruction(Opcode.PUSH1, (1,)), Instruction(Opcode.VECPUSH, (0,))],
                memory_limit=self._prologue_budget(),
            )
```

`DEFAULT_MEMORY_LIMIT` is already imported by `xqvm_py/tests/test_executor.py`.

---

## Design

### The defect

`spec/xqvm/SPEC.md` fixes the order of work inside one instruction: operand pops first (so a short stack raises `StackUnderflow`), then the allocation charge, then type and range validation. The Rust VM follows it. `xqvm_py` resolves the register *before* popping on 22 opcodes:

```python
def _runner_RESIZE(self, instr: Instruction) -> None:
    reg = instr.operands[0]
    xqmx = self._get_register_as_xqmx(reg)   # raises TypeMismatch here
    cols, rows = self.state.pop_n(2)          # Rust pops first, then raises InvalidGridDimensions
```

Two routes reach the divergence:

- **Route A — short stack.** The bytecode verifier rejects it, so it stops being chain-reachable once [QUI-1055](https://linear.app/quip-network/issue/QUI-1055) puts `verify()` in front of `store_program`. It still diverges for a direct embedder.
- **Route B — a well-formed stack carrying operand values that fault before the register read.** `INPUT` writes `RegType::Any`, so the verifier cannot know a register's kind and admits the program. This is consensus-visible: on a wasm32 chain runtime, two conforming implementations raising different fault identities on an admitted program is a consensus split.

### The rule, and the two sites that already follow it

`spec/xqvm/SPEC.md:176` states it normatively:

> **Error precedence.** Within one instruction, operand pops happen first (`StackUnderflow`), then the allocation charge, then type and range validation. An instruction can therefore raise `MemoryLimitExceeded` for work it would never have done, because the register it names holds the wrong type or the index it was given is out of range. That ordering is deliberate: the charge is what makes the later work safe to attempt, so it cannot be made conditional on that work succeeding.

Two runners already comply and set the house style every task below copies — the comment names the rule, the Rust counterpart, and the divergence the old order produced:

- `_runner_ITER` (`xqvm_py/executor.py:630`), landed by [QUI-1177](https://linear.app/quip-network/issue/QUI-1177).
- `_runner_SLACK` (`xqvm_py/executor.py:1041`), which additionally records *why* both its registers are discriminated before an early return.

Neither is in this plan's 22. The remaining 22 did not get the same treatment.

### Route-B enumeration

The ticket asks to enumerate which of the 22 admit route B rather than assume. Reading each Rust handler's fault sequence between its pops and its register read gives:

| Opcode(s) | Rust fault between pops and register read | Route B? |
|---|---|---|
| `RESIZE` | `InvalidGridDimensions` (`vm.rs:2136`, `2139`, `2141`) | **Yes, unconditional.** Pinnable by a vector today. |
| `ATLEAST` | `IndexOutOfBounds` on `k` (`vm.rs:2616`) before `reg_mut(model)` (`vm.rs:2636`) | **Yes, unconditional.** Python already raises `IndexOutOfBounds` here (`executor.py:1292`) and `fault_from_python` maps it. Pinnable by a vector today. |
| `ATLEASTW` | `VecLengthMismatch` (`vm.rs:2696`), `IndexOutOfBounds` (`2704`), `ArithmeticOverflow` (`2715`) — all before `reg_mut(model)` (`2729`) | **Yes, unconditional.** Python raises `VecLengthMismatch` (`executor.py:1321`), which `fault_from_python` maps. Pinnable by a vector today. |
| `EQUALITY` | `VecLengthMismatch` (`vm.rs:2551`) before `reg_mut(model)` (`vm.rs:2575`) | Yes in Rust, but `_runner_EQUALITY` has no length check at all — a second, separate divergence. Not pinnable here; see Out of Scope. |
| `VECPUSH`, `SETLINE`, `ADDLINE`, `SETQUAD`, `ADDQUAD`, `ONEHOTR`, `ONEHOTC`, `EXCLUDE`, `IMPLIES`, `REDUCE` | `MemoryLimitExceeded` from a charge placed before the register read | Yes, but only against a near-exhausted budget. |
| `VECGET`, `VECSET`, `GETLINE`, `GETQUAD`, `ROWFIND`, `COLFIND`, `ROWSUM`, `COLSUM` | none — Rust type-checks the register immediately after its pops | **No.** Route A only. |

Two consequences for the vectors:

1. **Three conformance vectors land: `RESIZE`, `ATLEAST`, `ATLEASTW`** — above the ticket's stated minimum of `RESIZE`. `EQUALITY` needs a length check Python does not have, which is a separate divergence (Out of Scope); the ten budget-dependent cases need a memory limit the harness cannot express — `conformance::Inputs` (`conformance/src/lib.rs:74`) carries `calldata`, `output_slots` and `step_limit`, but no `memory_limit`.
2. **The budget-dependent ten are pinned by per-VM unit tests instead** — `xqvm_py/tests/test_executor.py` can construct an `Executor` with any `memory_limit`, so the divergence is directly assertable there. Task 10 files the harness gap.

### Supporting changes

`_charge_coefficient` currently takes a resolved `XQMX`, which forces the type check to happen first — the charge cannot precede the read while the charge needs the read's result. Rust's `charge_coefficient` (`vm.rs:837`) takes a `Register` and peeks non-faulting:

```rust
fn charge_coefficient(&mut self, pos: usize, reg: Register, bytes: u64) -> Result<(), Error> {
    if matches!(self.reg(reg), RegVal::Model(_)) {
        self.charge(pos, bytes)?;
    }
    Ok(())
}
```

Python gets the same shape, built on a new `_peek_register` that returns `None` for an unset slot rather than raising `RegisterNotFound` (mirroring Rust's `RegVal::Unset`). `ONEHOTR`/`ONEHOTC` use the same peek to size their expansion charge, which is how `exec_one_hot_r` (`vm.rs:2377`) does it.

---

## File Structure

- `xqvm_py/executor.py` — modify: `_peek_register` (new), `_charge_coefficient` (signature change), and 22 runner bodies.
- `xqvm_py/tests/test_executor.py` — modify: a `TestFaultOrdering` class holding the AST invariant guard and the per-opcode fault-identity tests.
- `conformance/vectors/xqmx-grid/resize_type_after_dimension_check/` — create: `program.xqasm`, `inputs.json`, `expected.json`.
- `conformance/vectors/constraints/atleast_k_range_before_model_type/` — create: same three files.
- `conformance/vectors/constraints/atleastw_length_before_model_type/` — create: same three files.
- `conformance/README.md` — modify: the route-B enumeration table.
- `xqvm/src/vm.rs` — modify: `exec_equality` (`2524`–`2593`) charge/read order.
- `xqvm/tests/` — modify: a Rust test pinning `exec_equality`'s charge-before-read.

---

## Task 1: The ordering invariant guard, with a shrinking allowlist

**Files:**
- Modify: `xqvm_py/tests/test_executor.py`

**Interfaces:**
- Consumes: `Executor._build_dispatch_table` (`xqvm_py/executor.py:173`), which maps every `Opcode` to its bound `_runner_*`.
- Produces: `TestFaultOrdering.KNOWN_UNORDERED` — a `frozenset[str]` of runner names still violating the invariant. Every later task removes its opcodes from this set in the same commit as the fix.

Why a ratchet rather than a plain assertion: 22 fixes across eight commits would leave the invariant test red for the whole branch. The allowlist keeps every commit green while making it impossible to fix a site without declaring it, or to add a 23rd site silently.

- [ ] **Step 1: Write the guard test**

Append to `xqvm_py/tests/test_executor.py`:

```python
class TestFaultOrdering:
    """Every runner pops its operands before it resolves a register.

    `spec/xqvm/SPEC.md` fixes the order of work within one instruction:
    pops, then the allocation charge, then validation. A runner that reads
    its register first raises `TypeMismatch` where the Rust VM raises the
    operand fault -- a fault-identity divergence, which on a chain runtime
    is a consensus split (QUI-1178).
    """

    REGISTER_READS = frozenset({"_get_register_as_xqmx", "_get_register_as_vec"})
    POPS = frozenset({"pop", "pop_n"})

    #: Runners whose register read still precedes their pops. QUI-1178
    #: empties this set one family at a time; nothing may be added to it.
    KNOWN_UNORDERED = frozenset(
        {
            "_runner_VECPUSH", "_runner_VECGET", "_runner_VECSET",
            "_runner_GETLINE", "_runner_SETLINE", "_runner_ADDLINE",
            "_runner_GETQUAD", "_runner_SETQUAD", "_runner_ADDQUAD",
            "_runner_RESIZE", "_runner_ROWFIND", "_runner_COLFIND",
            "_runner_ROWSUM", "_runner_COLSUM", "_runner_ONEHOTR",
            "_runner_ONEHOTC", "_runner_EXCLUDE", "_runner_IMPLIES",
            "_runner_EQUALITY", "_runner_ATLEAST", "_runner_ATLEASTW",
            "_runner_REDUCE",
        }
    )

    @staticmethod
    def _first_positions(func):
        """Source position of the first register read and the first pop.

        Returns `(read_pos, pop_pos)`, either of which is `None` when the
        runner does not do that thing. Positions are `(lineno, col_offset)`
        so a read and a pop on one line still compare left to right.
        """
        import ast
        import inspect
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
        reads, pops = [], []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            name = (
                target.attr
                if isinstance(target, ast.Attribute)
                else getattr(target, "id", None)
            )
            pos = (node.lineno, node.col_offset)
            if name in TestFaultOrdering.REGISTER_READS:
                reads.append(pos)
            elif name in TestFaultOrdering.POPS:
                pops.append(pos)
        return (min(reads) if reads else None, min(pops) if pops else None)

    def test_pops_precede_the_register_read(self):
        offenders = set()
        for runner in Executor()._build_dispatch_table().values():
            read, pop = self._first_positions(runner)
            if read is not None and pop is not None and read < pop:
                offenders.add(runner.__name__)
        assert offenders == self.KNOWN_UNORDERED, (
            "the set of runners reading a register before popping changed; "
            "remove a runner from KNOWN_UNORDERED in the commit that fixes "
            "it, and never add one (spec/xqvm/SPEC.md, QUI-1178). "
            f"unexpected={sorted(offenders - self.KNOWN_UNORDERED)} "
            f"already_fixed={sorted(self.KNOWN_UNORDERED - offenders)}"
        )
```

- [ ] **Step 2: Run it and reconcile the starting set**

Run: `uv run --no-sync pytest xqvm_py/tests/test_executor.py::TestFaultOrdering -q`

Expected: PASS. `_runner_ITER` and `_runner_SLACK` already comply on this base, so the offender set is exactly the 22 names above.

If any name appears in `unexpected`, stop and report it — a 23rd site means the ticket's enumeration is incomplete and the plan needs revising before continuing. If a name appears in `already_fixed`, remove it from `KNOWN_UNORDERED` and note in the commit message that it was already compliant.

- [ ] **Step 3: Commit**

```bash
git add xqvm_py/tests/test_executor.py
git commit -m "test(xqvm-py): pin the pop-before-register-read invariant"
```

---

## Task 2: `RESIZE` — the verifier-clean divergence

`RESIZE` is the one site route B reaches unconditionally, so it gets the conformance vector and goes first: it proves the harness expresses the divergence before twenty-one mechanical edits land on top.

**Files:**
- Create: `conformance/vectors/xqmx-grid/resize_type_after_dimension_check/program.xqasm`
- Create: `conformance/vectors/xqmx-grid/resize_type_after_dimension_check/inputs.json`
- Create: `conformance/vectors/xqmx-grid/resize_type_after_dimension_check/expected.json`
- Modify: `xqvm_py/executor.py:1160-1173` (`_runner_RESIZE`)
- Modify: `xqvm_py/tests/test_executor.py` (`KNOWN_UNORDERED`)

**Interfaces:**
- Consumes: `TestFaultOrdering.KNOWN_UNORDERED` from Task 1.
- Produces: the three-file vector layout every later vector would copy, and the reorder shape the remaining tasks repeat.

- [ ] **Step 1: Write the failing vector**

`conformance/vectors/xqmx-grid/resize_type_after_dimension_check/program.xqasm`:

```
; resize_type_after_dimension_check: RESIZE validates its dimensions before
; it type-checks its register.
;
; r0 holds an int, not an XQMX, and the dimensions are zero. Both faults are
; live; SPEC.md's order of work decides which one is raised. INPUT writes a
; register of unknown kind, so the verifier admits this program -- the
; divergence is consensus-visible (QUI-1178 route B).

PUSH 0
INPUT r0         ; r0 = calldata[0] = 5, an int

PUSH 0           ; rows
PUSH 0           ; cols
RESIZE r0
HALT
```

`conformance/vectors/xqmx-grid/resize_type_after_dimension_check/inputs.json`:

```json
{
  "calldata": [5],
  "output_slots": 4
}
```

`conformance/vectors/xqmx-grid/resize_type_after_dimension_check/expected.json`:

```json
{
  "error": "INVALID_GRID_DIMENSIONS"
}
```

- [ ] **Step 2: Confirm the vector is admitted, and that it fails against Python**

Run: `cargo test -p xquad-conformance --no-default-features --features rust resize_type_after_dimension_check`
Expected: PASS — Rust already raises `InvalidGridDimensions`.

Run: `cargo test -p xquad-conformance --no-default-features --features python resize_type_after_dimension_check`
Expected: FAIL — Python raises `TypeMismatch`, reported as a fault-identity mismatch against `INVALID_GRID_DIMENSIONS`.

That contrast is the bug. Record both outputs before continuing.

- [ ] **Step 3: Reorder `_runner_RESIZE`**

The pops move above the register read, and the dimension check moves with them — `exec_resize` (`vm.rs:2134-2144`) validates `rows`/`cols` before `reg_mut`, so moving only the pop would still diverge.

```python
    def _runner_RESIZE(self, instr: Instruction) -> None:
        """RESIZE: Set grid dimensions."""
        reg = instr.operands[0]
        # Pops precede the register read, and the dimension check precedes it
        # too: `exec_resize` rejects a non-grid extent before it touches the
        # register, so a program with both faults must raise this one.
        cols, rows = self.state.pop_n(2)
        # A non-positive extent is not a grid. Assigning it unconditionally
        # left the model degenerate, which is how a grid reached the state
        # ONEHOTR and ONEHOTC reject.
        if rows <= 0 or cols <= 0:
            raise InvalidGridDimensions(rows, cols)
        xqmx = self._get_register_as_xqmx(reg)
        xqmx.rows = rows
        xqmx.cols = cols
```

- [ ] **Step 4: Drop `_runner_RESIZE` from the allowlist**

In `xqvm_py/tests/test_executor.py`, remove `"_runner_RESIZE",` from `KNOWN_UNORDERED`.

- [ ] **Step 5: Verify green**

Run: `cargo test -p xquad-conformance --no-default-features --features python resize`
Expected: PASS, including the pre-existing `resize_negative_rows` and `resize_zero_cols`.

Run: `uv run --no-sync pytest xqvm_py/tests -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add conformance/vectors/xqmx-grid/resize_type_after_dimension_check xqvm_py/executor.py xqvm_py/tests/test_executor.py
git commit -m "fix(xqvm-py): validate RESIZE dimensions before its register"
```

---

## Task 3: The vec family

**Files:**
- Modify: `xqvm_py/executor.py:1009-1039` (`_runner_VECPUSH`, `_runner_VECGET`, `_runner_VECSET`)
- Modify: `xqvm_py/tests/test_executor.py`

**Interfaces:**
- Consumes: `TestFaultOrdering.KNOWN_UNORDERED`.
- Produces: nothing later tasks depend on.

`VECPUSH` charges before its register read in Rust (`vm.rs:1775`), so its charge moves above the read too. `VECGET`/`VECSET` are plain pop-first moves — route A only, per the enumeration.

- [ ] **Step 1: Write the failing test**

Append to `TestFaultOrdering` in `xqvm_py/tests/test_executor.py`:

```python
    def test_vecpush_charges_before_it_reads_its_register(self):
        """A budget too small to hold the element faults before the type check.

        `exec_vec_push` charges at vm.rs:1775 and reads the register at
        vm.rs:1776, so an int in r0 under an exhausted budget is
        MemoryLimitExceeded in Rust, not TypeMismatch.
        """
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (7,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.VECPUSH, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        ex = Executor()
        with pytest.raises(MemoryLimitExceeded):
            ex.execute(prog, memory_limit=0, input_data={0: 5})

    def test_vecget_underflows_before_it_reads_its_register(self):
        prog = make_program(
            [Instruction(Opcode.VECGET, (0,)), Instruction(Opcode.HALT)]
        )
        ex = Executor()
        with pytest.raises(StackUnderflow):
            ex.execute(prog, input_data={0: 5})

    def test_vecset_underflows_before_it_reads_its_register(self):
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.VECSET, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        ex = Executor()
        with pytest.raises(StackUnderflow):
            ex.execute(prog, input_data={0: 5})
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --no-sync pytest xqvm_py/tests/test_executor.py::TestFaultOrdering -q`
Expected: three FAILs, each raising `TypeMismatch` where the test expects the operand fault.

If `Executor.execute` does not accept `input_data` alongside `memory_limit`, set the register directly via `ex.state.set_register(0, 5)` before `ex.execute(...)` and keep the rest of the test unchanged.

- [ ] **Step 3: Reorder the three runners**

```python
    def _runner_VECPUSH(self, instr: Instruction) -> None:
        """VECPUSH: Push value onto vec (infers/validates type)."""
        reg = instr.operands[0]
        # Pops precede the register read, and the charge precedes it too:
        # `exec_vec_push` charges for the element before it resolves the vec.
        value = self.state.pop()
        self._charge(VEC_ELEMENT_BYTES)
        vec = self._get_register_as_vec(reg)
        vec.push(value)

    def _runner_VECGET(self, instr: Instruction) -> None:
        """VECGET: Get vec[index]."""
        reg = instr.operands[0]
        # Pops precede the register read.
        index = self.state.pop()
        vec = self._get_register_as_vec(reg)
        value = vec.get(index)
```

(leave `_runner_VECGET`'s existing `if isinstance(value, int):` tail untouched)

```python
    def _runner_VECSET(self, instr: Instruction) -> None:
        """VECSET: Set vec[index] = value."""
        reg = instr.operands[0]
        # Pops precede the register read.
        value, index = self.state.pop_n(2)
        vec = self._get_register_as_vec(reg)
        vec.set(index, value)
```

- [ ] **Step 4: Drop the three names from the allowlist and verify**

Remove `"_runner_VECPUSH", "_runner_VECGET", "_runner_VECSET",` from `KNOWN_UNORDERED`.

Run: `uv run --no-sync pytest xqvm_py/tests -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add xqvm_py/executor.py xqvm_py/tests/test_executor.py
git commit -m "fix(xqvm-py): pop before the register read on the vec opcodes"
```

---

## Task 4: The coefficient accessors, and a slot-taking charge

**Files:**
- Modify: `xqvm_py/executor.py:463-472` (`_charge_coefficient`), `427-439` (add `_peek_register`), `895-942` (`GETLINE`, `SETLINE`, `ADDLINE`, `GETQUAD`, `SETQUAD`, `ADDQUAD`)
- Modify: `xqvm_py/tests/test_executor.py`

**Interfaces:**
- Consumes: `TestFaultOrdering.KNOWN_UNORDERED`.
- Produces:
  - `Executor._peek_register(self, slot: int) -> Value | None` — the register's value, or `None` when the slot is unset. Never raises. Mirrors Rust's `Vm::reg` returning `RegVal::Unset`.
  - `Executor._charge_coefficient(self, slot: int, nbytes: int) -> None` — **signature change** from `(xqmx: XQMX, nbytes: int)`. Charges only when the slot holds a model. Tasks 7 and 8 call the new form.

- [ ] **Step 1: Write the failing test**

Append to `TestFaultOrdering`:

```python
    @pytest.mark.parametrize(
        "opcode,operands,pushes",
        [
            (Opcode.SETLINE, (0,), 2),
            (Opcode.ADDLINE, (0,), 2),
            (Opcode.SETQUAD, (0,), 3),
            (Opcode.ADDQUAD, (0,), 3),
        ],
    )
    def test_coefficient_writers_underflow_before_the_register_read(
        self, opcode, operands, pushes
    ):
        """One operand short of what the opcode needs is StackUnderflow.

        Rust pops every operand (vm.rs:1998, 2027, 2078, 2105) before it
        charges or resolves the register, so a short stack raises here even
        when the register holds the wrong kind.
        """
        instructions = [Instruction(Opcode.PUSH1, (1,))] * (pushes - 1)
        instructions += [Instruction(opcode, operands), Instruction(Opcode.HALT)]
        ex = Executor()
        with pytest.raises(StackUnderflow):
            ex.execute(make_program(instructions), input_data={0: 5})

    @pytest.mark.parametrize("opcode", [Opcode.GETLINE, Opcode.GETQUAD])
    def test_coefficient_readers_underflow_before_the_register_read(self, opcode):
        ex = Executor()
        with pytest.raises(StackUnderflow):
            ex.execute(
                make_program([Instruction(opcode, (0,)), Instruction(Opcode.HALT)]),
                input_data={0: 5},
            )
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --no-sync pytest xqvm_py/tests/test_executor.py::TestFaultOrdering -q`
Expected: six FAILs raising `TypeMismatch`.

- [ ] **Step 3: Add the peek and re-sign the charge**

Replace `_charge_coefficient` (`xqvm_py/executor.py:463`):

```python
    def _charge_coefficient(self, slot: int, nbytes: int) -> None:
        """Charge for one coefficient written into register `slot`, if a model.

        Takes a slot rather than a resolved XQMX so the charge can precede
        the register's type check, matching `charge_coefficient`
        (`xqvm/src/vm.rs:837`): a register of the wrong kind, or an unset
        one, charges nothing and falls through to the type error.

        A sample writes into storage that was charged when the sample was
        allocated, so it costs nothing further. Writing a coefficient that
        already exists is charged too: the step limit already bounds how many
        of these a program can run.
        """
        value = self._peek_register(slot)
        if isinstance(value, XQMX) and value.is_model():
            self._charge(nbytes)
```

Add beside the other register helpers (`xqvm_py/executor.py:524`):

```python
    def _peek_register(self, slot: int) -> Value | None:
        """The register's value, or None when the slot is unset.

        Never raises. Sizing an allocation charge may inspect a register but
        must not fault on it (`spec/xqvm/SPEC.md`, order of work stage 2);
        this is Python's `RegVal::Unset` -- `xqvm/src/vm.rs:871`.
        """
        if not self.state.has_register(slot):
            return None
        return self.state.get_register(slot)
```

- [ ] **Step 4: Reorder the six runners**

```python
    def _runner_GETLINE(self, instr: Instruction) -> None:
        """GETLINE: Get linear coefficient."""
        reg = instr.operands[0]
        # Pops precede the register read.
        index = self.state.pop()
        xqmx = self._get_register_as_xqmx(reg)
        value = xqmx.get_linear(index)
        self.state.push(value)

    def _runner_SETLINE(self, instr: Instruction) -> None:
        """SETLINE: Set linear coefficient."""
        reg = instr.operands[0]
        # Pops, then the charge, then the register read: `exec_set_line`
        # charges at vm.rs:2000 and resolves the register at vm.rs:2001.
        value, index = self.state.pop_n(2)
        self._charge_coefficient(reg, LINEAR_ENTRY_BYTES)
        xqmx = self._get_register_as_xqmx(reg)
        xqmx.set_linear(index, value)

    def _runner_ADDLINE(self, instr: Instruction) -> None:
        """ADDLINE: Add to linear coefficient."""
        reg = instr.operands[0]
        # Pops, then the charge, then the register read; see SETLINE.
        delta, index = self.state.pop_n(2)
        self._charge_coefficient(reg, LINEAR_ENTRY_BYTES)
        xqmx = self._get_register_as_xqmx(reg)
        xqmx.add_linear(index, delta)

    def _runner_GETQUAD(self, instr: Instruction) -> None:
        """GETQUAD: Get quadratic coefficient."""
        reg = instr.operands[0]
        # Pops precede the register read.
        j, i = self.state.pop_n(2)
        xqmx = self._get_register_as_xqmx(reg)
        value = xqmx.get_quadratic(i, j)
        self.state.push(value)

    def _runner_SETQUAD(self, instr: Instruction) -> None:
        """SETQUAD: Set quadratic coefficient."""
        reg = instr.operands[0]
        # Pops, then the charge, then the register read; see SETLINE.
        value, j, i = self.state.pop_n(3)
        self._charge_coefficient(reg, QUAD_ENTRY_BYTES)
        xqmx = self._get_register_as_xqmx(reg)
        xqmx.set_quadratic(i, j, value)

    def _runner_ADDQUAD(self, instr: Instruction) -> None:
        """ADDQUAD: Add to quadratic coefficient."""
        reg = instr.operands[0]
        # Pops, then the charge, then the register read; see SETLINE.
        delta, j, i = self.state.pop_n(3)
        self._charge_coefficient(reg, QUAD_ENTRY_BYTES)
        xqmx = self._get_register_as_xqmx(reg)
        xqmx.add_quadratic(i, j, delta)
```

- [ ] **Step 5: Find every remaining caller of the old signature**

Run: `rg -n "_charge_coefficient" xqvm_py/`
Expected: only the definition and the six call sites above, plus `_runner_EXCLUDE` and `_runner_IMPLIES` (Task 7, still passing an `XQMX`). Fix those two call sites to pass `reg` now — Task 7 reorders their bodies, but leaving a stale signature between commits would break the branch.

- [ ] **Step 6: Verify**

Run: `uv run --no-sync pytest xqvm_py/tests -q`
Expected: PASS. The `TestAllocationBudget` class exercises these charge rates; if any of its expected byte counts moved, the peek is charging where the old code did not — stop and diagnose rather than adjusting the expectation.

- [ ] **Step 7: Drop the six names from the allowlist, then commit**

```bash
git add xqvm_py/executor.py xqvm_py/tests/test_executor.py
git commit -m "fix(xqvm-py): charge and pop before the coefficient register read"
```

---

## Task 5: The grid readers

**Files:**
- Modify: `xqvm_py/executor.py:1184-1215` (`ROWFIND`, `COLFIND`, `ROWSUM`, `COLSUM`)
- Modify: `xqvm_py/tests/test_executor.py`

**Interfaces:**
- Consumes: `TestFaultOrdering.KNOWN_UNORDERED`.
- Produces: nothing.

Route A only — Rust type-checks these immediately after their pops, with the axis-range check after (`vm.rs:2257`, `2285`, `2311`, `2340`). The reorder is a plain move.

- [ ] **Step 1: Write the failing test**

Append to `TestFaultOrdering`:

```python
    @pytest.mark.parametrize(
        "opcode,pushes",
        [
            (Opcode.ROWFIND, 2),
            (Opcode.COLFIND, 2),
            (Opcode.ROWSUM, 1),
            (Opcode.COLSUM, 1),
        ],
    )
    def test_grid_readers_underflow_before_the_register_read(self, opcode, pushes):
        instructions = [Instruction(Opcode.PUSH1, (1,))] * (pushes - 1)
        instructions += [Instruction(opcode, (0,)), Instruction(Opcode.HALT)]
        ex = Executor()
        with pytest.raises(StackUnderflow):
            ex.execute(make_program(instructions), input_data={0: 5})
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest xqvm_py/tests/test_executor.py::TestFaultOrdering -q`
Expected: four FAILs raising `TypeMismatch`.

- [ ] **Step 3: Reorder**

For each of the four, move the `self.state.pop...` line above the `self._get_register_as_xqmx(reg)` line and add the comment `# Pops precede the register read.` above the pop. Bodies otherwise unchanged:

```python
    def _runner_ROWFIND(self, instr: Instruction) -> None:
        """ROWFIND: Find first col where row has value."""
        reg = instr.operands[0]
        # Pops precede the register read.
        value, row = self.state.pop_n(2)
        xqmx = self._get_register_as_xqmx(reg)
        col = row_find(xqmx, row, value)
        self.state.push(col)

    def _runner_COLFIND(self, instr: Instruction) -> None:
        """COLFIND: Find first row where col has value."""
        reg = instr.operands[0]
        # Pops precede the register read.
        value, col = self.state.pop_n(2)
        xqmx = self._get_register_as_xqmx(reg)
        row = col_find(xqmx, col, value)
        self.state.push(row)

    def _runner_ROWSUM(self, instr: Instruction) -> None:
        """ROWSUM: Sum all values in row."""
        reg = instr.operands[0]
        # Pops precede the register read.
        row = self.state.pop()
        xqmx = self._get_register_as_xqmx(reg)
        total = xqmx_row_sum(xqmx, row)
        self.state.push(total)

    def _runner_COLSUM(self, instr: Instruction) -> None:
        """COLSUM: Sum all values in column."""
        reg = instr.operands[0]
        # Pops precede the register read.
        col = self.state.pop()
        xqmx = self._get_register_as_xqmx(reg)
        total = xqmx_col_sum(xqmx, col)
        self.state.push(total)
```

- [ ] **Step 4: Drop the four names from the allowlist and verify**

Run: `uv run --no-sync pytest xqvm_py/tests -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add xqvm_py/executor.py xqvm_py/tests/test_executor.py
git commit -m "fix(xqvm-py): pop before the register read on the grid readers"
```

---

## Task 6: `ONEHOTR` and `ONEHOTC` — sizing a charge without faulting

**Files:**
- Modify: `xqvm_py/executor.py:1216-1245`
- Modify: `xqvm_py/tests/test_executor.py`

**Interfaces:**
- Consumes: `Executor._peek_register` (Task 4), `TestFaultOrdering.KNOWN_UNORDERED`.
- Produces: nothing.

These are the pattern the ticket calls the intended one, and Rust already implements it (`vm.rs:2377-2382`): peek the register without faulting to size the charge, charge, then type-check, then check dimensions. Python currently type-checks first, so its charge cannot precede its read at all.

- [ ] **Step 1: Write the failing test**

Append to `TestFaultOrdering`:

```python
    @pytest.mark.parametrize("opcode", [Opcode.ONEHOTR, Opcode.ONEHOTC])
    def test_onehot_charges_before_it_reads_its_register(self, opcode):
        """A non-model register sizes the charge at zero, then type-fails.

        `exec_one_hot_r` peeks for `cols` at vm.rs:2377, charges at 2381 and
        only then resolves the register at 2000. An int in r0 must therefore
        still reach TypeMismatch, not MemoryLimitExceeded -- the peek is
        non-faulting in both directions.
        """
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(opcode, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        ex = Executor()
        with pytest.raises(TypeMismatch):
            ex.execute(prog, memory_limit=0, input_data={0: 5})

    @pytest.mark.parametrize("opcode", [Opcode.ONEHOTR, Opcode.ONEHOTC])
    def test_onehot_underflows_before_it_reads_its_register(self, opcode):
        ex = Executor()
        with pytest.raises(StackUnderflow):
            ex.execute(
                make_program(
                    [
                        Instruction(Opcode.PUSH1, (1,)),
                        Instruction(opcode, (0,)),
                        Instruction(Opcode.HALT),
                    ]
                ),
                input_data={0: 5},
            )
```

- [ ] **Step 2: Run to verify the underflow pair fails**

Run: `uv run --no-sync pytest xqvm_py/tests/test_executor.py::TestFaultOrdering -q -k onehot`
Expected: the two `underflows` cases FAIL raising `TypeMismatch`. The two `charges_before` cases already pass — they guard against the reorder overshooting into a spurious `MemoryLimitExceeded`, so keep them.

- [ ] **Step 3: Reorder both**

```python
    def _runner_ONEHOTR(self, instr: Instruction) -> None:
        """ONEHOTR: Add one-hot constraint for row."""
        reg = instr.operands[0]
        # Pops, then the charge, then the register read. The expansion writes
        # one linear term per column and one quadratic term per pair of
        # columns, so ONEHOTR costs O(cols^2) entries in one step -- and
        # RESIZE takes cols straight off the value stack. Sizing the charge
        # peeks without faulting (`exec_one_hot_r`, vm.rs:2377): a register
        # of the wrong kind charges nothing and falls through to the type
        # error below.
        penalty, row = self.state.pop_n(2)
        peeked = self._peek_register(reg)
        cols = peeked.cols if isinstance(peeked, XQMX) else 0
        self._charge_equality_expansion(cols)

        model = self._get_register_as_xqmx(reg)
        if model.rows == 0 or model.cols == 0:
            raise InvalidGridDimensions(model.rows, model.cols)

        indices = row_indices(model, row)
        expand_onehot(model, indices, penalty)

    def _runner_ONEHOTC(self, instr: Instruction) -> None:
        """ONEHOTC: Add one-hot constraint for column."""
        reg = instr.operands[0]
        # O(rows^2) entries in one step; see ONEHOTR for the charge order.
        penalty, col = self.state.pop_n(2)
        peeked = self._peek_register(reg)
        rows = peeked.rows if isinstance(peeked, XQMX) else 0
        self._charge_equality_expansion(rows)

        model = self._get_register_as_xqmx(reg)
        if model.rows == 0 or model.cols == 0:
            raise InvalidGridDimensions(model.rows, model.cols)

        indices = col_indices(model, col)
        expand_onehot(model, indices, penalty)
```

- [ ] **Step 4: Verify the constraint vectors still hold**

Run: `cargo test -p xquad-conformance --no-default-features --features python onehot`
Expected: PASS, including `onehotr_without_grid`, `onehotc_without_grid` and `onehotr_coeff`.

Run: `uv run --no-sync pytest xqvm_py/tests -q`
Expected: PASS.

- [ ] **Step 5: Drop the two names from the allowlist, then commit**

```bash
git add xqvm_py/executor.py xqvm_py/tests/test_executor.py
git commit -m "fix(xqvm-py): size the ONEHOT charge from a non-faulting peek"
```

---

## Task 7: `EXCLUDE`, `IMPLIES`, `REDUCE`

**Files:**
- Modify: `xqvm_py/executor.py:1246-1261` (`EXCLUDE`, `IMPLIES`), `1133-1145` (`REDUCE`)
- Modify: `xqvm_py/tests/test_executor.py`

**Interfaces:**
- Consumes: `Executor._charge_coefficient(slot, nbytes)` (Task 4), `TestFaultOrdering.KNOWN_UNORDERED`.
- Produces: nothing.

- [ ] **Step 1: Write the failing test**

Append to `TestFaultOrdering`:

```python
    @pytest.mark.parametrize(
        "opcode,pushes", [(Opcode.EXCLUDE, 3), (Opcode.IMPLIES, 3), (Opcode.REDUCE, 3)]
    )
    def test_expansion_opcodes_underflow_before_the_register_read(
        self, opcode, pushes
    ):
        instructions = [Instruction(Opcode.PUSH1, (1,))] * (pushes - 1)
        instructions += [Instruction(opcode, (0,)), Instruction(Opcode.HALT)]
        ex = Executor()
        with pytest.raises(StackUnderflow):
            ex.execute(make_program(instructions), input_data={0: 5})

    def test_reduce_charges_before_it_reads_its_register(self):
        """`exec_reduce` charges at vm.rs:2760-2761, resolves at 2762."""
        prog = make_program(
            [
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.PUSH1, (1,)),
                Instruction(Opcode.REDUCE, (0,)),
                Instruction(Opcode.HALT),
            ]
        )
        ex = Executor()
        with pytest.raises(MemoryLimitExceeded):
            ex.execute(prog, memory_limit=0, input_data={0: 5})
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-sync pytest xqvm_py/tests/test_executor.py::TestFaultOrdering -q`
Expected: four FAILs raising `TypeMismatch`.

- [ ] **Step 3: Reorder**

```python
    def _runner_EXCLUDE(self, instr: Instruction) -> None:
        """EXCLUDE: Add exclusion constraint with penalty."""
        reg = instr.operands[0]
        # Pops, then the charge, then the register read: `exec_exclude`
        # charges at vm.rs:2470 and resolves the register at vm.rs:2471.
        penalty, j, i = self.state.pop_n(3)
        self._charge_coefficient(reg, QUAD_ENTRY_BYTES)
        model = self._get_register_as_xqmx(reg)
        expand_exclude(model, i, j, penalty)

    def _runner_IMPLIES(self, instr: Instruction) -> None:
        """IMPLIES: Add implication constraint with penalty."""
        reg = instr.operands[0]
        # Pops, then the charge, then the register read; see EXCLUDE.
        penalty, j, i = self.state.pop_n(3)
        self._charge_coefficient(reg, LINEAR_ENTRY_BYTES + QUAD_ENTRY_BYTES)
        model = self._get_register_as_xqmx(reg)
        expand_implies(model, i, j, penalty)
```

For `REDUCE`, the two charges move above the register read, and everything after the charge stays put:

```python
    def _runner_REDUCE(self, instr: Instruction) -> None:
        """REDUCE: Rosenberg degree reduction."""
        model_reg = instr.operands[0]
        # Pops, then the charges, then the register read: `exec_reduce`
        # charges at vm.rs:2760-2761 and resolves the register at vm.rs:2762.
        p_aux, var_b, var_a = self.state.pop_n(3)
        # Rosenberg reduction adds one auxiliary variable, three quadratic
        # terms and one linear term.
        self._charge_variables(1)
        self._charge(3 * QUAD_ENTRY_BYTES + LINEAR_ENTRY_BYTES)
        model = self._get_register_as_xqmx(model_reg)
```

Read `_runner_REDUCE`'s remaining lines (`xqvm_py/executor.py:1354`ff) and leave them unchanged below the register read.

- [ ] **Step 4: Verify**

Run: `cargo test -p xquad-conformance --no-default-features --features python reduce exclude implies`
Expected: PASS, including `reduce_basic`, `reduce_chain`, `exclude_pair` and `implies_chain`.

Run: `uv run --no-sync pytest xqvm_py/tests -q`
Expected: PASS.

- [ ] **Step 5: Drop the three names from the allowlist, then commit**

```bash
git add xqvm_py/executor.py xqvm_py/tests/test_executor.py
git commit -m "fix(xqvm-py): charge before the register read on EXCLUDE/IMPLIES/REDUCE"
```

---

## Task 8: `EQUALITY`, `ATLEAST`, `ATLEASTW` — register order, not just pop order

**Files:**
- Modify: `xqvm_py/executor.py:1262-1345`
- Modify: `xqvm_py/tests/test_executor.py`
- Create: `conformance/vectors/constraints/atleast_k_range_before_model_type/` — `program.xqasm`, `inputs.json`, `expected.json`
- Create: `conformance/vectors/constraints/atleastw_length_before_model_type/` — same three files

**Interfaces:**
- Consumes: `Executor._peek_register` (Task 4), `TestFaultOrdering.KNOWN_UNORDERED`.
- Produces: an empty `KNOWN_UNORDERED` on the Python side (Task 10 retires the allowlist).

`ATLEAST` and `ATLEASTW` are the other two verifier-clean route-B cases, so they get conformance vectors alongside the reorder, exactly as `RESIZE` did in Task 2. `EQUALITY` does not: Rust's `VecLengthMismatch` has no Python counterpart in `_runner_EQUALITY`, which is a separate divergence (Out of Scope 2). Do not add that check here.

These three read three registers, and moving only the pops would leave a second divergence in place. Rust reads `indices` (then `coeffs`) immediately after its pops, and resolves `model` *after* the charges — `vm.rs:2533`/`2542`/`2575` for `EQUALITY`, `2603`/`2636` for `ATLEAST`, `2674`/`2683`/`2729` for `ATLEASTW`. Python reads `model` first in all three. The model read moves to just before its first mutating use.

The model in these handlers is read only to receive output, which is why it validates last under the Global Constraints rule.

- [ ] **Step 1: Write the two failing vectors**

`conformance/vectors/constraints/atleast_k_range_before_model_type/program.xqasm`:

```
; atleast_k_range_before_model_type: ATLEAST range-checks k before it
; type-checks its model register.
;
; r0 holds an int, not a model, and k=0 is out of ATLEAST's (0, n] range.
; Both faults are live; SPEC.md:176's error precedence decides which one is
; raised -- exec_at_least rejects k at vm.rs:2616, and only reaches
; reg_mut(model) at vm.rs:2636. INPUT writes a register of unknown kind, so
; the verifier admits this program (QUI-1178 route B).

PUSH 0
INPUT r0         ; r0 = calldata[0] = 5, an int -- not a model

VECI r1
PUSH 0
VECPUSH r1
PUSH 1
VECPUSH r1

PUSH 0           ; k = 0, out of range
PUSH 1           ; penalty
ATLEAST r0 r1
HALT
```

`inputs.json`:

```json
{
  "calldata": [5],
  "output_slots": 4
}
```

`expected.json`:

```json
{
  "error": "INDEX_OUT_OF_BOUNDS"
}
```

`conformance/vectors/constraints/atleastw_length_before_model_type/program.xqasm`:

```
; atleastw_length_before_model_type: ATLEASTW compares its two vec lengths
; before it type-checks its model register.
;
; r0 holds an int, not a model; indices has two entries and coeffs has one.
; exec_at_least_w raises VecLengthMismatch at vm.rs:2696 and only reaches
; reg_mut(model) at vm.rs:2729, so the length fault is the one both VMs
; must raise. Verifier-clean via INPUT (QUI-1178 route B).

PUSH 0
INPUT r0         ; r0 = calldata[0] = 5, an int -- not a model

VECI r1
PUSH 0
VECPUSH r1
PUSH 1
VECPUSH r1

VECI r2
PUSH 1
VECPUSH r2       ; one coefficient against two indices

PUSH 1           ; k
PUSH 1           ; penalty
ATLEASTW r0 r1 r2
HALT
```

`inputs.json`:

```json
{
  "calldata": [5],
  "output_slots": 4
}
```

`expected.json`:

```json
{
  "error": "VEC_LENGTH_MISMATCH"
}
```

Check the exact `Fault` spellings before writing `expected.json` — run `grep -n "IndexOutOfBounds\|VecLengthMismatch" conformance/src/lib.rs` and use the serialized names the harness expects, matching the style of `conformance/vectors/xqmx-grid/resize_negative_rows/expected.json`.

- [ ] **Step 2: Confirm each vector is admitted, and fails against Python**

Run: `cargo test -p xquad-conformance --no-default-features --features rust atleast`
Expected: PASS — Rust already raises these two faults.

Run: `cargo test -p xquad-conformance --no-default-features --features python atleast`
Expected: the two new vectors FAIL with a fault-identity mismatch (Python raises `TypeMismatch` on the model register); `atleast_basic`, `atleast_boundary`, `atleastw_basic`, `atleastw_unit` and `atleastw_weight_overflow` still PASS.

Record both outputs before continuing.

- [ ] **Step 3: Write the failing test**

Append to `TestFaultOrdering`:

```python
    @pytest.mark.parametrize(
        "opcode,operands",
        [
            (Opcode.EQUALITY, (0, 1, 2)),
            (Opcode.ATLEAST, (0, 1)),
            (Opcode.ATLEASTW, (0, 1, 2)),
        ],
    )
    def test_multi_register_opcodes_read_their_inputs_before_the_model(
        self, opcode, operands
    ):
        """The indices register is validated before the model register.

        Rust resolves `indices` right after its pops and `model` only after
        the charges (vm.rs:2533/2575, 2603/2636, 2674/2729). With both r0
        (model) and r1 (indices) holding ints, the reported register must be
        r1 in both VMs.
        """
        ex = Executor()
        with pytest.raises(TypeMismatch) as excinfo:
            ex.execute(
                make_program(
                    [
                        Instruction(Opcode.PUSH1, (1,)),
                        Instruction(Opcode.PUSH1, (1,)),
                        Instruction(opcode, operands),
                        Instruction(Opcode.HALT),
                    ]
                ),
                input_data={0: 5, 1: 5, 2: 5},
            )
        assert "r1" in str(excinfo.value)

    @pytest.mark.parametrize(
        "opcode,operands",
        [
            (Opcode.EQUALITY, (0, 1, 2)),
            (Opcode.ATLEAST, (0, 1)),
            (Opcode.ATLEASTW, (0, 1, 2)),
        ],
    )
    def test_multi_register_opcodes_underflow_before_any_register_read(
        self, opcode, operands
    ):
        ex = Executor()
        with pytest.raises(StackUnderflow):
            ex.execute(
                make_program(
                    [
                        Instruction(Opcode.PUSH1, (1,)),
                        Instruction(opcode, operands),
                        Instruction(Opcode.HALT),
                    ]
                ),
                input_data={0: 5, 1: 5, 2: 5},
            )
```

- [ ] **Step 4: Run to verify it fails**

Run: `uv run --no-sync pytest xqvm_py/tests/test_executor.py::TestFaultOrdering -q`
Expected: six FAILs — the first three reporting `r0` where `r1` is expected, the second three raising `TypeMismatch` where `StackUnderflow` is expected.

- [ ] **Step 5: Reorder `EQUALITY`**

```python
    def _runner_EQUALITY(self, instr: Instruction) -> None:
        """EQUALITY: Expand weighted equality constraint into QUBO terms."""
        model_reg = instr.operands[0]
        indices_reg = instr.operands[1]
        coeffs_reg = instr.operands[2]
        # Pops, then the input registers, then the charges, then the model:
        # `exec_equality` resolves indices and coeffs at vm.rs:2533/2542 and
        # only reaches `reg_mut(model)` at vm.rs:2575, after both charges.
        penalty, target = self.state.pop_n(2)
        indices_vec = self._get_register_as_vec(indices_reg)
        coeffs_vec = self._get_register_as_vec(coeffs_reg)
        indices = [indices_vec.get(i) for i in range(indices_vec.length)]
        coeffs = [coeffs_vec.get(i) for i in range(coeffs_vec.length)]
        # The expansion is quadratic in the number of terms, and EQUALITY
        # also grows the model to cover the largest index it was handed --
        # both from vec contents the program controls. Sizing the growth
        # peeks the model without faulting on it.
        peeked = self._peek_register(model_reg)
        current_size = peeked.size if isinstance(peeked, XQMX) else 0
        needed = max(indices) + 1 if indices else 0
        if indices:
            self._charge_variables(needed - current_size)
        self._charge_equality_expansion(len(indices))

        model = self._get_register_as_xqmx(model_reg)
        if indices:
            model.size = max(model.size, needed)
        expand_equality(model, indices, coeffs, target, penalty)
```

- [ ] **Step 6: Reorder `ATLEAST`**

```python
    def _runner_ATLEAST(self, instr: Instruction) -> None:
        """ATLEAST: At-least-k constraint with slack variables."""
        model_reg = instr.operands[0]
        indices_reg = instr.operands[1]
        # Pops, then the indices register, then the charges, then the model;
        # see EQUALITY. `exec_at_least` resolves indices at vm.rs:2603 and
        # the model at vm.rs:2636.
        penalty, k = self.state.pop_n(2)
        indices_vec = self._get_register_as_vec(indices_reg)
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

        model = self._get_register_as_xqmx(model_reg)
        require_model_mode(model, "ATLEAST")
        if num_slacks == 0:
            expand_equality(model, orig_indices, [1] * n, k, penalty)
            return
        slack_start = model.size
        model.size += num_slacks
        combined_indices = orig_indices + [slack_start + i for i in range(num_slacks)]
        combined_coeffs = [1] * n + [-(1 << i) for i in range(num_slacks)]
        expand_equality(model, combined_indices, combined_coeffs, k, penalty)
```

Note that `require_model_mode` moves down with the model read. Rust has no equivalent call at that point — that divergence is recorded under Out of Scope, and this task neither widens nor narrows it.

- [ ] **Step 7: Reorder `ATLEASTW`**

Apply the same shape: after `penalty, k = self.state.pop_n(2)`, read `indices_vec` then `coeffs_vec`, run the existing length check, `k <= 0` check, index/weight extraction, the `weight_sum`/`max_excess`/`num_slacks` derivation and both charges — all unchanged — then read the model:

```python
        penalty, k = self.state.pop_n(2)
        indices_vec = self._get_register_as_vec(indices_reg)
        coeffs_vec = self._get_register_as_vec(coeffs_reg)
        n = indices_vec.length
        if n != coeffs_vec.length:
            raise VecLengthMismatch("indices", n, "coeffs", coeffs_vec.length)
```

...through to...

```python
        self._charge_variables(num_slacks)
        self._charge_equality_expansion(n + num_slacks)

        model = self._get_register_as_xqmx(model_reg)
        require_model_mode(model, "ATLEASTW")
        if num_slacks == 0:
```

...with the rest of the body unchanged.

- [ ] **Step 8: Verify**

Run: `cargo test -p xquad-conformance --no-default-features --features python`
Expected: PASS across every vector including the two added in Step 1, notably `equality_basic`, `equality_weighted`, `equality_additive`, `equality_penalty_overflow`, `atleast_basic`, `atleast_boundary`, `atleastw_basic`, `atleastw_unit`, `atleastw_weight_overflow`.

Run: `uv run --no-sync pytest xqvm_py/tests -q`
Expected: PASS.

- [ ] **Step 9: Drop the three names from the allowlist, then commit**

`KNOWN_UNORDERED` is now empty.

```bash
git add xqvm_py/executor.py xqvm_py/tests/test_executor.py conformance/vectors/constraints
git commit -m "fix(xqvm-py): validate input registers before the model register"
```

---

## Task 9: `exec_equality` charges before its register reads

**Files:**
- Modify: `xqvm/src/vm.rs:2524-2593`
- Test: `xqvm/tests/` — add to the integration test file that already covers the allocation budget (find it with `rg -l "MemoryLimitExceeded" xqvm/tests/`; if none exists, add the test as a `#[cfg(test)]` case beside the other `vm.rs` unit tests)

**Interfaces:**
- Consumes: nothing from earlier tasks — this is independent of the Python work and may be done in parallel.
- Produces: nothing.

`exec_equality` type-checks *and clones* both vec registers before it charges for them, inverting the charge-then-validate order and doing real allocation ahead of the charge. It is bounded — it clones vecs that already exist — but `ONEHOTR`/`ONEHOTC` show the intended pattern, and this handler is the one place in the Rust VM that departs from it.

The obstacle: the `needed`/`charge_equality_expansion` sizing reads `idx_vec`, so the charge cannot simply move above the reads. Resolve it by peeking both registers non-faulting for their lengths and max index, charging, and only then type-checking and cloning.

- [ ] **Step 1: Write the failing test**

```rust
#[test]
fn equality_charges_before_it_reads_its_registers() {
    // `exec_equality` must charge for the expansion before it type-checks
    // and clones its vec registers (spec/xqvm/SPEC.md, order of work stage
    // 2 before stage 3). With a budget of zero and a coeffs register of the
    // wrong kind, the memory fault is the one that must surface.
    let mut vm = Vm::new();
    vm.set_memory_limit(0);
    vm.set_register(Register::from_slot(0), RegVal::Model(Model::new(4)));
    vm.set_register(Register::from_slot(1), RegVal::VecInt(vec![0, 1, 2]));
    vm.set_register(Register::from_slot(2), RegVal::Int(5));
    vm.push(1).unwrap();
    vm.push(1).unwrap();
    let err = vm
        .step_equality(Register::from_slot(0), Register::from_slot(1), Register::from_slot(2))
        .unwrap_err();
    assert!(
        matches!(err, Error::MemoryLimitExceeded { .. }),
        "expected MemoryLimitExceeded, got {err:?}"
    );
}
```

Adjust the constructor and register-setting calls to the actual `xqvm` test helpers — read a neighbouring test in the same file and copy its setup verbatim rather than inventing API. The assertion is the part that matters.

- [ ] **Step 2: Run to verify it fails**

Run: `cargo nextest run -p xqvm --all-features equality_charges_before`
Expected: FAIL — `Error::RegisterType { reg: 2, .. }` instead of `MemoryLimitExceeded`.

- [ ] **Step 3: Restructure `exec_equality`**

Replace lines `2158`–`2201` (the two `.clone()` blocks through `charge_equality_expansion`) with a peek-charge-then-validate sequence:

```rust
        // Size the charge from a non-faulting peek, so the allocation charge
        // precedes the type check (SPEC.md's order of work) and no vec is
        // cloned before it has been paid for. A register of the wrong kind
        // sizes to nothing and falls through to the type error below --
        // `exec_one_hot_r` charges the same way.
        let (n, needed) = match self.reg(indices) {
            RegVal::VecInt(v) => (
                v.len(),
                v.iter()
                    .max()
                    .and_then(|&max_idx| max_idx.checked_add(1))
                    .and_then(|needed| usize::try_from(needed).ok())
                    .unwrap_or(0),
            ),
            _ => (0, 0),
        };
        let current_size = match self.reg(model) {
            RegVal::Model(m) => m.size,
            _ => 0,
        };
        self.charge_variables(pos, needed.saturating_sub(current_size))?;
        self.charge_equality_expansion(pos, n)?;
        let idx_vec: Vec<i64> = self
            .reg(indices)
            .as_vec_int()
            .map_err(|e| Error::RegisterType {
                reg: indices.slot(),
                expected: "vec<int>",
                got: e.actual.kind_name(),
            })?
            .clone();
        let coeff_vec: Vec<i64> = self
            .reg(coeffs)
            .as_vec_int()
            .map_err(|e| Error::RegisterType {
                reg: coeffs.slot(),
                expected: "vec<int>",
                got: e.actual.kind_name(),
            })?
            .clone();
        if idx_vec.len() != coeff_vec.len() {
            return Err(Error::VecLengthMismatch {
                what: "indices",
                a: idx_vec.len(),
                other: "coeffs",
                b: coeff_vec.len(),
            });
        }
```

Then continue with the existing `let m = self.reg_mut(model)...` block at `2200`ff unchanged.

This changes one observable ordering in Rust: with an over-budget expansion *and* a length mismatch, the fault is now `MemoryLimitExceeded` rather than `VecLengthMismatch`. That is the order SPEC.md's stage 2/stage 3 split requires. Python's `EQUALITY` after Task 8 charges in the same place, so the two stay in step.

- [ ] **Step 4: Run to verify it passes**

Run: `cargo nextest run -p xqvm --all-features`
Expected: PASS, whole package.

Run: `cargo test -p xquad-conformance --no-default-features --features rust`
Expected: PASS — in particular `equality_penalty_overflow`, whose fault sits downstream of the charge.

- [ ] **Step 5: Commit**

```bash
git add xqvm/src/vm.rs xqvm/tests
git commit -m "fix(xqvm): charge EQUALITY before it reads and clones its vecs"
```

---

## Task 10: Retire the allowlist and close out

**Files:**
- Modify: `xqvm_py/tests/test_executor.py` (`TestFaultOrdering`)
- Modify: `conformance/README.md` (route-B enumeration table)

**Interfaces:**
- Consumes: everything above.
- Produces: the final state — an unconditional invariant with no allowlist.

- [ ] **Step 1: Turn the ratchet into a plain assertion**

Delete `KNOWN_UNORDERED` and rewrite the assertion:

```python
    def test_pops_precede_the_register_read(self):
        offenders = set()
        for runner in Executor()._build_dispatch_table().values():
            read, pop = self._first_positions(runner)
            if read is not None and pop is not None and read < pop:
                offenders.add(runner.__name__)
        assert offenders == set(), (
            "these runners resolve a register before popping their operands, "
            "which raises TypeMismatch where the Rust VM raises the operand "
            "fault -- a consensus-visible divergence "
            f"(spec/xqvm/SPEC.md, QUI-1178): {sorted(offenders)}"
        )
```

- [ ] **Step 2: Record the route-B enumeration**

Append to `conformance/README.md`, under whatever section documents vector coverage:

```markdown
### Fault ordering (QUI-1178)

`spec/xqvm/SPEC.md` fixes the order of work within one instruction: pops,
then the allocation charge, then validation. Where a register read once
preceded the pops in `xqvm_py`, the divergence was reachable two ways --
a short stack (which the verifier rejects) and an operand fault raised
ahead of the register read (which it admits). Only the second is
consensus-visible.

Of the 22 opcodes corrected, the second route reaches:

| Opcode(s) | Fault raised ahead of the register read | Pinned by |
|---|---|---|
| `RESIZE` | `InvalidGridDimensions`, unconditional | `vectors/xqmx-grid/resize_type_after_dimension_check` |
| `VECPUSH`, `SETLINE`, `ADDLINE`, `SETQUAD`, `ADDQUAD`, `ONEHOTR`, `ONEHOTC`, `EXCLUDE`, `IMPLIES`, `REDUCE` | `MemoryLimitExceeded`, only against a near-exhausted budget | `xqvm_py/tests/test_executor.py::TestFaultOrdering` -- `Inputs` carries no `memory_limit`, so a vector cannot express these |
| `ATLEAST` | `IndexOutOfBounds` on `k`, unconditional | `vectors/constraints/atleast_k_range_before_model_type` |
| `ATLEASTW` | `VecLengthMismatch`, unconditional | `vectors/constraints/atleastw_length_before_model_type` |
| `EQUALITY` | `VecLengthMismatch` in Rust | not pinnable: `_runner_EQUALITY` has no length check at all, a separate divergence filed alongside this work |
| `VECGET`, `VECSET`, `GETLINE`, `GETQUAD`, `ROWFIND`, `COLFIND`, `ROWSUM`, `COLSUM` | none -- Rust type-checks immediately after its pops | short stack only, not verifier-clean |
```

- [ ] **Step 3: Run the full verification set**

Run: `make conformance`
Expected: PASS for both `conformance-rs` and `conformance-py`.

Run: `uv run --no-sync pytest xqvm_py/tests -q`
Expected: PASS.

Run: `cargo nextest run -p xqvm --all-features`
Expected: PASS.

Run: `make lint` (or the repo's Python lint target — check `Makefile`)
Expected: clean.

Record the actual output of each. Do not report the task complete on any command that was not run in this session.

- [ ] **Step 4: Re-read the diff**

Run: `git diff main...HEAD -- xqvm_py/executor.py`

Check every reordered runner against its Rust counterpart one more time: same pop count, same charge placement, same register order, same validation sequence. This is the review that catches a transposed `pop_n` tuple — `value, index` versus `index, value` — which no test above would catch if both operands are `1`.

- [ ] **Step 5: File the out-of-scope findings**

Open one Linear ticket per item in the **Out of Scope** section below, each linked to QUI-1178. Do this before closing the branch, so the findings do not evaporate with the session.

- [ ] **Step 6: Commit**

```bash
git add xqvm_py/tests/test_executor.py conformance/README.md
git commit -m "test(xqvm-py): make the fault-ordering invariant unconditional"
```

---

## Out of Scope

Found while reading the handlers side by side. Each is a real Rust/Python divergence, none is what this ticket fixes, and none should be repaired inside it. File each as its own ticket in Task 10 Step 5.

1. **`EXCLUDE` and `IMPLIES` charge conditionally in Python, unconditionally in Rust.** `exec_exclude` (`vm.rs:2470`) calls `self.charge(pos, QUAD_ENTRY_BYTES)` for any register kind; Python calls `_charge_coefficient`, which charges only for a model. Against a tight budget with a sample in the register, Rust raises `MemoryLimitExceeded` and Python does not. Same for `IMPLIES`.

2. **`EQUALITY` has no length check in Python.** `exec_equality` raises `VecLengthMismatch` when `indices` and `coeffs` differ in length (`vm.rs:2551`); `_runner_EQUALITY` zips them implicitly. `Fault::VecLengthMismatch` exists but `fault_from_python` maps nothing to it.

3. **`require_model_mode` has no Rust counterpart in `ATLEAST`/`ATLEASTW`.** Python calls it; the Rust handlers do not check mode at that point. Whether Rust is missing a check or Python has a spurious one is a spec question, not a reorder.

4. **`conformance::Inputs` cannot express a memory limit.** It carries `calldata`, `output_slots` and `step_limit` (`conformance/src/lib.rs:74-98`). Adding `memory_limit`, plumbed to `Vm::set_memory_limit` and to a `--memory-limit` flag on `python -m xqvm_py run`, would let the ten budget-dependent route-B cases be pinned as vectors rather than as per-VM unit tests.

---

## Self-Review

**Spec coverage.** The ticket's four scope bullets map to: reorder the 22 Python runners → Tasks 2–8 (1 + 3 + 6 + 4 + 2 + 3 + 3 = 22, counting `RESIZE`, the vec three, the coefficient six, the grid four, the onehot two, `EXCLUDE`/`IMPLIES`/`REDUCE`, and the multi-register three); move `exec_equality`'s charge ahead of its register reads → Task 9; enumerate which of the 22 admit route B and pin those with vectors, `RESIZE` at minimum → the Route-B Enumeration section, Task 2's `RESIZE` vector, Task 8's `ATLEAST`/`ATLEASTW` vectors, and Task 10 Step 2; no spec change → met, `SPEC.md:176` already decides this. The ticket's "Done when" adds a regression dimension the scope bullets do not, covered by Task 1's invariant.

**Dependency check.** The ticket says vectors asserting an expected error depend on QUI-1017, which is Done — confirmed: `expected.json` with an `error` key is already the shape of `vectors/xqmx-grid/resize_negative_rows`. `SPEC.md:176` and the `ITER` fix are both on `origin/main`, so this plan has no unlanded prerequisites.

**Type consistency.** `_peek_register(slot) -> Value | None` is defined in Task 4 and consumed in Tasks 7, 9. `_charge_coefficient(slot, nbytes)` changes signature in Task 4; Task 4 Step 5 sweeps the two stale callers in Tasks 8's runners so no commit between them is broken. `KNOWN_UNORDERED` is created in Task 1, shrunk in Tasks 2–8, retired in Task 10.
