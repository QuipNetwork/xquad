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

CI still runs it on every pipeline. `test:substrate-fixture` in
[`.gitlab/ci/test.yml`](https://gitlab.com/quip.network/xquad/-/blob/main/.gitlab/ci/test.yml)
carries no `rules:`, `only:`, or `allow_failure:`, and `make
test-substrate-fixture` runs `cargo test --manifest-path
fixtures/pallet-xqvm/Cargo.toml`, exercising six pallet tests plus two
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
) -> DispatchResult
```

| Parameter | Type | Description |
|---|---|---|
| `bytecode` | `BoundedVec<u8, T::MaxProgramSize>` | XQBC-encoded program, produced by `InstructionBuilder::build().encode()` or by assembling `.xqasm` source. |
| `calldata` | `BoundedVec<i64, T::MaxCalldata>` | Integer values injected as `RegVal::Int` into the VM's calldata slots, in order. |

Decode, execute, store, in that order:

1. Require a signed origin (`ensure_signed`); an unsigned call is rejected
   with `BadOrigin` before pallet logic runs.
2. Decode `bytecode` as an `xqvm::Program`. A decode failure returns
   `Error::BytecodeInvalid` and nothing is stored.
3. Read `program.output_slots()` -- the number of `OUTPUT` instructions the
   program itself declares -- and use it as the output-slot count. The
   caller does not choose this; it comes from the bytecode.
4. Build a fresh `Vm`, set the calldata and output-slot count, and run the
   program. Any VM fault returns `Error::ExecutionFailed`; the pallet does
   not distinguish which fault occurred.
5. Collect the `Int` outputs (any other `RegVal` variant in an output slot
   is silently dropped from the result) and bound them to `T::MaxCalldata`.
   Exceeding that bound returns `Error::OutputOverflow`.
6. Store `bytecode` in `StoredProgram`, overwriting any previous value, and
   emit `Event::ProgramExecuted { who, outputs }`.

There is no separate store-then-execute split, no program lookup by hash,
and no explicit `step_limit` parameter -- the VM runs with its own default
step limit. Off-chain, produce the bytecode however you like; the assembler
CLI is `xquad asm`.

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

## What the Fixture's Tests Check

`fixtures/pallet-xqvm/src/tests.rs` covers three happy-path cases (an
arithmetic program with no calldata, a calldata passthrough, and a
two-value sum) and three error paths (invalid bytecode, a stack underflow
that reaches `ExecutionFailed`, and an unsigned origin rejected by
`ensure_signed`). Running `make test-substrate-fixture` against this tree
passes all eight tests: the six above, plus the two FRAME-generated
checks, a genesis-config build and a `construct_runtime!` integrity test.

## Calldata Limitations

`submit_program` only accepts `i64` calldata (not models, vectors, or
samples), because `BoundedVec<i64, T::MaxCalldata>` is the extrinsic's
parameter type. A program that needs a richer input must construct it
internally rather than receive it from the caller.
