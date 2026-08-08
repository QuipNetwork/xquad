# xq asm

Assemble an XQVM assembly source file into binary bytecode.

## Usage

```sh
xq asm <INPUT> [-o <OUTPUT>] [--stdout]
```

## Arguments

| Argument | Description |
|----------|-------------|
| `INPUT` | Path to the assembly source file (`.xqasm`). |

## Options

| Option | Description |
|--------|-------------|
| `-o, --output <FILE>` | Output file path. Defaults to `<INPUT>.xqb` when omitted. |
| `--stdout` | Write bytecode to stdout instead of a file. Conflicts with `-o`. |

## Examples

```sh
# Assemble to default output (program.xqb)
xq asm program.xqasm

# Assemble to a specific output file
xq asm program.xqasm -o build/program.xqb

# Pipe bytecode to another tool
xq asm program.xqasm --stdout | xq dism
```

## Output

On success, prints a summary to stderr:

```
assembled 12 instructions (28 bytes) -> program.xqb
```

## Error Reporting

Assembly errors include source file location and a highlighted snippet:

```
Error: unknown mnemonic
  ┌─ program.xqasm:3:1
  │
3 │ BADOP r0
  │ ^^^^^ unknown instruction
```
