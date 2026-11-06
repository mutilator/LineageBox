-- Passkeys (passkeys.py): signing in without Google, with the lock of a phone or computer (its fingerprint, face or
-- PIN). Only each passkey's public key is kept, so a copy of the catalog can't sign anyone in with one.
CREATE TABLE passkey (
  id            INTEGER PRIMARY KEY,
  account_id    INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
  credential_id BLOB NOT NULL UNIQUE,                       -- how the phone or computer names it
  public_key    BLOB NOT NULL,                              -- COSE, as it gave it
  sign_count    INTEGER NOT NULL DEFAULT 0,                 -- 0 for synced passkeys, which don't count
  rp_id         TEXT NOT NULL,                              -- the name it was made for: it works on that name only
  transports    TEXT,                                       -- JSON: how the browser reaches it (internal, hybrid...)
  synced        INTEGER NOT NULL DEFAULT 0,                 -- kept in a password manager (iCloud, Google...)
  name          TEXT NOT NULL,                              -- what the Passkeys page calls it
  created_at    TEXT NOT NULL,
  last_used_at  TEXT
);
CREATE INDEX passkey_account ON passkey(account_id);
-- An admin's link for making a passkey: kept as its SHA-256, as sessions are; it goes when used, and a new one for
-- the same account replaces it.
CREATE TABLE passkey_invite (
  token_hash TEXT PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES account(id) ON DELETE CASCADE,
  made_by    INTEGER REFERENCES account(id) ON DELETE SET NULL,
  made_at    TEXT NOT NULL,
  expires_at TEXT NOT NULL
) WITHOUT ROWID;
CREATE INDEX passkey_invite_account ON passkey_invite(account_id);
-- The random id passkeys know an account by: never its email, nor its number, which a new account can be given
-- after an old one is removed.
ALTER TABLE account ADD COLUMN passkey_user BLOB;
CREATE UNIQUE INDEX account_passkey_user ON account(passkey_user);
-- A session started with a passkey ends when that passkey is taken away (a lost phone). first_sign_in: the
-- account's first ever, which may add passkeys for longer (passkeys.may_add).
ALTER TABLE session ADD COLUMN passkey_id INTEGER REFERENCES passkey(id) ON DELETE CASCADE;
ALTER TABLE session ADD COLUMN first_sign_in INTEGER NOT NULL DEFAULT 0;
