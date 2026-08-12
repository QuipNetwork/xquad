# XQVM Reference

XQVM is the virtual machine at the core of XQuad: a stack-based interpreter
with a 256-slot register file that executes compiled quadratic-optimisation
programs. This section is the reference for that machine -- its execution
model, assembly language, instruction set, binary format, and verifier. For
what a QUBO/Ising model is and why you would want one, start at
[Concepts](../concepts/README.md) instead; this section assumes you already
have a program, or want to write one directly in `.xqasm`, and want to know
exactly what the machine does with it.

## A minimal program

Save this as `add.xqasm`, then assemble and run it:

```asm
; push two integers and add them
PUSH 10
PUSH 32
ADD
HALT
```

```sh
$ xquad asm add.xqasm -o add.xqb
assembled 4 instructions (21 bytes) -> add.xqb
$ xquad run add.xqb
stack (bottom to top):
  42
```

The program pushes 10 and 32 onto the value stack, adds them, and halts. The
result remains on the stack and is printed by `xquad run`. `xquad run --text
add.xqasm` skips the separate assembly step and runs the source directly.

## What this section covers

- **[VM Architecture](machine-model.md)** -- the value stack, the 256-slot
  register file, and the loop stack.
- **[Loop Stack](loops.md)** -- `RANGE`/`ITER`/`NEXT` iteration.
- **[Calldata and Outputs](io.md)** -- getting values into a program with
  `INPUT` and out with `OUTPUT`.
- **[Execution](execution.md)** -- the fetch-decode-execute cycle and
  execution tracing.
- **[Assembly](assembly.md)** -- the `.xqasm` text syntax.
- **[Assembly Examples](assembly-examples.md)** -- worked `.xqasm` programs.
- **[Instructions](instructions/README.md)** -- all 93 instructions, by
  category.
- **[Opcode Reference](opcodes.md)** -- the generated opcode table.
- **[Bytecode Format](bytecode-format.md)** -- the `.xqb` wire format.
- **[Verifier](verifier.md)** -- what the pre-execution verifier checks.
- **[CLI](cli/README.md)** -- the `xquad` command-line tool.
- **[Limits and Errors](limits-and-errors.md)** -- fixed and configurable
  limits, and every runtime and verifier error.
