# Your First Quip Job

This page submits one job to `aglais`, the public Quip test network,
from an empty account: define a four-city Travelling Salesman Problem
in XQCP, run its encoder on the XQVM locally, and hand the resulting
model to `SolverQuip`. Every output block below was captured from the
code on this page, run against `aglais` on 2026-09-24 at runtime
`specVersion 117`. [Quip Network](quip-network.md) is the reference
for the mechanism; this page is the walkthrough.

## Install

```sh
pip install "xquad[quip]"
```

The extra pulls in the chain client and `quip-signer`, the native
extension that signs extrinsics. `quip-signer` ships a prebuilt wheel
on Linux and builds from source with a local Rust toolchain on macOS
and Windows.

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
from xquad.sa import SolverQuip

solver = SolverQuip.for_network("aglais", topology="native")
print(solver.quote(model))
```

```text
[quip] job quote (network aglais, fee exact)
[quip]   reward     1.000000000000 AGLS
[quip]   fee        0.002193037538 AGLS
[quip]   total      1.002193037538 AGLS
[quip]   balance    0.000000000000 AGLS
[quip]   shortfall  1.002193037538 AGLS
```

Two arguments do the work:

- `"aglais"` selects the preset carrying the network's RPC endpoint
  and faucet; see [Named networks](quip-network.md#named-networks).
- `topology="native"` submits the order over the model's own coupling
  graph. In the default mode, the placement search finds no embedding
  of this model in the network's topology within its step budget and
  raises `PlacementError`; native mode skips placement. See
  [Native topology mode](quip-network.md#native-topology-mode).

No signer is passed, so `SolverQuip` uses the keystore at
`~/.quip/keystore.json`. On a first run the file does not exist yet:
`SolverQuip` creates it with a new signing key and warns with its path,
so the account starts empty. Keep the file: it is the account.
To use a different one, pass it:

```python
solver = SolverQuip.for_network("aglais", keystore="~/.quip/work.json", topology="native")
```

`quote()` builds and signs the job's `propose_job` extrinsic and asks
the chain what it would cost, without submitting anything. The
returned `JobQuote` carries each row in planck, the chain's smallest
unit, as `reward_planck`, `fee_planck`, `total_planck`,
`balance_planck` and `shortfall_planck`; `fee_exact` says the fee came
from the chain rather than a fallback estimate.

### What the figure means

As of 2026-09-24, at `specVersion 117`, a job costs 1 AGLS in reward
plus about 0.0022 AGLS in fee: about 1.0022 AGLS in total. The reward
is the chain's `MinReward`, a runtime constant. It is charged to gate
submissions and maps to no measured compute: the runtime declares
[`type VM = NoOpVm`](https://gitlab.com/quip.network/quip-validator/-/blob/161667b2ed7e375390ada523a459258f0f576f78/runtime/src/configs/mod.rs#L650), so the chain meters none of the
solver's work. The
fee is metered, burned, and does not rise with congestion. AGLS on
`aglais` comes free from the faucet. Both figures move when the
runtime is upgraded, so read them from `quote()` rather than from this
page.

## Submit

```python
result = solver.solve(model)
```

```text
[quip] job quote (network aglais, fee exact)
[quip]   reward     1.000000000000 AGLS
[quip]   fee        0.002193037538 AGLS
[quip]   total      1.002193037538 AGLS
[quip]   balance    0.000000000000 AGLS
[quip]   shortfall  1.002193037538 AGLS
```

`solve()` prints the same quote when stderr is a terminal; in a
notebook or a pipe it goes to the `xqsa.quip` logger at INFO instead.
It then runs two gates with their defaults. `autoconfirm=True`
accepts the price. The account
is short, so `autofund=True` draws one drip of 10 AGLS from the
faucet. It then proposes the job, reserving the reward, and waits
until the order is final by block height. This call returned after
about 94 seconds. Nothing else is printed on the way.

## Read the Result

```python
print(result.energy)
print(result.metadata)

vm = VM()
vm.set_calldata([result.sample, n])
vm.set_output_slots(1)
vm.run(programs.decoder)
print(list(vm.outputs()[0]))
```

```text
-723
{'order_id': 85, 'solver': '5F8JSnuLkeqo8tYj1NVAmpJ2aCh6qdd8VVBCB8D4yGKUshWu', 'best_energy_milli': -1817000, 'energy_matches_chain': True, 'num_submissions': 15, 'num_solutions': 1}
[1, 2, 3, 0]
```

- The decoded tour visits cities 1, 2, 3, 0 and returns to 1. Its
  length is 25 + 22 + 18 + 12 = 77, the shortest of the three distinct
  tours on four cities.
- `result.energy` is that length plus -800, the constant the eight
  one-hot penalties contribute at any valid assignment.
- `energy_matches_chain` is `True`: the energy recomputed locally from
  the returned spins equals the chain's own `best_energy_milli`, so
  encoding and decoding line up. `best_energy_milli` is in the SPIN
  basis at milli scale and is not comparable to `result.energy`
  directly.
- `order_id` identifies the job on-chain; `solver` is the account of
  the miner whose submission won.

The account now holds the drip less one job's total.

## Confirm Before Paying

`autoconfirm=False` asks on the terminal before anything is proposed.
Answering anything but `y` raises `QuipCancelledError`, which carries
the quote:

```python
from xquad.sa import QuipCancelledError

solver = SolverQuip.for_network("aglais", topology="native", autoconfirm=False)
try:
    result = solver.solve(model)
except QuipCancelledError as exc:
    print("cancelled:", exc)
    print(exc.quote.total_planck)
```

```text
[quip] job quote (network aglais, fee exact)
[quip]   reward     1.000000000000 AGLS
[quip]   fee        0.002193037538 AGLS
[quip]   total      1.002193037538 AGLS
[quip]   balance    8.997806962462 AGLS
[quip]   shortfall  0.000000000000 AGLS
[quip] Submit this job for 1.002193037538 AGLS? [y/N] n
cancelled: autoconfirm declined: answered 'n' at the prompt
1002193037538
```

With no terminal to ask on, a notebook included, `autoconfirm=False`
raises `QuipCancelledError` without prompting. A declined job costs
nothing: the gate runs before funding, so it draws no drip, and
nothing reaches the chain.

To decide in code instead, pass a callable that receives the
`JobQuote` and returns whether to submit. This one accepts any job up
to 2 AGLS (a token here has 12 decimals):

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
[quip] job quote (network aglais, fee exact)
[quip]   reward     1.000000000000 AGLS
[quip]   fee        0.002193037538 AGLS
[quip]   total      1.002193037538 AGLS
[quip]   balance    8.997806962462 AGLS
[quip]   shortfall  0.000000000000 AGLS
86 -723
```

## What a Failed Job Costs

If an order becomes final with no submissions, `solve()` tries to
reclaim the reserved reward, then raises `QuipJobFailedError`,
carrying the `order_id`. The reclaim is best-effort: when it
succeeds the reward comes back, and when it fails the error message
says so and the reward stays reserved until `solver.query(order_id,
model, topology="native")` or a manual reclaim frees it. The
transaction fee is burned either way. A faucet drip drawn for that job
stays in the account.

A job that outlasts the solver's timeout raises `QuipTimeoutError`
instead, also carrying `order_id`. The job is still live on-chain;
`solver.query(order_id, model, topology="native")` reads its result
once it is final. Pass the same `topology` the job was proposed with.

## What Native Mode Gives Up

A miner that cannot embed an arbitrary graph, such as a QPU-backed
one, cannot answer a native order. The simulated-annealing miners on
`aglais` answered every job on this page.
