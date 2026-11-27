-- Historical papers (papers.py, paper_reading.py, historical.py; PLAN.md §10.16): letters, awards, certificates,
-- property papers, clippings. A paper is a document row, on the image of its first page: a scan or photo of it, or the
-- file it is (image.media 'document': a PDF, a Word or text file, an email). Until now a document was only marked by
-- hand; the worker now finds them too (source 'auto'), and a person's word wins over it.
ALTER TABLE document ADD COLUMN source TEXT NOT NULL DEFAULT 'user';  -- who said what it is: 'auto' or 'user'
ALTER TABLE document ADD COLUMN written TEXT;       -- the date it was written or issued, as it says it ("3 June 1931")
ALTER TABLE document ADD COLUMN written_from TEXT;  -- the days that date means (dates.photo_date), for sorting and
ALTER TABLE document ADD COLUMN written_to TEXT;    -- the decades; NULL when it has none
ALTER TABLE document ADD COLUMN sender TEXT;        -- who wrote or sent it, as it says ("Ada Miller", an email's From)
ALTER TABLE document ADD COLUMN recipient TEXT;     -- whom it was written or sent to
ALTER TABLE document ADD COLUMN language TEXT;      -- what it is written in, as the AI names it ("English", "French")
ALTER TABLE document ADD COLUMN unreadable TEXT;    -- a file whose words can't be read here (an old Word file): why
-- The AI's card (paper_cards.py), made once its pages are read: a sentence or two of what it says, the places it
-- names (JSON), and which model made it, how it was asked, and when (NULL: to be made, or made again).
ALTER TABLE document ADD COLUMN summary TEXT;
ALTER TABLE document ADD COLUMN places TEXT;
ALTER TABLE document ADD COLUMN card_model TEXT;
ALTER TABLE document ADD COLUMN card_version INTEGER;
ALTER TABLE document ADD COLUMN card_at TEXT;

-- A page of a paper scanned page by page, after its first: the first page's image. Left out of every list (as a Live
-- Photo's clip is: catalog.VISIBLE) and seen with its paper.
ALTER TABLE image ADD COLUMN page_of INTEGER REFERENCES image(id) ON DELETE SET NULL;
CREATE INDEX image_page_of ON image(page_of) WHERE page_of IS NOT NULL;

-- The pages of each paper, in reading order, and what each one says.
CREATE TABLE paper_page (
  paper_id    INTEGER NOT NULL REFERENCES document(image_id) ON DELETE CASCADE,
  n           INTEGER NOT NULL,              -- 1, 2, ...
  image_id    INTEGER REFERENCES image(id) ON DELETE CASCADE,  -- a scanned page's own image; NULL: drawn from the file
  text        TEXT,                          -- what the page says, word for word; NULL until it is read
  text_source TEXT,                          -- 'file' (the file's own words), 'ai' (copied out by the AI), 'user'
  unsure      TEXT,                          -- JSON: the lines (from 0) the AI wasn't sure it read right
  blank       INTEGER NOT NULL DEFAULT 0,    -- nothing on it (the back of a page): kept, but nothing to read or show
  model       TEXT,                          -- the AI model that read it, and how it was asked (paper_reading.VERSION)
  version     INTEGER,
  read_at     TEXT,
  edited_at   TEXT,                          -- when a person last corrected its words
  PRIMARY KEY (paper_id, n)
);
CREATE INDEX paper_page_image ON paper_page(image_id) WHERE image_id IS NOT NULL;

-- Whether a photo the AI described as a document is a paper (paper_reading.py), so each is asked once: by 'ai', or a
-- person's word ('user'), which the AI never overrides.
CREATE TABLE paper_check (
  image_id INTEGER PRIMARY KEY REFERENCES image(id) ON DELETE CASCADE,
  is_paper INTEGER NOT NULL,
  by       TEXT NOT NULL,                    -- 'ai' or 'user'
  what     TEXT,                             -- what the AI took it for when it isn't one ("a screenshot", "a photo")
  model    TEXT,
  version  INTEGER,
  at       TEXT NOT NULL
);

-- Who a paper names, matched to the tree (paper_cards.py): 'auto' (sure: counts for who sees it, as a recognized
-- face does), 'suggested' (asked about on its page), 'user' (a person said it is them), 'not' (said it isn't).
CREATE TABLE paper_person (
  paper_id  INTEGER NOT NULL REFERENCES document(image_id) ON DELETE CASCADE,
  person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  role      TEXT NOT NULL,                   -- 'wrote', 'received', 'about' or 'named'
  name      TEXT,                            -- as the paper writes it
  state     TEXT NOT NULL,
  at        TEXT NOT NULL,
  PRIMARY KEY (paper_id, person_id)
);
CREATE INDEX paper_person_person ON paper_person(person_id);

-- The words of each page, to search (paper_search.py): rowid = paper id * 10000 + page.
CREATE VIRTUAL TABLE paper_fts USING fts5(about, people, words, tokenize = 'porter unicode61');

-- Introductions (introductions.py): the papers each account opened (0: the house, without family accounts), and
-- those offered to it, one put behind the rest by Show me another (skipped_at).
CREATE TABLE paper_seen (
  account_id INTEGER NOT NULL,
  paper_id   INTEGER NOT NULL REFERENCES document(image_id) ON DELETE CASCADE,
  seen_at    TEXT NOT NULL,
  PRIMARY KEY (account_id, paper_id)
);
CREATE TABLE paper_offer (
  account_id INTEGER NOT NULL,
  paper_id   INTEGER NOT NULL REFERENCES document(image_id) ON DELETE CASCADE,
  offered_at TEXT NOT NULL,
  skipped_at TEXT,
  PRIMARY KEY (account_id, paper_id)
);

-- A paper's words are searched (paper_search.py, from search.index_image): a page read or corrected, or who it
-- names, is indexed again; words a person corrected are summed up again (paper_cards.py). Each only adds what isn't
-- there yet, rather than INSERT OR IGNORE: an upsert's own rule (ON CONFLICT DO UPDATE) would override that.
CREATE TRIGGER paper_page_added AFTER INSERT ON paper_page BEGIN
  INSERT INTO search_stale SELECT new.paper_id WHERE NOT EXISTS (SELECT 1 FROM search_stale WHERE image_id = new.paper_id);
END;
CREATE TRIGGER paper_page_changed AFTER UPDATE OF text, blank ON paper_page BEGIN
  INSERT INTO search_stale SELECT new.paper_id WHERE NOT EXISTS (SELECT 1 FROM search_stale WHERE image_id = new.paper_id);
END;
CREATE TRIGGER paper_page_removed AFTER DELETE ON paper_page BEGIN
  INSERT INTO search_stale SELECT old.paper_id WHERE NOT EXISTS (SELECT 1 FROM search_stale WHERE image_id = old.paper_id);
END;
CREATE TRIGGER paper_words_corrected AFTER UPDATE OF text ON paper_page WHEN new.text_source = 'user' BEGIN
  UPDATE document SET card_at = NULL WHERE image_id = new.paper_id;
END;
CREATE TRIGGER paper_details_changed AFTER UPDATE OF kind, title, written, sender, recipient ON document BEGIN
  INSERT INTO search_stale SELECT new.image_id WHERE NOT EXISTS (SELECT 1 FROM search_stale WHERE image_id = new.image_id);
END;
CREATE TRIGGER paper_person_added AFTER INSERT ON paper_person BEGIN
  INSERT INTO search_stale SELECT new.paper_id WHERE NOT EXISTS (SELECT 1 FROM search_stale WHERE image_id = new.paper_id);
END;
CREATE TRIGGER paper_person_changed AFTER UPDATE OF state ON paper_person BEGIN
  INSERT INTO search_stale SELECT new.paper_id WHERE NOT EXISTS (SELECT 1 FROM search_stale WHERE image_id = new.paper_id);
END;
CREATE TRIGGER paper_person_removed AFTER DELETE ON paper_person BEGIN
  INSERT INTO search_stale SELECT old.paper_id WHERE NOT EXISTS (SELECT 1 FROM search_stale WHERE image_id = old.paper_id);
END;

-- The papers already marked by hand were read by no one: each gets its one page, to be read by the worker.
INSERT INTO paper_page(paper_id, n, image_id) SELECT image_id, 1, image_id FROM document;
