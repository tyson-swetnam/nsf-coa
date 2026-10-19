"""DataCite REST API (https://api.datacite.org) — DOIs for datasets, software, and reports (Zenodo, Dryad, ...)."""

from __future__ import annotations

from urllib.parse import quote

from nsf_coa.http import HttpClient
from nsf_coa.models import AuthorMention, PubDate, Work, clean_text, normalize_doi, normalize_orcid

BASE = "https://api.datacite.org"


class DataCite:
    def __init__(self, http: HttpClient):
        self.http = http

    def work(self, doi: str) -> Work | None:
        data = self.http.get_json(f"{BASE}/dois/{quote(normalize_doi(doi), safe='/')}")
        if not data or not data.get("data"):
            return None
        return self.parse_work(data["data"].get("attributes") or {})

    @staticmethod
    def parse_work(attrs: dict) -> Work:
        authors = []
        for c in attrs.get("creators") or []:
            if c.get("nameType") == "Organizational":
                continue
            orcid = None
            for ident in c.get("nameIdentifiers") or []:
                if (ident.get("nameIdentifierScheme") or "").upper() == "ORCID":
                    orcid = normalize_orcid(ident.get("nameIdentifier"))
            affiliations = []
            for aff in c.get("affiliation") or []:
                affiliations.append(clean_text(aff if isinstance(aff, str) else aff.get("name", "")))
            authors.append(AuthorMention(
                name=clean_text(c.get("name")),
                given=clean_text(c.get("givenName")) or None,
                family=clean_text(c.get("familyName")) or None,
                orcid=orcid,
                affiliations=[a for a in affiliations if a],
            ))
        date = None
        for d in attrs.get("dates") or []:
            if d.get("dateType") == "Issued":
                date = PubDate.parse(d.get("date"))
        date = date or PubDate.parse(attrs.get("publicationYear"))
        return Work(
            title=clean_text(((attrs.get("titles") or [{}])[0]).get("title")) or "(untitled)",
            date=date,
            authors=authors,
            doi=normalize_doi(attrs.get("doi")),
            type=((attrs.get("types") or {}).get("resourceTypeGeneral") or "").lower() or None,
            venue=attrs.get("publisher") if isinstance(attrs.get("publisher"), str) else None,
            sources={"datacite"},
        )
