"""OpenAlex (https://openalex.org) — open catalog of works, authors, and institutions.

OpenAlex requires a free API key for meaningful use (the keyless budget is shared
by everyone behind the same IP address).  Get one at
https://help.openalex.org/api/authentication/ and set ``OPENALEX_API_KEY``.
"""

from __future__ import annotations

import datetime as dt
import logging

from nsf_coa.http import HttpClient
from nsf_coa.models import (
    AuthorMention,
    PubDate,
    Work,
    clean_text,
    normalize_doi,
    normalize_openalex_id,
    normalize_orcid,
)

log = logging.getLogger(__name__)

BASE = "https://api.openalex.org"
WORK_FIELDS = ",".join([
    "id", "doi", "title", "display_name", "publication_date", "publication_year", "type",
    "primary_location", "authorships", "is_authors_truncated",
])
AUTHOR_FIELDS = ",".join([
    "id", "display_name", "display_name_alternatives", "orcid", "works_count",
    "last_known_institutions", "affiliations",
])


class OpenAlex:
    def __init__(self, http: HttpClient, api_key: str | None = None):
        self.http = http
        self.api_key = api_key

    def _get(self, path: str, **params):
        params.setdefault("api_key", self.api_key)
        params.setdefault("mailto", self.http.mailto)
        return self.http.get_json(f"{BASE}{path}", params=params)

    # ---- authors -------------------------------------------------------
    def author_by_orcid(self, orcid: str) -> dict | None:
        return self._get(f"/authors/orcid:{normalize_orcid(orcid)}", select=AUTHOR_FIELDS)

    def search_authors(self, name: str, limit: int = 10) -> list[dict]:
        data = self._get("/authors", search=name, per_page=limit, select=AUTHOR_FIELDS)
        return (data or {}).get("results", [])

    def authors_by_ids(self, ids: list[str]) -> dict[str, dict]:
        """Batch-fetch author records, keyed by bare OpenAlex author ID."""
        out: dict[str, dict] = {}
        ids = sorted({normalize_openalex_id(i) for i in ids if normalize_openalex_id(i)})
        for chunk_start in range(0, len(ids), 50):
            chunk = ids[chunk_start:chunk_start + 50]
            data = self._get("/authors", filter="openalex:" + "|".join(chunk),
                             per_page=50, select=AUTHOR_FIELDS)
            for rec in (data or {}).get("results", []):
                out[normalize_openalex_id(rec["id"])] = rec
        return out

    # ---- works ---------------------------------------------------------
    def works_for_author(self, author_ids: list[str], orcid: str | None, since: dt.date) -> list[Work]:
        filters = []
        ids = [normalize_openalex_id(a) for a in author_ids if normalize_openalex_id(a)]
        if ids:
            filters.append("author.id:" + "|".join(ids))
        if orcid:
            filters.append("author.orcid:" + normalize_orcid(orcid))
        works: dict[str, Work] = {}
        for author_filter in filters:
            for rec in self._paginate("/works", filter=f"{author_filter},from_publication_date:{since.isoformat()}"):
                work = self.parse_work(rec)
                works.setdefault(work.key, work)
        return list(works.values())

    def works_by_doi(self, dois: list[str]) -> dict[str, Work]:
        out: dict[str, Work] = {}
        dois = sorted({normalize_doi(d) for d in dois if normalize_doi(d)})
        for start in range(0, len(dois), 50):
            chunk = dois[start:start + 50]
            data = self._get("/works", filter="doi:" + "|".join(chunk), per_page=50, select=WORK_FIELDS)
            for rec in (data or {}).get("results", []):
                work = self.parse_work(rec)
                if work.doi:
                    out[work.doi] = work
        return out

    def _paginate(self, path: str, **params):
        cursor = "*"
        while cursor:
            data = self._get(path, cursor=cursor, per_page=200, select=WORK_FIELDS, **params)
            if not data:
                return
            for rec in data.get("results", []):
                if rec.get("is_authors_truncated"):
                    full = self._get(f"/works/{normalize_openalex_id(rec['id'])}", select=WORK_FIELDS)
                    rec = full or rec
                yield rec
            cursor = (data.get("meta") or {}).get("next_cursor")

    @staticmethod
    def parse_work(rec: dict) -> Work:
        authors = []
        for authorship in rec.get("authorships") or []:
            author = authorship.get("author") or {}
            name = clean_text(author.get("display_name") or authorship.get("raw_author_name"))
            affiliations = [clean_text(i.get("display_name")) for i in authorship.get("institutions") or []
                            if i.get("display_name")]
            if not affiliations:
                affiliations = [clean_text(a) for a in authorship.get("raw_affiliation_strings") or [] if a]
            authors.append(AuthorMention(
                name=name,
                orcid=normalize_orcid(author.get("orcid")),
                openalex_id=normalize_openalex_id(author.get("id")),
                affiliations=affiliations,
            ))
        venue = ((rec.get("primary_location") or {}).get("source") or {}).get("display_name")
        return Work(
            title=clean_text(rec.get("title") or rec.get("display_name")) or "(untitled)",
            date=PubDate.parse(rec.get("publication_date")) or PubDate.parse(rec.get("publication_year")),
            authors=authors,
            doi=normalize_doi(rec.get("doi")),
            openalex_id=normalize_openalex_id(rec.get("id")),
            type=rec.get("type"),
            venue=venue,
            sources={"openalex"},
        )


def institution_names(author_record: dict) -> list[str]:
    """Current (last known) institution names from an OpenAlex author record."""
    names = [i.get("display_name") for i in author_record.get("last_known_institutions") or [] if i.get("display_name")]
    if names:
        return names
    # Fall back to the affiliation with the most recent year.
    best_year, best = -1, []
    for aff in author_record.get("affiliations") or []:
        years = aff.get("years") or []
        inst = (aff.get("institution") or {}).get("display_name")
        if inst and years and max(years) > best_year:
            best_year, best = max(years), [inst]
    return best
