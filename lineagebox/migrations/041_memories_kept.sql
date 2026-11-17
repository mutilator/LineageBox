-- Each person's memories on Home (web/routes/home.py), kept until Settings → Browsing says new ones are due or they see
-- new photos. Worked out from what one person sees, they take seconds on a big library, and before this they were kept
-- in memory for 10 minutes, and less: anyone's change anywhere on the site, or a restart, threw them away.
-- account_id NULL is the house's one set, without family accounts; an account's go with it. formers: whether Home's
-- family holds the partners someone once had (a browser's choice, home.formers_shown).
CREATE TABLE memories_kept (
  account_id INTEGER REFERENCES account(id) ON DELETE CASCADE,
  formers    INTEGER NOT NULL,
  made_at    TEXT NOT NULL,     -- clock.now()
  photos     TEXT NOT NULL,     -- JSON: what they saw then (home.seen_photos), to tell new photos and ones taken away
  cards      TEXT NOT NULL      -- JSON: the memories, as memories.row gives them
);
CREATE UNIQUE INDEX memories_kept_whose ON memories_kept(coalesce(account_id, 0), formers);
