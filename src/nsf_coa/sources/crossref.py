"""Crossref REST API (https://api.crossref.org) — publisher-deposited metadata for DOIs."""

from __future__ import annotations

import datetime as dt
import difflib
from urllib.parse import quote

from nsf_coa.http import HttpClient
from nsf_coa.models import AuthorMention, PubDate, Work, clean_text, normalize_doi, normalize_orcid
from nsf_coa.names import normalize_text

BASE = "https://api.crossref.org"
SELECT = "DOI,title,author,issued,published,published-print,published-online,type,container-title"


class Crossref:
    def __init__(self, http: HttpClient):
        self.http = http

    def _params(self, **params):
        params.setdefault("mailto", self.http.mailto)
        return params

    def work(self, doi: str) -> Work | None:
        data = self.http.get_json(f"{BASE}/works/{quote(normalize_doi(doi), safe='/')}", params=self._params())
        if not data:
            return None
        return self.parse_work(data.get("message") or {})

    def works_for_orcid(self, orcid: str, since: dt.date) -> list[Work]:
        """Works whose deposited metadata carries this ORCID (often incomplete, but cheap)."""
        works, cursor = [], "*"
        while cursor:
            data = self.http.get_json(f"{BASE}/works", params=self._params(
                filter=f"orcid:{normalize_orcid(orcid)},from-pub-date:{since.isoformat()}",
                rows=500, cursor=cursor, select=SELECT))
            message = (data or {}).get("message") or {}
            items = message.get("items") or []
            works.extend(self.parse_work(i) for i in items)
            cursor = message.get("next-cursor") if items else None
        return works

    def match_title(self, title: str, year: int | None = None, threshold: float = 0.92) -> Work | None:
        """Find the Crossref record for a citation that lacks a DOI (e.g. Google Scholar BibTeX)."""
        params = self._params(**{"query.bibliographic": title, "rows": 5, "select": SELECT})
        if year:
            params["filter"] = f"from-pub-date:{year - 1},until-pub-date:{year + 1}"
        data = self.http.get_json(f"{BASE}/works", params=params)
        target = normalize_text(title)
        best, best_score = None, 0.0
        for item in ((data or {}).get("message") or {}).get("items") or []:
            candidate = normalize_text((item.get("title") or [""])[0])
            score = difflib.SequenceMatcher(None, target, candidate).ratio()
            if score > best_score:
                best, best_score = item, score
        if best is not None and best_score >= threshold:
            return self.parse_work(best)
        return None

    @staticmethod
    def parse_work(item: dict) -> Work:
        authors = []
        for a in item.get("author") or []:
            given, family = clean_text(a.get("given")), clean_text(a.get("family"))
            name = f"{given} {family}".strip() or clean_text(a.get("name"))
            authors.append(AuthorMention(
                name=name,
                given=given or None,
                family=family or None,
                orcid=normalize_orcid(a.get("ORCID")),
                affiliations=[clean_text(x.get("name")) for x in a.get("affiliation") or [] if x.get("name")],
            ))
        date = None
        for key in ("published", "issued", "published-online", "published-print"):
            parts = ((item.get(key) or {}).get("date-parts") or [[None]])[0]
            date = PubDate.parse(parts)
            if date:
                break
        return Work(
            title=clean_text((item.get("title") or ["(untitled)"])[0]),
            date=date,
            authors=authors,
            doi=normalize_doi(item.get("DOI")),
            type=item.get("type"),
            venue=clean_text((item.get("container-title") or [None])[0]) or None,
            sources={"crossref"},
        )
