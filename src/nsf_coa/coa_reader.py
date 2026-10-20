"""Read an existing NSF COA spreadsheet (current or older template versions).

Rows are recognized by their category code in column A (``R:``, ``G:``, ``T:``,
``A:``, ``C:``, ``B:``, ``E:``), so inserted rows and older template layouts
both work.  Table 1 rows are the rows below the ``1`` header.
"""

from __future__ import annotations

import datetime as dt
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

from nsf_coa.models import PubDate

TEMPLATE_PLACEHOLDERS = {"alphaman", "samplename"}


@dataclass
class PreviousCOA:
    path: Path
    self_name: str = ""
    affiliations: list[dict] = field(default_factory=list)  # Table 1
    rows: list[dict] = field(default_factory=list)  # all coded rows

    def by_code(self, *codes: str) -> list[dict]:
        return [r for r in self.rows if r["code"] in codes]


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dt.datetime, dt.date)):
        return value.strftime("%m/%d/%Y")
    return re.sub(r"\s+", " ", str(value)).strip()


def _date(value) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, (int, float)) and 20000 < value < 80000:  # Excel serial date
        return dt.date(1899, 12, 30) + dt.timedelta(days=int(value))
    parsed = PubDate.parse(_text(value)) if value else None
    return parsed.end if parsed else None


def _is_placeholder(name: str) -> bool:
    return any(p in name.lower() for p in TEMPLATE_PLACEHOLDERS)


def read_previous_coa(path: str | Path) -> PreviousCOA:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # template's Mac-only extension
        wb = openpyxl.load_workbook(path, data_only=True)
    result = PreviousCOA(path=Path(path))
    for ws in wb.worksheets:
        table = None
        for row in ws.iter_rows(min_col=1, max_col=5, values_only=True):
            a, b, c, d, e = (list(row) + [None] * 5)[:5]
            a_text = _text(a)
            if re.fullmatch(r"[1-5](\.0)?", a_text) and _text(b):
                table = int(float(a_text))
                continue
            if a_text.lower().startswith("table"):
                m = re.match(r"table\s*(\d)", a_text.lower())
                table = int(m.group(1)) if m else table
                continue
            code_match = re.fullmatch(r"([A-Za-z])\s*:?", a_text)
            if code_match and _text(b):
                name = _text(b)
                if _is_placeholder(name):
                    continue
                code = code_match.group(1).upper()
                row_out = {"code": code, "name": name, "table": table}
                if code == "R":
                    row_out.update(relationship=_text(c), optional=_text(d), last_active=_date(e))
                elif code in {"B", "E"}:
                    row_out.update(affiliation=_text(c), journal=_text(d), last_active=_date(e))
                else:
                    row_out.update(affiliation=_text(c), optional=_text(d), last_active=_date(e))
                result.rows.append(row_out)
            elif table == 1 and not a_text and (_text(b) or _text(c)):
                if _text(b) and not result.self_name and not _is_placeholder(_text(b)):
                    result.self_name = _text(b)
                if _text(c) and not (_text(b) and _is_placeholder(_text(b))):
                    result.affiliations.append({"affiliation": _text(c), "last_active": _date(d)})
    return result


def previous_coa_to_profile_dict(prev: PreviousCOA) -> dict:
    """Convert a previous COA into the manual sections of a profile YAML."""

    def iso(d):
        return d.isoformat() if d else None

    def clean(d: dict) -> dict:
        return {k: v for k, v in d.items() if v not in (None, "")}

    return {
        "person": clean({
            "name": prev.self_name,
            "affiliations": [clean({"name": a["affiliation"], "last_active": iso(a["last_active"])})
                             for a in prev.affiliations],
        }),
        "relationships": [clean({"name": r["name"], "relationship": r.get("relationship"),
                                 "optional": r.get("optional"), "last_active": iso(r.get("last_active"))})
                          for r in prev.by_code("R")],
        "advisors": [clean({"type": r["code"], "name": r["name"], "affiliation": r.get("affiliation"),
                            "optional": r.get("optional")})
                     for r in prev.by_code("G", "T")],
        "collaborators": [clean({"name": r["name"], "affiliation": r.get("affiliation"),
                                 "optional": r.get("optional"), "last_active": iso(r.get("last_active"))})
                          for r in prev.by_code("C")],
        "editorial": [clean({"type": r["code"], "name": r["name"], "affiliation": r.get("affiliation"),
                             "journal": r.get("journal"), "last_active": iso(r.get("last_active"))})
                      for r in prev.by_code("B", "E")],
    }
