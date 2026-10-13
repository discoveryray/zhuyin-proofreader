"""Lossless XLSX writes for rows whose Python strings are contract text.

Use only on text/data writers, not on cells intended to contain formulas.
Native numbers, booleans and None retain openpyxl's existing semantics.
"""

from openpyxl.utils import get_column_letter


def _validate_text(sheet, row, column, value, field=None):
    if not isinstance(value, str):
        return
    field = field or (sheet.cell(1, column).value if row > 1 else value)
    location = f"{sheet.title}/{field or get_column_letter(column)} ({get_column_letter(column)}{row})"
    # XLSX strings use XML 1.0. Reject before openpyxl can truncate the value or
    # let the XML serializer fail without naming the affected source field.
    for char in value:
        code = ord(char)
        if not (code in (9, 10, 13) or 0x20 <= code <= 0xD7FF
                or 0xE000 <= code <= 0xFFFD or 0x10000 <= code <= 0x10FFFF):
            raise ValueError(f"XLSX text {location}: XML 1.0 cannot preserve U+{code:04X}")
    units = sum(2 if ord(char) > 0xFFFF else 1 for char in value)
    if units > 32767:
        raise ValueError(f"XLSX text {location}: {units} UTF-16 units exceed the 32767 cell limit")


def write_text_value(sheet, row, column, value, *, field=None):
    """Write a declared text value (or an unchanged native scalar) to a cell."""
    _validate_text(sheet, row, column, value, field)
    cell = sheet.cell(row, column)
    cell.value = value
    if isinstance(value, str):
        cell.data_type = "s"
        cell.number_format = "@"
    return cell


def append_text_row(sheet, values):
    """Append contract data; all strings are literal text regardless of content."""
    values = list(values)
    row = sheet._current_row + 1
    # Validate the entire row before append, which otherwise silently truncates
    # long strings. Never change values, whitespace, punctuation or prefixes.
    for column, value in enumerate(values, 1):
        _validate_text(sheet, row, column, value)
    sheet.append(values)
    for column, value in enumerate(values, 1):
        if isinstance(value, str):
            cell = sheet.cell(row, column)
            cell.data_type = "s"
            cell.number_format = "@"
