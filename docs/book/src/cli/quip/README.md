# xquad quip

`xquad quip` manages Quip accounts and reads the job orders they placed
on a Quip network. It is a front end over the `xqsa.quip` Python API
that [Quip Network](../../solving/quip-network.md) and [Job
Orders](../../solving/quip-orders.md) describe; it places no orders
itself. Submit orders from Python, then follow them from the shell.

| Command | Description |
|---------|-------------|
| [`account list`](accounts.md#account-list) | The keystores found, the order that picks the signer, and how to change it. |
| [`account show`](accounts.md#account-show) | The account in effect: its source, balance, reserved amount and order slots. |
| [`account new`](accounts.md#account-new-and-account-import) | Create a keystore with a fresh seed, and use it. |
| [`account import`](accounts.md#account-new-and-account-import) | Create a keystore from a seed you already have, and use it. |
| [`account use`](accounts.md#account-use) | Prefer an account at a level, or clear the preference. |
| [`orders`](orders.md#orders) | The account's orders not yet closed, one line each. |
| [`status`](orders.md#status) | One order's receipt. |
| [`reclaim`](orders.md#reclaim) | Recover the reward of a final order nobody answered. |

## Installation

The group needs the `quip` extra, which brings in the chain client and
the signing extension:

```sh
pip install 'xquad[quip]'
```

Without it, every `xquad quip` command, and `--help` below the group
level, prints this on stderr and exits 1, while `xquad quip --help`,
`xquad --help` and `xquad vm` keep working:

```text
`xquad quip` needs the Quip extra: `pip install 'xquad[quip]'`
```

## Global options

These go between `quip` and the command, as in
`xquad quip --network aglais account show`:

| Flag | Meaning |
|------|---------|
| `--network NAME` | A named network preset, such as `aglais` (see [Named networks](../../solving/quip-network.md#named-networks)). |
| `--url URL` | A node's RPC URL. Defaults to `QUIP_RPC_URL`. |
| `--keystore PATH` | Sign with this keystore, over every preference. |
| `--json` | Print one JSON object on stdout instead of text. |

`--network` and `--url` are exclusive. A command that reads the chain
needs one of them, or `QUIP_RPC_URL`:

```text
Error: name a network with --network or a node with --url, or set QUIP_RPC_URL
```

`account list`, `account new`, `account import` and `account use` work
on local files only and need no network.

## What signs

Only `reclaim` sends a transaction, and only it spends anything: each
reclaim pays its own fee. `account show`, `orders` and `status` read
through a read-only client that resolves no signer, so they never create
a keystore. Under `QUIP_SIGNER_SEED`, `account show` and `orders` still
build a signer, to learn which account the seed is; they sign nothing
with it. `account new`, `account import` and `account use` write files
under `.quip/` or `~/.quip/` and touch no chain.

## Output and exit codes

With `--json`, a command prints exactly one JSON object on stdout.
Accounts are SS58 text and amounts are integers in planck; the text
output formats amounts in the network's token instead. `--json` is an
option of the group, so it goes before the command:
`xquad quip --network aglais --json account show`. Errors go to stderr
prefixed `Error:`, in both modes. The no-account error carries the
`account list` guidance under it, and a usage error prints a `Usage:`
line above it; the per-order lines of `reclaim --all` and the
missing-extra hint have no prefix.

| Exit code | Meaning |
|-----------|---------|
| `0` | Success. |
| `1` | The command failed: a chain or account error, the missing extra, no account set up, or a `reclaim --all` in which some order was not reclaimed. |
| `2` | A usage error, such as an unknown flag or `--network` with `--url`. |
