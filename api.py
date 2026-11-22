"""JSON API (real login + sessions) over radar/services, plus the web UI.
Run: python -m uvicorn api:app --reload"""
import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from radar import audit, auth, db, services

COOKIE = "radar_session"
WEB = Path(__file__).parent / "web"


@asynccontextmanager
async def lifespan(app):
    c = db.connect()
    try:
        db.migrate(c)
        auth.ensure_admin(c)
        if os.environ.get("SEED_DEMO", "1") == "1" and not db.is_seeded(c):
            db.reset_demo(c)
            services.recalc(c)
    finally:
        c.close()
    yield


app = FastAPI(title="Project Readiness Radar", lifespan=lifespan)


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    """State-changing requests must carry a custom header (cross-site forms cannot set it). Cookie is SameSite=Lax."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.headers.get("x-requested-with") != "radar":
        return JSONResponse({"detail": "Missing X-Requested-With header."}, status_code=403)
    return await call_next(request)


def get_conn():
    c = db.connect()
    try:
        yield c
    finally:
        c.close()


def current_user(request: Request, c=Depends(get_conn)):
    u = auth.user_from_token(c, request.cookies.get(COOKIE))
    if not u:
        raise HTTPException(401, "Not logged in.")
    return u


def call(fn, c, user, *args):
    """Business rules and permissions live in services/auth; this only maps errors to HTTP codes."""
    try:
        return fn(c, user, *args) or {"ok": True}
    except auth.Throttled as e:
        raise HTTPException(429, str(e))
    except auth.AuthError as e:
        raise HTTPException(401, str(e))
    except services.PermissionDenied as e:
        raise HTTPException(403, str(e))
    except services.Blocked as e:
        raise HTTPException(409, str(e))
    except services.RadarError as e:
        raise HTTPException(400, str(e))


class Login(BaseModel):
    username: str
    password: str


class Password(BaseModel):
    old: str
    new: str


class NewUser(BaseModel):
    username: str
    full_name: str
    password: str
    role: str


class Active(BaseModel):
    active: bool


class NewPassword(BaseModel):
    password: str


class Review(BaseModel):
    fingerprint: str
    decision: str
    comment: str = ""


class Confirm(BaseModel):
    item: str
    date: date
    note: str = ""


def me(u):
    return {"username": str(u), "full_name": u.full_name, "role": u.role}


# ---------------- pages ----------------
@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


# ---------------- auth ----------------
@app.post("/api/auth/login")
def login(b: Login, response: Response, c=Depends(get_conn)):
    try:
        token, user = auth.login(c, b.username, b.password)
    except auth.Throttled as e:
        raise HTTPException(429, str(e))
    except auth.AuthError as e:
        raise HTTPException(401, str(e))
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=os.environ.get("COOKIE_SECURE", "0") == "1",
                        max_age=auth.SESSION_HOURS * 3600, path="/")
    return me(user)


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, c=Depends(get_conn)):
    token = request.cookies.get(COOKIE)
    auth.logout(c, token, auth.user_from_token(c, token))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def whoami(user=Depends(current_user)):
    return me(user)


@app.post("/api/auth/password")
def change_password(b: Password, response: Response, user=Depends(current_user), c=Depends(get_conn)):
    call(auth.change_password, c, user, b.old, b.new)
    response.delete_cookie(COOKIE, path="/")          # all sessions were revoked: log in again
    return {"ok": True}


# ---------------- admin ----------------
@app.get("/api/admin/users")
def users(user=Depends(current_user), c=Depends(get_conn)):
    return call(auth.list_users, c, user)


@app.post("/api/admin/users")
def create_user(b: NewUser, user=Depends(current_user), c=Depends(get_conn)):
    return call(auth.create_user, c, user, b.username, b.full_name, b.password, b.role)


@app.post("/api/admin/users/{uid}/active")
def set_active(uid: int, b: Active, user=Depends(current_user), c=Depends(get_conn)):
    return call(auth.set_active, c, user, uid, b.active)


@app.post("/api/admin/users/{uid}/password")
def reset_password(uid: int, b: NewPassword, user=Depends(current_user), c=Depends(get_conn)):
    return call(auth.reset_password, c, user, uid, b.password)


@app.post("/api/reset")
def reset(user=Depends(current_user), c=Depends(get_conn)):
    if user.role != "admin":
        raise HTTPException(403, "Only an administrator can reset the demo project.")
    db.reset_demo(c)
    services.recalc(c)
    return {"ok": True}


# ---------------- project data ----------------
@app.get("/api/dashboard")
def dashboard(user=Depends(current_user), c=Depends(get_conn)):
    state, slips = services.forecast(c)
    active = [f for f in services.list_findings(c) if f["status"] in services.ACTIVE]
    acts = [{"id": a["id"], "name": a["name"], "stage": a["stage"], "status": a["status"], "start": a["start"].isoformat(),
             "finish": a["finish"].isoformat(), "slip": slips.get(a["id"], 0)}
            for a in sorted(state.activities.values(), key=lambda x: (x["start"], x["id"]))]
    return {"risks": sum(f["kind"] == "risk" for f in active), "gaps": sum(f["kind"] == "data_gap" for f in active),
            "slipping": len(slips), "worst": max(slips.values(), default=0), "activities": acts}


@app.get("/api/findings")
def findings(user=Depends(current_user), c=Depends(get_conn)):
    return services.list_findings(c)


@app.post("/api/review")
def review(b: Review, user=Depends(current_user), c=Depends(get_conn)):
    return call(services.review_finding, c, user, b.fingerprint, b.decision, b.comment)


@app.post("/api/confirm")
def confirm(b: Confirm, user=Depends(current_user), c=Depends(get_conn)):
    return call(services.confirm_delivery_date, c, user, b.item, b.date, b.note)


@app.get("/api/stages")
def stages(user=Depends(current_user), c=Depends(get_conn)):
    return [{**dict(s), "inspections": [dict(i) for i in c.execute("SELECT id,name,status,closed_by FROM inspections WHERE stage_id=?", (s["id"],))]}
            for s in c.execute("SELECT * FROM stages ORDER BY id")]


@app.post("/api/stages/{sid}/ready")
def ready(sid: str, user=Depends(current_user), c=Depends(get_conn)):
    return call(services.request_stage_ready, c, user, sid)


@app.post("/api/stages/{sid}/authorize")
def authorize(sid: str, user=Depends(current_user), c=Depends(get_conn)):
    return call(services.authorize_stage, c, user, sid)


@app.post("/api/inspections/{iid}/close")
def close(iid: str, user=Depends(current_user), c=Depends(get_conn)):
    return call(services.close_inspection, c, user, iid, "Closed via web app")


@app.get("/api/audit")
def audit_log(user=Depends(current_user), c=Depends(get_conn)):
    ok, info = audit.verify(c)
    return {"ok": ok, "info": info, "rows": audit.rows(c)}
