"""Real authentication: scrypt password hashing, server-side sessions, login throttle, user administration."""
import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

from . import audit
from .errors import PermissionDenied, RadarError

ROLES = ("field_engineer", "project_director", "admin")
MIN_PASSWORD = 10
SESSION_HOURS = 12
_FAILS = {}                      # in-memory login throttle (per process)
MAX_FAILS, WINDOW = 5, 600


class AuthError(RadarError):
    pass


class Throttled(AuthError):
    pass


class User(str):
    """A username (str) that carries its verified role. Services trust ONLY this role."""
    def __new__(cls, username, role, user_id=None, full_name=""):
        o = str.__new__(cls, username)
        o.role, o.user_id, o.full_name = role, user_id, full_name
        return o


SYSTEM = User("system", "admin")


def _b64(b):
    return base64.b64encode(b).decode()


def hash_password(pw, n=2 ** 14, r=8, p=1):
    salt = os.urandom(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(dk)}"


def verify_password(pw, stored):
    try:
        _, n, r, p, salt, dk = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except Exception:
        return False


_DUMMY = None


def _check_throttle(username):
    now = time.time()
    _FAILS[username] = [t for t in _FAILS.get(username, []) if now - t < WINDOW]
    if len(_FAILS[username]) >= MAX_FAILS:
        raise Throttled("Too many failed attempts. Try again in a few minutes.")


def login(conn, username, password):
    """Returns (session_token, User). Same error for unknown user and wrong password."""
    global _DUMMY
    username = (username or "").strip().lower()
    _check_throttle(username)
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    _DUMMY = _DUMMY or hash_password("dummy-password-for-timing")
    ok = verify_password(password or "", row["password_hash"] if row else _DUMMY)
    if not (row and ok and row["is_active"]):
        _FAILS.setdefault(username, []).append(time.time())
        audit.append(conn, "anonymous", "none", "login_failed", {"username": username})
        conn.commit()
        raise AuthError("Invalid username or password.")
    _FAILS.pop(username, None)
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?,?,?)",
                 (hashlib.sha256(token.encode()).hexdigest(), row["id"], datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)))
    conn.execute("UPDATE users SET last_login=? WHERE id=?", (datetime.now(timezone.utc).isoformat(timespec="seconds"), row["id"]))
    conn.execute("DELETE FROM sessions WHERE expires_at < now()")
    user = User(row["username"], row["role"], row["id"], row["full_name"])
    audit.append(conn, user, user.role, "login", {})
    conn.commit()
    return token, user


def user_from_token(conn, token):
    if not token:
        return None
    r = conn.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id "
                     "WHERE s.token_hash=? AND s.expires_at > now() AND u.is_active", (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    return User(r["username"], r["role"], r["id"], r["full_name"]) if r else None


def logout(conn, token, user=None):
    conn.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256((token or "").encode()).hexdigest(),))
    if user:
        audit.append(conn, user, user.role, "logout", {})
    conn.commit()


# ---- user administration (admin role only) ----
def _admin(actor):
    if getattr(actor, "role", None) != "admin":
        raise PermissionDenied("Only an administrator can manage users.")


def _check_password(pw):
    if not pw or len(pw) < MIN_PASSWORD:
        raise RadarError(f"Password must be at least {MIN_PASSWORD} characters.")


def create_user(conn, actor, username, full_name, password, role):
    _admin(actor)
    username = (username or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9._-]{3,32}", username):
        raise RadarError("Username: 3-32 characters, letters, digits, dot, dash or underscore.")
    if role not in ROLES:
        raise RadarError("Unknown role.")
    if not (full_name or "").strip():
        raise RadarError("Full name is required.")
    _check_password(password)
    if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        raise RadarError("That username already exists.")
    conn.execute("INSERT INTO users (username, full_name, password_hash, role) VALUES (?,?,?,?)",
                 (username, full_name.strip(), hash_password(password), role))
    audit.append(conn, actor, actor.role, "user_created", {"username": username, "role": role})
    conn.commit()


def list_users(conn, actor):
    _admin(actor)
    return [dict(r) for r in conn.execute("SELECT id, username, full_name, role, is_active, last_login FROM users ORDER BY id")]


def set_active(conn, actor, user_id, active):
    _admin(actor)
    if user_id == actor.user_id:
        raise RadarError("You cannot deactivate your own account.")
    conn.execute("UPDATE users SET is_active=? WHERE id=?", (bool(active), user_id))
    if not active:
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    audit.append(conn, actor, actor.role, "user_activated" if active else "user_deactivated", {"user_id": user_id})
    conn.commit()


def reset_password(conn, actor, user_id, new_password):
    _admin(actor)
    _check_password(new_password)
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new_password), user_id))
    conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    audit.append(conn, actor, actor.role, "password_reset_by_admin", {"user_id": user_id})
    conn.commit()


def change_password(conn, user, old, new):
    row = conn.execute("SELECT password_hash FROM users WHERE id=?", (user.user_id,)).fetchone()
    if not row or not verify_password(old or "", row["password_hash"]):
        raise AuthError("Current password is incorrect.")
    _check_password(new)
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new), user.user_id))
    conn.execute("DELETE FROM sessions WHERE user_id=?", (user.user_id,))
    audit.append(conn, user, user.role, "password_changed", {})
    conn.commit()


def ensure_admin(conn):
    """Bootstrap: create the first admin from ADMIN_USERNAME / ADMIN_PASSWORD if there are no users."""
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        return False
    u, p = os.environ.get("ADMIN_USERNAME"), os.environ.get("ADMIN_PASSWORD")
    if not (u and p):
        return False
    create_user(conn, SYSTEM, u, "Administrator", p, "admin")
    return True
