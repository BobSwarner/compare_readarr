# compare_readarr

Compare a [Readarr](https://github.com/Readarr/Readarr) library (stored in
PostgreSQL) against the files actually present on the local filesystem, and
report discrepancies in both directions:

- **MISSING** — tracked in Readarr's `BookFiles` table, but the file is not on disk.
- **ORPHANED** — present on disk, but not tracked in Readarr.

This is handy for finding books Readarr thinks it has but doesn't, or files
sitting on disk that Readarr never imported.

## Requirements

- Python 3.8+
- [`psycopg2`](https://pypi.org/project/psycopg2/) (`pip install psycopg2-binary`)
- Read access to Readarr's PostgreSQL database and to the library on disk

## Installation

```bash
git clone https://github.com/BobSwarner/compare_readarr.git
cd compare_readarr
python3 -m venv .venv
.venv/bin/pip install psycopg2-binary
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

## License

MIT
