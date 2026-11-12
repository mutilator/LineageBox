# Changelog

What's new in each release of LineageBox, newest first. A release's section here is also its notes on GitHub: CI
copies it there when the version is tagged ([releases](docs/development.md#releases)).

A **catalog update** adds to the catalog's tables. LineageBox copies the catalog before it updates it, and earlier
versions can't open the updated one.

## 1.6.1 (2026-11-11)

**Catalog update** (migration 034).

### Updated

- **The menu is at the side**, with every page in sight in sections: Library (Folders, Timeline, Map, Tags),
  Collections (Favourites, Albums, Tours), People, Tidy up (Name faces, Cleanup) and, for admins, Admin. **Add
  photos** is a button above them. It can shrink to its icons, and on a phone ☰ opens it. Nothing is under More any
  more.
- Colours, Photo tiles and Language moved to the round button at the top right.
- **Photo tiles** now apply to Home's photos by the day too: squares aimed at the faces, or whole photos, in three
  sizes.

### Fixed

- **Faces missed** and **No faces found** listed thousands of photos on the AI's wild guesses at how many people were
  in them (up to 5,000,000,000,000,000). The AI now counts the people in those photos again, on its own, and says how
  sure it is; a photo is listed only when it is at least 80% sure. The count and how sure show on the lists and on
  the photo's page, and a description's own count past belief isn't shown.

## 1.6.0 (2026-11-11)

### New

- **Through the years:** a person's face a year at a time, lined up on the eyes, from their page or their Home. Play
  it, step through it or pick a year, with faces from every photo or from portraits only.
- **Photo tiles** (Settings → Colours): squares cropped around the faces in them, so heads stay in sight, or whole
  photos in rows, in three sizes. On a phone, pinch to make them larger or smaller.
- **A closer look at every photo:** the worker looks at each photo again more closely, and turned each way where the
  AI sees people but no face was found, to find the small and sideways faces the first look missed. It looks again
  every 6 months, every year or only when asked (Settings → Faces).
- **Status → Work in progress:** a bar for each kind of work, with how many are done and the time left.
- JFIF photos (`.jfif`, `.jpe`) are read.

### Updated

- Uploads, sign-in, passkeys and the worker were hardened after a security review. A video or sound file is read only
  when it really is one of the usual kinds.
- Recent changes, and their undo, go back 90 days. Older changes are forgotten.

### Fixed

- Turning a camera raw photo gave an error.
- A video that ffmpeg took too long to read is tried again later.

## 1.5.1 (2026-11-09)

### New

- **Family buttons on Home:** a row of buttons opens the memories and newest photos of one person, you first and then
  each of your closest family. Admins who are in the tree have them too.

### Fixed

- **Read into the family tree** keeps names as the text spells them instead of "correcting" them, and no longer makes
  middle names into people of their own.
- Reading a text into the tree gave an error for someone whose birth name has no separate surname.

## 1.5.0 (2026-11-08)

### New

- **Demo mode**, for a public demo of LineageBox: one-click sign-in as a family member or an admin, small upload
  limits, the server's settings locked, and a reset to the saved demo on a schedule ([demo](docs/demo.md)). Try it
  at https://linbox.g-tech.dev.
- **Cleanup → No faces found:** photos where no face was found at all. Tick those to look at again more closely, or
  those with no faces to find.
- **Suggested dates → Looks too young:** photos where a grown-up's face looks like their childhood, with a date
  suggested from their childhood photos.

### Updated

- A photo frame shows the next photo at a tap, a click or →.
- Status says when Ollama's model was stopped for running out of memory, and the docs say how much each model needs.

## 1.4.0 (2026-11-08)

**Catalog update** (migrations 022 to 033).

### New

- **Undo for everyone:** every change shows Undo at once, and stays on Cleanup → Recent changes.
- **Captions and comments** on photos, shown above the AI's description and found by search.
- **Search by meaning** ("kids sledding" finds children on a snowy hill), **More like this** on each photo, and search
  filters (people together, years, a place, photos or videos, faces to name).
- **Name faces:** one face at a time with Yes, No and Skip, easy on a phone.
- **A new photo viewer** with zoom, swipe and face boxes.
- **iPhone photos (HEIC), most cameras' raw files, TIFF and Live Photos.** Cassettes and voice memos are catalogued
  as sound recordings, played and transcribed; long videos list their chapters.
- **Recorded stories** about a photo, a person or a life event, written down by Whisper.
- **Life stories** on each person's page, by age, with **sources** (a book, a website, a census page) and **adoption
  and fostering**.
- **Albums, favourites, slideshow videos** to save and send, and **the family book** to print: a person's stories,
  life events, family, documents and best photos year by year.
- **New for you:** new photos of your family and faces someone asked about; for admins, also damaged files, a disk
  nearly full, a service with a problem and a new version.
- **Photo frames:** a link that shows memories, a tour or an album full screen, without signing in.
- **Phone app:** install LineageBox on a phone's home screen, and share photos from the gallery straight to Upload.
- **Backups and export:** a nightly copy of the catalog for your backup program, and **Download everything** in one
  file that other programs read.
- **Imports from other apps:** Google Photos (Takeout) comes in named, with faces, keywords, captions and dates. The
  family's names, captions and places can go out into XMP files beside the photos.
- **Places without going online:** photos with GPS are named after the nearest town, and anyone can put a photo on
  the map by hand.
- **Cleanup → Suggested dates** for scans, from the photos beside them and who is in them, and **Camera clocks**,
  which puts right a camera whose clock was wrong.
- **Getting started** on the first page, light and dark themes, and the site can be translated.

### Updated

- Tags: rename one across the library, merge two, or take one off every photo for good. **Describe again** asks the
  AI for a new description.
- The events and places read from a folder's name can be corrected on that folder's page.
- GEDCOM import and export carry sources, adoptions and places, and imported people's photos are found by name.
- Big folders and years load as you scroll. Tick photos on any list to tag, date or file them together.
- A person's portrait can be picked from any of their faces, and a photo keeps its place in the list after a change.

### Fixed

- The map hid the menus.
- Labels on Status ran out of their panel.

## 1.3.0 (2026-11-06)

**Catalog update** (migrations 020 and 021).

### New

- **Read into the family tree:** give the local AI an obituary, notes or a photo of a document. It lists the people it
  names and what it says of them, matched to your tree, as changes you tick one by one.
- **Passkeys:** relatives sign in with their phone's or computer's own lock, from a link an admin sends. Family
  accounts can use passkeys alone, without Google.
- **The family tree page:** the whole line down from any ancestor, to move around, zoom and save as one large picture.
- **Remove a person** added by mistake (admins, at the bottom of their page).
- **Events on the Timeline:** each year lists its biggest days of photos, named by what they were (a wedding, a
  birthday).

### Updated

- A family member's Home puts their own family first.
- Accounts are listed folded, each with its passkeys.
- The storage meter counts removed files still waiting in Removed folders.

## 1.2.0 (2026-11-05)

### New

- **Memories on Home:** this week in earlier years, a birthday coming up, someone through the years, and the newest
  photos by the day they were taken. **Keep as a tour** saves one.
- **Hover preview:** point at a photo or a face for a moment and the whole photo shows larger, with the face outlined
  (Settings → Browsing).
- **Storage meter** for admins: how full the disk is and what fills it.

### Updated

- Pages, searches and file handling are faster on big libraries. **Status → Slow pages** lists the slowest.
- **Fix**, next to a wrong name on a photo, takes the place of "Not (name)": type who the face really is, or pick
  don't know who.

## 1.1.0 (2026-11-04)

**Catalog update** (migration 019).

### New

- **Sharing:** share a folder of your uploads, one photo or a tour with one relative or with every family account.
  They see those photos even from outside their own branch, and a shared folder keeps sharing what is added to it.
- **Folders:** your uploads, what is shared with you and what you share. Admins can share any folder or tour, and
  move a whole folder to Removed.

### Updated

- Settings has a menu at the side that shows one part at a time.

## 1.0.0 (2026-11-04)

The first release.

### New

- **A family tree built from facts:** enter parents, partners and siblings, and every other relationship is worked
  out from anyone's point of view. Life events and documents, GEDCOM 7.0 and 5.5.1, and checks for mistakes.
- **Family accounts:** relatives sign in with Google and see their own branch and the photos its people are in. They
  name faces, fix dates, add relatives and upload photos; admins can undo anything a member changed.
- **Face recognition:** name a person's faces once, and LineageBox finds them in the rest of the library.
- **Search in plain words** across descriptions by a local AI (Ollama), tags, places, dates and what is said in videos
  (whisper.cpp).
- **Dates** from cameras, folder names and file names, and old scans dated as loosely as you know them.
- **Timeline, map and tours.**
- **Cleanup:** duplicates, burst shots, blank and blurry photos, photos on their side and damaged files, for you to
  pick from. Nothing is removed until you pick it, and removed files can be restored.
- **Every video plays in the browser**, with 720p copies of the formats browsers can't play.
- Docker images for x86-64 and 64-bit ARM, and admins are told when an update is out.
