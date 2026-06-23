# CLAUDE.md

Guidance for Claude Code (and other AI assistants) working in this repository.

## What this project is

A single-file Python utility, `compare_readarr.py`, that compares a Readarr
library stored in PostgreSQL against the files on the local filesystem. It
reports three things: files in the DB but missing on disk (MISSING), files on
disk that Readarr doesn't track (ORPHANED), and tracked files whose path
doesn't match the book's author/title (MISMATCH). There is no package, no build
step, and no test suite.

## Layout

- `compare_readarr.py` — the entire program. Standard-library only except for
  `psycopg2`.
- `sample.env` — documented template; users copy it to `.env`.
- `.env` — local config/secrets, **git-ignored**. Never commit it.
- `README.md` — user-facing documentation.

## Runtime / environment

- Targets **Linux** with Python 3.8+. The only third-party dependency is
  `psycopg2` (`psycopg2-binary`).
- Intended to run inside a `.venv` (`.venv/bin/python compare_readarr.py`).
- Reads from Readarr's PostgreSQL `readarr-main` database; it never writes to
  the DB or modifies files on disk. Keep it read-only.

## Configuration model

Settings resolve with precedence **CLI flag > shell env var > env file**. When
adding a new option, preserve this pattern:

1. Add the `argparse` argument with `default=os.environ.get("NAME", <default>)`.
2. Document it in `README.md` (the settings table) and in `sample.env`.
3. For list/bool env values, follow the existing helpers (`env_bool`, and the
   comma/`;`-split handling done after `parse_args`).

## Key implementation details

- Readarr creates PostgreSQL tables with quoted PascalCase identifiers, so SQL
  must double-quote them (e.g. `"BookFiles"`, `"Editions"`).
- Readarr stores **absolute** paths. When it runs in a container the stored
  prefix differs from the host's; `--path-map DB_PREFIX=DISK_PREFIX` rewrites
  it. The raw DB path is preserved as `db_path` on each row.
- DB paths outside `--root` are intentionally excluded from comparison so a
  multi-root library doesn't generate false positives. If 0 DB paths fall under
  the root, the script prints a diagnostic with sample stored paths.
- `--limit` (default 10) truncates only the *displayed/exported* lists; summary
  counts always reflect true totals.
- The MISMATCH check (`find_mismatches`) is a heuristic comparing the stored
  path's author directory / book folder against the book's stored author/title.
  It uses `_norm_text` (accent- and punctuation-insensitive) and tolerates a
  `Series #n - ` prefix and a trailing `(year)` on the folder. It reads no file
  contents. Author mismatches are high-confidence; title mismatches are fuzzier
  and can be disabled via `--no-title-check` (or the whole check via
  `--no-mismatch-check`). When tuning it, guard against false positives from
  subtitles and series prefixes.
- The query selects `BookFiles."Id"` (as `file_id`), `EditionId`, and the
  book's `Id` so mismatches can be acted on. The file→book link is
  `BookFiles.EditionId -> Editions.Id -> Editions.BookId`; there is no direct
  BookId on a file.
- `--emit-sql PATH` writes a reviewable script of `DELETE FROM "BookFiles"`
  statements (one per mismatch, all of them regardless of `--limit`) to unlink
  files while leaving them on disk. The script stays read-only against the DB —
  it only generates SQL. NB: Readarr's API `DELETE /bookfile/{id}` deletes the
  physical file, so direct row deletion is the file-preserving path.
- `--emit-copy PATH` writes a `/bin/sh` script of `cp -r` commands, one per
  mismatch, copying the file's containing directory (book folder) to
  `--copy-dest` (default `/data/media/Download/manual-import/`) for re-import
  via Readarr's Manual Import. Source dirs are deduped, paths are `shlex.quote`d,
  and it uses `posixpath.dirname` (the comparison paths are POSIX). Covers all
  mismatches regardless of `--limit`. `--copy-progress` switches the emitted
  command to `cp -rv` (cp has no percentage bar; rsync/pv would be needed for
  that).
- Exit code is non-zero when any discrepancy is found (cron/alert friendly).

## Conventions

- Match the existing style: standard library first, clear `--flag`/`ENV` pairs,
  comments that explain *why*. Keep it a single self-contained script unless
  there's a strong reason to split it.
- Validate changes with `python -m py_compile compare_readarr.py`. There are no
  automated tests; verify behavior manually against a real or sample DB.
