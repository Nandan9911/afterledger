"""Accounts: signing up and in, each case kept to its own account, the sample cases open to all, and the one
admin who manages the accounts."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from afterledger import accounts
from afterledger.domain.case import CaseFile
from conftest import api_client

ADMIN = {"admin_email": "Admin@Example.org", "admin_password": "admin-pass-1", "admin_name": "Site admin"}


def _client(tmp_path: Path, **more: Any):
    gen = api_client(tmp_path, site="product", auth_secret="test-secret", **more)
    return gen, next(gen)


def _signup(c: Any, name: str, email: str, password: str = "password-1") -> tuple[str, dict]:
    r = c.post("/v1/auth/signup", json={"name": name, "email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"], r.json()["user"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_sign_up_sign_in_and_who_is_signed_in(tmp_path: Path) -> None:
    gen, c = _client(tmp_path)
    try:
        token, user = _signup(c, "Asha Rao", "Asha@Example.org ")
        assert user["email"] == "asha@example.org" and user["role"] == "user" and "password_hash" not in user
        assert c.get("/v1/auth/me", headers=_auth(token)).json()["name"] == "Asha Rao"
        assert c.get("/v1/auth/me").status_code == 401
        good = c.post("/v1/auth/signin", json={"email": "ASHA@example.org", "password": "password-1"})
        assert good.status_code == 200 and good.json()["user"]["id"] == user["id"]
        wrong = c.post("/v1/auth/signin", json={"email": "asha@example.org", "password": "nope-nope"})
        nobody = c.post("/v1/auth/signin", json={"email": "who@example.org", "password": "password-1"})
        assert wrong.status_code == nobody.status_code == 401 and wrong.json() == nobody.json()
        again = c.post("/v1/auth/signup", json={"name": "Other", "email": "asha@example.org", "password": "password-2"})
        assert again.status_code == 409
        assert c.post("/v1/auth/signup", json={"name": "S", "email": "s@example.org", "password": "short"}).status_code == 400
        assert c.post("/v1/auth/signup", json={"name": "S", "email": "not-an-email", "password": "password-1"}).status_code == 400
        assert c.post("/v1/auth/signup", json={"name": " ", "email": "t@example.org", "password": "password-1"}).status_code == 400
    finally:
        gen.close()


def test_tokens_cannot_be_forged_kept_past_expiry_or_used_after_a_password_change(tmp_path: Path) -> None:
    gen, c = _client(tmp_path)
    try:
        token, user = _signup(c, "Ben", "ben@example.org")
        body, sig = token.split(".")
        forged = f"{body}.{'A' * len(sig)}"
        assert c.get("/v1/auth/me", headers=_auth(forged)).status_code == 401
        assert c.get("/v1/auth/me", headers=_auth("garbage")).status_code == 401
        stored = c.portal.call(c.deps.users.get, user["id"])
        old = accounts.make_token(c.deps, stored, now=time.time() - 8 * 86400)
        assert c.get("/v1/auth/me", headers=_auth(old)).status_code == 401
        c.portal.call(lambda: accounts.update_user(c.deps, stored, password="new-password-1"))
        assert c.get("/v1/auth/me", headers=_auth(token)).status_code == 401      # revoked
        assert c.post("/v1/auth/signin", json={"email": "ben@example.org", "password": "new-password-1"}).status_code == 200
    finally:
        gen.close()


def test_each_case_belongs_to_its_account_and_the_samples_are_open_to_all(tmp_path: Path) -> None:
    gen, c = _client(tmp_path)
    try:
        sample = CaseFile(id="demo-verma", title="Rajesh Kumar Verma", demo_key="verma")
        c.portal.call(c.deps.cases.put_case, sample)
        assert c.post("/v1/cases", json={"lang": "en"}).status_code == 401
        a, _ = _signup(c, "Asha", "asha@example.org")
        b, _ = _signup(c, "Ben", "ben@example.org")
        mine = c.post("/v1/cases", json={"lang": "en"}, headers=_auth(a)).json()["case"]["id"]
        assert c.get(f"/v1/cases/{mine}", headers=_auth(a)).status_code == 200
        for headers in (_auth(b), {}):                   # another account, or nobody: it does not exist
            assert c.get(f"/v1/cases/{mine}", headers=headers).status_code == 404
            assert c.get(f"/v1/cases/{mine}/papers", headers=headers).status_code == 404
            assert c.post(f"/v1/cases/{mine}/turns", json={"text": "hello"}, headers=headers).status_code == 404
            assert c.delete(f"/v1/cases/{mine}", headers=headers).status_code == 404
        ids_a = [x["id"] for x in c.get("/v1/cases", headers=_auth(a)).json()]
        ids_b = [x["id"] for x in c.get("/v1/cases", headers=_auth(b)).json()]
        ids_anyone = [x["id"] for x in c.get("/v1/cases").json()]
        assert mine in ids_a and mine not in ids_b and mine not in ids_anyone
        assert "demo-verma" in ids_a and "demo-verma" in ids_b and "demo-verma" in ids_anyone
        assert c.get("/v1/cases/demo-verma").status_code == 200        # the samples need no account
        # a browser cannot set headers on event streams, images and downloads: the sign-in rides in the address
        assert c.get(f"/v1/cases/{mine}/activity", params={"access_token": a}).status_code == 200
        assert c.get(f"/v1/cases/{mine}/activity", params={"access_token": b}).status_code == 404
        assert c.delete(f"/v1/cases/{mine}", headers=_auth(a)).status_code == 200
    finally:
        gen.close()


def test_the_admin_manages_the_accounts(tmp_path: Path) -> None:
    gen, c = _client(tmp_path, **ADMIN)
    try:
        a, user_a = _signup(c, "Asha", "asha@example.org")
        case_a = c.post("/v1/cases", json={"lang": "en"}, headers=_auth(a)).json()["case"]["id"]
        signin = c.post("/v1/auth/signin", json={"email": "admin@example.org", "password": "admin-pass-1"})
        admin = signin.json()["token"]
        assert signin.json()["user"]["role"] == "admin" and signin.json()["user"]["name"] == "Site admin"
        assert c.get("/v1/admin/users").status_code == 401
        assert c.get("/v1/admin/users", headers=_auth(a)).status_code == 403
        rows = c.get("/v1/admin/users", headers=_auth(admin)).json()
        assert [(r["email"], r["role"], r["cases"]) for r in rows] == [("admin@example.org", "admin", 0),
                                                                      ("asha@example.org", "user", 1)]
        assert c.get(f"/v1/cases/{case_a}", headers=_auth(admin)).status_code == 200
        made = c.post("/v1/admin/users", json={"name": "Chen", "email": "chen@example.org", "password": "chen-pass-1"},
                      headers=_auth(admin))
        assert made.status_code == 200 and made.json()["role"] == "user"
        assert c.post("/v1/admin/users", json={"name": "Dup", "email": "chen@example.org", "password": "chen-pass-1"},
                      headers=_auth(admin)).status_code == 409
        edited = c.patch(f"/v1/admin/users/{user_a['id']}", json={"name": "Asha R", "password": "asha-new-pass"},
                         headers=_auth(admin))
        assert edited.status_code == 200 and edited.json()["name"] == "Asha R"
        assert c.get("/v1/auth/me", headers=_auth(a)).status_code == 401              # her old sign-in ended
        assert c.post("/v1/auth/signin", json={"email": "asha@example.org", "password": "asha-new-pass"}).status_code == 200
        admin_id = rows[0]["id"]
        assert c.patch(f"/v1/admin/users/{admin_id}", json={"name": "x"}, headers=_auth(admin)).status_code == 400
        assert c.delete(f"/v1/admin/users/{admin_id}", headers=_auth(admin)).status_code == 400
        gone = c.delete(f"/v1/admin/users/{user_a['id']}", headers=_auth(admin))
        assert gone.status_code == 200 and gone.json()["cases_removed"] == 1
        assert c.get(f"/v1/cases/{case_a}", headers=_auth(admin)).status_code == 404
        assert [r["email"] for r in c.get("/v1/admin/users", headers=_auth(admin)).json()] == [
            "admin@example.org", "chen@example.org"]
        assert c.post("/v1/auth/signin", json={"email": "asha@example.org", "password": "asha-new-pass"}).status_code == 401
    finally:
        gen.close()


def test_the_admin_comes_from_the_deployment_and_follows_its_password(tmp_path: Path) -> None:
    gen, c = _client(tmp_path, **ADMIN)
    try:
        token = c.post("/v1/auth/signin", json={"email": "admin@example.org", "password": "admin-pass-1"}).json()["token"]
    finally:
        gen.close()
    gen, c = _client(tmp_path, **{**ADMIN, "admin_password": "admin-pass-2"})       # redeployed, new password
    try:
        users = c.portal.call(c.deps.users.list)
        assert [u.role for u in users] == ["admin"]                                 # still one admin
        assert c.get("/v1/auth/me", headers=_auth(token)).status_code == 401
        assert c.post("/v1/auth/signin", json={"email": "admin@example.org", "password": "admin-pass-2"}).status_code == 200
        assert c.post("/v1/auth/signup", json={"name": "Me", "email": "admin@example.org",
                                               "password": "password-1"}).status_code == 409
    finally:
        gen.close()


def test_without_accounts_everything_works_as_before(tmp_path: Path) -> None:
    gen = api_client(tmp_path)                      # a working copy: AL_AUTH auto, not the public site
    c = next(gen)
    try:
        one = c.post("/v1/cases", json={"lang": "en"}).json()["case"]["id"]
        assert one in [x["id"] for x in c.get("/v1/cases").json()]
        assert c.get(f"/v1/cases/{one}").status_code == 200
        assert c.post("/v1/auth/signup", json={"name": "Dev", "email": "dev@example.org",
                                               "password": "password-1"}).status_code == 200
    finally:
        gen.close()


def test_passwords_are_salted_scrypt_hashes() -> None:
    one, two = accounts.hash_password("same-password"), accounts.hash_password("same-password")
    assert one != two and one.startswith("scrypt$16384$8$1$")
    assert accounts.check_password("same-password", one) and not accounts.check_password("other-password", one)
    assert not accounts.check_password("same-password", "not-a-hash")


def test_the_public_site_lists_only_the_sample_families_it_shows(tmp_path: Path) -> None:
    gen, c = _client(tmp_path, demo_families="verma,sato")
    try:
        for key, title in (("verma", "Rajesh Kumar Verma"), ("clarke", "Margaret Anne Clarke")):
            c.portal.call(c.deps.cases.put_case, CaseFile(id=f"demo-{key}", title=title, demo_key=key))
        listed = [x["id"] for x in c.get("/v1/cases").json()]
        assert "demo-verma" in listed and "demo-clarke" not in listed
        assert c.get("/v1/cases/demo-clarke").status_code == 200        # its address still opens it
    finally:
        gen.close()


def test_odd_tokens_are_no_sign_in_and_never_an_error(tmp_path: Path) -> None:
    gen, c = _client(tmp_path)
    try:
        for odd in ("é.é", "a" * 3000 + ".b", "....", "abc.déf"):
            r = c.get("/v1/auth/me", params={"access_token": odd})
            assert r.status_code == 401, (odd[:20], r.status_code)
    finally:
        gen.close()


def test_the_public_site_hides_model_use_and_lets_only_the_admin_reset_a_sample(tmp_path: Path, monkeypatch: Any) -> None:
    async def fake_reset(deps: Any, key: str) -> dict:
        return {"state": "ready"}

    monkeypatch.setattr("afterledger.app.demo.reset", fake_reset)
    gen, c = _client(tmp_path, **ADMIN)
    try:
        assert c.get("/v1/eval/usage").status_code == 404           # it names every case
        assert c.get("/v1/eval").status_code == 404
        token, _ = _signup(c, "Asha", "asha@example.org")
        assert c.post("/v1/demo/verma/reset").status_code == 403
        assert c.post("/v1/demo/verma/reset", headers=_auth(token)).status_code == 403
        admin = c.post("/v1/auth/signin", json={"email": "admin@example.org", "password": "admin-pass-1"}).json()["token"]
        assert c.post("/v1/demo/verma/reset", headers=_auth(admin)).status_code == 200
    finally:
        gen.close()
