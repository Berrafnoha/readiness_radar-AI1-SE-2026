"""PostgreSQL access: connection adapter, migrations, CSV import, demo reset, validated user import."""
import csv
import os
import pathlib
from datetime import date

import psycopg

ROOT = pathlib.Path(__file__).resolve().parent.parent
SEED_DIR = ROOT / "data" / "seed"
MIGRATIONS_DIR = ROOT / "migrations"

# Project tables are dropped/recreated by reset_demo(). users / sessions / schema_migrations are never touched.
TABLES = ["pending_sync", "explanations", "activity_history", "schedule_changes", "estimates", "specifications",
          "audit_log", "reviews", "findings", "inspections", "deliveries", "requirements", "dependencies",
          "activities", "stages"]
IMPORT_ORDER = ["stages", "activities", "dependencies", "requirements", "deliveries", "inspections",
                "specifications", "estimates"]
DATE_COLS = {"planned_start", "planned_finish", "actual_start", "promised_date", "closed_at"}


class Row(dict):
    """Row readable by column name AND by position (row["id"], row[0])."""
    def __getitem__(self, k):
        return list(self.values())[k] if isinstance(k, int) else dict.__getitem__(self, k)


def _row_factory(cursor):
    names = [c.name for c in cursor.description] if cursor.description else []
    return lambda values: Row(zip(names, values))


class Cur:
    def __init__(self, cur):
        self.cur = cur

    def fetchone(self):
        return self.cur.fetchone()

    def fetchall(self):
        return self.cur.fetchall()

    def __iter__(self):
        return iter(self.cur.fetchall())


class PgConn:
    """Thin adapter: keeps the conn.execute(sql, params) style used across the project; ? placeholders."""
    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql, params=None):
        cur = self.raw.cursor(row_factory=_row_factory)
        if params:
            sql = sql.replace("?", "%s")
        cur.execute(sql, params or None)
        return Cur(cur)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()


def database_url():
    return os.environ.get("DATABASE_URL", "postgresql://radar:radar@localhost:5432/radar")


def connect(url=None, schema=None):
    kw = {"options": f"-c search_path={schema}"} if schema else {}
    return PgConn(psycopg.connect(url or database_url(), **kw))


def migrate(conn):
    """Apply migrations/*.sql in order, once each."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT DEFAULT (now()::text))")
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if f.stem not in done:
            conn.execute(f.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (f.stem,))
    conn.commit()


def import_csv(conn, table, path):
    """Import one CSV. Raises ValueError with the row number on bad dates."""
    with open(path, newline="", encoding="utf-8") as fh:
        for n, row in enumerate(csv.DictReader(fh), start=2):
            row = {k: (v.strip() or None) for k, v in row.items()}
            for col in DATE_COLS & row.keys():
                if row[col]:
                    try:
                        date.fromisoformat(row[col])
                    except ValueError:
                        raise ValueError(f"{path.name} line {n}: '{row[col]}' is not YYYY-MM-DD ({col})")
            cols = ",".join(row)
            conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({','.join('?' * len(row))})", list(row.values()))


def reset_demo(conn, seed_dir=SEED_DIR):
    """DEMO ONLY: wipe project data (including the audit log) and reload the synthetic project. Users are kept."""
    conn.execute("".join(f"DROP TABLE IF EXISTS {t} CASCADE;" for t in TABLES) + "DROP FUNCTION IF EXISTS audit_append_only() CASCADE;")
    conn.execute((MIGRATIONS_DIR / "001_project.sql").read_text(encoding="utf-8"))
    for t in IMPORT_ORDER:
        import_csv(conn, t, seed_dir / f"{t}.csv")
    conn.commit()


def is_seeded(conn):
    try:
        n = conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
        conn.rollback()
        return n > 0
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        return False


# ---------------------------------------------------------------------------------------------
# User data import: validate first (dry run), then replace project data in ONE transaction.
# The audit log is never touched.
# ---------------------------------------------------------------------------------------------
import networkx as nx

REQUIRED = {"activities": ["id", "name", "stage_id", "planned_start", "planned_finish"],
            "dependencies": ["predecessor_id", "successor_id"],
            "requirements": ["id", "activity_id", "item"],
            "deliveries": ["item", "promised_date"],
            "inspections": ["id", "stage_id", "name"]}
OPTIONAL = ["stages", "specifications", "estimates"]
INSERT_ORDER = ["stages", "activities", "dependencies", "requirements", "deliveries", "inspections",
                "specifications", "estimates"]
CLEAR_ORDER = ["findings", "reviews", "explanations", "pending_sync", "schedule_changes", "activity_history",
               "estimates", "specifications", "inspections", "deliveries", "requirements", "dependencies",
               "activities", "stages"]


def _norm_date(v):
    from datetime import datetime
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def validate_dataset(frames):
    """frames: {table: [row dicts]}. Returns (clean_frames, errors, notes). Nothing is written."""
    errors, notes, clean = [], [], {}
    for t in list(REQUIRED) + OPTIONAL:
        rows = frames.get(t)
        if rows is None:
            if t in REQUIRED:
                errors.append(f"{t}: file is missing")
            continue
        cols = set(rows[0]) if rows else set()
        miss = [c for c in REQUIRED.get(t, []) if c not in cols]
        if miss:
            errors.append(f"{t}: missing column(s) {', '.join(miss)}")
            continue
        out, converted = [], 0
        for n, r in enumerate(rows, start=2):
            r = {str(k).strip(): (str(v).strip() if v is not None and str(v).strip() != "" else None) for k, v in r.items()}
            for col in DATE_COLS & r.keys():
                if r[col]:
                    nd = _norm_date(r[col])
                    if nd is None:
                        errors.append(f"{t} line {n}: '{r[col]}' is not a valid date ({col}); use YYYY-MM-DD")
                    else:
                        converted += nd != r[col]
                        r[col] = nd
            out.append(r)
        if converted:
            notes.append(f"{t}: {converted} date(s) converted from DD/MM/YYYY to YYYY-MM-DD")
        clean[t] = out
    if errors:
        return clean, errors, notes
    acts = clean["activities"]
    ids = [a["id"] for a in acts]
    for dup in {i for i in ids if ids.count(i) > 1}:
        errors.append(f"activities: duplicate id {dup}")
    if "stages" not in clean:
        clean["stages"] = [{"id": s, "name": s} for s in sorted({a["stage_id"] for a in acts if a["stage_id"]})]
        notes.append("stages: derived from activities.stage_id")
    stage_ids = {s["id"] for s in clean["stages"]}
    for n, a in enumerate(acts, start=2):
        if a["stage_id"] not in stage_ids:
            errors.append(f"activities line {n}: unknown stage {a['stage_id']}")
        if a["planned_start"] and a["planned_finish"] and a["planned_finish"] < a["planned_start"]:
            errors.append(f"activities line {n}: finish is before start ({a['id']})")
        a["status"] = a.get("status") or "planned"
    idset = set(ids)
    for n, d in enumerate(clean["dependencies"], start=2):
        for k in ("predecessor_id", "successor_id"):
            if d[k] not in idset:
                errors.append(f"dependencies line {n}: unknown activity {d[k]}")
    for n, r in enumerate(clean["requirements"], start=2):
        if r["activity_id"] not in idset:
            errors.append(f"requirements line {n}: unknown activity {r['activity_id']}")
    for n, i in enumerate(clean["inspections"], start=2):
        if i["stage_id"] not in stage_ids:
            errors.append(f"inspections line {n}: unknown stage {i['stage_id']}")
        i["required"] = 1 if str(i.get("required") or "1").lower() in ("1", "true", "yes", "oui") else 0
        i["status"] = i.get("status") or "open"
    g = nx.DiGraph([(d["predecessor_id"], d["successor_id"]) for d in clean["dependencies"]])
    if not nx.is_directed_acyclic_graph(g):
        errors.append("dependencies: cycle detected " + " -> ".join(next(nx.simple_cycles(g))))
    return clean, errors, notes


def import_dataset(conn, clean):
    """Replace project data atomically. audit_log is preserved."""
    try:
        for t in CLEAR_ORDER:
            conn.execute(f"DELETE FROM {t}")
        for t in INSERT_ORDER:
            cols_ok = {r["column_name"] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = ?", (t,))}
            for row in clean.get(t, []):
                row = {k: v for k, v in row.items() if k in cols_ok}
                conn.execute(f"INSERT INTO {t} ({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
