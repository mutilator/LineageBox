-- Family members who sign in (accounts.py), what each may see (access.py) and what they changed (changes.py).
-- With no accounts, or accounts switched off (settings key accounts_enabled), LineageBox works as before: home network
-- only, no sign-in.
CREATE TABLE account (
  id            INTEGER PRIMARY KEY,
  email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
  person_id     INTEGER REFERENCES person(id) ON DELETE SET NULL,  -- who they are in the family tree
  role          TEXT NOT NULL DEFAULT 'member',                    -- admin|member
  upload_folder TEXT,                                              -- their uploads, '/' separated from the share
  local_secret  TEXT,                                              -- admins: TOTP secret for signing in at home
                                                                   -- when Google can't be reached (base32)
  created_at    TEXT NOT NULL,
  last_seen_at  TEXT,
  disabled_at   TEXT
);
CREATE TABLE session (
  token_hash   TEXT PRIMARY KEY,                                   -- sha256 of the cookie's token, never the token
  account_id   INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
  created_at   TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  expires_at   TEXT NOT NULL,
  ip           TEXT,
  user_agent   TEXT
) WITHOUT ROWID;
CREATE INDEX session_account ON session(account_id);
-- Photos a member uploaded, kept once the worker has read them, so they stay theirs if the file is moved.
CREATE TABLE upload (
  image_id      INTEGER NOT NULL REFERENCES image(id),
  account_id    INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
  uploaded_at   TEXT NOT NULL,
  original_name TEXT,
  PRIMARY KEY (image_id, account_id)
) WITHOUT ROWID;
CREATE INDEX upload_account ON upload(account_id);
-- The admin's exceptions to who an account sees: someone also shown or not shown, alone or with their family.
CREATE TABLE access_rule (
  account_id INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
  person_id  INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  show       INTEGER NOT NULL,                                     -- 1 also shown, 0 not shown
  branch     INTEGER NOT NULL DEFAULT 0,                           -- 1 with their family, 0 them alone
  PRIMARY KEY (account_id, person_id)
) WITHOUT ROWID;
-- Changes family members made, with how to undo each (changes.py) and whom they let an account see that it couldn't.
CREATE TABLE change_log (
  id         INTEGER PRIMARY KEY,
  account_id INTEGER REFERENCES account(id) ON DELETE SET NULL,
  at         TEXT NOT NULL,
  kind       TEXT NOT NULL,
  summary    TEXT NOT NULL,                                        -- in words, for Cleanup → Recent changes
  undo       TEXT,                                                 -- JSON
  widened    TEXT,                                                 -- JSON: {account id: [person ids gained]}
  undone_at  TEXT
);
CREATE INDEX change_log_account ON change_log(account_id, at);
-- "Who is this?": a face someone asked about, shown on Home to everyone who can see its photo until it is named.
CREATE TABLE face_question (
  face_id    INTEGER PRIMARY KEY REFERENCES face(id),
  account_id INTEGER REFERENCES account(id) ON DELETE SET NULL,
  asked_at   TEXT NOT NULL
);
CREATE TABLE face_question_skip (                                  -- "Don't know": not asked of them again
  face_id    INTEGER NOT NULL,
  account_id INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
  PRIMARY KEY (face_id, account_id)
) WITHOUT ROWID;
ALTER TABLE person ADD COLUMN added_by INTEGER REFERENCES account(id) ON DELETE SET NULL;
ALTER TABLE person_group ADD COLUMN added_by INTEGER REFERENCES account(id) ON DELETE SET NULL;
ALTER TABLE face ADD COLUMN named_by INTEGER REFERENCES account(id) ON DELETE SET NULL;  -- named by hand by them
ALTER TABLE tour ADD COLUMN account_id INTEGER REFERENCES account(id) ON DELETE CASCADE; -- NULL: the admins'
ALTER TABLE tour ADD COLUMN shared INTEGER NOT NULL DEFAULT 0;     -- an admins' tour members may see
ALTER TABLE image ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0;    -- never shown to members (papers, medical)
ALTER TABLE folder ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0;   -- nor anything in this folder
