"""What the help panel (the ? key, or ? at the top right) says: about the page it is opened on, the keys that work
everywhere, and a line on every page. Keys of a page's own buttons and boxes are read from the page (data-key), so
they are not repeated here; `keys` are those a script handles instead (arrows, a slideshow).

Every text is marked N_() where it is written, for pybabel to find, and translated into the viewer's language where
the help is shown (for_page, everywhere, go, menu; docs/translating.md). Keys' names stay as they are on keyboards."""
from dataclasses import dataclass, field

from lineagebox.i18n import N_, gettext


@dataclass(frozen=True)
class Page:
    title: str
    about: tuple[str, ...]
    keys: tuple[tuple[str, str], ...] = field(default=())


# g then a letter: where it goes (the letters are also in app.js)
GO = (("h", "/home", N_("Home")), ("f", "/", N_("Folders")), ("t", "/timeline", N_("Timeline")),
      ("m", "/map", N_("Map")), ("p", "/people", N_("People")), ("a", "/family", N_("Family")),
      ("o", "/tours", N_("Tours")), ("g", "/tags", N_("Tags")), ("c", "/cleanup", N_("Cleanup")),
      ("s", "/status", N_("Status")), ("e", "/settings", N_("Settings")))

EVERYWHERE = (("?", N_("open or close this help")), ("/", N_("search")),
              ("Esc", N_("leave the box you are typing in, so keys work again; close this help")),
              ("Tab", N_("move to the next box or button; Shift+Tab goes back")),
              ("↓ ↑", N_("in a box for someone's name: pick one of the people it suggests as you type; Enter or Tab "
                         "takes them")),
              ("Enter", N_("save the box you are in (each box saves on its own)")))

# one line on each page of the menu (_side_menu.html), in its order, for the help's "Every page" list: Add photos, Home,
# then each section's pages, the bell and the admins' own. _help.html leaves out those the viewer has no link to.
MENU = (("/upload", N_("Add photos"), N_("photos and videos from this computer or phone, into a folder of your own: the "
                                         "button at the top of the menu")),
        ("/home", N_("Home"), N_("memories, faces someone asked about, and the newest photos of the family by day; for "
                                 "an admin, the first steps on a new LineageBox until they are done")),
        ("/", N_("Folders"), N_("your photos folder by folder, as they are on disk")),
        ("/folders", N_("Folders"), N_("your uploads, what others in the family share with you, and what you share")),
        ("/timeline", N_("Timeline"), N_("photos by the year and month they were taken")),
        ("/map", N_("Map"), N_("where photos were taken and where family lived, year by year")),
        ("/tags", N_("Tags"), N_("keywords given by the AI or by you; open one to work on its photos")),
        ("/favourites", N_("Favourites"), N_("the photos you gave a heart")),
        ("/albums", N_("Albums"), N_("photos picked by hand, in your own order, with captions")),
        ("/tours", N_("Tours"), N_("slideshows picked by rules: a person, a season, anniversaries")),
        ("/people", N_("People"), N_("everyone named, and faces to name; type to find someone")),
        ("/family", N_("Family"), N_("the family tree, reading obituaries and notes into it, acquaintances by group, "
                                     "and family tree programs")),
        ("/people/name", N_("Name faces"), N_("the faces nobody has named yet, one at a time")),
        ("/cleanup", N_("Cleanup"), N_("duplicates, blurry and similar shots to move to the Removed folder, and faces "
                                       "to confirm")),
        ("/news", N_("New for you"), N_("what happened that you may want to see: the bell at the top")),
        ("/status", N_("Status"), N_("what the worker is doing, downloads, and counts that link to their photos")),
        ("/settings", N_("Settings"),
         N_("folders, the schedule, AI, speech and faces, the address, and family accounts")))

SELECTING = (N_("Ticking photos: click a photo (or Tab to it and press Space) to tick it; ↗ opens it in a new tab. The "
                "bar at the bottom tags the ticked photos, adds someone to them, dates them, adds them to an album "
                "(type its name; a new name makes a new album), has the AI describe them again, or moves them to the "
                "Removed folder, from which they can be put back. Hold Shift and click another photo to tick every "
                "photo between the two. Ticks stay as more photos load and on the list's other pages, until you press "
                "Clear; Select all ticks the whole list, also the photos further down."))

# a long page of photos (a folder, a year, a tag, a tour) loads as it is scrolled (pieces.py)
IN_PIECES = (N_("Its photos load as you scroll down. The years and months at the side (along the top on a phone) jump "
                "to them: the one you are in is marked."))

PAGES = {
    "home": Page(N_("Home"), (
        N_("Memories, along the top: photos from this week in earlier years, tours worth making (a birthday coming up, "
           "someone through the years, a holiday) and a few of your own tours. Open one to see its photos or play it; "
           "Keep as a tour puts it on the Tours page. They change from day to day."),
        N_("Who is this?: faces someone in the family asked about. Type who it is, or Don't know to stop seeing it."),
        N_("Below them, the folders, tours and photos others shared with you, then the newest photos by the day they "
           "were taken, older photos added lately, and your own latest uploads."),
        N_("With a family account, the newest photos of you and your closest family (parents, partner, brothers and "
           "sisters, children) come first, under Recent photos of your family, however long ago they were taken. The "
           "buttons above them open the memories and photos of one person: yours first (only photos of you), then one "
           "for each of them. Show older photos of your family goes further back. Most recent photos has the rest. "
           "Your memories start with your family's biggest days of photos and their latest, named by what they were "
           "(a wedding, a birthday)."),
        N_("Anyone who is someone in the family tree has these buttons, admins too (through their account, or "
           "without family accounts the person marked with This is me): theirs, then one for each of their closest "
           "family. An admin's Home doesn't put their family's photos first."),
        N_("For admins of a new LineageBox, Getting started at the top: watch your photos folder, check the AI, name "
           "yourself and press This is me, name the largest groups of faces, start the family tree, and invite someone "
           "(or turn on family accounts when you're ready). Each step ticks itself off once it is done, and the list "
           "goes when they all are; Put this list away hides it for good."),
        N_("For admins, Storage at the top shows how full the disk is; it opens the Status page, which says what takes "
           "the room."))),
    "home_person": Page(N_("You or one of your family"), (
        N_("Home as it would be of one person: their memories (their biggest days of photos, named by what they were, "
           "and this week in earlier years), then their newest photos, however long ago they were taken. Show older "
           "photos goes further back."),
        N_("The buttons near the top of Home open this page: one for you, with only photos of you, and one for each "
           "of your closest family. Their page has their family, life events and every photo of them."),
        N_("Through the years, under the memories: their face a year at a time, lined up on the eyes and playing on "
           "its own. Click it to watch it all, step through it, or pick another photo for a year."))),
    "folders": Page(N_("Folders"), (
        N_("Your uploads: open one of your folders to see its photos, share it, or move it to the Removed folder. "
           "Select photos in a folder to move only those."),
        N_("Shared with you: folders, tours and photos others in the family shared with you or with everyone. You see "
           "their photos as you see your own family's."),
        N_("What you share: everything you shared and with whom. Stop sharing takes it back at once."),
        N_("An admin sees here everything anyone shares."))),
    "error": Page(N_("A page that can't be shown"), (
        N_("What was asked for isn't here: a photo or person removed or merged since the link was made, one outside "
           "your part of the family, or an address typed wrong. The start page has everything you can see."),)),
    "signin": Page(N_("Signing in"), (
        N_("Sign in with the Google account of the email address you were invited with."),
        N_("Or with a passkey: Sign in with a passkey, then unlock your phone or computer as you always do (your face, "
           "fingerprint or PIN). On a computer without your passkey, choose your phone in the window that opens. Your "
           "first passkey comes from a link an admin sends you."),
        N_("At home, when the internet is down, an admin can sign in with the code from their authenticator app."))),
    "signin_demo": Page(N_("Trying the demo"), (  # the sign-in page of a demo (demo.py)
        N_("Sign in to demo makes an account just for you, as someone in this made-up family, and signs you in. Nothing "
           "else is needed."),
        N_("Change what you like: name faces, fix dates, add to the family tree. Everyone trying the demo sees those "
           "changes, until it all goes back as it was. A few photos of your own can be uploaded too: only you see them, "
           "and they are deleted then."))),
    "passkey_invite": Page(N_("Making your passkey"), (
        N_("This page comes from a link an admin sent you. Make my passkey asks your phone or computer to make a "
           "passkey for this site: unlock it as you always do, and it keeps the passkey. You are then signed in."),
        N_("Make it on your own phone or computer: whoever unlocks it later signs in as you. The link works once."),
        N_("If your phone offers to keep the passkey in a password manager (iCloud Keychain, Google Password Manager), "
           "say yes: your other devices then have it too, and a new phone gets it back."))),
    "passkeys": Page(N_("Passkeys"), (
        N_("Your passkeys: each signs you in with the lock of a phone or computer, instead of Google. Rename one so "
           "you know which is which; Take it away stops it signing in and signs out wherever it signed you in (a lost "
           "phone)."),
        N_("Add a passkey makes one on the phone or computer you are using. For safety it works only soon after "
           "signing in (15 minutes, or 3 hours after your very first sign-in): otherwise sign out and in again."),
        N_("On a computer without a passkey, you can also sign in with your phone's: choose your phone in the window "
           "that opens, and point it at the code the computer shows."))),
    "upload": Page(N_("Uploading"), (
        N_("Drop photos, videos or whole folders on the box, or pick them. They go into your own folder, and "
           "LineageBox reads them within a minute or so: then faces are found and named, and the AI describes them."),
        N_("Whoever is in your photos sees them too. A photo with nobody in it is seen only by you (and the admins), "
           "until you share it: open your uploads folder on Folders to share a folder, or a photo's page to share it."),
        N_("Name a folder to keep a lot together (Christmas 2025); dropped folders keep their own names."),
        N_("On an Android phone with LineageBox on its home screen (over HTTPS, the browser's menu → Install app or "
           "Add to Home screen), share photos from the gallery and pick LineageBox: they come here and go up at "
           "once."))),
    "cleanup_damaged": Page(N_("Damaged files"), (
        N_("Files a disk spoilt: cut short, with a blank stretch where a block couldn't be read, or with other bytes "
           "than when they were catalogued, though nobody changed them. The worker reads every file whole once, a "
           "while each pass, then each new file."),
        N_("Tick and move them to the Removed folder, or put a good copy over the damaged file (from a backup): the "
           "worker reads it again. Fine as it is takes one off the list for good."))),
    "cleanup_changes": Page(N_("Recent changes"), (
        N_("What was changed on the site, by family members and admins: faces named, people merged or removed, tags, "
           "dates, captions and comments, people added to photos, family links, details, what a folder means. Undo "
           "puts one back while nobody has changed the same thing again since. Family members' only shows just "
           "theirs. Changes are kept for 90 days."),
        N_("A family link can let someone see more of the family than they should (marking a brother's wife as your "
           "mother makes her parents your grandparents): those are marked, with whom each account now sees."))),
    "cleanup_same": Page(N_("Same person?"), (
        N_("People a family member added who may already be here: the same name, another name, or the same first and "
           "last name. Merge them if they are one person; tick whether whoever added them goes on seeing them (a "
           "relative mapped wrong, or a friend from another family). Link them from the person's page if they are "
           "related, or say they are different people."),
        N_("Then two people anyone added whose faces named by hand look like one person's, though they were never "
           "named in one photo together and the family tree doesn't link them: often one person named twice, under a "
           "nickname or a misspelling. A merge can be undone on Recent changes."))),
    "cleanup_camera_clock": Page(N_("Camera clocks"), (
        N_("A camera whose clock was set wrong dates every photo it takes wrong until someone sets it again, and a "
           "clock gains or loses a little as the months go by. The cameras whose photos show people before they were "
           "born come first: that is the usual sign, and ⚠ marks those photos."),
        N_("Pick a camera to see its photos in the order it took them, a month at a time, with the time its clock "
           "gave each one. Click the first photo it dated wrong and say its dates are wrong from there, then click "
           "the last one (or say every photo after it). Then click one of them you know the date of and say when it "
           "was really taken: they all move as much as that one."),
        N_("Its clock gained or lost time as it went? Click another of those photos further on and date it too: the "
           "photos between the two move in step. The panel for a photo put right can also start or end the wrong "
           "dates there, forget a date you gave, or put them all back."),
        N_("Dates set by hand stay. Photos of that camera read later are moved the same way. Recent changes can undo "
           "each change."))),
    "cleanup_dates": Page(N_("Suggested dates"), (
        N_("A scanner, or a phone's scanning app, dates what it scans by the day it was scanned: an obituary from 1952 "
           "scanned this year is dated this year. Documents lists the scanned documents (and documents with no date "
           "of their own, or marked as a document on their page); Scanned photos, the old prints scanned the same "
           "way. A scan is known by its scanner's name, or a folder or file named for scans."),
        N_("Under each, what can tell its real date, best first: a date written on it (the AI reads each document "
           "once, in the hours it describes photos, and Read the date on it asks now), a date the AI's description "
           "saw on it, the family tree (a marriage certificate takes the marriage's date), and who is in it (not "
           "before anyone in it was born). Date it uses one; or type the date as you know it, as on a photo's page."),
        N_("Looks too young: photos, scanned or not, where someone named looks as they did as a child while the "
           "photo's date makes them grown up: an old print photographed with a phone, a copy dated when it was made. "
           "A face looks like their childhood when it looks like a young child's, or most like their own photos as a "
           "child (photos dated when they were 12 or younger); the date suggested is about when those photos were "
           "taken. It finds more as you name and date their childhood photos. If it isn't them, Not them takes the "
           "name off."),
        N_("A dated scan leaves the list, and Recent changes can undo the date. Its date is right leaves the date as "
           "it is and takes it off the list."))),
    "accounts": Page(N_("Family accounts"), (
        N_("Each account is an email address tied to someone in the family tree. A member sees their own part of the "
           "family: their ancestors, every blood relative, the partners of those and their children, and their own "
           "partner's side; the photos those people are in; and what they upload. An admin sees everything."),
        N_("Add an account is at the top of the list. Each account below shows its person's name: click it to change "
           "who they are, their role and their exceptions; it stays open while you do. Find an account narrows the "
           "list as you type a name, an email address, admin or member."),
        N_("Exceptions show someone more, or less, to one account: alone, or with their family."),
        N_("Passkeys, for those without a Google account: Make a passkey link on an account gives a link (and a QR "
           "code for their phone's camera) that makes their first passkey. It works once, for a week (a day for an "
           "admin's account). Their passkeys are listed on their account, each with Take it away. Passkeys need HTTPS, "
           "the Address from outside, and that name listed exactly under Host names, with no name with *: Passkeys, in "
           "Sign-in, says what is missing."),
        N_("See as shows the site exactly as that member sees it, until you stop; nothing can be changed meanwhile."),
        N_("Sign in with Google needs a client from Google Cloud: its redirect address is shown here to paste "
           "there."))),
    "codes": Page(N_("Codes for signing in at home"), (
        N_("When the internet is down, Google can't say who is signing in. An admin at home can then sign in with "
           "their email and the 6-digit code from an authenticator app on their phone (Google Authenticator and "
           "others)."),
        N_("Make a code shows a QR code once: scan it with the app, then type the code the app shows to check it "
           "works. Keep the key somewhere safe. Making a new code, or taking it away, stops the old one working (a "
           "lost phone)."))),
    "folder": Page(N_("A folder"), (
        N_("The folder as it is on disk: its photos and the folders in it. Photos found in more than one folder are "
           "the same photo: each shows wherever it has a copy."), IN_PIECES,
        N_("Select photos ticks photos to date them all at once (scans from one roll of film), tag them or add someone "
           "to them. Moving ticked photos to Removed takes only their files in this folder; copies elsewhere stay."),
        N_("Sharing and removing, at the top, of a folder that is yours (a family member's: their uploads): share it "
           "with one family account or with everyone who has one, and they see its photos, those added later too. Move "
           "this folder to Removed takes it with everything in it; it can be put back from Cleanup → Removed."),
        N_("What this folder means (admins): the place, occasion, date and names its name was read as, which every "
           "photo in it and below takes on. Put a misread one right; it is then never read again by the AI, and Read "
           "the name again forgets it."),
        SELECTING)),
    "timeline": Page(N_("Timeline"), (
        N_("Every year with photos, newest first. The bars show January to December, so busy and quiet months stand "
           "out. Open a year to see its photos month by month."),
        N_("Under each year are its events: its biggest days of photos, named by what they were (a wedding, a "
           "birthday, the event a folder is named for) or by their dates. The two latest years show theirs; point at "
           "another year to see its events, and click one to see its photos."),
        N_("Photos dated only by their file's time can be in the wrong year: Status → Where dates come from lists "
           "them."))),
    "year": Page(N_("A year"), (
        N_("The year's events at the top (its biggest days of photos: click one to see them), then its photos, month "
           "by month. The months at the side (along the top on a phone) jump to each one; more photos load as you "
           "scroll down."), SELECTING), (
        ("← →", N_("the year before or after")),)),
    "map": Page(N_("Map"), (
        N_("Where photos were taken (by their GPS, or the place in their folder's name) and where family events "
           "happened. The bars show photos per year: dark for those with a place, light for those without."),
        N_("Pick a year with the slider, or a bar, to see only that year. Click a dot for its photos and events."),
        N_("Places from folder names and events are looked up on OpenStreetMap when that is on in Settings; one put in "
           "the wrong spot can be moved by typing where it really is."),
        N_("Places from folder names start as not checked. Press Right place on one that is where it belongs, and it "
           "moves to the confirmed ones; untick a kind in the box at the top right to see only those left to check."),
        N_("Photos with GPS are named after the nearest town, from a list that comes with LineageBox (nothing is asked "
           "online for that)."),
        N_("To put a photo on the map yourself, open it and pick Put it on the map in its Details (admins: a folder, "
           "from the folder's page). Click where it was taken, or find a town by name, drag the pin to the spot and "
           "press Save; Undo in the message puts it back. A place given this way wins over the photo's GPS and its "
           "folder's."))),
    "people": Page(N_("People"), (
        N_("Everyone named, the people in the most photos first; Show more shows the next ones. Type in the find box "
           "to look among all of them by name, other name, relationship or group."),
        N_("Name faces asks about the faces nobody has named or checked yet, one at a time. Cleanup → Confirm faces "
           "lists them all at once."))),
    "name_faces": Page(N_("Name faces"), (
        N_("One face at a time, the whole photo beside it with the face outlined. “Is this …?”: Yes names the face as "
           "them; Not them says it isn't them, and then who else it may be is asked, or who it is; Skip leaves it for "
           "later. A face nobody looks enough like has a box to type who it is in, and Don't know, after which you "
           "aren't asked about it again. Recognition learns from every face you name."),
        N_("The faces come in this order: those someone in the family asked about (Who is this?, on Home), then those "
           "in photos with people already named that look like someone else, then those recognised automatically that "
           "nobody has checked, the least certain first, and last the groups of faces that look like one person, to "
           "name all at once (Don't know them sets aside a group nobody in the family knows). A group whose faces "
           "together look like someone already named asks “Are these …?”: Yes names them all. The buttons at the top "
           "show one kind only."),
        N_("Not a face hides a pattern, a statue or a poster. Ask the family puts a face on Home for everyone who sees "
           "its photo. Open the photo shows it with the others in it. Tapped the wrong answer? The message that says "
           "what it did has Undo beside it.")),
        keys=(("→", N_("skip, as S does")),)),
    "cleanup_faces": Page(N_("Confirm faces"), (
        N_("Seen together: unnamed faces in photos where someone is already named, grouped by who they look most like. "
           "Nobody is in a photo twice, so each face is compared only with the people not named in its photo yet: next "
           "to one brother, a boy who looks like both brothers is suggested as the other. Untick any face that isn't "
           "them, then confirm: the ticked faces are named, and the unticked ones are never suggested or recognised as "
           "them again."),
        N_("Photos to finish: photos and videos where two or more people are named and someone else isn't yet. Type "
           "who each face is or take the guess; “don't know” stops asking about someone nobody knows. In a video, "
           "naming someone also names their other faces in it that clearly match, and such faces are named by "
           "themselves as soon as the video has a face of that person named."),
        N_("Unnamed groups: faces nobody has named, grouped by likeness, the largest first, down to groups of two or "
           "three. Click × (not them) on any face that doesn't belong, then type who it is; a name already taken adds "
           "the faces to that person. When the faces together look like someone already named, their name is filled "
           "in: Enter takes it. Don't know them sets aside a group nobody in the family knows."),
        N_("Ages to check: faces named as someone who can't be them by their age, because the photo is dated before "
           "they were born, or the face looks like a young child's while they were grown up and the photo's date was "
           "set by hand or said to be right (else it is under Suggested dates → Looks too young). Usually the name is right "
           "and the photo's date is wrong, often a whole camera's: Camera clocks puts those right. Not them takes the "
           "name off; for an admin, Right as it is stops listing it."),
        N_("Faces missed: group photos where some faces were found and the description counts more people. Look for "
           "faces again looks more closely; family photos come first. Photos with no face found at all are under "
           "Cleanup → No faces found."))),
    "cleanup_no_faces": Page(N_("No faces found"), (
        N_("Photos where the AI's description sees people but no face was found in them, the newest first. Hover over "
           "a photo for what the description says."),
        N_("Look again: the ticked photos are looked at again more closely (at twice the size, keeping fainter faces), "
           "in the background, a few a minute; the faces found are recognised where they clearly match. No faces to "
           "find: their people face away or are too far off, and they aren't listed again; Undo, or Recent changes, "
           "lists them again. Either takes them off this list."),
        N_("The worker also looks closely at every photo on its own, some each pass (Settings → Faces), and at a photo "
           "turned when no face is found: a photo it has looked at leaves this list."))),
    "person": Page(N_("A person"), (
        N_("Everything about one person: their name, sex, birth, family and other relationships, life events, "
           "documents and photos."),
        N_("Family: enter only what you know directly (parents, children, siblings, partners, friends); nieces, "
           "cousins and in-laws are worked out. A new name adds that person. While fewer than two parents are entered, "
           "a box in the list adds one; a child with one parent entered offers their parent's partners as the other."),
        N_("Life events: births, deaths, weddings, moves, with dates as exact as you know them (1984-06-12, June 1984, "
           "about 1950, between 1950 and 1955). Press the number of an event to change it."),
        N_("After adding a life event, the Add box is ready for the next one."),
        N_("At the top, their portrait: Choose the portrait picks the face People, the Family page, the tree and "
           "exports show for them, from the faces named as them. Through the years, beside it, plays their face a "
           "year at a time."),
        N_("Life story: what the family knows of their life, in your own words. Anyone who can see them can write it, "
           "and family tree programs get it as a note. Notes, under it, hold where things came from: what a reading "
           "added, notes from another family tree."),
        N_("Adopted or fostered: add them as an adopted son, a foster daughter, an adoptive mother and so on, or "
           "change “by birth” beside a parent or child. Relationships say so (adoptive mother, foster brother); "
           "adoption makes family as birth does, so an adoptive mother's mother is a grandmother."),
        N_("Sources: books, websites and records that bear out what is here (the 1940 census, page 12), for them or "
           "one of their life events. One typed again by the same title is the same source."),
        N_("Life, by age: their life events and photos, grouped by how old they were (by year, without a birth date). "
           "Open a group to see its photos; a few photos in all show at once, and more load as you scroll. Select "
           "photos, above the groups, ticks their photos to tag them, date them or put them in an album together."),
        N_("With no birth date, Born may say between which years the photos say they were born: their face looks like "
           "a young child's in some of them. A face's look can't tell an adult's age, so it says nothing for "
           "grown-ups."),
        N_("Stories: recordings of the family telling about them, or about one of their life events (pick it under "
           "About). Anyone who can see the person can record one; what is said is written down automatically, when "
           "that is set up."),
        N_("For admins, at the bottom of the page: Remove, for someone added by mistake or a name you don't want any "
           "more. Faces named as them lose the name and go back to the unnamed faces, to be named as whoever they are; "
           "their family links, life events and other names go with them. Someone with an account stays until the "
           "account is removed. For someone here twice under two names, use Merge instead."))),
    "person_years": Page(N_("Through the years"), (
        N_("Someone's face a year at a time, each photo lined up on the eyes and fading into the next. Press Play (or "
           "p), step with ‹ › or the arrow keys (or swipe the face on a phone), or pick a year in the strip under it. "
           "Speed sets how long each year shows."),
        N_("Every photo takes their clearest face each year from any photo: large, facing the camera and sharp, named "
           "by hand before recognised. Portraits takes only photos the AI described as a portrait or a selfie, or "
           "tagged portrait, selfie or headshot, with nobody else as near the camera: the faces follow on more "
           "smoothly, but there are fewer years. The number beside each says how many years it finds."),
        N_("Photos with no date of their own, or dated across more than two years (the 1960s), and faces too small "
           "to line up are left out. With a birth date, each year shows how old they were."),
        N_("Another from this year shows the year's next clearest face, and this browser remembers it. A face that "
           "isn't them: open the photo and take the name off it, and it leaves here too."))),
    "person_faces": Page(N_("Checking faces"), (
        N_("The faces recognised as this person, the least certain first, and the ones you named. Click × (not them) "
           "on any face that isn't them: the name comes off, it is never recognised as them again, and it can be named "
           "as whoever it is on its photo."),
        N_("On a face recognised automatically, ✓ says it is them: it then counts as named by you, and recognition "
           "learns from it. Yes, the ticked are … does that for every face still ticked; untick any you aren't sure "
           "of, and they stay as they are."))),
    "family": Page(N_("Family"), (
        N_("At the top, for admins: read into the family tree. Paste an obituary, an announcement, a letter or notes, "
           "or add photos of one; the AI lists what it would add, and you approve each change."),
        N_("The family around one person, from their grandparents down to their children, starting with you (the "
           "person marked “This is me”). Click anyone to put them in the middle, to climb up or down the tree a "
           "generation at a time; double-click someone to open their page. The line above says the way back to you."),
        N_("View in classic family tree style opens the whole line down from one ancestor on a page of its own, to "
           "move around and save as a picture."),
        N_("Below, folded away: everyone by relationship, friends and pets, acquaintances (teachers, friends, bands) "
           "by group, and those not placed yet. Point at a group's name to see who is in it, or click it to keep it "
           "open."),
        N_("Family tree programs, folded away at the bottom: Export downloads the tree as a file Ancestry, "
           "FamilySearch, MyHeritage or Gramps open (with each person's portrait if you like); for admins, Import "
           "brings one in, its people matched to those here, and you check each match before anything changes."))),
    "family_tree": Page(N_("The whole line down from one ancestor"), (
        N_("Everyone descended from one ancestor, with their partners: pick whom it starts from at the top. A bold "
           "frame is the family line; a plain one married in; a dashed one a former partner."),
        N_("Drag the tree to move around it, or use the arrow keys once it is clicked. Scroll or pinch to zoom in and "
           "out; Show it all fits the whole tree in the frame. Click someone to open their page."),
        N_("Save as a picture downloads the whole tree as one large PNG, however much of it shows.")), (
        ("← → ↑ ↓", N_("move around the tree (click it first)")),)),
    "family_names": Page(N_("Names to check"), (
        N_("Family tree programs keep given names and surnames apart. Each name here was split at its last word; fix "
           "the ones that are wrong and confirm the rest."),)),
    "family_read": Page(N_("Read into the family tree"), (
        N_("Paste an obituary, a wedding or birth announcement, a letter or notes, or add photos of one. The AI reads "
           "it and lists the changes it would make to the family tree, for you to tick."),
        N_("It takes a minute or two; you can leave the page meanwhile. Readings stay here, to look over later."))),
    "family_read_review": Page(N_("What it read"), (
        N_("Each person the text names, who they are in the tree, and the changes the text suggests for them. Check "
           "who is who first (an unsure match is marked; pick someone, someone new, or leave them out): the changes "
           "follow."),
        N_("Ticked changes add what the tree lacks. Where the text and the tree differ, the change is unticked and "
           "says what the tree has: tick it only if the text is right. Changes for someone new are made only with "
           "them. Your ticks are kept as you click them, also when picking who someone is loads the page again."),
        N_("Once the ticked changes are made, the page shows only what changed. Someone it added by mistake can be "
           "removed there with Remove from the tree."),
        N_("What the AI copied from photos can be corrected under What was read, then read again; reading it again "
           "also lists what wasn't ticked once more."))),
    "family_import": Page(N_("Bringing in a family tree"), (
        N_("Who in the file is who here: the unsure matches come first. Pick the person they are, or “add as new”. "
           "Nothing changes until you apply it, and what is already here is kept where the file disagrees."),)),
    "tours": Page(N_("Tours"), (
        N_("Slideshows made by rules. A tour picks its photos again each time it is opened, so new photos join it."),
        N_("Suggested tours are picked from your own photos: the people in the most of them, the two most often "
           "together, busy years and seasons, birthdays coming up, and occasions whose tags come back every year. Make "
           "this tour saves one; it can be changed or deleted like any other."),
        N_("Start one from a kind of tour (a person's year, a season, anniversaries, a family line); kinds can be "
           "changed or added in Settings."),
        N_("Shared with you: tours others in the family made and shared with you. You see each as it shows its photos "
           "to whoever made it."),
        N_("Albums, below your tours: photos picked by hand instead of by rules. All albums opens Albums."))),
    "tour": Page(N_("A tour"), (
        N_("Its photos, best of each group of similar shots; your favourites (♡ on a photo) come before others like "
           "them. Play shows them full screen, after a title card with the tour's name and the time its photos span. "
           "Each photo shows its caption, who is in it and when it was taken, as exactly as that is known (“Summer "
           "1987”); a video plays to its end."),
        N_("Save as a video makes the slideshow into one video file to send: LineageBox makes it in a few minutes, and "
           "the page says when it is ready to download. It is kept for 30 days; Make it again makes a new one."),
        N_("A tour opened from Home's memories isn't saved until you press Keep as a tour."),
        N_("Sharing, at the bottom of your own tour: share it with one family account or with everyone who has one. "
           "They see the photos it picks, also those of your family they couldn't see before, and the tour keeps "
           "picking new ones for them. Photos others shared with you are not passed on."),
        N_("Its photos load as you scroll down. The years and months at the side (along the top on a phone) jump to "
           "them: the one you are in is marked. Select photos, on a saved tour, ticks them to work on together."),
        SELECTING), (
        ("Space", N_("pause or go on (in the slideshow)")), ("← →", N_("previous or next photo (in the slideshow)")),
        ("Esc", N_("leave the slideshow")))),
    "albums": Page(N_("Albums"), (
        N_("Albums hold photos picked by hand, in the order you want them, with a cover and a caption for each: a "
           "wedding, a reunion, a trip. Tours pick their photos by rules; an album holds just what you put in it."),
        N_("Make one here by its name, or tick photos on any page of photos and type an album's name in the bar at the "
           "bottom (a new name makes a new album). A photo's page adds it too, under Albums."),
        N_("Shared with you: albums others in the family made and shared with you."),
        N_("Your favourites: the photos you gave a heart (♡ on a photo's page)."))),
    "album": Page(N_("An album"), (
        N_("Its photos in its own order, each with the album's caption for it; where the album has none, the photo's "
           "own caption shows. Play shows them full screen, after a title card; Save as a video makes one video file "
           "of it to send."),
        N_("In your own album: drag photos into order (or use ↑ and ↓), ★ makes one the cover, type a caption under a "
           "photo and press Enter, and × takes a photo out of the album (it stays in your photos; Undo puts it back)."),
        N_("Sharing, at the bottom of your own album: share it with one family account or with everyone who has one. "
           "They see its photos in its order, also those of your family they couldn't see before. Photos others shared "
           "with you are not passed on.")), (
        ("Space", N_("pause or go on (in the slideshow)")), ("← →", N_("previous or next photo (in the slideshow)")),
        ("Esc", N_("leave the slideshow")))),
    "favourites": Page(N_("Your favourites"), (
        N_("The photos you gave a heart (♡ on a photo's page), the latest first. Tours pick a favourite before other "
           "photos like it, and memories on Home use one as their cover."),
        N_("Play shows them as a slideshow; Make an album of them puts them in an album you can order, caption and "
           "share."))),
    "book": Page(N_("A family book"), (
        N_("A book of one person, or of a person and everyone descended from them: a cover, their portrait, their life "
           "story, life events, family, documents, and their best photos year by year (favourites first), with the "
           "family's captions."),
        N_("Print or save as a PDF opens the browser's print window: choose Save as PDF as the printer for a file to "
           "send or take to a print shop. Each person starts on a new page."),
        N_("It is made from the family tree and the photos as they are now: name more faces, add life events or "
           "captions, and the book shows them."))),
    "tour_wizard": Page(N_("Making a tour"), (
        N_("Answer the questions; the line at the bottom says how many photos the answers so far give."),)),
    "tour_type": Page(N_("A kind of tour"), (
        N_("The rules a kind of tour starts from. Leave a choice on “ask each time” to choose it whenever a tour of "
           "this kind is made."),)),
    "tags": Page(N_("Tags"), (
        N_("Keywords given to photos, by the AI as it describes them or by you. Type to find one; open one to see its "
           "photos and work on them together."),)),
    "tag": Page(N_("A tag"), (
        N_("The photos with this tag, oldest first, and tags often given with it."), IN_PIECES,
        N_("Select photos to tag them with something else, add someone to them, take this tag off them (for good: the "
           "AI won't give it back), have the AI describe them again, or move them to Removed."), SELECTING,
        N_("For admins, “Rename, merge or remove this tag”: a new name changes it on every photo; another tag's name "
           "puts the two together as one (asked first); Remove from every photo takes it off them all for good, and "
           "the AI doesn't give it again. When the AI later says the old name, it means the tag as it is now. Each can "
           "be undone at once, or from Cleanup → Recent changes."))),
    "search": Page(N_("Search"), (
        N_("People whose name (or other name) matches come first, then photos. Every word must match a description, "
           "tag, place, occasion, folder name, person, date, a caption or comment someone wrote, what is said in a "
           "video, a document's text or words the AI read in the photo (a sign, a cake): “beach 2014”, “Emma snow”. "
           "The last word may be the start of one: “birthd” finds birthdays."),
        N_("Search also finds photos by what the words mean, when the AI's computer is on: “kids sledding” finds "
           "children on a snowy hill, though the words aren't the same. Names, family words and years must still be in "
           "the photo: “Emma sledding” is Emma's photos only. When that computer is off, search finds words only."),
        N_("As you type, the people, family words (“cousins”, “grandma”), places, tags and years that fit are listed "
           "under the box: pick one with a click or a tap. A person, place or year picked is added as a filter, so "
           "picking two people finds the photos they are in together, with no words at all."),
        N_("The buttons narrow the photos down (only photos or only videos, faces nobody has named yet, photos with no "
           "date, years and a place) and sort them: best match, newest or oldest first. Each filter on the search "
           "shows at the top; click it to take it off. On a photo, ← and → go through these same results, in the same "
           "order."),
        N_("When nothing is found, the page suggests what would find something: a word spelt like a name in your "
           "photos, or the search without one of its words or filters."), SELECTING),
        keys=(("↓ ↑", N_("in the search box: pick one of the people, places, tags or years it suggests; Enter takes "
                         "it, Esc closes the list")),)),
    "photos": Page(N_("Photos from Status"), (
        N_("The photos one line of the Status page counts."), SELECTING)),
    "image": Page(N_("A photo"), (
        N_("The photo, the family's caption and comments, what the AI says about it, its tags, the people in it and "
           "its files."),
        N_("The faces found on the photo are outlined, with their names: click or tap one to name it, or to fix its "
           "name. On a phone, swipe to step, tap the photo to see only it, pinch or double-tap to zoom, and slide the "
           "details up from the foot. With a mouse, double-click (or Ctrl and the wheel) zooms."),
        N_("The caption is the family's own words: anyone who can see the photo can write or change it, and it shows "
           "above the AI's description, in search and in tours. Comments go under it; whoever wrote one, or an admin, "
           "can change it or take it away."),
        N_("Tags: × takes one off for good; type in “+ tag” to add one. People: name a face by typing who it is; add "
           "someone whose face wasn't found with “Someone else here?”. “Fix” on a named face puts a wrong name right: "
           "type who it is instead, or “not them” to take the name off so the face waits to be named. Either way it is "
           "never suggested or recognised as the wrong person again."),
        N_("An unnamed face that looks like someone not already in the photo shows their name with a ?: click it if "
           "it's them. “not a face” hides a pattern or a statue; “ask the family” puts the face on Home, under Who is "
           "this?, for everyone who sees the photo. “Look for faces again” looks more closely than the first time, for "
           "small faces in group photos; the new faces are recognised where they clearly match, and faces marked not a "
           "face come back."),
        N_("Taken: a date that is wrong (a scan dated by its folder, a camera with its clock wrong) can be put right "
           "as exactly as you know it: 1987-08-14, June 1987, summer 1987, 1987, the 1980s, about 1987, between summer "
           "1987 and summer 1988. Winter 1988 means December 1988 to February 1989 (south of the equator, set on "
           "Settings, that is summer 1988). The automatic date is kept, and “use the automatic date” goes back to it."),
        N_("A photo dated only by its file's time or its folder may show “Probably …” under Taken: the photos beside "
           "it in its folder, or who is in it, say when it was taken (Why? says how). Use it dates the photo that way, "
           "as if you had typed it."),
        N_("Describe again, under the AI's description: the AI looks at the photo again and writes a new description, "
           "soon or in the hours it works (Settings → Schedule). The caption and the tags people gave or took off "
           "stay. An admin may name another AI model to do it. More like this, further down: photos whose descriptions "
           "are closest to this one's, from other days."),
        N_("Is this a document? Mark scans of certificates, letters and records, then say whose record it is: it shows "
           "on their page as a source."),
        N_("Sharing, for a photo you uploaded: share it with one family account or with everyone who has one."),
        N_("♡ at the top makes the photo one of your favourites (h): tours pick it before others like it, and memories "
           "show it. Albums, near the foot: the albums it is in, and a box to add it to one (a new name makes a new "
           "album)."),
        N_("Stories: record what you remember about the photo, in your own words, under its comments (Record a story). "
           "On a phone the button opens its recorder; on a computer the page records with the microphone when the site "
           "is opened by its secure (https) address. What is said is written down automatically, when that is set up, "
           "and search finds it. Whoever recorded a story, or an admin, can correct its words or take it away (Undo "
           "brings it back)."),
        N_("Videos and sound recordings: what is said is written out under the player, each line a link to its moment. "
           "A line heard wrong can be put right with “fix” (left empty, it is taken out); corrections are kept and "
           "searched. A long video, such as a digitised tape, lists its chapters: where each recording on it "
           "starts.")), (
        ("← →", N_("previous or next photo")), ("Esc", N_("back to where you came from, or out of only the photo")))),
    "cleanup": Page(N_("Cleanup"), (
        N_("Lists of photos you may not want: duplicate copies, nearly black or blurry ones, screenshots and the like. "
           "Nothing is removed until you select it; removed files move to the Removed folder and can be put back."),
        N_("With a family account: faces to name, your part of the family tree, and blank, blurry and similar shots "
           "among the photos you uploaded yourself (an admin can put back what you move to Removed)."))),
    "cleanup_copies": Page(N_("Duplicate copies"), (
        N_("Pairs of folders holding identical copies of the same photos. Remove the copies from one folder; the other "
           "keeps them. Keep a pair to stop it being suggested."),)),
    "cleanup_list": Page(N_("A cleanup list"), (
        N_("Ticked photos are the ones to remove; untick any you want to keep. Keep takes a photo off every cleanup "
           "list for good."), SELECTING)),
    "cleanup_similar": Page(N_("Similar shots"), (
        N_("Groups of burst shots and copies of the same picture. The best of each group is kept; the rest are ticked "
           "for removal. Pick a kind to work through one at a time."),)),
    "cleanup_removed": Page(N_("Removed"), (
        N_("Files moved to the Removed folders, by when. Restore puts them back where they were. Emptying deletes them "
           "for good, and with them the previews and face crops LineageBox made."),)),
    "cleanup_family": Page(N_("Family tree"), (
        N_("What looks wrong or unfinished in the family tree: someone with one parent entered (with the parent's "
           "partners offered as the other), brothers and sisters with no parents, dates that can't be right, one "
           "person perhaps entered twice, parents with no sex set, and a child's parents not entered as partners."),
        N_("Photos that don't fit the dates: someone named in photos taken before they were born, or more than a year "
           "after they died, or in a photo from before a parent was born. The name may be wrong, or the photo's date "
           "(a scan often has the day it was scanned): see the photos opens them."),
        N_("Put each right here or on the person's page. “fine as it is” stops listing one that is right: a parent "
           "nobody knows, two people who only share a name."))),
    "cleanup_turned": Page(N_("Turned photos"), (
        N_("Photos on their side or upside down, found by the faces in them: all lying the same way, none upright. "
           "Each is shown turned the way the faces say. Tick the ones that look right and turn them; their files "
           "change too (the orientation tag only), so every program shows them upright. “fine as they are” stops "
           "listing the rest."),
        N_("Sure means two or more faces agree, or one face does and the AI, shown the photo both ways, agrees too. "
           "Settings → Cleanup can also list those worth a look: one face the AI hasn't agreed on yet, or faces the AI "
           "disagrees with. A photo with no clear face can't be found this way."),
        N_("Any photo can also be turned by hand, under its files on its own page."), SELECTING)),
    "cleanup_kept": Page(N_("Kept"), (
        N_("Photos and folder pairs you chose to keep: cleanup doesn't suggest them. Undo to let it."),)),
    "status": Page(N_("Status"), (
        N_("What the worker is doing and has left to do, downloads in progress, problems, and counts of the catalog."),
        N_("Work in progress: each kind of work, with a bar of how far it is (how many of how many) and the time left "
           "at the pace of the last day, counting the hours between the worker's passes. It needs a pass or two to "
           "tell; not moving lately means it waits for its turn after other steps, for the describing hours, or for "
           "its service."),
        N_("Point at a line to see what it counts. Numbers that are links open those photos, such as all photos with "
           "GPS or all dated only by their file's time."),
        N_("Storage: how full each disk is that holds the watched folders or LineageBox's data folder, and how much of "
           "it is photos and videos, removed files (until Cleanup → Removed is emptied), LineageBox's previews and "
           "catalog, and other files. It turns red when space runs low."),
        N_("Backups: when the last good copy of the catalog was made, where, how big it is and how many are kept. A "
           "line at the top says so when a copy failed, or none has been made for two days."),
        N_("Slow pages lists the pages that took more than a second to answer since LineageBox last started: where to "
           "look when the site feels slow."),
        N_("Alerts, at the top when there are any: files found damaged, a disk nearly full, a service with a problem "
           "and a newer version, as on New for you (the bell)."))),
    "settings": Page(N_("Settings"), (
        N_("The menu at the side shows one part of Settings at a time (on a phone, it is above): the watched folders, "
           "how they are read and their paths on your network; when the worker runs; the AI (Ollama), speech (Whisper) "
           "and faces; the map's place lookups; kinds of tour; the cleanup lists; how long pointing at a photo takes "
           "to show it larger; the address and HTTPS; updates; backups; and family accounts with the codes for signing "
           "in at home."),
        N_("Address and HTTPS: the host names LineageBox answers to, a reverse proxy in front of it, and its HTTPS "
           "certificate (files it can read, or uploaded). Switching HTTPS restarts the web server and moves the page "
           "to its new address. Passkeys there says whether passkeys can be set up, and what is missing if not."),
        N_("Backup: the daily copy of the catalog (on or off, its folder, how many to keep), Back up now, and Download "
           "everything: the family tree and what the family said about every photo, as one zip that other programs "
           "read."),
        N_("Photo frames: links for a tablet, a TV or a Home Assistant dashboard that show memories, a tour or an "
           "album full screen without signing in, and nothing else; each can be turned off."),
        N_("Other photo programs: XMP files beside the photos with the names, captions, tags, dates and places the "
           "family gave them, for digiKam, darktable or Lightroom (off until you turn it on), and bringing in the "
           "names, tags and dates other apps (Picasa, digiKam, Lightroom, Google Takeout) left with the photos, with "
           "what was brought in so far."),
        N_("Changes apply on the worker's next pass, which saving starts right away."))),
    "empty": Page(N_("No photos yet"), (
        N_("Getting started lists what makes a new LineageBox worth opening, first of all a folder to watch (Settings "
           "→ Folders): LineageBox then finds and reads the photos in it, and Status shows how far it is."),)),
    "news": Page(N_("New for you"), (
        N_("What happened lately that you may want to see: something shared with you, a face someone asked about in a "
           "photo you can see (Who is this?), new photos of your family once their faces are found, and words someone "
           "wrote on a photo you uploaded, are in or wrote on before. Only what you can see now is listed."),
        N_("The bell at the top says how many are new. Opening this page marks them all as seen."),
        N_("Admins also see alerts: files found damaged, a disk nearly full, a service with a problem (the AI, speech, "
           "videos, backups), and a newer version of LineageBox. They are on the Status page too."),
        N_("Nothing is sent anywhere, by email or otherwise: it is all on this page."))),
    "news_photos": Page(N_("New photos"), (
        N_("The new photos one line of New for you is about, those you can see, newest first by the day they were "
           "taken. Open one to see it, name the faces in it or write on it."),)),
    "photo_frame": Page(N_("Photo frame"), (
        N_("Memories, a tour or an album, one photo at a time, full screen, with its date and caption small at the "
           "foot: for an old tablet, a TV or a Home Assistant dashboard. Touch the screen or move the mouse for Full "
           "screen; → shows the next photo."),
        N_("An admin makes a link for a frame under Settings → Photo frames: it opens this page and nothing else of "
           "the site, without signing in, and can be turned off there."),), (("→", N_("the next photo")),)),
}


def for_page(name: str) -> Page:
    """A page's help in the viewer's language."""
    page = PAGES.get(name, Page(N_("This page"), ()))
    return Page(gettext(page.title), tuple(gettext(p) for p in page.about),
                tuple((key, gettext(what)) for key, what in page.keys))


def everywhere() -> tuple:
    return tuple((key, gettext(what)) for key, what in EVERYWHERE)


def go() -> tuple:
    return tuple((letter, href, gettext(name)) for letter, href, name in GO)


def menu() -> tuple:
    return tuple((href, gettext(name), gettext(what)) for href, name, what in MENU)
