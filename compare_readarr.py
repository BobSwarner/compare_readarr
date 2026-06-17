#!/usr/bin/env python3
"""
compare_readarr.py

Compare a Readarr (PostgreSQL) database against the actual files present on
the local filesystem, and report discrepancies in both directions:

  * MISSING  - tracked in Readarr's BookFiles table, but the file is not on disk
  * ORPHANED - present on disk, but not tracked in Readarr's BookFiles table

Expected on-disk layout (this is informational only; the comparison is driven
by Readarr's stored paths, so an unusual layout still works):

    /data/media/books/spoken/<Author Name>/[Series title]/[(Series title) (n) - ]<book title>/<book files>

Usage examples
--------------
    # Use PG* environment variables for the connection
    ./compare_readarr.py --root /data/media/books/spoken

    # Explicit connection settings
    ./compare_readarr.py \
        --root /data/media/books/spoken \
        --db-host 127.0.0.1 --db-port 5432 \
        --db-name readarr-main --db-user readarr

    # Machine-readable output
    ./compare_readarr.py --root /data/media/books/spoken --json report.json

Connection settings precedence: explicit CLI flag > PG* env var > built-in default.
The password is read from --db-password, then PGPASSWORD, then ~/.pgpass (libpq).

Requires: Python 3.8+, psycopg2 (`pip install psycopg2-binary`).
"""

import argparse
import json
import os
import sys
from pathlib import Path


# Extensions that count as actual book/audiobook content. Files on disk with
# any other extension (cover.jpg, metadata.opf, .nfo, ...) are ignored when
# looking for orphans so they don't generate noise.
DEFAULT_EXTENSIONS = {
    # audio
    ".m4b", ".m4a", ".mp3", ".flac", ".ogg", ".opus", ".aac", ".wma", ".mp4",
    # ebook
    ".epub", ".mobi", ".azw", ".azw3", ".pdf", ".cbz", ".cbr", ".djvu",
}


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def env_bool(value):
    """Interpret a string env value as a boolean."""
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def load_env_file(path, override=False):
    """Load KEY=VALUE pairs from a simple .env file into os.environ.

    Lines that are blank or start with '#' are ignored. Surrounding single or
    double quotes are stripped from values, and a leading "export " is allowed.
    By default existing environment variables are NOT overwritten (override=False),
    so the real shell environment takes precedence over the file.

    Returns True if the file was found and read, False otherwise.
    """
    if not path or not os.path.isfile(path):
        return False
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if (len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'"):
                value = value[1:-1]
            if not key:
                continue
            if override or key not in os.environ:
                os.environ[key] = value
    return True


def parse_args(argv=None):
    # First, resolve --env-file (default ".env") and load it so that the env
    # vars it defines become the defaults for the options below. Real shell
    # environment variables still take precedence over the file's values.
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--env-file", default=os.environ.get("ENV_FILE", ".env"))
    pre.add_argument("--no-env-file", action="store_true")
    pre_args, _ = pre.parse_known_args(argv)
    if not pre_args.no_env_file:
        loaded = load_env_file(pre_args.env_file)
        if loaded:
            eprint(f"Loaded settings from env file: {pre_args.env_file}")

    p = argparse.ArgumentParser(
        description="Compare Readarr (PostgreSQL) DB against files on disk.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--env-file",
        default=os.environ.get("ENV_FILE", ".env"),
        help="Path to an env file with KEY=VALUE settings (default: .env). "
        "Loaded before parsing; shell env vars take precedence over it.",
    )
    p.add_argument(
        "--no-env-file",
        action="store_true",
        help="Do not load any env file, even if .env exists.",
    )
    p.add_argument(
        "--root",
        default=os.environ.get("ROOT", "/data/media/books/spoken"),
        help="Filesystem root to scan for book files (env: ROOT).",
    )
    p.add_argument(
        "--db-host",
        default=os.environ.get("PGHOST", "localhost"),
        help="PostgreSQL host (env: PGHOST).",
    )
    p.add_argument(
        "--db-port",
        type=int,
        default=int(os.environ.get("PGPORT", "5432")),
        help="PostgreSQL port (env: PGPORT).",
    )
    p.add_argument(
        "--db-name",
        default=os.environ.get("PGDATABASE", "readarr-main"),
        help="Readarr main database name (env: PGDATABASE).",
    )
    p.add_argument(
        "--db-user",
        default=os.environ.get("PGUSER", "readarr"),
        help="PostgreSQL user (env: PGUSER).",
    )
    p.add_argument(
        "--db-password",
        default=os.environ.get("PGPASSWORD"),
        help="PostgreSQL password (env: PGPASSWORD, or use ~/.pgpass).",
    )
    p.add_argument(
        "--ext",
        action="append",
        default=None,
        help="Additional file extension to treat as book content (repeatable, "
        "e.g. --ext .m4b). If given, these are ADDED to the defaults. "
        "(env: EXT, comma-separated)",
    )
    p.add_argument(
        "--all-files",
        action="store_true",
        default=env_bool(os.environ.get("ALL_FILES", "")),
        help="Consider every file on disk (ignore the extension filter). "
        "Useful to find stray non-book files, but noisier. (env: ALL_FILES)",
    )
    p.add_argument(
        "--json",
        metavar="PATH",
        default=os.environ.get("JSON"),
        help="Also write a full machine-readable report to this JSON file. "
        "(env: JSON)",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        default=env_bool(os.environ.get("QUIET", "")),
        help="Only print the summary counts, not individual paths. (env: QUIET)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=int(os.environ.get("LIMIT", "10")),
        help="Stop after reporting this many files in each category (for "
        "testing). Summary counts still reflect the true totals. Use 0 for "
        "no limit. (env: LIMIT)",
    )
    p.add_argument(
        "--path-map",
        action="append",
        default=None,
        help="Rewrite a DB path prefix to its on-disk equivalent before "
        "comparing, as 'DB_PREFIX=DISK_PREFIX'. Use this when Readarr runs in "
        "a container and stores a different mount path than the host. "
        "Repeatable. (env: PATH_MAP, entries separated by ';')",
    )
    args = p.parse_args(argv)

    # EXT env var is comma-separated; merge it in if --ext wasn't given on CLI.
    if args.ext is None and os.environ.get("EXT"):
        args.ext = [e for e in os.environ["EXT"].split(",") if e.strip()]

    # PATH_MAP env var holds ';'-separated entries; used if --path-map absent.
    if args.path_map is None and os.environ.get("PATH_MAP"):
        args.path_map = [e for e in os.environ["PATH_MAP"].split(";") if e.strip()]

    return args


def parse_path_maps(entries):
    """Parse 'DB_PREFIX=DISK_PREFIX' strings into a list of (db, disk) tuples."""
    maps = []
    for entry in entries or []:
        if "=" not in entry:
            eprint(f"WARNING: ignoring malformed --path-map (no '='): {entry}")
            continue
        db_prefix, disk_prefix = entry.split("=", 1)
        maps.append((db_prefix.strip(), disk_prefix.strip()))
    return maps


def apply_path_maps(path, maps):
    """Rewrite the first matching DB prefix in `path` to its disk equivalent."""
    for db_prefix, disk_prefix in maps:
        if path.startswith(db_prefix):
            return disk_prefix + path[len(db_prefix):]
    return path


def get_db_paths(args):
    """Return (set_of_paths, list_of_rows) from Readarr's BookFiles table.

    Each row is a dict with path/author/book/edition for richer reporting.
    """
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        eprint(
            "ERROR: psycopg2 is not installed. Install it with:\n"
            "    pip install psycopg2-binary"
        )
        sys.exit(2)

    conn_kwargs = dict(
        host=args.db_host,
        port=args.db_port,
        dbname=args.db_name,
        user=args.db_user,
    )
    if args.db_password:
        conn_kwargs["password"] = args.db_password

    # Readarr creates tables with quoted PascalCase identifiers, so they must
    # be double-quoted in queries. The join enriches each file with its
    # author/book title for friendlier output; LEFT JOINs keep rows even if
    # metadata links are incomplete.
    query = """
        SELECT
            bf."Path"        AS path,
            am."Name"        AS author,
            b."Title"        AS book,
            e."Title"        AS edition
        FROM "BookFiles" bf
        LEFT JOIN "Editions"       e  ON e."Id"  = bf."EditionId"
        LEFT JOIN "Books"          b  ON b."Id"  = e."BookId"
        LEFT JOIN "AuthorMetadata" am ON am."Id" = b."AuthorMetadataId"
    """

    try:
        conn = psycopg2.connect(**conn_kwargs)
    except Exception as exc:  # noqa: BLE001 - surface a clean message
        eprint(f"ERROR: could not connect to PostgreSQL: {exc}")
        sys.exit(2)

    maps = parse_path_maps(args.path_map)

    rows = []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query)
            for r in cur:
                if r["path"]:
                    r = dict(r)
                    # Preserve the raw DB path; compare/report on the remapped one.
                    r["db_path"] = r["path"]
                    r["path"] = apply_path_maps(r["path"], maps)
                    rows.append(r)
    finally:
        conn.close()

    paths = {normalize(r["path"]) for r in rows}
    return paths, rows


def normalize(path):
    """Normalize a path for comparison (resolve . and .., strip trailing /)."""
    return os.path.normpath(path)


def scan_disk(root, extensions, all_files):
    """Walk the filesystem root and return a set of normalized file paths."""
    found = set()
    root_path = Path(root)
    if not root_path.exists():
        eprint(f"ERROR: root path does not exist: {root}")
        sys.exit(2)
    if not root_path.is_dir():
        eprint(f"ERROR: root path is not a directory: {root}")
        sys.exit(2)

    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not all_files:
                ext = os.path.splitext(name)[1].lower()
                if ext not in extensions:
                    continue
            found.add(normalize(os.path.join(dirpath, name)))
    return found


def main(argv=None):
    args = parse_args(argv)

    extensions = set(DEFAULT_EXTENSIONS)
    if args.ext:
        extensions.update(e.lower() if e.startswith(".") else "." + e.lower()
                           for e in args.ext)

    db_paths, db_rows = get_db_paths(args)
    disk_paths = scan_disk(args.root, extensions, args.all_files)

    # Only compare DB entries that live under the scanned root, so a DB with
    # books in other roots doesn't produce false "missing" reports.
    root_norm = normalize(args.root) + os.sep
    db_under_root = {p for p in db_paths
                     if (p + os.sep).startswith(root_norm) or p == normalize(args.root)}

    missing = sorted(db_under_root - disk_paths)   # in DB, not on disk
    orphaned = sorted(disk_paths - db_paths)        # on disk, not in DB
    matched = db_under_root & disk_paths

    # Diagnostic: if the DB has files but none fall under the scanned root, the
    # stored paths almost certainly use a different prefix (e.g. Readarr in a
    # container). Show a sample so the user can build a --path-map.
    if db_paths and not db_under_root:
        eprint("")
        eprint("WARNING: 0 DB paths fall under the scanned root "
               f"({args.root}).")
        eprint("The paths Readarr stores look like this:")
        for r in db_rows[:5]:
            eprint(f"    {r.get('db_path', r['path'])}")
        eprint("")
        eprint("If those use a different prefix than the host, remap with e.g.:")
        eprint("    --path-map '/books=/data/media/books/spoken'")
        eprint("(or set PATH_MAP in your .env). See the sample DB paths above.")
        eprint("")

    # Apply the reporting limit (0 = no limit). Summary counts below still use
    # the full lists; only the displayed/exported entries are truncated.
    limit = args.limit if args.limit and args.limit > 0 else None
    missing_shown = missing[:limit] if limit else missing
    orphaned_shown = orphaned[:limit] if limit else orphaned

    # Build a lookup so we can annotate missing files with author/book.
    row_by_path = {normalize(r["path"]): r for r in db_rows}

    # ---- Output -------------------------------------------------------------
    print("=" * 70)
    print("Readarr DB <-> filesystem comparison")
    print("=" * 70)
    print(f"Root scanned          : {args.root}")
    print(f"Files on disk         : {len(disk_paths)}")
    print(f"BookFiles in DB       : {len(db_paths)} "
          f"({len(db_under_root)} under root)")
    print(f"Matched               : {len(matched)}")
    print(f"MISSING (db, no file) : {len(missing)}")
    print(f"ORPHANED (file, no db): {len(orphaned)}")
    print()

    if not args.quiet:
        if missing:
            shown = len(missing_shown)
            suffix = f" (showing first {shown} of {len(missing)})" if shown < len(missing) else f" ({len(missing)})"
            print("-" * 70)
            print(f"MISSING - tracked in Readarr but not found on disk{suffix}:")
            print("-" * 70)
            for path in missing_shown:
                r = row_by_path.get(path, {})
                label = " - ".join(filter(None, [r.get("author"), r.get("book")]))
                print(f"  {path}")
                if label:
                    print(f"      ({label})")
            print()

        if orphaned:
            shown = len(orphaned_shown)
            suffix = f" (showing first {shown} of {len(orphaned)})" if shown < len(orphaned) else f" ({len(orphaned)})"
            print("-" * 70)
            print(f"ORPHANED - on disk but not tracked in Readarr{suffix}:")
            print("-" * 70)
            for path in orphaned_shown:
                print(f"  {path}")
            print()

    if args.json:
        report = {
            "root": args.root,
            "limit": args.limit,
            "counts": {
                "disk_files": len(disk_paths),
                "db_files": len(db_paths),
                "db_files_under_root": len(db_under_root),
                "matched": len(matched),
                "missing": len(missing),
                "orphaned": len(orphaned),
            },
            "missing": [
                {
                    "path": p,
                    "author": row_by_path.get(p, {}).get("author"),
                    "book": row_by_path.get(p, {}).get("book"),
                    "edition": row_by_path.get(p, {}).get("edition"),
                }
                for p in missing_shown
            ],
            "orphaned": orphaned_shown,
        }
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
        print(f"JSON report written to: {args.json}")

    # Exit non-zero if any discrepancy found, so it's usable in cron/alerts.
    return 1 if (missing or orphaned) else 0


if __name__ == "__main__":
    sys.exit(main())
