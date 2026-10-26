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

Both the chain RPC connection over `wss://` and the faucet request
verify the certificate chain, host name and validity dates, and by
default neither checks revocation (no CRL, no OCSP). To check
revocation lists, pass an `ssl.SSLContext` as `ssl_context` to
`SolverQuip`, `SolverQuip.for_network` or `fund_from_faucet`. Both
clients then use it in place of the default CA-bundle context,
reconnects and `autofund` included; it is ignored for `ws://` and
`http://` URLs, and `SolverQuip` refuses it with an `https://` RPC URL,
whose transport cannot use it. The context replaces the default
entirely, so it must carry its own trusted roots: on a macOS Python whose
trust store is empty, load certifi's bundle as below. A
standard-library context checks only revocation lists you fetch and
load yourself. With `VERIFY_CRL_CHECK_LEAF`, as below, it needs the list
from each server certificate's issuer; with `VERIFY_CRL_CHECK_CHAIN`,
one for every certificate in each chain. One context serves both
servers, so with `autofund` on, load the lists for the faucet's issuer
as well as the RPC node's. A handshake missing a list it needs fails.

```python
import ssl
import certifi
from xqsa.quip import SolverQuip

ctx = ssl.create_default_context(cafile=certifi.where())
ctx.load_verify_locations(cafile="issuer-crl.pem")  # a PEM CRL you fetched
ctx.verify_flags |= ssl.VERIFY_CRL_CHECK_LEAF
solver = SolverQuip.for_network("aglais", ssl_context=ctx)
```

A standard-library context cannot check OCSP, which needs a different
TLS stack that fails closed when the check does not finish. Code that
opens its own chain connection with `connect` in `xqsa.quip.metadata`
passes a context through `ws_options["sslopt"]["context"]` instead.

Configuration resolves from constructor arguments first, then
environment variables:

| Argument | Environment variable | Meaning |
|---|---|---|
| `url` | `QUIP_RPC_URL` | Websocket RPC endpoint |
| `seed` | `QUIP_SIGNER_SEED` | 32-byte hex master seed |
| `keystore` | `QUIP_KEYSTORE` | Keystore file path (loaded, or created on first use). Unset, the account preference or `~/.quip/keystore.json` applies; see [Accounts](#accounts) |
| `reward` | `QUIP_REWARD` | Reward in planck; falls back to the chain's `MinReward` |
| `faucet` | `QUIP_FAUCET_URL` | Faucet base URL used to top up a short account when `autofund` allows it. The variable is read only when the RPC URL also comes from `QUIP_RPC_URL`, so an explicit `url` never picks up a faucet for another chain |
| `autoconfirm` | `QUIP_AUTOCONFIRM` | Whether to submit without asking. `True` (the default) submits; `False` asks on the terminal (stdin, and stderr or stdout) and raises `QuipCancelledError` without one, a notebook included; a callable judges the job's `JobQuote`. The variable takes `1/true/yes/on` or `0/false/no/off` |
| `access` | none | Who may answer: `"Open"` or `AccessOpen()` (the default), or `AccessAllow(accounts=[...], types=[...])` |
| `resolution` | none | The payout rule: `"SingleBest"` (the default), `ResolutionSingleBest()`, `ResolutionTopNEqual(n)` or `ResolutionTopNWeighted(n)` |
| `delivery` | none | How results are delivered; only `"OnChainOnly"` (the default) is accepted |
| `deadline_blocks` | none | Blocks an order stays open; defaults to `100` |
| `block_wait` | none | Blocks an order keeps accepting answers after the first one arrives; defaults to `10` |
| `autofund` | `QUIP_AUTOFUND` | Whether to draw one drip from `faucet` when the account cannot cover the quoted job; a shortfall larger than one drip, or an account already holding more than one drip, raises instead. Same forms and default as `autoconfirm` |
| `inclusion_window_blocks` | none | Blocks a signed proposal stays valid for, from the finalized block it was signed at: 64 to 2048, rounded up to a power of two. Defaults to 64, `DEFAULT_INCLUSION_WINDOW_BLOCKS`. A client setting, not an order option; see [Settling a send](quip-orders.md#settling-a-send) |
| `ssl_context` | none | An `ssl.SSLContext` used for both the `wss://` RPC connection and the faucet request in place of the default CA-bundle context; ignored for `ws://` and `http://` URLs, refused with an `https://` RPC URL. See the revocation note above |

The signer comes from the first of these that is set: `seed`,
`keystore`, `QUIP_SIGNER_SEED`, `QUIP_KEYSTORE`, the project account
preference, the user account preference, then `~/.quip/keystore.json`,
created on first use. An explicit argument wins over the environment, so
a `keystore` argument wins over an exported `QUIP_SIGNER_SEED`.
`spec_id` and
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
`"OnChainOnly"`: the client has no subscriber for the `ResultReady`
event the pallet's `Callback` delivery emits when the order expires.

Before connecting, the constructor raises `ValueError` for a `delivery`
other than `"OnChainOnly"`, and for an `access` or `resolution` that is
not one of the rules above. After connecting, it checks the default
`access` against the chain's allowlist limits. The remaining options are
checked against the chain's limits when an order is created.

### Accounts

A keystore is an account: the file holds its 32-byte master seed in
plain text, so treat it as the account itself and keep it out of
version control. Each account level holds keystore files and an account
preference:

| Level | Keystore files | Preference |
|---|---|---|
| Project | `.quip/keystores/<name>.json` | `.quip/config.toml` |
| User | `~/.quip/keystores/<name>.json`, plus `~/.quip/keystore.json` as the account `default` | `~/.quip/config.toml` |

The project level is the nearest `.quip/` directory found walking up
from the working directory, stopping below the home directory, so
`~/.quip/` is never a project. A `.quip/` owned by another user or
writable by everyone is skipped, so a shared directory such as `/tmp`
cannot choose the signer. A preference is the single line
`account = "<name>"`. A project preference may name a project or a user
keystore, and a name present at both levels resolves to the project one;
a user preference applies in every directory, so it names a user
keystore. Account names are lowercase letters, digits, `_` and `-`, at
most 64 characters, starting with a letter or digit.

`xqsa.quip.accounts` manages them. Each call takes the level explicitly:

```python
from xqsa.quip import create_account, resolve_accounts, use_account

create_account("work", level="user")
use_account("work", level="project")
print(resolve_accounts().in_effect)
```

`create_account` writes a keystore from a fresh seed, or from `seed=`,
and refuses to replace one unless `overwrite=True`; with no name it
writes `default`. At project level with no `.quip/` found, it makes one
in the working directory, and anything created under a project `.quip/`
also writes a `.quip/.gitignore` of `*` when there is none.
`use_account` and `clear_account` set and remove a preference.
`resolve_accounts` lists every keystore with its account and walks the
signer order above, reporting the step in effect. Each step is a
`SignerSource`, whose `account_id` is the 32-byte account of the
keystore it points at, read from the account the keystore caches, so no
seed is loaded; it is `None` for the seed steps and for a missing or
unusable keystore. `resolve_accounts` never writes a file
or reads a seed, so an unusable keystore or a broken preference is
listed with the problem rather than raised. `SolverQuip` raises
`QuipSigningError` only when such a step is the one it would sign with.

For reads that need no signer, pass `read_only=True`:

```python
solver = SolverQuip.for_network("aglais", read_only=True)
print(solver.balance(account=address))  # SS58 text, 0x hex or bytes
```

A read-only client resolves no signer, so it never creates a keystore,
and refuses `seed` and `keystore`. `get_receipt`, and `list_orders`,
`balance`, `reserved` and `order_slots` given `account=`, work on it.
Everything that signs, `account`, and those four without an account
raise `QuipSigningError`.

A signing client reads its own account the same way:

```python
solver = SolverQuip.for_network("aglais")
print(solver.account)
print(solver.balance(), solver.reserved(), solver.order_slots())
```

```text
5FXRydTJYE4cPGBH9CSf3dyGvaJNnYATEZJMyFqMiLPHv9oc
0 0 (0, 32)
```

`account` is the signer's account in SS58. `balance()` is the free
balance in planck, which an order's reward and fee are paid from.
`reserved()` is the reserved balance: the chain reserves an order's
reward when it is proposed and releases it when a claim or a reclaim
closes the order, and it includes anything else the runtime reserves.
`order_slots()` returns `(used, limit)`: the orders not yet closed, each
holding a slot until a claim or a reclaim closes it, and the runtime's
`MaxOrdersPerProposer`, `None` when the runtime sets no cap. Each takes
`account=` to read another account. `xquad quip account show` prints
all four; see [Accounts](../cli/quip/accounts.md).
(Captured on 2026-10-26 with a fresh, unfunded account.)

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
connection fails. The preset also names its network's faucet, if it
has one, which the solver uses unless `faucet=` names another;
`QUIP_FAUCET_URL` does not override a preset.

A preset may also pin its network's genesis hash and may have no
faucet. With a pinned genesis, `for_network()` refuses a node that
serves another chain with a `QuipConnectionError` naming both hashes.
With no faucet, an account that cannot cover a quote is refused with a
`QuipSubmissionError` that says `<network> has no faucet; fund the
account another way.` Neither current preset does either: both name a
faucet, and both leave the genesis open, because a test network
relaunches with a new genesis. The Quip MainNet preset will pin its
genesis and have no faucet.

### Runtime compatibility

`SolverQuip` reads the runtime's `specVersion` once, when it connects; a
client that stays connected across an upgrade keeps the version it read
until it reconnects. Runtime 119 renamed the order's energy-ceiling
field from `min_energy_milli` to `max_energy_milli`. xquad 0.4.5 and
later send the ceiling as `max_energy_milli` only, so they need runtime
119 or later: on an older runtime the proposal fails to encode. xquad
0.4.2 and earlier cannot propose on 119.

`ResolutionTopNWeighted` needs runtime 119 or later. On an older runtime
every order that uses it is refused before signing, because older
runtimes weight the split by absolute energy, so a worse answer can take
the larger share.

Moving a network to runtime 119 starts it afresh instead of upgrading it
in place. Orders placed before the move do not carry over, so an order
id recorded before it does not name the same order after it. `aglais`
has run runtime 119 since its relaunch on 2026-10-15.

The signed-extension order comes from the chain's metadata, not from
a list in the client. At its first connect, `SolverQuip` checks that it
can encode the extrinsic envelope and every signed extension the
runtime declares, and every signature is checked against the runtime
the same way. A runtime it cannot read or sign for raises
`QuipMetadataError`: at connect, or at signing when the runtime
changed under a live client, before anything is sent. The remedy is
upgrading xquad; retrying the same node gives the same answer.

Every reconnect must reach the chain the first connection reached: a
node that serves another genesis is refused with a
`QuipConnectionError` naming both hashes. A reconnect checks the genesis
only, and fails with `QuipMetadataError` only for a node whose metadata
this client cannot read. A `submit()` that is settling a send stops at
once on either fault and leaves the order `sending` (see [Settling a
send](quip-orders.md#settling-a-send)).

Each connection logs the network, runtime and genesis at `INFO` on the
`xqsa.quip` logger, with `reconnected to` in place of `connected to` on
a reconnect (captured on 2026-10-26):

```text
connected to Quip aglais at wss://bootnode-1.aglais.quip.network:20049/rpc: specVersion 119, transactionVersion 7, genesis 0x542a9915acf840b8940d35ca6de242f669a0c6e3b40f9a8d8c14b9bf7b8ebecb
```

## Job Lifecycle

Submitting an order calls `propose_job`, which reserves the full reward
and emits a `JobProposed` event. Miners discover work **only** from that
live event stream -- there is no storage backfill, so a job proposed
before a miner subscribed stays invisible to it. A miner that accepts
solves it on its own hardware with its own algorithm and submits a
solution.

The chain marks an order `Expired` at its effective expiry, or a few
blocks later when many orders expire at once, and only a claim or a
reclaim closes it. `SolverQuip` computes finality itself from block
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
in between or half its inclusion window has passed (see [Settling a
send](quip-orders.md#settling-a-send)). `quote()` returns a `JobQuote`:

| Field | Meaning |
|---|---|
| `network` | The preset name, or `None` for an explicit `url` |
| `account` | The paying account, in SS58 form |
| `quoted_at_block` | The block the quote was read at |
| `inclusion_window_blocks` | Blocks the signed proposal stays valid for; see [Settling a send](quip-orders.md#settling-a-send) |
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
| `best_energy_milli` | `int` | The leader's ranked energy on chain, in milli units. It stays the leader's even when a stored-energy mismatch makes `solve()` return another solver's vector; only when the ranking cannot be read is it the chosen solver's stored best. |
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
records the physical Advantage system that ran the job under
`metadata["params"]["solver"]`, so the same name refers to a different
kind of thing depending on the backend.

`solve()` returns the leader's best vector, the answer the chain pays
for, rather than the best vector any solver stored. `leader`,
`leader_read` and `energy_matches_chain` say whether that rule held or a
fallback chose another vector; [Reading the
answer](quip-orders.md#reading-the-answer) describes the rule and both
fallbacks.

## Failure Taxonomy

Sixteen exception classes cover this backend -- one base class and
fifteen concrete failures, spanning local encoding checks, order checks,
the consent and funding gates, and network-dependent lifecycle failures.
Fifteen are exported from `xqsa.quip` and `xquad.sa.quip`; the
deprecated `QuipUnconfirmedError` still imports from both, with a
warning, but `import *` does not bring it in:

| Exception | Raised when |
|---|---|
| `QuipError` | Base class for every error below |
| `EncodingError` | The model is not an `XqmxModel`, its domain is unsupported, or it has no nonzero linear or quadratic term. Also covers a coefficient whose milli value overflows `i32` (see [Coefficient Encoding](#coefficient-encoding)), and a native order over the mempool's `MaxNodes` or `MaxEdges` |
| `PlacementError` | The model's coupling graph is not a subgraph of the target topology. Raised in default mode only; see [Native topology mode](#native-topology-mode) |
| `QuipSigningError` | Extrinsic assembly, keystore handling, or submission fails |
| `QuipConnectionError` | The node is unreachable; a configured Ising spec or topology is not registered on-chain; a mempool constant cannot be read (`MinReward`, `MaxDeadlineBlocks`, `MaxBlockWait`, `MaxSolutions` and `MaxBidMiners` at construction, `MaxNodes` and `MaxEdges` per native order); the chain defines no `MinReward` and no reward was given; an order id names no order; a balance, nonce or block read fails; or the node serves another chain than the preset pins, named by both genesis hashes. A reconnect to another chain than the first connection reached raises it too, but that happens only while a send is settling, so it reaches the caller as the `__cause__` of `QuipSubmitIncompleteError`. Most come from the constructor or `for_network()`, which read the limits once |
| `QuipMetadataError` | This client cannot read or sign for the node's runtime: its metadata, an extrinsic envelope or signed extension it cannot encode, or blocks or events it cannot decode while settling a send. Raised at connect, on a reconnect whose metadata it cannot read, and when `quote()` or `submit()` signs; while a send is settling it is the `__cause__` of `QuipSubmitIncompleteError` instead. The remedy is upgrading xquad, not retrying |
| `QuipSubmissionError` | An extrinsic cannot be submitted or the chain rejects it. Raised when `propose_job` fails on chain (the fee was paid, no order was placed, and the order moves to `failed`), and when the account's nonce moves twice while submitting because another client is sending from the account. Also raised before proposing when the account cannot cover the quote and `autofund` cannot help, with a hint for each case: a named network with no faucet says to fund the account another way, a raw `url=` with none says how to name one, a shortfall larger than one drip suggests checking `reward=`, and a balance already above one drip is one the faucet will not top up. Also raised when a sent proposal certainly never reached the chain: the order stays a draft |
| `QuipOrderOptionError` | A `QuipSubmissionError`: an order option fails a check before signing, such as a reward below `MinReward`, a deadline or block wait out of range, a bad payout or access rule, or a quality floor out of range. Carries the option's name as `option`. Nothing is spent |
| `QuipReclaimRefusedError` | A `QuipSubmissionError`: `reclaim()` refuses before signing because another account proposed the order, it is already closed, it is still open, or it was answered; or the chain refuses the reclaim and charges the fee |
| `QuipSubmitIncompleteError` | A `QuipSubmissionError`: a sent `propose_job` could not be settled by the end of its window; or settling stopped at once, because its block or events could not be decoded, a reconnect reached another chain or a node whose metadata this client cannot read, or the node discarded the state it needs. The order is `sending`; calling `submit()` again resumes it and never places a second order. Carries `order`, `extrinsic_hash`, `nonce` and `window_end`, with the cause as `__cause__`; see [Settling a send](quip-orders.md#settling-a-send) |
| `QuipUnconfirmedError` | Deprecated, and no longer raised since v0.4.5; importing it from `xqsa.quip` warns. A `QuipSubmissionError` that carried `extrinsic_hash` and `block_hash` for a send whose outcome was unknown; `QuipSubmitIncompleteError` replaces it |
| `QuipCancelledError` | The `autoconfirm` or `autofund` gate declines the job, at the prompt, by callable, or for want of a terminal; carries the declined `JobQuote` as `quote`. Nothing has been proposed or funded |
| `QuipFaucetError` | The faucet refuses a drip or cannot be reached; carries the HTTP `status` (`None` for a transport failure) and the parsed JSON `body` |
| `QuipTopologyError` | Retained for compatibility; nothing raises it. It reported a topology absent from `MineableTopologies`, which does not gate the mempool |
| `QuipTimeoutError` | An order does not reach finality before the configured timeout. Carries `order_id` and, when a wait raised it, the order's `receipt`, so the caller can call `exc.receipt.wait()` again; another session rebuilds the receipt with `get_receipt(order_id)` |
| `QuipJobFailedError` | A final order has no usable solution; carries `order_id`. From `solve()`, the reward of an unanswered order was reclaimed first, and the message says whether the reclaim succeeded; it also names any quality floors or allowlist that may explain why nobody answered. `JobOrder.best()` raises it without reclaiming |

The `EncodingError` emptiness check tests whether `linear_items()` and
`quadratic_items()` are both empty. Both list
nonzero terms only -- an `XqmxModel` drops a term the moment its
coefficient returns to `0` -- so "has no terms" and "carries only zero
terms" are the same condition, and an all-zero model never reaches the
chain.

`EncodingError` and `PlacementError` can be raised entirely locally,
before anything touches the network -- they come from
`xqsa.quip.codec`, the same module the coefficient encoding above uses.
The one exception is the native size check, which reads `MaxNodes` and
`MaxEdges` from the chain first; a failed read raises
`QuipConnectionError`. `QuipOrderOptionError` and `QuipCancelledError`
are also raised before anything is signed or proposed. The rest depend
on chain state or the round trip completing.

## Deprecated APIs

Each of these warns with a `DeprecationWarning` and is removed in
v0.5.0:

| Deprecated | Since | Use instead | Notes |
|---|---|---|---|
| `SolverQuip.quote(model, ...)` | v0.4.3 | `solver.create_order(model, ...).quote()` | |
| `SolverQuip.status(order_id)` | v0.4.3 | `solver.get_receipt(order_id).status()` | The keys differ: `state` and `chain_status` replace `status` and `is_final` |
| `SolverQuip.query(order_id, model, ...)` | v0.4.3 | `solver.get_receipt(order_id)` to follow an order; `JobOrder.wait()` and `best()` in the session that submitted it | Still reclaims an unanswered order; see below |
| `mode=` on the constructor, `for_network()`, `quote()` and `solve()` | v0.4.3 | `access=` | |
| A raw chain dict for `access` or `resolution` on `solve()` or `quote()` | v0.4.3 | `AccessOpen()`, `AccessAllow(...)`, or a `Resolution` rule | |
| An unknown keyword on `solve()` or `quote()`, including `delivery=` and `spec_id=` | v0.4.3 | None; the option is dropped | |
| `xqsa.quip_codec`, `quip_signing`, `quip_metadata`, `quip_faucet`, `quip_networks` | v0.4.3 | `xqsa.quip.codec`, `.signing`, `.metadata`, `.faucet`, `.networks` | Warns on the first import only |
| The 27 Quip names on `xqsa` and `xquad.sa`, such as `xqsa.SolverQuip` | v0.4.3 | The same names on `xqsa.quip` or `xquad.sa.quip` | They left both `__all__` lists, so `from xqsa import *` no longer brings them in |
| `QuipUnconfirmedError` | v0.4.5 | `QuipSubmitIncompleteError` | No longer raised; warns on import from `xqsa.quip` |
| Loading a pickled `unconfirmed` order | v0.4.5 | None | Orders pickled by 0.4.3 or 0.4.4 in that state still load, warn, and are never sent again; look up the extrinsic the warning names |
| `xqsa.quip.signing.SIGNED_EXTENSIONS` | v0.4.5 | None | The signed-extension order now comes from the runtime metadata |
| The top-level commands `xquad asm`, `dism`, `run` and `verify` | v0.4.5 | `xquad vm asm`, `dis`, `run` and `check` | Print a warning line to stderr rather than a `DeprecationWarning`; see [xquad vm](../cli/vm/) |

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
