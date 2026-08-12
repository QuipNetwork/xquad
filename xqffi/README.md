# xqffi -- PyO3 FFI bindings for the XQuad Rust runtime

The Python extension that exposes the Rust [`xqvm`](https://gitlab.com/quip.network/xquad/-/tree/main/xqvm) interpreter and [`xqasm`](https://gitlab.com/quip.network/xquad/-/tree/main/xqasm) assembler. Ships as abi3 manylinux wheels (x86_64 + aarch64, CPython >= 3.13) plus a universal sdist for other platforms, all built by maturin.

`xqffi` is a **pure FFI shim** -- it only re-exposes the Rust types and entry points. User-facing conveniences (`Program` / `Session` / `RunResult`, keyword calldata, trace inspection) live in the [`xquad`](https://gitlab.com/quip.network/xquad/-/tree/main/xquad) umbrella package.

## Install

```sh
pip install xqffi
```

## Surface

### `xqffi.vm` -- low-level one-shot

What the conformance harness drives. Small wrapper over `xqvm::Vm`:

```python
from xqffi.vm import Vm
from xqffi.asm import assemble_source

vm = Vm()
vm.set_calldata([40, 2])
vm.set_output_slots(1)
vm.run(assemble_source(src))
assert vm.outputs() == [42]
```

`XqmxModel` and `XqmxSample` are thin getter/setter wrappers around the Rust types for direct model/sample manipulation.

### `xqffi.asm` -- assembly

`parse_xqasm(str)` -> wire dict; `assemble_source(str)` -> raw bytes; `disassemble(bytes)` -> human-readable listing; `instruction_count(bytes)` -> instruction count.

### `xqffi.verifier` -- static verification

`verify(bytes)` and `verify_source(str)` run the pre-execution verifier: every jump lands on a `TARGET`, every register is read after it is written, and stack depth balances. Both return `None` on success and raise `ValueError` on a rejected program. See [Verifier](https://gitlab.com/quip.network/xquad/-/blob/main/docs/book/src/xqvm/verifier.md).

## Interactive / multi-run API

Use [`xquad.program`](https://gitlab.com/quip.network/xquad/-/tree/main/xquad) for the REPL / notebook / script workflow:

```python
from xquad.program import Program

program = Program.from_source(src)
session = program.session(output_slots=1)
session.set_calldata([40, 2])
result = session.run()
assert dict(result.outputs) == {0: 42}
```

`Program.load(bytes)` takes wire-format bytecode; `Session.run()` returns a `RunResult` with dict-keyed outputs (unset slots present as `None`), residual stack, and step count.

## Also see

- [`xqvm_py`](https://gitlab.com/quip.network/xquad/-/tree/main/xqvm_py) -- pure-Python reference VM (conformance oracle).
- [`xqcp`](https://gitlab.com/quip.network/xquad/-/tree/main/xqcp) -- constraint programming DSL that compiles to `.xqasm`.
- [`xqsa`](https://gitlab.com/quip.network/xquad/-/tree/main/xqsa) -- solver adapters: CPU/GPU annealers, D-Wave QPU, Quip network.
- [`xquad`](https://gitlab.com/quip.network/xquad/-/tree/main/xquad) -- umbrella meta-package with the interactive API.
- [Running Programs](https://gitlab.com/quip.network/xquad/-/blob/main/docs/book/src/running/README.md) -- full tour.

## License

AGPL-3.0-or-later.
