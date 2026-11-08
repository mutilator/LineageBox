# The export format

**Settings → Backup → Download everything** (admins only), or `python -m lineagebox export <file>`, makes one zip that
holds what your family put into LineageBox, in formats other programs read without it. It is for keeping somewhere
else, and for moving to another program one day. To put LineageBox itself back as it was, use the nightly copies of the
catalog instead ([Backups](install.md#backups)): they hold everything, this holds what can't be made again.

Not in it: the photos and videos themselves (they stay where they are, and the export finds them by their paths and
SHA-256), previews, AI descriptions and tags, transcripts, and face embeddings. Another program makes its own of those.

The export is read from the catalog at one moment, while LineageBox carries on.

## Files in the zip

| File | What it is |
|---|---|
| `family-tree.ged` | The family tree as GEDCOM 7.0, as the Family page's Export makes it: people, names, life events, families, other relationships, pets and groups. Genealogy programs import it. |
| `photos.json` | Every photo and video, with what people gave it (below). UTF-8 JSON, one object. |
| `README.txt` | Which file is what, when it was made and by which version. |
| `xmp/` | An XMP sidecar for each photo file the family gave names, a caption, tags, a date or a place, at the file's path (`xmp/local/Photos/2012/IMG_1234.JPG.xmp` for `local/Photos/2012/IMG_1234.JPG`), as LineageBox writes them beside the photos when that is on ([user guide](user-guide.md#other-photo-programs)). Copied beside the photos, digiKam, darktable, Lightroom (raw files) and ExifTool read them. |
| `stories/` | The stories the family recorded, as they were recorded (WebM, M4A, MP3, WAV...), each at the `file` its record names (`stories/ab/ab12....webm`). Stored, not compressed again. |

## photos.json

One JSON object:

| Field | What it is |
|---|---|
| `format` | Always `"lineagebox-export"`. |
| `format_version` | `1`. A later version adds fields; one that changes what a field means gets a new number. |
| `made_at` | When the export was made, local time (`2026-11-07T10:15:00`). |
| `made_by` | The LineageBox version that made it (`"LineageBox 1.4.0"`). |
| `catalog_version` | The catalog's schema version it was read from. |
| `people` | Everyone in the catalog: `id`, `name`, `uid` (GEDCOM's UID, which stays the same across exports and imports) and `gedcom`, their record in `family-tree.ged` (`"@I12@"`). Photos name people by `id`. Those they have: `life_story` (what the family wrote on their page), `notes` (sources a reading added, notes from other trees), `portrait` (the face chosen for them: its photo's `photo` SHA-256 and `box`, as a face's), `parents_not_by_birth` (each `person` they were `as` `adopted` or `foster` by) and `files_elsewhere` (files another family tree linked to them, not in the library: `file` as it wrote it, `title`). |
| `sources` | Written sources (books, websites, records with no photo here): `title`, and those of `author`, `publication` and `note` it has, `uid`, `gedcom` (its SOUR record, `"@T3@"`), and `cited_for`: each a `person` (by `id`) or an `event` (as a document's, below), with `page`, where in the source. |
| `places` | Places on the map that someone confirmed, or put there by hand: `name` (as the folders or events write it), `lat`, `lon`, `found_as` (what the map service called it), `confirmed_at`, `by_hand` (moved by hand rather than looked up). |
| `placed_folders` | Folders an admin put on the map by hand: `path`, `lat`, `lon` and `at` (when). Their photos without a place of their own were taken there. |
| `folders` | Folders whose place, event or date someone set by hand: `path`, and those of `kind`, `place`, `event`, `year`, `month`, `day` that are set, and `hidden` when nothing in the folder is shown to family members. |
| `stories` | Stories recorded about a person (those about a photo are in its record): each as a photo's (below), with `person`, whom it is about, and `event` when it is about one of their life events (its GEDCOM `tag`, and `type` and `date` if it has them). |
| `albums` | The albums the family made (photos picked by hand), oldest first: `name`, `about` (the words under its name), `made_by` (`email` and `person` of the account that made it; none: the admins', or made at home without accounts), `created_at`, `photos` (in the album's order, each its `sha256` and the album's own `caption` for it, when it has one) and `cover` (the SHA-256 of its cover photo). |
| `photos` | One record for each photo, video and sound recording, in the order they were catalogued. |

Every path is relative to the media root, the folder all the photo sources are in, with `/` between folders, as the
catalog writes them: `local/Photos/2012/Zoo/DSC_0001.jpg`. Times are local, without a time zone, as cameras write
them. Each record holds only the fields it has a value for.

### A photo's record

| Field | What it is |
|---|---|
| `sha256` | The SHA-256 of the file's contents: the same photo, found anywhere, under any name. |
| `media` | `photo`, `video` or `audio` (a sound recording: a cassette, a voice memo). |
| `format` | The kind of file: `jpeg`, `png`, `nef`... |
| `files` | Every path where this photo is on the share now. One photo copied into two folders is one record with two paths. |
| `missing_files` | Paths where it was, but isn't any more: `path`, and `removed_to` when Cleanup moved it to a Removed folder. |
| `date` | When it was taken: `at`, and `source`, where that came from: `exif` (the camera), `camera` (a video's own date), `filename`, `folder` (a date in a folder's name), `copy` (the camera's date of a near-identical copy), `import` (a date another app gave it, in its XMP or IPTC or Google Takeout's file, for a photo with no date of its own), `mtime` (only the file's time, often wrong) or `user` (set by hand). A date set by hand also has `as_said` (as it was given: `"summer 1987"`, `"between 1986 and 1988"`), `from` and `to` (its first and last day; `at` is the middle) and `automatic`, the date it had before (`at`, `source`). `camera_wrote`: the date its camera wrote, when an admin put that camera's clock right (Cleanup → Camera clocks). |
| `width`, `height` | In pixels, the right way up. |
| `duration` | A video's or recording's length, in seconds. |
| `camera` | The camera or phone, as it names itself. |
| `gps` | `lat` and `lon`, where the camera or phone recorded it was taken (or, for a photo without, where another app such as Google Takeout said it was). |
| `placed` | Where someone put it on the map by hand: `lat`, `lon` and `at` (when). It wins over `gps`. |
| `place` | The confirmed place (one of `places`) that its folder, or a folder above it, names. |
| `tags` | Tags given by hand. |
| `tags_taken_off` | Tags someone took off this photo for good (an AI tag that was wrong): another program should leave them off too. |
| `faces` | Faces that have a name, or that someone said aren't someone: `box`, and `person`, `named_by`, `not_people`, `frame_seconds` (below). |
| `people` | People added to the photo by hand, by `id`: in it, but without a face to name (the back of a head, a group far away). |
| `caption` | The photo's caption: `text`, `at` (when it was written), `edited_at` (when it was last changed) and `by` (below). |
| `comments` | What the family wrote under it, oldest first, each like `caption`. |
| `stories` | Stories recorded about it, oldest first: `file` (in the zip's `stories/`), `mime`, `seconds` (its length, when known), `title`, `at`, `by` (as a comment's), `words` (what is said in it) and `words_by_hand` (`true` when someone wrote or corrected them; otherwise Whisper wrote them down). |
| `transcript_corrections` | Lines of a video's or recording's transcript that someone put right: `at` (the second the line starts at) and `text` (`""`: nobody said it). The rest of the transcript is Whisper's, and isn't exported. |
| `document` | When the photo is a record (a certificate, an obituary, a letter): its `kind` and `title`, and `records`, what it is a record of: each `{"person": id}`, or `{"event": {...}}`, one of their life events, with its GEDCOM `tag` (`BIRT`, `MARR`, `DEAT`...), and those of `type`, `date` (as GEDCOM writes it: `"3 MAR 1950"`, `"ABT 1950"`), `place` that it has, and `person` (an id) or `couple` (two ids). |
| `favourite_of` | Who gave it a heart (♡): each account's `email` and `person`, or `{"home": true}` for one given without family accounts. |
| `hidden` | `true` when it is never shown to family members, only admins (papers, medical records). |
| `unreadable` | `true` when LineageBox couldn't open the file as a photo or video. |

A face:

| Field | What it is |
|---|---|
| `box` | `[left, top, right, bottom]` as shares of the photo's width and height, from 0 to 1, with the photo the right way up (as it is shown, its EXIF rotation applied). For a video, of the frame. |
| `person` | Who it is, by `id`. |
| `named_by` | `hand` (someone named it) or `recognition` (LineageBox recognised a face someone had named elsewhere). |
| `not_people` | People someone said this face is not, by `id`. A face can have these and no `person`. |
| `frame_seconds` | For a video: how far into it the frame is, in seconds. |

Who wrote a caption or comment (`by`), when family accounts were on: `email`, the account's address, and `person`,
who that account is in the family tree.

### An example

A photo with a date given by hand, one named face, a tag, a caption and a comment (shortened):

```json
{
  "format": "lineagebox-export", "format_version": 1, "made_at": "2026-11-07T10:15:00",
  "made_by": "LineageBox 1.4.0", "catalog_version": 22,
  "people": [{"id": 12, "name": "Owen Miller", "uid": "4f1c2b7e-...", "gedcom": "@I12@"}],
  "places": [{"name": "San Diego Zoo, California", "lat": 32.73, "lon": -117.15, "found_as": "San Diego Zoo",
              "confirmed_at": "2026-11-02T10:00:00", "by_hand": false}],
  "folders": [],
  "photos": [
    {"sha256": "9b7f...", "media": "photo", "format": "jpeg",
     "files": ["local/Scans/Album 3/scan0042.jpg"],
     "date": {"at": "1987-07-16T12:00:00", "source": "user", "as_said": "summer 1987", "from": "1987-06-01",
              "to": "1987-08-31", "automatic": {"at": "2009-03-01T18:22:10", "source": "mtime"}},
     "width": 3000, "height": 2000, "place": "San Diego Zoo, California",
     "tags": ["zoo trip"],
     "faces": [{"box": [0.1, 0.2, 0.3, 0.4], "person": 12, "named_by": "hand"}],
     "caption": {"text": "Owen at the zoo", "at": "2026-11-01T10:00:00", "by": {"email": "ada@example.com"}},
     "comments": [{"text": "I remember the giraffes", "at": "2026-11-02T09:00:00"}]}
  ]
}
```

## Reading it

Any JSON reader opens `photos.json`; for a very big library, a streaming one (such as Python's `ijson`) reads it a photo
at a time. To find a photo's file on another machine, look for its `files` paths under that machine's photo folder, or
compare SHA-256s if the folders were reorganised. To bring the people across, import `family-tree.ged` into the other
program and match `people` by `uid`, or by their record (`gedcom`).
