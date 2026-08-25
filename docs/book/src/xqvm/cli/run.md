# xquad run

Execute XQVM bytecode or assembly source, printing the residual outputs and
stack, with optional step-by-step tracing.

## Usage

```sh
xquad run [OPTIONS] <FILE>
```

## Arguments

| Argument | Description |
|----------|-------------|
| `FILE` | Bytecode (`.xqb`) file to run, or assembly (`.xqasm`) source when `--text` is set. |

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--text` | off | Treat `FILE` as assembly source and assemble it before running. |
| `--calldata <CALLDATA>` | none | Comma-separated `i64` integers passed to `INPUT` instructions. |
| `--outputs <OUTPUTS>` | `16` | Number of output slots available for `OUTPUT` instructions. |
| `--step-limit <STEP_LIMIT>` | `10000000` | Maximum number of instructions to execute. The limit is exact: `0` executes nothing. Conflicts with `--unlimited-steps`. See [Step limits](../../running/#step-limits). |
| `--unlimited-steps` | off | Run without a step limit. Conflicts with `--step-limit`. A program that never halts will not return. |
| `--memory-limit <MEMORY_LIMIT>` | `1073741824` | Allocation budget in bytes for models, samples and vectors. See [The allocation budget](../limits-and-errors.md#the-allocation-budget). |
| `--trace` | off | Enable step-by-step execution tracing. |
| `--trace-format <TRACE_FORMAT>` | `text` | Trace output format: `text` or `json`. Requires `--trace`. |
| `--trace-file <TRACE_FILE>` | stderr | Write trace output to a file instead of stderr. Requires `--trace`. |

`xquad run` does not call the verifier. A program that fails verification
can still be handed to `run`; some defects (an unset register read, for
example) are also caught by the VM's own runtime checks and abort with a
runtime error, but others are not. See [xquad verify](verify.md) and
[Verification](../../running/verification.md) for what static verification
does and does not guarantee.

## Examples

### Run bytecode

```sh
xquad run add.xqb
```

```
stack (bottom to top):
  42
```

### Run assembly directly

```sh
xquad run --text add.xqasm
```

Skips the separate `xquad asm` step; the source is assembled in memory and
run immediately.

### Pass calldata and read outputs

`INPUT` and `OUTPUT` each take one register operand in assembly, but both
pop the slot index off the value stack at runtime -- the index is not
implicit in the instruction. A program that reads `calldata[0]` into
`r0`, adds one, and writes the result to output slot 0 needs the index
pushed explicitly before each call: `PUSH 0; INPUT r0; ...; PUSH 0;
OUTPUT r1; HALT`.

```sh
xquad run io.xqb --calldata 41
```

```
outputs:
  [0] = Int(42)
```

`--calldata` accepts a comma-separated list; each value is consumed in
order by successive `INPUT` instructions. `OUTPUT` faults `UnsetRegister`
if the register it reads was never written, the same as `LOAD`. Both
`INPUT` and `OUTPUT` fault with `CallDataIndex`/`OutputIndex`
respectively if the popped index is out of range for the calldata or
output-slot array -- the index itself is not guaranteed to be valid just
because it came off the stack. See [Register I/O](../instructions/register-io.md)
for the full set of `INPUT`/`OUTPUT` error modes.

### Enable tracing

```sh
xquad run regs.xqb --trace
```

Trace lines go to stderr:

```
step    offset    instruction            stack                      read-regs        written-regs   
     1  0x0000    PUSH1 7                [7]                                                        
     2  0x0002    STOW r0                []                                          r0=7           
     3  0x0004    LOAD r0                [7]                        r0=7                            
     4  0x0006    HALT                   [7]                                                        
```

and the residual stack goes to stdout, separately from the trace:

```
stack (bottom to top):
  7
```

Trace lines go to stderr by default so the stdout result is not
interleaved with them in a terminal. Redirect stderr separately to
capture the trace on its own:

```sh
xquad run regs.xqb --trace 2>trace.txt
```

### JSON trace

```sh
xquad run regs.xqb --trace --trace-format json --trace-file trace.jsonl
```

`trace.jsonl` then holds one JSON object per step:

```json
{"step":1,"pos":0,"instruction":"PUSH1 7","stack":[7],"read_regs":{},"written_regs":{}}
{"step":2,"pos":2,"instruction":"STOW r0","stack":[],"read_regs":{},"written_regs":{"0":{"type":"int","value":7}}}
{"step":3,"pos":4,"instruction":"LOAD r0","stack":[7],"read_regs":{"0":{"type":"int","value":7}},"written_regs":{}}
{"step":4,"pos":6,"instruction":"HALT","stack":[7],"read_regs":{},"written_regs":{}}
```

### Custom step limits

`countloop.xqb` assembles a five-iteration `RANGE` loop around a `NOP`:
six encoded instructions, 14 executed steps, because the body and its
`NEXT` run once per iteration. A limit that is reached before `HALT`
aborts the run:

```sh
xquad run countloop.xqb --step-limit 3
```

```
Error: xqvm::runtime_error

  × step limit of 3 exceeded
```

The limit is exact, and `0` is not a sentinel for "unlimited". A zero
budget permits no instructions at all, so the first fetch fails:

```sh
xquad run countloop.xqb --step-limit 0
```

```
Error: xqvm::runtime_error

  × step limit of 0 exceeded
```

Passing `--unlimited-steps` alongside `--step-limit` is rejected by the
argument parser rather than resolved by precedence, so opting out of the
bound cannot be said two ways at once:

```sh
xquad run countloop.xqb --step-limit 0 --unlimited-steps
```

```
error: the argument '--step-limit <STEP_LIMIT>' cannot be used with '--unlimited-steps'
```

The conflict fires on an explicit `--step-limit`, not on the default, so
`--unlimited-steps` on its own is accepted. It removes the bound
entirely, which is only safe where you control the program or can
abandon the thread -- a program that never halts will not return:

```sh
xquad run countloop.xqb --unlimited-steps
```

prints nothing, since the program leaves no outputs and no residual
stack. To raise the limit rather than remove it, pass a larger number,
up to `u64::MAX`:

```sh
xquad run countloop.xqb --step-limit 18446744073709551615
```

also runs to completion, but for a different reason: the limit is higher,
not absent. A budget that exactly covers the program succeeds --
`--step-limit 14` runs `countloop.xqb` to completion, and `--step-limit
13` does not.

## Output

After execution, `xquad run` prints, in order:

1. **Outputs** -- every output slot that was written, with its index and
   value.
2. **Stack** -- any values remaining on the value stack, bottom to top.

A run that both writes outputs and leaves values on the stack prints both
sections:

```
outputs:
  [0] = Int(42)
  [1] = VecInt([1, 2, 3])
stack (bottom to top):
  7
```

## Error reporting

Runtime errors print the faulting instruction with a disassembled context
listing around it. `underflow.xqb` is `PUSH 1 / ADD / HALT`: `ADD` needs
two stack values and only one was pushed.

```sh
xquad run underflow.xqb
```

```
Error: xqvm::runtime_error

  × stack underflow at byte 0x0002
   ╭─[underflow.xqb:2:1]
 1 │   0x0000:  PUSH1   1
 2 │   0x0002:  ADD     
   · ─────────┬─────────
   ·          ╰── execution failed here
 3 │   0x0003:  HALT    
   ╰────
```
