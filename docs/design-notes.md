# Design notes and measurements

Why LineageBox is built the way it is. Most choices were measured on a real family library of a few tens of thousands
of photos and over a thousand videos, from DSLRs, compact cameras, a camcorder and two decades of phones. The tests ran
on a desktop PC with a mid-range graphics card, and on a small ARM board for the Docker install. The numbers are a
guide, not a promise for other libraries.

## Principles

- **The photos are read-only.** All derived data (database, thumbnails, previews, face crops, frames) lives in the
  catalog folder. Only Cleanup, uploads, 720p copies, confirmed rotations and, once an admin turns them on, XMP
  sidecars beside the photos write to the photo folders. A photo file itself changes only when someone asks: turning
  it, or an admin writing the family's names and places into it.
- **Read each original once.** Ingest computes the hash, EXIF, perceptual hash, thumbnail and preview from one read.
  Faces, the AI and the web UI work from the local preview, so they don't depend on a slow or sleeping share. 2048 px
  is the size face detection was tested at.
- **Every stage is idempotent and resumable.** Stop it at any time. Failures are recorded per photo.
- **People's edits always win.** Anything a person set by hand is never overwritten by a re-run.
- **The AI never sees names.** Descriptions are kept apart from naming, so renaming or merging people never needs a
  re-run, which would take days for a big library.
- **AI output is versioned.** Each description records its model and prompt version, so a subset can be described again
  with a better model and compared. Going back to an earlier model doesn't redo its photos.
- **Nothing is specific to one library.** Suggestions, defaults and thresholds come from counts over the installer's
  own catalog at run time, never from one family's names, places, years or tags. A library may be used to measure a
  rule, but the result must be a rule. Suggested tours work this way (`tour_suggestions.py`).

## What a family library looks like

- **Duplicates are everywhere.** About 30% of files were extra copies: whole albums copied under several folders.
  Same name and size matched identical SHA-256 in every sampled group, so exact hashing removes them.
- **Near-duplicates need perceptual hashing:** resized copies in `Previews`/`Thumbs` folders, and edited copies
  (`DSC_8627.jpg` and `DSC_8627-2.jpg`) that differ in bytes.
- **GPS is rare** (2% here), so places mostly come from folder names. **Folder names are rich**: places ("Grand Canyon
  AZ"), events ("5th Birthday", "Halloween"), people, dates in many formats ("2007-03-08", "Easter13"), and junk
  (`Thumbs`, `.thumbnails`, `AVCHD`, `*_files`).
- **Capture dates:** 92% had EXIF dates. The rest come from folder names, file names (`IMG_20160811_182239`) or file
  times, and the source is recorded, so dates from file times can be listed and fixed.

## Faces: InsightFace `buffalo_l`

SCRFD detection with an ArcFace R50 embedding, on onnxruntime. It was chosen as the most accurate open-source model
that installs without compiling anything. `face_recognition`/dlib is older and markedly worse on children and turned
faces. DeepFace pulls in TensorFlow.

- **Speed:** 36–57 ms a photo on the GPU, about 2.3 s on a desktop CPU, and 0.75 s median on the ARM board, with
  the same faces found. The embeddings matched the GPU's to ≥ 0.9997, so faces run fine on a NAS.
- **Junk faces** (backs of heads, stuffed toys, a dog) have a low ArcFace embedding norm: a median of 12.1 against 21.5
  overall. A cut-off around 15 drops them and keeps about 90% of faces.
- **Grouping:** HDBSCAN over unnamed faces, on 128 PCA dimensions. That gave the purest groups when measured. Random
  samples of the largest groups were each one person.
- **The hard limit:** two brothers as toddlers scored 0.50 against each other, the same as one baby at two different
  events (0.51). No single threshold separates them. So recognition is conservative: the top-3 similarity must be
  ≥ 0.5 and 0.1 ahead of the runner-up, with one person per photo. A person confirms the rest. **Seen together** uses
  the "nobody is in a photo twice" rule to suggest the closest person not already named in the photo.
- **Groups that look like someone** (`people.looks_like`): a group's faces scored together. Each person's hand-named
  faces of one day, taken as a group (1,299 groups) against everyone's faces of other days, scored best for the right
  person 97.7% of the time. At ≥ 0.4 and 0.05 ahead of the next, 92% of the groups got a name and 99.9% of those were
  right; for the 369 groups whose faces recognition mostly wouldn't have named one by one, 71% and 99.6%. The young
  brothers' groups were all right, though their single faces can't be told apart. Faces set aside as strangers,
  grouped the same way, were taken for someone in 36 of 554 groups, so the name is only filled in for a person to
  confirm.
- **Groups of 2 or 3** (`people._small_groups`): HDBSCAN with smaller groups split people up (at 2, 1,649 groups of
  7,716 hand-named faces instead of 347). Instead, among the faces it leaves alone, each is linked to its closest other
  (never one of its own photo) at ≥ 0.5, and what that joins, if 2 or 3 faces, is a group: 135 pairs were one person
  99.3% of the time and 72 threes 100%; fours and more joined that way were 92%, so they are left alone.
- **One person named twice** (`people.same_by_face`): from the person with fewer hand-named faces, the median over
  their faces of the mean of each one's 3 closest of the other's. One person's faces of one day against their other
  faces scored ≥ 0.4 94% of the time; of 5,836 pairs of different people never in one photo together, the closest
  scored 0.35. Comparing 12 faces of each first, spread over the years, kept 99.2% of the pairs found and takes half a
  second.
- **Faces missed:** the AI counts the people it sees. 3,495 of 36,969 described photos had 3 or more people and at least
  2 more than faces found, 882 of them with someone named. Those come first; the rest are often crowds or people seen
  from behind. Looking again (1280 px, a lower bar) is offered on each.
- InsightFace licenses these models for **non-commercial research purposes only**. LineageBox downloads them; it doesn't
  include them.

### What a face's estimated age can say

InsightFace estimates an age for every face (`face.est_age`). Measured on 3,612 hand-named faces of 16 people with a
birth date, on photos with a camera date (plus 16,292 recognised ones), it can't tell anyone's age:

| True age | Faces | Estimate's error, mean | 90% within |
|---|---|---|---|
| under 2 | 397 | +28.5 years | 47 |
| 2–3 | 297 | +26.9 | 42 |
| 4–6 | 366 | +26.8 | 47 |
| 7–12 | 453 | +22 | 34 |
| 13–17 | 168 | +14.0 | 22 |
| 18–24 | 184 | +16.7 | 38 |
| 25–39 | 916 | +8.0 (10.2 either way) | 27 |
| 40–59 | 786 | +2.1 (10.2 either way) | 20 |
| 60 and over | 46 | −12.2 (14.3 either way) | 33 |

Children are estimated as adults, and across one person's photos the estimate rose with their age no more than
chance (rank correlation −0.36 to 0.49). Two young brothers in 1,357 photos together: the older looked older 49% of the
time. So the rule is:

- **Recognition doesn't use ages.** Birth dates do help: nobody is recognised or suggested in a photo taken before they
  were born.
- **Only "a young child" is said.** All 134 faces estimated under 15 were children of 10 or less (2 of 1,563
  recognised ones were 12 or 13), though only one face in seven of a child under 7 is estimated that young. So a face
  estimated under 15 named as someone 18 or older then is listed under Confirm faces → **Ages to check**; and someone
  with no birth date whose face looks that young on two days or more gets a hint by Born: born at most 12 years
  before the second-latest of those days, and not after their first photo (nothing when the two disagree). Nothing
  is said of an adult's age.
- **Named before they were born** is the other check, and the most useful: 535 named faces were in photos dated before
  the person's birth, 533 of them from one camera whose clock had been two years behind for a year, and reset to its
  first day once. Hence **Camera clocks**, the cameras with such faces first. Its first form (2026-11-07) moved a
  camera's dates between two days by an amount typed in, which meant guessing the amount without seeing the photos.
  Now an admin looks through the camera's photos in the order it took them, marks the first and last it dated wrong,
  and says when one of them was really taken (`camera_clock.py`); a clock that drifted is two or more such photos,
  with the photos between them moved in proportion. A stretch is kept, for photos of that camera read later.

### A date for a photo without one

`date_hints.py` suggests a date for a photo dated only by its file's time or a folder, from what else is known. Each of
36,342 photos with a camera date and 3 or more others in its folder, dated from those as if it had none:

- When the middle 80% of the folder's dated photos were within a month, the photo was within 0.6 of a month of their
  middle 99% of the time: the month is suggested (99% right in a check of 600). Within two years, within 7.5 months:
  "about" the year (a year either side).
- The dated photos just before and after it by file name, within a year of each other: within 0.34 years of their
  middle 99% of the time, also in folders spanning many years.
- Who is in it gives a lower bound: no photo is from before the birth of someone in it (true of 96.6% of 15,571
  photos; nearly all the rest were that camera with its clock wrong). A face that looks like a young child's, or a
  death, gives an upper one; within 4 years, those years are suggested.
- The AI's season can't be checked: it is told the date of the photos that have one. It is only mentioned.

651 of that library's 1,615 photos dated by file time or folder had 3 or more dated photos in their folder. A copy of a
dated photo also takes its near-duplicate group's camera date by itself (`dedup.share_dates`), as an automatic date.

## Descriptions: a vision model through Ollama

`ministral-3` (8.9B, Q4_K_M), asked for a JSON-schema answer at temperature 0.1.

- **Image size:** 512 px costs about 830 prompt tokens, and 1024 px about 1,520. It caps around 1,650. 1024 px is
  enough, and the server's default 8k context is plenty.
- **Speed:** about 4 s a photo for the full record on that graphics card, and 2.4–2.6 s with two Ollama slots and four
  requests in flight (1.7× one slot). `gemma3:12b` was more accurate but more than twice as slow.
- **Accuracy:** 11 of 15 varied photos were fully right. Typical mistakes were a fawn called a goat and a red rake
  called an umbrella. Descriptions are labelled as AI-made in the UI, and people's tags override them.
- **Folder context helps a lot.** With the folder's place and event in the prompt, the model names landmarks it would
  otherwise miss. An eclipse photo went from "a firework explodes" to "partial solar eclipse".
- **Don't send `num_ctx`.** When another client (Open WebUI, say) shares the Ollama server, a context size
  different from the server's default makes Ollama reload the model every time the two clients alternate.
- **Folder names in batches** of 25, matched by id, because the model rewrites paths. The answer is indented JSON
  of about 67 tokens a folder (1,560–1,670 for 25), so a batch gets 120 tokens a folder, not the 1,024 a description
  gets. An answer cut off anyway is asked again in halves, so a wordier model still gets through, and a folder the
  model loops on waits on its own.
- Near-duplicate groups are described once, from their largest member, and the result is copied to the others.
- The LLM's own "blurry" and "document/screenshot" flags were unreliable (blurry on 74% of photos), so Cleanup doesn't
  use them. Blur is a pixel sharpness score: the lowest 1% is almost all black frames, blank sky and lens caps. Papers
  and screens are specific phrases in descriptions of photos without people.

## Search by meaning: descriptions read by a small Ollama model

The owner's choice (2026-11-07): the AI's descriptions read by an embedding model on the same Ollama, not a model that
looks at the pictures on the LineageBox machine. Words find words when the PC is away, as before.

- **Four models tried** (all Apache-2.0), on 30 made-up descriptions with 30 paraphrased searches ("ski trip",
  "just born", "a pup by the seaside"), and on 4,000 real descriptions read straight from a copy of a catalog, with 20
  searches whose right answers were told by a word they must hold:

  | Model | Size | Dimensions | Right among the first 20 found, real descriptions | A description on a desktop processor alone |
  |---|---|---|---|---|
  | `nomic-embed-text` | 274 MB | 768 | 81% | 56 ms |
  | `mxbai-embed-large` | 670 MB | 1,024 | 79% | 133 ms |
  | `all-minilm` | 46 MB | 384 | 76% | 17 ms |
  | `granite-embedding:30m` | 63 MB | 384 | 69% | |

  All four put the right made-up description first for almost every paraphrase. `nomic-embed-text` was picked: the
  most right on real descriptions, quick enough on a processor alone (20,000 descriptions in about 20 minutes), and
  768 numbers a photo (3 KB; 60 MB in memory for 20,000). A search's words take about 0.17 s, nearly all of it
  Ollama's own time per request, on a graphics card or not.
- **How close is close enough** (`embeddings.FLOORS`): the likeness above which most of what is found is about the
  search. For `nomic-embed-text`, 0.62: above it about 60% of what is found holds the search's word, and about half of
  all that does is found; 0.70 finds a sixth of it. A model not measured is held to 85% of its best match.
- **Blending** is reciprocal rank fusion (`search._with_meaning`): each photo counts 1 / (10 + its place) in the word
  matches and in the meaning matches. A photo found both ways comes first; then the best of each mix, so a search
  whose words are in descriptions still shows them first. The words that name people (names, family words) and
  years aren't read for meaning, and must match as words: "Emma sledding" is Emma's photos, not every sledding photo.
- **Its own words, and the family's:** what is read is the family's caption, then the AI's caption, setting,
  occasion, and its lists of activities, things, animals and tags, at most 1,500 characters.
- **More like this** shows the 12 photos closest to one, at most 3 from one day: the closest are mostly the same
  party's other photos, which the similar photos and the folder already show.
- Models that ask for words before what they read are given them (`embeddings.PREFIXES`): `search_document:` and
  `search_query:` for nomic-embed-text.
- **On a copy of a 36,000-photo catalog** (made-up vectors, a desktop processor): comparing a search with every photo
  took 2.4 ms, reading all the vectors into memory 0.23 s, the web app's look for new ones 15 ms (from an index on
  `embedding(model, made_at)`; 110 ms without it), and the worker's list of what to read 0.06 s.
- **Memory on a small board:** 40,000 photos at 768 numbers are 123 MB. The web app's matrix is made the size of what
  it reads, then grown by a quarter as the worker adds more (doubling would hold up to 250 MB), and a model changed on
  the Settings page lets the last one's vectors go. A model replaced under the same name by one whose vectors are of
  another length (pulled again, a custom one) has every photo read again: vectors of two lengths can't be compared.

## The database: SQLite with FTS5, embeddings in NumPy

- FTS5 with Porter stemming ranked the right photo first in every test query, and handles plurals and verb forms.
- Nearest faces by brute force in NumPy took 5.7 ms a query over 100,000 faces (200 MB of memory), against 245 ms
  with sqlite-vec. A batch of 1,000 queries took 0.4 s. Descriptions' vectors are compared the same way, kept in the
  web app's memory and topped up every 10 seconds with what the worker added since.
- PostgreSQL with pgvector would suit many concurrent writers. For a household it only adds a service to run.
- Upgrades run under the write lock as the web app starts, so each must be quick. Measured on a copy of a catalog of
  41,664 photos and 57,837 faces (370 MB) on the development PC: migrations 022 to 031 together took 0.05 s (023,
  which makes the search index again, 0.02 s), after 0.9 s for the copy made before them. The worker's next pass
  fills the search index again in 7 s, 25 photos a write.
- Undo keeps each change's rows as they were, in the columns they had then. A change kept before an upgrade added a
  column (a photo's `camera_taken_at`, a person's `biography`) is compared and put back in those columns only: the
  new one was none of the change's, and doesn't make it look changed since.

## The web UI

FastAPI with Jinja2 templates and a few hundred lines of plain JavaScript. There is no Node build step. Pages rendered
in 16–130 ms on the development PC, including a folder of over a thousand photos and a year of several thousand, and
in 36–350 ms on the ARM board.

- **A short menu.** Ten to twelve links in a row were more than a family member needs, and showed them Tags and
  Cleanup first. Home, People, Family, Upload and Folders stay up front, the search box beside them; the rest is
  under More, a menu opened by a click (nothing only on hover), which folds into ☰ with the others. The help's
  "Every page" list follows the menu, and a test checks that they match.
- **One word for each answer about a face.** Six words meant "not this person" (No, ×, stranger, Strangers, Hide,
  don't know who). They are now two, whatever the action behind them, which stays as it was: **Not them** (the face
  isn't who it is named, suggested or grouped as; the × on a small face picture is labelled so) and **Don't know**
  (nobody knows them: stop asking). Don't know on one face stops asking that account; on a group (Don't know them) or
  a face in a photo to finish it sets the faces aside for everyone, as Hide and stranger did, and says so.
- **The first steps are worked out, not ticked by hand.** Each is read from the catalog (a watched folder, a
  description, a person marked as you, faces named, a family link, a member's account), so a step done by the config
  file, an imported tree or the command line counts too. "Name the largest groups" is done once five people are named
  and no unnamed group is larger than the fifth one's faces: on a library the family had worked on for months, 127
  people were named, the largest of the groups left had 17 faces, and the fifth person had hundreds, so it is done
  many times over; on a new one it asks for the people in the most photos. Once all are done the list is noted as
  put away, so Home stops counting faces for it.
- **Light or dark per browser, before the first paint.** The choice is the person's, not the install's, so it lives in
  the browser. A tiny script file in the page's head (the Content-Security-Policy allows no inline script) puts it
  on `<html>` before the stylesheet applies, so a dark page never flashes light. The dark colours are written twice
  in the stylesheet, for the system's setting and for the choice. The tree's picture is drawn from the light colours
  on white, whatever the page shows, since it is printed and sent; printing turns the page light too.
- **Long pages a piece at a time.** A tile is about 430 bytes of page, so a folder of 5,000 photos was over 2 MB of
  page and 5,000 elements for a phone to lay out, all made by the router at once. Folder, year, tag and tour pages now
  send 200 tiles (about 85 kB) and load the next 200 as the page is scrolled near its end; a person's life by age
  already loaded a group at a time (240), and its Show more now opens by itself the same way. The list itself is
  still read whole (one query, cheap), so the scrubber knows every year and month and where each starts, a photo's
  ← → walk the whole list, and Select all can tick photos not loaded yet. Jumping far down loads the page from there
  (`?skip=`) rather than every piece before it, and the address keeps what was loaded (`?upto=`, at most 3,000), so
  Back from a photo lays the same tiles out in the same places and the browser's own scroll position fits. Plain links
  (Show more, Show earlier photos) do the same without scripts.
- **Sharp tiles made when a sharp screen asks.** The 400 px thumbnail is soft where a tile is three device pixels to
  each CSS pixel (phones). A tile twice the size is made from the 2,048 px preview the first time it is asked for
  (about 90 ms on the development PC, decoding the JPEG at half size; at most two at once on the router) and kept,
  about 50–100 kB each. Making them for the whole library at ingest would take hours on the router and room most
  photos never need. `srcset` describes the two by the width a square tile gets from them (300 and 600 px, the short
  side of a 4:3 photo) with `sizes` of a third of a phone's width or 170 px, so a desktop at 1× keeps the thumbnail
  and a phone or a 2× screen takes the sharp one. A preview newer than the sharp tile (a photo turned) has it made
  again; a photo with no preview gets the thumbnail, never a broken tile. A request waits at most 2 seconds for its
  turn, then gets the thumbnail for now: each waiting request holds one of the web app's threads (40), which every
  other request needs too, and a phone on a fast connection asks for a screenful at once. A stand-in is sent
  `no-cache`, so the browser asks again the next time the page shows it, rather than keeping the soft one a year.
- **Alt text from one query per grid.** Each tile's alt text names who is in the photo (faces left to right, then
  people added by hand, four at most), when, and whether it is a video or a recording, read in one query for the
  grid on the viewer's own connection, so a member's tiles name only the people they see.
- **Keys of one letter can be turned off.** Speech software and some screen readers send letters as commands, and a
  stray `g` or `s` can move away from a page. WCAG's character key shortcuts ask for a way to turn such keys off: a
  switch in the help, kept per browser, which every handler of a letter or sign asks. Arrows, Esc, Tab and Enter
  stay. The slideshow is a modal `<dialog>`, so the keyboard and a screen reader stay in it while it plays.
- **Plain words for the family.** Codec names, "720p copy", model names, "the worker" and error messages stay on the
  same pages for admins, who can act on them; members get what it means for them ("it can't be played here yet").
  Keyboard hints are hidden where there is no mouse (`hover: none`), where nobody has a keyboard to press them on.
- **Ready for translation, English only (the owner's choice, 2026-11-07).** The text was moved behind gettext rather
  than translated: Jinja's own i18n extension, catalogs in the standard `.po`/`.mo` format, so any translator's tool
  works, and Python's `gettext` reads them, without Babel at run time. Two kinds of text are kept apart because
  they are escaped differently: `{% trans %}` is the page's HTML (its translation is HTML too, with quotes outside
  tags turned into character references, so a translation can't end an attribute), `_()` plain text. Moving about
  1,700 pieces of text from 92 templates was done by a script and checked by rendering every page before and after,
  as the access sweep asks for them (an admin, two members, signed out, seeing as a member, a photo frame, a home
  install: 7,215 answers): the same as a browser reads them, whitespace aside. The only bytes that changed are
  apostrophes in attributes, now written `&#39;` (what `_()` escapes, as any value is). Sentences split around a
  number or a link stayed in pieces where the page's layout splits them. The scripts get their plural forms as a
  table of each number's form, since the site's Content-Security-Policy allows no `eval` of a catalog's rule.

## On phones, frames and the outbox

- **An installable web app, no native app** (the owner's choice): a web manifest and a service worker give an icon on
  the home screen, a faster start and Android's share sheet. The service worker keeps only the site's own static files
  of the running version (their URLs carry the version, so a new one is fetched after an update) and an offline page.
  Pages and photos always go to the server: what a phone shows after signing out, or after an account is switched
  off, is decided there alone, and photos are kept only as the browser's HTTP cache keeps them (private, by content).
  The share sheet posts to `/upload/shared`; the service worker keeps the files on the phone and hands them to the
  Upload page's own picker, so they go up through the usual, checked upload (one request per file, which needs the
  app's header a share-sheet post can't carry). Phones install only over HTTPS; over plain http nothing is
  registered, so the home-network install works exactly as before. The icon is LineageBox's own: a three-generation
  tree on the site's blue, drawn for it.
- **Device links put the token in the address, not a cookie.** A Home Assistant dashboard shows a frame inside its own
  page, where browsers don't send another site's cookies (and over plain http can't be made to); old TV browsers keep
  cookies badly. The token is taken off the address before anything else sees it, so the server's log and the
  routes see `/p/<sha>`, not the token. It is 128 bits, as hard to guess as a session's and short enough to type.
- **A device link is not an account.** Rather than a fake account with a narrow view, a link has its own Viewer (no
  account, no people, only its frame's photos) and its own allowlist of routes: a route added later is closed to it
  unless someone adds it on purpose. Its photos are those of its one view, worked out as the account it was made for
  sees them, so a link never shows more than that account could.
- **The frame's script is old-fashioned on purpose** (ES5, XMLHttpRequest, no promises of its own): the browsers of a
  tablet from 2015 or a TV stop at the first line they can't read. It keeps its last list while the server is away,
  passes over pictures that don't load, and loads the page again only once the server is seen to answer.
- **The outbox: events written once, checked when shown.** The code that knows writes an event (a share, a question,
  a comment, new photos once their faces were looked for, an alert). Who may see one is worked out each time it is
  shown, from what they may see then, because that changes: a share is stopped, a photo hidden, a face named. One
  event of new photos names up to 2,000 of them, and each member is told of those they see. No email (the owner's
  choice for now: nothing leaves the house), but a digest or Web Push would read the same events with the same check,
  each with its own mark of what it sent.
- **Alerts are told once.** A service's problem again only after a day, a disk still nearly full after a week, a
  version and each damaged batch once; Ollama only after three days away, since the PC it often runs on sleeps at
  night.

## Videos

- **ffmpeg** (the CLI, not PyAV), because the copies need the CLI anyway.
- **Frames** by length: one every ~10 s for the first 20 (a phone's clip), then one a minute, 120 at most. In the
  library LineageBox was developed on (2,241 videos, 32 hours, on 2026-11-02), 13 videos ran over 10 minutes and the
  longest 44, which the old cap of 20 frames covered at one every 2.2 minutes; it now gets 61, and a 2-hour tape 120.
  Blank and near-identical frames are dropped, and the clearest becomes the preview. A frame takes 0.3–0.7 s on the
  ARM board. On a file with no index (MPEG-TS from some recorders) a frame may only be found by decoding from the
  start, each later one taking longer: once one takes too long (2 minutes to seek to it, or 15 to decode up to it),
  the video keeps the frames it has rather than spending hours on the rest. Faces are found on every frame, and each person counts once per video. Four frames go to the AI in one
  request (about 3,200 prompt tokens). Videos read before keep the faces found then; their extra frames get faces
  with **Look for faces again** (the faces stage never looks at a video twice, or it would find the same faces again).
- **Chapters** for videos over 10 minutes (a digitised tape is many recordings end to end): ffmpeg decodes only the
  keyframes, 64 pixels wide, and gives each a scene score against the one before and whether it is nearly black.
  Decoding only keyframes was 6× faster than every frame on a 720p H.264 test video (1.9 s against 11.6 s for 10
  minutes, one thread). A chapter starts at the first picture after a blank (the gap between two recordings), else at
  the biggest changes of picture (score 0.4 or more; unrelated test pictures scored 0.52–0.82, one scene 10 s later
  0.02–0.26), at least 2 minutes apart and 40 at most. These are rules, not measured on real tapes: that library has
  none yet. Sparse keyframes (an encoder that puts one every 10 s) make the score compare pictures further apart, but
  encoders also put a keyframe at most cuts.
- **Dates:** a camcorder .MTS file's date comes from the H.264 stream, else the container's (UTC), else the file name.
- **Playable copies:** 95% of videos were H.264, but browsers can't play about a quarter of them as they are (AVCHD
  .MTS, MOV with PCM sound, 3GP, AVI, WMV, HEVC). These get a 720p copy next to the original, marked in its
  comment. A copy is never a catalog entry, and Cleanup moves it with its video.
- **Transcripts:** whisper.cpp large-v3-turbo with Silero VAD first, 26–29× real time on the GPU. Only stretches
  with speech are transcribed. Wind, music and the usual Whisper inventions ("Thank you.") are filtered out. The
  language is Whisper's own guess by default (`auto`), stretch by stretch, so a family that speaks two languages gets
  both; a short or noisy stretch can be guessed wrong, which is why it can still be set. A corrected line is kept by
  the second it starts at (`transcript.edits`): filtering again under better rules keeps the same segments, so the
  corrections still land on their lines.
- **Sound recordings** are `image.media = 'audio'`, one more kind of item rather than a table of their own, so
  folders, search, dates, places, people added by hand, copies and Cleanup all work for them as they are. The faces
  and describing stages skip them (their stage bits are set at once; describing leaves `audio` out), and so do
  tours. Their thumbnail is ffmpeg's picture of the sound wave. A file tagged with an artist, an album and a track
  number is taken for a song and left out: a share often holds a music library, which would otherwise fill the
  library with "recordings" and send hours of lyrics to Whisper. A family's digitised tape tagged that way is rare,
  and the rule is plain enough to get round.
- **Recorded stories** are kept as files in the data folder, not as BLOBs in the catalog: the catalog is copied whole
  every night, and a few MB of sound each would make every copy that much bigger. The nightly backup copies the files
  beside the catalog's copy, once each. Whisper's words (`heard`) and a person's (`words`) are separate columns, so
  Whisper hearing a story never stands in the way of undoing its recording. Taking one away hides it for a month
  (`removed_at`) rather than deleting it, so Undo can bring the sound back. Recording in the page (MediaRecorder)
  works only on a secure address, so a file box (`accept="audio/*" capture`, which opens a phone's recorder) is
  always offered too.
- **A tour saved as a video** is made by the worker, never in a request: 1280 × 720 at 15 frames a second, a 1 s
  crossfade, at most 60 s of each video in it, at most 300 slides and 40 minutes. The words (caption, date, names, the
  title card) are drawn by Pillow and the frames piped to ffmpeg as raw pictures, rather than drawn by ffmpeg's
  drawtext: that needs a font set up for fontconfig and escaping of every caption, and differs between builds. The
  font is DejaVu Sans (in the Docker image, so accented names show), else the system's own. A 13-second video took
  1 s on the development PC. The slides are kept as the one who asked saw them, so a member's video holds only what
  they may see without anything being worked out again in the worker.

## Albums, favourites and the family book

- **Albums are not tours with a fixed list.** A tour picks again every time it opens; an album is what someone put in
  it, in their order. Both are shared the same way, and a shared album passes on only the photos its maker may pass on
  (kept in `album_passed`, as `tour_photo` for a tour), so nobody shares on what was shared with them.
- **An album's caption is its own**, beside the photo's family caption: the same photo can say "the bride's
  grandmother" in the wedding album and "Grandma at 80" everywhere else. Where an album has none, the photo's own
  shows, so nothing is written twice.
- **A heart outweighs what else a tour weighs** (100, where the three clearest faces and a sharp photo give about
  60-90): among similar shots, or for a year's place, a favourite is picked first; between two favourites the usual
  scores decide. On a member's connection only their own hearts count; on an admin's, everyone's, so a photo several
  people love comes first in the tours admins make for the family.
- **Dates on slides say what is known**: a date typed as a span shows as typed ("Summer 1987"), a folder's year as
  the year, a folder's month as the month, and only a camera's or a file's date as a day. A title card's span follows
  the same rule ("1987 to 2024", "June to August 2024").
- **The family book is a web page printed by the browser**, not a PDF made on the server: no PDF library to ship, the
  same fonts and pictures as the site, and Save as PDF in every browser. Print styles (`book.css`) start each person
  on a new page and keep photos whole. A branch's book holds at most 80 people, the nearest generations first, and 24
  photos each, so it prints in minutes, not hours.

## Rotation, names and places written into files

When someone confirms a photo's rotation, LineageBox also writes it into the file where the format allows: its
orientation tag, without re-compressing, with ExifTool, for JPEG, TIFF and NEF. The photo's faces, names and tags carry
over to the changed file. A rotation is suggested only when the faces in a photo agree on it, never when some faces are
upright (a child hanging upside down among people standing).

What the family gives a photo goes to other programs as XMP, in a **sidecar** beside it, off until an admin turns it
on, and always in downloads and the export (the owner's decision of 2026-11-07). Only what people made is written:
names given to faces by hand (as MWG regions, which digiKam, Lightroom, Picasa 3.9 and ExifTool read, and as IPTC's
PersonInImage and keywords), people added by hand, the family's caption (`dc:description`), tags given by hand, a
date set by hand (`exif:DateTimeOriginal`, and `photoshop:DateCreated` as exactly as it is known: a year, a month or
a day) and a place put on the map by hand. Recognition's unconfirmed guesses and the AI's words stay in the catalog:
another program would take them as the family's word.

- **The name is `IMG_1234.JPG.xmp`,** the photo's whole name plus `.xmp`, as digiKam and darktable write them. The
  other convention, `IMG_1234.xmp` (Lightroom's and Capture One's, for raw files), gives a raw file and the JPEG the
  camera saved with it, or a Live Photo's still and clip, one sidecar between them. digiKam reads both names; Lightroom
  reads `IMG_1234.xmp` beside a raw file and a JPEG's own XMP, which an admin's **Write into the file** gives it.
- **Another program's sidecar is never written over** (darktable keeps a photo's edits in one). LineageBox marks its
  own (`lineagebox:Written`) and records what it wrote, so each is written again only when what it says changes.
  darktable and digiKam read a sidecar of the photo's name and write their own work into it, keeping what was there,
  the mark too: one written since by another XMP library (Exiv2 in both, Adobe's, ExifTool), which puts its own name
  in `x:xmptk`, is theirs from then on (`sidecars.as_written`), never written over or deleted. The packet's maker,
  not the catalog's record, says so, as that record can be behind (the service stopped between writing and recording).
- **Boxes** are fractions of the picture as it is shown, turned upright, with the size it had (`AppliedToDimensions`).
  Apps disagree on whether a box is meant before or after the EXIF orientation, so reading other apps' boxes tries
  both and takes the one that covers more of the faces found.
- **Writing into a file** (an admin, on the photo's page) adds the keywords and names to those the file has, never
  twice, replaces its caption and face regions, and also puts a date or place by hand in the EXIF, where phone
  galleries and Windows look. The values go through an argument file in UTF-8, so names with accents reach ExifTool as
  they are on every system. The caption, regions, date and place are copied from a sidecar made for the run
  (`-tagsfromfile` with those tags named): ExifTool given `-tagsfromfile` with no tags after it copies every tag, and
  the sidecar's lists of keywords and people would replace the file's. The changed file is flushed to the disk
  (`fsync`) before the original makes way, as the original is deleted once the catalog has the change.
- **In Docker the worker only reads the photos,** so the copies service, which already writes 720p copies beside the
  videos, writes the sidecars too; the worker, from source, writes them itself.

**Places.** A place put on the map by hand, for a photo (anyone who sees it) or a folder (an admin), is "confirmed
GPS": it wins over the camera's GPS, which phones get wrong indoors, and goes into the sidecar, and into the file only
when an admin asks. A place read from a folder's name, even confirmed on the map, is a town's or landmark's centre,
not where each photo was taken, so it stays in the catalog. A folder's place by hand is only for its photos without
GPS of their own, as on the map: it never goes into the sidecar or file of a photo whose camera gave a position, nor
past a nearer folder whose name gives a place (`HandPlaces.of`).

**Town names without going online.** Photos with GPS are named after the nearest town from GeoNames' towns and
cities of over 5,000 people (about 70,000, CC BY 4.0), trimmed to a name, a position to about 100 m, the region,
country and population: 1 MB compressed, 0.2 s to read when first needed. The list of towns over 15,000 people is half
the size but leaves country areas 30 km or more from any name; the list of those over 1,000 is two and a half times the
size. The nearest town wins, not the biggest near it: a village a kilometre away says more than a city twenty
kilometres off. Under 10 km a photo is "in" the town, under 50 km "near" it, beyond that "60 km from" it.

**Other apps' names** come in below everything set here, once per photo, from the start of the file only (Pillow reads
a file's headers, not its picture), so a library of tens of thousands is read in a few passes. A name's face is the
one its box overlaps by a fifth or more of their combined area, or whose centre each box holds: apps draw boxes from
the hairline to the chin or just around the eyes, so the same face's boxes overlap by about a third.

## Family

- Only facts are entered: parents, children, siblings, partners (current or former), sex, and other relationships.
  Everything else is worked out, relative to "me".
- Step-family comes only from current partners. After a divorce, people are described by the way to them instead
  ("ex-husband's wife's daughter").
- Names aren't unique: two people can share a name, and a typed name is looked up among the people the viewer can
  see.
- How a child belongs to a parent is part of the link (birth, adopted, foster), as GEDCOM's FAMC.PEDI has it, not a
  relationship of its own. Adoption makes family as birth does: kin words and the tree go up adoptive links, so an
  adopted son is among "sons" in search and his children are grandchildren; only the link itself says "adoptive
  mother" or "adopted son", and parents by birth of someone adopted are "birth mother". A foster family is family
  through that link only: "foster brother", "foster grandmother", and no in-laws or step-family through it. Two birth
  parents and two adoptive ones at most; foster parents come and go, so they have no limit.
- The life story is a column of its own (`person.biography`), beside `person.notes`: the notes hold what readings
  and other trees wrote there (sources, mostly), which the family shouldn't have to write around. GEDCOM has no
  biography: it goes out as the person's first NOTE, marked in 7.0 with the extension `_BIO` (declared in HEAD.SCHMA)
  so it comes back as the story; other programs show it as a note, and 5.5.1 (no declared extensions; Gramps warns
  of unknown tags) has no mark, so it comes back as notes.
- GEDCOM 7 requires an attribute's value as its payload ("1 OCCU Teacher"); exports before 2026-11-07 wrote it as
  TYPE, which imports still read.
  An attribute with no value is written with its place, else its name ("Lived in"), and those are read back as no
  value, so a round trip doesn't make a second event.
- 5.5.1 cuts long text into CONC lines of 200 characters, never beside a space (a line's last space is lost: other
  programs strip it, and so does the export) and doubling each @ within its own line (cut between the two of a
  doubled @, it came back as two; both were found on 2026-11-07, checking long notes). Files are read as UTF-8, as
  UTF-16 when they start with its byte order mark (5.5.1's "UNICODE"), else as Windows' ANSI; ANSEL, the 5.5 character
  set, isn't decoded, so its accented letters come out wrong.
- A tree's date in a year no calendar has (0000, or 9999 that some programs write for "not known") is kept as written
  but given no sort date: ages, the life by age and Ages to check work from the sort date, and there is no day in
  those years to count from (the person's page failed on them before 2026-11-07).
- A family tree's media are matched to the library by file name only when one photo has that name; a camera's own
  name (IMG_1234, DSC01234, Scan_0001) must match its folder too, since every camera makes the same names. A
  LineageBox export's media/<SHA-256>.jpg is matched by the SHA-256. A photo found has the person added to it, as
  the other program said; an import isn't on Recent changes, so its report counts them.
- An event's coordinates from a family tree (PLAC.MAP) replace only an automatic lookup, never a place put on the map
  by hand or confirmed there. The export writes coordinates only for those (by hand, confirmed, or from a tree), not
  for a lookup's guess.
- The tree's checks against photos list someone named in photos from before their birth (a date read from a folder's
  or file's name compared by its year only, as it may say no more) or from more than a year after the year they died:
  a funeral, a memorial, the photos looked at the next year are expected, and after that they are most often scans
  dated the day they were made. Each problem's key holds how many photos it is about, so one marked fine comes back
  when more such photos are named.
