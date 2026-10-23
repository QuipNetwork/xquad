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

"""
Named Quip accounts, stored per project and per user, and the account preference.

Keystores and preferences live at two levels::

    =======  ================================================  ==============================
    Level    Keystores                                          Preference
    =======  ================================================  ==============================
    project  ``.quip/keystores/<name>.json``                    ``.quip/config.toml``
    user     ``~/.quip/keystores/<name>.json``, and             ``~/.quip/config.toml``
             ``~/.quip/keystore.json`` as ``default``
    =======  ================================================  ==============================

The project level is the nearest ``.quip/`` found walking up from the working
directory, stopping below the home directory, so ``~/.quip/`` is never a
project. A preference is the single line ``account = "<name>"``; a name at
both levels resolves to the project one.

:func:`resolve_accounts` lists the keystores and walks the signer precedence
``SolverQuip`` follows: ``seed=``, ``keystore=``, ``QUIP_SIGNER_SEED``,
``QUIP_KEYSTORE``, the project preference, the user preference, then
``default``. It reads each account from the keystore's cached
``account_id_hex``, never the seed, and never writes a file.
:func:`create_account`, :func:`use_account` and :func:`clear_account` write.
Creating anything under a project ``.quip/`` also writes ``.quip/.gitignore``
containing ``*`` when there is none, so a seed never reaches a commit.

Examples:
    Writes under the home directory, so it is not run as a doctest::

        create_account("work", level="user")
        use_account("work", level="project")
        print(resolve_accounts().in_effect)
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Level = Literal["project", "user"]

_LEVELS = ("project", "user")
_NAME_RULE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_QUIP_DIR = ".quip"
_CONFIG = "config.toml"
_KEYSTORES = "keystores"
# The user-level ``default`` is the keystore ``SolverQuip()`` has always generated.
_USER_DEFAULT = "keystore.json"
# What a seed source reports as its value: the seed itself is never echoed.
_SET = "set"


@dataclass(frozen=True)
class Account:
    """One keystore file found on disk.

    ``account_id`` is read from the file's cached ``account_id_hex``; it is
    ``None`` when the file is unusable, and ``problem`` says why.
    """

    name: str
    level: Level
    path: Path
    account_id: bytes | None
    problem: str | None


@dataclass(frozen=True)
class SignerSource:
    """One step of the signer precedence.

    ``value`` is the name or path set at that step, ``None`` when the step is
    unset; for ``seed=`` and ``QUIP_SIGNER_SEED`` it is the marker ``"set"``.
    ``path`` is the keystore the step points at, which for ``keystore=``,
    ``QUIP_KEYSTORE`` and ``default`` may not exist yet (it is created on first
    use). ``problem`` says why the step cannot sign.
    """

    step: Literal["seed=", "keystore=", "QUIP_SIGNER_SEED", "QUIP_KEYSTORE", "project", "user", "default"]
    value: str | None
    path: Path | None
    problem: str | None


@dataclass(frozen=True)
class AccountResolution:
    """The keystores found and the signer precedence, with the step in effect.

    ``accounts`` lists the project keystores, then the user ones.
    ``sources`` holds every precedence step in order; ``in_effect`` is the
    first one set, the step a signer is built from.
    """

    accounts: tuple[Account, ...]
    sources: tuple[SignerSource, ...]
    in_effect: SignerSource | None


def resolve_accounts(*, seed: bytes | str | None = None, keystore: str | Path | None = None) -> AccountResolution:
    """List the Quip accounts and resolve which one signs, without writing anything.

    ``seed`` and ``keystore`` are the ``SolverQuip`` arguments of the same
    name. An unusable keystore or preference is reported in its ``problem``
    rather than raised, so a stale file never breaks a listing.

    Examples:
        Reads the home directory, so it is not run as a doctest::

            resolution = resolve_accounts()
            print(resolution.in_effect.step, resolution.in_effect.path)
    """
    project = _find_project()
    user = _find_user_dir()
    accounts = (*_list_accounts(project, "project"), *_list_accounts(user, "user"))
    default_path = user / _USER_DEFAULT if user is not None else None
    by_level: dict[str, dict[str, Account]] = {level: {} for level in _LEVELS}
    for account in accounts:
        # A stray ``~/.quip/keystores/default.json`` is listed but never resolves as ``default``.
        if not (account.level == "user" and account.name == "default" and account.path != default_path):
            by_level[account.level].setdefault(account.name, account)
    env_seed = os.environ.get("QUIP_SIGNER_SEED")
    env_keystore = os.environ.get("QUIP_KEYSTORE")
    default = by_level["user"].get("default")
    sources = (
        SignerSource("seed=", _SET if seed else None, None, None),
        _keystore_source("keystore=", keystore),
        SignerSource("QUIP_SIGNER_SEED", _SET if env_seed else None, None, None),
        _keystore_source("QUIP_KEYSTORE", env_keystore),
        _preference_source("project", project, (by_level["project"], by_level["user"])),
        _preference_source("user", user, (by_level["user"],)),
        SignerSource("default", "default", default_path, default.problem if default else None),
    )
    in_effect = next((source for source in sources if source.value is not None), None)
    return AccountResolution(accounts=accounts, sources=sources, in_effect=in_effect)


def create_account(
    name: str = "default", *, level: Level, seed: bytes | str | None = None, overwrite: bool = False
) -> Account:
    """Write a new keystore named ``name`` at ``level``, from ``seed`` or a fresh one.

    ``seed`` takes the forms :func:`xqsa.quip.signing.signer_from_seed` does.
    ``default`` at user level is ``~/.quip/keystore.json``. At project level
    with no ``.quip/`` found, one is made in the working directory.

    Raises:
        ValueError: if ``name`` or ``level`` is invalid, the project level is
            asked for in the home directory, or the working directory's
            ``.quip`` is not a usable project directory.
        QuipSigningError: if the keystore exists and ``overwrite`` is False,
            or ``seed`` is not 32 bytes.
        ImportError: if the ``[quip]`` extra is not installed.
    """
    from xqsa.quip import signing

    _check_name(name)
    seed_bytes = os.urandom(signing.MASTER_SEED_LEN) if seed is None else signing._coerce_seed(seed)
    signer = signing.signer_from_seed(seed_bytes)
    base = _level_dir_for_write(level)
    path = _keystore_path(base, level, name)
    if path.exists() and not overwrite:
        raise signing._keystore_exists(path)
    if level == "project":
        _write_gitignore(base)
    try:
        signing._write_keystore(path, seed_bytes, signer, overwrite=overwrite)
    except FileExistsError as exc:
        raise signing._keystore_exists(path) from exc
    return Account(name=name, level=level, path=path, account_id=bytes(signer.account_id), problem=None)


def use_account(name: str, *, level: Level) -> None:
    """Set the account preference at ``level`` to ``name``.

    A project preference may name a project or a user keystore; a user
    preference applies in every directory, so it may name only a user one.

    Raises:
        ValueError: if ``name`` or ``level`` is invalid, no keystore by that
            name resolves from ``level``, the existing ``config.toml`` holds
            other keys, the project level is asked for in the home directory,
            or the working directory's ``.quip`` is not a usable project directory.
    """
    _check_name(name)
    _check_level(level)
    project = _find_project()
    in_project = level == "project" and project is not None and _keystore_path(project, "project", name).exists()
    if not in_project and not _keystore_path(_user_dir(), "user", name).exists():
        where = "project or user" if level == "project" else "user"
        raise ValueError(f"no {where} keystore named {name!r}; create it with `xquad quip account new --name {name}`")
    base = _level_dir_for_write(level)
    config = base / _CONFIG
    _check_rewritable(config)
    base.mkdir(parents=True, exist_ok=True)
    if level == "project":
        _write_gitignore(base)
    # A temp file and a replace, so a concurrent reader never sees a partial preference.
    fd, tmp_name = tempfile.mkstemp(dir=base, prefix=f".{_CONFIG}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(f'account = "{name}"\n')
        os.replace(tmp_name, config)
    finally:
        Path(tmp_name).unlink(missing_ok=True)


def clear_account(*, level: Level) -> None:
    """Remove the account preference at ``level``; nothing to remove is not an error.

    Raises:
        ValueError: if ``level`` is invalid, or ``config.toml`` holds keys
            besides ``account``, is not valid TOML, or cannot be read.
    """
    _check_level(level)
    base = _find_project() if level == "project" else _user_dir()
    if base is None:
        return
    config = base / _CONFIG
    _check_rewritable(config)
    config.unlink(missing_ok=True)


def _user_dir() -> Path:
    try:
        return Path.home() / _QUIP_DIR
    except RuntimeError as exc:
        raise ValueError("cannot determine the home directory") from exc


def _find_user_dir() -> Path | None:
    """Return the user ``.quip/``, or ``None`` when no home directory can be determined."""
    try:
        return _user_dir()
    except ValueError:
        return None


def _find_project() -> Path | None:
    """Return the nearest project ``.quip/``, walking up and stopping below home.

    A working directory that no longer exists has no project, and a ``.quip``
    that resolves to the user level (a symlink), belongs to another user or is
    world-writable is never one.
    """
    try:
        cwd = Path.cwd().resolve()
    except OSError:
        return None
    user = _find_user_dir()
    home = user.parent.resolve() if user is not None else None
    user_quip = user.resolve() if user is not None else None
    for directory in (cwd, *cwd.parents):
        if directory == home:
            return None
        candidate = directory / _QUIP_DIR
        if candidate.is_dir() and candidate.resolve() != user_quip and _trusted(candidate):
            return candidate
    return None


def _trusted(directory: Path) -> bool:
    """Whether ``directory`` is owned by this user and not world-writable, so no other user planted it."""
    if not hasattr(os, "getuid"):
        return True  # shortcut: no ownership check on Windows; add an ACL check if shared Windows hosts matter.
    try:
        info = directory.stat()
    except OSError:
        return False
    return info.st_uid == os.getuid() and not info.st_mode & 0o002


def _level_dir_for_write(level: Level) -> Path:
    """Return the ``.quip/`` a write at ``level`` goes to, making no directory."""
    _check_level(level)
    if level == "user":
        return _user_dir()
    project = _find_project()
    if project is not None:
        return project
    cwd = Path.cwd().resolve()
    if cwd == _user_dir().parent.resolve():
        raise ValueError("the home directory is the user level; use `--global`")
    candidate = cwd / _QUIP_DIR
    if candidate.exists() or candidate.is_symlink():
        raise ValueError(
            f"{candidate} exists but is not a project .quip/ directory "
            "(a file, a link to the user level, another user's or world-writable)"
        )
    return candidate


def _check_name(name: str) -> None:
    if not isinstance(name, str) or not _NAME_RULE.fullmatch(name):
        raise ValueError(
            f"account name {name!r} must match {_NAME_RULE.pattern}: "
            "a lowercase letter or digit, then up to 63 of those, '_' or '-'"
        )


def _check_level(level: str) -> None:
    if level not in _LEVELS:
        raise ValueError(f"level={level!r}; expected 'project' or 'user'")


def _keystore_path(base: Path, level: Level, name: str) -> Path:
    if level == "user" and name == "default":
        return base / _USER_DEFAULT
    return base / _KEYSTORES / f"{name}.json"


def _write_gitignore(base: Path) -> None:
    """Write ``.gitignore`` containing ``*`` into ``base``, only when absent."""
    base.mkdir(parents=True, exist_ok=True)
    try:
        with (base / ".gitignore").open("x") as handle:
            handle.write("*\n")
    except FileExistsError:
        pass  # a team may commit its own, e.g. sharing config.toml.


def _list_accounts(base: Path | None, level: Level) -> list[Account]:
    """List the keystores under ``base``; what the process cannot read is left out."""
    if base is None:
        return []
    found = []
    try:
        if level == "user" and (base / _USER_DEFAULT).is_file():
            found.append(_read_account("default", level, base / _USER_DEFAULT))
        keystores = base / _KEYSTORES
        if keystores.is_dir():
            for path in sorted(keystores.glob("*.json")):
                account = _read_account(path.stem, level, path)
                if level == "user" and path.stem == "default":
                    problem = f"never used: the user `default` account is {base / _USER_DEFAULT}"
                    account = Account(account.name, level, path, account.account_id, problem)
                found.append(account)
    except OSError:
        pass  # keep what was read before the level became unreadable.
    return found


def _read_account(name: str, level: Level, path: Path) -> Account:
    """Read the cached account of the keystore at ``path``; never the seed."""
    problem = None
    account_id = None
    if not _NAME_RULE.fullmatch(name):
        problem = f"{name!r} is not a valid account name"
    else:
        account_id, problem = _cached_account_id(path)
    return Account(name=name, level=level, path=path, account_id=account_id, problem=problem)


def _cached_account_id(path: Path) -> tuple[bytes | None, str | None]:
    from xqsa.quip import signing

    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return None, f"not readable JSON: {exc}"
    if not isinstance(raw, dict):
        return None, "not a JSON object"
    if raw.get("version") != signing.KEYSTORE_VERSION:
        return None, f"keystore version {raw.get('version')!r} not supported"
    scheme = raw.get("scheme")
    if scheme == "hybrid":
        return None, "an H3 account the chain no longer accepts; create a new one"
    if scheme != signing.KEYSTORE_SCHEME:
        return None, f"keystore scheme {scheme!r} not supported"
    if raw.get("encrypted"):
        return None, "passphrase-encrypted keystores are not supported yet"
    cached = raw.get("account_id_hex")
    try:
        account_id = bytes.fromhex(signing._strip_0x(cached)) if isinstance(cached, str) else None
    except ValueError:
        account_id = None
    if account_id is None:
        return None, "no readable 'account_id_hex' field"
    if len(account_id) != 32:
        return None, f"'account_id_hex' is {len(account_id)} bytes, not 32"
    return account_id, None


def _keystore_source(step: Literal["keystore=", "QUIP_KEYSTORE"], keystore: str | Path | None) -> SignerSource:
    if not keystore:
        return SignerSource(step, None, None, None)
    try:
        path = Path(keystore).expanduser()
    except RuntimeError as exc:  # ``~user`` naming no user here.
        return SignerSource(step, str(keystore), None, f"cannot expand {keystore}: {exc}")
    # A missing path is created on first use; anything already there must be a usable keystore.
    try:
        problem = _cached_account_id(path)[1] if path.exists() else None
    except OSError as exc:  # e.g. under a directory this process cannot enter.
        problem = f"cannot read {path}: {exc}"
    return SignerSource(step, str(keystore), path, problem)


def _preference_source(
    step: Literal["project", "user"], base: Path | None, lookups: tuple[dict[str, Account], ...]
) -> SignerSource:
    """The precedence step for the preference in ``base``, looked up in ``lookups`` in order."""
    if base is None:
        return SignerSource(step, None, None, None)
    config = base / _CONFIG
    name, problem = _read_preference(config)
    if problem is not None:
        return SignerSource(step, str(config), None, problem)
    if name is None:
        return SignerSource(step, None, None, None)
    account = next((found[name] for found in lookups if name in found), None)
    if account is None:
        problem = f"no keystore named {name!r}; create it with `xquad quip account new --name {name}`"
        return SignerSource(step, name, None, problem)
    return SignerSource(step, name, account.path, account.problem)


def _read_preference(config: Path) -> tuple[str | None, str | None]:
    """Return the preferred name in ``config``, or the problem reading it."""
    try:
        with config.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return None, f"{config} is not readable TOML: {exc}"
    name = raw.get("account")
    if name is None:
        return None, None
    if not isinstance(name, str):
        return None, f"{config}: `account` must be a string, got {type(name).__name__}"
    if not _NAME_RULE.fullmatch(name):
        return None, f"{config}: {name!r} is not a valid account name"
    return name, None


def _check_rewritable(config: Path) -> None:
    """Refuse to rewrite a ``config.toml`` the single-line writer would lose keys from."""
    try:
        with config.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError:
        return
    except OSError as exc:  # a directory, or a file this process cannot read.
        raise ValueError(f"{config} cannot be read ({exc}); fix it by hand") from exc
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"{config} is not valid TOML ({exc}); edit it by hand") from exc
    if set(raw) - {"account"}:
        raise ValueError(f"{config} has keys besides `account`; edit it by hand")
