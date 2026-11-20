"""CLI / Debug script to run the sync pipeline against a specific folder with detailed logging."""
import argparse
import json
import os
import sys
import threading
from pathlib import Path

from .database import connect, data_dir
from .indexer import sync, local_folder
from .sync_logger import get_sync_logger, enable_console_logging, log_info


def main():
    enable_console_logging()
    parser = argparse.ArgumentParser(description="Run sync against a folder and output detailed diagnostics.")
    parser.add_argument("folder", help="Folder path containing Excel registers to sync")
    parser.add_argument("--db", default=None, help="Path to SQLite database to use (defaults to logs/debug_sync.sqlite3)")
    parser.add_argument("--app-db", action="store_true", help="Sync directly into the main app database")
    parser.add_argument("--rebuild", action="store_true", help="Force rebuild (ignore mtime/size cache)")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    if args.app_db:
        db_path = data_dir() / "index.sqlite3"
    elif args.db:
        db_path = Path(args.db).resolve()
    else:
        debug_db_dir = project_root / "logs"
        debug_db_dir.mkdir(parents=True, exist_ok=True)
        db_path = debug_db_dir / "debug_sync.sqlite3"

    folder_path = args.folder
    print("=" * 80)
    print("LOCAL REGISTER SEARCH - DEBUG SYNC")
    print(f"Target folder: {folder_path}")
    print(f"Database path: {db_path}")
    print(f"Rebuild flag : {args.rebuild}")
    print("=" * 80)

    cancel = threading.Event()
    result = sync(db_path, [folder_path], cancel=cancel, rebuild=args.rebuild)

    print("\n" + "=" * 80)
    print("SYNC RESULT SUMMARY")
    print("=" * 80)
    print(f"Files Indexed : {result.get('indexed', 0)} (files_indexed={result.get('files_indexed', 0)})")
    print(f"Files Skipped : {result.get('skipped', 0)}")
    print(f"Files Removed : {result.get('removed', 0)}")
    print(f"Rows Indexed  : {result.get('rows_indexed', 0)}")
    print(f"Errors ({len(result.get('errors', []))}): {result.get('errors', [])}")
    print(f"File Status Details:")
    for item in result.get('file_status', []):
        print(f"  - [{item.get('status').upper()}] {item.get('path')}")
        print(f"    Rows: {item.get('rows', 0)} | Reason: {item.get('reason')}")

    # Inspect the database
    if db_path.exists():
        print("\n" + "=" * 80)
        print("DATABASE INSPECTION")
        print("=" * 80)
        db = connect(db_path)
        try:
            files = db.execute("SELECT path, mtime, size, stale, error FROM files").fetchall()
            print(f"Indexed Files in DB ({len(files)}):")
            for f in files:
                print(f"  * {f['path']} (size={f['size']}, stale={f['stale']}, error='{f['error']}')")

            headers = db.execute("SELECT path, sheet, headers FROM sheet_headers").fetchall()
            print(f"\nSheet Headers in DB ({len(headers)}):")
            for h in headers:
                print(f"  * Sheet '{h['sheet']}': {h['headers']}")

            row_counts = db.execute("SELECT sheet, count(*) as cnt FROM rows GROUP BY sheet").fetchall()
            print(f"\nRow Counts per Sheet in DB:")
            total_rows = 0
            for rc in row_counts:
                print(f"  * Sheet '{rc['sheet']}': {rc['cnt']} rows")
                total_rows += rc['cnt']
            print(f"  -> Total rows in database: {total_rows}")

            # Sample 2 rows per sheet
            for rc in row_counts:
                sheet = rc['sheet']
                print(f"\nSample data from '{sheet}':")
                sample_rows = db.execute("SELECT rownum, cells FROM rows WHERE sheet=? ORDER BY rownum LIMIT 2", (sheet,)).fetchall()
                for r in sample_rows:
                    print(f"    Row {r['rownum']}: {r['cells']}")
        finally:
            db.close()

    print("=" * 80)
    return 0 if not result.get('errors') else 1


if __name__ == "__main__":
    sys.exit(main())
