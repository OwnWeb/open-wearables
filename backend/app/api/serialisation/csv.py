"""Streaming CSV writer used by the workout export endpoint."""

from __future__ import annotations

import csv
import io
from typing import Iterable, Iterator


def stream_csv_rows(rows: Iterable[list[str]]) -> Iterator[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for row in rows:
        writer.writerow(row)
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
