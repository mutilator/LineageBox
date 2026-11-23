# Contributing to LineageBox

Thanks for helping. Bug reports, ideas, documentation fixes and code are all welcome.

## Reporting a problem

[Open an issue](https://github.com/mutilator/LineageBox/issues/new/choose) and pick the form that fits: something
that isn't working, an idea, a question, or the documentation. The bug form asks for what you did, what you expected
and what happened, the version, how LineageBox runs (Docker or from source, the machine, where Ollama runs) and the
end of `docker compose logs worker` and `docker compose logs web`. [Troubleshooting](docs/troubleshooting.md) may
already have the answer.

Please leave out photos, people's names, email addresses and network addresses you don't want public.

## Making a change

1. Set up a development copy: [docs/development.md](docs/development.md). Switch on the
   [commit checks](docs/development.md#commit-checks) (`git config core.hooksPath .githooks`), which stop a commit
   that holds a password, a key, a real email address or a file that should stay on your machine.
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

## NAS profiles

The [setup wizard](docs/install.md#the-setup-wizard) learns each NAS from
[`lineagebox/setup/nas.toml`](lineagebox/setup/nas.toml). The file holds the user and group ids to suggest, where the
folders usually are, the steps for that NAS's own Docker app, and its models' processors and memory. If it is wrong
about your NAS, or your NAS isn't there, a pull request that changes only that file is welcome:

1. Change or add the entry. The top of the file explains each key, and the other entries show the way.
2. Run `python -m pytest tests/test_setup.py`. It checks every entry and fails on a key it doesn't know.
3. Say in the pull request where the facts come from: the vendor's documentation, or `id` and `ls -ln` on your own
   machine. Leave out your own user names, folder names and addresses.

Write the steps as the NAS's menus name them, for people who have never used SSH. Mark a number you aren't sure of with
`guess = true`.

## Translations

LineageBox ships in English only, and is ready for translations: [docs/translating.md](docs/translating.md) says
how to make one (extract the text with pybabel, translate the `.po` files, compile them) and try it. Send the `.po` and
`.mo` files of your language as a pull request, like code.

## License

LineageBox is under the [GNU Affero General Public License, version 3](LICENSE). By opening a pull request, you agree
that your contribution is shared under the same license.

## Security

If you find a way for someone to see photos or people they shouldn't, or to reach LineageBox from outside without
signing in, please report it privately, not in a public issue: [SECURITY.md](SECURITY.md) says how.
