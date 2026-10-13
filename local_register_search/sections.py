"""Conservative, bounded-lookahead section detection."""
import re
from collections import deque
from itertools import groupby, islice
from openpyxl.utils.cell import column_index_from_string

FIELD_LABELS = {'company', 'given name', 'surname', 'first name', 'last name',
                'function', 'phone', 'telephone', 'email', 'address', 'name', 'date'}


def is_heading(record, following):
    cells, style = record[3], record[4]
    values = [value.strip() for value in cells.values() if value.strip()]
    if not 1 <= len(values) <= 2 or not all(any(c.isalpha() for c in value) for value in values):
        return False
    if values[0].casefold().rstrip(':') in FIELD_LABELS:
        return False
    text = ' | '.join(values)
    numbered = bool(re.match(r'^[A-Za-z0-9]{1,3}\s*[-.:)]\s+\S', text))
    nearby = [row for row in following if row[1] - record[1] <= 6]
    if len(nearby) < 2:
        return False
    if style['styled'] or style['merged']:
        return numbered or any(len(row[3]) >= 2 for row in nearby) or all(
            next(iter(row[3].values())).strip().casefold().rstrip(':') in FIELD_LABELS for row in nearby[:2])
    return all(column_index_from_string(column) <= 3 for column in cells) and (
        numbered or (len(values) == 1 and len(values[0].split()) >= 2 and len(nearby) >= 3
                     and all(len(row[3]) > len(cells) for row in nearby[:3])))


def contextual_rows(records):
    for sheet, group in groupby(records, key=lambda record: record[0]):
        group = iter(group)
        buffer = deque(islice(group, 4))
        section, headers = '', {}
        while buffer:
            record = buffer.popleft()
            if record[1] == 1:
                headers = record[3]
            else:
                if is_heading(record, list(buffer)):
                    section = ' | '.join(value.strip() for value in record[3].values() if value.strip())
                yield record[:4], headers, section
            next_record = next(group, None)
            if next_record is not None:
                buffer.append(next_record)
