# Solver Registry

## Naming Convention

Concrete solver classes follow the pattern `Solver{Vendor/Technology}{ComputeTarget}`. Vendor-first grouping keeps related solvers together in sorted order.

v0.3.0 uses `Solver` (abstract) with `SolverDWaveCPU`, `SolverDWaveQPU`, `SolverCudaGPU`, `SolverMetalGPU`, and `SolverQuip` (concrete).

## Algorithm Families

### D-Wave SDK Family

Solvers that use the `dimod` BQM format and D-Wave SDK packages. The SDK handles model conversion and sampling; solvers differ in where the BQM is solved (CPU, QPU).

Shared base helpers: `_model_to_bqm()`, `_sample_to_xqmx()` (concrete methods on the `Solver` base class).

### Custom Kernel Family

Custom GPU kernels (CUDA `.cu` / Metal `.metal`) bypassing the D-Wave SDK for the compute step. Higher performance ceiling, more code to maintain.

Algorithm selection is a configuration option on the solver class via a `strategy` parameter (e.g. `"sa"`, `"gibbs"`, `"metropolis"`), not separate solver classes. This matches the convention from the quip-protocol codebase.

`SolverCudaGPU` is the first solver in this family, using CuPy RawKernel for custom CUDA C++ kernels. It converts XQMX models directly to dense GPU arrays (skipping the BQM intermediate) and runs parallel-replica simulated annealing with Metropolis acceptance.

`SolverMetalGPU` is the Apple Silicon counterpart, using inline Metal Shading Language kernels dispatched via `pyobjc-framework-Metal`. It supports `strategy="sa"` (simulated annealing) and `strategy="gibbs"` (block Gibbs sampling over a greedy graph colouring). Like the CUDA solver it works on dense float32 arrays; unlike it, acceptance randomness is generated on-device (xorshift32 per replica) so no random buffer is allocated.

### Gate-Based QAOA Family

Variational quantum circuits via Qiskit. Non-D-Wave; entirely different algorithmic approach (parameterised circuits + classical optimizer). IBM and IonQ are separate solvers because they use different Qiskit provider packages.

### Network Mempool Family

Solvers that do not run the optimization locally at all: they submit the model to a decentralized compute market and read the result back. `SolverQuip` is the first member. It proposes the model as a job to the Quip Network `QuantumComputeMempool` pallet, a miner solves it (on its own hardware, with whatever algorithm it runs), and the adapter decodes the returned solution as an XQMX sample. The algorithm is the miner's; this family owns the protocol plumbing.

`SolverQuip` (`quip.py`) is a synchronous `Solver`. Notable characteristics:

- **Subgraph placement.** The model is placed onto the miner's fixed hardware topology (advantage2_system1-class): an injective variable -> node mapping where every coupling lands on a real hardware edge. Placement is deterministic, so `query()` can re-derive it from the model without persisting it. Minor-embedding with chains is out of scope; a model that is not a subgraph of the topology raises `PlacementError`. The target topology is resolved at construction from `topology=`, then `QUIP_TOPOLOGY`, then the chain's `QuantumPow.DefaultTopology`. All three are deployment-local: the same advantage2 graph hashes differently across deployments because the hash folds in each deployment's allowed-value specs, so nothing is pinned in the codebase and an unresolvable topology raises rather than falling through to a hash no chain would accept. `QUIP_TOPOLOGY` targets a registered non-default topology without threading a constructor argument through, which is what makes that path testable against a live chain. `solve()` does not consult `QuantumPow.MineableTopologies`: that is the chain's active mining set, gating `submit_proof` and so block production, and it does not decide what the compute mempool admits or answers. An order carries its nodes, edges and coefficients inline and no topology hash at all, so the chain cannot perceive which topology an order was built against, and an order proposed against a registered-but-non-mineable topology is admitted on chain and answered by the fleet normally. See "Topology policy is not chain-enforced" below. The network currently exposes a single default topology; multi-topology discovery and success-ranking (auto-selecting among several registered topologies per job) is deferred to future protocol-side work, out of scope until a second topology or hardware type registers.
- **Topology policy is not chain-enforced.** Three topology sets matter and only two exist. `QuantumPow.RegisteredTopologies` publishes a topology; registering obligates nobody, so registered means proposed, not supported. `QuantumPow.MineableTopologies` is the active mining set and gates `submit_proof` alone; it is near-singleton by design, because the difficulty decay anchor is global and a second non-default entry would corrupt it. A third set -- topologies a miner is required to be able to solve -- is deliberately not implemented; `register_topology` is `ensure_root`, so the admin gate substitutes for now. The mempool binds an order to no topology at all: `propose_job` and `submit_solution` read neither storage item and both rebuild their index from the order's own inline arrays, and the job that reaches a miner carries edges and coefficients with no topology id to filter on. That is the design rather than an omission. The network is intended to be mostly permissionless, so chain-enforced topology policy as a class -- a required-to-solve set, banned sets, an admission gate at `propose_job` -- is out of scope on principle rather than merely unbuilt, and filtering belongs in miner software as local policy. Even that waits on the job carrying a topology id, which today it does not. The consequence for this adapter: *mineable* is the consensus predicate, not the mempool's, so `SolverQuip` does not consult it. An earlier revision checked it before reserving the reward and raised `QuipTopologyError`. That check is deleted rather than relaxed to a warning: the condition cannot affect whether an order is admitted or answered, so warning about it would report something that changes no outcome, which is how warnings become noise. `QuipTopologyError` is retained as an exported name and is no longer raised.
- **Milli-scale `i32` encoding.** XQMX natural-scale coefficients are scaled by 1000 into the chain's `i32` `h`/`j` arrays. See "Coefficient encoding and allowed values" below.
- **Hybrid signing.** The chain's `Signature` is `HybridTxSignature` (sr25519 + FN-DSA-512), which `substrate-interface` cannot produce. All crypto is delegated to the `quip_signer` extension; `xqsa.quip_signing` owns only the extrinsic-assembly layer. The account is a 32-byte master seed (env `QUIP_SIGNER_SEED`) or a keystore file (env `QUIP_KEYSTORE`), funded on localdev via the faucet. Do not derive dev accounts from `//Alice`-style URIs -- the hybrid chain yields a different account.
- **Block-height finality (lazy lifecycle).** No on-chain hook closes orders. Status flips `Opened -> Expired` only when an extrinsic touches the order, and `-> Closed` only on settlement, so the adapter never waits for `OrderClosed`. It computes finality itself from block height: `effective_expiry = min(created_at + deadline_blocks, first_solution_at + block_wait)`, final once the head reaches it. `solve()` polls to finality; `query(order_id, model)` returns `None` until then; `status(order_id)` is a one-read snapshot.
- **Auto-reclaim.** `propose_job` reserves the full reward. If an order finalizes with no solutions, the adapter best-effort submits `reclaim_order` (unreserving the reward) before raising `QuipJobFailedError`; reclaim failure is logged, not raised.
- **Native signing extension.** `quip_signer` (hybrid sr25519 + FN-DSA-512) installs via the `[quip]` extra alongside `substrate-interface`. Linux resolves a manylinux wheel; macOS and Windows build it from the sdist with a local Rust toolchain, matching xquad's own native-package policy. `import xqsa` and every other solver stay healthy without the extra; only constructing `SolverQuip` triggers the guards.
- **Pre-release volatility.** Quip's public network (aglais) and the local DevNet are both pre-release: pallet metadata, the signed-extension layout, the hybrid signature suite, and economic parameters can all change between releases. Re-validate after an upgrade rather than assuming a recorded coordinate still holds.

Configuration (constructor arg, then environment): `url`/`QUIP_RPC_URL`, `seed`/`QUIP_SIGNER_SEED`, `keystore`/`QUIP_KEYSTORE`, `reward`/`QUIP_REWARD` (else the chain `MinReward`), `topology`/`QUIP_TOPOLOGY` (else the chain `DefaultTopology`), `faucet`/`QUIP_FAUCET_URL` (base URL `autofund` draws from), `autoconfirm`/`QUIP_AUTOCONFIRM` (submit without asking, ask on the terminal, or a callable judging the `JobQuote`; default submits), `autofund`/`QUIP_AUTOFUND` (same three forms, decides whether a short account is topped up from `faucet` before proposing). The `spec_id` is a constructor-only override, defaulting to the chain's `DefaultIsingSpecId`. The `mode`, `resolution`, and `delivery` constructor args are pass-through job parameters defaulting to `Open` / `SingleBest` / `OnChainOnly` (bare variants only); the pallet's data-carrying variants -- `Callback` delivery (push `ResultReady` to a consumer-hosted endpoint) and `Bid` mode (restrict solvers by account or miner type) -- are intentionally unexercised in v1, as they mainly serve non-xquad job types. They will be drafted out of this note into a scoped ticket if and when an xquad consumer needs them. `solve()` quotes the job (the reward plus the chain-reported fee, weighed against the account balance) and passes its consent gates before proposing it: `autoconfirm` first, then `autofund` only when the account is short. `SolverQuip.for_network(name, **kwargs)` builds a solver from a named preset (`aglais`, `devnet`) in `xqsa.quip_networks`, passing the preset RPC as `url`.

## Dependency Model

Each solver beyond the base `SolverDWaveCPU` lives behind an optional extra. Import guards are lazy (inside `__init__`, not at module level) so `import xqsa` never fails.

- `ImportError` with a `pip install xqsa[extra]` hint when the required extra is not installed
- `RuntimeError` when required hardware is not detected (GPU, QPU)

## Solver Registry

| Solver | File | Extra | Hardware | Status |
|--------|------|-------|----------|--------|
| `SolverDWaveCPU` | `dwave_cpu.py` | (base) | CPU | implemented |
| `SolverDWaveQPU` | `dwave_qpu.py` | `[dwave]` | D-Wave QPU (cloud) | implemented |
| `SolverCudaGPU` | `cuda_gpu.py` | `[cuda]` | NVIDIA CUDA GPU | implemented |
| `SolverMetalGPU` | `metal_gpu.py` | `[metal]` | Apple Metal GPU | implemented |
| `SolverQuip` | `quip.py` | `[quip]` | Quip Network mempool (miner-solved) | implemented |
| `SolverIBMQAOA` | -- | `[ibm]` | IBM QPU / AerSimulator | planned |
| `SolverIonQQAOA` | -- | `[ionq]` | IonQ trapped-ion QPU | planned |

## Selecting by Name

`xqsa.build_solver(name, *, seed=None)` constructs a solver from a short,
CLI-friendly name, decoupling callers (example runners, the smoke harness,
`--solver` flags) from concrete class names. `xqsa.SOLVERS` is the
authoritative name->class map and `xqsa.DEFAULT_SOLVER` is the baseline.
`seed` is forwarded only to the classical SA backends; the QPU is physical
hardware and `quip` is configured from the environment, so both take no seed.

| CLI name | Class |
|----------|-------|
| `dwave-cpu` (default) | `SolverDWaveCPU` |
| `dwave-qpu` | `SolverDWaveQPU` |
| `cuda-gpu` | `SolverCudaGPU` |
| `metal-gpu` | `SolverMetalGPU` |
| `quip` | `SolverQuip` (env-configured; no seed; needs `[quip]`) |

## Protocol Miner Equivalence

Mapping between `quip-protocol` miner config keys and xqsa solver classes:

| Protocol miner | xqsa solver |
|----------------|-------------|
| `cpu` | `SolverDWaveCPU` |
| `qpu` | `SolverDWaveQPU` |
| `cuda` | `SolverCudaGPU(strategy="sa")` |
| `cuda-gibbs` | `SolverCudaGPU(strategy="gibbs")` |
| `metal` | `SolverMetalGPU(strategy="sa")` |
| `metal-gibbs` | `SolverMetalGPU(strategy="gibbs")` |
| IBM QAOA (feature branch) | `SolverIBMQAOA` |
| IonQ QAOA (feature branch) | `SolverIonQQAOA` |
| `modal` | No xqsa equivalent (orchestration layer, not a solver algorithm) |

`SolverQuip` has no row here: it is the proposer side, not a miner. It submits jobs that the miners above solve, so it complements rather than mirrors a miner config key.

## Node identifiers

An order's graph carries hardware node labels from the published topology, not a renumbering of them. `SolverQuip` places a model onto the chain's registered topology and submits the placed subgraph under the labels the placement produced, so a two-variable model landing on hardware nodes 0 and 12 proposes `nodes = [0, 12]`, never `[0, 1]`.

Labels and positions are both present in an order, and they index different things:

| Field | Indexed by |
|-------|------------|
| `nodes` | hardware labels, in the topology's canonical order |
| `edges` | pairs of hardware labels, each of which must appear in `nodes` |
| `h_values` | position in `nodes` |
| `j_values` | position in `edges` |
| a returned spin vector | position in `nodes` |

The graph is expressed in labels; the coefficient and solution vectors are expressed in positions. `decode_solution` reads a spin back by position and recovers the model variable through the placement, which is deterministic.

The proposer does not renumber to `[0..n-1]`, for two reasons:

- **The labels are the only thing tying an order to real hardware.** A mempool order carries its graph inline and no topology id, so nothing on chain records which topology it was placed against. The node labels are the sole carrier of that. Renumbering to `[0..n-1]` would assert adjacencies the hardware need not have, turning a placed subgraph into a graph no miner can map onto its own.
- **The pallet does not require contiguity.** `validate_topology_consistency` checks that node ids are unique, that array lengths match, and that every edge endpoint appears in `nodes`. A sparse, non-contiguous node set is valid.

A consumer that needs positions derives them from the order's own `nodes` array. Conflating the two is not hypothetical: a coordinator that forwards an order's edge endpoints onward as node labels, where the wire expects positions, silently never answers any order whose node ids are not already `[0..n-1]`. The order stays open and no solution is ever submitted, which presents as an idle fleet rather than as an error.

## Coefficient encoding and allowed values

This section is the stable reference linked from `SolverQuip`'s `EncodingError` (on `i32` overflow) and from its one-time educational warning (on out-of-spec coefficients).

### Natural-scale to milli-scale

XQMX coefficients are natural-scale integers: a field or coupling of `5` means `5.0`. The `QuantumComputeMempool` pallet stores `h` (fields) and `j` (couplings) as milli-scale `i32`, read back on-chain as `value / 1000`. `SolverQuip` therefore multiplies each coefficient by `MILLI_SCALE = 1000` when encoding. A coefficient that is not exactly representable at milli precision (finer than `1/1000`) is rejected with `EncodingError`.

### The `i32` limit

Because the on-chain fields are `i32`, the milli value must fit `[-2_147_483_648, 2_147_483_647]`. That caps the natural coefficient at `MAX_NATURAL_COEFFICIENT = 2_147_483` (since `2_147_483 * 1000` fits and the next integer does not). A coefficient that overflows this range raises `EncodingError` -- this is a real chain field limit, so it is a hard error, not a warning. Rescale your model (the optimum is invariant under a positive uniform scaling of all coefficients) to fit.

### Allowed-value sets (advisory, not enforced)

Each topology declares allowed-value sets for its coefficients (for example the default plain-Ising spec allows `{-1, 0, +1}` for fields and `{-1, +1}` for couplings). `SolverQuip` submits coefficients **as-is** and does not reject out-of-spec values. There is deliberately no strict mode. The reasons:

- **The pallet does not enforce the sets.** `propose_job` passes `None`/`None` for the allowed-value arguments to `validate_topology_consistency`, so it validates only structural consistency (no duplicate nodes, matching array lengths, every edge endpoint present), never the coefficient values.
- **Nothing routes on the values.** A mempool job reaches a miner as edges and coefficients with no topology id attached, so there is no eligibility test for a coefficient to fail and out-of-spec values neither change eligibility nor block solving. A registered topology's allowed-value specs bind the consensus `submit_proof` path, not this one.
- **Real hardware rescales monotonically.** A physical annealer maps coefficients onto its analog range with a monotonic (optimum-preserving) rescaling, so an out-of-spec model is still solved correctly on hardware.
- **Strict mode would be useless.** The default sets (`{-1, 0, +1}` / `{-1, +1}`) would reject nearly every non-trivial model -- in particular any BINARY model, whose spin transform produces quarter-integer fields -- which is stricter than the chain itself and not helpful.

So when any encoded coefficient falls outside the fetched allowed-value sets, `SolverQuip` emits a one-time `warnings.warn` (naming an offender and linking here) and proceeds. The sets are fetched from `QuantumPow.Topologies` and are skipped silently when unknown.
