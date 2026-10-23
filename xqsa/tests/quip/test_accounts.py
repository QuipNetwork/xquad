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

"""Named Quip accounts and preferences, over temporary home and project directories."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from xqsa.quip import (
    QuipSigningError,
    clear_account,
    create_account,
    resolve_accounts,
    use_account,
)

pytestmark = pytest.mark.usefixtures("seed_signer")

SEED_A = "0x" + "0a" * 32
SEED_B = "0x" + "0b" * 32


def _files(root: Path) -> set[Path]:
    return {path for path in root.rglob("*")}


def _keystore(path: Path, **fields) -> None:
    raw = {
        "version": 1,
        "scheme": "hybrid-falcon512",
        "encrypted": False,
        "master_seed_hex": SEED_A,
        "account_id_hex": "0x" + "cd" * 32,
        "public_key_hex": "0x" + "ef" * 32,
    }
    raw.update(fields)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({k: v for k, v in raw.items() if v is not None}))


class TestProjectDiscovery:
    def test_nearest_quip_dir_walking_up(self, quip_home) -> None:
        home, project = quip_home
        (home / "work" / ".quip").mkdir()
        nested = project / "src"
        nested.mkdir()
        account = create_account("dev", level="project")
        assert account.path == home / "work" / ".quip" / "keystores" / "dev.json"

    def test_walk_stops_below_home(self, quip_home) -> None:
        home, _ = quip_home
        create_account("work", level="user")
        resolution = resolve_accounts()
        assert [(a.name, a.level) for a in resolution.accounts] == [("work", "user")]

    def test_created_in_working_directory_when_none_found(self, quip_home) -> None:
        _, project = quip_home
        account = create_account("dev", level="project")
        assert account.path == project.resolve() / ".quip" / "keystores" / "dev.json"
        assert account.level == "project"

    def test_project_level_in_home_raises(self, quip_home, monkeypatch) -> None:
        home, _ = quip_home
        monkeypatch.chdir(home)
        with pytest.raises(ValueError, match="home directory is the user level"):
            create_account("dev", level="project")
        assert not (home / ".quip").exists()

    def test_outside_home_walks_to_root(self, quip_home, monkeypatch, tmp_path) -> None:
        outside = tmp_path / "elsewhere" / "deep"
        outside.mkdir(parents=True)
        (tmp_path / "elsewhere" / ".quip").mkdir()
        monkeypatch.chdir(outside)
        account = create_account("dev", level="project")
        assert account.path.parent.parent == (tmp_path / "elsewhere" / ".quip").resolve()


class TestNames:
    @pytest.mark.parametrize("name", ["../x", "Work", "a" * 65, "", "-x", "_x", "a.b", "a/b"])
    def test_invalid_names_refused(self, quip_home, name) -> None:
        with pytest.raises(ValueError, match="must match"):
            create_account(name, level="user")
        with pytest.raises(ValueError, match="must match"):
            use_account(name, level="user")
        assert not (quip_home[0] / ".quip").exists()

    @pytest.mark.parametrize("name", ["a", "0", "work", "a_b-c", "a" * 64])
    def test_valid_names(self, quip_home, name) -> None:
        assert create_account(name, level="user").name == name

    def test_invalid_level(self, quip_home) -> None:
        with pytest.raises(ValueError, match="expected 'project' or 'user'"):
            create_account("x", level="global")  # type: ignore[arg-type]


class TestCreate:
    def test_default_maps_to_user_keystore(self, quip_home) -> None:
        home, _ = quip_home
        account = create_account(level="user")
        assert account.name == "default"
        assert account.path == home / ".quip" / "keystore.json"

    def test_default_at_project_level(self, quip_home) -> None:
        _, project = quip_home
        assert create_account(level="project").path == project.resolve() / ".quip" / "keystores" / "default.json"

    def test_given_seed_is_stored(self, quip_home) -> None:
        account = create_account("a", level="user", seed=SEED_A)
        raw = json.loads(account.path.read_text())
        assert raw["master_seed_hex"] == SEED_A
        assert raw["account_id_hex"] == "0x" + account.account_id.hex()
        assert account.path.stat().st_mode & 0o777 == 0o600

    def test_fresh_seeds_differ(self, quip_home) -> None:
        assert create_account("a", level="user").account_id != create_account("b", level="user").account_id

    def test_existing_refused_without_overwrite(self, quip_home) -> None:
        first = create_account("a", level="user", seed=SEED_A)
        with pytest.raises(QuipSigningError, match="already exists"):
            create_account("a", level="user", seed=SEED_B)
        assert json.loads(first.path.read_text())["master_seed_hex"] == SEED_A

    def test_overwrite_replaces(self, quip_home) -> None:
        create_account("a", level="user", seed=SEED_A)
        account = create_account("a", level="user", seed=SEED_B, overwrite=True)
        assert json.loads(account.path.read_text())["master_seed_hex"] == SEED_B

    def test_project_gitignore_written_once(self, quip_home) -> None:
        _, project = quip_home
        create_account("a", level="project")
        gitignore = project / ".quip" / ".gitignore"
        assert gitignore.read_text() == "*\n"
        gitignore.write_text("*\n!config.toml\n")
        create_account("b", level="project")
        use_account("b", level="project")
        assert gitignore.read_text() == "*\n!config.toml\n"

    def test_user_level_writes_no_gitignore(self, quip_home) -> None:
        home, _ = quip_home
        create_account("a", level="user")
        assert not (home / ".quip" / ".gitignore").exists()


class TestPreferences:
    def test_use_writes_single_line(self, quip_home) -> None:
        home, project = quip_home
        create_account("a", level="user")
        use_account("a", level="user")
        use_account("a", level="project")
        assert (home / ".quip" / "config.toml").read_text() == 'account = "a"\n'
        assert (project / ".quip" / "config.toml").read_text() == 'account = "a"\n'
        assert (project / ".quip" / ".gitignore").read_text() == "*\n"

    def test_use_refuses_unknown_name(self, quip_home) -> None:
        with pytest.raises(ValueError, match="no project or user keystore named 'a'"):
            use_account("a", level="project")
        with pytest.raises(ValueError, match="no user keystore named 'a'"):
            use_account("a", level="user")

    def test_user_preference_refuses_project_keystore(self, quip_home) -> None:
        create_account("a", level="project")
        use_account("a", level="project")
        with pytest.raises(ValueError, match="no user keystore named 'a'"):
            use_account("a", level="user")

    def test_user_default_needs_the_default_file(self, quip_home) -> None:
        with pytest.raises(ValueError, match="no user keystore named 'default'"):
            use_account("default", level="user")
        create_account(level="user")
        use_account("default", level="user")

    def test_use_and_clear_refuse_other_keys(self, quip_home) -> None:
        home, _ = quip_home
        create_account("a", level="user")
        config = home / ".quip" / "config.toml"
        config.write_text('account = "a"\ntheme = "dark"\n')
        with pytest.raises(ValueError, match="keys besides `account`"):
            use_account("a", level="user")
        with pytest.raises(ValueError, match="keys besides `account`"):
            clear_account(level="user")
        assert config.read_text() == 'account = "a"\ntheme = "dark"\n'

    def test_use_refuses_bad_toml(self, quip_home) -> None:
        home, _ = quip_home
        create_account("a", level="user")
        (home / ".quip" / "config.toml").write_text("account = \n")
        with pytest.raises(ValueError, match="not valid TOML"):
            use_account("a", level="user")

    def test_clear_removes_preference(self, quip_home) -> None:
        home, _ = quip_home
        create_account("a", level="user")
        use_account("a", level="user")
        clear_account(level="user")
        assert not (home / ".quip" / "config.toml").exists()
        clear_account(level="user")  # nothing left to clear
        clear_account(level="project")  # no project at all


class TestResolve:
    def test_creates_no_file(self, quip_home, tmp_path) -> None:
        before = _files(tmp_path)
        resolution = resolve_accounts(keystore=str(tmp_path / "missing.json"))
        assert _files(tmp_path) == before
        assert resolution.in_effect.step == "keystore="
        assert resolution.in_effect.path == tmp_path / "missing.json"

    def test_creates_no_file_with_preferences(self, quip_home, tmp_path, monkeypatch) -> None:
        create_account("a", level="user")
        use_account("a", level="project")
        monkeypatch.setenv("QUIP_KEYSTORE", str(tmp_path / "env.json"))
        before = _files(tmp_path)
        resolve_accounts()
        assert _files(tmp_path) == before

    def test_nothing_configured_is_default(self, quip_home) -> None:
        home, _ = quip_home
        source = resolve_accounts().in_effect
        assert (source.step, source.value, source.path, source.problem) == (
            "default",
            "default",
            home / ".quip" / "keystore.json",
            None,
        )

    def test_seed_never_echoed(self, quip_home, monkeypatch) -> None:
        monkeypatch.setenv("QUIP_SIGNER_SEED", SEED_B)
        resolution = resolve_accounts(seed=SEED_A)
        values = {source.step: source.value for source in resolution.sources}
        assert values["seed="] == values["QUIP_SIGNER_SEED"] == "set"
        assert SEED_A not in repr(resolution) and SEED_B not in repr(resolution)

    def test_lists_project_then_user(self, quip_home) -> None:
        create_account("a", level="user", seed=SEED_A)
        create_account("a", level="project", seed=SEED_B)
        create_account(level="user")
        rows = [(a.name, a.level) for a in resolve_accounts().accounts]
        assert rows == [("a", "project"), ("default", "user"), ("a", "user")]

    def test_project_name_shadows_user(self, quip_home) -> None:
        _, project = quip_home
        create_account("a", level="user", seed=SEED_A)
        project_account = create_account("a", level="project", seed=SEED_B)
        use_account("a", level="project")
        source = resolve_accounts().in_effect
        assert (source.step, source.value, source.path) == ("project", "a", project_account.path)

    def test_project_preference_reaches_user_keystore(self, quip_home) -> None:
        user_account = create_account("a", level="user")
        use_account("a", level="project")
        assert resolve_accounts().in_effect.path == user_account.path

    def test_project_default_does_not_shadow_default_step(self, quip_home) -> None:
        home, _ = quip_home
        create_account(level="project")
        source = resolve_accounts().in_effect
        assert (source.step, source.path) == ("default", home / ".quip" / "keystore.json")

    def test_missing_preferred_keystore_is_a_problem(self, quip_home) -> None:
        home, _ = quip_home
        create_account("a", level="user")
        use_account("a", level="user")
        (home / ".quip" / "keystores" / "a.json").unlink()
        source = resolve_accounts().in_effect
        assert source.step == "user"
        assert "no keystore named 'a'" in source.problem

    @pytest.mark.parametrize(
        ("text", "problem"),
        [
            ("account = \n", "not readable TOML"),
            ("account = 3\n", "must be a string"),
            ('account = "../x"\n', "not a valid account name"),
        ],
    )
    def test_broken_preference_is_a_problem(self, quip_home, text, problem) -> None:
        home, _ = quip_home
        (home / ".quip").mkdir()
        (home / ".quip" / "config.toml").write_text(text)
        source = resolve_accounts().in_effect
        assert source.step == "user"
        assert problem in source.problem

    def test_config_without_account_key_is_no_preference(self, quip_home) -> None:
        home, _ = quip_home
        (home / ".quip").mkdir()
        (home / ".quip" / "config.toml").write_text('theme = "dark"\n')
        assert resolve_accounts().in_effect.step == "default"

    @pytest.mark.parametrize(
        ("fields", "raw", "problem"),
        [
            ({}, "{not json", "not readable JSON"),
            ({"version": 2}, None, "version 2 not supported"),
            ({"scheme": "hybrid"}, None, "H3 account"),
            ({"scheme": "other"}, None, "scheme 'other' not supported"),
            ({"account_id_hex": None}, None, "no readable 'account_id_hex'"),
            ({"account_id_hex": "0xabcd"}, None, "2 bytes, not 32"),
            ({"encrypted": True}, None, "passphrase-encrypted"),
        ],
    )
    def test_unusable_keystores_listed(self, quip_home, fields, raw, problem) -> None:
        home, _ = quip_home
        path = home / ".quip" / "keystores" / "bad.json"
        _keystore(path, **fields)
        if raw is not None:
            path.write_text(raw)
        (account,) = resolve_accounts().accounts
        assert account.name == "bad" and account.account_id is None
        assert problem in account.problem

    def test_unusable_preferred_keystore_is_a_problem(self, quip_home) -> None:
        home, _ = quip_home
        _keystore(home / ".quip" / "keystores" / "bad.json", version=2)
        (home / ".quip" / "config.toml").write_text('account = "bad"\n')
        assert "not supported" in resolve_accounts().in_effect.problem

    def test_account_read_from_cache_in_any_case(self, quip_home) -> None:
        home, _ = quip_home
        _keystore(home / ".quip" / "keystores" / "a.json", account_id_hex="0x" + "AB" * 32, master_seed_hex=None)
        (account,) = resolve_accounts().accounts
        assert account.account_id == bytes.fromhex("ab" * 32)
        assert account.problem is None

    def test_invalid_file_name_listed(self, quip_home) -> None:
        home, _ = quip_home
        _keystore(home / ".quip" / "keystores" / "Work.json")
        (account,) = resolve_accounts().accounts
        assert "not a valid account name" in account.problem

    def test_user_keystores_default_json_never_used(self, quip_home) -> None:
        home, _ = quip_home
        _keystore(home / ".quip" / "keystores" / "default.json")
        (account,) = resolve_accounts().accounts
        assert "never used" in account.problem


class TestEdgeCases:
    def test_stray_user_default_json_never_shadows_keystore_json(self, quip_home) -> None:
        home, _ = quip_home
        real = create_account(level="user", seed=SEED_A)
        _keystore(home / ".quip" / "keystores" / "default.json")
        use_account("default", level="user")
        resolution = resolve_accounts()
        assert (resolution.in_effect.step, resolution.in_effect.path) == ("user", real.path)
        assert resolution.in_effect.problem is None
        default = next(source for source in resolution.sources if source.step == "default")
        assert default.problem is None

    def test_unusable_default_reported_on_the_default_step(self, quip_home) -> None:
        home, _ = quip_home
        _keystore(home / ".quip" / "keystore.json", version=2)
        source = resolve_accounts().in_effect
        assert source.step == "default"
        assert "version 2 not supported" in source.problem

    def test_deleted_working_directory_has_no_project(self, quip_home, monkeypatch, tmp_path) -> None:
        gone = tmp_path / "gone"
        gone.mkdir()
        monkeypatch.chdir(gone)
        gone.rmdir()
        resolution = resolve_accounts(seed=SEED_A)
        assert resolution.in_effect.step == "seed="
        assert next(s for s in resolution.sources if s.step == "project").value is None

    def test_no_home_directory_has_no_user_level(self, quip_home, monkeypatch) -> None:
        def no_home(cls):
            raise RuntimeError("Could not determine home directory.")

        monkeypatch.setattr(Path, "home", classmethod(no_home))
        resolution = resolve_accounts(seed=SEED_A)
        assert resolution.in_effect.step == "seed="
        assert resolution.accounts == ()
        assert next(s for s in resolution.sources if s.step == "default").path is None

    @pytest.mark.parametrize(
        "call", [lambda: create_account("a", level="user"), lambda: use_account("a", level="user")]
    )
    def test_no_home_directory_write_raises_value_error(self, quip_home, monkeypatch, call) -> None:
        def no_home(cls):
            raise RuntimeError("Could not determine home directory.")

        monkeypatch.setattr(Path, "home", classmethod(no_home))
        with pytest.raises(ValueError, match="cannot determine the home directory"):
            call()

    @pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX ownership")
    @pytest.mark.parametrize("untrusted", ["world-writable", "another user's"])
    def test_untrusted_project_dir_is_skipped(self, quip_home, monkeypatch, untrusted) -> None:
        _, project = quip_home
        create_account("p", level="project", seed=SEED_A)
        use_account("p", level="project")
        if untrusted == "world-writable":
            (project / ".quip").chmod(0o777)
        else:
            uid = os.getuid()
            monkeypatch.setattr(os, "getuid", lambda: uid + 1)
        assert resolve_accounts().in_effect.step == "default"
        assert all(account.level == "user" for account in resolve_accounts().accounts)

    def test_quip_file_in_working_directory_refused(self, quip_home) -> None:
        _, project = quip_home
        (project / ".quip").write_text("")
        with pytest.raises(ValueError, match="not a project .quip/ directory"):
            create_account("a", level="project")
        create_account("a", level="user")
        with pytest.raises(ValueError, match="not a project .quip/ directory"):
            use_account("a", level="project")

    def test_link_to_user_level_is_never_a_project(self, quip_home) -> None:
        home, project = quip_home
        create_account("a", level="user")
        (home / ".quip" / "config.toml").write_text('account = "a"\n')
        (project / ".quip").symlink_to(home / ".quip")
        resolution = resolve_accounts()
        assert [(a.name, a.level) for a in resolution.accounts] == [("a", "user")]
        assert resolution.in_effect.step == "user"
        with pytest.raises(ValueError, match="not a project .quip/ directory"):
            create_account("b", level="project")

    def test_stray_user_default_json_alone_is_not_the_default(self, quip_home) -> None:
        home, _ = quip_home
        _keystore(home / ".quip" / "keystores" / "default.json")
        resolution = resolve_accounts()
        assert (resolution.in_effect.step, resolution.in_effect.problem) == ("default", None)
        (home / ".quip" / "config.toml").write_text('account = "default"\n')
        assert "no keystore named 'default'" in resolve_accounts().in_effect.problem

    @pytest.mark.parametrize("step", ["keystore=", "QUIP_KEYSTORE"])
    @pytest.mark.parametrize(("kind", "problem"), [("file", "H3 account"), ("directory", "not readable JSON")])
    def test_unusable_keystore_path_reported(self, quip_home, monkeypatch, tmp_path, step, kind, problem) -> None:
        path = tmp_path / "bad.json"
        if kind == "file":
            _keystore(path, scheme="hybrid")
        else:
            path.mkdir()
        kwargs = {"keystore": str(path)} if step == "keystore=" else {}
        if step == "QUIP_KEYSTORE":
            monkeypatch.setenv("QUIP_KEYSTORE", str(path))
        source = resolve_accounts(**kwargs).in_effect
        assert source.step == step
        assert problem in source.problem

    @pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs a non-root POSIX user")
    def test_unenterable_user_dir_lists_nothing(self, quip_home) -> None:
        home, _ = quip_home
        create_account("a", level="user")
        (home / ".quip").chmod(0)
        try:
            resolution = resolve_accounts(seed=SEED_A)
        finally:
            (home / ".quip").chmod(0o700)
        assert resolution.in_effect.step == "seed="
        assert resolution.accounts == ()

    @pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs a non-root POSIX user")
    def test_unreadable_keystore_env_does_not_break_a_seed(self, quip_home, monkeypatch, tmp_path) -> None:
        locked = tmp_path / "locked"
        locked.mkdir()
        monkeypatch.setenv("QUIP_KEYSTORE", str(locked / "keystore.json"))
        locked.chmod(0)
        try:
            resolution = resolve_accounts(seed=SEED_A)
        finally:
            locked.chmod(0o700)
        assert resolution.in_effect.step == "seed="
        env = next(source for source in resolution.sources if source.step == "QUIP_KEYSTORE")
        assert "cannot read" in env.problem

    @pytest.mark.parametrize("level", ["project", "user"])
    def test_non_utf8_config_is_a_problem_not_an_error(self, quip_home, level) -> None:
        home, project = quip_home
        base = (project if level == "project" else home) / ".quip"
        base.mkdir()
        (base / "config.toml").write_bytes(b"\xff\xfea\x00")
        assert resolve_accounts(seed=SEED_A).in_effect.step == "seed="
        source = next(s for s in resolve_accounts().sources if s.step == level)
        assert "not readable TOML" in source.problem
        with pytest.raises(ValueError, match="not valid TOML"):
            clear_account(level=level)

    def test_unknown_user_keystore_path_does_not_break_a_seed(self, quip_home, monkeypatch) -> None:
        monkeypatch.setenv("QUIP_KEYSTORE", "~nosuchuser_qui1610/k.json")
        resolution = resolve_accounts(seed=SEED_A)
        assert resolution.in_effect.step == "seed="
        env = next(source for source in resolution.sources if source.step == "QUIP_KEYSTORE")
        assert env.path is None
        assert "cannot expand" in env.problem

    @pytest.mark.parametrize("level", ["project", "user"])
    def test_config_directory_refused_with_value_error(self, quip_home, level) -> None:
        home, project = quip_home
        create_account("a", level="user")
        (project / ".quip").mkdir()
        base = (project if level == "project" else home) / ".quip"
        (base / "config.toml").mkdir()
        assert resolve_accounts(seed=SEED_A).in_effect.step == "seed="
        with pytest.raises(ValueError, match="cannot be read"):
            use_account("a", level=level)
        with pytest.raises(ValueError, match="cannot be read"):
            clear_account(level=level)
