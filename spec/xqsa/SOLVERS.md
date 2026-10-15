# Solver Registry

## Naming Convention

Public solver classes are named for where the model is solved: `SolverCPU`, `SolverGPU`, `SolverDWave` and `SolverQuip`. What runs there is chosen by selectors, not by more classes:

| Selector | Class | Picks |
|----------|-------|-------|
| `algorithm` | `SolverCPU`, `SolverGPU` | The sampling algorithm. Values follow the solver binary suffixes (`sa`, `gibbs`, ...), so new algorithms add values rather than renames. |
| `engine` | `SolverGPU` | The GPU runtime: `"cuda"` or `"metal"`. Required; there is no auto-detection. |
| `mode` | `SolverDWave` | How D-Wave hardware is used: `"qpu"`. |

Selectors are keyword-only constructor arguments. Each class passes every other keyword through to the implementation it composes (see [INTERFACE.md](INTERFACE.md)). Implementations are named for how they run: `InProcessCPU`, `InProcessCuda`, `InProcessMetal` and `InProcessDWave` run in the Python process. They are not exported from `xqsa`.

## Algorithm Families

### D-Wave SDK Family

Implementations that use the `dimod` BQM format and D-Wave SDK packages: `InProcessCPU` (`dwave-samplers`) and `InProcessDWave` (`dwave-system`). The SDK handles model conversion and sampling; they differ in where the BQM is solved (CPU, QPU).

Shared helpers: the module functions `model_to_bqm()` and `sample_to_xqmx()` in `xqsa.solver`.

`SolverDWave(mode="qpu")` runs `InProcessDWave`. Any other `mode` raises `ValueError`; hybrid modes are not available yet. Of the Ocean sampling parameters, it forwards only `chain_strength` and `auto_scale`, from the constructor or per call, and records each one it forwarded in `params`. `None` means unset: a per-call `chain_strength=None` forwards nothing for that call, even when the constructor set a value. Any other Ocean keyword raises `TypeError` rather than reaching the sampler unchecked.

### Custom Kernel Family

Custom GPU kernels (CUDA `.cu` / Metal `.metal`) bypassing the D-Wave SDK for the compute step. Higher performance ceiling, more code to maintain. `SolverGPU` runs them; `engine` picks the kernel set and `algorithm` the kernel within it.

`InProcessCuda` (`engine="cuda"`) uses CuPy RawKernel for custom CUDA C++ kernels. It converts XQMX models directly to dense float64 GPU arrays (skipping the BQM intermediate) and runs parallel-replica simulated annealing with Metropolis acceptance.

`InProcessMetal` (`engine="metal"`) is the Apple Silicon counterpart, using inline Metal Shading Language kernels dispatched via `pyobjc-framework-Metal`. It supports `algorithm="sa"` (simulated annealing) and `algorithm="gibbs"` (block Gibbs sampling over a greedy graph colouring), on dense float32 arrays.

Both engines generate acceptance randomness on-device per replica, so no random buffer is allocated.

### In-process algorithms

`algorithm` accepts only what runs in-process today. Any other value raises `ValueError` saying the algorithm is not available in-process; further algorithms arrive with the solver binaries.

| Class | `engine` | `algorithm` values | Default |
|-------|----------|--------------------|---------|
| `SolverCPU` | -- | `"sa"` | `"sa"` |
| `SolverGPU` | `"cuda"` | `"sa"` | `"sa"` |
| `SolverGPU` | `"metal"` | `"sa"`, `"gibbs"` | `"sa"` |

### Parameters

Parameters per (engine, algorithm). Every parameter is a constructor keyword; those marked per-call can also be passed to `solve()`. `seed`, `reads` and `params` in the result are described in [INTERFACE.md](INTERFACE.md#metadata-schema).

**`SolverCPU`, `algorithm="sa"`:**

| Parameter | Default | Per-call |
|-----------|---------|----------|
| `num_reads` | `100` | yes |
| `num_sweeps` | `1000` | yes |
| `num_sweeps_per_beta` | `1` | yes |
| `beta_range` | `None` (auto) | yes |
| `seed` | `None` | yes |

`params` keys: `algorithm`, `num_sweeps`, `num_sweeps_per_beta`, `num_betas`, `beta_range`, `num_occurrences`, `raw_energy`.

**`SolverGPU`, `engine="cuda"` with `algorithm="sa"`, and `engine="metal"` with `algorithm="sa"` or `"gibbs"`:**

| Parameter | Default | Per-call |
|-----------|---------|----------|
| `num_reads` | `100` | yes |
| `num_sweeps` | `1000` | yes |
| `num_sweeps_per_beta` | `1` | yes |
| `beta_range` | `None` (auto) | yes |
| `beta_schedule_type` | `"linear"` on CUDA, `"geometric"` on Metal | yes |
| `seed` | `None` | yes |

`beta_schedule_type` is `"linear"` or `"geometric"`. The two engines keep different defaults: CUDA's linear default reproduces its historical in-kernel ramp bit for bit. `params` keys: `algorithm`, `engine`, `num_sweeps`, `num_sweeps_per_beta`, `num_betas`, `beta_range`, `beta_schedule_type`, `raw_energy`.

**`SolverDWave`, `mode="qpu"`:**

| Parameter | Default | Per-call |
|-----------|---------|----------|
| `token` | `DWAVE_API_TOKEN` | no |
| `endpoint` | `DWAVE_API_ENDPOINT` | no |
| `solver` | first Pegasus solver | no |
| `num_reads` | `100` | yes |
| `annealing_time` | `20` | yes |
| `chain_strength` | not forwarded | yes |
| `auto_scale` | not forwarded | yes |

`params` keys: `mode`, `solver`, `qpu_timing`, `annealing_time`, `raw_energy`, plus `chain_strength` and `auto_scale` when forwarded. `seed` is always `None`.

### Gate-Based QAOA Family

Variational quantum circuits via Qiskit. Non-D-Wave; entirely different algorithmic approach (parameterised circuits + classical optimizer). IBM and IonQ are separate solvers because they use different Qiskit provider packages.

### Network Mempool Family

Solvers that do not run the optimization locally at all: they submit the model to a decentralized compute market and read the result back. `SolverQuip` is the first member. It proposes the model as a job to the Quip Network `QuantumComputeMempool` pallet, miners solve it (each on its own hardware, with whatever algorithm it runs), the chain ranks the solvers that answered, and the adapter decodes the leader's best vector, the answer the chain pays for, as an XQMX sample. The algorithm is the miner's; this family owns the protocol plumbing.

`SolverQuip` (`quip/client.py`) is a synchronous `Solver`. Notable characteristics:

- **Subgraph placement.** The model is placed onto the miner's fixed hardware topology (advantage2_system1-class): an injective variable -> node mapping where every coupling lands on a real hardware edge. Placement is deterministic, computed from the model alone, so it is never persisted: an in-session `JobOrder` holds it, and the deprecated `query()` re-derives it from the model. Minor-embedding with chains is out of scope; a model that is not a subgraph of the topology raises `PlacementError`. The target topology is resolved at construction from `topology=`, then `QUIP_TOPOLOGY`, then the chain's `QuantumPow.DefaultTopology`. All three are deployment-local: the same advantage2 graph hashes differently across deployments because the hash folds in each deployment's allowed-value specs, so nothing is pinned in the codebase and an unresolvable topology raises rather than falling through to a hash no chain would accept. `QUIP_TOPOLOGY` targets a registered non-default topology without threading a constructor argument through, which is what makes that path testable against a live chain. `solve()` does not consult `QuantumPow.MineableTopologies`: that is the chain's active mining set, gating `submit_proof` and so block production, and it does not decide what the compute mempool admits or answers. An order carries its nodes, edges and coefficients inline and no topology hash at all, so the chain cannot perceive which topology an order was built against, and an order proposed against a registered-but-non-mineable topology is admitted on chain and answered by the fleet normally. See "Topology policy is not chain-enforced" below. The network currently exposes a single default topology; multi-topology discovery and success-ranking (auto-selecting among several registered topologies per job) is deferred to future protocol-side work, out of scope until a second topology or hardware type registers. `topology="native"` (constructor, `create_order()`/`JobOrder.set()` option, per call on `solve()`, or `QUIP_TOPOLOGY=native`; case and surrounding whitespace ignored) never raises `PlacementError`: it builds the topology from the model's own variables and couplings, relabelled densely to `0..n-1`, reads no chain topology, and rejects `mapping=` with `ValueError`. A native order is as large as the model, so one over the mempool's `MaxNodes` (5,000) or `MaxEdges` (50,000), read from the chain for each native order, raises `EncodingError` before it is quoted; a failed read raises `QuipConnectionError`. Miners that cannot embed an arbitrary graph, such as QPU-backed ones, cannot answer these orders; the simulated-annealing fleet can. Native mode is opt-in only: falling back to it on `PlacementError` would turn a call that raises today into one that proposes and spends funds.
- **Topology policy is not chain-enforced.** Three topology sets matter and only two exist. `QuantumPow.RegisteredTopologies` publishes a topology; registering obligates nobody, so registered means proposed, not supported. `QuantumPow.MineableTopologies` is the active mining set and gates `submit_proof` alone; it is near-singleton by design, because the difficulty decay anchor is global and a second non-default entry would corrupt it. A third set -- topologies a miner is required to be able to solve -- is deliberately not implemented; `register_topology` is `ensure_root`, so the admin gate substitutes for now. The mempool binds an order to no topology at all: `propose_job` and `submit_solution` read neither storage item and both rebuild their index from the order's own inline arrays, and the job that reaches a miner carries edges and coefficients with no topology id to filter on. That is the design rather than an omission. The network is intended to be mostly permissionless, so chain-enforced topology policy as a class -- a required-to-solve set, banned sets, an admission gate at `propose_job` -- is out of scope on principle rather than merely unbuilt, and filtering belongs in miner software as local policy. Even that waits on the job carrying a topology id, which today it does not. The consequence for this adapter: *mineable* is the consensus predicate, not the mempool's, so `SolverQuip` does not consult it. An earlier revision checked it before reserving the reward and raised `QuipTopologyError`. That check is deleted rather than relaxed to a warning: the condition cannot affect whether an order is admitted or answered, so warning about it would report something that changes no outcome, which is how warnings become noise. `QuipTopologyError` is retained as an exported name and is no longer raised.
- **Milli-scale `i32` encoding.** XQMX natural-scale coefficients are scaled by 1000 into the chain's `i32` `h`/`j` arrays. See "Coefficient encoding and allowed values" below.
- **Hybrid signing.** The chain's `Signature` is `HybridTxSignature` (sr25519 + FN-DSA-512), which `substrate-interface` cannot produce. All crypto is delegated to the `quip_signer` extension; `xqsa.quip.signing` owns only the extrinsic-assembly layer. The account is a 32-byte master seed (env `QUIP_SIGNER_SEED`) or a keystore file (env `QUIP_KEYSTORE`), funded on localdev via the faucet. Do not derive dev accounts from `//Alice`-style URIs -- the hybrid chain yields a different account.
- **Block-height finality.** Status flips `Opened -> Expired` at the order's effective expiry, or a few blocks later when many orders expire at once, and `-> Closed` only on a claim or reclaim, so the adapter never waits for `OrderClosed`. It computes finality itself from block height: `effective_expiry = min(created_at + deadline_blocks, first_solution_at + block_wait)`, final once the head reaches it. `solve()` and `JobOrder.wait()` poll to finality. A submitted order has a `JobOrderReceipt`, which any session rebuilds from the order id with `SolverQuip.get_receipt(order_id)`; it reads the order's status, solvers, raw vectors and settlement, and reclaims it, and it replaces the deprecated `query(order_id, model)` and `status(order_id)`. It needs no model, so decoding answers onto one still takes the in-session `JobOrder` (`best()`, `solutions()`) or, from another session, the deprecated `query()`.
- **Auto-reclaim.** `propose_job` reserves the full reward. Only `solve()` and the deprecated `query()` reclaim on their own: if an order finalizes with no solutions, they submit `reclaim_order` (unreserving the reward) before raising `QuipJobFailedError`, whose message carries the outcome: reclaimed, refused with the reason, or possibly still reserved with the call to retry. `JobOrder.wait()`, `JobOrder.best()` and a receipt's `wait()` never reclaim. `reclaim()` on a `JobOrder` or `JobOrderReceipt` reclaims explicitly, refusing with `QuipReclaimRefusedError` before signing when the order was proposed by another account, is already closed, is still open, or was answered.
- **Native signing extension.** `quip_signer` (hybrid sr25519 + FN-DSA-512) installs via the `[quip]` extra alongside `substrate-interface`. Linux resolves a manylinux wheel; macOS and Windows build it from the sdist with a local Rust toolchain, matching xquad's own native-package policy. `import xqsa` and every other solver stay healthy without the extra; only constructing `SolverQuip` triggers the guards.
- **Runtime 119.** Runtime 119 renames the order's energy-ceiling field from `min_energy_milli` to `max_energy_milli`. The adapter sends the ceiling under both names and the struct encoder keeps the one the runtime lists, so it proposes on runtimes before and after 119; xquad 0.4.2 and earlier cannot propose on 119. `ResolutionTopNWeighted` needs 119, because older runtimes weight the split by absolute energy rather than rank; the adapter reads `specVersion` once at connect and refuses the rule before signing on an older runtime. Runtime 119 starts a network afresh rather than upgrading it in place, so orders placed before it do not carry over.
- **Pre-release volatility.** Quip's public network (aglais) and the local DevNet are both pre-release: pallet metadata, the signed-extension layout, the hybrid signature suite, and economic parameters can all change between releases. Re-validate after an upgrade rather than assuming a recorded coordinate still holds.

Configuration (constructor arg, then environment): `url`/`QUIP_RPC_URL`, `seed`/`QUIP_SIGNER_SEED`, `keystore`/`QUIP_KEYSTORE` (else `~/.quip/keystore.json`, created on first use), `reward`/`QUIP_REWARD` (else the chain `MinReward`), `topology`/`QUIP_TOPOLOGY` (else the chain `DefaultTopology`), `faucet`/`QUIP_FAUCET_URL` (base URL `autofund` draws from; the variable is read only when the RPC URL also comes from `QUIP_RPC_URL`), `autoconfirm`/`QUIP_AUTOCONFIRM` (`True` submits without asking and is the default, `False` asks on the terminal (stdin, and stderr or stdout) and raises `QuipCancelledError` without one, a callable judges the `JobQuote`; the variable takes `1/true/yes/on` or `0/false/no/off`), `autofund`/`QUIP_AUTOFUND` (same three forms, decides whether a short account is topped up with one drip from `faucet` before proposing; a larger shortfall, or an account already holding more than one drip, raises). The `spec_id` is a constructor-only override, defaulting to the chain's `DefaultIsingSpecId`. `access`, `resolution`, `deadline_blocks` (default 100) and `block_wait` (default 10) are typed, checked client defaults that every order inherits. `access` is `AccessOpen()` (the default) or `AccessAllow(accounts=[...], types=[...])`, encoded as the pallet's `{"Bid": {"miners": ..., "miner_types": ...}}`, each field an `Option<BoundedVec<..>>` with an empty list sent as `None`; `mode=` is a deprecated alias. `resolution` is `"SingleBest"` (the default) or a `Resolution` rule: `ResolutionSingleBest()`, `ResolutionTopNEqual(n)` or `ResolutionTopNWeighted(n)`, `n` from 1 to 32. `delivery` accepts only `OnChainOnly`: `Callback` delivery stays unsupported, because the client has no subscriber for the `ResultReady` event the pallet emits when the order expires. The primary surface is `create_order(model, **options)`, which returns a draft `JobOrder` with ten per-order options: those four, `reward`, `topology`, `mapping`, and the quality floors `max_energy` (an energy ceiling in model units), `min_solutions` and `min_diversity`. Every option is checked against the chain's limits (`MinReward`, `MaxDeadlineBlocks`, `MaxBlockWait`, `MaxSolutions`, `MaxBidMiners`, the runtime's `MinerType` list), read once at connect, and refused with `QuipOrderOptionError` before anything is signed. `JobOrder.quote()` signs the `propose_job` extrinsic once and prices it; `submit()` sends that signature, re-signing only if the account nonce moved. `solve()` is `create_order(...).submit().wait()` plus decoding. `submit()` and `solve()` quote the job (the reward plus the chain-reported fee, weighed against the account balance) and pass its consent gates before proposing it: `autoconfirm` first, then `autofund` only when the account is short. `SolverQuip.for_network(name, **kwargs)` builds a solver from a named preset (`aglais`, `devnet`) in `xqsa.quip.networks`, passing the preset RPC as `url` and its faucet as `faucet` unless `faucet=` names another.

## Dependency Model

`SolverCPU` needs only the base package. Every other solver needs an optional extra, and its dependency is imported only when the solver is constructed, so `import xqsa` never fails: `InProcessCuda`, `InProcessMetal` and `InProcessDWave` import theirs inside `__init__`, `SolverGPU` imports the engine module it picks, and `SolverQuip` checks for `substrate-interface` and imports its signing module on construction.

- `ImportError` with a `pip install xqsa[extra]` hint when the required extra is not installed
- `RuntimeError` when required hardware is not detected (GPU, QPU)

## Solver Registry

| Solver | Implementation (file) | Extra | Hardware | Status |
|--------|-----------------------|-------|----------|--------|
| `SolverCPU` | `InProcessCPU` (`dwave_cpu.py`) | (base) | CPU | implemented |
| `SolverGPU(engine="cuda")` | `InProcessCuda` (`cuda_gpu.py`) | `[cuda]` | NVIDIA CUDA GPU | implemented |
| `SolverGPU(engine="metal")` | `InProcessMetal` (`metal_gpu.py`) | `[metal]` | Apple Metal GPU | implemented |
| `SolverDWave(mode="qpu")` | `InProcessDWave` (`dwave_qpu.py`) | `[dwave]` | D-Wave QPU (cloud) | implemented |
| `SolverQuip` | -- (`quip/`) | `[quip]` | Quip Network mempool (miner-solved) | implemented |
| `SolverIBMQAOA` | -- | `[ibm]` | IBM QPU / AerSimulator | planned |
| `SolverIonQQAOA` | -- | `[ionq]` | IonQ trapped-ion QPU | planned |

The public classes live in `solvers.py`, except `SolverQuip` in the `quip/` package.

## Selecting by Name

`xqsa.build_solver(name, *, seed=None)` constructs a solver from a short, CLI-friendly name, decoupling callers (example runners, the smoke harness, `--solver` flags) from concrete class names. `xqsa.SOLVERS` is the authoritative name -> factory map and `xqsa.DEFAULT_SOLVER` is the baseline. A factory is a class or a `functools.partial` of one, so `cuda` and `metal` both build `SolverGPU`. `seed` is forwarded only to the classical SA backends; the QPU is physical hardware and `quip` is configured from the environment, so both take no seed. The `cuda` and `metal` entries bind `num_reads=200` and `num_sweeps=2000`, so `SOLVERS["cuda"]()` and `build_solver("cuda")` build the same solver; a keyword passed to the factory overrides them.

| Name | Builds |
|------|--------|
| `cpu` (default) | `SolverCPU()` |
| `cuda` | `SolverGPU(engine="cuda")` |
| `metal` | `SolverGPU(engine="metal")` |
| `dwave` | `SolverDWave()` |
| `quip` | `SolverQuip()` (env-configured; no seed; needs `[quip]`) |

## Protocol Miner Equivalence

Mapping between `quip-protocol` miner config keys and xqsa solvers:

| Protocol miner | xqsa solver |
|----------------|-------------|
| `cpu` | `SolverCPU()` |
| `qpu` | `SolverDWave(mode="qpu")` |
| `cuda` | `SolverGPU(engine="cuda", algorithm="sa")` |
| `cuda-gibbs` | No in-process equivalent (CUDA runs `"sa"` only) |
| `metal` | `SolverGPU(engine="metal", algorithm="sa")` |
| `metal-gibbs` | `SolverGPU(engine="metal", algorithm="gibbs")` |
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

This section is the stable reference linked from `SolverQuip`'s `EncodingError` (on `i32` overflow) and from its one-time warnings (on milli-rounded and on out-of-spec coefficients).

### Natural-scale to milli-scale

XQMX coefficients are natural-scale integers: a field or coupling of `5` means `5.0`. The `QuantumComputeMempool` pallet stores `h` (fields) and `j` (couplings) as milli-scale `i32`, read back on-chain as `value / 1000`. `SolverQuip` therefore multiplies each coefficient by `MILLI_SCALE = 1000` when encoding and rounds it to the nearest integer, ties to even. A coefficient finer than `1/1000` is rounded rather than rejected, so float weights and the off-grid spin fields a BINARY model's basis change produces still encode. Each rounding moves a coefficient by at most `0.0005`. The largest rounding error is recorded as `IsingJob.quantization_error`, reported once per solver instance as a `warnings.warn`, and returned on every result as `metadata["quantization_error"]`. Rounding perturbs only the model the miners search: the energy `SolverQuip` returns is recomputed on the original model. The winning submission is still chosen by the chain's energy on the rounded model, and only the vectors inside it are re-scored on the original. `quantization_error` bounds each coefficient, not the energy: a sample's energy can move by up to `quantization_error` times the number of nonzero terms. A model whose coefficients all round to zero is rejected with `EncodingError` rather than proposed as an empty order.

### The `i32` limit

Because the on-chain fields are `i32`, the milli value must fit `[-2_147_483_648, 2_147_483_647]`. That caps the natural coefficient at `MAX_NATURAL_COEFFICIENT = 2_147_483` (since `2_147_483 * 1000` fits and the next integer does not). A coefficient that overflows this range raises `EncodingError` -- this is a real chain field limit, so it is a hard error, not a warning. Rescale your model (the optimum is invariant under a positive uniform scaling of all coefficients) to fit.

### Allowed-value sets (advisory, not enforced)

Each topology declares allowed-value sets for its coefficients (for example the default plain-Ising spec allows `{-1, 0, +1}` for fields and `{-1, +1}` for couplings). `SolverQuip` submits coefficients **as-is** and does not reject out-of-spec values. There is deliberately no strict mode. The reasons:

- **The pallet does not enforce the sets.** `propose_job` passes `None`/`None` for the allowed-value arguments to `validate_topology_consistency`, so it validates only structural consistency (no duplicate nodes, matching array lengths, every edge endpoint present), never the coefficient values.
- **Nothing routes on the values.** A mempool job reaches a miner as edges and coefficients with no topology id attached, so there is no eligibility test for a coefficient to fail and out-of-spec values neither change eligibility nor block solving. A registered topology's allowed-value specs bind the consensus `submit_proof` path, not this one.
- **Real hardware rescales monotonically.** A physical annealer maps coefficients onto its analog range with a monotonic (optimum-preserving) rescaling, so an out-of-spec model is still solved correctly on hardware.
- **Strict mode would be useless.** The default sets (`{-1, 0, +1}` / `{-1, +1}`) would reject nearly every non-trivial model -- in particular any BINARY model, whose spin transform produces quarter-integer fields -- which is stricter than the chain itself and not helpful.

So when any encoded coefficient falls outside the fetched allowed-value sets, `SolverQuip` emits a one-time `warnings.warn` (naming an offender and linking here) and proceeds. The sets are fetched from `QuantumPow.Topologies` and are skipped silently when unknown.
