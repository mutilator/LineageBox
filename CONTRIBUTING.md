# Contributing to LineageBox

Thanks for helping. Bug reports, ideas, documentation fixes and code are all welcome.

## Reporting a problem

Open an issue with what you did, what you expected and what happened. Include the end of `docker compose logs worker`
and `docker compose logs web`, and how LineageBox runs (Docker or from source, the machine, where Ollama runs).
[Troubleshooting](docs/troubleshooting.md) may already have the answer.

Please leave out photos, people's names, email addresses and network addresses you don't want public.

## Making a change

1. Set up a development copy: [docs/development.md](docs/development.md).
2. Make the change, with a test. The suite fakes the face model, Ollama and Whisper, so it needs no GPU and no
   services.
3. Run the whole suite: `python -m pytest -q`. After adding a page or an API, make sure `tests/test_access_web.py`
   passes. It checks that no route leaks photos to family members who shouldn't see them.
4. Update the documentation in `docs/` if the change affects what people see or set. If people will notice it, add a
   line to [CHANGELOG.md](CHANGELOG.md) under `## Unreleased` (at the top; start it if it isn't there), in **New**,
   **Updated** or **Fixed**.
5. Open a pull request saying what it changes and why.

A few rules shape most of the code ([details](docs/development.md#conventions)):

- what a person set by hand is never overwritten by a re-run;
- nothing is specific to one family's library: no hard-coded names, places, years or tags;
- the photo folders are only read, except by Cleanup, uploads, 720p copies and confirmed rotations;
- UI text is written for non-technical family members.

## Translations

LineageBox ships in English only, and is ready for translations: [docs/translating.md](docs/translating.md) says
how to make one (extract the text with pybabel, translate the `.po` files, compile them) and try it. Send the `.po` and
`.mo` files of your language as a pull request, like code.

## Licence

LineageBox is under the [GNU Affero General Public License, version 3](LICENSE). By opening a pull request, you agree
that your contribution is shared under the same licence.

## Security

If you find a way for someone to see photos or people they shouldn't, or to reach LineageBox from outside without
signing in, please report it privately, not in a public issue: [SECURITY.md](SECURITY.md) says how.
