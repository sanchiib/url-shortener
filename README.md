# url-shortener

A minimal command-line URL shortener. No external services, no
third-party packages — just Python's standard library and a local
SQLite file for storage.

## Features

- `shorten` — generate a short code for a long URL
- `resolve` — look up the original URL for a code (and optionally open it)
- `list` — show every mapping created so far, with click counts
- Custom aliases (`--alias`) instead of an auto-generated code
- Click tracking: every `resolve` bumps a counter
- Data persists in a SQLite database, so it survives across runs and
  terminal sessions
- Shortening the same URL twice returns the existing code instead of
  creating a duplicate row

## Requirements

Python 3.9+. Nothing else — `argparse`, `sqlite3`, `urllib.parse`, and
`webbrowser` are all in the standard library.

## Installation

```bash
git clone <this-repo-url>
cd url-shortener
```

There's nothing to `pip install`. Just run `main.py` with `py`.

## Usage

```bash
py main.py shorten <url> [--alias CODE]
py main.py resolve <code> [--open]
py main.py list [--json]
```

By default the database lives at `~/.urlshortener/shortener.db`, so the
tool works the same way regardless of which directory you run it from.
Pass `--db /path/to/file.db` (before the subcommand) to use a different
location, which is also how the test suite isolates itself.

### Shorten a URL

```
$ py main.py shorten "https://github.com/psf/requests/blob/main/README.md"
c8x
```

The command prints the short code on stdout, so it's easy to capture
in a variable or pipe elsewhere.

### Shorten with a custom alias

```
$ py main.py shorten "https://example.com/pricing" --alias pricing
pricing
```

Aliases must be 3-32 characters, using only letters, digits, `-`, and
`_`. Reusing an alias that's already taken fails with an error instead
of overwriting the existing mapping:

```
$ py main.py shorten "https://example.com/other" --alias pricing
error: Alias 'pricing' is already taken (points to https://example.com/pricing).
```

### Shortening the same URL again

```
$ py main.py shorten "https://github.com/psf/requests/blob/main/README.md"
c8x
(note: 'https://github.com/psf/requests/blob/main/README.md' was already shortened as 'c8x')
```

The code is still printed on stdout; the note goes to stderr so
scripts consuming the code aren't affected.

### Resolve a code

```
$ py main.py resolve pricing
https://example.com/pricing
```

Add `--open` to also open the URL in your default browser:

```
$ py main.py resolve pricing --open
```

Resolving an unknown code fails clearly instead of crashing:

```
$ py main.py resolve doesnotexist
error: No URL found for code 'doesnotexist'.
```

### List everything

```
$ py main.py list
CODE     CLICKS  CREATED              URL
c8x           0  2026-08-29T09:24:22+00:00  https://github.com/psf/requests/blob/main/README.md
c8y           0  2026-08-29T09:24:22+00:00  https://docs.python.org/3/library/sqlite3.html
pricing       1  2026-08-29T09:24:23+00:00  https://example.com/pricing
```

Pass `--json` for machine-readable output:

```
$ py main.py list --json
[
  {
    "code": "pricing",
    "url": "https://example.com/pricing",
    "created_at": "2026-08-29T09:24:23+00:00",
    "clicks": 1
  }
]
```

### Invalid input

```
$ py main.py shorten "not-a-url"
error: Unsupported or missing scheme in 'not-a-url'. URLs must start with http:// or https://.
```

Only `http://` and `https://` URLs are accepted. Errors exit with
status code `1`; successful commands exit with `0`.

## How it works

- **Storage**: a single SQLite database (`urls` table: `id`, `code`,
  `long_url`, `created_at`, `clicks`). SQLite was chosen over a flat
  JSON/CSV file because it gives atomic writes and simple uniqueness
  constraints (`UNIQUE` on `code`) for free, without pulling in any
  dependency beyond the standard library.
- **Code generation**: each new URL is inserted first so SQLite can
  assign it an autoincrement `id`. That `id` (offset by a constant so
  early codes don't look like `"1"`, `"2"`, ...) is then base62-encoded
  (`0-9A-Za-z`) into the short code. This is the same basic idea real
  URL shorteners use — codes are short, unique by construction, and
  never require a collision-retry loop.
- **Aliases** bypass the generator entirely and use the user-supplied
  string directly as the primary key, after validating its charset and
  checking it isn't already in use.

## Running the tests

```bash
py -m unittest discover -s tests -v
```

The tests use a temporary SQLite file per test case, so they never
touch your real `~/.urlshortener/shortener.db`.

## Limitations / possible extensions

- No expiry or deletion of old codes.
- No concurrent-writer protection beyond what SQLite gives by default
  (fine for a single-user CLI tool, not for a multi-process server).
- `list` prints everything; there's no pagination or filtering, since
  a personal shortener's history is unlikely to get large enough to
  need it.
