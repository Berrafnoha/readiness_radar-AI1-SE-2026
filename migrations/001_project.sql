CREATE TABLE stages (id TEXT PRIMARY KEY, name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','ready_requested','authorized')),
  authorized_by TEXT, authorized_at TEXT);
CREATE TABLE activities (id TEXT PRIMARY KEY, name TEXT NOT NULL, work_package TEXT,
  stage_id TEXT REFERENCES stages(id), planned_start TEXT NOT NULL, planned_finish TEXT NOT NULL,
  actual_start TEXT, status TEXT NOT NULL DEFAULT 'planned');
CREATE TABLE dependencies (predecessor_id TEXT REFERENCES activities(id),
  successor_id TEXT REFERENCES activities(id), PRIMARY KEY (predecessor_id, successor_id));
CREATE TABLE requirements (id INTEGER PRIMARY KEY, activity_id TEXT REFERENCES activities(id),
  item TEXT NOT NULL, quantity TEXT);
CREATE TABLE deliveries (id SERIAL PRIMARY KEY, item TEXT NOT NULL, supplier TEXT,
  promised_date TEXT NOT NULL, source_doc TEXT, confirmed INTEGER NOT NULL DEFAULT 0,
  confirmed_by TEXT, created_at TEXT DEFAULT (now()::text));
CREATE TABLE inspections (id TEXT PRIMARY KEY, stage_id TEXT REFERENCES stages(id), name TEXT NOT NULL,
  required INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
  closed_by TEXT, closed_at TEXT);
CREATE TABLE specifications (item TEXT PRIMARY KEY, spec_quantity DOUBLE PRECISION, unit TEXT, source_doc TEXT, page TEXT);
CREATE TABLE estimates (id SERIAL PRIMARY KEY, item TEXT NOT NULL, quantity DOUBLE PRECISION, unit TEXT,
  source_doc TEXT, confirmed INTEGER NOT NULL DEFAULT 0, confirmed_by TEXT, created_at TEXT DEFAULT (now()::text));
CREATE TABLE schedule_changes (id SERIAL PRIMARY KEY, activity_id TEXT, old_start TEXT, new_start TEXT,
  reason TEXT, requested_by TEXT, requested_at TEXT, status TEXT NOT NULL DEFAULT 'pending', decided_by TEXT,
  decided_at TEXT, decision_note TEXT, impact_json TEXT);
CREATE TABLE activity_history (id SERIAL PRIMARY KEY, activity_id TEXT, old_start TEXT, old_finish TEXT,
  new_start TEXT, new_finish TEXT, change_id INTEGER, changed_at TEXT);
CREATE TABLE findings (fingerprint TEXT PRIMARY KEY, rule_id TEXT, kind TEXT, severity TEXT, title TEXT,
  evidence TEXT, affected TEXT, status TEXT NOT NULL, signature TEXT, first_seen TEXT, updated_at TEXT);
CREATE TABLE reviews (id SERIAL PRIMARY KEY, fingerprint TEXT, "user" TEXT, decision TEXT, comment TEXT, ts TEXT);
CREATE TABLE explanations (fingerprint TEXT, signature TEXT, summary TEXT, actions_json TEXT, source TEXT,
  created_at TEXT, PRIMARY KEY (fingerprint, signature));
CREATE TABLE pending_sync (id SERIAL PRIMARY KEY, "user" TEXT, action TEXT, args_json TEXT,
  base_fact_json TEXT, queued_at TEXT, status TEXT NOT NULL DEFAULT 'queued', result_note TEXT, synced_at TEXT);
CREATE TABLE audit_log (seq SERIAL PRIMARY KEY, ts TEXT NOT NULL, "user" TEXT NOT NULL, role TEXT NOT NULL,
  action TEXT NOT NULL, payload TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE OR REPLACE FUNCTION audit_append_only() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'audit log is append-only'; END; $$ LANGUAGE plpgsql;
CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_log FOR EACH ROW EXECUTE FUNCTION audit_append_only();
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_log FOR EACH ROW EXECUTE FUNCTION audit_append_only();
CREATE TRIGGER audit_no_truncate BEFORE TRUNCATE ON audit_log FOR EACH STATEMENT EXECUTE FUNCTION audit_append_only();
