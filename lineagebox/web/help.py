"""What the help panel (the ? key, or ? at the top right) says: about the page it is opened on, the keys that work
everywhere, and a line on every page. Keys of a page's own buttons and boxes are read from the page (data-key), so
they are not repeated here; `keys` are those a script handles instead (arrows, a slideshow)."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Page:
    title: str
    about: tuple[str, ...]
    keys: tuple[tuple[str, str], ...] = field(default=())


# g then a letter: where it goes (the letters are also in app.js)
GO = (("h", "/home", "Home"), ("f", "/", "Folders"), ("t", "/timeline", "Timeline"), ("m", "/map", "Map"), ("p", "/people", "People"),
      ("a", "/family", "Family"), ("o", "/tours", "Tours"), ("g", "/tags", "Tags"), ("c", "/cleanup", "Cleanup"),
      ("s", "/status", "Status"), ("e", "/settings", "Settings"))

EVERYWHERE = (("?", "open or close this help"), ("/", "search"),
              ("Esc", "leave the box you are typing in, so keys work again; close this help"),
              ("Tab", "move to the next box or button; Shift+Tab goes back"),
              ("↓ ↑", "in a box for someone's name: pick one of the people it suggests as you type; Enter or Tab "
                      "takes them"),
              ("Enter", "save the box you are in (each box saves on its own)"))

# one line on each page of the menu, for the help's "Every page" list
MENU = (("/home", "Home", "memories, faces someone asked about, and the newest photos of the family by day"),
        ("/", "Folders", "your photos folder by folder, as they are on disk"),
        ("/folders", "Folders", "your uploads, what others in the family share with you, and what you share"),
        ("/timeline", "Timeline", "photos by the year and month they were taken"),
        ("/map", "Map", "where photos were taken and where family lived, year by year"),
        ("/people", "People", "faces to name, and everyone named; type to find someone"),
        ("/family", "Family", "the family tree, reading obituaries and notes into it, acquaintances by group, and "
                                    "family tree programs (GEDCOM)"),
        ("/tours", "Tours", "slideshows picked by rules: a person, a season, anniversaries"),
        ("/tags", "Tags", "keywords given by the AI or by you; open one to work on its photos"),
        ("/cleanup", "Cleanup", "duplicates, blurry and similar shots to move to the Removed folder"),
        ("/status", "Status", "what the worker is doing, downloads, and counts that link to their photos"),
        ("/settings", "Settings", "folders, the schedule, AI, speech and faces, the address, and family accounts"))

SELECTING = ("Ticking photos: click a photo (or Tab to it and press Space) to tick it; ↗ opens it in a new tab. The bar at "
             "the bottom tags the ticked photos, adds someone to them, dates them, or moves them to the Removed folder, "
             "from which they can be put back.")

PAGES = {
    "home": Page("Home", (
        "Memories, along the top: photos from this week in earlier years, tours worth making (a birthday coming up, "
        "someone through the years, a holiday) and a few of your own tours. Open one to see its photos or play it; "
        "Keep as a tour puts it on the Tours page. They change from day to day.",
        "Who is this?: faces someone in the family asked about. Type who it is, or Don't know to stop seeing it.",
        "Below them, the folders, tours and photos others shared with you, then the newest photos by the day they "
        "were taken, older photos added lately, and your own latest uploads.",
        "With a family account, the newest photos of you and your closest family (parents, partner, brothers and "
        "sisters, children) come first, under Recent photos of your family, however long ago they were taken; a "
        "button above them for each of them opens their memories and photos. Show "
        "older photos of your family goes further back. Most recent photos has the rest. Your memories start with "
        "your family's biggest days of photos and their latest, named by what they were (a wedding, a birthday).",
        "For admins, Storage at the top shows how full the disk is; it opens the Status page, which says what takes "
        "the room.")),
    "home_person": Page("One of your family", (
        "Home as it would be of one person: their memories (their biggest days of photos, named by what they were, "
        "and this week in earlier years), then their newest photos, however long ago they were taken. Show older "
        "photos goes further back.",
        "The buttons above Recent photos of your family on Home open this page for each of your closest family. "
        "Their page has their family, life events and every photo of them.")),
    "folders": Page("Folders", (
        "Your uploads: open one of your folders to see its photos, share it, or move it to the Removed folder. "
        "Select photos in a folder to move only those.",
        "Shared with you: folders, tours and photos others in the family shared with you or with everyone. You see "
        "their photos as you see your own family's.",
        "What you share: everything you shared and with whom. Stop sharing takes it back at once.",
        "An admin sees here everything anyone shares.")),
    "error": Page("A page that can't be shown", (
        "What was asked for isn't here: a photo or person removed or merged since the link was made, one outside "
        "your part of the family, or an address typed wrong. The start page has everything you can see.",)),
    "signin": Page("Signing in", (
        "Sign in with the Google account of the email address you were invited with.",
        "Or with a passkey: Sign in with a passkey, then unlock your phone or computer as you always do (your face, "
        "fingerprint or PIN). On a computer without your passkey, choose your phone in the window that opens. Your "
        "first passkey comes from a link an admin sends you.",
        "At home, when the internet is down, an admin can sign in with the code from their authenticator app.")),
    "passkey_invite": Page("Making your passkey", (
        "This page comes from a link an admin sent you. Make my passkey asks your phone or computer to make a "
        "passkey for this site: unlock it as you always do, and it keeps the passkey. You are then signed in.",
        "Make it on your own phone or computer: whoever unlocks it later signs in as you. The link works once.",
        "If your phone offers to keep the passkey in a password manager (iCloud Keychain, Google Password "
        "Manager), say yes: your other devices then have it too, and a new phone gets it back.")),
    "passkeys": Page("Passkeys", (
        "Your passkeys: each signs you in with the lock of a phone or computer, instead of Google. Rename one so "
        "you know which is which; Take it away stops it signing in and signs out wherever it signed you in (a lost "
        "phone).",
        "Add a passkey makes one on the phone or computer you are using. For safety it works only soon after "
        "signing in (15 minutes, or 3 hours after your very first sign-in): otherwise sign out and in again.",
        "On a computer without a passkey, you can also sign in with your phone's: choose your phone in the window "
        "that opens, and point it at the code the computer shows.")),
    "upload": Page("Uploading", (
        "Drop photos, videos or whole folders on the box, or pick them. They go into your own folder, and the worker "
        "reads them within a minute or so: then faces are found and named, and the AI describes them.",
        "Whoever is in your photos sees them too. A photo with nobody in it is seen only by you (and the admins), "
        "until you share it: open your uploads folder on Folders to share a folder, or a photo's page to share it.",
        "Name a folder to keep a lot together (Christmas 2025); dropped folders keep their own names.")),
    "cleanup_damaged": Page("Damaged files", (
        "Files a disk spoilt: cut short, with a blank stretch where a block couldn't be read, or with other bytes than "
        "when they were catalogued, though nobody changed them. The worker reads every file whole once, a while each "
        "pass, then each new file.",
        "Tick and move them to the Removed folder, or put a good copy over the damaged file (from a backup): the "
        "worker reads it again. Fine as it is takes one off the list for good.")),
    "cleanup_changes": Page("Recent changes", (
        "What family members changed: faces named, tags, dates, people added to photos, family links, details. "
        "Undo puts one back while nobody has changed the same thing again since.",
        "A family link can let someone see more of the family than they should (marking a brother's wife as your "
        "mother makes her parents your grandparents): those are marked, with whom each account now sees.")),
    "cleanup_same": Page("Same person?", (
        "People a family member added who may already be here: the same name, another name, or the same first and "
        "last name. Merge them if they are one person; tick whether whoever added them goes on seeing them (a "
        "relative mapped wrong, or a friend from another family). Link them from the person's page if they are "
        "related, or say they are different people.",)),
    "accounts": Page("Family accounts", (
        "Each account is an email address tied to someone in the family tree. A member sees their own part of the "
        "family: their ancestors, every blood relative, the partners of those and their children, and their own "
        "partner's side; the photos those people are in; and what they upload. An admin sees everything.",
        "Add an account is at the top of the list. Each account below shows its person's name: click it to change "
        "who they are, their role and their exceptions; it stays open while you do. Find an account narrows the "
        "list as you type a name, an email address, admin or member.",
        "Exceptions show someone more, or less, to one account: alone, or with their family.",
        "Passkeys, for those without a Google account: Make a passkey link on an account gives a link (and a QR code "
        "for their phone's camera) that makes their first passkey. It works once, for a week (a day for an admin's "
        "account). Their passkeys are listed on their account, each with Take it away. Passkeys need HTTPS, the "
        "Address from outside, and that name listed exactly under Host names, with no name with *: Passkeys, in "
        "Sign-in, says what is missing.",
        "See as shows the site exactly as that member sees it, until you stop; nothing can be changed meanwhile.",
        "Sign in with Google needs a client from Google Cloud: its redirect address is shown here to paste there.")),
    "codes": Page("Codes for signing in at home", (
        "When the internet is down, Google can't say who is signing in. An admin at home can then sign in with their "
        "email and the 6-digit code from an authenticator app on their phone (Google Authenticator and others).",
        "Make a code shows a QR code once: scan it with the app, then type the code the app shows to check it works. "
        "Keep the key somewhere safe. Making a new code, or taking it away, stops the old one working (a lost "
        "phone).")),
    "folder": Page("A folder", (
        "The folder as it is on disk: its photos and the folders in it. Photos found in more than one folder are the "
        "same photo: each shows wherever it has a copy.",
        "Select photos ticks photos to date them all at once (scans from one roll of film), tag them or add someone "
        "to them. Moving ticked photos to Removed takes only their files in this folder; copies elsewhere stay.",
        "Sharing and removing, at the top, of a folder that is yours (a family member's: their uploads): share it "
        "with one family account or with everyone who has one, and they see its photos, those added later too. Move "
        "this folder to Removed takes it with everything in it; it can be put back from Cleanup → Removed.",
        SELECTING)),
    "timeline": Page("Timeline", (
        "Every year with photos, newest first. The bars show January to December, so busy and quiet months stand "
        "out. Open a year to see its photos month by month.",
        "Under each year are its events: its biggest days of photos, named by what they were (a wedding, a "
        "birthday, the event a folder is named for) or by their dates. The two latest years show theirs; point at "
        "another year to see its events, and click one to see its photos.",
        "Photos dated only by their file's time can be in the wrong year: Status → Where dates come from lists them.")),
    "year": Page("A year", (
        "The year's events at the top (its biggest days of photos: click one to see them), then its photos, month "
        "by month. The months above them jump to each one.",), (
        ("← →", "the year before or after"),)),
    "map": Page("Map", (
        "Where photos were taken (by their GPS, or the place in their folder's name) and where family events "
        "happened. The bars show photos per year: dark for those with a place, light for those without.",
        "Pick a year with the slider, or a bar, to see only that year. Click a dot for its photos and events.",
        "Places from folder names and events are looked up on OpenStreetMap when that is on in Settings; one put "
        "in the wrong spot can be moved by typing where it really is.",
        "Places from folder names start as not checked. Press Right place on one that is where it belongs, and it "
        "moves to the confirmed ones; untick a kind in the box at the top right to see only those left to check.")),
    "people": Page("People", (
        "Everyone named, the people in the most photos first; Show more shows the next ones. Type in the find box "
        "to look among all of them by name, other name, relationship or group.",
        "Faces nobody has named yet are under Cleanup → Confirm faces.")),
    "cleanup_faces": Page("Confirm faces", (
        "Seen together: unnamed faces in photos where someone is already named, grouped by who they look most like. "
        "Nobody is in a photo twice, so each face is compared only with the people not named in its photo yet: next "
        "to one brother, a boy who looks like both brothers is suggested as the other. Untick any face that isn't "
        "them, then confirm: the ticked faces are named, and the unticked ones are never suggested or recognised as "
        "them again.",
        "Photos to finish: photos and videos where two or more people are named and someone else isn't yet. Type "
        "who each face is or take the guess; “stranger” stops asking. In a video, naming someone also names their "
        "other faces in it that clearly match, and such faces are named by themselves as soon as the video has a "
        "face of that person named.",
        "Unnamed groups: faces nobody has named, grouped by likeness, the largest first. Click × on any face that "
        "doesn't belong, then type who it is; a name already taken adds the faces to that person. Hide a group of "
        "strangers to stop seeing it.")),
    "person": Page("A person", (
        "Everything about one person: their name, sex, birth, family and other relationships, life events, "
        "documents and photos.",
        "Family: enter only what you know directly (parents, children, siblings, partners, friends); nieces, cousins "
        "and in-laws are worked out. A new name adds that person. While fewer than two parents are entered, a box "
        "in the list adds one; a child with one parent entered offers their parent's partners as the other.",
        "Life events: births, deaths, weddings, moves, with dates as exact as you know them (1984-06-12, June 1984, "
        "about 1950, between 1950 and 1955). Press the number of an event to change it.",
        "After adding a life event, the Add box is ready for the next one.",
        "For admins, at the bottom of the page of someone in no photos: Remove, for someone added by mistake (by "
        "reading a notice into the tree, say). Their family links, life events and other names go with them.")),
    "person_faces": Page("Checking faces", (
        "The faces recognised as this person, the least certain first, and the ones you named. Click × on any face "
        "that isn't them: the name comes off, it is never recognised as them again, and it can be named as whoever "
        "it is on its photo.",)),
    "family": Page("Family", (
        "At the top, for admins: read into the family tree. Paste an obituary, an announcement, a letter or notes, or "
        "add photos of one; the AI lists what it would add, and you approve each change.",
        "The family around one person, from their grandparents down to their children, starting with you (the "
        "person marked “This is me”). Click anyone to put them in the middle, to climb up or down the tree a "
        "generation at a time; double-click someone to open their page. The line above says the way back to you.",
        "View in classic family tree style opens the whole line down from one ancestor on a page of its own, to "
        "move around and save as a picture.",
        "Below, folded away: everyone by relationship, friends and pets, acquaintances (teachers, friends, bands) by "
        "group, and those not placed yet. Point at a group's name to see who is in it, or click it to keep it open.",
        "Family tree programs (GEDCOM), at the bottom: Export downloads a GEDCOM file (with photos as a .gdz if you "
        "like); Import brings one in, its people matched to those here, and you check each match before anything "
        "changes.")),
    "family_tree": Page("The whole line down from one ancestor", (
        "Everyone descended from one ancestor, with their partners: pick whom it starts from at the top. A bold "
        "frame is the family line; a plain one married in; a dashed one a former partner.",
        "Drag the tree to move around it, or use the arrow keys once it is clicked. Scroll or pinch to zoom in and "
        "out; Show it all fits the whole tree in the frame. Click someone to open their page.",
        "Save as a picture downloads the whole tree as one large PNG, however much of it shows."), (
        ("← → ↑ ↓", "move around the tree (click it first)"),)),
    "family_names": Page("Names to check", (
        "Family tree programs keep given names and surnames apart. Each name here was split at its last word; fix "
        "the ones that are wrong and confirm the rest.",)),
    "family_read": Page("Read into the family tree", (
        "Paste an obituary, a wedding or birth announcement, a letter or notes, or add photos of one. The AI reads it "
        "and lists the changes it would make to the family tree, for you to tick.",
        "It takes a minute or two; you can leave the page meanwhile. Readings stay here, to look over later.")),
    "family_read_review": Page("What it read", (
        "Each person the text names, who they are in the tree, and the changes the text suggests for them. Check who "
        "is who first (an unsure match is marked; pick someone, someone new, or leave them out): the changes follow.",
        "Ticked changes add what the tree lacks. Where the text and the tree differ, the change is unticked and says "
        "what the tree has: tick it only if the text is right. Changes for someone new are made only with them. "
        "Your ticks are kept as you click them, also when picking who someone is loads the page again.",
        "Once the ticked changes are made, the page shows only what changed. Someone it added by mistake can be "
        "removed there with Remove from the tree.",
        "What the AI copied from photos can be corrected under What was read, then read again; reading it again also "
        "lists what wasn't ticked once more.")),
    "family_import": Page("Bringing in a family tree", (
        "Who in the file is who here: the unsure matches come first. Pick the person they are, or “add as new”. "
        "Nothing changes until you apply it, and what is already here is kept where the file disagrees.",)),
    "tours": Page("Tours", (
        "Slideshows made by rules. A tour picks its photos again each time it is opened, so new photos join it.",
        "Suggested tours are picked from your own photos: the people in the most of them, the two most often "
        "together, busy years and seasons, birthdays coming up, and occasions whose tags come back every year. "
        "Make this tour saves one; it can be changed or deleted like any other.",
        "Start one from a kind of tour (a person's year, a season, anniversaries, a family line); kinds can be "
        "changed or added in Settings.",
        "Shared with you: tours others in the family made and shared with you. You see each as it shows its photos "
        "to whoever made it.")),
    "tour": Page("A tour", (
        "Its photos, best of each group of similar shots. Play shows them full screen.",
        "A tour opened from Home's memories isn't saved until you press Keep as a tour.",
        "Sharing, at the bottom of your own tour: share it with one family account or with everyone who has one. "
        "They see the photos it picks, also those of your family they couldn't see before, and the tour keeps "
        "picking new ones for them. Photos others shared with you are not passed on."), (
        ("Space", "pause or go on (in the slideshow)"), ("← →", "previous or next photo (in the slideshow)"),
        ("Esc", "leave the slideshow"))),
    "tour_wizard": Page("Making a tour", (
        "Answer the questions; the line at the bottom says how many photos the answers so far give.",)),
    "tour_type": Page("A kind of tour", (
        "The rules a kind of tour starts from. Leave a choice on “ask each time” to choose it whenever a tour of "
        "this kind is made.",)),
    "tags": Page("Tags", (
        "Keywords given to photos, by the AI as it describes them or by you. Type to find one; open one to see its "
        "photos and work on them together.",)),
    "tag": Page("A tag", (
        "The photos with this tag, oldest first, and tags often given with it.",
        "Select photos to tag them with something else, add someone to them, take this tag off them (for good: the "
        "AI won't give it back), or move them to Removed.", SELECTING)),
    "search": Page("Search", (
        "People whose name (or other name) matches come first, then photos. Every word must match a description, "
        "tag, place, occasion, folder name, person or date: “beach 2014”, “Emma snow”.", SELECTING)),
    "photos": Page("Photos from Status", (
        "The photos one line of the Status page counts.", SELECTING)),
    "image": Page("A photo", (
        "The photo, what the AI says about it, its tags, the people in it and its files.",
        "Tags: × takes one off for good; type in “+ tag” to add one. People: name a face by typing who it is; add "
        "someone whose face wasn't found with “Someone else here?”. “Fix” on a named face puts a wrong name right: "
        "type who it is instead, or “don't know who” to take the name off so the face waits to be named. Either way "
        "it is never suggested or recognised as the wrong person again.",
        "An unnamed face that looks like someone not already in the photo shows their name with a ?: click it if "
        "it's them. “not a face” hides a pattern or a statue. “Look for faces again” looks more closely than the "
        "first time, for small faces in group photos; the new faces are recognised where they clearly match, and "
        "faces marked not a face come back.",
        "Taken: a date that is wrong (a scan dated by its folder, a camera with its clock wrong) can be put right "
        "as exactly as you know it: 1987-08-14, June 1987, summer 1987, 1987, the 1980s, about 1987, between "
        "summer 1987 and summer 1988. Winter 1988 means December 1988 to February 1989 (south of the equator, set on "
        "Settings, that is summer 1988). The automatic date is "
        "kept, and “use the automatic date” goes back to it.",
        "Is this a document? Mark scans of certificates, letters and records, then say whose record it is: it shows "
        "on their page as a source.",
        "Sharing, for a photo you uploaded: share it with one family account or with everyone who has one."), (
        ("← →", "previous or next photo"), ("Esc", "back to where you came from"))),
    "cleanup": Page("Cleanup", (
        "Lists of photos you may not want: duplicate copies, nearly black or blurry ones, screenshots and the like. "
        "Nothing is removed until you select it; removed files move to the Removed folder and can be put back.",)),
    "cleanup_copies": Page("Duplicate copies", (
        "Pairs of folders holding identical copies of the same photos. Remove the copies from one folder; the "
        "other keeps them. Keep a pair to stop it being suggested.",)),
    "cleanup_list": Page("A cleanup list", (
        "Ticked photos are the ones to remove; untick any you want to keep. Keep takes a photo off every cleanup "
        "list for good.", SELECTING)),
    "cleanup_similar": Page("Similar shots", (
        "Groups of burst shots and copies of the same picture. The best of each group is kept; the rest are ticked "
        "for removal. Pick a kind to work through one at a time.",)),
    "cleanup_removed": Page("Removed", (
        "Files moved to the Removed folders, by when. Restore puts them back where they were. Emptying deletes "
        "them for good, and with them the previews and face crops LineageBox made.",)),
    "cleanup_family": Page("Family tree", (
        "What looks wrong or unfinished in the family tree: someone with one parent entered (with the parent's "
        "partners offered as the other), brothers and sisters with no parents, dates that can't be right, one "
        "person perhaps entered twice, parents with no sex set, and a child's parents not entered as partners.",
        "Put each right here or on the person's page. “fine as it is” stops listing one that is right: a parent "
        "nobody knows, two people who only share a name.")),
    "cleanup_turned": Page("Turned photos", (
        "Photos on their side or upside down, found by the faces in them: all lying the same way, none upright. Each "
        "is shown turned the way the faces say. Tick the ones that look right and turn them; their files change too "
        "(the orientation tag only), so every program shows them upright. “fine as they are” stops listing the rest.",
        "Sure means two or more faces agree, or one face does and the AI, shown the photo both ways, agrees too. "
        "Settings → Cleanup can also list those worth a look: one face the AI hasn't agreed on yet, or faces the AI "
        "disagrees with. A photo with no clear face can't be found this way.",
        "Any photo can also be turned by hand, under its files on its own page.", SELECTING)),
    "cleanup_kept": Page("Kept", (
        "Photos and folder pairs you chose to keep: cleanup doesn't suggest them. Undo to let it.",)),
    "status": Page("Status", (
        "What the worker is doing and has left to do, downloads in progress, problems, and counts of the catalog.",
        "Point at a line to see what it counts. Numbers that are links open those photos, such as all photos with "
        "GPS or all dated only by their file's time.",
        "Storage: how full each disk is that holds the watched folders or LineageBox's data folder, and how much of "
        "it is photos and videos, removed files (until Cleanup → Removed is emptied), LineageBox's previews and "
        "catalog, and other files. It turns red when space runs low.",
        "Slow pages lists the pages that took more than a second to answer since LineageBox last started: where to "
        "look when the site feels slow.")),
    "settings": Page("Settings", (
        "The menu at the side shows one part of Settings at a time (on a phone, it is above): the watched folders, "
        "how they are read and their paths on your network; when the worker runs; the AI (Ollama), speech (Whisper) "
        "and faces; the map's place lookups; kinds of tour; the cleanup lists; how long pointing at a photo takes to "
        "show it larger; the address and HTTPS; updates; and family accounts with the codes for signing in at home.",
        "Address and HTTPS: the host names LineageBox answers to, a reverse proxy in front of it, and its HTTPS "
        "certificate (files it can read, or uploaded). Switching HTTPS restarts the web server and moves the page "
        "to its new address. Passkeys there says whether passkeys can be set up, and what is missing if not.",
        "Changes apply on the worker's next pass, which saving starts right away.")),
    "empty": Page("No photos yet", (
        "Add a watched folder in Settings; the worker then finds and reads the photos in it.",)),
}


def for_page(name: str) -> Page:
    return PAGES.get(name, Page("This page", ()))
