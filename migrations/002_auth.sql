CREATE TABLE users (id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, full_name TEXT NOT NULL,
  password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('field_engineer','project_director','admin')),
  is_active BOOLEAN NOT NULL DEFAULT TRUE, created_at TEXT DEFAULT (now()::text), last_login TEXT);
CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), expires_at TIMESTAMPTZ NOT NULL);
CREATE INDEX sessions_user_idx ON sessions(user_id);
