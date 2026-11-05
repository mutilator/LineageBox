# User guide

What each part of LineageBox does, page by page. Every page also has its own help: press **?** (or the **?** at the top
right) for what the page does and its keyboard shortcuts.

## People and the family tree

### Naming faces

The **People** page lists groups of unnamed faces that look like one person, largest first. Type a name. When the
folders the photos are in suggest one, it is filled in already. Then press **Find more photos of these people**, which
names the faces elsewhere that clearly match.

- Every face in a group has an **×** for strangers mixed in. Naming the group then names only what is left.
- **Seen together** suggests who the unnamed faces in a photo are, using the people already named in it. Nobody is in
  a photo twice, so each face is matched to the closest person not yet named in that photo. Untick the faces that
  aren't them, then confirm. Unticked faces are never suggested or recognised as that person again.
- On a photo, **Fix** next to a wrong name lets you type who the face really is, or pick **don't know who** to take
  the name off so the face waits to be named. It is never suggested or recognised as the wrong person again. An
  unnamed face that looks like someone not yet in the photo offers their name to click.
- **Check faces** on a person's page lists their named faces, the least alike first, for spotting mistakes.

Names you give by hand always win: recognition never overwrites them.

### The family tree

Enter only facts on each person's page: parents, children, siblings, partners (current or former), sex, and other
relationships such as friends, teachers or pets. LineageBox works out the rest (grandparents, nieces, in-laws,
step-family) relative to the person marked **This is me**. Those words work in search, too.

- The **Family** page shows a family tree. Each person's page lists their whole family from their own point of view.
- **Life events** (births, weddings, moves, graduations) are on each person's page, with dates as exact as you know
  them ("about 1950").
- **Groups** hold people who aren't family: the kids' friends, family friends, a band from a concert. Groups are
  filters on the People page, and searchable.
- **Documents:** mark a photo of a certificate, an obituary or a letter as a document, and link it to the person or
  event it records.
- **Export** writes the tree as GEDCOM, for Ancestry, FamilySearch, MyHeritage, Gramps and the like: GEDCOM 7.0, or
  5.5.1 for older programs. You can include each person's portrait, cropped to their face (a GEDZIP), and leave out
  living people's details for sharing.
- **Bring in a family tree** reads a GEDCOM file and matches its people with yours, by ID, names, birth years and
  relatives, with the AI's opinion on the unsure ones. You check every match before anything is added, and nothing you
  entered is overwritten.

Keys on a person's page: `b` birth date, `e` add a life event, `1`–`9` change one, `r` add a relative.

### Sharing with the family

With [family accounts](family-accounts.md), relatives sign in with Google and see their own branch of the tree and the photos
its people are in. They name faces, add relatives and events, fix dates and upload their own photos. **Who is this?**
on a face asks everyone who can see the photo, and those faces lead their Home page. Admins see every change under
**Cleanup → Recent changes**, with undo.

Members also share: a folder of their uploads, one photo they uploaded, or a tour they made, with one relative's
account or with everyone who has one. Whoever it is shared with sees those photos, including those from outside their
own branch, and a shared folder keeps sharing what is added to it later. **Folders** lists a member's uploads, what is
shared with them and what they share; admins find everything anyone shares there too, and can share any folder or tour.

## Finding photos

### Search

The search box matches:

- the AI's descriptions and tags;
- people's names, including other names such as maiden names and nicknames;
- relationship words, worked out from the family tree: "nephews 2024", "grandma";
- places and events read from folder names, and the folder names themselves;
- dates: "2012", "june 2012", "summer";
- what is said in videos.

Examples: "goat", "emma zoo 2012", "christmas tree", "happy birthday". Results also list the people whose names match.

### Browsing

- **Home** starts with a row of memories: photos from this week in earlier years ("5 years ago"), tours worth
  making (a birthday coming up, someone through the years, a holiday) and a few of your own tours. They change from
  day to day; one opens like a tour, and **Keep as a tour** saves it. Below come faces someone asked about, what
  others shared with you, then the newest photos by the day they were taken (Yesterday, Tuesday, then by month),
  older photos added lately, and your own uploads. Admins also see how full the disk is.
- **Folders** shows the watched folders as they are on disk. A photo with copies in several folders shows in each.
  At the top of a folder, **Sharing and removing** shares it with family accounts or moves the whole folder to
  Removed (for a family member: a folder of their own uploads).
- **Timeline** shows every year with photos. Its bars show January to December, so busy and quiet months stand out.
- **Map** shows where photos were taken, from their GPS or from a place in their folder's name, along with family
  events that have a place. A slider picks the year.
- **Tags** lists every keyword the AI or you have given, with photo counts and a filter box. A tag's page shows its
  photos and the tags most often given with it.

A small **≈** on a thumbnail means its date came from the file name or the file's time rather than the camera.

Point at a photo or a face for a moment (half a second; **Settings → Browsing** changes it, or turns it off) and the
whole photo shows larger beside it, with the face outlined: a quick way to see who someone is on the Cleanup and
People pages without opening each photo. Once one shows, the next photo you point at shows at once. Clicking still
opens the photo, and ↗ still opens it in a new tab.

### A photo's page

- **← and →** step through the folder or year, and **Esc** goes back.
- The side panel lists every copy of the file, with download and **Copy network path** (paste it into File
  Explorer's address bar; set the paths under **Settings → On the network**).
- **× on a tag** takes it off for good: describing the photo again won't bring it back. **+ tag** adds one.
- **Someone else here?** adds a person whose face wasn't found.
- **Look for faces again** looks more closely, for small faces in group photos.
- **Remove photo** and **Remove this copy** move files to the Removed folder.

## Dates

LineageBox dates each photo from its camera date, else its folder's name, else its file name, else the file's time.
**Status → Where dates come from** lists the photos dated by file time, which may be in the wrong year.

To fix a wrong date (a scan dated by its folder, a camera with its clock wrong), set it under **Taken** on the photo's
page, as exactly as you know it:

- `1987-08-14`, `June 1987`, `summer 1987`;
- `the 1980s`, `about 1987`;
- `between summer 1987 and summer 1988`.

The photo is placed in the middle of that span, and searches find it under every year it may be from. **Use the
automatic date** goes back. To date many photos at once (a roll of film), use **Select photos**.

## Working on many photos at once

**Select photos**, on a folder, a tag, a search or a Status list, ticks photos so you can, all at once:

- tag them, or take a tag off;
- add someone to them;
- date them;
- move them to Removed. On a folder's page that takes only their files in that folder; copies elsewhere stay.

## Tours

**Tours** are slideshows picked from the library, such as "the kids, summer 2021" or "grandpa through the years".

- Kinds of tour are set up on **Settings → Photo tours**: who, when, which words, how many photos, photos or videos.
- Whatever a kind leaves open, a wizard asks for. Tours are saved with a name made from the answers.
- Photos are picked afresh each time a tour opens: one per group of similar shots, never blurry, with the clearest
  faces, from different days.
- **Suggested tours** come from what the library holds, such as days that come back year after year.
- With family accounts, a tour can be shared with a relative's account or with everyone who has one. They see the
  photos it picks as it shows them to whoever made it, and it keeps picking new ones for them.

## Cleanup

The **Cleanup** page lists what could go. Nothing is removed until you pick it, and removed files go to a Removed folder
next to their watched folder, keeping their folder path. **Removed** restores a whole cleanup step at once, or a single
photo from its page. **Empty the Removed folder** is the only thing in LineageBox that deletes files.

- **Duplicate copies:** folders holding byte-identical copies of the same photos. You remove the copies on one side,
  and the photo stays on the other. A photo's last copy is never removed this way.
- **Similar shots:** burst shots and copies of the same picture, with a filter for each kind:
  - **Smaller copies:** the same picture at a lower resolution.
  - **Same-size copies:** saved twice, re-saved or renamed.
  - **RAW + JPEG:** choose whether to keep the raw file or the JPEG.
  - **Copies without the camera date.**
  - **Burst shots**, and groups of **4 or more photos**.

  Copy kinds have a button to move the ticked photos from every group at once, as one step you can restore. The
  suggested keeper is the largest, then the one with the camera date, then the sharpest. Each photo shows its folder,
  because the small copy is often the one in a named album.
- **Damaged files:** files a disk spoilt: cut short, with a blank stretch where a block couldn't be read, or with
  different bytes than when they were catalogued. The worker reads every file whole once, a while each pass. Move them
  to Removed, or copy a good one from a backup over the damaged file.
- **Blank and blurry:** ranked by a sharpness score measured from the pixels, worst first. Black frames and lens caps
  come first, and soft keepers further down. The cut-off is under **Settings → Cleanup**.
- **Papers, screens, wiring and repairs:** photos without people whose description mentions things like "receipt",
  "computer screen" or "wiring". These lists grow as descriptions finish.
- **Turned photos:** photos that are probably on their side, judged from the faces in them, with the AI as a second
  opinion when it is on. Confirming writes the rotation into the file too, where the format allows (JPEG, TIFF and NEF,
  with ExifTool), without re-compressing it.
- **Family tree:** checks, with nothing to remove:
  - someone with only one parent entered;
  - siblings with no parents;
  - dates that can't be right (born before a parent, or after a parent died);
  - two names that may be one person ("Jenny" and "Jenny Smith");
  - parents with no sex set;
  - a child's parents not entered as partners.

  **Fine as it is** stops listing one that is right.
- **Keep** takes photos off the lists for good. Tick them and press **Keep**, press **Keep all** on a group of similar
  shots, or **Keep both** on a pair of folders. **Kept** lists them all and can put any back.

Any search result has **Select photos to remove**.

## Status

- what the worker is doing now, and its recent activity;
- how far the descriptions are, with the pace and the time left;
- what is waiting at each stage;
- downloads (face models, Ollama models, Whisper models);
- red notes when something needs attention, such as Ollama not answering or a model that can't look at pictures;
- **Storage**: how full each disk is that holds the watched folders or LineageBox's data folder, and how much of it
  is photos and videos, LineageBox's previews and catalog (measured once a day), and other files. It turns red when
  less than a tenth, or less than 20 GB, is free.

It refreshes itself every 10 seconds. The numbers link to their photos (all with GPS, all dated by file time, each
format), and pointing at a line says what it counts. **Check now** starts a worker pass at once.

## Settings

Changes here apply without a restart. They are kept in the catalog, so the web UI and the worker both see them, and
they win over `.env` and config files.

The menu at the side shows one part at a time (on a phone, it is above the settings):

- **Folders:**
  - **Watched folders:** browse the photos and press **Watch**, or paste a network path. **Stop watching** hides a
    folder's photos but keeps their descriptions and names in case it comes back.
  - **Scanning:** how many photos are read at once, and folder names to skip. A slow disk or share does better with
    fewer.
  - **On the network:** the network paths for **Copy network path**.
  - **Set in the config file:** the photo folders, the catalog folder and the network allow list. They are set only in
    the config, so a mistake on the page can't lock anyone out.
- **Schedule:** how often to look for new photos, and when the AI may describe: any time, certain hours and days, or
  paused.
- **AI, speech and faces:** **AI (Ollama)** and **Speech in videos (Whisper)**: on or off, the address, the model, how
  many at a time, and a connection check. **Faces:** on or off.
- **Map:** looking up place names with OpenStreetMap's Nominatim. It is off until you turn it on, because it sends the
  place names (and nothing else) to nominatim.openstreetmap.org. It looks up a few a minute and each name only once, as
  Nominatim's [usage policy](https://operations.osmfoundation.org/policies/nominatim/) asks. That policy allows light
  use only: turning it on is your decision, and an install with thousands of place names, or many installs sharing one
  service, should use another Nominatim (`[places] url`, [configuration](configuration.md)).
- **Tours:** the kinds of tour.
- **Cleanup:** the Blank and blurry cut-off, and how sure Turned photos must be.
- **Browsing:** how long to point at a photo before it shows larger (0 turns that off).
- **Address and HTTPS:** host names, the reverse proxy and the HTTPS certificate ([remote access](remote-access.md)).
- **Updates:** whether to look for a newer version once a day.
- **Family accounts:** **Accounts and sign-in**, and **Codes for signing in at home**, when accounts are used
  ([family accounts](family-accounts.md)).

## Keyboard

- `?` shows the page's help and keys.
- `/` searches.
- `g` then a letter goes to a page (the help lists them).
- On a photo, `←` `→` step through photos and `Esc` goes back.
