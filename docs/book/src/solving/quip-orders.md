# Job Orders

`SolverQuip.solve()` proposes a job, waits for it and decodes the answer
in one call. `create_order()` splits that call into steps: it returns a
draft `JobOrder` that you can inspect, change, price and submit, and a
submitted order leaves a receipt that follows it on chain from any
session. Every option is checked against the chain's limits before
anything is signed, so a refused option costs nothing.

This page covers the order surface. [Quip Network](quip-network.md)
covers installation, configuration, encoding, cost and the error
classes, and [Your First Quip Job](quip-first-job.md) walks one order
through end to end. The examples import from `xquad.sa.quip`;
`xqsa.quip` exports the same names. Every output on this page was
captured on `aglais` at `specVersion 118` on 2026-10-12, from the
16-variable TSP model the tutorial builds, unless it says otherwise.

## Orders

```python
from xquad.sa.quip import SolverQuip

solver = SolverQuip.for_network("aglais", topology="native")
order = solver.create_order(model, deadline_blocks=200)
print(order)
print(order.quote())
order.submit().wait()
print(order.receipt())
```

`create_order(model, **options)` places the model and returns a draft.
It takes ten options. The first six default to the client's values (see
[Installation and
Configuration](quip-network.md#installation-and-configuration));
`mapping` and the three quality floors exist per order only:

| Option | Default | Meaning |
|---|---|---|
| `reward` | The client's `reward`, else `QUIP_REWARD`, else `MinReward` | Planck reserved at proposal and paid to the winners |
| `access` | `AccessOpen()` | Who may answer; see [Access](#access) |
| `resolution` | `"SingleBest"` | How the reward is split; see [Payout rules](#payout-rules) |
| `deadline_blocks` | `100` | Blocks the order stays open, from 10 to `MaxDeadlineBlocks` |
| `block_wait` | `10` | Blocks the order keeps accepting answers after the first, from 0 to `MaxBlockWait` |
| `topology` | The client's topology | A registered topology hash, or `"native"` |
| `mapping` | None | An explicit variable-to-node placement; not allowed with `"native"` |
| `max_energy`, `min_solutions`, `min_diversity` | None | See [Quality floors](#quality-floors) |

Any other keyword raises `TypeError`, including `mode=` (deprecated) and
`delivery=` (client-only):

```text
TypeError: unknown order option 'mode'; expected one of access, block_wait, deadline_blocks, mapping, max_energy, min_diversity, min_solutions, resolution, reward, topology
```

`reward`, `deadline_blocks` and `block_wait` must be integers
(`TypeError: reward must be an int, not str`). A raw chain dict for
`access` or `resolution` raises
`TypeError: access: raw chain values are not accepted`.

### States

An order is in one of five states. `status()["state"]` reports it, and
the order box shows it in its title.

| State | Meaning |
|---|---|
| `draft` | Built by `create_order()`; nothing is on chain. Only a draft accepts `set()`, `quote()` and `submit()` |
| `submitted` | Proposed and included; the order has an id and a receipt |
| `finalized` | The chain head reached the order's effective expiry (see [Job Lifecycle](quip-network.md#job-lifecycle)); `wait()` and `status()` move a submitted order here |
| `failed` | `propose_job` was included but its dispatch failed: the fee was paid and no order was placed |
| `unconfirmed` | `propose_job` was sent, but its outcome or order id is unknown. The order may be on chain, so it is never resent |

Calling `set()`, `quote()` or `submit()` on anything but a draft raises
`QuipSubmissionError`:

```text
QuipSubmissionError: order 325 is submitted; set() works only on a draft order
```

### Changing a draft

`set(**options)` takes the same options as `create_order()`. It re-runs
every check over the whole configuration and leaves the order unchanged
if any check refuses, so it also repeats the warnings for options you
did not touch. It places the model again only when `topology` or
`mapping` changes. Any change drops the cached signature, so the next
`quote()` or `submit()` signs afresh. `options()` returns all ten
options as a dict, and each is also a read-only property
(`order.reward`, `order.access`, `order.max_energy` and so on).

### Quote and submit

`quote()` signs the order's `propose_job` extrinsic once and caches it,
then prices it: the fee from the chain's `payment_queryInfo`, the
account balance and the head block, all re-read on every call. Nothing
is sent and no nonce is consumed. It returns a `JobQuote`; [Cost
Model](quip-network.md#cost-model) lists its fields.

`submit()` sends the cached extrinsic, signing first if the order was
never quoted. Before sending, it quotes again and runs the `autoconfirm`
and `autofund` gates, as `solve()` does. It ends in one of three states:

- `submitted`: the order is on chain, and `submit()` returns the order.
- `failed`: the dispatch failed, and `submit()` raises
  `QuipSubmissionError("propose_job 0x... failed on chain in block 0x...: <error>. The fee was paid and no order was placed.")`.
- `unconfirmed`: the outcome is unknown, and `submit()` raises
  `QuipUnconfirmedError`, which carries `extrinsic_hash` and
  `block_hash`.

A signature carries the account's nonce. When another order from the
same account is sent between `quote()` and `submit()`, the nonce has
moved, so `submit()` signs again. Two drafts quoted together and then
submitted show it:

```python
a = solver.create_order(model, resolution=ResolutionTopNEqual(2))
b = solver.create_order(model, access=AccessAllow(accounts=[me]), deadline_blocks=10, block_wait=0)
a.quote(), b.quote()
a.submit()
b.submit()
```

```text
proposed Quip job: order_id=326 spec_id=0x8f46...67f0 reward=1000000000000 planck (block 0x1ce9...9e99)
account nonce moved from 1 to 2 since signing; signing again
proposed Quip job: order_id=327 spec_id=0x8f46...67f0 reward=1000000000000 planck (block 0x99b3...4276)
```

(Log lines from the `xqsa.quip` logger at `INFO`, hashes shortened.) If
the nonce moves again while `submit()` signs, it refuses rather than
race the other client:
`QuipSubmissionError: account nonce moved twice while submitting; another client is sending from this account. Nothing was sent.`

`submit()` logs the quote box at `INFO` on the `xqsa.quip` logger, and
also prints it to stderr when stderr is a terminal.

### Checks before signing

`create_order()` and `set()` check each option against the chain's
limits, which the client reads once when it connects. A refusal raises
`QuipOrderOptionError`, a `QuipSubmissionError` whose `option` attribute
names the option. A warning is a `UserWarning`; the order is still
built. The limits on `aglais` are `MinReward` 1 AGLS,
`MaxDeadlineBlocks` 1000 and `MaxBlockWait` 100.

| Option | Refused | Warns |
|---|---|---|
| `reward` | `reward=999999999999 planck is below the chain's MinReward of 1000000000000` | |
| `deadline_blocks` | `deadline_blocks=1001 must be at least 10 and at most MaxDeadlineBlocks (1000)` | `deadline_blocks=50: orders shorter than 100 blocks often go unanswered` |
| `block_wait` | `block_wait=101 must be at least 0 and at most MaxBlockWait (100)` | `block_wait=100 is not below deadline_blocks=100; the deadline ends the order before the wait for better answers can` |

The chain itself accepts a deadline of 0, which expires on arrival. The
short-deadline warning reflects how often `aglais` answered: an order
with a 1-block deadline was answered 0 times out of 11, 30 blocks 28
times out of 38, and 100 blocks 108 times out of 108. The other options'
checks are in their sections below.

### The order box

`print(order)` renders a 72-column box. Its sections are the network;
the model and where it was placed; the terms (payout, access, floors,
deadline, block wait and reward); and a summary of the latest quote and
the receipt:

```text
╭─ Job order (draft) ──────────────────────────────────────────────────╮
│ Network     aglais                                                   │
├──────────────────────────────────────────────────────────────────────┤
│ Model       16 variables, 96 couplings, binary                       │
│ Placed      native, 16 spins                                         │
├──────────────────────────────────────────────────────────────────────┤
│ Payout      single best                                              │
│ Access      open                                                     │
│ Floors      none                                                     │
│ Deadline    100 blocks                                               │
│ Block wait  10 blocks                                                │
│ Reward      1.000000000000 AGLS                                      │
├──────────────────────────────────────────────────────────────────────┤
│ Quote       not quoted                                               │
│ Receipt     not submitted                                            │
╰──────────────────────────────────────────────────────────────────────╯
```

On a draft, the `Quote` row shows the last quote taken and whether the
account can afford it. Once the order has an id, a `Paid` row replaces
it: the fee the chain charged, read from the order's
`TransactionFeePaid` event, the reward, and the quote's total beside
them for comparison. The quoted fee is an estimate at the head block, so
the two can differ. When the fee cannot be read, the row says
`fee unknown`. A failed or unconfirmed order has no id to read a fee
from, so its `Quoted` row shows the quote alone.

## Payout rules

<!-- xquad:defect QUI-1823 -->
> **Not yet verified on runtime 119.** `ResolutionTopNWeighted` was
> tested only by its refusal on `aglais` at runtime 118. No network ran
> runtime 119 when this was written, so an order using it may behave
> differently there. Report problems at the [issue
> tracker](https://gitlab.com/quip.network/xquad/-/issues).

The `resolution` option decides how the reward is split:

- `"SingleBest"` or `ResolutionSingleBest()`, the default: the leader,
  the solver the chain ranks first, takes the whole reward.
- `ResolutionTopNEqual(n)`: the best `n` ranked solvers split it evenly.
- `ResolutionTopNWeighted(n)`: the best `n` split it by rank. With `k`
  ranked solvers, rank `r` (0 is best) gets weight `k - r`; energies
  decide only the order.

```python
from xquad.sa.quip import ResolutionTopNEqual

order = solver.create_order(model, resolution=ResolutionTopNEqual(2))
```

`n` runs from 1 to 32. The rule's own constructor checks it, so a bad
`n` raises `ValueError`
(`ResolutionTopNEqual: n must be from 1 to 32, got 33`) or `TypeError`
before any order exists. `n=1` warns
`ResolutionTopNEqual(1) pays exactly like ResolutionSingleBest()`. Any
other value for `resolution` is refused: with `ValueError` on the
constructor, and with `QuipOrderOptionError` on an order:

```text
resolution='TopNEqual' is not a payout rule; pass 'SingleBest' or one of ResolutionSingleBest(), ResolutionTopNEqual(n), ResolutionTopNWeighted(n)
```

`ResolutionTopNWeighted` needs runtime 119 or later (see [Runtime
compatibility](quip-network.md#runtime-compatibility)). `aglais` runs
118, so `create_order()`, `set()`, `solve()` and `quote()` refuse it
before signing:

```text
QuipOrderOptionError: ResolutionTopNWeighted needs runtime 119 or later, and the chain runs runtime 118; older runtimes weight by absolute energy, so a worse answer can take the larger share
```

A top-N rule with `n` above 1 and `block_wait=0` warns, because the
order closes on its first answer:
`resolution=top 3, equal split: with block_wait=0 only the first answer is accepted, so there can be only one winner`.

### Settlement

<!-- xquad:defect QUI-1823 -->
> **Not yet verified on runtime 119.** The `TopNWeighted` split below
> follows the runtime 119 pallet and has not been checked against a
> settled order, since no network ran runtime 119 when this was written.
> Report problems at the [issue
> tracker](https://gitlab.com/quip.network/xquad/-/issues).

The chain ranks solvers by energy, lowest first, with the earlier
submitter first on a tie. Winners are the first `n` ranked solvers, so
an order with fewer answers than `n` has fewer winners. A receipt's
`settlement()` computes each winner's share:

- `TopNEqual`: each winner gets `reward // k`, and rank 0 also gets the
  remainder. A reward of 10 over 3 winners splits `[4, 3, 3]`.
- `TopNWeighted`: with `total = k * (k + 1) / 2`, rank `r` gets
  `(reward // total) * w + (reward % total) * w // total` for its weight
  `w`, and rank 0 also gets the remainder. A reward of 10 over 3 winners
  splits `[6, 3, 1]`.

A top-2 order answered by eight solvers, two of them tied at the best
energy:

```python
print(order.settlement())
```

```text
{'claimed': False, 'winners': [{'solver': '5DPhy7jpFj3ikUZgVpwkrXnjFLYXqsWWPNUCrEvmzq2AEwFJ', 'energy_milli': -1817000, 'allocation_planck': 500000000000}, {'solver': '5Eb82sAEyRToSgRK51njamL6xMT2gGriDKtXTMBfR31PSvwk', 'energy_milli': -1817000, 'allocation_planck': 500000000000}]}
```

An allocation is not a payment. One `claim_reward` extrinsic pays every
winner; until then the receipt shows each as `awaiting claim`, and
`claimed` turns `True` once the chain closes the order. Below runtime
119, `settlement()` of a `TopNWeighted` order raises
`NotImplementedError`, and the receipt prints that message as its
`Reward` row. The order box and the receipt show the rule as
`single best`, `top 2, equal split` or `top 2, weighted by rank`.

## Access

The `access` option decides who may answer. `"Open"` or `AccessOpen()`,
the default, admits every registered solver.
`AccessAllow(accounts=[...], types=[...])` admits a solver whose account
is listed or whose declared type is listed:

```python
from xquad.sa.quip import AccessAllow

order = solver.create_order(model, access=AccessAllow(types=["QpuDwave"]))
```

Solver types are self-declared when a solver registers, and the protocol
does not verify them, so a type entry admits any solver that claims it.
On `aglais`, an order allowlisted to `Cpu` was answered by our own
solver and three others.

The allowlist is bounded:

- It must name at least one account or type:
  `AccessAllow: name at least one account or solver type; the chain refuses an empty list`
  (`ValueError`, from the constructor). An empty list would fail on
  chain after the fee was paid.
- At most 8 types (`ValueError`).
- At most `MaxBidMiners` accounts, 16 on `aglais`:
  `AccessAllow lists 17 accounts; the chain's MaxBidMiners is 16`
  (`QuipOrderOptionError`).
- Type names come from the runtime's `MinerType`, on `aglais` `Cpu`,
  `Gpu`, `QpuDwave`, `QpuIbm`, `QpuIonq`, `QpuPasqal` and `Asic`:
  `AccessAllow: unknown solver type 'Fpga'; the chain has Cpu, Gpu, QpuDwave, QpuIbm, QpuIonq, QpuPasqal, Asic`
  (`QuipOrderOptionError`).

Over either cap the runtime fails with no readable error, which is why
the client refuses first. Duplicate entries are dropped, and accounts,
given as SS58, `0x` hex or bytes, are normalised to `0x` hex. A bad
value for `access` raises `ValueError` on the constructor and
`QuipOrderOptionError` on an order:
`access='Bid' is not an access rule; pass 'Open' or one of AccessOpen(), AccessAllow(accounts=[...], types=[...])`.

An allowlisted account that is not a registered solver is never refused,
but it warns, naming the account in SS58:

```text
UserWarning: access: 5FdrtfYWZKnRQJT356sXLne1V2GSCWs9DTs7vkXht35bSFgb is not a registered solver, so it cannot answer until it registers
```

The constructor warns for its default `access`, and `create_order()` and
`set()` warn when they change it. When nobody on the allowlist answers,
the no-answer error says so:
`order 327 has no answers; only allowlisted solvers could answer (accounts 5Fdr..SFgb) and none did; once it is final, reclaim its reward with order.reclaim()`.

A solver that is not on the list and answers anyway is refused on chain
with `System.ExtrinsicFailed: QuantumComputeMempool.NotEligibleSolver`,
followed by the error's description when the metadata carries one.
Network coordinators submit to allowlisted orders regardless and pay for
each refused submission, so a block explorer can show many refused
submissions against one allowlisted order: 4 to 142 per order on
`aglais`.

## Quality floors

Three per-order options set a minimum quality an answer must reach. None
of them is a client default.

- `max_energy`: an energy ceiling. The chain drops each submitted vector
  above it.
- `min_solutions`: how many vectors each answer must carry. The chain
  stores exactly that many from each submission.
- `min_diversity`: how different those vectors must be, as a fraction of
  the placed spins. `0` means no floor.

`max_energy` is in model units, the same units as `result.energy`. For a
BINARY model the chain's energy differs from the model's by a constant
offset, which the client subtracts before it sends the ceiling; [An
Energy Ceiling on the Quip
Network](energy-and-precision.md#an-energy-ceiling-on-the-quip-network)
works through the conversion. A ceiling that moves shows both values in
the order box's `Floors` row, here for `max_energy=-2` on the model
`-2*x0 + x0*x1`:

```text
│ Floors      energy <= -2 (chain -1.25)                               │
```

Refusals (`QuipOrderOptionError` unless marked) and warnings:

| Option | Refused | Warns |
|---|---|---|
| `max_energy` | A `bool` or a non-number (`TypeError`); not finite; too large for a float; outside `i64` once converted to milli; below the lowest energy the placed model can reach: `max_energy=-1000000.0 is below -5388, the lowest energy the placed model can reach, so no answer could meet it` | At or above the highest energy: `max_energy=1000000.0 is at or above 7576, the highest energy the placed model can reach, so it filters nothing`. On a model whose coefficients were rounded: `max_energy=-1: the model's coefficients were rounded to the chain's milli grid, and the chain applies the ceiling to the rounded model, so answers near it can land on either side` |
| `min_solutions` | Not an int (`TypeError`); outside 1 to `MaxSolutions` (20 on `aglais`): `min_solutions=21 must be at least 1 and at most MaxSolutions (20)` | Exactly 1: `min_solutions=1: the chain stores only the first vector of each answer, but ranks the answer by its best vector` |
| `min_diversity` | Not a number (`TypeError`); outside 0 to 0.5; not a whole number of thousandths; above what the placed spins allow: `min_diversity=0.4 is above 1/3, the most diversity 3 placed spins allow, since a vector and its full flip count as one`; positive with `min_solutions=1`: `min_diversity=0.3 needs min_solutions of at least 2 or unset; one solution has no diversity (the chain's InvalidDiversityConfig)` | |

What the chain does with each floor:

- The ceiling drops each submitted vector above it. A submission is
  refused (`InsufficientEnergy`) only when none of its vectors is left.
- With `min_solutions=k`, the chain stores exactly the `k` most diverse
  vectors of each submission. That subset can leave out a solver's best
  vector, which is one reason `best()` can fall back (see [Reading the
  answer](#reading-the-answer)).
- Diversity is the average pairwise Hamming distance over the placed
  spins, in thousandths, with a vector and its full flip counted as the
  same vector. That is why `n` spins cap it at `floor(n/2)/n`: a 3-spin
  model caps at 0.333.

Floors narrow the answers a solver can give, and a small model may have
too few distinct vectors to meet `min_solutions` or `min_diversity` at
all, so an order with floors can go unanswered. The no-answer error then
names them: `best()` in model units, `solve()` in chain units. The order
box shows floors in model units; the receipt shows them in chain units;
both show `none` when no floor is set.

## Receipts

A submitted order has a receipt, a `JobOrderReceipt`. It holds only the
order id and the network's genesis hash, needs no model, and reads the
chain on every call. `order.receipt()` returns it; on a draft it raises
`QuipSubmissionError: this order is a draft; submit() it first`. Any
session rebuilds a receipt from the order id:

```python
solver = SolverQuip.for_network("aglais")
print(solver.list_orders())
receipt = solver.get_receipt(325)
print(receipt.status())
```

```text
[330, 329, 328, 327, 326, 325]
{'state': 'finalized', 'order_id': 325, 'chain_status': 'Closed', 'created_at': 580498, 'first_solution_at': 580499, 'effective_expiry': 580509, 'current_block': 580657, 'solution_count': 9}
```

`get_receipt()` raises `QuipConnectionError` for an id that names no
order
(`order 1000000000 not found on-chain (QuantumComputeMempool.JobOrders)`).

`list_orders(state=None, account=None)` returns an account's order ids,
newest first. `state` is `"submitted"`, `"finalized"` or `None`, and any
other value raises `ValueError`; `account` is SS58, `0x` hex or bytes,
and defaults to the signer. It returns at most the newest 32 ids, after
the `state` filter, and warns when there are more:
`showing 32 of N orders on this account. This 32-order page limit was imposed for the old protocol cap of 32 orders per account; please flag it to the xquad team so it can be lifted.`

The receipt and the in-session `JobOrder` share these methods:

| Method | Returns |
|---|---|
| `status()` | `state` (`submitted` or `finalized`, which sticks once reached), `order_id`, `chain_status` (the chain's `OrderStatus`), `created_at`, `first_solution_at`, `effective_expiry`, `current_block` and `solution_count` |
| `wait()` | The receipt (or order) once it is final, polling every `poll_interval`; raises `QuipTimeoutError` after the client's `timeout` |
| `solvers()` | One entry per solver that answered: `solver`, `ranked_energy_milli`, `energy_milli`, `submitted_at`, `num_solutions`. Ranked solvers come first, then the rest by stored energy and then by submission time. `energy_milli` is the solver's stored best, which a later resubmission can overwrite |
| `raw_solutions(solver=None)` | `{solver: {"energy_milli": ..., "spins": ...}}` over the placed chain nodes, not decoded onto the model; `KeyError` for a solver that did not answer |
| `settlement()` | `{"claimed": bool, "winners": [...]}`; see [Settlement](#settlement) |
| `reclaim()` | The planck refunded; see below |

`chain_status` and `state` answer different questions. `state` is
`finalized` once the chain head passes the order's effective expiry,
while `chain_status` stays `Opened` until an extrinsic touches the
order, such as a winner's claim or a reclaim. The deprecated
`SolverQuip.status()` returns the old keys, `status` and `is_final`, in
place of `state` and `chain_status`.

`wait()` raises `QuipTimeoutError` with the receipt attached, so the
caller can wait again instead of rebuilding it:

```python
from xquad.sa.quip import QuipTimeoutError

try:
    order.wait()
except QuipTimeoutError as exc:
    receipt = exc.receipt
    receipt.wait()
```

`QuipTimeoutError` drops the receipt when it is pickled, since the
receipt holds a live connection; another process rebuilds it with
`get_receipt(exc.order_id)`.

### The receipt box

`print(receipt)` renders a box with five sections: the network, proposer
and submission block and time; the problem; the terms, with floors in
chain units; the outcome (status, answer count, leader and its energy);
and, under a double rule, the money. The fee is what the chain charged
the proposing extrinsic, tip included, read from the block that placed
the order; when it cannot be read the row says `unknown`. The `Reward`
row is `reserved, leader so far`, `reserved, no answers yet`,
`reclaimed`, or `unanswered: reclaim with get_receipt(N).reclaim()`; an
answered, final order shows one `Winner` row per winner,
`awaiting claim` or `claimed`:

```text
╭─ Job order 325 receipt ──────────────────────────────────────────────╮
│ Network     aglais                                                   │
│ Proposer    5FdrtfYWZKnRQJT356sXLne1V2GSCWs9DTs7vkXht35bSFgb         │
│ Submitted   block 580498, 2026-10-12 17:11:24 UTC                    │
├──────────────────────────────────────────────────────────────────────┤
│ Problem     16 spins, 96 couplings                                   │
│ Spec        0x8f46f3a31321d1d093314fc769c42cbe7a83d71a0b69e6571a0f68 │
│             e2a04067f0                                               │
├──────────────────────────────────────────────────────────────────────┤
│ Payout      single best                                              │
│ Access      open                                                     │
│ Floors      none                                                     │
│ Deadline    100 blocks                                               │
│ Block wait  10 blocks                                                │
│ Reward      1.000000000000 AGLS                                      │
├──────────────────────────────────────────────────────────────────────┤
│ Status      final                                                    │
│ Answers     9                                                        │
│ Leader      5DXr9LokuLE5bs2ZZyhDwbJfR9SqceL7kohQbgMebDcymhfD         │
│ Energy      -1817                                                    │
╞══════════════════════════════════════════════════════════════════════╡
│ Fee         0.001907881410 AGLS                                      │
│ Winner      1.000000000000 AGLS, awaiting claim                      │
╰──────────────────────────────────────────────────────────────────────╯
```

### Reclaiming

`reclaim()` returns an unanswered order's reserved reward and returns
the planck refunded. It reads the head block, then refuses before
signing, with `QuipReclaimRefusedError`, in this order:

1. `order N was proposed by <account>, not by this account`
2. `order N is already closed (reclaimed or settled)`
3. `order N is open until block B (about M min); reclaim after it closes`
4. `order N was answered by K solvers; its reward awaits a claim and
   cannot be reclaimed`

If the chain refuses the reclaim, it raises `QuipReclaimRefusedError`
naming the block and the chain's error, and the fee is charged. If the
outcome is unknown, it raises
`QuipSubmissionError("the reclaim of order N (<extrinsic>) was sent but its outcome is unknown")`;
a retry is safe, since a reclaim that landed makes the next one refuse
as already closed. A reclaim costs its own transaction fee, about 0.0011
AGLS on `aglais`.

Only `solve()`, and the deprecated `query()`, reclaim on their own: when
an order finalizes with no answers, they reclaim it and raise
`QuipJobFailedError`, saying whether the reclaim succeeded:

```text
QuipJobFailedError: order 330 finalized with no solutions; only allowlisted solvers could answer (accounts 5Fdr..SFgb) and none did; the reserved reward was reclaimed
```

`JobOrder.wait()`, `JobOrder.best()` and a receipt's `wait()` never
reclaim.

## Reading the answer

An in-session `JobOrder` holds the model it was built from, so it
decodes answers onto it:

```python
solution = order.best()
print(solution.energy)
print(solution.solver, solution.leader, solution.energy_milli)
```

```text
-723
5DXr9LokuLE5bs2ZZyhDwbJfR9SqceL7kohQbgMebDcymhfD True -1817000
```

`best()` returns a `Solution` (importable from `xquad.sa.quip`, or
`xqsa.quip`, only) with five fields:

| Field | Meaning |
|---|---|
| `sample` | The vector decoded onto the model, as an XQMX sample |
| `energy` | Its energy recomputed on your model; authoritative |
| `energy_milli` | Its energy on the chain's milli scale, over the coefficients the order carried |
| `solver` | The account that answered with it |
| `leader` | Whether the chain ranks that account first |

`solutions(solver=None)` returns every stored answer as a `Solution`,
best first by recomputed energy with the leader's vectors first on a
tie. With `solver`, it returns that solver's vectors alone, and raises
`KeyError` for a solver that did not answer. An unanswered order returns
an empty list.

`best()` returns the leader's best vector, the answer the chain pays
for, not necessarily the best vector any solver stored. It falls back to
the best stored vector overall, the leader first on a tie, in two cases:

- The ranking cannot be read, or names a solver with no stored answer.
  `metadata["leader_read"]` is then `False` on a `solve()` result.
- None of the leader's stored vectors recomputes to the energy the chain
  ranked it at. `energy_matches_chain` is then `False`, and the client
  logs both energies at `WARNING`. A worse resubmission can overwrite a
  solver's stored answer, and `min_solutions` can store a subset without
  the best vector, so this is not necessarily an encoding fault.

Under a fallback, `leader` stays `True` only when the leader's own
vector is also the best overall. `best()` reads the chain each time, so
on an open order a later call can return a different answer. It never
reclaims: with no answers, it raises `QuipJobFailedError` and tells you
to call `order.reclaim()` once the order is final.

Decoding needs the in-session `JobOrder`, because a receipt does not
hold the model. From another session, `get_receipt(order_id)` gives you
the order's status, solvers, raw vectors, settlement and reclaim, but
not a decoded sample; the deprecated `query(order_id, model, ...)` is
the only way to decode one there (see [Deprecated in
v0.4.3](quip-network.md#deprecated-in-v043)).
