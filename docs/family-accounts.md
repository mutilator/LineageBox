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
are never shown to members. They get a **Download** button instead.

Tags, places on the map, life events, documents, tours, search results and counts all follow from those people and
photos. Settings, Status and the Cleanup pages that move files are for admins only.

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

Members upload photos, videos and whole folders from the **Upload** page. They go into
`<first watched folder>/Family uploads/<person id>/`, and the worker reads them within a minute or so. The folder's
name, which watched folder holds it, and the largest file allowed (4 GB by default) are settings on the Family accounts
page.

Members manage their uploads from **Folders**. They open one of their folders to move it, with everything in it, to the
Removed folder, or tick photos in it to move only those. They can remove only their own uploads, and an admin can put
anything back from **Cleanup → Removed**.

## Sharing

A member shares what is theirs: a folder of their uploads, one photo they uploaded, or a tour they made. An admin can
share any folder, photo or tour. Each is shared with one member's account (a member picks among the people in their
part of the family) or with everyone who has an account.

- **A folder:** they see every photo in it and in the folders below it, including those added later. They can open the
  folder on their own **Folders** page.
- **A photo:** they see that photo.
- **A tour:** they see the tour and the photos it picks, as it shows them to whoever made it, even photos of the maker's
  family that are outside their own branch. The tour keeps picking new photos for them. Photos someone else shared
  with the maker are not passed on.

Whoever it is shared with sees those photos as they see their own family's: on Home, in search, on the timeline and
the map. People in them from outside their branch are named without a link. A photo an admin hid is never shared, and
nothing an account shared is shown while that account is switched off.

The **Sharing** panel of a folder (at the top of its page), photo or tour (at the bottom) shows whom it is shared
with, with **Stop sharing**.
**Folders** lists everything a member shares; for an admin it lists everything anyone shares. Members' shares are on
**Cleanup → Recent changes** too, with undo.

## Keeping an eye on changes

- **Cleanup → Recent changes** lists what members changed, by member and day, with **Undo**. A family link can widen
  what someone sees (marking someone as your mother makes her parents your grandparents). Changes like that are
  marked, with who now sees whom.
- **Cleanup → Same person?** lists people members added who may already be in the catalog under another name. Merge
  them, mark them as related, or say they are different people.
- On any photo, anyone can ask **Who is this?** about a face. Those faces lead the Home page for everyone who can see
  the photo.

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
passkey link's SHA-256.
