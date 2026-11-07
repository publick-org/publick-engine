-- The alerts' D1 database (worker/alerts.js). Apply with:
--   npx wrangler d1 execute <database> --remote --file worker/alerts.sql
-- Every statement can be run again.

-- One row an address followed: the reader's email (lower case), the town (its hostname's first part,
-- "gloucester-ma"), what's followed ('address', and its key, "62 MAIN ST"), the language, the SHA-256 of
-- the confirmation token, when it was asked for, and when it was confirmed (none: not yet).
CREATE TABLE IF NOT EXISTS follows (
  email TEXT NOT NULL,
  town TEXT NOT NULL,
  kind TEXT NOT NULL,
  key TEXT NOT NULL,
  lang TEXT NOT NULL,
  token TEXT NOT NULL,
  created_at TEXT NOT NULL,
  confirmed_at TEXT,
  PRIMARY KEY (email, town, kind, key)
);
CREATE INDEX IF NOT EXISTS follows_by_key ON follows (town, kind, key);
CREATE UNIQUE INDEX IF NOT EXISTS follows_by_token ON follows (token);

-- A reader with a follow, and the SHA-256 of the token in their page's and unsubscribe links.
CREATE TABLE IF NOT EXISTS readers (
  email TEXT PRIMARY KEY,
  manage TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS readers_by_manage ON readers (manage);

-- Each town's last send: the newest mention it covered, and the day (UTC) an email last went out.
CREATE TABLE IF NOT EXISTS sends (
  town TEXT PRIMARY KEY,
  through TEXT NOT NULL,
  day TEXT
);

-- Each email sent, by reader, town and day, recorded before it's sent, so none goes twice.
CREATE TABLE IF NOT EXISTS deliveries (
  email TEXT NOT NULL,
  town TEXT NOT NULL,
  day TEXT NOT NULL,
  PRIMARY KEY (email, town, day)
);
