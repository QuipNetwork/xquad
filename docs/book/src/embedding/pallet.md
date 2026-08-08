# Pallet Integration

XQVM can run on-chain as a Substrate pallet. The pallet provides two
extrinsics: one to store programs and one to execute them. This chapter
describes the pallet's interface, configuration, and weight model.

## Configuration

The pallet is configured via the `Config` trait:

```rust
#[pallet::config]
pub trait Config: frame_system::Config {
    type RuntimeEvent: From<Event<Self>>
        + IsType<<Self as frame_system::Config>::RuntimeEvent>;

    /// Maximum size of a stored XQVM program in bytes.
    type MaxProgramSize: Get<u32>;

    /// Maximum number of calldata entries (i64 values).
    type MaxCallDataLen: Get<u32>;

    /// Maximum number of output slots.
    type MaxOutputSlots: Get<u32>;

    /// Maximum step limit per execution.
    type MaxStepLimit: Get<u64>;

    /// Weight charged per XQVM execution step (ref_time component).
    type WeightPerStep: Get<Weight>;

    type WeightInfo: WeightInfo;
}
```

### Configuration Constants

| Constant | Purpose | Example Value |
|----------|---------|---------------|
| `MaxProgramSize` | Upper bound on bytecode size in bytes. | 65,536 |
| `MaxCallDataLen` | Maximum calldata entries for `execute`. | 32 |
| `MaxOutputSlots` | Maximum output slots for `execute`. | 32 |
| `MaxStepLimit` | Cap on the `step_limit` parameter. | 100,000 |
| `WeightPerStep` | Weight charged per VM instruction. | 1,000 ref_time |

## Storage

| Item | Key | Value | Description |
|------|-----|-------|-------------|
| `Programs` | `T::Hash` (Blake2-256) | `BoundedVec<u8, T::MaxProgramSize>` | Stored bytecode, keyed by hash. |
| `ProgramOwner` | `T::Hash` | `T::AccountId` | Account that stored each program. |

Programs are keyed by their Blake2-256 hash for deduplication. Storing the same
bytecode twice is rejected with `ProgramAlreadyExists`.

## Extrinsics

### `store_program` (call index 0)

Store an XQVM program on-chain.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `bytecode` | `BoundedVec<u8, T::MaxProgramSize>` | Encoded XQVM bytecode. |

**Behaviour:**

1. Validate the bytecode by decoding it as a `Program`.
2. Compute the Blake2-256 hash.
3. Check for duplicates.
4. Store the bytecode and record the owner.
5. Emit `ProgramStored`.

**Errors:** `InvalidBytecode`, `ProgramAlreadyExists`.

### `execute` (call index 1)

Execute a stored XQVM program.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `program_hash` | `T::Hash` | Blake2-256 hash of the stored program. |
| `calldata` | `BoundedVec<i64, T::MaxCallDataLen>` | Integer calldata values. |
| `output_slots` | `u32` | Number of output slots to allocate. |
| `step_limit` | `u64` | Maximum instructions to execute. |

**Behaviour:**

1. Validate `step_limit ≤ MaxStepLimit` and `output_slots ≤ MaxOutputSlots`.
2. Look up the program by hash.
3. Create a VM, configure calldata, outputs, and step limit.
4. Execute the program.
5. Collect integer outputs.
6. Emit `ProgramExecuted` with actual steps used and outputs.
7. Refund unused weight.

**Weight model:**

Weight is pre-charged based on `step_limit`:

```
pre_charged = execute_base + WeightPerStep * step_limit
```

After execution, actual weight is calculated from the real step count:

```
actual = execute_base + WeightPerStep * steps_used
```

The difference is refunded via `PostDispatchInfo`. This means users pay only
for the instructions actually executed, not the worst-case limit.

**Errors:** `ProgramNotFound`, `StepLimitTooHigh`, `TooManyOutputSlots`, and
any VM runtime error (mapped from `aglais_xqvm_vm::Error`).

## Events

| Event | Fields | Description |
|-------|--------|-------------|
| `ProgramStored` | `program_hash`, `owner`, `size` | Emitted when bytecode is stored. |
| `ProgramExecuted` | `caller`, `program_hash`, `steps_used`, `outputs` | Emitted after successful execution. |

## Error Mapping

VM runtime errors are mapped to pallet errors:

| VM Error | Pallet Error |
|----------|-------------|
| `StackUnderflow` | `VmStackUnderflow` |
| `StackOverflow` | `VmStackOverflow` |
| `DivisionByZero` | `VmDivisionByZero` |
| `StepLimitExceeded` | `VmStepLimitExceeded` |
| `BadOpcode`, `TruncatedInstruction` | `VmBadBytecode` |
| `RegisterType` | `VmRegisterType` |
| All other errors | `VmRuntimeError` |

## Calldata Limitations

The pallet's `execute` extrinsic only supports `i64` calldata values (not
models, vectors, or samples). For richer input types, programs must construct
them internally or receive them through a different mechanism.

## Workflow

A typical on-chain workflow:

1. **Off-chain:** Assemble the program with `xq asm`.
2. **On-chain:** Call `store_program` with the bytecode.
3. **On-chain:** Call `execute` with calldata and desired output slots.
4. **Off-chain:** Read the `ProgramExecuted` event to get outputs.
