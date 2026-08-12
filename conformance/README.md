# XQVM Conformance Harness

Mechanical cross-implementation check that the Rust production VM
([`xqvm`](../xqvm/)) and the Python reference VM
([`xqvm_py`](../xqvm_py/)) agree on every observable behaviour defined
by [`spec/xqvm/SPEC.md`](../spec/xqvm/SPEC.md).

The harness is a Rust test crate (`xquad-conformance`). Each vector
becomes two `#[test]` functions -- one per runtime -- generated at
`cargo build` time by [`build.rs`](build.rs). CI runs them as two
independent GitLab jobs so a Python-side regression cannot mask a
Rust-side pass (or vice versa).

## Layout

```
conformance/
├── opcodes.yaml          single machine-readable source for the 93-opcode table
├── vectors/
│   ├── arithmetic/<name>/
│   │   ├── program.xqasm       canonical assembly source
│   │   ├── inputs.json         {"calldata": [i64, ...], "output_slots": N}
│   │   └── expected.json       {"outputs": [i64|null, ...], "final_stack": [i64, ...]}
│   ├── control-flow/<name>/
│   ├── energy/<name>/
│   ├── constraints/<name>/
│   ├── vector-ops/<name>/
│   └── xqmx-grid/<name>/
├── src/                  Rust library + manual CLI binary
├── tests/rust.rs         generated in-process Rust runner tests
└── tests/python.rs       generated subprocess Python runner tests
```

## File format

### `program.xqasm`

Canonical human-readable source. The assembler is authoritative: whatever
`xqasm::assemble_source()` produces becomes the canonical bytecode.
Prefer explicit `PUSH1`/`PUSH2`/... forms when the constant width matters
for the vector; use the `PUSH` sugar where it doesn't.

The harness assembles `program.xqasm` in-process on every run; encoding
correctness is owned by the `xqasm` crate's own test suite, so no
pre-assembled bytecode artifact is committed.

### `inputs.json`

```json
{
  "calldata": [6, 7],
  "output_slots": 16
}
```

`output_slots` is optional (defaults to 16, matching `xquad run`). The
`calldata` array is exposed to the program in slot order: slot 0 holds
the first value, slot 1 the second, and so on.

### `expected.json`

```json
{
  "outputs": [42],
  "final_stack": []
}
```

`outputs` is a sparse map, per the state model in `spec/xqvm/SPEC.md` --
each entry is either an `i64` (the value written by `OUTPUT`) or `null`
(the slot was reserved but explicitly zeroed by a peer entry). Trailing
unset slots are omitted entirely, so a program that writes slot 0 out of
16 reserved slots produces `[42]`, not `[42, null, ...]`.
Explicitly-written zeroes are preserved; only slots that `OUTPUT` never
touched disappear. `final_stack` is the residual stack at `HALT` (bottom
to top); an empty stack is typical.

## Authoring a new vector

1. Write `program.xqasm` with a minimal scenario that exercises the
   behaviour you care about.
2. Pick the matching vector directory under `vectors/` (or create a new
   one). These directory names are a separate, coarser vocabulary from
   `opcodes.yaml`'s own `category` field -- the two do not need to match,
   and mostly don't.
3. Write `inputs.json` with the calldata the program reads.
4. Produce `expected.json` by running one of the two impls and
   inspecting the result. The harness then asserts the *other* impl
   agrees. If they disagree, you've found spec drift -- file a bug
   ticket, and either fix the divergent impl or exclude the vector
   with a comment referencing the ticket.

## Drift policy

There is no `DRIFT.md`. Either every vector passes on both runtimes or
the build is broken. Concrete enforcement:

- **Opcode table** -- `xqvm/build.rs` asserts `opcodes.yaml` against the
  `opcodes!` x-macro at compile time; `scripts/check-opcode-parity.py`
  asserts `opcodes.yaml` against `xqvm_py/opcodes.py` in CI.
- **Bytecode encoding** -- owned by the `xqasm` crate's own test suite
  (`xqasm/tests/integration.rs` plus assembler unit tests).
- **Observable behaviour** -- [`check_vector`](src/lib.rs) asserts the
  observed `{outputs, final_stack}` matches `expected.json` for both
  runtimes.

## Running locally

```sh
# Full matrix (both runtimes, all vectors)
cargo test -p xquad-conformance

# Rust only
cargo test -p xquad-conformance --no-default-features --features rust

# Python only (needs python3 + xqvm_py importable from xqvm_py/)
cargo test -p xquad-conformance --no-default-features --features python

# Manual CLI for authoring / triaging a single vector
cargo run -p xquad-conformance -- --filter arithmetic/add_basic --impl both
```

Override the Python interpreter with `XQUAD_CONFORMANCE_PYTHON=...`.
