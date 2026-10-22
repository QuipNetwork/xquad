# xquad vm asm

Assemble an `.xqasm` source file into binary XQVM bytecode.

## Usage

```sh
xquad vm asm <INPUT> [-o <OUTPUT>] [--stdout]
```

## Arguments

| Argument | Description |
|----------|-------------|
| `INPUT` | Path to the assembly source file. |

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `-o, --output <OUTPUT>` | `<INPUT>` with its extension replaced by `.xqb` | Output file path. |
| `--stdout` | off | Write bytecode to stdout instead of a file. Conflicts with `-o`/`--output`. |

`-o` and `--stdout` are mutually exclusive; passing both is a usage error
(exit `2`), not a runtime one:

```sh
xquad vm asm add.xqasm -o build/program.xqb --stdout
```

```
Usage: xquad vm asm [OPTIONS] INPUT
Try 'xquad vm asm --help' for help.

Error: --output cannot be used with --stdout
```

## Examples

### Assemble to the default output

```sh
xquad vm asm add.xqasm
```

Writes `add.xqb` next to the source and prints a summary to stderr:

```
assembled 4 instructions (21 bytes) -> add.xqb
```

### Assemble to a specific output file

```sh
mkdir -p build
xquad vm asm add.xqasm -o build/program.xqb
```

### Assemble does not run the verifier

`xquad vm asm` only assembles; it does not check the result with
[`xquad vm check`](check.md). A source file with no mnemonic errors can
still fail verification once assembled.

### Pipe bytecode to another tool

```sh
xquad vm asm add.xqasm --stdout | xquad vm dis
```

```
  0x0000:  PUSH1   10
  0x0002:  PUSH1   32
  0x0004:  ADD     
  0x0005:  HALT    
```

## Output

On success, prints a summary to stderr (suppressed with `--stdout`, since
the bytecode itself occupies stdout). The instruction count includes every
`TARGET` the assembler emits, so a program with jumps counts higher than
its visible mnemonics. `branch.xqasm` below has eight mnemonic lines and
two labels (`.0`, `.1`), each of which emits a `TARGET`:

```asm
PUSH 5
PUSH 10
GT
JUMPI .0
PUSH 99
JUMP .1
.0: PUSH 0
.1: HALT
```

```sh
xquad vm asm branch.xqasm -o branch.xqb
```

```
assembled 10 instructions (31 bytes) -> branch.xqb
```

## Error reporting

An assembly error is one line on stderr, and the command exits `1`:

```
Error: assembly failed: unknown mnemonic `BADOP`
```
