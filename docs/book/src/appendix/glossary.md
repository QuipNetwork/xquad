# Glossary

Terms as this book uses them, alphabetically. Each entry links to the page
that treats it in full.

**Backend.** Where a model actually solves: locally on a CPU or GPU, on
D-Wave's cloud QPU, or on the Quip network. Choosing one does not change
the model, the encoder, or the verifier. See
[Backends](../concepts/backends.md).

**Calldata.** The read-only array of values a host program supplies to a
VM run before execution starts. `INPUT` reads from it by slot index. See
[Calldata and Outputs](../xqvm/io.md).

**Chain strength.** A D-Wave QPU parameter. It sets how strongly a group of
physical qubits standing in for one logical variable are coupled together,
so they read out as one value rather than breaking apart under the
problem's own couplings. See [D-Wave QPU](../solving/dwave-qpu.md).

**Conformance vector.** A fixed test case -- program, calldata, and
expected output -- that the conformance harness runs on both the Rust and
Python VMs and checks for agreement. See [Conformance](../embedding/conformance.md).

**Decoder.** One of the three programs a problem compiles to: takes a
sample and extracts the answer in the problem's own terms (a tour, a
partition, a set of selected items). See
[Three Programs](../concepts/three-programs.md).

**Domain.** The set of values a model's variables take: binary (\\(\{0,
1\}\\)), spin (\\(\{-1, 1\}\\)), or discrete (\\(\{-k, \ldots, k-1\}\\)). A
model and the sample solving it must share a domain. See [Quadratic
Models](../concepts/quadratic-models.md#three-domains).

**Embedding (D-Wave).** Mapping a model's logical variables onto a QPU's
physical qubit graph, called *minor embedding*: each logical variable
becomes a chain of one or more physical qubits held together by chain
strength (above). `SolverDWaveQPU` builds this automatically. See [D-Wave
QPU](../solving/dwave-qpu.md#what-embedding-means).

**Embedding (Rust).** Using the `xqvm` and `xqasm` crates directly from a
Rust program, without the Python packages. See [Embedding
Overview](../embedding/README.md).

**Encoder.** One of the three programs a problem compiles to: reads
runtime inputs from calldata and builds the model. See
[Three Programs](../concepts/three-programs.md).

**Energy.** The single number a model computes for a candidate assignment
-- the Hamiltonian evaluated at that point. A solver searches for the
assignment that minimises it; `ENERGY` computes it directly so a program
can check a solver's answer instead of trusting it. See [Quadratic
Models](../concepts/quadratic-models.md#energy).

**Hamiltonian.** The function a quadratic model represents: a sum of
linear and quadratic terms over the variables, borrowed from the physics
term for a system's total energy. See [Quadratic
Models](../concepts/quadratic-models.md).

**Ising model.** The spin domain: variables take values \\(-1\\) or
\\(+1\\), the domain quantum annealing hardware minimises natively. See
[Quadratic Models](../concepts/quadratic-models.md#three-domains).

**Model.** An `XqmxModel`: two sparse coefficient maps, `linear` and
`quadratic`, that together define a Hamiltonian over a fixed number of
variables. Built with `BQMX`/`SQMX`/`XQMX` and the coefficient-access
opcodes. See [Quadratic Models](../concepts/quadratic-models.md) and
[VM Architecture](../xqvm/machine-model.md).

**Output slot.** The writable array a VM run populates via `OUTPUT`,
read back by the host program after the run halts. See [Calldata and
Outputs](../xqvm/io.md).

**Penalty.** A term added to a model's Hamiltonian that is zero when a
constraint holds and positive when it does not. Minimising the combined
Hamiltonian then tends to satisfy the constraint too. Sizing the penalty
weight correctly is the real engineering problem in constraint-by-penalty
modelling. See [Quadratic
Models](../concepts/quadratic-models.md#penalties-folding-a-constraint-into-the-objective)
and [Constraints](../modelling/constraints.md#choosing-a-penalty-weight).

**QUBO.** Quadratic Unconstrained Binary Optimisation: the binary domain,
where each variable is either selected or not. See [Quadratic
Models](../concepts/quadratic-models.md#three-domains).

**Register.** One of 256 typed slots (`r0`-`r255`) holding a `RegVal`:
an integer, a vector, a model, or a sample. See
[VM Architecture](../xqvm/machine-model.md#register-file).

**Sample.** An `XqmxSample`: one candidate assignment, a single value per
variable, produced by a solver or built by hand. Shares a domain and
variable count with the model it answers, but not its shape. See
[Quadratic Models](../concepts/quadratic-models.md).

**Solver.** Anything implementing the `xqsa` interface: takes a model,
returns a sample and its reported energy. `xqsa` ships five: a CPU
annealer, CUDA and Metal GPU annealers, the D-Wave QPU, and the Quip
network. See [Solving Overview](../solving/README.md).

**Step limit.** The maximum number of instructions a VM run will execute
before faulting with `StepLimitExceeded`, guarding against runaway
programs. Configurable per run; defaults to 10,000,000. See [Limits and
Errors](../xqvm/limits-and-errors.md).

**TARGET.** The opcode marking a valid jump destination. Every label a
program jumps to must have a `TARGET` at that position. The VM scans for
them once at load time, rather than reading a precomputed mapping out of
the wire format, and resolves jumps against that scan. See
[Execution](../xqvm/execution.md) and [Builder
API](../embedding/builder-api.md#labels-and-the-jumptable).

**Verifier.** Two related but distinct things in this book. The **bytecode
verifier** is static analysis (`xqvm::verifier`) that checks a program's
structure before it runs: every jump lands on a `TARGET`, and every
register is read only after it is written. See
[Verifier](../xqvm/verifier.md). The **verifier program** is one of the
three programs a problem compiles to: it checks a sample against the
encoder's constraints and recomputes its energy independently. See
[Three Programs](../concepts/three-programs.md).

**XQBC.** The binary wire format: a 15-byte header (magic, version, slot
counts, code length, checksum) followed by the raw instruction stream. See
[Bytecode Format](../xqvm/bytecode-format.md).

**XQCP.** X-Quadratic Constraint Programming: the Python DSL that turns a
problem description into the three XQVM programs. See [Modelling
Lifecycle](../modelling/README.md).

**XQMX.** The matrix type the VM allocates and manipulates, in either of
two modes: a model (`XqmxModel`, a Hamiltonian's coefficients) or a
sample (`XqmxSample`, one candidate assignment). See [VM
Architecture](../xqvm/machine-model.md).

**XQSA.** X-Quadratic Solver Adapters: the Python package holding one
adapter per solving backend. See [Solving Overview](../solving/README.md).

**XQVM.** X-Quadratic Virtual Machine: the stack machine every XQuad
problem compiles to, and the layer both the Rust and Python
implementations implement. See [XQVM Reference](../xqvm/README.md).
