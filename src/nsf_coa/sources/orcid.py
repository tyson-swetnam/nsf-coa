"""ORCID public API (https://pub.orcid.org) — a researcher's self-curated works and employment.

ORCID work summaries do not include co-author lists, so works are returned as
DOI "stubs" that are later resolved through OpenAlex, Crossref, or DataCite.
"""

from __future__ import annotations

from dataclasses import dataclass

from nsf_coa.http import HttpClient
from nsf_coa.models import PubDate, Work, clean_text, normalize_doi, normalize_orcid

BASE = "https://pub.orcid.org/v3.0"
HEADERS = {"Accept": "application/json"}


def _date(block: dict | None) -> PubDate | None:
    if not block:
        return None
    parts = [(block.get(k) or {}).get("value") for k in ("year", "month", "day")]
    return PubDate.parse([p for p in parts if p])


@dataclass
class Employment:
    organization: str
    department: str | None
    role: str | None
    start: PubDate | None
    end: PubDate | None


class Orcid:
    def __init__(self, http: HttpClient):
        self.http = http

    def _get(self, orcid: str, section: str):
        return self.http.get_json(f"{BASE}/{normalize_orcid(orcid)}/{section}", headers=HEADERS)

    def person_name(self, orcid: str) -> tuple[str, str] | None:
        data = self._get(orcid, "person") or {}
        name = data.get("name") or {}
        given = (name.get("given-names") or {}).get("value") or ""
        family = (name.get("family-name") or {}).get("value") or ""
        return (given, family) if family else None

    def works(self, orcid: str) -> list[Work]:
        """Work stubs (title, date, DOI) from the ORCID record; one per ORCID work group."""
        data = self._get(orcid, "works") or {}
        works = []
        for group in data.get("group") or []:
            summaries = group.get("work-summary") or []
            if not summaries:
                continue
            summary = summaries[0]
            doi = None
            for ext in (group.get("external-ids") or {}).get("external-id") or []:
                if ext.get("external-id-type") == "doi" and ext.get("external-id-relationship") == "self":
                    doi = normalize_doi(ext.get("external-id-value"))
                    break
            title = clean_text((((summary.get("title") or {}).get("title")) or {}).get("value")) or "(untitled)"
            works.append(Work(
                title=title,
                date=_date(summary.get("publication-date")),
                doi=doi,
                type=summary.get("type"),
                venue=(summary.get("journal-title") or {}).get("value"),
                sources={"orcid"},
            ))
        return works

    def employments(self, orcid: str) -> list[Employment]:
        out = []
        for section, key in (("employments", "employment-summary"), ("invited-positions", "invited-position-summary")):
            data = self._get(orcid, section) or {}
            for group in data.get("affiliation-group") or []:
                for summary in group.get("summaries") or []:
                    s = summary.get(key) or {}
                    out.append(Employment(
                        organization=(s.get("organization") or {}).get("name", ""),
                        department=s.get("department-name"),
                        role=s.get("role-title"),
                        start=_date(s.get("start-date")),
                        end=_date(s.get("end-date")),
                    ))
        return out
