# Quip Network

`SolverQuip` is the one backend in this chapter that does not solve
anything itself. It proposes the model as a job to the Quip Network's
`QuantumComputeMempool` pallet, waits for a miner -- hardware it does
not control, running an algorithm it does not choose -- to solve it,
and decodes whatever comes back as an XQMX sample.

This page is derived from
[`xqsa/quip/`](https://gitlab.com/quip.network/xquad/-/tree/main/xqsa/quip),
`spec/xqsa/SOLVERS.md`, and an internal contributor testing guide. The
mechanism it describes has been exercised end to end against `aglais`,
the public Quip test network. [Your First Quip Job](quip-first-job.md)
walks one job through it; [Gaps](#gaps) lists what the figures there
cannot promise.

## Installation and Configuration

Requires the `[quip]` extra:

```sh
pip install "xquad[quip]"
```

The umbrella `xquad` package forwards `[quip]` to the matching `xqsa`
extra, as it does `[cuda]`, `[dwave]`, and `[metal]`; with `xqsa` alone,
`pip install "xqsa[quip]"` is the same install. The extra brings in
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
| `keystore` | `QUIP_KEYSTORE` | Keystore file path (loaded, or created on first use); defaults to `~/.quip/keystore.json` |
| `reward` | `QUIP_REWARD` | Reward in planck; falls back to the chain's `MinReward` |
| `faucet` | `QUIP_FAUCET_URL` | Faucet base URL used to top up a short account when `autofund` allows it. The variable is read only when the RPC URL also comes from `QUIP_RPC_URL`, so an explicit `url` never picks up a faucet for another chain |
| `autoconfirm` | `QUIP_AUTOCONFIRM` | Whether to submit without asking. `True` (the default) submits; `False` asks on the terminal (stdin, and stderr or stdout) and raises `QuipCancelledError` without one, a notebook included; a callable judges the job's `JobQuote`. The variable takes `1/true/yes/on` or `0/false/no/off` |
| `access` | none | Who may answer: `"Open"` or `AccessOpen()` (the default), or `AccessAllow(accounts=[...], types=[...])` |
| `resolution` | none | The payout rule: `"SingleBest"` (the default), `ResolutionSingleBest()`, `ResolutionTopNEqual(n)` or `ResolutionTopNWeighted(n)` |
| `delivery` | none | How results are delivered; only `"OnChainOnly"` (the default) is accepted |
| `deadline_blocks` | none | Blocks an order stays open; defaults to `100` |
| `block_wait` | none | Blocks an order keeps accepting answers after the first one arrives; defaults to `10` |
| `autofund` | `QUIP_AUTOFUND` | Whether to draw one drip from `faucet` when the account cannot cover the quoted job; a shortfall larger than one drip, or an account already holding more than one drip, raises instead. Same forms and default as `autoconfirm` |

A `seed` wins over a `keystore`; with neither set, `SolverQuip` uses
`~/.quip/keystore.json`, creating it on first use. `spec_id` and
`topology` both default to chain state (`DefaultIsingSpecId` and
`DefaultTopology`). `spec_id` is a constructor-only override with no
environment variable. `topology` also reads `QUIP_TOPOLOGY`, accepts
`"native"`, and can be overridden per order; see [The Topology
Constraint](#the-topology-constraint).

The order options among these (`reward`, `access`, `resolution`,
`deadline_blocks`, `block_wait` and `topology`) are client defaults.
Every order `create_order()` builds inherits them, and `create_order()`
or `JobOrder.set()` override them for that order. The quality floors
(`max_energy`, `min_solutions` and `min_diversity`) exist per order
only, never as client defaults. [Job Orders](quip-orders.md) covers each
option, including the [payout rules](quip-orders.md#payout-rules) and
[access rules](quip-orders.md#access). `delivery` accepts only
`"OnChainOnly"`: the pallet's `Callback` delivery fires its
`ResultReady` event only when a reward is claimed, which network solvers
do not do.

Before connecting, the constructor raises `ValueError` for a `delivery`
other than `"OnChainOnly"`, and for an `access` or `resolution` that is
not one of the rules above. After connecting, it checks the default
`access` against the chain's allowlist limits. The remaining options are
checked against the chain's limits when an order is created.

### Named networks

`SolverQuip.for_network(name, **kwargs)` builds a solver from a named
preset in `xqsa.quip.networks` instead of an explicit `url`:

```python
SolverQuip.for_network("aglais")
```

Two presets are registered: `aglais`, the public test network, and
`devnet`, the local Docker stack contributors run for offline testing.
The RPC and faucet coordinates behind each preset live in
`xqsa.quip.networks`, move with the deployment, and are corrected in
patch releases. The test network's own page,
[aglais.quip.network](https://aglais.quip.network), publishes the
current endpoints and the faucet; check the preset against it if a
connection fails. The preset also names its network's faucet, which the
solver uses unless `faucet=` names another; `QUIP_FAUCET_URL` does not
override a preset.

### Runtime compatibility

<!-- xquad:defect QUI-1823 -->
> **Not yet verified on runtime 119.** This page was tested on `aglais`
> at runtime 118 only. No network ran runtime 119 when it was written,
> so proposing an order there, and the rest of this section, may behave
> differently. Report problems at the [issue
> tracker](https://gitlab.com/quip.network/xquad/-/issues).

`SolverQuip` reads the runtime's `specVersion` once, when it connects; a
client that stays connected across an upgrade keeps the version it read
until it reconnects. Runtime 119 renamed the order's energy-ceiling
field from `min_energy_milli` to `max_energy_milli`. xquad 0.4.3 sends
the ceiling under both names and the encoder keeps the one the runtime
lists, so it proposes on runtimes before and after 119. xquad 0.4.2 and
earlier cannot propose on 119.

`ResolutionTopNWeighted` needs runtime 119 or later. On an older runtime
every order that uses it is refused before signing, because older
runtimes weight the split by absolute energy, so a worse answer can take
the larger share.

Moving a network to runtime 119 starts it afresh instead of upgrading it
in place. Orders placed before the move do not carry over, so an order
id recorded before it does not name the same order after it. `aglais`
runs runtime 118 as of 2026-10-12.

## Job Lifecycle

Submitting an order calls `propose_job`, which reserves the full reward
and emits a `JobProposed` event. Miners discover work **only** from that
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

`JobOrder.wait()` and `solve()` poll until the chain head reaches that
height. `solve(model, **options)` is
`create_order(model, **options).submit().wait()` followed by decoding
the answer into a `SolverResult` (see [Metadata](#metadata)); unlike
`create_order()`, it warns about an unknown option and drops it instead
of raising. The order surface -- drafts, quotes, receipts, reclaiming,
and reading answers -- is on [Job Orders](quip-orders.md).

Only `solve()`, and the deprecated `query()`, reclaim on their own. When
an order finalizes with zero solutions, they reclaim the reserved reward
through `reclaim_order` and raise `QuipJobFailedError`, whose message
says whether the reward was reclaimed or why it was not.
`JobOrder.wait()`, `JobOrder.best()` and a receipt's `wait()` never
reclaim; call `reclaim()` on the order or its receipt.

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

### Native topology mode

Passing `topology="native"` builds the topology from the model's own
variables and couplings instead of reading one from the chain. It is
accepted on the constructor, as an order option on `create_order()` and
`JobOrder.set()`, per call on `solve()` and the deprecated `quote()` and
`query()`, and through `QUIP_TOPOLOGY=native`, matched without regard to
case or surrounding whitespace:

```python
result = solver.solve(model, topology="native")
```

The variables, after any BINARY-to-SPIN fold, are relabelled densely to
`0..n-1`, so placement is the identity and cannot raise
`PlacementError`. No chain topology is read, and the native topology
carries no allowed-value sets, so the allowed-value warning stays
silent; the mempool never enforces those sets. `mapping=` has no
hardware graph to target in this mode and raises `ValueError`. A
per-call value overrides the constructor's in either direction. A native
order is as large as the model, so one over the mempool's `MaxNodes`
(5,000) or `MaxEdges` (50,000) raises `EncodingError` before it is
quoted. The solver reads both bounds from the chain for each native
order; a failed read raises `QuipConnectionError`.

Native mode narrows who can answer. A miner that cannot embed an
arbitrary graph, such as a QPU-backed one, cannot solve these orders;
the simulated-annealing miner fleet can. Native mode is never chosen
automatically on `PlacementError`: a call that raises today would
otherwise start proposing jobs and spending funds.

## Coefficient Encoding

The normative rules for this section are
[Coefficient encoding and allowed values](https://gitlab.com/quip.network/xquad/-/blob/main/spec/xqsa/SOLVERS.md#coefficient-encoding-and-allowed-values)
-- the same URL `EncodingError`, the rounding warning, and the
allowed-value warning below all carry.

XQMX coefficients are natural-scale integers. The pallet stores
couplings and fields as milli-scale `i32` values, read back on-chain as
`value / 1000`. Confirmed directly from
[`xqsa/quip/codec.py`](https://gitlab.com/quip.network/xquad/-/blob/main/xqsa/quip/codec.py):

```python
from xqsa.quip.codec import MILLI_SCALE, MAX_NATURAL_COEFFICIENT
print(MILLI_SCALE, MAX_NATURAL_COEFFICIENT)
# 1000 2147483
```

`SolverQuip` computes spin coefficients from the model, multiplies
each by `MILLI_SCALE`, and rounds to the nearest integer milli value,
ties to even. A coefficient finer than `1/1000` is therefore rounded,
not refused. Each rounding moves a coefficient by at most `0.0005`.
`SolverQuip` reports the largest such error once per solver instance as
a `warnings.warn` when the model is placed (by `create_order()`, a
`set()` that places it again, or `solve()`), and on every result as
`metadata["quantization_error"]`. A coefficient whose rounded milli
value would overflow `i32` still raises `EncodingError`. An energy
ceiling (`max_energy`) on a rounded model warns as well, because the
chain applies the ceiling to the rounded model; [Energy and
Precision](energy-and-precision.md) explains the ceiling's units.

Rounding changes only the model the miners search. `result.energy` is
recomputed on your original model, so a returned sample is never scored
against the rounded coefficients. The winning submission is still
chosen by the chain's energy on the rounded model; only the vectors
inside it are re-scored on yours. `quantization_error` bounds each
coefficient, not the energy: a sample's energy can move by up to
`quantization_error` times the number of nonzero terms. A model whose
coefficients all round to zero raises `EncodingError` rather than
proposing an empty order. A BINARY model with ordinary
three-decimal weights is the common case: the basis change below
divides each quadratic weight by 4, so `1.5` on `x_0` and `0.001` on
`x_0 x_1` give a spin field of `0.75025`, which encodes as `750`.

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

A job costs two amounts, both in planck, the chain's smallest
denomination:

- **The reward**: the order's `reward`, else the client's `reward`, else
  `QUIP_REWARD`, else the chain's `MinReward`. A reward below
  `MinReward` raises `QuipOrderOptionError` before anything is signed.
  `propose_job` reserves the reward from the caller's account. A solved
  order's reward is allocated to its winning solvers on chain and paid
  when one of them claims it; a receipt's `settlement()` shows the
  split. An order that finalizes with no solutions can have its reward
  reclaimed, and `solve()` reclaims it itself, so a failed job costs the
  fee, not the reward.
- **The transaction fee** for `propose_job`, burned either way.

`create_order(model).quote()` prices an order without proposing it. It
signs the `propose_job` extrinsic the order would submit, asks the chain
for its fee through `payment_queryInfo`, and reads the account balance.
Nothing is submitted and no nonce is consumed. `submit()` later sends
the same signed extrinsic, re-signing only if the account's nonce moved
in between. `quote()` returns a `JobQuote`:

| Field | Meaning |
|---|---|
| `network` | The preset name, or `None` for an explicit `url` |
| `account` | The paying account, in SS58 form |
| `quoted_at_block` | The block the quote was read at |
| `num_variables` | The model's variables |
| `num_spins`, `num_couplings` | The spins and couplings the model was placed onto |
| `placement` | `"native"`, or the topology hash the model was placed on |
| `reward_planck` | The reward the job reserves |
| `fee_planck` | The transaction fee |
| `fee_exact` | `True` when the fee is the chain's answer; `False` when that query failed and a fixed headroom stands in for it |
| `balance_planck` | The account's free balance |
| `token_symbol`, `token_decimals` | The chain's token, used to render amounts |
| `total_planck` | `reward_planck + fee_planck`: what the account must hold |
| `shortfall_planck` | How far the balance falls short of the total, or `0` |
| `affordable` | Whether the balance covers the total |

`str(quote)` renders a 72-column box: the network, account and block;
the problem and where it was placed; the reward, fee and total in token
units; then the balance, the shortfall and whether the account can
afford the job. `submit()` and `solve()` build the same quote before
proposing, log it at `INFO` on the `xqsa.quip` logger (also printing it
to stderr when stderr is a terminal), and pass it to the `autoconfirm`
and `autofund` gates. A field the solver could not read is left out of
the box.

What each part of the price measures:

- **The fee is metered.** It comes from benchmarked weights plus the
  encoded length, so it grows with the model's nodes and edges. It is
  small next to the reward. Fees are
  [burned, not paid to the block author](https://gitlab.com/quip.network/quip-validator/-/blob/161667b2ed7e375390ada523a459258f0f576f78/runtime/src/configs/mod.rs#L348), and do not respond
  to congestion: the runtime's
  [fee multiplier is constant](https://gitlab.com/quip.network/quip-validator/-/blob/161667b2ed7e375390ada523a459258f0f576f78/runtime/src/configs/mod.rs#L343-352).
- **The reward is arbitrary.** `MinReward` is a round
  [runtime constant](https://gitlab.com/quip.network/quip-validator/-/blob/161667b2ed7e375390ada523a459258f0f576f78/runtime/src/configs/mod.rs#L558) that gates submissions. Nothing derives
  it from the cost of solving, yet it is nearly all of what a job
  costs.
- **Solver compute is not metered.** The runtime declares
  [`type VM = NoOpVm`](https://gitlab.com/quip.network/quip-validator/-/blob/161667b2ed7e375390ada523a459258f0f576f78/runtime/src/configs/mod.rs#L650), so nothing on-chain executes or
  measures the miner's work, and that work never enters the price.
- **The token is subsidised.** On `aglais`, AGLS comes free from the
  faucet and has no external price. `autofund` draws from the faucet for
  a short account, and `fund_from_faucet(dest, url=...)` requests one
  drip directly.

This page does not print a current figure, because every one of these
amounts is live chain state; `JobOrder.quote()` reads it. For a dated figure
from an observed job, see [Your First Quip Job](quip-first-job.md#what-the-figure-means).

## Metadata

`SolverResult.metadata` for `quip` carries nine keys, eight read from
the order and one from the encoding:

| Key | Type | Meaning |
|---|---|---|
| `order_id` | `int` | The on-chain order id; `get_receipt(order_id)` rebuilds the order's receipt in any session. |
| `solver` | `str` | The solver account whose vector is returned; always present. |
| `best_energy_milli` | `int` | The leader's ranked energy on chain, in milli units. When a fallback fires, the chosen solver's stored best. |
| `energy_matches_chain` | `bool` | Whether any stored vector of the chosen solver reaches the chain's energy. `False` means a fallback returned a different vector, not necessarily an encoding fault. |
| `leader_read` | `bool` | Whether the chain's ranking of the order was read. |
| `leader` | `bool` | Whether the sample is the leader's: the solver the chain pays. |
| `num_submissions` | `int` | How many solvers have a stored answer; a solver's resubmission overwrites its earlier one. |
| `num_solutions` | `int` | How many vectors the chain stores for the returned sample's solver. |
| `quantization_error` | `float` | The largest absolute error, in natural units, from rounding a spin coefficient to the milli grid. `0.0` when every coefficient was already on it; at most `0.0005`. |

`best_energy_milli` is milli-scale (see [Coefficient
Encoding](#coefficient-encoding)) and is not directly comparable to
`result.energy`, the authoritative natural-scale energy recomputed on
your model; diffing the two without converting scale first will make a
correct result look wrong.

`metadata["solver"]` here names the solver account that answered, not a
piece of hardware -- [D-Wave QPU](dwave-qpu.md#how-a-qpu-result-differs)
uses the same key for the physical Advantage system that ran the job, so
the same key names a different kind of thing depending on the backend.

`solve()` returns the leader's best vector, the answer the chain pays
for, rather than the best vector any solver stored. `leader`,
`leader_read` and `energy_matches_chain` say whether that rule held or a
fallback chose another vector; [Reading the
answer](quip-orders.md#reading-the-answer) describes the rule and both
fallbacks.

## Failure Taxonomy

Fifteen exception classes cover this backend, all exported from
`xqsa.quip` and `xquad.sa.quip` -- one base class and fourteen concrete
failures, spanning local encoding checks, order checks, the consent and
funding gates, and network-dependent lifecycle failures:

| Exception | Raised when |
|---|---|
| `QuipError` | Base class for every error below |
| `EncodingError` | The model is not `MODEL`-mode, its domain is unsupported, or both its `linear` and `quadratic` dicts are empty. Also covers a NaN coefficient, a model whose coefficients all round to zero at milli scale, a coefficient whose rounded milli value overflows `i32` (see [Coefficient Encoding](#coefficient-encoding)), and a native order over the mempool's `MaxNodes` or `MaxEdges` |
| `PlacementError` | The model's coupling graph is not a subgraph of the target topology. Raised in default mode only; see [Native topology mode](#native-topology-mode) |
| `QuipSigningError` | Extrinsic assembly, keystore handling, or submission fails |
| `QuipConnectionError` | The node is unreachable; a configured Ising spec or topology is not registered on-chain; a mempool constant cannot be read (`MinReward`, `MaxDeadlineBlocks`, `MaxBlockWait`, `MaxSolutions` and `MaxBidMiners` at construction, `MaxNodes` and `MaxEdges` per native order); the chain defines no `MinReward` and no reward was given; an order id names no order; or a balance, nonce, block, timestamp or fee read fails. Most come from the constructor or `for_network()`, which read the limits once |
| `QuipMetadataError` | The node's runtime metadata cannot be decoded by this client |
| `QuipSubmissionError` | An extrinsic cannot be submitted or the chain rejects it. Raised when `propose_job` fails on chain (the fee was paid, no order was placed, and the order moves to `failed`), and when the account's nonce moves twice while submitting because another client is sending from the account. Also raised before proposing when the account cannot cover the quote and `autofund` cannot help: no faucet is configured, the shortfall is larger than one drip, or the balance already exceeds one drip |
| `QuipOrderOptionError` | A `QuipSubmissionError`: an order option fails a check before signing, such as a reward below `MinReward`, a deadline or block wait out of range, a bad payout or access rule, or a quality floor out of range. Carries the option's name as `option`. Nothing is spent |
| `QuipReclaimRefusedError` | A `QuipSubmissionError`: `reclaim()` refuses before signing because another account proposed the order, it is already closed, it is still open, or it was answered; or the chain refuses the reclaim and charges the fee |
| `QuipUnconfirmedError` | A `QuipSubmissionError`: `propose_job` was sent but its outcome or order id is unknown. The order may be on chain, so it is never resent; the order moves to `unconfirmed`. Carries `extrinsic_hash` and `block_hash` (`None` when not seen in a block) |
| `QuipCancelledError` | The `autoconfirm` or `autofund` gate declines the job, at the prompt, by callable, or for want of a terminal; carries the declined `JobQuote` as `quote`. Nothing has been proposed or funded |
| `QuipFaucetError` | The faucet refuses a drip or cannot be reached; carries the HTTP `status` (`None` for a transport failure) and the parsed JSON `body` |
| `QuipTopologyError` | Retained for compatibility; nothing raises it. It reported a topology absent from `MineableTopologies`, which does not gate the mempool |
| `QuipTimeoutError` | An order does not reach finality before the configured timeout. Carries `order_id` and, when a wait raised it, the order's `receipt`, so the caller can call `exc.receipt.wait()` again; another session rebuilds the receipt with `get_receipt(order_id)` |
| `QuipJobFailedError` | A final order has no usable solution; carries `order_id`. From `solve()`, the reward of an unanswered order was reclaimed first, and the message says whether the reclaim succeeded; it also names any quality floors or allowlist that may explain why nobody answered. `JobOrder.best()` raises it without reclaiming |

The `EncodingError` emptiness check tests the dicts themselves, not the
values inside them -- but `XQMX.set_linear()`
/ `set_quadratic()` pop a key the moment its value reaches `0`
(`xqvm_py/xqmx.py:175-226`), so for any model built through the normal
API, "has no terms" and "carries only zero terms" are the same
condition in practice. A model constructed directly as a dataclass,
bypassing those setters, can hold explicit zero entries that leave the
dicts non-empty; that model passes the `EncodingError` check and
submits all-zero coefficient arrays.

`EncodingError` and `PlacementError` can be raised entirely locally,
before anything touches the network -- they come from
`xqsa.quip.codec`, the same module the coefficient encoding above uses.
The one exception is the native size check, which reads `MaxNodes` and
`MaxEdges` from the chain first; a failed read raises
`QuipConnectionError`. `QuipOrderOptionError` and `QuipCancelledError`
are also raised before anything is signed or proposed. The rest depend
on chain state or the round trip completing.

## Deprecated in v0.4.3

Each of these warns with a `DeprecationWarning` and is removed in
v0.5.0:

| Deprecated | Use instead | Notes |
|---|---|---|
| `SolverQuip.quote(model, ...)` | `solver.create_order(model, ...).quote()` | |
| `SolverQuip.status(order_id)` | `solver.get_receipt(order_id).status()` | The keys differ: `state` and `chain_status` replace `status` and `is_final` |
| `SolverQuip.query(order_id, model, ...)` | `solver.get_receipt(order_id)` to follow an order; `JobOrder.wait()` and `best()` in the session that submitted it | Still reclaims an unanswered order; see below |
| `mode=` on the constructor, `for_network()`, `quote()` and `solve()` | `access=` | |
| A raw chain dict for `access` or `resolution` on `solve()` or `quote()` | `AccessOpen()`, `AccessAllow(...)`, or a `Resolution` rule | |
| An unknown keyword on `solve()` or `quote()`, including `delivery=` and `spec_id=` | None; the option is dropped | |
| `xqsa.quip_codec`, `quip_signing`, `quip_metadata`, `quip_faucet`, `quip_networks` | `xqsa.quip.codec`, `.signing`, `.metadata`, `.faucet`, `.networks` | Warns on the first import only |
| The 27 Quip names on `xqsa` and `xquad.sa`, such as `xqsa.SolverQuip` | The same names on `xqsa.quip` or `xquad.sa.quip` | They left both `__all__` lists, so `from xqsa import *` no longer brings them in |

`solve()` is not deprecated, and neither is a typed `resolution=` on
`solve()`. The strict paths refuse what the deprecated ones accept:
`create_order()` and `JobOrder.set()` raise `TypeError` for `mode=`, a
raw chain dict, or an unknown option.

`query()` has no full replacement yet. A receipt follows an order on
chain but cannot decode its answers onto your model, so from a session
other than the one that submitted the order,
`query(order_id, model, ...)` is still the only way to get a decoded
`SolverResult`.

Python shows a `DeprecationWarning` by default only when the deprecated
call is made from `__main__`: a script sees it, but a library calling
the same API does not. Run with `-W default::DeprecationWarning` to see
every one; each points at the calling line.

## Gaps

[Your First Quip Job](quip-first-job.md) now walks a funded job end to
end with the numbers one run produced. What stays open is that those
numbers move:

- **`MinReward` is a runtime constant**, not storage governance can
  set. It changes only with a runtime upgrade, with no extrinsic and no
  event. `SolverQuip` reads it live, so the toolchain follows a change;
  prose does not.
- **The fee weights are benchmark output.** Re-running the runtime's
  benchmarks moves the fee for the same model.
- **Congestion is not a cause.** The fee multiplier is constant, so
  for a given model and the default reward, only an upgrade moves
  either figure. `JobOrder.quote()` is the only reading to trust at
  submission time.
- **Timing and fleet behaviour are unmeasured.** How long a job takes
  and how many miners answer it depend on the fleet at that moment; the
  tutorial reports one run, not a distribution.
