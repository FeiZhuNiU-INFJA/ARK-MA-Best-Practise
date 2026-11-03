"""Resolve exact string fields from local, already hash-verified source files."""
import csv
import json
import re
from pathlib import Path


def resolve_field(locator, source_files, cache):
    def check(ok, message):
        if not ok:
            raise ValueError(message)

    def positive(value):
        return type(value) is int and value >= 1

    check(isinstance(locator, dict), 'Field source locator must be an object')
    index = locator.get('source_index')
    check(type(index) is int and 0 <= index < len(source_files), 'Invalid source_index')
    path = Path(source_files[index]['path'])
    ext = path.suffix.lower()
    record_key = None
    if ext == '.xlsx':
        from openpyxl import load_workbook
        from openpyxl.utils.cell import coordinate_to_tuple
        sheet, cell = locator.get('sheet'), locator.get('cell')
        check(isinstance(sheet, str) and sheet, 'XLSX sheet is required')
        check(isinstance(cell, str) and re.fullmatch(r'[A-Za-z]+[1-9][0-9]*', cell), 'XLSX locator needs one cell')
        key = (str(path), sheet)
        if key not in cache:
            wb = load_workbook(path, read_only=True, data_only=True)
            try:
                check(sheet in wb.sheetnames, 'Unknown XLSX sheet')
                cache[key] = list(wb[sheet].values)
            finally:
                wb.close()
        rows = cache[key]
        row, col = coordinate_to_tuple(cell)
        check(row <= len(rows) and col <= len(rows[row-1]), 'XLSX locator outside source')
        value = rows[row-1][col-1]
        record_key = (str(path), sheet, row)
    elif ext == '.csv':
        row, col = locator.get('row'), locator.get('column')
        delimiter = locator.get('delimiter', ',')
        check(positive(row) and positive(col), 'CSV row/column must be positive integers')
        check(isinstance(delimiter, str) and len(delimiter) == 1, 'CSV delimiter must be one character')
        key = (str(path), delimiter)
        if key not in cache:
            with path.open(encoding='utf-8-sig', newline='') as f:
                cache[key] = list(csv.reader(f, delimiter=delimiter))
        rows = cache[key]
        check(row <= len(rows) and col <= len(rows[row-1]), 'CSV locator outside source')
        value = rows[row-1][col-1]
        record_key = (str(path), 'csv', row)
    elif ext == '.json':
        pointer = locator.get('pointer')
        check(isinstance(pointer, str) and (pointer == '' or pointer.startswith('/')), 'Invalid JSON Pointer')
        key = (str(path), 'json')
        if key not in cache:
            cache[key] = json.loads(path.read_text(encoding='utf-8-sig'))
        value = cache[key]
        for token in ([] if pointer == '' else pointer[1:].split('/')):
            check(not re.search(r'~(?![01])', token), 'Invalid JSON Pointer escape')
            token = token.replace('~1', '/').replace('~0', '~')
            if isinstance(value, list):
                check(bool(re.fullmatch(r'0|[1-9][0-9]*', token)) and int(token) < len(value), 'Invalid JSON array index')
                value = value[int(token)]
            else:
                check(isinstance(value, dict) and token in value, 'JSON Pointer not found')
                value = value[token]
    elif ext in ('.txt', '.md'):
        start, end = locator.get('line_start'), locator.get('line_end')
        key = (str(path), 'lines')
        if key not in cache:
            cache[key] = path.read_text(encoding='utf-8-sig').splitlines()
        lines = cache[key]
        check(positive(start) and positive(end) and start <= end <= len(lines), 'Invalid text line range')
        value = '\n'.join(lines[start-1:end])
    else:
        raise ValueError('Unsupported raw field source: ' + ext)
    check(isinstance(value, str), 'Located source field must be text')
    return value, record_key
