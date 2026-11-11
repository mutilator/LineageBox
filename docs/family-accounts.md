# Family accounts

With family accounts, relatives sign in with their Google account or a passkey, from anywhere, and see their own part
of the family tree and the photos those people are in. They can name faces, fix dates, add to the tree and upload their own photos.
Admins see everything, and can undo anything a member changed.

Accounts are off until an admin turns them on. While they are off, LineageBox answers the home network only and nobody
signs in. Once they are on, **everyone signs in, at home too**.

## Who sees what

A member's account is tied to their own person in the family tree. They see:

- themselves, their ancestors, and every blood relative (everyone descended from those ancestors: siblings, cousins,
  nieces, aunts and so on);
- the partners of all of those, and those partners' children. A brother's wife and her children are included, but not
  her parents;
- former partners who share a child with someone in the tree;
- their own partner's side, by the same rules, so a couple sees the same family;
- people they added themselves, such as a friend they named on one of their photos;
- any exceptions an admin set on the account: one person, or a whole branch, shown or hidden.

Of the photos and videos, they see:

- the ones someone they can see is in (a face named as them, or the person added to the photo by hand);
- the ones they uploaded themselves;
- the ones someone shared with them (see [Sharing](#sharing)).

A photo with nobody in it is seen only by whoever uploaded it, and by the admins. Everything already in the library
belongs to the admins. Admins can also hide a photo, or a whole folder, from every member.

On a photo, faces of people outside the member's tree show a name with no link. Paths on the share and network paths
are never shown to members. They get a **Download** button instead. What thumbnails say to a screen reader (who is in
each photo) names only the people the member sees.

Tags, places on the map, life events, documents, written sources, tours, albums, family books, search results (by words and by meaning) and counts all
follow from those people and photos, and so does a photo's **More like this**. Each account's favourites (♡) are its
own: nobody else sees them. Settings, Status and the Cleanup pages
that move files are for admins only, except Blank and blurry and Similar shots of a member's own uploads (below).

A member's menu has Home, People, Family, Upload and Folders (their uploads and what is shared with them) up front,
and the rest under More (Timeline, Map, Tours, Albums, Favourites, Name faces, Tags, Cleanup). Their pages say things
plainly: no file formats, programs or background work, which admins still see on the same pages (a video's codecs,
the AI model, why a file can't be read). The first steps of a new LineageBox, on Home, are for admins only.

Once a translation is installed ([translating](translating.md)), each relative sees the site in their browser's
language, or the one they pick under More → Language, kept in that browser; a photo frame follows its own browser's.
Sign-in pages follow the browser. What the family wrote (names, captions, comments, stories) is shown as written.

## Before you start

- LineageBox must be reachable over **HTTPS on a public name**, such as `https://photos.example.com`. Google only sends
  people back to such an address, and browsers only make passkeys for one. [Remote access](remote-access.md) explains
  how, best with a reverse proxy.
- For Sign in with Google, you need a Google account to create the sign-in client. Relatives without a Google account
  sign in with a [passkey](#passkeys-signing-in-without-google) instead.

## Setting it up

1. **Make yourself an admin**, tied to your own person in the tree. Also make a code for signing in when Google can't
   be reached:

   ```sh
   docker compose exec web python -m lineagebox accounts add you@example.com --admin --person "Your Name"
   docker compose exec web python -m lineagebox accounts local-code you@example.com
   ```

   Put the key it prints into an authenticator app (Google Authenticator and others: *Enter a setup key*, time based).
   Once you're signed in, **Settings → Codes for signing in at home** can do the same with a QR code.

2. **Create a Google sign-in client** at [console.cloud.google.com](https://console.cloud.google.com):
   1. Create a project.
   2. Set up its **OAuth consent screen**: External, then publish it. It only asks for `openid`, `email` and `profile`,
      so Google doesn't need to review those. **Leave out the app logo**, though: on an External app that is
      published, a logo on the consent screen needs Google's
      [brand verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/brand-verification)
      first, and that needs a public home page, a privacy policy on the same domain, and the domain verified in Google
      Search Console. A family install does without.
   3. Create an **OAuth client** of type *Web application*, with the authorised redirect URI
      `https://<your name for LineageBox>/auth/google/callback`. The Family accounts page shows the exact address to
      paste.

3. **On Settings → Family accounts:**
   - paste the client's ID and secret. The secret is never shown again;
   - set **Address from outside** to `https://<your name for LineageBox>`;
   - add your relatives' email addresses, each tied to their person in the tree, as members (or admins);
   - tick **Family accounts on** and save.

4. **Check before you tell anyone:** **See as** on the same page shows the site exactly as a member will see it,
   read-only, until you stop.

To turn accounts off again from the command line: `docker compose exec web python -m lineagebox accounts off`.

## Passkeys: signing in without Google

A passkey signs someone in with the lock of their own phone or computer: their face, fingerprint or PIN, as they
unlock it every day. There is no password, and no Google account is needed. The phone or computer keeps the passkey,
or their password manager does (iCloud Keychain, Google Password Manager), which also gives it to their other
devices. LineageBox keeps only its public key.

### What passkeys need

Browsers make and use a passkey only over HTTPS that they trust, and only on the one name it was made for. So
LineageBox lets passkeys be set up only when all of these hold. **Settings → Family accounts → Sign-in → Passkeys**,
and **Passkeys** on **Settings → Address and HTTPS**, say which is missing:

1. **HTTPS is on**: a certificate on **Address and HTTPS**, or a reverse proxy that does HTTPS, listed there under
   **Reverse proxy** ([remote access](remote-access.md)). The certificate must be one browsers trust, such as Let's
   Encrypt's or your NAS's: they refuse passkeys on a self-signed one.
2. **Address from outside** (Family accounts → Sign-in) is set to the name, such as `https://photos.example.com`. A
   name, not an IP address: passkeys are made for that name.
3. **That name is listed exactly under Host names**, and **no name with `*` is listed** there. A name with `*` makes
   LineageBox answer to many names, while a passkey works on one only.

Passkeys made before still sign in if a `*` name is added later; only new ones can't be made until it is gone.
**Changing the Address from outside** to another name leaves every passkey made for the old one unusable: everyone
makes a new one. The page says so when it is saved, and each person's old passkeys are marked on their account.

### Giving someone a passkey

1. On **Settings → Family accounts**, open their account and press **Make a passkey link**.
2. Send them the link (by text or email), or let them point their phone's camera at the QR code shown with it. It is
   shown only then. It works once, for 7 days, or 24 hours for an admin's account, which opens everything. Making
   another stops the first working; **Stop the link** does too.
3. They open it on their own phone or computer, check that it names them, and press **Make my passkey**. Their phone
   or computer asks them to unlock it, keeps the passkey, and they are signed in.
4. From then on they press **Sign in with a passkey** on the sign-in page. On a computer without their passkey, the
   browser offers to use their phone: it shows a QR code for the phone, which answers over Bluetooth.

Anyone with the link can make a passkey for that account, so send it to that person alone. The email address is still
needed: it names the account, and lets them use Google too, if they ever have it.

### More passkeys, and lost phones

Once signed in, anyone can add a passkey on another phone or computer: the round button at the top → **Passkeys** →
**Add a passkey**. Google users can add one too. For safety a passkey can be added only soon after signing in: within
3 hours of someone's very first sign-in, otherwise within 15 minutes. Someone who finds a phone left signed in can't
add one of their own that way. Later on, sign out and in again first.

A lost phone: take its passkey away on **Passkeys**, or an admin does on their account. It stops signing in, and the
browsers it signed in on are signed out. A passkey kept in a password manager comes back on a new phone signed in to
the same Apple or Google account; otherwise make them a new link.

Passkeys need no internet to sign in. At home with the internet down, they still work when the home network can find
the site by its name: give the name a local DNS entry on your router, pointing at LineageBox or its proxy.

## Signing in at home without the internet

When the internet is down, Google can't say who is signing in. An admin at home can then sign in with their email and
the 6-digit code from their authenticator app. This works only from the home network, only for admins, and only 5
tries per 15 minutes. Making a new code, or taking the code away (a lost phone), stops the old one working.

## Uploads

Members upload photos, videos, sound recordings and whole folders from the **Upload** page. They go into
`<first watched folder>/Family uploads/<person id>/`, and the worker reads them within a minute or so. The folder's
name, which watched folder holds it, and the largest file allowed (4 GB by default) are settings on the Family accounts
page.

Members manage their uploads from **Folders**. They open one of their folders to move it, with everything in it, to the
Removed folder, or tick photos in it to move only those. They can remove only their own uploads, and an admin can put
anything back from **Cleanup → Removed**. Their Cleanup page also lists **Blank and blurry** and **Similar shots**
among the photos they uploaded and nobody else has (no copy outside their uploads folder), to tick and move to Removed
the same way; Keep, and clearing every group at once, stay with the admins.

## Stories

On any photo or person they can see, a member can record a story (**Record a story**: the microphone, or a phone's
recorder), or one about one of that person's life events. Recording with the microphone in the page needs the site's
secure (https) address; the phone's recorder works either way. Members hear the stories on the photos and people they
see, and only those: one about a life event only while they see that event too. Who recorded it is named only if
they see that person. Whoever recorded a story, or an admin, can correct its words or take it away; like every
member's change it is listed under **Cleanup → Recent changes**, and Undo brings a story taken away back for a month.
Anyone who sees a video or a recording can also correct a line of what Whisper wrote down.

## Sharing

A member shares what is theirs: a folder of their uploads, one photo they uploaded, or a tour or album they made. An
admin can share any folder, photo, tour or album. Each is shared with one member's account (a member picks among the people in their
part of the family) or with everyone who has an account.

- **A folder:** they see every photo in it and in the folders below it, including those added later. They can open the
  folder on their own **Folders** page.
- **A photo:** they see that photo.
- **A tour:** they see the tour and the photos it picks, as it shows them to whoever made it, even photos of the maker's
  family that are outside their own branch. The tour keeps picking new photos for them. Photos someone else shared
  with the maker are not passed on.
- **An album:** they see the album, its photos in its order with its captions, as for a tour: even photos of the
  maker's family outside their own branch, but not photos someone else shared with the maker. They may look at it,
  play it, download it and save it as a video; only its maker, or an admin, changes it.

Whoever it is shared with sees those photos as they see their own family's: on Home, in search, on the timeline and
the map. People in them from outside their branch are named without a link. A photo an admin hid is never shared, and
nothing an account shared is shown while that account is switched off.

The **Sharing** panel of a folder (at the top of its page), photo, tour or album (at the bottom) shows whom it is shared
with, with **Stop sharing**.
**Folders** lists everything a member shares; for an admin it lists everything anyone shares. Members' shares are on
**Cleanup → Recent changes** too, with undo.

## Keeping an eye on changes

- **Cleanup → Recent changes** lists what was changed, by whom and when, with **Undo**: members' changes, and
  admins' own too (two people merged, someone removed, a group of faces named, many photos dated at once, a folder's
  meaning corrected). **Family members' only** shows just theirs. A family link can widen what someone sees (marking
  someone as your mother makes her parents your grandparents). Changes like that are marked, with who now sees whom.
  Changes are kept for 90 days, then forgotten: they leave the list and can no longer be undone.
- Right after a change, a note at the foot of the page has its own **Undo**: a member can take back their own change
  for half an hour; after that it is an admin's to undo.
- **Cleanup → Same person?** lists people members added who may already be in the catalog under another name, and
  any two people whose faces look like one person's. Merge them, mark them as related, or say they are different
  people.
- Members see **Cleanup → Confirm faces** for their part of the family, with **Ages to check** (faces named as someone
  who can't be them by their age) and **Faces missed** (group photos with faces to look for again). They can take a
  wrong name off or look again; **Right as it is**, **All found** and **Camera clocks** are for admins.
- On the page of anyone they see, members can write the **life story** and notes, choose the **portrait** (from the
  faces named as them in photos the member sees), add **sources** for them or their life events, and say a child was
  adopted or fostered. Each is on Recent changes, with Undo. **Cleanup → Family tree** shows them the checks for their
  part of the family, photos that don't fit the dates included; **Fine as it is** is for admins.
- On any photo, anyone can ask **Who is this?** about a face. Those faces lead the Home page for everyone who can see
  the photo.
- Anyone who sees a photo may ask for it to be **described again**, alone or ticked with others: the worker does it
  with the usual model (only admins pick another). Renaming, merging or removing a tag across the library is for
  admins, kept on Recent changes with Undo.
- Anyone who sees a photo may **put it on the map** (Details → Put it on the map), or move it; it is on Recent changes,
  with Undo. Putting a whole folder on the map, and writing names and places into a photo file, are for admins.
- A member's downloads (a zip of photos, or a photo's **names and caption** file beside the original) carry the
  photos' XMP sidecars with the names, caption, tags, date and place the family gave them, naming only the people the
  member can see.

## New for you

The bell at the top of the page counts, for each account, what is new since they last opened **New for you**: a share
with them (or with everyone), a **Who is this?** about a face in a photo they can see, new photos of their family once
the worker has looked for faces in them, and words written under a photo they uploaded, are in or wrote on before.
Nobody is told of what they did themselves.

Each event is written once, when it happens, but who may see it is worked out each time it is shown, from what they
may see then: a share that was stopped, a photo an admin hid since, a face named in the meantime or a comment taken
away drops off the list, and a member is never told of a photo, face or share outside what they see. Admins' alerts
(damaged files, a disk nearly full, a service with a problem, a newer version) are for admins only. Each account has
its own mark of what it has seen; an admin seeing the site as a member marks nothing for them.

Nothing is sent outside the house: there is no email and no push to phones. The events are kept in the catalog for 90
days, in a form that email or push could read later if they are ever added.

## Photo frames: device links

A photo frame, a TV or a Home Assistant dashboard shows memories, a tour or an album without anyone signing in on it,
through a
**device link** an admin makes under **Settings → Photo frames** ([user guide](user-guide.md#photo-frames-and-tvs)):

- **One view.** Memories as one account sees them (a member's only of their part of the family, as on their Home), the
  whole library's memories, a tour as whoever made it sees it, or an album (in its order, with its captions) with
  the photos whoever made it may pass on. The link sees the photos and videos on that view's
  list and nothing else: no names (the frame shows each photo's date and the family's caption), no other page, no
  API, no other photo. Every other address answers it as if it weren't there. A photo an admin hid from family
  members is on no frame, even the whole library's or an admin's album: whoever walks past a frame sees it.
- **Read-only.** It changes nothing; it can't even reach the sign-in page.
- **Revocable.** **Turn off** stops it at once. A link also stops while the account it shows memories as is switched
  off, and goes with that account or that tour when they are removed. A link to an album that was deleted shows
  nothing, also once a newer album has been given the deleted one's number. The list says when each link was last
  used and from which address.
- **Home network only, unless unticked.** A link for a frame in another house works from anywhere, at the address
  from outside (`public_url`). Whoever has such a link sees what it shows, so treat it like a key to that one view.

The link is `/d/<token>`, a random 128-bit token shown once; the catalog keeps only its SHA-256, as for sessions. It
needs no cookie, so it works in a Home Assistant dashboard's frame and on browsers too old for the rest of the site;
those frame pages may be shown inside another site's page (they have nothing to click). Without family accounts the
frame page is open on the home network like everything else (`/photo-frame`), and device links work at home only.

## Managing accounts from the command line

```sh
docker compose exec web python -m lineagebox accounts list
docker compose exec web python -m lineagebox accounts add relative@example.com --person "Their Name"
docker compose exec web python -m lineagebox accounts disable relative@example.com    # or enable, remove
docker compose exec web python -m lineagebox accounts sign-out relative@example.com   # ends their sessions
docker compose exec web python -m lineagebox accounts invite relative@example.com     # a link for a passkey
docker compose exec web python -m lineagebox accounts passkeys relative@example.com   # their passkeys
docker compose exec web python -m lineagebox accounts on                              # or off
```

A first admin without a Google account: add the account with `accounts add ... --admin`, make a link with
`accounts invite`, open it at home on your phone, then turn accounts on. Accounts can't be turned on until an admin has
a way in: Google, a passkey or a code.

A session lasts 90 days from last use (a setting). Disabling or removing an account ends its sessions at once. How
access is enforced is in [access control](access-control.md).

## Backups

The catalog database holds the Google client secret and the secrets behind the admins' sign-in codes, so keep backups
of the catalog folder as private as the photos. Passkeys add nothing secret to it: only their public keys, and each
passkey link's SHA-256. Photo frames' device links likewise keep only their tokens' SHA-256.
