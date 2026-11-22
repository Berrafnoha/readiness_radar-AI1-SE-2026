import pytest
from fastapi.testclient import TestClient

import api
from radar import auth, db
from radar.auth import SYSTEM
from tests.conftest import database_url_for_tests

H = {"X-Requested-With": "radar"}


@pytest.fixture
def web(conn, monkeypatch):
    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    orig = db.connect
    monkeypatch.setattr(db, "connect", lambda url=None, schema_=None, **k: orig(database_url_for_tests(), schema=schema))
    for name, role in [("karim", "field_engineer"), ("samira", "project_director"), ("root", "admin")]:
        auth.create_user(conn, SYSTEM, name, name.title(), "correct-horse-1", role)
    conn.commit()

    def login(name):
        c = TestClient(api.app)
        r = c.post("/api/auth/login", json={"username": name, "password": "correct-horse-1"}, headers=H)
        assert r.status_code == 200
        return c
    return login, TestClient(api.app)


def test_requires_login_and_csrf_header(web):
    login, anon = web
    assert anon.get("/api/dashboard").status_code == 401
    assert anon.post("/api/auth/login", json={"username": "karim", "password": "correct-horse-1"}).status_code == 403   # no CSRF header
    assert anon.post("/api/auth/login", json={"username": "karim", "password": "bad"}, headers=H).status_code == 401
    assert anon.get("/").status_code == 200


def test_session_cookie_flags_and_me_and_logout(web):
    login, _ = web
    c = TestClient(api.app)
    r = c.post("/api/auth/login", json={"username": "karim", "password": "correct-horse-1"}, headers=H)
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=lax" in r.headers["set-cookie"].lower()
    assert c.get("/api/auth/me").json()["role"] == "field_engineer"
    assert c.post("/api/auth/logout", headers=H).status_code == 200
    assert c.get("/api/auth/me").status_code == 401


def test_workflow_and_permissions_over_http(web):
    login, _ = web
    eng, boss = login("karim"), login("samira")
    assert eng.get("/api/dashboard").json()["risks"] == 4
    fp = next(f["fingerprint"] for f in eng.get("/api/findings").json() if "P-101" in f["fingerprint"] and f["rule_id"] == "R1")
    assert boss.post("/api/review", json={"fingerprint": fp, "decision": "confirmed", "comment": "x"}, headers=H).status_code == 403
    assert eng.post("/api/confirm", json={"item": "Dewatering pump P-101", "date": "2026-10-25", "note": "email"}, headers=H).status_code == 200
    assert eng.get("/api/dashboard").json()["risks"] == 3
    assert eng.post("/api/stages/S1/ready", headers=H).status_code == 200
    assert eng.post("/api/stages/S1/authorize", headers=H).status_code == 403
    assert boss.post("/api/stages/S1/authorize", headers=H).status_code == 409        # open inspection blocks
    assert eng.post("/api/inspections/I2/close", headers=H).status_code == 200
    assert boss.post("/api/stages/S1/authorize", headers=H).status_code == 200
    assert boss.get("/api/audit").json()["ok"] is True


def test_admin_user_management_over_http(web):
    login, _ = web
    eng, admin = login("karim"), login("root")
    body = {"username": "newbie", "full_name": "New Bie", "password": "another-long-pw", "role": "field_engineer"}
    assert eng.post("/api/admin/users", json=body, headers=H).status_code == 403
    assert admin.post("/api/admin/users", json=body, headers=H).status_code == 200
    assert any(u["username"] == "newbie" for u in admin.get("/api/admin/users").json())
    assert admin.post("/api/reset", headers=H).status_code == 200
    assert eng.post("/api/reset", headers=H).status_code == 403
