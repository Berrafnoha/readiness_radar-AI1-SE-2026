import threading

import pytest

from radar import audit, auth, db, services
from radar.auth import SYSTEM, User
from tests.conftest import database_url_for_tests


def mk(conn, name, role, pw="correct-horse-1"):
    auth.create_user(conn, SYSTEM, name, name.title(), pw, role)


def test_only_admin_can_create_users_and_passwords_are_hashed(conn):
    with pytest.raises(services.PermissionDenied):
        auth.create_user(conn, User("karim", "field_engineer"), "bob", "Bob", "long-enough-pw", "field_engineer")
    mk(conn, "karim", "field_engineer")
    stored = conn.execute("SELECT password_hash FROM users WHERE username='karim'").fetchone()[0]
    assert stored.startswith("scrypt$") and "correct-horse-1" not in stored


def test_login_session_and_logout(conn):
    mk(conn, "karim", "field_engineer")
    token, user = auth.login(conn, "Karim", "correct-horse-1")          # username is case-insensitive
    assert user.role == "field_engineer"
    assert auth.user_from_token(conn, token).role == "field_engineer"
    auth.logout(conn, token, user)
    assert auth.user_from_token(conn, token) is None
    assert any(r["action"] == "login" for r in audit.rows(conn))


def test_wrong_password_and_unknown_user_give_same_error(conn):
    mk(conn, "karim", "field_engineer")
    msgs = set()
    for u, p in [("karim", "wrong-password-1"), ("nobody", "whatever-password")]:
        with pytest.raises(auth.AuthError) as e:
            auth.login(conn, u, p)
        msgs.add(str(e.value))
    assert len(msgs) == 1
    assert any(r["action"] == "login_failed" for r in audit.rows(conn))


def test_login_throttle(conn):
    mk(conn, "karim", "field_engineer")
    for _ in range(auth.MAX_FAILS):
        with pytest.raises(auth.AuthError):
            auth.login(conn, "karim", "bad-password-123")
    with pytest.raises(auth.Throttled):
        auth.login(conn, "karim", "correct-horse-1")


def test_password_rules_and_duplicates(conn):
    with pytest.raises(services.RadarError):
        auth.create_user(conn, SYSTEM, "karim", "K", "short", "field_engineer")
    mk(conn, "karim", "field_engineer")
    with pytest.raises(services.RadarError):
        mk(conn, "karim", "field_engineer")
    with pytest.raises(services.RadarError):
        auth.create_user(conn, SYSTEM, "x y", "K", "long-enough-pw", "field_engineer")


def test_deactivation_revokes_sessions_and_blocks_login(conn):
    mk(conn, "karim", "field_engineer")
    mk(conn, "root", "admin")
    token, user = auth.login(conn, "karim", "correct-horse-1")
    _, admin = auth.login(conn, "root", "correct-horse-1")
    auth.set_active(conn, admin, user.user_id, False)
    assert auth.user_from_token(conn, token) is None
    with pytest.raises(auth.AuthError):
        auth.login(conn, "karim", "correct-horse-1")
    with pytest.raises(services.RadarError):                       # cannot lock yourself out
        auth.set_active(conn, admin, admin.user_id, False)


def test_change_password_revokes_sessions(conn):
    mk(conn, "karim", "field_engineer")
    token, user = auth.login(conn, "karim", "correct-horse-1")
    with pytest.raises(auth.AuthError):
        auth.change_password(conn, user, "wrong", "new-password-123")
    auth.change_password(conn, user, "correct-horse-1", "new-password-123")
    assert auth.user_from_token(conn, token) is None
    auth.login(conn, "karim", "new-password-123")


def test_services_reject_plain_strings_and_admin_cannot_do_operations(conn):
    with pytest.raises(services.PermissionDenied):
        services.close_inspection(conn, "karim", "I2", "forged plain string")
    with pytest.raises(services.PermissionDenied):
        services.close_inspection(conn, User("root", "admin"), "I2", "admin is not an engineer")
    with pytest.raises(services.PermissionDenied):
        services.authorize_stage(conn, User("root", "admin"), "S1")


def test_concurrent_audit_appends_keep_chain_intact(conn):
    conn.commit()
    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    errors = []

    def worker(n):
        try:
            c = db.connect(database_url_for_tests(), schema=schema)
            for i in range(15):
                audit.append(c, f"w{n}", "system", "concurrent_test", {"i": i})
                c.commit()
            c.close()
        except Exception as e:                                     # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors
    ok, n = audit.verify(conn)
    assert ok and n >= 60
