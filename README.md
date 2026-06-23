# compare_readarr

Compare a [Readarr](https://github.com/Readarr/Readarr) library (stored in
PostgreSQL) against the files actually present on the local filesystem, and
report discrepancies in both directions:

- **MISSING** — tracked in Readarr's `BookFiles` table, but the file is not on disk.
- **ORPHANED** — present on disk, but not tracked in Readarr.
- **MISMATCH** — tracked in Readarr, but the associated file's path doesn't match
  the book's author and/or title — i.e. the wrong file appears to be linked to
  the book.

This is handy for finding books Readarr thinks it has but doesn't, files
sitting on disk that Readarr never imported, or books pointing at the wrong
file.

## Requirements

- Python 3.8+
- [`psycopg2`](https://pypi.org/project/psycopg2/) (`pip install psycopg2-binary`)
- Read access to Readarr's PostgreSQL database and to the library on disk

## Installation

```bash
git clone https://github.com/BobSwarner/compare_readarr.git
cd compare_readarr
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Configuration

Settings can be supplied as command-line flags, environment variables, or an
env file. **Precedence: CLI flag > shell environment variable > env file.**

Copy the provided sample and edit it:

```bash
cp sample.env .env
```

The script loads `.env` automatically (override with `--env-file PATH`, or skip
with `--no-env-file`). `.env` is git-ignored so your credentials stay local.

### Settings

| CLI flag        | Env var      | Default                      | Description |
|-----------------|--------------|------------------------------|-------------|
| `--root`        | `ROOT`       | `/data/media/books/spoken`   | Filesystem root to scan. |
| `--db-host`     | `PGHOST`     | `localhost`                  | PostgreSQL host. |
| `--db-port`     | `PGPORT`     | `5432`                       | PostgreSQL port. |
| `--db-name`     | `PGDATABASE` | `readarr-main`               | Readarr main database name. |
| `--db-user`     | `PGUSER`     | `readarr`                    | PostgreSQL user. |
| `--db-password` | `PGPASSWORD` | (uses `~/.pgpass` if unset)  | PostgreSQL password. |
| `--ext`         | `EXT`        | built-in audio/ebook list    | Extra extensions to treat as book content (CLI repeatable; env comma-separated). |
| `--all-files`   | `ALL_FILES`  | `false`                      | Consider every file on disk, ignoring the extension filter. |
| `--json`        | `JSON`       | (none)                       | Also write a machine-readable JSON report to this path. |
| `--quiet`       | `QUIET`      | `false`                      | Print summary counts only, not individual paths. |
| `--limit`       | `LIMIT`      | `10`                         | Report at most this many files per category (`0` = unlimited). |
| `--path-map`    | `PATH_MAP`   | (none)                       | Rewrite DB path prefixes to on-disk paths (see below). |
| `--no-mismatch-check` | `NO_MISMATCH_CHECK` | `false`           | Skip the MISMATCH check entirely. |
| `--no-title-check`    | `NO_TITLE_CHECK`    | `false`           | In the MISMATCH check, compare author folders only (ignore titles). |
| `--emit-sql`    | `EMIT_SQL`   | (none)                       | Write a SQL script that unlinks every MISMATCH (see below). |
| `--emit-copy`   | `EMIT_COPY`  | (none)                       | Write a shell script of `cp -r` commands for every MISMATCH (see below). |
| `--copy-dest`   | `COPY_DEST`  | `/data/media/Download/manual-import/` | Destination for the `--emit-copy` commands. |
| `--env-file`    | `ENV_FILE`   | `.env`                       | Path to the env file to load. |
| `--no-env-file` | —            | —                            | Do not load any env file. |

## Usage

```bash
# Using .env for connection settings
.venv/bin/python compare_readarr.py

# A full production run (no per-category limit)
.venv/bin/python compare_readarr.py --limit 0

# Explicit connection, machine-readable output
.venv/bin/python compare_readarr.py \
    --db-host 127.0.0.1 --db-name readarr-main --db-user readarr \
    --json /tmp/report.json
```

The script exits non-zero when any discrepancy is found, so it can be used in
cron jobs or alerting.

## Path mapping (containerized Readarr)

Readarr stores **absolute** paths in its database. If Readarr runs in a
container, those paths reflect the container's mount point, not the host's. For
example Readarr may store `/media/books/spoken/...` while the host sees
`/data/media/books/spoken/...`. When the prefixes don't match, every file
appears orphaned and the script prints a diagnostic showing the stored paths.

Rewrite the prefix with `--path-map DB_PREFIX=DISK_PREFIX`:

```bash
.venv/bin/python compare_readarr.py --path-map '/media=/data/media'
```

Or in `.env` (separate multiple entries with `;`):

```
PATH_MAP=/media=/data/media
```

## How it works

1. Reads every `Path` from Readarr's `BookFiles` table (joined to
   `Editions` → `Books` → `AuthorMetadata` for readable labels).
2. Applies any `--path-map` prefix rewrites.
3. Walks `--root` on disk, considering only known book/audio extensions
   (unless `--all-files`).
4. Compares the two sets and reports MISSING and ORPHANED files. DB paths
   outside `--root` are ignored so a multi-root library doesn't produce false
   results.
5. For each tracked file, checks that the author directory in its path matches
   the book's author, and that the book folder reflects the book's title; any
   that don't are reported as MISMATCH.

### The MISMATCH check

The expected layout puts each file under its author and a per-book folder:
`<root>/<Author>/[Series]/[Series #n - ]<Book Title>[ (year)]/<file>`. The check
compares those path components against the author/title Readarr has stored for
the book. Comparison is accent- and punctuation-insensitive, and the title
match tolerates a leading `Series #n - ` prefix and a trailing `(year)`.

It is a **heuristic** based on stored paths vs. stored metadata — it does not
read file contents. Author-folder mismatches are high-confidence; title
mismatches are fuzzier. If titles produce false positives in your library, add
`--no-title-check` to compare author folders only, or `--no-mismatch-check` to
disable the check entirely.

The MISMATCH output includes each file's `BookFiles.Id`, the value you need to
act on the association.

### Fixing mismatches (`--emit-sql`)

To disassociate a mismatched file from its book **without deleting the file**,
do it in the database — note that Readarr's API `DELETE /api/v1/bookfile/{id}`
deletes the file from disk, which is usually not what you want here.

`--emit-sql unlink.sql` writes a reviewable SQL script with one
`DELETE FROM "BookFiles" WHERE "Id" = …;` per mismatch (with the path and
reason as comments). The script itself stays read-only against the DB; you
review and apply the generated SQL yourself:

```bash
.venv/bin/python compare_readarr.py --limit 0 --emit-sql unlink.sql
# review unlink.sql, then:
pg_dump readarr-main > readarr-backup.sql     # back up first
# stop Readarr, then apply:
psql -d readarr-main -f unlink.sql
# start Readarr
```

The emitted script covers **all** mismatches regardless of `--limit`. Deleting
a `BookFiles` row removes Readarr's association but leaves the file on disk
(it will then show as ORPHANED). To stop a later rescan from re-linking it to
the same wrong book, move/rename the file into the correct book's folder or fix
it via Readarr's **Manual Import**.

### Staging mismatches for re-import (`--emit-copy`)

`--emit-copy copy.sh` writes a shell script with one `cp -r` per mismatch that
copies the **directory containing** the mismatched file (its book folder) into
a staging directory — `--copy-dest`, default
`/data/media/Download/manual-import/`. Originals are left in place, so you can
then re-import the copies against the correct book via Readarr's **Manual
Import**.

```bash
.venv/bin/python compare_readarr.py --limit 0 --emit-copy copy.sh
# review copy.sh, then:
sh copy.sh
```

Like `--emit-sql`, it covers **all** mismatches regardless of `--limit`. Folders
shared by several mismatched files are copied only once, and all paths are
shell-quoted so spaces and special characters are handled.

## License

MIT
