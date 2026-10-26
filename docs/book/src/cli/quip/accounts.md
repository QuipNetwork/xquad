# Accounts

A Quip account is a keystore file holding a 32-byte master seed in plain
text. Keystores live at two levels, as git keeps config: a project level
in the nearest `.quip/` directory above the working directory, and a
user level in `~/.quip/`. Each level also holds an account preference
naming the keystore to sign with. [Accounts](../../solving/quip-network.md#accounts)
on the Quip Network page covers the layout and the full signer order;
this page covers the commands.

The outputs on this page were captured on 2026-10-26 in a fresh project
directory with a throwaway account, against `aglais` at `specVersion 119`.

## Levels

`account new`, `account import` and `account use` act on one level:

- `--project` acts on the project level, creating `.quip/` in the
  working directory when none is found.
- `--global` acts on the user level.
- With neither, the level is the project when the working directory is
  inside one, else the user.

Anything created under a project `.quip/` also writes a
`.quip/.gitignore` of `*` when there is none, so keystores stay out of
version control.

## account list

```sh
xquad quip account list
```

Lists every keystore found, the order that decides which one signs, and
how to change it. It reads local files only:

```text
Keystores found:

* default  project  .quip/keystores/default.json
           5FXRydTJYE4cPGBH9CSf3dyGvaJNnYATEZJMyFqMiLPHv9oc

Resolution order (the first match signs):

  1  QUIP_SIGNER_SEED     unset
  2  QUIP_KEYSTORE        unset
* 3  project preference   default (.quip/config.toml)
  4  user preference      unset
  5  default              ~/.quip/keystore.json

Setting the account:

  xquad quip account use <name>             prefer <name> in this project, else for your user
  xquad quip account use <name> --global    prefer <name> for your user
  xquad quip account use --unset [--global] clear the preference at that level
  --keystore PATH, QUIP_KEYSTORE, QUIP_SIGNER_SEED override the preferences
```

`*` marks the keystore that signs and the step that chose it. A keystore
that cannot be read, or a preference naming one that does not exist, is
listed with a `!` line saying why, rather than failing the listing.
With `--keystore PATH`, the keystore list marks that file `*` with an
`(override)` suffix, as a `-` row when it is not one of the listed
keystores, and no step of the resolution order is marked.

## account show

```sh
xquad quip --network aglais account show
```

The setup check: the account in effect, where it came from, and what it
holds on the network.

```text
Account   5FXRydTJYE4cPGBH9CSf3dyGvaJNnYATEZJMyFqMiLPHv9oc
Source    project preference default
Balance   0.000000000000 AGLS
Reserved  0.000000000000 AGLS
Orders    0 of 32 slots used
```

`Balance` is the free balance an order's reward and fee are paid from.
`Reserved` is the reward still held by orders not yet closed, plus
anything else the runtime reserves. `Orders` counts the slots the
account's unclosed orders hold against the runtime's
`MaxOrdersPerProposer`; each order holds a slot until a claim or a
reclaim closes it, and the line reads `unlimited` when the runtime sets
no cap.

With `--json`:

```json
{
  "account": "5FXRydTJYE4cPGBH9CSf3dyGvaJNnYATEZJMyFqMiLPHv9oc",
  "source": "project",
  "balance": 0,
  "reserved": 0,
  "order_slots": {
    "used": 0,
    "limit": 32
  }
}
```

With no usable account, it prints the `account list` guidance on stderr,
creates nothing, and exits 1:

```text
Error: No Quip account is set up.

No keystores found. Create one with `xquad quip account new`, or bring a seed with `xquad quip account import`.
...
```

When nothing at all is set up (no seed, no override, no preference and
no default keystore) and the command runs at a terminal without
`--json`, it first offers to create one: `new`, `import` or `skip` (the
default), then the level and the name. After creating it, it shows the
new account when a network is named; without `--network`, `--url` or
`QUIP_RPC_URL`, the keystore is still created and the command then
exits 1 with the missing-network error. Skipping exits 1. A broken
step, such as a preference naming a missing keystore, gets an error
naming the fix instead of the offer.

## account new and account import

```sh
xquad quip account new [--name NAME] [--project | --global] [--force] [--no-use]
xquad quip account import [--name NAME] [--project | --global] [--no-use]
```

`account new` writes a keystore from a fresh seed; `account import`
writes one from a seed you already have, as 32 bytes of hex. The
seed is read from a hidden prompt at a terminal, else from the first
line of stdin, and has no flag, so it never lands in shell history:

```sh
xquad quip account import --name work < seed.txt
```

`--name` defaults to `default`. Names are lowercase letters, digits, `_`
and `-`, at most 64 characters, starting with a letter or digit.

```text
$ xquad quip account new --project
created default at the project level: .quip/keystores/default.json
  5FXRydTJYE4cPGBH9CSf3dyGvaJNnYATEZJMyFqMiLPHv9oc
now in use at the project level; change it with `xquad quip account use`
```

The new account becomes the preference at its level, unless `--no-use`
is given or it is the user-level `default`, which signs when nothing
else is set and needs no preference. Neither command replaces an
existing keystore of the same name: `account new --force` replaces it
and names the account it replaced, and `account import` asks for
another `--name`.

## account use

```sh
xquad quip account use NAME [--project | --global]
xquad quip account use --unset [--project | --global]
```

Sets the preference at a level to the keystore `NAME`, or clears it with
`--unset`. A project preference may name a project or a user keystore; a
user preference applies in every directory, so it names a user keystore.
Pass a name or `--unset`, not both. `--keystore`, `QUIP_KEYSTORE` and
`QUIP_SIGNER_SEED` still override any preference.

Here a user keystore `work`, made with `account new --name work
--global --no-use`, is chosen for the project, then the preference is
cleared again:

```text
$ xquad quip account use work
work is now in use at the project level: 5FRkgm6s4rddCTdMWGU4aPm3Uu642r96oCXLZmcwp184ByNp
$ xquad quip account use --unset
cleared the project account preference
```

`account new`, `account import` and `account use` take `--json` too,
before the command (`xquad quip --json account use work --global`),
printing the account, its name, the level and the keystore path (here
with the home directory shortened):

```json
{
  "account": "5FRkgm6s4rddCTdMWGU4aPm3Uu642r96oCXLZmcwp184ByNp",
  "name": "work",
  "level": "user",
  "path": "/home/me/.quip/keystores/work.json"
}
```

`account use --unset` prints `null` for the account and name, and the
cleared `config.toml` as the path. `account list --json` prints the
keystores, the resolution steps and the step in effect.
