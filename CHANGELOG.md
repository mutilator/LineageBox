# Changelog

What's new in each release of LineageBox, newest first. A release's section here is also its notes on GitHub: CI
copies it there when the version is tagged ([releases](docs/development.md#releases)).

A **catalog update** adds to the catalog's tables. LineageBox copies the catalog before it updates it, and earlier
versions can't open the updated one.

## 1.10.0 (2026-11-17)

**Catalog update** (migration 039).

### New

- **A couple see what each other adds.** A husband and wife — or any two people marked as current partners in the
  family tree, married or not — now see each other's albums, tours, journeys and uploads without either of them
  having to share anything. Each finds the other's under **Shared with you** on **Folders**, among their own albums
  and tours, and on Home. It follows the family tree: it starts when a couple are entered as partners and stops when
  they are not current ones any more, so there is nothing to stop sharing. What someone else shared with one of them
  is not passed on, and a photo an admin hid is never passed on. Admins can turn it off for the whole library under
  **Settings → Family accounts → Couples**. An album or tour used to be kept as the admins' own, without saying
  which admin made it; from now on it is recorded as its maker's, and where a library has a single admin the ones
  already in it become that admin's when the catalog updates. With several admins there is no telling who made
  which, so those stay the admins' own and are shared by hand as before.

- **A trip on the map: an album's journey.** An album is already a list of photos in the order you want them, and a
  trip is just that — New York, Colorado, Yellowstone, Mount Rushmore, the Mall of America, home again. Put a
  trip's photos in an album, in the order you travelled, and **🗺 See the journey** at the top of the album
  draws it on a map of its own: each place in turn, numbered, with the way from one to the next, arrows pointing
  which way it went, and dots running along the line. **Follow the journey** sends a light along it, waiting at each
  place and lighting that stop up in the list below. Photos taken near one another, one after another in the album,
  are one stop; going back at the end to the place you set out from is a stop of its own. **Places this close are one
  stop** (5, 25 or 100 km) says how near counts as the same place. Albums that draw a journey are marked as such on
  **Albums**. Nothing is kept in the catalog: the journey is read from the album every time it is shown, so putting
  its photos in another order, or on the map, changes it at once. The [user guide](docs/user-guide.md#journeys) says
  how to make one.
- **Photos put on the map many at a time.** Most photos have no GPS, and placing them one by one was the only way.
  Tick photos on any page of photos — a folder, a year, a search, a tag, a person — and **Put N on the map**
  in the bar at the foot opens the map and asks for one place for all of them; **Select all** first does a whole
  folder or a whole search. An album of your own has **Put them on the map** at the top for all of its photos. One
  **Undo** takes the lot back. A place given this way wins over the photos' own GPS, so it also puts a batch right.
- **Journeys on the Map page.** The map's box at the top right has a **Journeys** tick of its own, on to begin with:
  every journey of the albums you see is drawn on the main map, each leg with an arrow showing which way it went.
  They draw themselves when the map opens, all at once and the way a travel film does — a place, then a dashed
  arc bowing out to the next, a place again — and then stay as they are.
  Click a line for its stops, how far it went and a way into it. Picking a year leaves the journeys with a photo
  from that year.
- **Put in date order**, in your own album, puts its photos in the order they were taken, the earliest first: the
  order a trip was travelled, without dragging a few hundred photos. Photos with no date at all stay at the end, and
  **Undo** in the message puts the order back.

### Fixed

- **A hospital bill counts as a health record.** Cleanup → Private papers knew medical records, lab results and
  insurance cards, but not a bill or statement from a hospital, which says as much about someone's health: an
  itemized 1983 hospital statement sat in plain sight. It now also knows hospital bills, statements and accounts,
  medical bills and expenses, a doctor's bill and a discharge summary.

## 1.9.0 (2026-11-17)

### New

- **Everyone named is on the People page, photos or not.** Someone no photo in the library shows — a person in the
  family tree whose photos aren't here, someone a reading or an import added, a name typed by mistake — was left off
  the People page altogether, so there was no way to open them, change their name, place them in the family or take
  them out again. They are now at the end of the list, and **No photos** beside the groups picks them out.
- **Not placed yet tidies up as well.** The section at the foot of the Family page now lists people with no photos
  too, first, since nothing else in the library shows them. For an admin, a person with no photos who is in no part
  of the family tree and no group has a **×** on their card that takes them out of the catalog, with **Undo** in the
  message that follows.

### Fixed

- **A change to the ticked photos now says plainly that it was made.** Tagging the ticked photos, adding someone to
  them, dating them or putting them in an album left the page exactly as it was, with what you typed still in the
  box, and said what it had done in small grey words in the bar at the foot: it looked as though nothing had
  happened. The message now comes up over the bar with a ✓, and **Undo** beside it, the box you typed in is emptied,
  and the photos the change was made to light up for a moment in the list.
- **Files deleted from a Removed folder by hand are noticed.** The scan never looks inside the Removed folders, so
  photos deleted there over the share (rather than with **Empty the Removed folder**) went on counting towards
  **Removed files** in the storage on Home and Status — on one library, 341 GB of room that had been given back
  weeks before — and were still offered to be put back, although the files were gone. The worker now checks after
  each scan, forgets those files, and deletes the previews and thumbnails it had kept for them.

- **A person's Home keeps the photos whose date isn't certain.** It shows photos by the day they were taken, and a
  date taken from when the file was copied, or from a folder's or file's name, isn't a day to put a photo under, so
  those were left out of it altogether, as were photos with no date at all: a page could show 3 photos of someone
  who is in 32, and someone whose photos are all film scans had an empty one. They are now under the days, as
  **More photos of …**, newest first by whatever date they have, each with the ≈ mark that says the date is a guess.
  (Cleanup → Suggested dates can put real dates on them.)

## 1.8.0 (2026-11-16)

**Catalog update** (migrations 036 to 038).

### New

- **Settings → Reorganize folders:** moves every photo, video and recording in the watched folders into a folder for
  its year and month (`2016/2016-07`), by the date LineageBox has for it, keeping its name; ticked, **Folders by
  kind** puts `Photos`, `Scans`, `Videos` and `Recordings` at the top, each with its years and months. **See where
  everything goes** shows the new layout and moves nothing. A photo's own files go with it, everything else goes to
  `Other files`, and empty folders and other programs' leftovers (`Thumbs.db`, `.DS_Store`) can go with them. Names,
  tags, dates, captions, what the old folder names said, and hiding or sharing a folder all stay as they were, and
  **Put back** undoes the latest run.
- **Boyfriends and girlfriends** in the family tree: a couple can now be a husband and wife, partners who share a
  life without marrying, or a boyfriend and girlfriend, each of them current or former ("ex-wife",
  "ex-girlfriend"). Say which when you add them on a person's page, and the page says it beside them. Many families
  have people who never married, some with children together: their children belong to both parents and are family
  on both sides, as any other couple's are. Nobody marries in, so there are no in-laws and no step-family from going
  out together: a girlfriend's mother reads "girlfriend's mother", a sister's boyfriend "sister's boyfriend". Anyone
  entered as a boyfriend or girlfriend before, when it could only be typed as another relationship, becomes such a
  couple when the catalog updates.

### Updated

- **Former partners are family on Home:** the row of buttons holds the partners someone once had, last, so their
  photos and the years together are a click away like anyone else's. The last button, **Hide former partners**,
  leaves them out, and this browser remembers that choice.
- **A family member sees their own former partner.** Until now someone's former wife or girlfriend was part of their
  family only when they had children together, so an ex-wife of many years, in hundreds of photos, was missing from
  People and her photos were not shown. Other people's former partners are unchanged: those still need a child in
  the family.
- **American spelling throughout**, in what the site says and in the code: Favorites (was Favourites), colors,     recognized, catalog, license, center, gray, labeled, neighbor. `/favourites` still opens Favorites, and the  heart's old address still works, so bookmarks, shared links and a phone that cached the old page keep working.  An export's `photos.json` now writes `favorites` and `favorite_of`.
- **WebM videos** are read like any other, and play without a 720p copy when the browser can show them (VP8 or VP9
  with Opus or Vorbis sound).

## 1.7.2 (2026-11-14)

### Updated

- **Albums** can be put in order by dragging on a phone or tablet too: rest a finger on a photo a moment, then drag
  it where it goes (a quick swipe still scrolls). With a mouse or a finger, holding a photo near the top or bottom of
  the window scrolls the page, and Esc puts it back. ↑ and ↓ still work.
- The **[wiki](https://github.com/mutilator/LineageBox/wiki)** is brought up to date with each release, and has this
  changelog as a page of its own.

## 1.7.1 (2026-11-12)

### Updated

- **Name faces** asks about the likelier person. A recognized face that, after more faces were named, looks more
  like someone else (often a brother or sister) came first as "Is this … ? About 0% sure", asking about its less
  likely name. It now asks whether it is the person it looks like, says what it was recognized as, and answers
  **Yes**, **No, it's …** (keeps the name) or **Neither**. People born after a photo no longer make a recognized
  face in it look less sure.

## 1.7.0 (2026-11-12)

**Catalog update** (migration 035).

### New

- **How sure a face's name is:** People → Name faces says how sure it is of each face it recognized, or thinks looks
  like someone (at most 98%: even of the surest, about 1 in 50 is someone else), and asks about the least sure first.
  **Yes to the surest at once** names every face of a step at least 98%, 95% or 90% sure, with Undo. Check faces
  shows it under each face.
- **Cleanup → Private papers**, first on Cleanup and by itself: photos that look like driver's licenses,
  Social Security cards, bank cards, checks, tax forms, insurance cards, health records, or birth and marriage
  certificates of people living, found in the AI's descriptions. **Hide from family members** hides the ticked ones
  from every family member (whoever uploaded one still sees it); **Fine to show** stops listing one. Admins see the
  whole library's, family members the photos they uploaded. Admins are told on New for you when new ones are found.

### Updated

- **Groups** take in a whole family: putting a family friend in Family friends (or any group) asks whether their
  wife, children and the rest of their family go in it too, listing those not in it yet to untick. Your own family is
  never on the list.
- **Status** is more compact: the AI descriptions panel is gone, as its row under Work in progress says as much. The
  AI's work and transcripts now say what they wait for (the describing hours and when they start, Ollama or Whisper
  not answering, a model that can't look at pictures), a line above the rows says when they run, and Downloads shows
  only while something downloads.

## 1.6.1 (2026-11-11)

**Catalog update** (migration 034).

### Updated

- **The menu is at the side**, with every page in sight in sections: Library (Folders, Timeline, Map, Tags),
  Collections (Favorites, Albums, Tours), People, Tidy up (Name faces, Cleanup) and, for admins, Admin. **Add
  photos** is a button above them. It can shrink to its icons, and on a phone ☰ opens it. Nothing is under More any
  more.
- Colors, Photo tiles and Language moved to the round button at the top right.
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
- **Photo tiles** (Settings → Colors): squares cropped around the faces in them, so heads stay in sight, or whole
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
- **iPhone photos (HEIC), most cameras' raw files, TIFF and Live Photos.** Cassettes and voice memos are cataloged
  as sound recordings, played and transcribed; long videos list their chapters.
- **Recorded stories** about a photo, a person or a life event, written down by Whisper.
- **Life stories** on each person's page, by age, with **sources** (a book, a website, a census page) and **adoption
  and fostering**.
- **Albums, favorites, slideshow videos** to save and send, and **the family book** to print: a person's stories,
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
