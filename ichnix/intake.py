"""Read one bounded, UTF-8 mapped report for private workspace intake."""
import json
import os
import stat

from ichnix.contracts import _case_id, _field

MAX_REPORT_BYTES = 1024 * 1024
FIELDS = {"id", "title", "service", "component", "revision", "owner"}


def _unique_fields(pairs):
    row = dict(pairs)
    if len(row) != len(pairs):
        raise ValueError("duplicate report field")
    return row


def parse_mapped_report(raw):
    if not 0 < len(raw) <= MAX_REPORT_BYTES:
        raise ValueError("invalid report size")
    try:
        report = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_fields)
    except RecursionError:
        raise ValueError("expected a flat report object") from None
    if not isinstance(report, dict) or report.keys() != FIELDS:
        raise ValueError("expected six mapped metadata fields")
    if _case_id(report["id"]) in (".", ".."):
        raise ValueError("invalid case identifier")
    for key in report:
        report[key] = _field(report, key, 240 if key == "title" else 160)
    return report


def read_mapped_report(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("expected a regular report file")
        return parse_mapped_report(stream.read(MAX_REPORT_BYTES + 1))
