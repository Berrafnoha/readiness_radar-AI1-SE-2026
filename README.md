# Project Readiness Radar

**Authors:** BERRAF Noha & BAKIRI Soundous

Human-in-the-loop consistency checker that sits on top of existing planning sheets (P6 / MS Project / Excel exports). It cross-checks plans, deliveries, quantities, and inspections using **deterministic rules**, surfaces concrete evidence, and enforces separation of duties between **operational confirmation** (Field Engineer) and **formal authorization** (Project Director).

---

## Live Deployments & Interfaces

Both interfaces share the same core rule engine (`radar/`) and PostgreSQL database:

- **Public Web Application (FastAPI):** [https://readiness-radar1.onrender.com](https://readiness-radar1.onrender.com)  
  *Full multi-user web platform with role-based login, session management, and API documentation (`/docs`).*
- **Internal Company Dashboard (Streamlit):** [https://readiness-radar-f6z5fud4xti6fbzaqbpbkg.streamlit.app](https://readiness-radar-f6z5fud4xti6fbzaqbpbkg.streamlit.app)  
  *Streamlined internal desktop/company interface designed for quick operational reviews and local deployments.*

> *Note: The FastAPI web app runs on Render's free tier and may take ~30–50 seconds to wake up on the first visit after inactivity.*

---

## Architecture

```text
FastAPI Web UI (api.py + web/) ──┐
                                 ├──> radar/services.py ──> radar/rules.py ──> PostgreSQL
Streamlit App (app.py) ──────────┘         ├── audit.py (hash-chained log)
                                           ├── explainer.py (grounded AI)
                                           ├── pdf_record.py (verification PDF)
                                           └── whatif.py (scenario simulation)
---
 ## Run (PostgreSQL + real login)
Requirements: Python 3.12, Docker Desktop.
```powershell
docker compose up -d                          # PostgreSQL 16 on localhost:5432 (user/password/db: radar)
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:DATABASE_URL="postgresql://radar:radar@localhost:5432/radar"
$env:ADMIN_USERNAME="admin"
$env:ADMIN_PASSWORD="choose-a-long-password"   # first admin, created only if there are no users yet
python -m pytest -q                           # tests use isolated schemas in the same database
python -m uvicorn api:app --reload            # open http://127.0.0.1:8000 and sign in
```
First start creates the schema (migrations/*.sql), the admin account, and loads the synthetic demo project
(`SEED_DEMO=0` for an empty database). Sign in as admin, open **Users**, and create a Field Engineer and a Project Director.
Command line: `python -m radar.cli create-user NAME --role field_engineer` | `migrate` | `seed-demo`.
The legacy Streamlit demo (`streamlit run app.py`) uses the same database but demo identities, not real login.

## Real login: how it works
- Passwords hashed with scrypt (random salt); minimum 10 characters. Sessions are server-side (token stored hashed), 12 h, HttpOnly + SameSite=Lax cookie (`COOKIE_SECURE=1` behind HTTPS).
- State-changing requests need the `X-Requested-With: radar` header (CSRF defense). Login is throttled (5 failures / 10 min per username, per process).
- Roles: field_engineer (confirms facts), project_director (authorizes), admin (manages users and the demo project only; cannot perform operational actions).
- Services trust only the role carried by the authenticated user object, never a plain string. All login, logout, failed-login and user-admin events go to the audit log.
- Audit appends are serialized with a PostgreSQL advisory lock so concurrent users cannot fork the hash chain (tested with 4 threads).


## Rules
| ID | What it detects |
|----|-----------------|
| R1 | Activity starts before a required item is delivered |
| R2 | Successor starts / planned before predecessor is complete |
| R3 | Stage marked ready/authorized with a required inspection open |
| R4 | Downstream impact: dependency-graph forward pass (NetworkX), enriches R1-R3 and drives the forecast |
| R5 | Estimated quantity differs from the specified quantity beyond a tolerance (default 5%) |
| DQ | Data gaps: dependency cycle, bad dates, no requirement, no delivery, unit mismatch, zero spec |

## Features
- Dashboard: plan vs forecast; Findings with evidence and review workflow (valid / false positive / needs info)
- Stages: engineer submits, director authorizes (blocked while inspections are open; separation of duties)
- Change requests: engineer requests a schedule change with impact preview; only the director approves; history kept
- What-if: compares two mitigation scenarios; schedule impact computed by the engine, costs are editable assumptions
- Import: upload CSV/XLSX with dry-run validation (row-level errors), atomic replace, audit log preserved
- Simulated offline sync: engineer updates queue in SQLite, conflicts detected on sync, director actions disabled offline
- AI explainer (Gemini/Groq): sees only computed finding facts; output rejected if it contains numbers not in the facts
- PDF "Verification record (DRAFT)" with evidence, review history, sign-off block and content hash
- Audit log: append-only (DB triggers) and hash-chained; verify button; CSV/JSON export

## Honest limitations
- Offline mode is a simulation of queue-and-sync, not network detection. The Streamlit demo still uses demo identities.
- No password reset by e-mail, no MFA, no per-project access control (one shared project), login throttle is per process. Dates and timestamps are stored as ISO text (not native DATE types).
- Production use still needs HTTPS (reverse proxy), database backups and secrets management.
- Site location on the PDF is typed by the user, not captured by GPS. A typed name is not a legal signature.
- Dependencies are finish-to-start only, calendar days, no resource levelling: a consistency checker, not a planner.
- Approving a change shifts the activity and its downstream chain by the stored impact snapshot.
- Not built: PDF/quotation extraction with human confirmation (see docs/NEXT_STEPS.md, prompt 8), real authentication, P6 XER import.
- Audit chain is tamper-evident, not tamper-proof (someone with raw DB access could rebuild it).

