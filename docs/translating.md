# Translating LineageBox

LineageBox is written in English, and English is the only language it ships with. Everything the pages and their
scripts say goes through one function, so a family that wants LineageBox in another language can add a translation
without changing the code, and send it back to be included for everyone.

This page is for translators. How to write new text so that it can be translated is in
[development](development.md#text-people-read), and how it works in [architecture](architecture.md#other-languages).

## What is translated

- **The pages**: every template in `lineagebox/web/templates/`, including the help panel (`lineagebox/web/help.py`).
- **The scripts' messages**: `app.js`, `map.js` and `frame.js` in `lineagebox/web/static/` (Saved, That didn't work,
  the upload progress, the map's legend...).
- **Dates, numbers and sizes**: the order of a date, the names of the months and days, the thousands separator and
  decimal mark, and the units of sizes (MB, GB) are messages of their own, so a translation sets them too.

Not yet: the messages that come from the Python code (what a change that can't be made says, the Status page's
lines about the worker and the services, kinship words such as *niece*, dates such as *about 1950* in the family
tree), the offline page of the phone app (`static/offline.html`) and these documents. Those stay in English in a
translated LineageBox for now.

## The files

A language is a folder under `lineagebox/locale/`, named by its code (`de`, `fr`, `pt_BR`):

```
lineagebox/locale/
  pages.cfg, scripts.cfg            what pybabel reads, for each catalog
  de/LC_MESSAGES/
    lineagebox.po, lineagebox.mo        the pages, the help and the formats
    lineagebox-js.po, lineagebox-js.mo  the scripts' messages
```

The `.po` files are what you translate; the `.mo` files are compiled from them, and are what LineageBox reads. Both
are committed. The templates of the catalogs (`lineagebox.pot`, `lineagebox-js.pot`) are made again from the code
whenever you start or update a translation, and are not kept in the repository.

## Adding a language

You need a development copy ([development](development.md#setting-up)): `pip install -e ".[dev]"` brings
[Babel](https://babel.pocoo.org)'s `pybabel` (BSD-3-Clause), the tool these steps use. LineageBox itself doesn't need
it to run. Any `.po` editor works for the translating itself, such as [Poedit](https://poedit.net).

1. **Extract** the text, from the repository's top folder:

   ```sh
   pybabel extract -F lineagebox/locale/pages.cfg -o lineagebox/locale/lineagebox.pot --no-wrap --sort-by-file .
   pybabel extract -F lineagebox/locale/scripts.cfg --no-default-keywords -k t -k tn:1,2 \
       -o lineagebox/locale/lineagebox-js.pot --no-wrap .
   ```

2. **Start** the language (here German, `de`):

   ```sh
   pybabel init -i lineagebox/locale/lineagebox.pot -d lineagebox/locale -D lineagebox -l de
   pybabel init -i lineagebox/locale/lineagebox-js.pot -d lineagebox/locale -D lineagebox-js -l de
   ```

3. **Translate** `de/LC_MESSAGES/lineagebox.po` and `lineagebox-js.po` (see [the rules](#the-rules) below).

4. **Compile** them:

   ```sh
   pybabel compile -d lineagebox/locale -D lineagebox --statistics
   pybabel compile -d lineagebox/locale -D lineagebox-js --statistics
   ```

5. **Try it**: restart LineageBox (`serve`, or the containers after building the image). With more than one language
   installed, the round button at the top has **Language**, which each browser keeps for itself; until someone picks one there,
   each visitor gets the first language their browser asks for that LineageBox has, else `[web] language`
   ([configuration](configuration.md#web-and-network)), else English. A message left untranslated shows in English.

## Updating a translation

When the code changes, extract again (step 1), bring the new and changed text into your `.po` files, translate what
is marked new or *fuzzy*, and compile (step 4):

```sh
pybabel update -i lineagebox/locale/lineagebox.pot -d lineagebox/locale -D lineagebox --no-wrap
pybabel update -i lineagebox/locale/lineagebox-js.pot -d lineagebox/locale -D lineagebox-js --no-wrap
```

## The rules

- **The pages' text is HTML.** Keep the tags in a message (`<a href="%(href)s">`, `<b>`, `<kbd>`) and write
  `&amp;` for &, `&lt;` for <, as the English does. Quotes are fine as they are: LineageBox turns them into
  character references, so they can't break a page. (The scripts' messages, and the messages a page puts into a
  button's title or a box's placeholder, are plain text: write & and < as they are.)
- **Keep the placeholders**, in any order your language needs: `%(name)s` in the pages, `{name}` in the scripts and
  in labels such as `Move {n} to Removed`. `%%` is a % sign.
- **Plurals**: the `.po` file's `Plural-Forms` line (filled in by `pybabel init`) says how many forms your language
  has; each message with a number has one line for each. `{n}` or `%(num)s` is the number, already written your
  way.
- **Dates, numbers and sizes** have messages of their own, each with a *context* that says what it is:
  - `thousands separator in numbers, as in 12,345` (`,`) and `decimal mark in numbers, as in 3.5` (`.`);
  - `a date, as Jun 3, 2012`: `{month} {day}, {year}`, reordered as your language writes a date
    (`{day}. {month} {year}`); `a time of day, as 14:05`: `{hour:02}:{minute:02}`; `a date and its time of day`:
    `{date} {time}`;
  - the months (`month`: January..., `month, short`: Jan...) and days of the week (`weekday`, `weekday, short`);
  - `a size, as 74.8 GB` (`{number} {unit}`) and the units (`kilobytes`: KB...).
- **`the name of this language, in this language`**: write your language's own name (Deutsch, Français). It is how
  the Language menu lists it.
- **Short pieces.** Some sentences are in pieces around a number, a link or a choice: a card's count stands on its
  own, and *files are identical copies...* follows it. The `#:` lines above each message in the `.po` file name the
  template and line it comes from; open it to see a piece in its place.
- **Who it is for**: the family, most of them not technical. Plain words, as the English tries to be.

## Sending it back

Open a pull request with the `.po` and `.mo` files of your language
([contributing](../CONTRIBUTING.md#translations)). A translation is shared under the same licence as LineageBox.
