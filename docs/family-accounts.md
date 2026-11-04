# Family accounts

With family accounts, relatives sign in with their Google account, from anywhere, and see their own part of the family
tree and the photos those people are in. They can name faces, fix dates, add to the tree and upload their own photos.
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
- the ones they uploaded themselves.

A photo with nobody in it is seen only by whoever uploaded it, and by the admins. Everything already in the library
belongs to the admins. Admins can also hide a photo, or a whole folder, from every member.

On a photo, faces of people outside the member's tree show a name with no link. Paths on the share and network paths
are never shown to members. They get a **Download** button instead.

Tags, places on the map, life events, documents, tours, search results and counts all follow from those people and
photos. Settings, Status and the Cleanup pages that move files are for admins only.

## Before you start

- LineageBox must be reachable over **HTTPS on a public name**, such as `https://photos.example.com`. Google only sends
  people back to such an address. [Remote access](remote-access.md) explains how, best with a reverse proxy.
- You need a Google account to create the sign-in client.

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

## Signing in at home without the internet

When the internet is down, Google can't say who is signing in. An admin at home can then sign in with their email and
the 6-digit code from their authenticator app. This works only from the home network, only for admins, and only 5
tries per 15 minutes. Making a new code, or taking the code away (a lost phone), stops the old one working.

## Uploads

Members upload photos, videos and whole folders from the **Upload** page. They go into
`<first watched folder>/Family uploads/<person id>/`, and the worker reads them within a minute or so. The folder's
name, which watched folder holds it, and the largest file allowed (4 GB by default) are settings on the Family accounts
page. Members can remove only their own uploads, to the Removed folder.

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
docker compose exec web python -m lineagebox accounts on                              # or off
```

A session lasts 90 days from last use (a setting). Disabling or removing an account ends its sessions at once. How
access is enforced is in [access control](access-control.md).

## Backups

The catalog database holds the Google client secret and the secrets behind the admins' sign-in codes, so keep backups
of the catalog folder as private as the photos.
