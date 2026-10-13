import re
from pathlib import Path
from .database import connect, normalize


def search(path, query, mode='All Words', file='', sheet='', page=0, page_size=100, sort=None):
    db = connect(path)
    db.create_function('filename', 1, lambda value: Path(value).name.casefold())
    try:
        clauses, args = [], []
        query = normalize(query.strip())
        if query:
            if mode == 'All Words':
                words = query.split()
                # FTS narrows plain words; literal checks also support punctuation and partial identifiers.
                if all(re.fullmatch(r'[a-z0-9]+', word) for word in words):
                    clauses.append('r.id IN (SELECT rowid FROM row_fts WHERE row_fts MATCH ?)')
                    args.append(' AND '.join('"' + word + '"*' for word in words))
                for word in words:
                    clauses.append('instr(r.normalized,?)>0')
                    args.append(word)
            else:
                clauses.append('instr(r.normalized,?)>0')
                args.append(query)
        if file:
            clauses.append('r.path=?')
            args.append(file)
        if sheet:
            clauses.append('r.sheet=?')
            args.append(sheet)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        total = db.execute('SELECT count(*) FROM rows r' + where, args).fetchone()[0]
        order = 'r.path,r.sheet,r.rownum'
        if sort:
            key, descending = sort
            expression = {'file': 'filename(r.path)', 'sheet': 'r.sheet COLLATE NOCASE', 'row': 'r.rownum'}.get(key)
            if expression is None and re.fullmatch('[A-Z]+', key):
                expression = "json_extract(r.cells, '$." + key + "')"
            if expression:
                order = expression + (' DESC' if descending else ' ASC') + ',r.path,r.sheet,r.rownum'
        rows = db.execute('SELECT r.*,f.stale,h.headers FROM rows r JOIN files f ON f.path=r.path '
                          'LEFT JOIN sheet_headers h ON h.path=r.path AND h.sheet=r.sheet' + where +
                          ' ORDER BY ' + order + ' LIMIT ? OFFSET ?', args + [page_size, max(0, page * page_size)]).fetchall()
        return total, [dict(row) for row in rows]
    finally:
        db.close()
