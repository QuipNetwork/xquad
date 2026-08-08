# xq run

Execute XQVM bytecode or assembly source with optional tracing.

## Usage

```sh
xq run [OPTIONS] <FILE>
```

## Arguments

| Argument | Description |
|----------|-------------|
| `FILE` | Bytecode (`.xqb`) or assembly (`.xqasm`) file to run. |

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--text` | -- | Treat `FILE` as assembly source and assemble before running. |
| `--calldata <VALUES>` | -- | Comma-separated `i64` integers passed to `INPUT` instructions. |
| `--outputs <N>` | `16` | Number of output slots available for `OUTPUT` instructions. |
| `--step-limit <N>` | `10000000` | Maximum number of instructions to execute. `0` = unlimited. |
| `--trace` | -- | Enable step-by-step execution tracing. |
| `--trace-format <FMT>` | `text` | Trace output format: `text` or `json`. Requires `--trace`. |
| `--trace-file <PATH>` | stderr | Write trace output to a file. Requires `--trace`. |

## Examples

### Run bytecode

```sh
xq run program.xqb
```

### Run assembly directly

```sh
xq run --text program.xqasm
```

### Pass calldata

```sh
xq run program.xqb --calldata 10,20,30
```

### Enable tracing

```sh
# Text trace to stderr
xq run program.xqb --trace

# JSON trace to a file
xq run program.xqb --trace --trace-format json --trace-file trace.jsonl
```

### Custom limits

```sh
# Unlimited execution
xq run program.xqb --step-limit 0

# Low limit for testing
xq run program.xqb --step-limit 1000
```

## Output

After execution, `xq run` prints:

1. **Outputs** -- all non-default output slots with their index and value.
2. **Stack** -- any values remaining on the stack (bottom to top).

```
outputs:
  [0] = Int(42)
  [1] = VecInt([1, 2, 3])
stack (bottom to top):
  7
```

## Tracing

### Text Format

Human-readable aligned columns showing each step:

- Step number
- Byte offset
- Instruction mnemonic and operands
- Stack state
- Register reads and writes
- Loop depth

### JSON Format (JSONL)

One JSON object per step, suitable for machine processing:

```json
{"step":1,"pos":0,"instruction":"PUSH1","stack":[42],"reads":[],"writes":[],"loop_depth":0}
```

## Error Reporting

Runtime errors include the faulting instruction with a disassembled context
listing:

```
Error: stack underflow
  ┌─ program.xqb:0x0003
  │
  │     PUSH1          42
  │ --> ADD                  ← stack underflow here
  │     HALT
```
