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

"""`xquad quip` through click's `CliRunner`, on the fake chain and temporary home and project directories."""

from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

pytest.importorskip("scalecodec.utils.ss58")  # accounts print as SS58.

from xqsa.quip import (  # noqa: E402
    JobOrderReceipt,
    QuipConnectionError,
    QuipReclaimRefusedError,
    create_account,
    use_account,
)
from xqsa.tests.quip.test_client import UNIT, _chain_iface, _clear_quip_env, _install  # noqa: E402
from xqsa.tests.quip.test_receipt import LEADING, SOLVER, _receipt_order  # noqa: E402
from xquad.cli import main  # noqa: E402

# `xquad.cli.quip` names the click group the package re-exports, so reach the module itself.
quip_cli = importlib.import_module("xquad.cli.quip")

MEMPOOL = "QuantumComputeMempool"
SEED_A = "0x" + "0a" * 32
SEED_B = "0x" + "0b" * 32


def _files(root: Path) -> set[Path]:
    return set(root.rglob("*"))


def quip(*args: str, stdin: str | None = None) -> Result:
    return CliRunner().invoke(main, ["quip", *args], input=stdin)


@pytest.fixture
def chain(monkeypatch, quip_home, seed_signer):
    """A fake chain behind `QUIP_RPC_URL`: 4 AGLS free, 1 reserved, orders 3 and 7 open until block 100."""
    iface = _chain_iface(order=_receipt_order(), head=50, balance=4 * UNIT)
    iface.storage[("System", "Account")] = {"nonce": 0, "data": {"free": 4 * UNIT, "reserved": UNIT}}
    iface.storage[(MEMPOOL, "ProposerOrders")] = [3, 7]
    iface.constants[(MEMPOOL, "MaxOrdersPerProposer")] = 32
    _install(monkeypatch, iface)
    _clear_quip_env(monkeypatch)
    monkeypatch.setenv("QUIP_RPC_URL", "ws://fake:9944")
    return iface


def _ss58(account_id: bytes) -> str:
    return quip_cli._ss58(account_id)


class TestMissingNetwork:
    def test_needs_a_network(self, chain, monkeypatch) -> None:
        create_account(level="user", seed=SEED_A)
        monkeypatch.delenv("QUIP_RPC_URL")
        result = quip("account", "show")
        assert result.exit_code == 1
        assert "name a network with --network or a node with --url" in result.stderr

    def test_network_and_url_exclusive(self, chain) -> None:
        result = quip("--network", "aglais", "--url", "ws://x", "account", "list")
        assert result.exit_code == 2
        assert "pass --network or --url, not both" in result.stderr


class TestAccountList:
    def test_nothing_found_writes_nothing(self, chain, quip_home, tmp_path) -> None:
        before = _files(tmp_path)
        result = quip("account", "list")
        assert result.exit_code == 0, result.output
        assert result.stdout.startswith("No keystores found. Create one with `xquad quip account new`")
        assert "* 5  default              ~/.quip/keystore.json" in result.stdout
        assert _files(tmp_path) == before

    def test_marks_the_preference_in_effect(self, chain) -> None:
        probe = create_account("probe", level="project", seed=SEED_A)
        work = create_account("work", level="user", seed=SEED_B)
        use_account("probe", level="project")
        use_account("work", level="user")
        lines = quip("account", "list").stdout.splitlines()
        assert lines[:5] == [
            "Keystores found:",
            "",
            "* probe  project  .quip/keystores/probe.json",
            f"         {_ss58(probe.account_id)}",
            "  work   user     ~/.quip/keystores/work.json",
        ]
        assert "* 3  project preference   probe   (.quip/config.toml)" in lines
        assert "  4  user preference      work    (~/.quip/config.toml)" in lines
        assert f"         {_ss58(work.account_id)}" in lines
        assert "Setting the account:" in lines

    def test_keystore_override_is_marked_not_a_row(self, chain) -> None:
        work = create_account("work", level="user", seed=SEED_A)
        result = quip("--keystore", str(work.path), "account", "list")
        assert "* work  user     ~/.quip/keystores/work.json  (override)" in result.stdout
        assert [line for line in result.stdout.splitlines() if line.startswith("*")] == [
            "* work  user     ~/.quip/keystores/work.json  (override)"
        ]

    def test_unexpandable_keystore_override_shows_as_given(self, chain) -> None:
        result = quip("--keystore", "~nosuchuser-xq/key.json", "account", "list")
        assert result.exception is None or isinstance(result.exception, SystemExit), result.output
        assert "~nosuchuser-xq/key.json  (override)" in result.stdout

    def test_json(self, chain) -> None:
        work = create_account("work", level="user", seed=SEED_A)
        use_account("work", level="user")
        data = json.loads(quip("--json", "account", "list").stdout)
        assert data["in_effect"] == "user"
        assert data["accounts"] == [
            {
                "name": "work",
                "level": "user",
                "path": str(work.path),
                "account": _ss58(work.account_id),
                "problem": None,
            }
        ]
        assert [s["step"] for s in data["sources"]] == [
            "keystore=",
            "QUIP_SIGNER_SEED",
            "QUIP_KEYSTORE",
            "project",
            "user",
            "default",
        ]


class TestAccountShow:
    def test_no_account_gives_guidance_and_creates_nothing(self, chain, tmp_path) -> None:
        before = _files(tmp_path)
        result = quip("account", "show")
        assert result.exit_code == 1
        assert "No Quip account is set up." in result.stderr
        assert "Resolution order (the first match signs):" in result.stderr
        assert result.stdout == ""
        assert _files(tmp_path) == before

    def test_reports_the_account_and_its_numbers(self, chain) -> None:
        work = create_account("work", level="user", seed=SEED_A)
        use_account("work", level="user")
        result = quip("account", "show")
        assert result.exit_code == 0, result.output
        assert result.stdout.splitlines() == [
            f"Account   {_ss58(work.account_id)}",
            "Source    user preference work",
            "Balance   4.000000000000 AGLS",
            "Reserved  1.000000000000 AGLS",
            "Orders    2 of 32 slots used",
        ]

    def test_json(self, chain) -> None:
        default = create_account(level="user", seed=SEED_A)
        data = json.loads(quip("--json", "account", "show").stdout)
        assert data == {
            "account": _ss58(default.account_id),
            "source": "default",
            "balance": 4 * UNIT,
            "reserved": UNIT,
            "order_slots": {"used": 2, "limit": 32},
        }

    def test_seed_reads_through_the_signer(self, chain, monkeypatch, seed_signer) -> None:
        monkeypatch.setenv("QUIP_SIGNER_SEED", SEED_B)
        data = json.loads(quip("--json", "account", "show").stdout)
        assert data["source"] == "QUIP_SIGNER_SEED"
        assert data["account"] == _ss58(seed_signer.HybridSigner.from_seed(SEED_B).account_id)

    def test_broken_preference_names_its_fix_without_prompting(self, chain, monkeypatch, quip_home) -> None:
        home, _ = quip_home
        create_account("work", level="user", seed=SEED_A)
        use_account("work", level="user")
        (home / ".quip" / "keystores" / "work.json").unlink()
        monkeypatch.setattr(quip_cli, "_interactive", lambda opts: True)
        result = quip("account", "show", stdin="new\n")
        assert result.exit_code == 1
        assert "no keystore named 'work'" in result.stderr
        assert "`xquad quip account use --unset --global`" in result.stderr
        assert "Create one now?" not in result.output

    def test_missing_override_is_reported(self, chain, tmp_path) -> None:
        result = quip("--keystore", str(tmp_path / "gone.json"), "account", "show")
        assert result.exit_code == 1
        assert "names no keystore" in result.stderr and "gone.json does not exist" in result.stderr
        assert "drop --keystore, or point it at an existing keystore" in result.stderr
        listing = quip("--keystore", str(tmp_path / "gone.json"), "account", "list").stdout
        assert "gone.json  (override)" in listing and "! does not exist" in listing
        assert not (tmp_path / "gone.json").exists()

    def test_missing_quip_keystore_names_its_way_out(self, chain, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("QUIP_KEYSTORE", str(tmp_path / "gone.json"))
        result = quip("account", "show")
        assert result.exit_code == 1
        assert "unset QUIP_KEYSTORE, or point it at an existing keystore" in result.stderr

    def test_prompt_creates_then_shows(self, chain, monkeypatch, quip_home) -> None:
        home, _ = quip_home
        monkeypatch.setattr(quip_cli, "_interactive", lambda opts: True)
        result = quip("account", "show", stdin="new\nuser\n\n")
        assert result.exit_code == 0, result.output
        assert (home / ".quip" / "keystore.json").exists()
        assert "Source    default keystore ~/.quip/keystore.json" in result.stdout

    def test_prompt_import_reads_the_seed_hidden(self, chain, monkeypatch, quip_home, seed_signer) -> None:
        monkeypatch.setattr(quip_cli, "_interactive", lambda opts: True)
        result = quip("account", "show", stdin=f"import\nproject\nprobe\n{SEED_B}\n")
        assert result.exit_code == 0, result.output
        assert SEED_B not in result.output and SEED_B[2:] not in result.output
        assert _ss58(seed_signer.HybridSigner.from_seed(SEED_B).account_id) in result.stdout
        assert "Source    project preference probe" in result.stdout

    def test_prompt_skip_writes_nothing(self, chain, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(quip_cli, "_interactive", lambda opts: True)
        before = _files(tmp_path)
        result = quip("account", "show", stdin="\n")
        assert result.exit_code == 1
        assert _files(tmp_path) == before

    def test_never_prompts_off_a_terminal(self, chain) -> None:
        assert "Create one now?" not in quip("account", "show", stdin="new\n").output


class TestAccountNewImportUse:
    def test_level_follows_the_project(self, chain, quip_home) -> None:
        _, project = quip_home
        assert quip("account", "new", "--name", "a").exit_code == 0
        assert (Path.home() / ".quip" / "keystores" / "a.json").exists()  # no project yet: user level.
        assert (Path.home() / ".quip" / "config.toml").read_text() == 'account = "a"\n'
        assert quip("account", "new", "--name", "b", "--project").exit_code == 0
        result = quip("account", "new", "--name", "c")  # now inside a project.
        assert result.exit_code == 0, result.output
        assert (project / ".quip" / "keystores" / "c.json").exists()
        assert (project / ".quip" / "config.toml").read_text() == 'account = "c"\n'
        assert "now in use at the project level" in result.stdout

    def test_refuses_to_overwrite_without_force(self, chain) -> None:
        old = create_account("a", level="user", seed=SEED_A)
        result = quip("account", "new", "--name", "a", "--global")
        assert result.exit_code == 1
        assert "already exists" in result.stderr and "--force replaces it" in result.stderr
        result = quip("account", "new", "--name", "a", "--global", "--force")
        assert result.exit_code == 0, result.output
        assert f"replaced {_ss58(old.account_id)}" in result.stdout

    def test_user_default_and_no_use_set_no_preference(self, chain) -> None:
        result = quip("account", "new", "--global")
        assert "it is the user default" in result.stdout
        assert quip("account", "new", "--name", "x", "--global", "--no-use").exit_code == 0
        assert not (Path.home() / ".quip" / "config.toml").exists()

    def test_preference_failure_keeps_the_keystore(self, chain, monkeypatch) -> None:
        def refuse(name, *, level):
            raise ValueError("config.toml holds other keys")

        monkeypatch.setattr("xqsa.quip.use_account", refuse)
        result = quip("account", "new", "--name", "a", "--global")
        assert result.exit_code == 1
        assert "could not set the user preference: config.toml holds other keys" in result.stderr
        assert "The keystore stays" in result.stderr
        assert (Path.home() / ".quip" / "keystores" / "a.json").exists()

    def test_import_refuses_a_taken_name_before_reading_the_seed(self, chain) -> None:
        create_account("w", level="user", seed=SEED_A)
        result = quip("account", "import", "--name", "w", "--global", stdin="")
        assert result.exit_code == 1
        assert "already exists" in result.stderr and "choose another --name" in result.stderr
        assert "no seed on stdin" not in result.stderr

    def test_import_from_stdin_never_echoes(self, chain, seed_signer) -> None:
        result = quip("--json", "account", "import", "--name", "w", "--global", stdin=SEED_B + "\n")
        assert result.exit_code == 0, result.output
        assert SEED_B[2:] not in result.output
        data = json.loads(result.stdout)
        assert data == {
            "account": _ss58(seed_signer.HybridSigner.from_seed(SEED_B).account_id),
            "name": "w",
            "level": "user",
            "path": str(Path.home() / ".quip" / "keystores" / "w.json"),
        }

    def test_import_prompts_for_the_seed_on_stderr(self, chain, monkeypatch) -> None:
        tty = types.SimpleNamespace(isatty=lambda: True)
        monkeypatch.setattr(quip_cli, "sys", types.SimpleNamespace(stdin=tty, exit=sys.exit))
        result = quip("--json", "account", "import", "--name", "w", "--global", stdin=SEED_B + "\n")
        assert result.exit_code == 0, result.output
        assert "Seed (hex)" in result.stderr
        assert json.loads(result.stdout)["name"] == "w"

    def test_import_bad_seed(self, chain) -> None:
        result = quip("account", "import", "--global", stdin="0xnothex\n")
        assert result.exit_code == 1
        assert "seed is not valid hex" in result.stderr
        assert "nothex" not in result.stderr

    def test_use_and_unset(self, chain, quip_home) -> None:
        _, project = quip_home
        create_account("a", level="user", seed=SEED_A)
        result = quip("account", "use", "a", "--project")
        assert result.exit_code == 0, result.output
        assert (project / ".quip" / "config.toml").read_text() == 'account = "a"\n'
        assert quip("account", "use", "--unset").exit_code == 0
        assert not (project / ".quip" / "config.toml").exists()

    def test_use_unknown_name(self, chain) -> None:
        result = quip("account", "use", "nope", "--global")
        assert result.exit_code == 1
        assert "no user keystore named 'nope'" in result.stderr

    @pytest.mark.parametrize("args", [[], ["a", "--unset"], ["a", "--global", "--project"]])
    def test_use_usage(self, chain, args) -> None:
        assert quip("account", "use", *args).exit_code == 2


class TestOrdersAndStatus:
    @pytest.fixture(autouse=True)
    def _account(self, chain) -> None:
        create_account(level="user", seed=SEED_A)

    def test_orders(self, chain) -> None:
        result = quip("orders")
        assert result.exit_code == 0, result.output
        assert result.stdout.splitlines() == ["7\topen until block 100\tnone", "3\topen until block 100\tnone"]
        data = json.loads(quip("--json", "orders", "--state", "submitted").stdout)
        assert [o["order_id"] for o in data["orders"]] == [7, 3]
        assert quip("orders", "--state", "unanswered").stdout == "no orders in state unanswered\n"

    def test_orders_of_another_account(self, chain, monkeypatch) -> None:
        seen = []
        real = quip_cli._connect
        monkeypatch.setattr(
            quip_cli, "_connect", lambda opts, signing: seen.append(signing) or real(opts, signing=signing)
        )
        assert quip("orders", "--account", "0x" + "11" * 32).exit_code == 0
        assert seen == [False]

    @pytest.mark.parametrize(
        ("order", "head", "front", "line"),
        [
            pytest.param(_receipt_order(), 50, None, "1\topen until block 100\tnone", id="open"),
            pytest.param(_receipt_order(), 200, LEADING, "1\tfinal\t{leader}", id="final"),
            pytest.param(_receipt_order(solution_count=0), 200, None, "1\treclaimable\tnone", id="reclaimable"),
            pytest.param(_receipt_order(status="Closed"), 200, None, "1\tclosed\tnone", id="closed"),
        ],
    )
    def test_status_short(self, chain, order, head, front, line) -> None:
        chain.storage[(MEMPOOL, "JobOrders")] = order
        if front is not None:
            chain.storage[(MEMPOOL, "OrderFrontRunner")] = front
        chain.head = head
        leader = _ss58(bytes.fromhex(SOLVER[2:]))
        line = line.format(leader=leader)
        assert quip("status", "1", "--short").stdout == line + "\n"
        data = json.loads(quip("--json", "status", "1", "--short").stdout)
        assert data["state"] == line.split("\t")[1].split()[0]
        assert data["leader"] == (leader if front is not None else None)

    def test_status_prints_the_receipt(self, chain) -> None:
        result = quip("status", "1")
        assert result.exit_code == 0, result.output
        assert result.stdout.startswith("╭─ Job order 1 receipt")
        data = json.loads(quip("--json", "status", "1").stdout)
        assert data.keys() == {"summary", "status", "solvers", "settlement"}
        assert data["summary"]["state"] == "open"

    def test_status_needs_no_account(self, chain, quip_home) -> None:
        home, _ = quip_home
        (home / ".quip" / "keystore.json").unlink()
        assert quip("status", "1", "--short").exit_code == 0
        assert not (home / ".quip" / "keystore.json").exists()


class TestReclaim:
    @pytest.fixture(autouse=True)
    def _account(self, chain) -> None:
        create_account(level="user", seed=SEED_A)

    def _reclaims(self, monkeypatch, refuse=frozenset(), error=QuipReclaimRefusedError) -> list[int]:
        done: list[int] = []

        def fake(receipt):
            if receipt.order_id in refuse:
                raise error(f"order {receipt.order_id} was answered")
            done.append(receipt.order_id)
            return UNIT

        monkeypatch.setattr(JobOrderReceipt, "reclaim", fake)
        return done

    def _unanswered(self, monkeypatch, ids) -> None:
        monkeypatch.setattr("xqsa.quip.SolverQuip.list_orders", lambda self, state=None, account=None: list(ids))

    def test_one(self, chain, monkeypatch) -> None:
        self._reclaims(monkeypatch)
        result = quip("reclaim", "3")
        assert result.exit_code == 0, result.output
        assert result.stdout == "reclaimed order 3: 1.000000000000 AGLS refunded\n"
        assert json.loads(quip("--json", "reclaim", "3").stdout) == {"order_id": 3, "refunded": UNIT}

    def test_refused_one_gives_the_reason(self, chain, monkeypatch) -> None:
        self._reclaims(monkeypatch, refuse={3})
        result = quip("reclaim", "3")
        assert result.exit_code == 1
        assert "order 3 was answered" in result.stderr

    def test_no_account_refused_before_building_a_signer(self, chain, quip_home) -> None:
        home, _ = quip_home
        (home / ".quip" / "keystore.json").unlink()
        result = quip("reclaim", "3")
        assert result.exit_code == 1
        assert "No Quip account is set up." in result.stderr
        assert not (home / ".quip" / "keystore.json").exists()

    def test_all_with_nothing_to_reclaim(self, chain, monkeypatch) -> None:
        self._unanswered(monkeypatch, [])
        result = quip("reclaim", "--all")
        assert result.exit_code == 0
        assert result.stdout.startswith("nothing to reclaim on ")
        assert json.loads(quip("--json", "reclaim", "--all").stdout) == {"reclaimed": [], "refused": [], "total": 0}

    def test_all_needs_yes_off_a_terminal(self, chain, monkeypatch) -> None:
        done = self._reclaims(monkeypatch)
        self._unanswered(monkeypatch, [7, 3])
        result = quip("reclaim", "--all")
        assert result.exit_code == 1
        assert "order 7  5.000000000000 AGLS" in result.stdout  # the reward each order reserved.
        assert "2 orders, 10.000000000000 AGLS to recover" in result.stdout
        assert "pass --yes" in result.stderr
        assert done == []

    @pytest.mark.parametrize(("answer", "expected"), [("y\n", [7, 3]), ("\n", [])])
    def test_all_asks_at_a_terminal(self, chain, monkeypatch, answer, expected) -> None:
        done = self._reclaims(monkeypatch)
        self._unanswered(monkeypatch, [7, 3])
        monkeypatch.setattr(quip_cli, "_interactive", lambda opts: True)
        result = quip("reclaim", "--all", stdin=answer)
        assert done == expected
        assert result.exit_code == (0 if expected else 1)

    def test_all_yes_reports_each(self, chain, monkeypatch) -> None:
        done = self._reclaims(monkeypatch, refuse={3})
        self._unanswered(monkeypatch, [7, 3])
        result = quip("--json", "reclaim", "--all", "--yes")
        assert result.exit_code == 1  # one was refused.
        assert done == [7]
        assert json.loads(result.stdout) == {
            "reclaimed": [{"order_id": 7, "refunded": UNIT}],
            "refused": [{"order_id": 3, "reason": "order 3 was answered"}],
            "total": UNIT,
        }
        assert "order 3 not reclaimed: order 3 was answered" in result.stderr

    # A raw transport error stands for the chain reads that do not wrap theirs in QuipConnectionError.
    @pytest.mark.parametrize("error", [QuipConnectionError, ConnectionResetError])
    def test_all_reports_what_landed_when_one_fails(self, chain, monkeypatch, error) -> None:
        done = self._reclaims(monkeypatch, refuse={3}, error=error)
        self._unanswered(monkeypatch, [7, 3])
        result = quip("--json", "reclaim", "--all", "--yes")
        assert result.exit_code == 1
        assert done == [7]
        assert json.loads(result.stdout)["reclaimed"] == [{"order_id": 7, "refunded": UNIT}]

    @pytest.mark.parametrize("args", [[], ["3", "--all"]])
    def test_usage(self, chain, args) -> None:
        assert quip("reclaim", *args).exit_code == 2
