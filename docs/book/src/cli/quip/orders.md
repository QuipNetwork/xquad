# Orders

These commands follow job orders already on chain. They need no model
and no session: an order id is enough. Placing an order is done from
Python, with `create_order()` or `solve()` (see [Job
Orders](../../solving/quip-orders.md)).

The `orders` and `status` outputs on this page were captured on
2026-10-26 against `aglais` at `specVersion 119`, reading the orders
[Your First Quip Job](../../solving/quip-first-job.md) placed.

## orders

```sh
xquad quip --network aglais orders [--state STATE] [--account SS58]
```

Lists the account's orders not yet closed, newest first, one line each:
the order id, its state and the leader, tab-separated. The account is
the one in effect (see [account show](accounts.md#account-show)), or
`--account` names another; reading another account needs no keystore.

```text
$ xquad quip --network aglais orders --account 5DL9B7rhMw1cg7kXzqmdj5QZ1V67vBvfYPq2bTEgZfSXiBSJ
24	final	5Ho6x2cpdd34W89rgFrUTms4JsEZfiWKyUpwW9vnpbGDvsN4
23	final	5GqG8c8zWHX33ruGJX4FDokxCTFrASVi268einKJY1XnHNYn
21	final	5GqG8c8zWHX33ruGJX4FDokxCTFrASVi268einKJY1XnHNYn
20	final	5GqG8c8zWHX33ruGJX4FDokxCTFrASVi268einKJY1XnHNYn
```

The state column is one of:

| State | Meaning |
|-------|---------|
| `open until block N` | Still accepting answers. |
| `final` | Final and answered; the reward awaits a claim. |
| `reclaimable` | Final with no answers; [`reclaim`](#reclaim) refunds it. |
| `closed` | Claimed or reclaimed; the chain records both alike. |

The leader is `none` until someone answers. The chain drops an order
from the account's list once a claim or a reclaim closes it, so a
closed order is listed only by `status`. `--state` keeps the orders that
are `submitted` (still open), `finalized`, or `unanswered` (final with
no answers, the ones `reclaim --all` would take); an empty result says
so:

```text
$ xquad quip --network aglais orders --account 5DL9B7rhMw1cg7kXzqmdj5QZ1V67vBvfYPq2bTEgZfSXiBSJ --state unanswered
no orders in state unanswered
```

At most the newest 32 orders are listed, with a warning when there are
more. With `--json`, the same fields come as one object per order:

```json
{
  "orders": [
    {
      "order_id": 24,
      "state": "final",
      "open_until": null,
      "answers": 2,
      "leader": "5Ho6x2cpdd34W89rgFrUTms4JsEZfiWKyUpwW9vnpbGDvsN4"
    },
    ...
  ]
}
```

`open_until` is the block an open order closes at, `null` otherwise;
`answers` counts the solvers that answered.

## status

```sh
xquad quip --network aglais status ORDER_ID [--short]
```

Prints the order's receipt, the same box `print(receipt)` renders in
Python; [The receipt box](../../solving/quip-orders.md#the-receipt-box)
explains each row. Any order on the network can be read, not only the
account's own:

```text
$ xquad quip --network aglais status 20
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

`--short` prints the one line `orders` prints, for scripts:

```text
$ xquad quip --network aglais status 20 --short
20	final	5GqG8c8zWHX33ruGJX4FDokxCTFrASVi268einKJY1XnHNYn
```

`--json --short` prints that line's fields as one object. `--json`
alone prints `summary` (those fields), `status` (the receipt's
`status()` dict), `solvers` and `settlement`, as [Receipts](../../solving/quip-orders.md#receipts)
describes them. An id that names no order exits 1:

```text
Error: order 999999 not found on-chain (QuantumComputeMempool.JobOrders)
```

## reclaim

```sh
xquad quip --network aglais reclaim ORDER_ID
xquad quip --network aglais reclaim --all [--yes]
```

Returns the reserved reward of a final order nobody answered to the
account that proposed it. It is the one `xquad quip` command that signs:
each reclaim is a transaction from the account in effect and pays its
own fee. Pass an order id or `--all`, not both.

`reclaim ORDER_ID` refuses before signing when the order was proposed by
another account, is already closed, is still open, or was answered; the
reasons are listed under
[Reclaiming](../../solving/quip-orders.md#reclaiming). A refused reclaim
costs nothing.

`reclaim --all` takes every order `orders --state unanswered` lists. It
shows them and their total, then asks before signing anything. The
output below is illustrative, assembled from the messages in
`xquad/cli/quip.py` rather than captured, with made-up order ids:

```text
$ xquad quip --network aglais reclaim --all
order 31  1.000000000000 AGLS
order 28  1.000000000000 AGLS
2 orders, 2.000000000000 AGLS to recover; each reclaim pays its own fee
Reclaim them? [y/N]: y
reclaimed order 31: 1.000000000000 AGLS refunded
reclaimed order 28: 1.000000000000 AGLS refunded
2.000000000000 AGLS recovered from 2 of 2 orders
```

The prompt defaults to no; declining reclaims nothing and exits 1.
Without a terminal, or with `--json`, there is no prompt, so `--yes` is
required:

```text
Error: pass --yes to reclaim without a prompt
```

With nothing to reclaim it prints `nothing to reclaim on <account>`. An
order that is refused or fails part way is reported on stderr as
`order N not reclaimed: <reason>`, and the rest still go ahead; the
command then exits 1. With `--json`, `reclaim ORDER_ID` prints
`order_id` and `refunded`, and `reclaim --all --yes` prints the
`reclaimed` and `refused` lists and the `total` refunded, in planck.
