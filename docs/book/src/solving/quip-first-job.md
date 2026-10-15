# Your First Quip Job

This page submits one job to `aglais`, the public Quip test network,
from an empty account: define a four-city Travelling Salesman Problem in
XQCP, run its encoder on the XQVM locally, and hand the resulting model
to `SolverQuip` as a job order. Every output block below was captured
from the code on this page, run against `aglais` on 2026-10-15 at
runtime `specVersion 119`. [Quip Network](quip-network.md) is the
reference for the mechanism and [Job Orders](quip-orders.md) for the
order surface; this page is the walkthrough.

## Install

```sh
pip install "xquad[quip]"
```

The extra pulls in the chain client and `quip-signer`, the native
extension that signs extrinsics. `quip-signer` ships a prebuilt wheel on
Linux and builds from source with a local Rust toolchain on macOS and
Windows.

## Build the Model

The model is the [TSP example](../examples/tsp.md) at `n = 4`, with a
fixed distance matrix instead of a random one so the numbers below are
reproducible. The encoder runs on your machine; nothing touches the
network yet.

```python
from xquad.cp import Domain, Problem, Types, xq_triu
from xquad.vm import VM

n = 4
distances = [12, 30, 25, 18, 40, 22]  # xq_triu order: 0-1, 0-2, 1-2, 0-3, 1-3, 2-3

problem = Problem("TSP")
num_cities = problem.input("num_cities", type=Types.Int)
distance_matrix = problem.input("distance_matrix", type=Types.Vec)
problem.define_model(size=num_cities * num_cities, domain=Domain.BINARY, rows=num_cities, cols=num_cities)

with problem.range(0, num_cities - 1) as city_i:
    with problem.range(city_i + 1, num_cities) as city_j:
        dist = problem.stow("dist", distance_matrix.get(xq_triu(city_i, city_j)))
        with problem.range(0, num_cities) as position:
            next_position = (position + 1) % num_cities
            problem.model.quadratic[(city_i, position), (city_j, next_position)].add(dist)
            problem.model.quadratic[(city_j, position), (city_i, next_position)].add(dist)

with problem.range(0, num_cities) as city:
    problem.model.apply_onehot_row(city, penalty=100)
with problem.range(0, num_cities) as position:
    problem.model.apply_onehot_col(position, penalty=100)

tour = problem.output("tour", type=Types.Vec)
with problem.range(0, num_cities) as position:
    tour.append(problem.sample.colfind(col=position, value=1))

programs = problem.compile()

vm = VM()
vm.set_calldata([n, distances])
vm.set_output_slots(1)
vm.run(programs.encoder)
model = vm.outputs()[0]
```

`model` is a 16-variable BINARY XQMX: one variable per city and tour
position.

## Connect and Quote

```python
from xquad.sa.quip import SolverQuip

solver = SolverQuip.for_network("aglais", topology="native")
order = solver.create_order(model)
print(order)
print(order.quote())
```

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
╭─ Job quote ──────────────────────────────────────────────────────────╮
│ Network     aglais                                                   │
│ Account     5DL9B7rhMw1cg7kXzqmdj5QZ1V67vBvfYPq2bTEgZfSXiBSJ         │
│ Quoted at   block 1896                                               │
├──────────────────────────────────────────────────────────────────────┤
│ Problem     16 variables -> 16 spins, 96 couplings                   │
│ Placed on   native                                                   │
├──────────────────────────────────────────────────────────────────────┤
│ Reward      1.000000000000 AGLS  refunded if unanswered              │
│ Fee         0.002011933394 AGLS  exact                               │
│ Total       1.002011933394 AGLS                                      │
╞══════════════════════════════════════════════════════════════════════╡
│ Balance     0.000000000000 AGLS                                      │
│ Shortfall   1.002011933394 AGLS                                      │
│ Affordable  no                                                       │
╰──────────────────────────────────────────────────────────────────────╯
```

Two arguments do the work:

- `"aglais"` selects the preset carrying the network's RPC endpoint and
  faucet; see [Named networks](quip-network.md#named-networks).
- `topology="native"` submits the order over the model's own coupling
  graph. In the default mode, the placement search finds no embedding of
  this model in the network's topology within its step budget and raises
  `PlacementError`; native mode skips placement. See [Native topology
  mode](quip-network.md#native-topology-mode).

`create_order()` places the model and returns a draft `JobOrder` with
the client's defaults: winner takes all, any solver may answer, no
quality floors, a 100-block deadline and a 10-block wait after the first
answer. Nothing has been signed or sent. [Job Orders](quip-orders.md)
covers every option a draft takes.

No signer is passed, so `SolverQuip` uses the keystore at
`~/.quip/keystore.json`. On a first run the file does not exist yet:
`SolverQuip` creates it with a new signing key and warns with its path,
so the account starts empty. Keep the file: it is the account, and the
`Account` row shows its address. To use a different one, pass it:

```python
solver = SolverQuip.for_network("aglais", keystore="~/.quip/work.json", topology="native")
```

`quote()` signs the order's `propose_job` extrinsic and asks the chain
what it would cost, without submitting anything. The returned `JobQuote`
carries each amount in planck, the chain's smallest unit, as
`reward_planck`, `fee_planck`, `total_planck`, `balance_planck` and
`shortfall_planck`; `fee_exact` says the fee came from the chain rather
than a fallback estimate. The signature is kept, and `submit()` sends
those same bytes.

### What the figure means

As of 2026-10-15, at `specVersion 119`, a job costs 1 AGLS in reward
plus about 0.0020 AGLS in fee: about 1.0020 AGLS in total. The reward is
the chain's `MinReward`, a runtime constant. It is charged to gate
submissions and maps to no measured compute: the runtime declares
[`type VM = NoOpVm`](https://gitlab.com/quip.network/quip-validator/-/blob/161667b2ed7e375390ada523a459258f0f576f78/runtime/src/configs/mod.rs#L650),
so the chain meters none of the solver's work. The fee is metered,
burned, and does not rise with congestion. AGLS on `aglais` comes free
from the faucet. Both figures move when the runtime is upgraded, so read
them from `quote()` rather than from this page.

## Submit

```python
order.submit()
```

```text
╭─ Job quote ──────────────────────────────────────────────────────────╮
│ Network     aglais                                                   │
│ Account     5DL9B7rhMw1cg7kXzqmdj5QZ1V67vBvfYPq2bTEgZfSXiBSJ         │
│ Quoted at   block 1896                                               │
├──────────────────────────────────────────────────────────────────────┤
│ Problem     16 variables -> 16 spins, 96 couplings                   │
│ Placed on   native                                                   │
├──────────────────────────────────────────────────────────────────────┤
│ Reward      1.000000000000 AGLS  refunded if unanswered              │
│ Fee         0.002011933394 AGLS  exact                               │
│ Total       1.002011933394 AGLS                                      │
╞══════════════════════════════════════════════════════════════════════╡
│ Balance     0.000000000000 AGLS                                      │
│ Shortfall   1.002011933394 AGLS                                      │
│ Affordable  no                                                       │
╰──────────────────────────────────────────────────────────────────────╯
```

`submit()` quotes once more and shows that quote: it always goes to the
`xqsa.quip` logger at `INFO`, and it is also printed when stderr is a
terminal, as here. It then runs two gates with their defaults.
`autoconfirm=True` accepts the price. The account is short, so
`autofund=True` draws one drip of 10 AGLS from the faucet. Finally it
sends the signed `propose_job`, which reserves the reward, and returns
the order, now `submitted`. The order is read-only from here: `set()`,
`quote()` and `submit()` refuse it.

## Read the Result

```python
order.wait()
solution = order.best()
print(solution.energy)
print(solution.solver, solution.leader, solution.energy_milli)

vm = VM()
vm.set_calldata([solution.sample, n])
vm.set_output_slots(1)
vm.run(programs.decoder)
print(list(vm.outputs()[0]))
```

```text
-696
5GqG8c8zWHX33ruGJX4FDokxCTFrASVi268einKJY1XnHNYn True -1790000
[0, 1, 3, 2]
```

`wait()` blocks until the order is final by block height. Here the first
answer arrived six blocks after the order was included, and the order
was final ten blocks later, at the end of its 10-block wait; the script
returned about 122 seconds after it started.

- The decoded tour visits cities 0, 1, 3, 2 and returns to 0. Its length
  is 12 + 40 + 22 + 30 = 104. It is a valid tour but not the shortest:
  of the three distinct tours on four cities, 0, 1, 2, 3 is shortest, at
  77, with energy -723. The network pays for the best answer it
  receives, not for the optimum, and here the two answers that arrived
  missed it. The one-call form below came back with the shortest tour.
- `solution.energy` is that length plus -800, the constant the eight
  one-hot penalties contribute at any valid assignment. It is recomputed
  on your model.
- `best()` returns the leader's best vector: the answer from the solver
  the chain ranks first, and pays. `solution.leader` is `True` when that
  rule held; [Reading the answer](quip-orders.md#reading-the-answer)
  covers the cases where it falls back to another solver's vector.
- `energy_milli` is the vector's energy on the chain's milli scale, in
  the SPIN basis, and is not comparable to `solution.energy` directly.

The order's receipt shows what the chain recorded, including the fee and
the payout:

```python
print(order.receipt())
```

```text
╭─ Job order 20 receipt ───────────────────────────────────────────────╮
│ Network     aglais                                                   │
│ Proposer    5DL9B7rhMw1cg7kXzqmdj5QZ1V67vBvfYPq2bTEgZfSXiBSJ         │
│ Submitted   block 1898, 2026-10-15 04:22:24 UTC                      │
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
│ Answers     2                                                        │
│ Leader      5GqG8c8zWHX33ruGJX4FDokxCTFrASVi268einKJY1XnHNYn         │
│ Energy      -1790                                                    │
╞══════════════════════════════════════════════════════════════════════╡
│ Fee         0.002011933394 AGLS                                      │
│ Winner      1.000000000000 AGLS, awaiting claim                      │
╰──────────────────────────────────────────────────────────────────────╯
```

Two solvers answered; the leader wins the whole reward, which it
receives once a claim is made. The account now holds the drip less one
job's total.

### The one-call form

`solve()` does all of the above in one call: it creates the order,
submits it, waits, and returns the leader's answer as a `SolverResult`,
whose metadata names the order:

```python
result = solver.solve(model)
print(result.energy)
print(result.metadata)
```

```text
-723
{'order_id': 23, 'solver': '5GqG8c8zWHX33ruGJX4FDokxCTFrASVi268einKJY1XnHNYn', 'best_energy_milli': -1817000, 'energy_matches_chain': True, 'leader_read': True, 'leader': True, 'num_submissions': 1, 'num_solutions': 1, 'quantization_error': 0.0}
```

`solve()` prints the quote box the same way `submit()` does.
[Metadata](quip-network.md#metadata) explains each key.

## Confirm Before Paying

`autoconfirm=False` asks on the terminal before anything is proposed.
Answering anything but `y` raises `QuipCancelledError`, which carries
the quote:

```python
from xquad.sa.quip import QuipCancelledError

solver = SolverQuip.for_network("aglais", topology="native", autoconfirm=False)
try:
    result = solver.solve(model)
except QuipCancelledError as exc:
    print("cancelled:", exc)
    print(exc.quote.total_planck)
```

```text
╭─ Job quote ──────────────────────────────────────────────────────────╮
│ Network     aglais                                                   │
│ Account     5DL9B7rhMw1cg7kXzqmdj5QZ1V67vBvfYPq2bTEgZfSXiBSJ         │
│ Quoted at   block 1952                                               │
├──────────────────────────────────────────────────────────────────────┤
│ Problem     16 variables -> 16 spins, 96 couplings                   │
│ Placed on   native                                                   │
├──────────────────────────────────────────────────────────────────────┤
│ Reward      1.000000000000 AGLS  refunded if unanswered              │
│ Fee         0.002011933394 AGLS  exact                               │
│ Total       1.002011933394 AGLS                                      │
╞══════════════════════════════════════════════════════════════════════╡
│ Balance     4.989940332991 AGLS                                      │
│ Shortfall   0.000000000000 AGLS                                      │
│ Affordable  yes                                                      │
╰──────────────────────────────────────────────────────────────────────╯
[quip] Submit this job for 1.002011933394 AGLS? [y/N] n
cancelled: autoconfirm declined: answered 'n' at the prompt
1002011933394
```

The same gate runs in `order.submit()`. With no terminal to ask on, a
notebook included, `autoconfirm=False` raises `QuipCancelledError`
without prompting. A declined job costs nothing: the gate runs before
funding, so it draws no drip, and nothing reaches the chain.

To decide in code instead, pass a callable that receives the `JobQuote`
and returns whether to submit. This one accepts any job up to 2 AGLS (a
token here has 12 decimals):

```python
solver = SolverQuip.for_network(
    "aglais",
    topology="native",
    autoconfirm=lambda quote: quote.total_planck <= 2 * 10**12,
)
result = solver.solve(model)
print(result.metadata["order_id"], result.energy)
```

```text
╭─ Job quote ──────────────────────────────────────────────────────────╮
│ Network     aglais                                                   │
│ Account     5DL9B7rhMw1cg7kXzqmdj5QZ1V67vBvfYPq2bTEgZfSXiBSJ         │
│ Quoted at   block 1936                                               │
├──────────────────────────────────────────────────────────────────────┤
│ Problem     16 variables -> 16 spins, 96 couplings                   │
│ Placed on   native                                                   │
├──────────────────────────────────────────────────────────────────────┤
│ Reward      1.000000000000 AGLS  refunded if unanswered              │
│ Fee         0.002011933394 AGLS  exact                               │
│ Total       1.002011933394 AGLS                                      │
╞══════════════════════════════════════════════════════════════════════╡
│ Balance     5.991952266385 AGLS                                      │
│ Shortfall   0.000000000000 AGLS                                      │
│ Affordable  yes                                                      │
╰──────────────────────────────────────────────────────────────────────╯
24 -723
```

## What a Failed Job Costs

An order that becomes final with no answers costs its transaction fee,
which is burned either way. Its reward stays reserved until the order is
reclaimed. `solve()` reclaims it for you and raises
`QuipJobFailedError`, carrying the `order_id`, with a message that says
whether the reclaim succeeded:

```text
QuipJobFailedError: order 25 finalized with no solutions; only allowlisted solvers could answer (accounts 5DL9..iBSJ) and none did; the reserved reward was reclaimed
```

That order was restricted to an account that is not a registered solver,
so nobody could answer it. Its total cost was the proposal fee plus the
reclaim fee, about 0.004 AGLS. A faucet drip drawn for a job stays in
the account either way.

On an order you submitted yourself, `wait()` and `best()` never reclaim.
`best()` raises `QuipJobFailedError` and tells you to reclaim; do it
once the order is final:

```python
refunded = order.reclaim()
```

A job that outlasts the solver's timeout raises `QuipTimeoutError`
instead, carrying `order_id` and the order's receipt. The job is still
live on chain, so wait on the receipt again:

```python
from xquad.sa.quip import QuipTimeoutError

try:
    order.wait()
except QuipTimeoutError as exc:
    exc.receipt.wait()
```

## Coming Back Later

The order lives on chain, not in your session. A new process lists the
account's orders and rebuilds any one's receipt from its id:

```python
solver = SolverQuip.for_network("aglais")
print(solver.list_orders())
receipt = solver.get_receipt(20)
print(receipt.status())
```

```text
[24, 23, 21, 20]
{'state': 'finalized', 'order_id': 20, 'chain_status': 'Expired', 'created_at': 1898, 'first_solution_at': 1904, 'effective_expiry': 1914, 'current_block': 2219, 'solution_count': 2}
```

`chain_status` is `Expired`: the chain marked the order expired at its
effective expiry. It turns `Closed` once someone claims it, and
`print(receipt)` then shows the `Winner` row as `claimed`. A closed
order drops out of `list_orders()`, which lists only orders not yet
closed, but its receipt still rebuilds from the id. Orders 21 and 22
were placed for the examples on [Job Orders](quip-orders.md). Orders 22
and 25 are already missing: they went unanswered and were reclaimed. A
receipt also lists the solvers that answered and their raw spin vectors,
shows the settlement, and reclaims an unanswered order with
`receipt.reclaim()`.

A receipt does not hold your model, so it cannot decode an answer into a
tour. Decoding needs the `JobOrder` from the session that submitted it.
From a new session, the deprecated
`solver.query(order_id, model, topology="native")` still returns a
decoded `SolverResult`, with the same `topology` the order was proposed
with; it warns and is removed in v0.5.0 (see [Deprecated in
v0.4.3](quip-network.md#deprecated-in-v043)).

## What Native Mode Gives Up

A miner that cannot embed an arbitrary graph, such as a QPU-backed one,
cannot answer a native order. The simulated-annealing miners on `aglais`
answered every open job on this page.
