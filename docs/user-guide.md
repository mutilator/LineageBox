# User guide

What each part of LineageBox does, page by page. Every page also has its own help: press **?** (or the **?** at the top
right) for what the page does and its keyboard shortcuts.

## Getting started and getting around

### The first steps

A new LineageBox opens on **Getting started**, at the top of an admin's Home (and on the start page while no folder is
watched): the steps that make it worth opening, each a link to where it is done.

1. **Watch your photos folder** (Settings → Folders): LineageBox reads the photos in it in the background, and never
   changes them.
2. **Check the AI**: Settings → AI, speech and faces says whether the computer with Ollama answers; the step is done
   once a photo has been described, or when the AI is turned off on purpose.
3. **Name yourself and press This is me** (with family accounts: pick yourself as your account's person), so that
   relationships and Home are worked out from you.
4. **Name the largest groups of faces** (Name faces, the groups): done once five people are named and no unnamed
   group is larger than the fifth one's faces.
5. **Start the family tree** on your own page, or bring one in from another program.
6. **Invite someone in the family** (with family accounts on). With them off, this is **Turn on family accounts when
   you're ready**: optional, so the list doesn't wait for it.

Each step ticks itself off from what is in the catalog, however it was done (the config file, a tree brought in), and
the list goes once all of them are done. **Put this list away** hides it for good. Family members never see it.

### The menu

The pages used most are up front: **Home**, **People**, **Family**, **Upload** (with a family account) and
**Folders**, with the search box beside them. **More** opens the rest: **Timeline**, **Map**, **Tours**, **Albums**
and **Favourites**, then **Name faces**, **Tags** and **Cleanup**. Admins also have **Status** and **Settings** up
front. The bell is **New for you**. The LineageBox logo always goes to Home.

When the links don't fit (always on a phone) they fold into **☰**, More's after the others, and on a phone only the
logo's picture shows. The menu works from the keyboard: Tab to **More**, Enter opens it, Esc closes it.

### Light or dark

**Colours**, at the foot of More: **Automatic** follows the computer or phone (light, or dark when it is set to dark),
**Light** and **Dark** keep that one. The choice is kept in each browser, so a phone and a computer can differ, and the
page never flashes the other colours first. The map's popups and boxes follow it too; a picture of the family tree is
always drawn on white.

**Language**, below Colours, is there only once a translation has been added ([translating](translating.md)):
**Automatic** shows the site in the first language your browser asks for that LineageBox has, and picking one keeps it
in this browser. Names, captions and what the family wrote stay as they were written.

### Printing

A person's page and the Family page print as a page about the family: the top bar, buttons and boxes to type in are
left out, filled-in details print as words, the folded panels (life story, family, life events, sources; everyone by
relationship) print unfolded, and dark pages print light on white. For a book to keep, see the family book on a
person's page.

## People and the family tree

### Naming faces

The **People** page lists everyone named, and **Name faces** at its top asks about the faces still to name, one at a
time: the face large, with the whole photo beside it (above it on a phone) and the face outlined. Recognition learns
from every face you name, so each answer helps it find the rest.

- **Is this …?** shows who the face most likely is. **Yes** names it as them; **Not them** says it isn't them, and the
  same face comes back with who else it may be, or a box to type who it is; **Skip** leaves it for later. The keys are
  `y`, `n` and `s` (or `→`), and every button is big enough to tap on a phone.
- The answers about a face have the same words everywhere: **Not them** (it isn't the person it is named, suggested
  or grouped as; on a small face picture it is the **×**), **Don't know** (nobody you know: stop asking; for a group,
  **Don't know them** sets it aside for everyone), **Not a face**, and **Ask the family**. On a group, `d` is Don't
  know them, as it is Don't know on one face.
- A face nobody looks enough like has a box to type who it is, with the names suggested as you type. **Don't know**
  stops asking you about it; **Ask the family** puts it on everyone's Home under **Who is this?**.
- The faces come in this order: those someone in the family asked about; those in photos with people already named
  that look like someone else (nobody is in a photo twice, so each face is matched to the closest person not yet
  named in that photo); those recognised automatically that nobody has checked, the least certain first; and last,
  groups of faces that look like one person, down to groups of two or three, to name all at once. When a group's
  faces together look like someone already named, it asks **Are these …?** and **Yes** names them all; otherwise the
  folders the photos are in may suggest the name. The buttons at the top go through one kind only. Family members are
  asked only about photos and people they can see.
- **Cleanup → Confirm faces** has the same faces as lists, for going through many at once: **Seen together** (untick
  the faces that aren't them, then confirm; unticked faces are never suggested or recognised as that person again),
  **Photos to finish** (**don't know** sets aside someone nobody knows) and **Unnamed groups** (the **×**, not them,
  on a face takes out one that doesn't belong; **Don't know them** sets aside a group nobody knows; who a group looks
  like is filled in). Two more lists check what was found:
  - **Ages to check:** faces named as someone who can't be them by their age: the photo is dated before they were
    born, or the face looks like a young child's while they were grown up then and the photo's date was set by hand
    or said to be right (otherwise it is under **Suggested dates → Looks too young**). Usually the name is right and
    the photo's date is wrong (see [Dates](#dates)); **Not them** takes the name off, and an admin's **Right as it
    is** stops listing one.
  - **Faces missed:** group photos where some faces were found and the AI's description counts more people, family
    photos first. **Look for faces again** looks more closely; the photo then leaves the list (**All found**, for an
    admin, takes it off as it is).
- **Cleanup → No faces found** (admins) lists the photos where the AI's description sees people but no face was found
  at all, the newest first (hover over one for the description). Tick those with faces to find and press **Look
  again**: the worker looks at them more closely in the background, a few a minute, and recognises the faces it
  finds. Tick those whose people face away or are too far off and press **No faces to find**: they aren't listed
  again (Undo, or Recent changes, lists them again).

  Admins' **Find more photos of these people**, on People, names the faces elsewhere that clearly match.
- A face's look can't tell anyone's age (it takes children for adults), so ages are never used to recognise anyone.
  On the page of someone with no birth date, **Born** may say between which years the photos say they were born, when
  their face looks like a young child's in some of them.
- On a photo, **Fix** next to a wrong name lets you type who the face really is, or pick **not them** to take
  the name off so the face waits to be named. It is never suggested or recognised as the wrong person again. An
  unnamed face that looks like someone not yet in the photo offers their name to click.
- **Check faces** on a person's page lists their named faces, the least alike first, for spotting mistakes, and the
  faces recognised as them: **✓** says one is them (it then counts as named by you), **×** that it isn't, and
  **Yes, the ticked are …** confirms every face left ticked.

Names you give by hand always win: recognition never overwrites them.

### The family tree

Enter only facts on each person's page: parents, children, siblings, partners (current or former), sex, and other
relationships such as friends, teachers or pets. LineageBox works out the rest (grandparents, nieces, in-laws,
step-family) relative to the person marked **This is me**. Those words work in search, too.

- The **Family** page shows the family around one person, starting with you; click anyone to put them in the middle.
  Below it, folded away, are everyone by relationship (point at a group to see who is in it, or click it to keep it
  open), friends and pets, groups, and the people not placed yet. Each person's page lists their whole family from
  their own point of view.
- **View in classic family tree style** opens the whole line down from one ancestor on a page of its own: drag to
  move around the tree, scroll or pinch to zoom, and **Save as a picture** downloads the whole tree as one large PNG.
- **Life events** (births, weddings, moves, graduations, adoption, bar and bat mitzvahs, censuses, probate and more,
  as GEDCOM 7 has them) are on each person's page, with dates as exact as you know them ("about 1950"). For a job, a
  religion, a title and the like, **What** says which.
- **Adopted or fostered:** add someone as an adopted son, a foster daughter, an adoptive mother and so on, or change
  **by birth** beside a parent or child on their page. Relationships say so ("adoptive mother", "birth mother",
  "adopted son", "foster brother"), and search finds those words. Adoption makes family as birth does (an adoptive
  mother's mother is a grandmother, an adopted son is among the sons); a foster family is family through the foster
  link only ("foster grandmother"). A child can have two birth parents and two adoptive ones, and foster parents
  besides; the tree marks adopted and foster children.
- **Groups** hold people who aren't family: the kids' friends, family friends, a band from a concert. Groups are
  filters on the People page, and searchable.
- **Documents:** mark a photo of a certificate, an obituary or a letter as a document, and link it to the person or
  event it records.
- **Sources** (on a person's page): a book, a website, a census page or a register that bears out what is there, for
  the person or one of their life events, with where in it ("page 12, line 4", or the record's web address). One
  typed again by the same title is the same source. Family tree programs get documents and sources alike.
- **Stories** on a person's page are recordings of the family telling about them: how they met, the farm, the war.
  **Record a story** records with the microphone, or (on a phone) opens its recorder; pick **About** to make it a
  story of one of their life events (a wedding's story shows on both partners' pages). Anyone who can see the person
  can record one, up to 30 minutes. Whisper, when it is set up, writes down what is said; whoever recorded a story,
  or an admin, can **correct** its words and title or **take it away** (Undo brings it back for a month). Someone
  with stories can't be removed by mistake: take them away first, or merge the person into whoever it really is.
- **A family book**: **A book of …** on a person's page, or **A book of this family** beside View in classic family
  tree style, makes pages to print or keep as a PDF, a gift for a reunion or a memorial. A person's book has their
  portrait, life story, life events, family, the documents that record them, and their best photos year by year
  (favourites first) with the family's captions; a family's book has everyone descended from that person, a few pages
  each, with a list of contents. **Print or save as a PDF** opens the browser's print window: choose **Save as PDF**
  as the printer. It is made from what you see, as the tree and photos are now.
- **Export** (folded away at the bottom of the Family page, under **Family tree programs**) writes the tree as GEDCOM, for Ancestry, FamilySearch, MyHeritage,
  Gramps and the like: GEDCOM 7.0, or 5.5.1 for older programs. You can include each person's portrait, cropped to
  their face (a GEDZIP), and leave out living people's details for sharing. Life stories go as notes, adoptions as
  adopted, sources with where in them, and the places you put on the map with their coordinates.
- **Import** brings in a family tree: it reads a GEDCOM file and matches its people with yours, by ID, names, birth
  years and relatives, with the AI's opinion on the unsure ones. You check every match before anything is added, and
  nothing you entered is overwritten. A file saved as UTF-8 or as Unicode (UTF-16) reads as it was written; one in
  the old ANSEL characters of some 5.5 programs loses its accents, so save it again as UTF-8 if you can. A date in a
  year no calendar has (0000, or 9999 for "not known") is kept as the file wrote it, but gives no ages. Its sources come in cited where the tree cited them, and its places' coordinates
  put them on the map. Its photos are looked for in your photo folders by file name: one found has the person added
  to it (or, for a record, cites them); one not found is listed on the person's page, under **Files another family
  tree names**, so you can copy it in.
- **Read into the family tree** (at the top of the Family page, or a document photo's page) takes an obituary, a
  wedding or birth announcement, a letter or notes, pasted in or as photos, and the AI lists what it would add: new
  people, other names, life events, parents, partners. Each change is a box to tick. People already in the tree are
  found through their relatives ("his wife Kathy" is the wife the tree has, whatever her first name there), and you
  can pick someone else, add them as new or leave them out. Where the text and the tree disagree, the change is
  offered unticked, with what the tree has. Relatives the text doesn't place (grandchildren whose parents it doesn't
  name) are listed, not added. Your ticks are kept as you click them. Once you make the ticked changes, the page shows
  only what changed, and someone added by mistake can be removed from the tree right there; reading it again lists
  what you left unticked once more. It needs the AI (Ollama) on; it takes a minute or two (the page shows an
  hourglass meanwhile), and a long obituary is read in two halves.
- **Remove** (at the bottom of a person's page, for admins) takes someone out of the catalog: added by mistake, or a
  name you don't want any more. Every face named as them loses the name and goes back to the unnamed faces
  (Cleanup → Confirm faces), where it can be grouped, recognised or named as whoever it is; their family links, life
  events and other names go with them. Someone with an account stays until the account is removed. For a few faces
  named wrongly, use **Check faces**; for someone here twice under two names, **Merge**.

### A person's page

Each person's page tells their life:

- **Their portrait**, at the top: click it (or **Choose the portrait**) to pick, from the faces named as them, the one
  People, the Family page, the tree and exports show: their clearest faces, or a year's. On any photo of them, **☆
  portrait** beside their name makes that face it. Until you choose one, it is their clearest named face.
- **Life story**: what the family knows of their life, in your own words. Anyone in the family who can see them can
  write or change it, and an admin can undo it on Cleanup → Recent changes. **Notes**, under it, hold where things
  came from: what reading an obituary added, notes from another family tree.
- **Family and relationships**, **Life events**, **Sources** and **Documents**, as above.
- **Life, by age**: their life events (with the documents and sources that bear each out) and their photos, grouped
  by how old they were, from their first year on, with photos from before they were born or after they died, and
  those with no date, apart. Open a group to see its photos; someone with only a few photos has them all open.
  Without a birth date, the groups are years.

Keys on a person's page: `b` birth date, `e` add a life event, `1`–`9` change one, `r` add a relative, `l` write the
life story, `u` add a source.

### Sharing with the family

With [family accounts](family-accounts.md), relatives sign in with Google or a passkey (the lock of their own phone or
computer, from a link an admin sends them) and see their own branch of the tree and the photos
its people are in. They name faces, add relatives and events, fix dates and upload their own photos. **Who is this?**
on a face asks everyone who can see the photo, and those faces lead their Home page. Admins see every change under
**Cleanup → Recent changes**, with undo.

Members also share: a folder of their uploads, one photo they uploaded, or a tour or album they made, with one relative's
account or with everyone who has one. Whoever it is shared with sees those photos, including those from outside their
own branch, and a shared folder keeps sharing what is added to it later. **Folders** lists a member's uploads, what is
shared with them and what they share; admins find everything anyone shares there too, and can share any folder or tour.

### New for you

The bell at the top of every page counts what happened since you last looked, and opens **New for you**:

- something shared with you (or with everyone);
- **Who is this?** asked about a face in a photo you can see (not your own questions, and not once the face is named
  or you said you don't know them);
- new photos of your family, once the worker has looked for the faces in them (with a few of them; the line opens
  all of them). A member is told only of photos they can see; their own uploads aren't news to them;
- words someone wrote under a photo you uploaded, are in, or wrote on before.

Admins also see **alerts**, which are on the Status page too: files the check found damaged, a disk nearly full
(again each week while it stays so), a service with a problem (the AI's model missing, describing or transcribing
failing, videos that can't be converted, a backup that failed), Ollama not answering for three days, and a newer
version of LineageBox.

What each person sees is checked when the page is shown, against what they may see then: a share that was stopped, a
photo an admin hid or a comment taken away no longer shows. Opening the page marks everything on it as seen; each
account has its own. Without family accounts the house has one list, as an admin. Nothing is emailed or sent to
phones: it is all on this page. Lines older than 90 days are deleted.

### LineageBox on your phone

Over HTTPS ([remote access](remote-access.md)), a phone can put LineageBox on its home screen like an app, with no app
store: in Chrome on Android, the menu → **Install app** (or **Add to Home screen**); on an iPhone, Safari's Share →
**Add to Home Screen**. It then opens without the browser's bars, and starts faster: the site's own script, styles
and icons are kept on the phone. Pages and photos are always fetched from LineageBox, so signing out, or an account
switched off, works as it always has; with no connection, a page says LineageBox can't be reached.

On Android, once installed, LineageBox is in the gallery's **Share** menu: pick photos or videos, share them to
LineageBox, and Upload opens and sends them into your uploads folder. Over plain http at home nothing of this is
offered, and every page works as before.

### Photo frames and TVs

An old tablet, a smart TV's browser or a Home Assistant dashboard can show memories, a tour or an album full screen,
one photo
at a time with a slow fade, its date and caption small at the foot, and videos playing silently. It asks for its
photos again every half hour and keeps going while LineageBox restarts; where the browser allows it (over HTTPS) it
keeps the screen on, and elsewhere set the device's screen never to turn off. A tap or click anywhere on it (or → on a
keyboard) shows the next photo at once, to look through them all before you share its link.

- **On a device someone signs in on**, open `/photo-frame` for your own memories (this week in earlier years, then
  the best of other years), or **As a photo frame** on a tour's page.
- **On a device nobody signs in on**, an admin makes a link under **Settings → Photo frames**: a name, what it shows
  (memories as one account sees them, or the whole library's; a tour, as whoever made it sees it; or an album, in its
  order and with its captions), how long each
  photo stays, and whether it works only on the home network (ticked unless it's for another house). The link is shown
  once, with a QR code a tablet's camera can open; in Home Assistant, put it in a **Webpage** card. A link opens its
  frame and the photos on it, nothing else of the site, and never shows a photo hidden from family members. **Turn
  off** stops it at once; the list says when each was last used and from where.

## Finding photos

### Search

The search box at the top matches:

- the AI's descriptions and tags, and the words it read in the photo (a sign, a cake, a gravestone);
- the captions and comments the family writes on photos, which count for more than the AI's words;
- people's names, including other names such as maiden names and nicknames;
- relationship words, worked out from the family tree: "nephews 2024", "grandma";
- places and events read from folder names, and the folder names themselves;
- dates: "2012", "june 2012", "summer";
- what is said in videos and sound recordings, and the text of documents read into the family tree (an obituary, a
  letter);
- the stories recorded on a photo: their titles, and what is said in them, which count like comments. Search for
  "recording" to find the sound recordings (cassettes, voice memos).

Every word must match. The last one may be the start of a word, so "birthd" already finds birthdays. Examples:
"goat", "emma zoo 2012", "christmas tree", "happy birthday". Results also list the people whose names match.

Search also finds photos by what the words mean, not only the words themselves: "kids sledding" finds a photo
described as "children on a snowy hill", and "a pup by the seaside" a dog on the beach. Photos found both ways come
first, then the best of each mix. Names, family words and years still have to be in the photo: "emma sledding" finds
only Emma's. This needs the AI's computer to be on (Ollama, with the model for search by meaning: [Ollama](ollama.md#search-by-meaning));
while it is off, search finds words alone, as it always did, and nothing waits for it.

As you type, a list under the box offers the people, family words ("cousins"), places, tags and years that fit. Pick
one with a click or a tap (or ↓ ↑ and Enter): a tag or a family word goes into the search, and a person, a place or a
year is added as a filter. Pick two people to find the photos they are in together, with no words at all.

On the results:

- each filter on the search shows at the top; click it to take it off;
- the buttons narrow the results to only photos or only videos, to photos with a face nobody has named yet (to name
  them), or to photos with no date of their own (to date them); below them, pick years and a place;
- they are sorted by best match, or the newest or the oldest first;
- words typed in the box at the top keep the filters on;
- when nothing is found, the page suggests what would find something: a word spelt like a tag, a name or a place in
  your photos, or the search without one of its words or filters.

A search's address holds its words and filters, so it can be bookmarked or sent to someone in the family (who finds
only the photos they may see). On a photo opened from the results, ← and → go through the same results in the same
order. Up to 5,000 photos are shown; more words or filters find the rest. A family member's search, like everything
else, finds only the photos they can see, and suggests only the people, places and tags of those.

### Browsing

- **Home** starts with a row of memories: photos from this week in earlier years ("5 years ago"), tours worth
  making (a birthday coming up, someone through the years, a holiday) and a few of your own tours. They change from
  day to day; one opens like a tour, and **Keep as a tour** saves it. Below come faces someone asked about, what
  others shared with you, then the newest photos by the day they were taken (Yesterday, Tuesday, then by month),
  older photos added lately, and your own uploads. A relative with a family account sees the newest photos of
  themselves and their closest family (parents, partner, brothers and sisters, children) first, however long ago they
  were taken, with **Show older photos of your family** to go further back, and then the rest. Their memories start
  with their family's biggest days of photos and the latest, each named by a life event on those days, the event
  its folder is named for, or what its photos show (a wedding, Christmas). Admins also see how full the disk is, and
  on a new LineageBox, [the first steps](#the-first-steps) until they are done.
- **Folders** shows the watched folders as they are on disk. A photo with copies in several folders shows in each.
  At the top of a folder, **Sharing and removing** shares it with family accounts or moves the whole folder to
  Removed (for a family member: a folder of their own uploads). For an admin, **What this folder means** shows what
  its name was read as (a place, an occasion, a date, names) and corrects it: every photo in it and below takes that
  on, so one misread folder name can be put right for all of them at once. A correction is never read again by the AI
  (**Read the name again** gives it back), and Recent changes can undo it.
- **Timeline** shows every year with photos, each with its events: its biggest days of photos, named by what they
  were (a wedding, a birthday, the event a folder is named for) or by their dates. The two latest years show theirs;
  point at another, or press **Events** beside it, to see its events, and click one to see its photos. Its bars show January to December, so busy
  and quiet months stand out.
- **Map** shows where photos were taken, from their GPS or from a place in their folder's name, along with family
  events that have a place. A slider picks the year. Photos with GPS are named after the nearest town (Springfield,
  near Springfield, or 60 km from it), from a list of towns that comes with LineageBox, so nothing is looked up online
  for them. See [Putting photos on the map](#putting-photos-on-the-map) for photos with no place.
- **Tags** lists every keyword the AI or you have given, with photo counts and a filter box. A tag's page shows its
  photos and the tags most often given with it. For admins, **Rename, merge or remove this tag** on a tag's page
  changes it across the whole library:
  - a new name renames it on every photo; another tag's name merges the two into that one (it asks first). Where a
    photo had both, what people said wins: a tag given by hand stays given by hand, and one taken off stays off;
  - **Remove from every photo** takes it off them all for good, as × does on one photo, and the AI doesn't give it to
    any photo again (someone may still add it by hand);
  - when the AI later uses the old name, describing a photo, it means the tag as it is now;
  - each shows **Undo** at once, and stays on Cleanup → Recent changes. On a big library, search finds photos by the
    new name a few minutes later, once the worker has been through them.

A small **≈** on a thumbnail means its date came from the file name or the file's time rather than the camera (the
page says so under the photos).

Long pages of photos (a folder, a year, a tag, a tour, a person's life by age) show the first 200 and load more as you
scroll down, so they open quickly on a phone and on a small LineageBox; without scripts, **Show more** at the foot
does the same. The years and months at the side (along the top on a phone) jump to them, and mark where you are as
you scroll; a year further down opens the page from there, with **Show earlier photos** at its top. Going back from
a photo returns to the same place, with the same photos loaded. A photo's ← → still step through the whole list.
Changing a photo's date can move it in a folder or a year, which go by date; its page keeps the place it had (107 of
320 stays 107, and → goes on to the photo that came after it), so you carry on from where you were.

On a sharp screen (most phones and some laptops), thumbnails are shown at twice their usual size so they don't look
soft. Each is made from the photo's preview the first time a sharp screen asks for it, and kept.

For screen readers, every thumbnail says who is in the photo (only the people you can see), when it was taken, and
whether it is a video or a recording.

Point at a photo or a face for a moment (half a second; **Settings → Browsing** changes it, or turns it off) and the
whole photo shows larger beside it, with the face outlined: a quick way to see who someone is on the Cleanup and
People pages without opening each photo. Once one shows, the next photo you point at shows at once. Clicking still
opens the photo, and ↗ still opens it in a new tab. On a phone or tablet, hold a finger on a face instead.

### A photo's page

- **← and →** (or ‹ › on the photo) step through the folder, year, search or list you opened it from, and **Back**
  or **Esc** goes back to that list, where you were on it.
- On a phone, swipe to step, tap the photo to see only it (tap again for the rest), and pinch or double-tap to zoom;
  the details are in a sheet at the foot that you slide up. With a mouse, double-click to zoom, or hold Ctrl and use
  the wheel; **⛶** (or **v**) shows only the photo.
- The faces found on the photo are outlined on it, with their names; click or tap one to name it, or to fix its name.
  **☺ Faces** (or **f**) hides the outlines.
- **The caption** is the family's own words for the photo: who, where, the occasion. Anyone who can see the photo can
  write it or change it, and it shows above the AI's description, in search and on the photo's slide in a tour.
  **Comments** go under it ("I remember this day"); whoever wrote one, or an admin, can change it or take it away.
- **♡** at the top (or **h**) makes the photo one of your favourites: tours pick it before others like it, and
  memories show it. **Albums**, near the foot, lists the albums it is in and adds it to one.
- The side panel lists every copy of the file, with download and **Copy network path** (paste it into File
  Explorer's address bar; set the paths under **Settings → On the network**). When the family has given the photo
  names, a caption, tags, a date or a place, **its sidecar (.xmp)** beside a download is a small file with them, for
  other photo programs: keep it next to the photo, with the same name plus .xmp ([other photo
  programs](#other-photo-programs)).
- **Details → Location** says where it was taken (the nearest town, and whether that is from the camera's GPS, put
  there by hand, or from its folder) with **Put it on the map** or **Move it on the map**
  ([putting photos on the map](#putting-photos-on-the-map)).
- **Tools → Write the names, caption and place into the file** (admins, for JPEG, TIFF and NEF files) writes them into
  the photo file itself, for programs that only read the photo, like phone galleries and Windows. It changes the file
  (every copy of it), as turning a photo does; nothing else in it changes.
- **× on a tag** takes it off for good: describing the photo again won't bring it back (**Undo** in the note that
  shows puts it back). **+ tag** adds one.
- **Someone else here?** adds a person whose face wasn't found.
- **Look for faces again** looks more closely, for small faces in group photos.
- **Describe again**, under the AI's description, has the AI look at the photo again and write a new description. The
  worker does it soon, or in the hours the AI may work (**Settings → Schedule**); Status shows how many are waiting.
  The caption, and the tags people gave or took off, stay as they are. Admins may name another AI model in the box
  beside it, from those Ollama has, to try a better one on a photo the usual one got wrong ([Ollama](ollama.md#describing-again)).
  Sound recordings ticked with photos are left out: the AI has nothing to look at in them. Many photos asked for at
  once are described a little each time the worker looks at the library, before any others.
- **More like this**, under the similar photos, shows the photos whose descriptions are closest to this one's, from
  other days as much as there are (with search by meaning on).
- **Remove photo** and **Remove this copy** move files to the Removed folder.
- **Stories:** under the comments, **Record a story** records what you remember about the photo, in your own words.
  On a phone the button opens its own recorder; on a computer the page records with the microphone, but only when the
  site is opened by its secure (https) address (at home over plain http, use the computer's own recorder and pick
  the file). Listen to it, then **Keep this story**; it can be up to 30 minutes. Anyone who can see the photo can
  record one. Whisper, when it is set up, writes down what is said, and search finds the photo by it. Whoever
  recorded a story, or an admin, can correct its words or take it away (**Undo** brings it back).
- **Videos** play on the page. Under the player, **Said in the video** lists what was said, each line a link to its
  moment; **fix** beside a line puts right a word Whisper misheard (leave it empty to take out a line nobody said).
  Corrections are kept, searched, and never written over when the video is transcribed again; **as Whisper heard
  it** puts a line back. A long video (a digitised tape) lists its **chapters**: where each recording on the tape
  starts, or the picture changes completely, each with a picture and the first words said in it. Click one to go
  there. Long videos get more frames too: one every 10 seconds for the first 20, then one a minute.
- **Sound recordings** (a cassette digitised to MP3, a phone's voice memo) are in the folders and lists like photos,
  marked 🎙 with their length, with a picture of their sound wave. Their page plays them, and shows what is said in
  them as for a video. **Whose voices are these?** adds the people speaking. A song from an album (tagged with its
  artist, album and track number) is left out as music.
- **TIFF scans** (`.tif`, `.tiff`, what scanners and photo editors save) are read like other photos: browsers can't
  show TIFF, so their page, the grids and slideshows show a JPEG copy LineageBox makes, and **Download** gives the
  TIFF itself. A 16-bit scan is shown in 8 bits; a TIFF of several pages is shown by its first.
- **A Live Photo** from an iPhone (the photo and the few seconds of video around it, copied off the phone as two files
  with the same name) is one photo here, as on the phone: its page plays the video, and removing or restoring the
  photo takes both files. Removing one copy of the photo, when another copy stays (one folder's copies in Cleanup),
  leaves the video where it is if it has no other copy: it then shows as a short video of its own.

### Putting photos on the map

A photo with no place (or the wrong one) is put on the map from its page: **Details → Put it on the map** opens the
map with a box above it. Click where it was taken, or type a town's name in **Find a town** and pick it, then drag the
pin to the very spot and press **Save this place**. **Undo** in the message that shows takes it back, and Cleanup →
Recent changes keeps it. Anyone who can see a photo can place it; admins can also place a whole folder, from **Put it
on the map** at the top of the folder's page: its photos, and those of the folders in it, without a place of their
own, are then shown there. From the keyboard, move the map with the arrow keys (+ and − zoom) and press **Pin the
middle of the map**.

Which place a photo shows: one put there by hand wins (a phone's GPS can be wrong), then the camera's GPS, then the
nearest folder, up from its own, that is placed by hand or whose name gives a place. Places put there by hand go into
the photo's XMP sidecar, when those are on ([other photo programs](#other-photo-programs)), and into the file itself
only when an admin asks on its page; a folder's, only for its photos the camera gave no position.

## Other photo programs

**Bringing in what other apps knew.** A library tagged in another app starts out named here. Each photo is read once,
and what is found comes in below anything set in LineageBox, which it never changes:

- **faces named** in digiKam, Lightroom, Picasa 3.9 or Windows Photo Gallery (written in the photo, or in an XMP file
  beside it), and in Picasa's `.picasa.ini` files (with the names from its contact list in the same file, or from a
  `contacts.xml` copied into the folder or one above it). Once the photo's faces are found here, each name goes on
  the face its box covers; a name whose face wasn't found adds the person to the photo. A name nobody here has adds
  that person to People; check those under **Settings → Other photo programs**, and merge two who are one;
- **keywords** become tags (one taken off here stays off), and a keyword that is someone's name adds them to the photo;
- **captions**, unless the photo has one here (cameras' own words, like "OLYMPUS DIGITAL CAMERA", are left out; a
  longer one than a caption here may be keeps its first 2,000 characters);
- **dates**, for photos dated only by their file's time, a folder or a file name; and **places**, for photos without
  GPS;
- **Google Takeout's** files beside the photos (IMG_1234.JPG.json or IMG_1234.JPG.supplemental-metadata.json): when a
  photo was taken, where, its description and the people Google Photos named in it.

It is on for a new catalog. A catalog that already had photos when LineageBox was updated keeps it off until an admin
turns it on, so a library named here doesn't suddenly gain every name an old program left behind. **Settings → Other
photo programs** counts what came in.

**Giving it to other programs.** With **Write what the family gives each photo into a small file beside it** on, each
photo the family has given something gets an XMP sidecar beside it, named after the photo (IMG_1234.JPG.xmp beside
IMG_1234.JPG): names on faces (as boxes, the way digiKam, Lightroom and Picasa mark faces), who is in it, the caption,
tags given by hand, a date set by hand and a place put on the map by hand. Names recognition guessed and nobody
confirmed, and the AI's words, are left out. digiKam, darktable, ExifTool and Lightroom (for raw files) read them. The
worker keeps them up to date as names and captions change, and takes LineageBox's away from a photo that has nothing
left to say. A file of that name another program made is left alone, and so is one of LineageBox's that another
program has written into since (darktable and digiKam add their own work to the sidecar they find). Cleanup moves a
photo's sidecar with it to the Removed folder and back. Photos downloaded as a zip, and Download everything, carry the same sidecars whether this is
on or not.

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

A photo dated only by its file's time or its folder may show **Probably …** under Taken: `June 2012` when the photos
beside it in its folder were all taken that month, `about 2012` when within a couple of years, or the years who is in
it allows (nobody is in a photo from before they were born, or after they died). **Why?** says how it was worked out, and **Use it** dates
the photo that way, as if you had typed it. Otherwise it may say **Not before 2014**, from who is in it.

An edited, resized or scanned copy that lost its camera date takes the date of the photo it is a copy of (its
near-duplicate), shown as "camera date of a copy". A date set by hand always stays.

**A camera with its clock wrong:** **Cleanup → Camera clocks** (for admins) lists the cameras, those whose photos show
people before they were born first, the usual sign. Pick one to see its photos in the order it took them, a month at a
time, with the time its clock gave each; ⚠ marks a photo with someone named in it before they were born. Then:

1. Click the first photo it dated wrong and press **Its dates are wrong from this photo**.
2. Click the last one it dated wrong (in a later month if need be), or say **every photo after it**.
3. Click one of those you know the date of (a birthday, a holiday) and type when it was really taken: the day, and the
   time if you know it (left as it is, the camera's time of day stays). **Move the photos** moves them all as much as
   that one.
4. If its clock gained or lost time as it went, click another of those photos further on and date it too: the photos
   between the two move in step, a little more or less each, and those after the last one dated move as much as it.
   A clock that was right at first and drifted later: date its first photo as it is (leave its day and time as they
   are) and a later one as it really was.

The panel of a photo put right also starts or ends the wrong dates at it, forgets a date you gave, or puts them all
back. Photos of that camera read later are moved the same way, dates set by hand stay, and **Recent changes** can undo
each change (photos of the camera read since go back too).

**Scans:** a scanner, or a phone's scanning app, dates what it scans by the day it was scanned, so a letter from 1952
scanned this year is dated this year. **Cleanup → Suggested dates** (for admins) lists them in three tabs:

- **Documents:** photos marked as a document on their page, or that the AI describes as one, that were scanned, plus
  documents with no date at all but their file's time. A document dated by its folder is left alone: someone named
  that folder.
- **Scanned photos:** old prints scanned the same way.
- **Looks too young:** other photos where someone named looks as they did as a child while the photo's date makes
  them 18 or older: an old print photographed with a phone, a copy dated the day it was made. A face looks like their
  childhood when it looks like a young child's, or looks most like their own photos from when they were 12 or younger
  (photos with a camera's date, or dated by hand). Each face is shown with **Not them**, for a name that is wrong. It
  finds more as you name and date their childhood photos.

A scan is known by its scanner (its camera name, such as an HP ScanJet or Epson Perfection) or by "scan" in the name
of its folder or file (ScannedPhotos, CamScanner, Scan_0001). Under each, what can tell its real date, best first:

- a date written on it: the AI reads each scan on the list once, in the hours it describes photos (documents first),
  and **Read the date on it** asks now;
- a date the AI's description saw on it;
- the family tree: a document cited for someone's life event takes its date, and a marriage certificate, birth or
  death certificate, obituary or diploma of someone takes the date of that event;
- the scans just before and after it in its folder that you dated by hand (an album is scanned in its order);
  for a document that isn't a scan, the camera-dated photos beside it;
- who is in it: not before anyone in it was born, nor after anyone in it died; and for someone who looks as they did
  as a child in it, about when their childhood photos it looks most like were taken, or else their childhood (birth
  to 12).

**Date it** gives it that date, as if you had typed it on its page; or type the date as you know it. Either can be
undone on Recent changes, and the scan leaves the list. **Its date is right** leaves it as it is and takes it off.

## Working on many photos at once

**Select photos**, on a folder, a year, a tag, a search, a tour, a person's page (above their life by age) or a Status
list, ticks photos: click a photo, or Tab to it and press Space. Hold **Shift** and click another photo to tick every
photo between the two. Ticks stay as more of the list loads and on its other pages, until the photos are moved away
or you press **Clear**; the bar says how many are ticked, and how many of them aren't on the screen. **Select all**
ticks the whole list, also the photos further down. Then, all at once:

- tag them, or take a tag off;
- add someone to them;
- date them;
- add them to an album (type its name; a new name makes a new album);
- have the AI describe them again (an admin may name the model in the box beside it);
- move them to Removed. On a folder's page that takes only their files in that folder; copies elsewhere stay;
- download their originals as one zip (**Download**).

At most 5,000 photos are changed in one go, an album holds up to 2,000 photos, and one download holds at most 2,000
photos: with more ticked, the bar says so, and nothing is done to only some of them.

## Tours

**Tours** are slideshows picked from the library, such as "the kids, summer 2021" or "grandpa through the years".

- Kinds of tour are set up on **Settings → Photo tours**: who, when, which words, how many photos, photos or videos.
- Whatever a kind leaves open, a wizard asks for. Tours are saved with a name made from the answers.
- Photos are picked afresh each time a tour opens: one per group of similar shots, never blurry, with the clearest
  faces, from different days.
- **Suggested tours** come from what the library holds, such as days that come back year after year.
- Your **favourites** (♡ on a photo's page) come first: of similar shots, in a year or a month, a photo with a heart
  is picked before the others. On an admin's tour everyone's hearts count, so a photo the family loves wins.
- **Play** starts with a title card (the tour's name and the time its photos span), then shows each photo with its
  caption, who is in it and when it was taken, as exactly as that is known: "Summer 1987" for a date typed as a
  season, "1990" for a photo dated by its folder. A video plays to its end, with its sound.
- **Save as a video** makes the slideshow into one video file (an MP4, 1280 × 720) to send to someone or play on a TV.
  The worker makes it in a few minutes; the page says how far it has got and offers **Download the video** when it
  is ready. It holds what you see of the tour, and is kept 30 days (**Make it again** makes a new one). A slideshow
  of more than 300 photos, or longer than 40 minutes, is too long for one.
- With family accounts, a tour can be shared with a relative's account or with everyone who has one. They see the
  photos it picks as it shows them to whoever made it, and it keeps picking new ones for them.

## Albums and favourites

**Albums** hold photos picked by hand, in the order you want them, with a cover and a caption for each: a wedding, a
reunion, a trip. Tours pick their photos by rules and change as photos are added; an album holds just what you put in
it. They are listed on **Albums** (under Tours).

- **Make one** on Albums by its name, or tick photos on any page of photos (a folder, a year, a search, a person) and
  type an album's name under **Add to an album** in the bar at the foot: a name you have no album of makes a new one.
  A photo's page adds it too, under **Albums**, which also lists the albums it is in.
- In your own album, drag the photos into order (or use ↑ and ↓, on a phone or with the keyboard), press ★ on the one
  to be its cover, type a caption under a photo and press Enter, and press × to take a photo out (it stays in your
  photos; **Undo** puts it back). **Rename** also takes a few words about the album.
- An album's caption for a photo is the album's own ("Grandma, who flew in from Ohio"). Where it has none, the photo's
  own caption shows, in grey, so nothing has to be written twice.
- **Play** shows it as a slideshow in its order, after a title card; **Save as a video** and **Download all** work as
  for a tour. Opening a photo from an album, **← →** go through the album in its order.
- With family accounts, each member has their own albums, and shares one as a tour is shared: they see its photos
  in its order, including those of your family they couldn't see before, but not photos others shared with you.
  Others may look at an album shared with them; only its maker, or an admin, changes it.
- **Favourites**: **♡** on a photo's page (or **h**) gives it your heart. Each account has its own. **Your
  favourites**, on Albums, lists them, the latest first, to play or to make into an album. Tours and Home's memories
  show favourites first.

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
  - photos that don't fit the dates: someone named in photos taken before they were born, or more than a year after
    they died, or in a photo from before a parent was born. The name may be wrong, or the photo's date (a scan of an
    old photo often has the day it was scanned); **see the photos** opens them. Marked fine, one comes back if more
    such photos are named;
  - two names that may be one person ("Jenny" and "Jenny Smith");
  - parents with no sex set;
  - a child's parents not entered as partners.

  **Fine as it is** stops listing one that is right.
- **Same person?:** people who may be in the catalog twice: added by a family member under a name already here, or
  two people anyone added whose faces look like one person's, though they were never named in one photo together
  and aren't linked in the family tree. **Same person: merge** makes them one (Recent changes can undo it);
  **Different people** stops listing them.
- **Camera clocks:** cameras whose photos show people before they were born, to put their dates right at once (see
  [Dates](#dates)).
- **Suggested dates:** scanned documents and photos dated by the day they were scanned, and photos where someone looks
  too young for the date, each with what can tell its real date (see [Dates](#dates)).
- **No faces found:** photos where the AI sees people and no face was found, to look at again or set aside.
- **Keep** takes photos off the lists for good. Tick them and press **Keep**, press **Keep all** on a group of similar
  shots, or **Keep both** on a pair of folders. **Kept** lists them all and can put any back.
- **Recent changes:** what was changed on the site (names given, people merged or removed, tags, dates, the family
  tree, folders' meanings), newest first, with **Undo** for each, whether or not family accounts are on. An undo never
  overwrites a change made over it since; it says so instead.

Any search result has **Select photos to remove**.

A family member's Cleanup page has **Confirm faces** and **Family tree** for their part of the family, and **Blank and
blurry** and **Similar shots** for the photos they uploaded themselves (those with no copy anywhere else). They tick
and move those to Removed as they can their uploads anywhere; an admin can put them back. Keep, and moving the ticked
photos of every group at once, are admins' only.

## Status

- what the worker is doing now, and its recent activity;
- how far the descriptions are, with the pace and the time left;
- what is waiting at each stage;
- downloads (face models, Ollama models, Whisper models);
- red notes when something needs attention, such as Ollama not answering or a model that can't look at pictures;
- **Storage**: how full each disk is that holds the watched folders or LineageBox's data folder, and how much of it
  is photos and videos, removed files waiting in the Removed folders (emptying them gives that back), LineageBox's
  previews and catalog (measured once a day), and other files. It turns red when less than a tenth, or less than
  20 GB, is free.
- **Backups**: the last good nightly copy of the catalog (when, where, how big, how many are kept), and a red note
  when a copy failed or none has been made for two days.
- **Alerts**, at the top when there are any: the newest of the admins' alerts from New for you (damaged files, a disk
  nearly full, a service with a problem, a newer version).

It refreshes itself every 10 seconds. The numbers link to their photos (all with GPS, all dated by file time, each
format), and pointing at a line says what it counts. **Check now** starts a worker pass at once.

## Settings

Changes here apply without a restart. They are kept in the catalog, so the web UI and the worker both see them, and
they win over `.env` and config files. On a new LineageBox, [the first steps](#the-first-steps) on Home point to the
parts to set first (the watched folder, the AI). Light or dark is not here: each browser chooses its own, under
More → Colours.

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
- **AI, speech and faces:** **AI (Ollama)** and **Speech in videos, recordings and stories (Whisper)**: on or off, the
  address, the model, how many at a time, and a connection check; for speech, the **language spoken** (auto, for
  Whisper to tell from the speech, or a code such as en). **Faces:** on or off.
- **Map:** looking up place names with OpenStreetMap's Nominatim. It is off until you turn it on, because it sends the
  place names (and nothing else) to nominatim.openstreetmap.org. It looks up a few a minute and each name only once, as
  Nominatim's [usage policy](https://operations.osmfoundation.org/policies/nominatim/) asks. That policy allows light
  use only: turning it on is your decision, and an install with thousands of place names, or many installs sharing one
  service, should use another Nominatim (`[places] url`, [configuration](configuration.md)).
- **Other photo programs:** XMP sidecars beside the photos (off until you turn them on), and bringing in the names,
  tags, captions and dates other apps left with the photos, with what came in so far ([other photo
  programs](#other-photo-programs)).
- **Tours:** the kinds of tour.
- **Cleanup:** the Blank and blurry cut-off, and how sure Turned photos must be.
- **Browsing:** how long to point at a photo before it shows larger (0 turns that off).
- **Address and HTTPS:** host names, the reverse proxy and the HTTPS certificate ([remote access](remote-access.md)),
  and whether passkeys can be set up.
- **Updates:** whether to look for a newer version once a day.
- **Backup:** the nightly copy of the catalog, on unless you turn it off: where it goes (a folder in the data folder
  unless you name another, such as a second disk or a folder Dropbox or Google Drive keeps in sync), how many to keep,
  and **Back up now**. **Download everything** gives one zip of the family's work (the tree, names on faces, dates,
  tags, captions and comments) that any other program can read ([export](export.md)); restoring a copy is in
  [installation](install.md#updating-and-backups).
- **Photo frames:** links for a tablet, a TV or a Home Assistant dashboard that show memories, a tour or an album
  without
  anyone signing in, each with **Turn off** ([photo frames](#photo-frames-and-tvs)).
- **Family accounts:** **Accounts and sign-in** (with each account's passkeys and passkey links), and **Codes for
  signing in at home**, when accounts are used ([family accounts](family-accounts.md)).

## Keyboard

- `?` shows the page's help and keys.
- `/` searches.
- `g` then a letter goes to a page (the help lists them).
- On a photo, `←` `→` step through photos and `Esc` goes back; `h` gives it your heart, or takes it off.
- On a tour, an album or Your favourites, `p` plays the slideshow. The slideshow holds the keyboard while it plays:
  space pauses, `←` `→` step, `Esc` closes it and puts you back on **Play**; a screen reader stays in it.
- The keys of one letter or sign (`?`, `/`, `g` and the letters on buttons) can be turned off at the foot of the help
  (**Keys of one letter or sign**), in each browser: for a screen reader or speech software of your own, or if they get
  pressed by mistake. The arrow keys, `Esc`, Tab and Enter still work, and the help opens from **?** at the top.
- On a long page of photos, Tab reaches the years and months at the side; one picked moves the keyboard to its first
  photo.
- On the map, Tab reaches each circle; Enter or Space opens it, and `Esc` closes it again.
- On Upload, Tab reaches **Pick photos** and **Pick a folder**; Space or Enter opens the browser's picker.
- On a phone or tablet (a screen with no mouse to point with), the help and the pages leave the keys out: there is no
  keyboard to press them on.
