# Copyright (C) 2026 Postquant Labs Incorporated
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`xquad quip`: Quip accounts and order receipts.

A thin front end over `xqsa.quip`: the account functions, `SolverQuip`,
`list_orders`, `get_receipt` and `JobOrderReceipt.reclaim`. `xqsa.quip` is
imported inside each command, so `xquad --help` and `xquad vm ...` never
load it. Every command but `reclaim`, `account new`, `account import` and
`account use` reads through a read-only client; under `QUIP_SIGNER_SEED`
the account comes from a signing one, so the seed loads in one place.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

_EXTRA_HINT = "`xquad quip` needs the Quip extra: `pip install 'xquad[quip]'`"
# The resolution steps `xquad quip` can put in effect, numbered as `account list` shows them.
_ROWS = (
    ("QUIP_SIGNER_SEED", "QUIP_SIGNER_SEED"),
    ("QUIP_KEYSTORE", "QUIP_KEYSTORE"),
    ("project", "project preference"),
    ("user", "user preference"),
    ("default", "default"),
)
_SETTING_HELP = """Setting the account:

  xquad quip account use <name>             prefer <name> in this project
  xquad quip account use <name> --global    prefer <name> for your user
  xquad quip account use --unset [--global] clear a preference
  --keystore PATH, QUIP_KEYSTORE, QUIP_SIGNER_SEED override the preferences"""
_NO_KEYSTORES = (
    "No keystores found. Create one with `xquad quip account new`, or bring a seed with `xquad quip account import`."
)


@dataclass(frozen=True)
class _Options:
    network: str | None
    url: str | None
    keystore: str | None
    json: bool


@click.group()
@click.option("--network", metavar="NAME", help="A named Quip network, such as aglais.")
@click.option("--url", metavar="URL", help="A node's RPC URL; defaults to QUIP_RPC_URL.")
@click.option("--keystore", metavar="PATH", help="Sign with this keystore, over every preference.")
@click.option("--json", "as_json", is_flag=True, help="Print one JSON object on stdout.")
@click.pass_context
def quip(ctx: click.Context, network: str | None, url: str | None, keystore: str | None, as_json: bool) -> None:
    """Quip accounts and order receipts.

    `account show`, `orders` and `status` sign nothing; under
    QUIP_SIGNER_SEED, `account show` and `orders` still build a signer to
    learn the account.
    """
    try:
        import quip_signer  # noqa: F401 -- presence check for the extra.
        import substrateinterface  # noqa: F401 -- presence check for the extra.
    except ImportError:
        click.echo(_EXTRA_HINT, err=True)
        ctx.exit(1)
    if network and url:
        raise click.UsageError("pass --network or --url, not both")
    ctx.obj = _Options(network=network, url=url, keystore=keystore, json=as_json)


@quip.group()
def account() -> None:
    """List, create, import and choose Quip accounts."""


@account.command("list")
@click.pass_obj
def account_list(opts: _Options) -> None:
    """The keystores found, the resolution order, and how to change it."""
    from xqsa.quip import resolve_accounts

    resolution = resolve_accounts(keystore=opts.keystore)
    if opts.json:
        _emit(
            {
                "accounts": [_account_json(a) for a in resolution.accounts],
                "sources": [_source_json(s) for s in resolution.sources if s.step != "seed="],
                "in_effect": None if resolution.in_effect is None else resolution.in_effect.step,
            }
        )
    else:
        click.echo(_listing(resolution))


@account.command("show")
@click.pass_obj
def account_show(opts: _Options) -> None:
    """The account in effect: its source, balance, reserved amount and order slots.

    The setup check. With no account it prints the guidance `account list`
    does and exits 1, creating nothing; at a terminal with nothing set up at
    all, it first offers to create one.
    """
    from xqsa.quip import resolve_accounts

    resolution = resolve_accounts(keystore=opts.keystore)
    source = resolution.in_effect
    if not _has_account(source):
        message = _no_account(resolution)
        if not (_nothing_set(source) and _interactive(opts)):
            raise click.ClickException(message)
        click.echo(message, err=True)
        if not _offer_account():
            sys.exit(1)
        resolution = resolve_accounts(keystore=opts.keystore)
    with _errors():
        solver, owner = _reader(opts, resolution.in_effect)
        address = solver.account if owner is None else _ss58(owner, solver)
        balance, reserved = solver.balance(owner), solver.reserved(owner)
        used, limit = solver.order_slots(owner)
    if opts.json:
        _emit(
            {
                "account": address,
                "source": resolution.in_effect.step,
                "balance": balance,
                "reserved": reserved,
                "order_slots": {"used": used, "limit": limit},
            }
        )
        return
    amount = _amount(solver)
    click.echo(f"Account   {address}")
    click.echo(f"Source    {_describe(resolution.in_effect)}")
    click.echo(f"Balance   {amount(balance)}")
    click.echo(f"Reserved  {amount(reserved)}")
    click.echo(f"Orders    {used} of {'unlimited' if limit is None else limit} slots used")


_LEVEL_OPTIONS = (
    click.option("--global", "user_level", is_flag=True, help="Act on the user level."),
    click.option("--project", "project_level", is_flag=True, help="Act on the project level, creating .quip/."),
)


def _level_options(command: Any) -> Any:
    for option in reversed(_LEVEL_OPTIONS):
        command = option(command)
    return command


@account.command("new")
@click.option("--name", default="default", show_default=True, help="The account's name.")
@_level_options
@click.option("--force", is_flag=True, help="Replace a keystore of the same name.")
@click.option("--no-use", is_flag=True, help="Do not set the account preference.")
@click.pass_obj
def account_new(opts: _Options, name: str, user_level: bool, project_level: bool, force: bool, no_use: bool) -> None:
    """Create a keystore with a fresh seed, and use it.

    The level is the project when inside one, else the user, as git picks
    its config. The new account becomes the preference at that level unless
    `--no-use`, or it is the user default, which resolves without one.
    """
    _create(opts, name, _level(user_level, project_level), seed=None, force=force, no_use=no_use)


@account.command("import")
@click.option("--name", default="default", show_default=True, help="The account's name.")
@_level_options
@click.option("--no-use", is_flag=True, help="Do not set the account preference.")
@click.pass_obj
def account_import(opts: _Options, name: str, user_level: bool, project_level: bool, no_use: bool) -> None:
    """Create a keystore from a seed read from a prompt or stdin, and use it.

    The seed is 32 bytes of hex. It is never echoed, and there is no flag
    for it, so it never lands in shell history. Levels and the preference
    work as for `account new`.
    """
    level = _level(user_level, project_level)
    _create(opts, name, level, seed=_read_seed(), force=False, no_use=no_use)


@account.command("use")
@click.argument("name", required=False)
@_level_options
@click.option("--unset", is_flag=True, help="Clear the preference instead.")
@click.pass_obj
def account_use(opts: _Options, name: str | None, user_level: bool, project_level: bool, unset: bool) -> None:
    """Prefer the account NAME at a level, or clear the preference with --unset."""
    from xqsa.quip import clear_account, resolve_accounts, use_account

    if unset == (name is not None):
        raise click.UsageError("pass an account name or --unset")
    level = _level(user_level, project_level)
    with _errors():
        if unset:
            clear_account(level=level)
        else:
            use_account(name, level=level)
    config = _config_path(level)
    found = next(
        (a for a in resolve_accounts().accounts if a.name == name and (level == "project" or a.level == "user")),
        None,
    )
    address = None if found is None or found.account_id is None else _ss58(found.account_id)
    path = config if found is None else found.path  # the keystore now in use, or the cleared config.toml.
    if opts.json:
        _emit({"account": address, "name": name, "level": level, "path": None if path is None else str(path)})
    elif unset:
        click.echo(f"cleared the {level} account preference")
    else:
        click.echo(f"{name} is now in use at the {level} level: {address or 'account unreadable'}")


@quip.command()
@click.option(
    "--state",
    type=click.Choice(["submitted", "finalized", "unanswered"]),
    help="Keep only open, final, or final and unanswered orders.",
)
@click.option("--account", "account_arg", metavar="SS58", help="Another account's orders.")
@click.pass_obj
def orders(opts: _Options, state: str | None, account_arg: str | None) -> None:
    """The account's orders not yet closed, newest first, one line each."""
    with _errors():
        if account_arg is None:
            from xqsa.quip import resolve_accounts

            resolution = resolve_accounts(keystore=opts.keystore)
            if not _has_account(resolution.in_effect):
                raise click.ClickException(_no_account(resolution))
            solver, owner = _reader(opts, resolution.in_effect)
        else:
            solver, owner = _connect(opts, signing=False), account_arg
        summaries = [solver.get_receipt(order_id).summary() for order_id in solver.list_orders(state, owner)]
    if opts.json:
        _emit({"orders": summaries})
    elif not summaries:
        click.echo("no orders" + (f" in state {state}" if state else ""))
    else:
        for summary in summaries:
            click.echo(_short(summary))


@quip.command()
@click.argument("order_id", type=click.IntRange(min=0))
@click.option("--short", is_flag=True, help="One line: order id, state and leader, tab-separated.")
@click.pass_obj
def status(opts: _Options, order_id: int, short: bool) -> None:
    """The receipt of ORDER_ID: its terms, outcome and money."""
    with _errors():
        receipt = _connect(opts, signing=False).get_receipt(order_id)
        summary = receipt.summary()
        if short and opts.json:
            _emit(summary)
            return
        if short or not opts.json:
            click.echo(_short(summary) if short else str(receipt))
            return
        try:
            settlement = receipt.settlement()
        except NotImplementedError:
            settlement = None
        _emit({"summary": summary, "status": receipt.status(), "solvers": receipt.solvers(), "settlement": settlement})


@quip.command()
@click.argument("order_id", type=click.IntRange(min=0), required=False)
@click.option("--all", "reclaim_all", is_flag=True, help="Every unanswered final order on the account.")
@click.option("--yes", is_flag=True, help="Reclaim without asking first.")
@click.pass_obj
def reclaim(opts: _Options, order_id: int | None, reclaim_all: bool, yes: bool) -> None:
    """Reclaim the reward of a final order nobody answered.

    A refused reclaim is caught before anything is signed. `--all` lists the
    reclaimable orders and their total, then asks before signing, since each
    reclaim pays its own fee.
    """
    from xqsa.quip import QuipSubmissionError, resolve_accounts

    if reclaim_all == (order_id is not None):
        raise click.UsageError("pass an order id or --all")
    resolution = resolve_accounts(keystore=opts.keystore)
    if not _has_account(resolution.in_effect):
        raise click.ClickException(_no_account(resolution))
    with _errors():
        solver = _connect(opts, signing=True)
        amount = _amount(solver)
        if order_id is not None:
            refunded = solver.get_receipt(order_id).reclaim()
            if opts.json:
                _emit({"order_id": order_id, "refunded": refunded})
            else:
                click.echo(f"reclaimed order {order_id}: {amount(refunded)} refunded")
            return
        ids = solver.list_orders(state="unanswered")
        if not ids:
            if opts.json:
                _emit({"reclaimed": [], "refused": [], "total": 0})
            else:
                click.echo(f"nothing to reclaim on {solver.account}")
            return
        # shortcut: reads the reward off the private order read; add it to summary() if more callers need it.
        rewards = {i: int(solver._fetch_order(i)["reward"]) for i in ids}
    if not opts.json:
        for i, reward in rewards.items():
            click.echo(f"order {i}  {amount(reward)}")
        click.echo(f"{len(ids)} orders, {amount(sum(rewards.values()))} to recover; each reclaim pays its own fee")
    if not yes:
        if not _interactive(opts):
            raise click.ClickException("pass --yes to reclaim without a prompt")
        if not click.confirm("Reclaim them?", default=False):
            raise click.ClickException("nothing reclaimed")
    reclaimed, refused = [], []
    with _errors():
        for i in ids:
            try:
                refunded = solver.get_receipt(i).reclaim()
            except QuipSubmissionError as exc:  # refused, or sent with its outcome unknown: report and go on.
                refused.append({"order_id": i, "reason": str(exc)})
                click.echo(f"order {i} not reclaimed: {exc}", err=True)
                continue
            reclaimed.append({"order_id": i, "refunded": refunded})
            if not opts.json:
                click.echo(f"reclaimed order {i}: {amount(refunded)} refunded")
    total = sum(entry["refunded"] for entry in reclaimed)
    if opts.json:
        _emit({"reclaimed": reclaimed, "refused": refused, "total": total})
    else:
        click.echo(f"{amount(total)} recovered from {len(reclaimed)} of {len(ids)} orders")
    if refused:
        sys.exit(1)


@contextlib.contextmanager
def _errors() -> Iterator[None]:
    """Report the errors `xqsa.quip` raises as a one-line message on stderr and exit 1."""
    from xqsa.quip import QuipError

    try:
        yield
    except (QuipError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


def _emit(value: dict[str, Any]) -> None:
    click.echo(json.dumps(value, indent=2, default=lambda v: "0x" + v.hex() if isinstance(v, bytes) else str(v)))


def _interactive(opts: _Options) -> bool:
    """Whether a prompt may appear: stdin and stdout are terminals and `--json` is off."""
    return sys.stdin.isatty() and sys.stdout.isatty() and not opts.json


def _level(user_level: bool, project_level: bool) -> str:
    """The level `--global`/`--project` name, else the project when inside one, as git does."""
    from xqsa.quip import accounts

    if user_level and project_level:
        raise click.UsageError("pass --global or --project, not both")
    if user_level or project_level:
        return "user" if user_level else "project"
    return "project" if accounts._find_project() is not None else "user"


def _connect(opts: _Options, *, signing: bool) -> Any:
    """A `SolverQuip` on the network the options name; read-only unless `signing`."""
    from xqsa.quip import SolverQuip

    kwargs: dict[str, Any] = {"keystore": opts.keystore} if signing else {"read_only": True}
    if opts.network:
        return SolverQuip.for_network(opts.network, **kwargs)
    url = opts.url or os.environ.get("QUIP_RPC_URL")
    if not url:
        raise click.ClickException("name a network with --network or a node with --url, or set QUIP_RPC_URL")
    return SolverQuip(url=url, **kwargs)


def _reader(opts: _Options, source: Any) -> tuple[Any, bytes | None]:
    """The client and the account to read: a signing client under a seed, else read-only with `account=`."""
    if source.step == "QUIP_SIGNER_SEED":
        return _connect(opts, signing=True), None
    return _connect(opts, signing=False), source.account_id


def _has_account(source: Any) -> bool:
    return source is not None and (source.step == "QUIP_SIGNER_SEED" or source.account_id is not None)


def _nothing_set(source: Any) -> bool:
    """No seed, override, preference or default keystore: the only case `account show` offers to fix."""
    return source is not None and source.step == "default" and source.problem is None and not source.path.exists()


def _no_account(resolution: Any) -> str:
    """Why no account is in effect, and what fixes it."""
    source = resolution.in_effect
    if source is None or _nothing_set(source):
        return "No Quip account is set up.\n\n" + _listing(resolution)
    where = _describe(source)
    if source.problem is not None:
        if source.step in ("project", "user"):
            unset = "--unset --global" if source.step == "user" else "--unset"
            fix = f"fix it, or clear it with `xquad quip account use {unset}`"
        elif source.step == "default":
            fix = "replace it with `xquad quip account new --global --force`, or choose another account"
        else:
            fix = "point it at a usable keystore"
        return f"the {where} cannot sign: {source.problem}; {fix}"
    return f"the {where} names no keystore: {source.path} does not exist"


def _offer_account() -> bool:
    """Ask to create or import an account; write it after the last answer. False when skipped."""
    choice = click.prompt(
        "Create one now?", type=click.Choice(["new", "import", "skip"]), default="skip", show_choices=True
    )
    if choice == "skip":
        return False
    level = click.prompt("Level", type=click.Choice(["project", "user"]), default=_level(False, False))
    name = click.prompt("Name", default="default")
    seed = click.prompt("Seed (hex)", hide_input=True) if choice == "import" else None
    _create(_Options(None, None, None, json=False), name, level, seed=seed, force=False, no_use=False)
    click.echo()
    return True


def _read_seed() -> str:
    """The seed for `account import`: a hidden prompt at a terminal, else the first line of stdin."""
    if sys.stdin.isatty():
        return click.prompt("Seed (hex)", hide_input=True)
    seed = sys.stdin.readline().strip()
    if not seed:
        raise click.ClickException("no seed on stdin")
    return seed


def _create(opts: _Options, name: str, level: str, *, seed: str | None, force: bool, no_use: bool) -> None:
    """Write a keystore, then set the preference at its level unless `no_use` or it is the user default."""
    from xqsa.quip import accounts, create_account, resolve_accounts, use_account

    with _errors():
        path = accounts._keystore_path(accounts._level_dir_for_write(level), level, name)
        if path.exists() and not force:
            raise click.ClickException(f"an account named {name!r} already exists at {path}; --force replaces it")
        replaced = next((a for a in resolve_accounts().accounts if a.path == path), None)
        created = create_account(name, level=level, seed=seed, overwrite=force)
    user_default = level == "user" and name == "default"
    use_error = None
    if not (no_use or user_default):
        try:
            use_account(name, level=level)
        except ValueError as exc:
            use_error = exc
    address = _ss58(created.account_id)
    flag = " --global" if level == "user" else ""
    if use_error is not None:
        raise click.ClickException(
            f"wrote {created.path}, but could not set the {level} preference: {use_error}. "
            f"The keystore stays; choose it with `xquad quip account use {name}{flag}`"
        )
    if opts.json:
        _emit({"account": address, "name": name, "level": level, "path": str(created.path)})
        return
    click.echo(f"created {name} at the {level} level: {_display_path(created.path)}")
    click.echo(f"  {address}")
    if replaced is not None:
        old = "an unreadable keystore" if replaced.account_id is None else _ss58(replaced.account_id)
        click.echo(f"  replaced {old}")
    if user_default:
        click.echo("it is the user default, which signs when nothing else is set")
    elif no_use:
        click.echo(f"not in use; choose it with `xquad quip account use {name}{flag}`")
    else:
        click.echo(f"now in use at the {level} level; change it with `xquad quip account use`")


def _listing(resolution: Any) -> str:
    """The three sections of `account list`: keystores, resolution order, how to set the account."""
    by_step = {source.step: source for source in resolution.sources}
    in_effect = resolution.in_effect
    override = by_step.get("keystore=")
    rows = [(a.name, a.level, a.path, a.account_id, a.problem) for a in resolution.accounts]
    if override is not None and override.value is not None and all(row[2] != override.path for row in rows):
        rows.insert(0, ("-", "", override.path, override.account_id, override.problem))
    if rows:
        width = max(len(row[0]) for row in rows)
        lines = ["Keystores found:", ""]
        for name, level, path, account_id, problem in rows:
            marked = in_effect is not None and in_effect.path == path and in_effect.step != "QUIP_SIGNER_SEED"
            is_override = override is not None and override.value is not None and override.path == path
            suffix = "  (override)" if is_override else ""
            lines.append(f"{'*' if marked else ' '} {name:<{width}}  {level:<7}  {_display_path(path)}{suffix}")
            detail = _ss58(account_id) if account_id is not None else f"! {problem}"
            lines.append(f"{'':<{width + 4}}{detail}")
        keystores = "\n".join(lines)
    else:
        keystores = _NO_KEYSTORES
    order = ["Resolution order (the first match signs):", ""]
    for number, (step, label) in enumerate(_ROWS, start=1):
        source = by_step[step]
        if source.value is None:
            value = "unset"
        elif step == "QUIP_SIGNER_SEED":
            value = "set"
        elif step in ("project", "user"):
            config = _config_path(step)
            # An unreadable config.toml reports its own path as the value, not a name.
            value = "unreadable" if source.value == str(config) else f"{source.value:<7}"
            value += f" ({_display_path(config)})"
        else:
            value = _display_path(source.path) if source.path is not None else source.value
        order.append(f"{'*' if in_effect is source else ' '} {number}  {label:<19}  {value}")
        if source.value is not None and source.problem is not None:
            order.append(f"{'':<26}! {source.problem}")
    return "\n\n".join([keystores, "\n".join(order), _SETTING_HELP])


def _config_path(level: str) -> Path | None:
    """The `config.toml` holding the preference at `level`, `None` when there is no such level here."""
    from xqsa.quip import accounts

    base = accounts._find_project() if level == "project" else accounts._find_user_dir()
    return None if base is None else base / "config.toml"


def _describe(source: Any) -> str:
    """The step in effect, as `account show` names its source."""
    path = "" if source.path is None else _display_path(source.path)
    if source.step == "keystore=":
        return f"--keystore {path} (override)"
    if source.step == "QUIP_KEYSTORE":
        return f"QUIP_KEYSTORE {path}"
    if source.step in ("project", "user"):
        return f"{source.step} preference {source.value}"
    if source.step == "default":
        return f"default keystore {path}"
    return source.step


def _display_path(path: Path) -> str:
    """`path` relative to the working directory when below it, else with the home directory as `~`."""
    with contextlib.suppress(ValueError, OSError):
        return str(path.relative_to(Path.cwd()))
    with contextlib.suppress(ValueError, RuntimeError):
        return str(Path("~") / path.relative_to(Path.home()))
    return str(path)


def _ss58(account_id: bytes, solver: Any = None) -> str:
    """`account_id` in SS58: in the chain's format when connected, else the generic one keystores show."""
    from xqsa.quip import chain

    if solver is not None:
        return chain.ss58_address(solver._iface, account_id)
    try:
        from scalecodec.utils.ss58 import ss58_encode

        return ss58_encode(bytes(account_id), 42)
    except Exception:  # noqa: BLE001 -- display only.
        return "0x" + bytes(account_id).hex()


def _amount(solver: Any) -> Any:
    """A formatter from planck to the network's token, as the receipt box prints amounts."""
    from xqsa.quip.quote import _format_planck

    symbol, decimals = solver._token()
    return lambda planck: f"{_format_planck(planck, decimals)} {symbol}"


def _short(summary: dict[str, Any]) -> str:
    """The `status --short` line: order id, state and leader, tab-separated."""
    state = summary["state"]
    if state == "open":
        state = f"open until block {summary['open_until']}"
    return f"{summary['order_id']}\t{state}\t{summary['leader'] or 'none'}"


def _account_json(account: Any) -> dict[str, Any]:
    return {
        "name": account.name,
        "level": account.level,
        "path": str(account.path),
        "account": None if account.account_id is None else _ss58(account.account_id),
        "problem": account.problem,
    }


def _source_json(source: Any) -> dict[str, Any]:
    return {
        "step": source.step,
        "value": source.value,
        "path": None if source.path is None else str(source.path),
        "account": None if source.account_id is None else _ss58(source.account_id),
        "problem": source.problem,
    }
