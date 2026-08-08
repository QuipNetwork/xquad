# Calldata and Outputs

Calldata and output slots provide the interface between the VM and the host
environment. They allow programs to receive input and return results without
direct access to external systems.

## Calldata (Input)

Calldata is a read-only array of `RegVal` values, set before execution begins.
Programs access calldata via the `INPUT` instruction:

```asm
PUSH 0       ; slot index
INPUT r0     ; r0 ← calldata[0]
```

Any `RegVal` variant can be passed as calldata: integers, vectors, models, and
samples. This enables multi-program pipelines where one program's output model
becomes another program's input.

### Setting Calldata (Rust API)

```rust
let mut vm = Vm::new();
vm.set_calldata(vec![
    RegVal::Int(42),
    RegVal::VecInt(vec![1, 2, 3]),
    RegVal::Model(my_model),
]);
```

### Setting Calldata (CLI)

```sh
xq run program.xqb --calldata 10,20,30
```

The CLI `--calldata` flag only supports integer values. For richer types, use
the Rust API.

## Output Slots

Output slots are a writable array of `RegVal` values, initialised to `Int(0)`.
Programs write to output slots via the `OUTPUT` instruction:

```asm
PUSH 0       ; slot index
OUTPUT r0    ; outputs[0] ← r0
```

### Reading Outputs (Rust API)

```rust
let mut vm = Vm::new();
vm.set_output_slots(4);
vm.run(&program)?;

for (i, val) in vm.outputs().iter().enumerate() {
    println!("[{i}] = {val:?}");
}
```

### Reading Outputs (CLI)

`xq run` prints all non-default output slots after execution:

```sh
xq run program.xqb --outputs 4
```

```
outputs:
  [0] = Int(42)
```

## Pipeline Pattern

Calldata and outputs enable multi-program pipelines. A common pattern in the
TSP example:

1. **Encoder** receives `N` and distances as calldata, outputs a QUBO model.
2. **Verifier** receives the model and a sample as calldata, outputs energy and
   validity.
3. **Decoder** receives the sample as calldata, outputs the tour.

Each program runs in its own `Vm` instance. The host (Rust code or pallet)
marshals outputs from one VM into calldata for the next.

## Errors

- **`CallDataIndex`** -- `INPUT` with an index ≥ calldata length.
- **`OutputIndex`** -- `OUTPUT` with an index ≥ output slot count.
