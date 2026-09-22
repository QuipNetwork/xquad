# Quip Network

`SolverQuip` is the one backend in this chapter that does not solve
anything itself. It proposes the model as a job to the Quip Network's
`QuantumComputeMempool` pallet, waits for a miner -- hardware it does
not control, running an algorithm it does not choose -- to solve it,
and decodes whatever comes back as an XQMX sample.

This page is derived from
[`xqsa/quip.py`](https://gitlab.com/quip.network/xquad/-/blob/main/xqsa/quip.py),
`spec/xqsa/SOLVERS.md`, and an internal contributor testing guide. The
mechanism it describes has been exercised end to end against `aglais`,
the public Quip test network; [Gaps](#gaps) lists what this page still
leaves unanswered for a reader.

## Installation and Configuration

Requires the `[quip]` extra:

```sh
pip install xqsa[quip]
```

The umbrella `xquad` package forwards `[cuda]`, `[dwave]`, and
`[metal]` to the matching `xqsa` extra, but has no `[quip]` extra of
its own -- install it against `xqsa` directly. The extra brings in
`substrate-interface` (the chain RPC client), `certifi` (the CA
bundle `wss://` falls back to on a macOS Python with none), and
`quip-signer`, a
native extension providing the chain's hybrid signature scheme
(sr25519 plus FN-DSA-512), which `substrate-interface` alone cannot
produce. `quip-signer` resolves a prebuilt wheel on Linux and builds
from source with a local Rust toolchain on macOS and Windows. Both
guards are lazy: `import xqsa` and every other solver in this chapter
work without the extra installed; only constructing `SolverQuip` needs
it.

Configuration resolves from constructor arguments first, then
environment variables:

| Argument | Environment variable | Meaning |
|---|---|---|
| `url` | `QUIP_RPC_URL` | Websocket RPC endpoint |
| `seed` | `QUIP_SIGNER_SEED` | 32-byte hex master seed |
| `keystore` | `QUIP_KEYSTORE` | Keystore file path (loaded, or created on first use) |
| `reward` | `QUIP_REWARD` | Reward in planck; falls back to the chain's `MinReward` |
| `faucet` | `QUIP_FAUCET_URL` | Faucet base URL used to top up a short account when `autofund` allows it |
| `autoconfirm` | `QUIP_AUTOCONFIRM` | Whether to submit without asking; `True` submits, `False` asks on the terminal, or a callable judging the job's `JobQuote` |
| `autofund` | `QUIP_AUTOFUND` | Whether to draw from `faucet` when the account cannot cover the quoted job; same three forms as `autoconfirm` |

Provide exactly one of `seed` or `keystore`. `spec_id` and `topology`
are constructor-only overrides -- both default to chain state
(`DefaultIsingSpecId` and `DefaultTopology`) and have no environment
variable. `mode`, `resolution`, and `delivery` are pass-through job
parameters defaulting to `Open`, `SingleBest`, and `OnChainOnly`; the
pallet's data-carrying variants (`Callback` delivery, `Bid` mode) exist
on-chain but `SolverQuip` does not exercise them.

### Named networks

`SolverQuip.for_network(name, **kwargs)` builds a solver from a named
preset in `xqsa.quip_networks` instead of an explicit `url`:

```python
SolverQuip.for_network("aglais", keystore="~/.quip/keystore.json")
```

Two presets are registered: `aglais`, the public test network, and
`devnet`, the local Docker stack contributors run for offline testing.
The RPC and faucet coordinates behind each preset live in
`xqsa.quip_networks`, move with the deployment, and are corrected in
patch releases. The test network's own page,
[aglais.quip.network](https://aglais.quip.network), publishes the
current endpoints and the faucet; check the preset against it if a
connection fails.

## Job Lifecycle

`solve()` calls `propose_job`, which reserves the full reward and
emits a `JobProposed` event. Miners discover work **only** from that
live event stream -- there is no storage backfill, so a job proposed
before a miner subscribed stays invisible to it. A miner that accepts
solves it on its own hardware with its own algorithm and submits a
solution.

No on-chain hook closes an order; its status changes only when an
extrinsic touches it. `SolverQuip` computes finality itself from block
height instead of waiting for an `OrderClosed` event:

```text
effective_expiry = min(created_at + deadline_blocks, first_solution_at + block_wait)
```

`solve()` polls until the chain head reaches that height, then reads
the result. `query(order_id, model, *, mapping=None, topology=None)`
returns `None` before finality, and a full `SolverResult` after -- not
a bare sample: `order_id`, `solver`, and the rest of the fields in
[Metadata](#metadata) come with it. Pass `mapping=`/`topology=` only
when they were non-default at proposal time; they let the order be
read back from a different process, for example after catching a
`QuipTimeoutError`, using the same deterministic placement.
`status(order_id)` is a separate, single-read snapshot that does not
wait and never decodes a result. Both `solve()` and `query()` share the
same finalization path: if an order finalizes with zero solutions, the
reward is best-effort reclaimed via `reclaim_order` (a failed reclaim
is logged, not raised) and `QuipJobFailedError` is raised.

## The Topology Constraint

Real annealing hardware -- and the miner fleet behind this network --
has a fixed set of physical couplings. `SolverQuip` skips the chains
and minor embedding that [D-Wave QPU](dwave-qpu.md)'s
`EmbeddingComposite` builds, and instead requires **subgraph
placement**: an injective variable-to-node mapping where every one of
the model's couplings maps onto a real hardware edge. A model whose
coupling graph is not a subgraph of the target topology raises
`PlacementError`, carrying the couplings that could not be placed.
Placement is deterministic, computed from the model alone, so nothing
about it needs to be persisted or looked up later.

The target topology is resolved once, at construction, from three
sources in order: the `topology=` argument, the `QUIP_TOPOLOGY`
environment variable, then the chain's `QuantumPow.DefaultTopology`.
Because the same topology shape hashes differently per deployment --
each network's allowed-value specifications fold into the hash -- a hash
from one Quip deployment is not portable to another. That is why nothing
is pinned in the codebase: every source is deployment-local, and a
topology that resolves from none of them raises rather than selecting a
hash no chain would accept. `QUIP_TOPOLOGY` is the operator's override
for whichever chain they are pointed at, and targets a registered
non-default topology without threading a constructor argument through.
`solve()` does not check the resolved hash against
`QuantumPow.MineableTopologies`. That set is the chain's active *mining*
set: it gates `submit_proof`, and so block production, not the compute
mempool. An order carries its nodes, edges and coefficients inline and
no topology hash at all, so the chain cannot perceive which topology an
order was built against, and the solver fleet has no topology field to
filter on. A topology that is registered but not mineable is proposed,
matched and solved like any other.

## Coefficient Encoding

The normative rules for this section are
[Coefficient encoding and allowed values](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqsa/SOLVERS.md#coefficient-encoding-and-allowed-values)
-- the same URL `EncodingError` and the allowed-value warning below both
carry.

XQMX coefficients are natural-scale integers. The pallet stores
couplings and fields as milli-scale `i32` values, read back on-chain as
`value / 1000`. Confirmed directly from
[`xqsa/quip_codec.py`](https://gitlab.com/quip.network/xquad/-/blob/main/xqsa/quip_codec.py):

```python
from xqsa.quip_codec import MILLI_SCALE, MAX_NATURAL_COEFFICIENT
print(MILLI_SCALE, MAX_NATURAL_COEFFICIENT)
# 1000 2147483
```

`SolverQuip` computes spin coefficients from the model and multiplies
each by `MILLI_SCALE` before encoding. A coefficient not exactly
representable at `1/1000` precision, or one whose milli value would
overflow `i32`, raises `EncodingError` rather than silently truncating.

`MAX_NATURAL_COEFFICIENT` is the exact ceiling only for a **SPIN**
model, whose `h`/`j` coefficients pass through to the pallet unchanged.
A **BINARY** model goes through a basis change first (`s = 2x - 1`, via
`dimod`), which folds every quadratic coefficient into the linear field
of both variables it touches: `j_ij = b_ij / 4`, and
`h_i = a_i / 2 + 1/4 * (sum of every quadratic coefficient incident on
i)`. `_to_milli()` only ever sees these post-conversion values, so for
a BINARY model the natural-scale ceiling moves in both directions from
what the constant alone suggests:

- An **isolated** coefficient survives past `MAX_NATURAL_COEFFICIENT`:
  an isolated quadratic term up to about `8_589_934` (roughly 4x), an
  isolated linear term up to about `4_294_967` (roughly 2x).
- A **high-degree** variable can overflow well below it, because `h_i`
  aggregates a quarter of every incident quadratic term. Ten quadratic
  terms of `1_000_000` all touching variable `0` give `h_0 = 2_500_000`
  -- nowhere near `MAX_NATURAL_COEFFICIENT` -- but its milli value,
  `2_500_000_000`, exceeds `i32` and raises `EncodingError`.

The error in that case reads `h[0] = 2500000.0 overflows the encodable
range`, naming a linear field the caller never set directly; the actual
cause is the aggregate of ten quadratic terms landing on variable `0`
during the basis change. This is a range shift, not a precision loss --
quarters and halves of integers are exact in floating point, and exact
at `1/1000` milli scale too (`0.25` becomes `250`) -- the stored value
is correct, only the range it has to fit in moved.

Rescaling a model does not change its optimum (see
[Choosing a Penalty Weight](../concepts/quadratic-models.md#choosing-a-penalty-weight)),
so an oversized coefficient is fixable by scaling the whole model down,
not a hard ceiling on what you can express.

Each topology also declares an allowed-value set for its coefficients
(the default plain-Ising spec allows `{-1, 0, +1}` for fields and
`{-1, +1}` for couplings), but the pallet does not enforce it, so
`SolverQuip` submits coefficients as-is. A miner matches jobs by an
exact hash over the topology's structure, not over the coefficient
values, and real hardware rescales monotonically in any case. An
out-of-spec coefficient produces a one-time `warnings.warn`, not a
rejection.

## Cost Model

Before submission, `solve()` reserves the configured `reward` (or the
chain's `MinReward` if none is given) from the caller's account, in the
chain's smallest denomination, planck. A client-side balance check adds
a fee-headroom buffer on top of the reward to catch an insufficient
balance early with a readable error; the chain itself is the
authoritative source for the actual reserve and fee deduction. If the
order finalizes with no solutions, the adapter best-effort reclaims the
reserved reward before raising -- a failed job costs the transaction
fee, not the reward. A successfully solved job's reward is paid out
on-chain to the miner; `SolverQuip` does not expose that payout as part
of `SolverResult`.

This page deliberately does not give a current `MinReward` figure or
translate planck into any external currency -- see [Gaps](#gaps).

## Metadata

`SolverResult.metadata` for `quip` carries six keys, populated from the
winning submission (`xqsa/quip.py:807-816`):

| Key | Type | Meaning |
|---|---|---|
| `order_id` | `int` | The on-chain order id -- the handle `query(order_id, model)` needs to recover a result after a `QuipTimeoutError`. |
| `solver` | `str \| None` | The miner account that won the order (`chosen.get("solver")`); can be absent. |
| `best_energy_milli` | `int` | The chain's own reported best energy, in milli units. |
| `energy_matches_chain` | `bool` | Canary: whether the local milli recompute from the returned spin vector equals `best_energy_milli`, confirming index alignment and encoding. |
| `num_submissions` | `int` | How many miner submissions the order received. |
| `num_solutions` | `int` | How many solution vectors the winning submission carried. |

`best_energy_milli` is milli-scale (see
[Coefficient Encoding](#coefficient-encoding)) and is not directly
comparable to `result.energy`, the authoritative natural-scale integer
`_recompute_energy()` computes (`quip.py:795`); diffing the two without
converting scale first will make a correct result look wrong.

`metadata["solver"]` here names the miner that solved the job, not a
piece of hardware -- [D-Wave QPU](dwave-qpu.md#how-a-qpu-result-differs)
uses the same key for the physical Advantage system that ran the job,
so the same key names a different kind of thing depending on the
backend.

`energy_matches_chain` is the most useful key day to day: it is the
encoding-correctness canary, confirming that the placement and decoding
`SolverQuip` used line up with what the chain itself computed.

## Failure Taxonomy

Nine exception classes cover this backend, confirmed by import from
`xqsa.__all__` -- one base class and eight concrete failures, spanning
both local encoding checks and network-dependent lifecycle failures:

| Exception | Raised when |
|---|---|
| `QuipError` | Base class for every error below |
| `EncodingError` | The model is not `MODEL`-mode, its domain is unsupported, or both its `linear` and `quadratic` dicts are empty. Also covers a coefficient that fails milli-scale conversion (see [Coefficient Encoding](#coefficient-encoding)) |
| `PlacementError` | The model's coupling graph is not a subgraph of the target topology |
| `QuipSigningError` | Extrinsic assembly, keystore handling, or submission fails |
| `QuipConnectionError` | The node is unreachable, or a configured Ising spec is not registered on-chain |
| `QuipSubmissionError` | An extrinsic cannot be submitted or the chain rejects it |
| `QuipTopologyError` | Retained for compatibility; nothing raises it. It reported a topology absent from `MineableTopologies`, which does not gate the mempool |
| `QuipTimeoutError` | An order does not reach finality before the configured timeout; carries `order_id` so the caller can recover the result later with `query()` |
| `QuipJobFailedError` | A final order has no usable solution; carries `order_id` |

The `EncodingError` emptiness check tests the dicts themselves, not the
values inside them (`quip_codec.py:643-644`) -- but `XQMX.set_linear()`
/ `set_quadratic()` pop a key the moment its value reaches `0`
(`xqvm_py/xqmx.py:175-226`), so for any model built through the normal
API, "has no terms" and "carries only zero terms" are the same
condition in practice. A model constructed directly as a dataclass,
bypassing those setters, can hold explicit zero entries that leave the
dicts non-empty; that model passes the `EncodingError` check and
submits all-zero coefficient arrays.

`EncodingError` and `PlacementError` can be raised entirely locally,
before anything touches the network -- they come from
`xqsa.quip_codec`, the same module the coefficient encoding above uses.
The rest depend on chain state or the round trip completing.

## Gaps

This chapter answers the mechanism -- lifecycle, topology, encoding,
cost accounting, failures -- in detail. It does not answer what a reader
outside the project needs to actually run a job:

- **No funding walkthrough.** The `aglais` preset carries the faucet's
  address, but `SolverQuip` does not request funds itself, and this
  page does not walk through funding an account; the test network's
  page above shows the faucet request.
- **No current reward or fee figures.** `MinReward` and transaction fees
  are live chain state that the contributor guide itself warns changes
  between releases; this chapter does not assert a number that could go
  stale in the published book.
- **No numbers from an observed job.** The propose -> solve -> decode
  path is exercised against a live deployment by the contributor suite,
  but the figures a particular submission produces -- timing, an actual
  `order_id`, a real `qpu_timing`-equivalent, how many blocks a fresh
  job typically takes -- are deployment- and fleet-dependent, so none is
  quoted here.
