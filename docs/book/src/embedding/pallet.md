# Pallet Fixture

[`pallet-xqvm`](https://gitlab.com/quip.network/xquad/-/tree/main/fixtures/pallet-xqvm)
embeds `xqvm` inside a Substrate FRAME runtime, as a compile-time and
runtime integration gate. Its own module documentation states the purpose
plainly: if `xqvm`'s public API changes in a way that breaks Substrate
pallet integration, CI fails here first.
`fixtures/pallet-xqvm` is an excluded member of the main Cargo workspace,
declared in
[`Cargo.toml`](https://gitlab.com/quip.network/xquad/-/blob/main/Cargo.toml),
because it pulls in a heavy polkadot-sdk git dependency.
Treat this chapter as a reference integration and a fixture to build on,
not as a supported deployment path with its own release cycle.

CI still runs it, and blocks on it. `test:substrate` in
[`.gitlab/ci/test.yml`](https://gitlab.com/quip.network/xquad/-/blob/main/.gitlab/ci/test.yml)
is path-gated: on a merge request or a feature branch it runs only when
the diff touches `xqvm`, the fixture, or the files that define the job
itself, and it runs unconditionally on protected refs and release tags.
It carries no `allow_failure:`, so it blocks the pipeline whenever it is
created. `make
test-substrate-fixture` runs `cargo test --manifest-path
fixtures/pallet-xqvm/Cargo.toml`, exercising eight pallet tests plus two
runtime-integrity checks FRAME generates from `construct_runtime!`. Every
claim below is checked against
[`fixtures/pallet-xqvm/src/lib.rs`](https://gitlab.com/quip.network/xquad/-/blob/main/fixtures/pallet-xqvm/src/lib.rs).

## Configuration

The pallet is configured via the `Config` trait:

```rust,ignore
#[pallet::config]
pub trait Config: frame_system::Config<RuntimeEvent: From<Event<Self>>> {
    /// Maximum byte length of an uploaded XQBC program.
    #[pallet::constant]
    type MaxProgramSize: Get<u32>;

    /// Maximum number of calldata input slots and output slots.
    #[pallet::constant]
    type MaxCalldata: Get<u32>;
}
```

Two associated types, both bounds rather than tunable behaviour. The mock
runtime used by the fixture's own tests sets `MaxProgramSize = 65_536` (64
KiB) and `MaxCalldata = 256`, matching the VM's 256-slot register file. A
single `MaxCalldata` bound caps both the calldata a caller supplies and the
outputs the pallet can return -- there is no separate output-side limit.

## Storage

```rust,ignore
#[pallet::storage]
pub type StoredProgram<T: Config> =
    StorageValue<_, BoundedVec<u8, T::MaxProgramSize>, OptionQuery>;
```

One value, not a map. `StoredProgram` holds the bytecode of the most
recently executed program; calling the pallet's extrinsic again overwrites
it. There is no per-program storage keyed by hash, and no record of which
account submitted which program beyond the event stream below.

## The `submit_program` Extrinsic

The pallet exposes exactly one dispatchable, call index 0:

```rust,ignore
#[pallet::call_index(0)]
pub fn submit_program(
    origin: OriginFor<T>,
    bytecode: BoundedVec<u8, T::MaxProgramSize>,
    calldata: BoundedVec<i64, T::MaxCalldata>,
    output_slots: u32,
    step_limit: u64,
) -> DispatchResult
```

| Parameter | Type | Description |
|---|---|---|
| `bytecode` | `BoundedVec<u8, T::MaxProgramSize>` | XQBC-encoded program, produced by `InstructionBuilder::build().encode()` or by assembling `.xqasm` source. |
| `calldata` | `BoundedVec<i64, T::MaxCalldata>` | Integer values injected as `RegVal::Int` into the VM's calldata slots, in order. |
| `output_slots` | `u32` | Output slots to reserve. The caller declares its own arity; see step 3. May not exceed `MaxCalldata`. |
| `step_limit` | `u64` | Instructions the run may execute. The bound is exact: `0` executes nothing and the call fails with `ExecutionFailed` rather than succeeding vacuously. |

Decode, execute, store, in that order:

1. Require a signed origin (`ensure_signed`); an unsigned call is rejected
   with `BadOrigin` before pallet logic runs.
2. Decode `bytecode` as an `xqvm::Program`. A decode failure returns
   `Error::BytecodeInvalid` and nothing is stored.
3. Take the output-slot count from the caller's `output_slots` argument,
   rejecting a request above `MaxCalldata` with `OutputSlotsTooLarge`.

   Do **not** derive it from `program.output_slots()`. That header byte is
   a saturating count of `OUTPUT` *instructions*, not of slots, so one
   `OUTPUT` inside a loop that writes slots 0 and 1 records `1`; sizing the
   output vec from it makes the slot-1 write raise `OutputIndex` and the
   call fail with `ExecutionFailed`, rejecting a program the VM and the
   verifier both accept. See
   [the bytecode format](../xqvm/bytecode-format.md), which states that
   neither header count may be used to pre-size a slot array.
4. Build a fresh `Vm`, set the calldata, the output-slot count and the
   caller's `step_limit`, and run the program. Any VM fault returns
   `Error::ExecutionFailed`; the pallet does not distinguish which fault
   occurred, so a budget exhausted one instruction short of `HALT` is
   indistinguishable from a decode-clean program that divided by zero.
5. Collect the `Int` outputs (any other `RegVal` variant in an output slot
   is silently dropped from the result) and bound them to `T::MaxCalldata`.
   Exceeding that bound returns `Error::OutputOverflow`.
6. Store `bytecode` in `StoredProgram`, overwriting any previous value, and
   emit `Event::ProgramExecuted { who, outputs }`.

There is no separate store-then-execute split and no program lookup by
hash. The step budget is an extrinsic argument rather than a pallet
constant because that is the shape the real pallet has to take: what a
caller pre-pays for is what the VM may spend, and a budget the caller
cannot name is a threat model the fixture cannot express.

The allocation budget has no such argument. The fixture never calls
`set_memory_limit`, so the VM's 1 GiB default applies, which is far too
generous for a runtime that has to price what it admits -- inside a
wasm32 runtime the heap gives out long before the budget does, and the
allocator traps the whole execution instead of returning a fault the
pallet can report. A production pallet should set and price a much
smaller budget. Off-chain, produce the bytecode however you like; the
assembler CLI is `xquad asm`.

The cargo profile the runtime is built with is a second default an
operator must not inherit without reading it. Do not compile the runtime
that carries this pallet with `overflow-checks = true` until every
`#[expect(clippy::arithmetic_side_effects, ...)]` entry in `xqvm` has been
re-read as a claim about that build: the VM already raises
`ArithmeticOverflow` for the arithmetic it performs on a program's behalf,
so the flag adds nothing there and instead turns the host-side operations
that allow-list records into panics, and a panic inside block execution is
a block-production fault, strictly worse than a wrong answer. The flag
would not touch the VM's deliberate wraps, which are method calls
(`SHL`'s `wrapping_shl`, `SLACK`'s `wrapping_mul`); it acts on the bare
operator sites, which are exactly the ones the allow-list claims cannot
leave range. That is why the reading is the gate, and it does not expire
once the allow-list exists.

## Weight

```rust,ignore
#[pallet::weight(Weight::from_parts(10_000, 0).saturating_add(T::DbWeight::get().writes(1)))]
```

A fixed placeholder, not a metered cost model: the same weight is charged
regardless of program size or step count actually used. The extrinsic
neither computes an actual weight from `vm.steps()` nor refunds any
difference via `PostDispatchInfo`. The pallet's own source comment says as
much: a production integration must supply real benchmarks. This weight
exists only so the extrinsic compiles and the fixture's tests can dispatch
it.

## Events and Errors

| Event | Fields | Description |
|---|---|---|
| `ProgramExecuted` | `who`, `outputs` | Emitted once, after a successful run. `outputs` holds only the `Int`-valued output slots, in slot order. |

| Error | Meaning |
|---|---|
| `BytecodeInvalid` | `bytecode` failed XQBC decode. |
| `ExecutionFailed` | The VM faulted at runtime -- stack underflow, an unresolved jump, or any other `xqvm::Error` variant, all mapped to this one case. |
| `OutputOverflow` | The program produced more `Int` outputs than `MaxCalldata` allows. |
| `StepLimitTooLarge` | `step_limit` exceeds `MaxStepLimit`. Checked before the decode, so an over-budget request never pays to parse the program it would have run. |
| `OutputSlotsTooLarge` | `output_slots` exceeds `MaxCalldata`. |

## What the Fixture's Tests Check

`fixtures/pallet-xqvm/src/tests.rs` covers four groups, thirteen tests in
all:

- **Happy paths** -- an arithmetic program with no calldata, a calldata
  passthrough, and a two-value sum.
- **Error paths** -- invalid bytecode, a stack underflow that reaches
  `ExecutionFailed`, and an unsigned origin rejected by `ensure_signed`.
- **The step budget** -- a request above `MaxStepLimit`, one exactly at
  it, a zero budget, and a budget shorter than the program.
- **The output-slot count** -- one `OUTPUT` inside a loop writing a slot
  per iteration, the same program with the count the header byte would
  have supplied, and a count above `MaxCalldata`. The middle test asserts
  `program.output_slots() == 1` directly, so the pair records what that
  header byte is and is not good for.

Running `make test-substrate-fixture` against this tree passes fifteen:
the thirteen above plus two FRAME-generated checks, a genesis-config
build and a `construct_runtime!` integrity test.

## Calldata Limitations

`submit_program` only accepts `i64` calldata (not models, vectors, or
samples), because `BoundedVec<i64, T::MaxCalldata>` is the extrinsic's
parameter type. A program that needs a richer input must construct it
internally rather than receive it from the caller.
