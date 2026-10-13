import os
import sqlite3
import unicodedata
from pathlib import Path


def normalize(text):
    return unicodedata.normalize('NFKC', str(text)).casefold()


def data_dir():
    path = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'LocalRegisterSearch'
    path.mkdir(parents=True, exist_ok=True)
    return path


def connect(path):
    db = sqlite3.connect(str(path), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, mtime INTEGER, size INTEGER,
      stale INTEGER DEFAULT 0, error TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS rows(id INTEGER PRIMARY KEY, path TEXT, sheet TEXT,
      rownum INTEGER, hidden INTEGER, cells TEXT, text TEXT, normalized TEXT);
    CREATE INDEX IF NOT EXISTS rows_path ON rows(path);
    CREATE VIRTUAL TABLE IF NOT EXISTS row_fts USING fts5(normalized, content='rows', content_rowid='id');
    CREATE TRIGGER IF NOT EXISTS rows_ai AFTER INSERT ON rows BEGIN
      INSERT INTO row_fts(rowid, normalized) VALUES(new.id,new.normalized); END;
    CREATE TRIGGER IF NOT EXISTS rows_ad AFTER DELETE ON rows BEGIN
      INSERT INTO row_fts(row_fts,rowid,normalized) VALUES('delete',old.id,old.normalized); END;
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS sheet_headers(path TEXT, sheet TEXT, headers TEXT,
      PRIMARY KEY(path,sheet));
    ''')
    columns = {row['name'] for row in db.execute('PRAGMA table_info(rows)')}
    with db:
        for name in ('row_text', 'row_digits'):
            if name not in columns:
                db.execute(f"ALTER TABLE rows ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
        if 'row_section' not in columns:
            db.execute('ALTER TABLE rows ADD COLUMN row_section TEXT')
    return db


def needs_search_update(db):
    return bool(db.execute("SELECT 1 FROM rows WHERE row_text IS NULL OR row_text='' OR row_section IS NULL LIMIT 1").fetchone())
