# Register I/O

Instructions for moving data between the stack, register file, calldata, and
output slots.

| Code | Mnemonic | Arguments | Stack Effect | Register Effect | Description |
|------|----------|-----------|--------------|-----------------|-------------|
| `0x0A` | `LOAD` | `reg: Register` | \\([\ldots] \to [\ldots, v]\\) | `read` | `reg` must hold \\(\text{Int}(v)\\). Push \\(v\\) onto the stack. Errors if `reg` holds any other variant. |
| `0x0B` | `STOW` | `reg: Register` | \\([\ldots, v] \to [\ldots]\\) | `write` | Pop \\(v\\). Write \\(\text{reg} \leftarrow \text{Int}(v)\\). |
| `0x0C` | `DROP` | `reg: Register` | \\([\ldots] \to [\ldots]\\) | `write` | Write \\(\text{reg} \leftarrow \text{Int}(0)\\), releasing any heap allocation the slot held (models, vectors, samples). |
| `0x0E` | `INPUT` | `reg: Register` | \\([\ldots, s] \to [\ldots]\\) | `write` | Pop \\(s\\) (slot index). Clone \\(\text{calldata}[s]\\) into `reg`. Any `RegVal` variant is transferable. Errors if \\(s\\) is out of range. |
| `0x0F` | `OUTPUT` | `reg: Register` | \\([\ldots, s] \to [\ldots]\\) | `read` | Pop \\(s\\) (slot index). Clone `reg`'s value into \\(\text{outputs}[s]\\). Errors if \\(s\\) is out of range. |

## Stack-Register Bridge

The stack holds only `i64` integers. To move richer types (models, vectors,
samples) into and out of the VM, use `INPUT` and `OUTPUT` with the calldata and
output slot arrays.

`LOAD` and `STOW` bridge the stack and register file for integer values only.
`LOAD` errors if the register does not hold `Int` -- it will not silently coerce
other types.

## Memory Management

`DROP` is the only way to explicitly free a register's allocation. Setting a
register to \\(\text{Int}(0)\\) releases any model, vector, or sample that was previously
stored there. This is important for controlling memory usage in long-running
programs.
