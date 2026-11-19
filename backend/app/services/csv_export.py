"""CSV export that is safe to open in Excel / Google Sheets.

Cells starting with = + - @ (or tab / carriage return) are evaluated as formulas by spreadsheet apps,
so a candidate named `=HYPERLINK("http://evil",...)` could attack the mentor who opens the export.
Such cells are prefixed with an apostrophe (OWASP "CSV injection" guidance).
"""
import csv
import io
from collections.abc import Iterable

from fastapi.responses import Response

_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value) -> str:
    if value is None:
        return ""
    s = str(value)
    # Plain numbers (including negatives) are safe and should stay numeric in the spreadsheet.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return s
    return "'" + s if s.startswith(_DANGEROUS) else s


def csv_response(filename: str, header: list[str], rows: Iterable[list]) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for row in rows:
        w.writerow([safe_cell(v) for v in row])
    return Response(
        content="﻿" + buf.getvalue(),  # BOM so Excel detects UTF-8 (names with accents)
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
