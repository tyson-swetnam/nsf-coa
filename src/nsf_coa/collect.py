"""Gather works from every configured source and resolve them to full author lists."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from nsf_coa.config import Profile
from nsf_coa.http import HttpClient, SourceUnavailable
from nsf_coa.models import Work
from nsf_coa.names import normalize_text
from nsf_coa.sources.crossref import Crossref
from nsf_coa.sources.datacite import DataCite
from nsf_coa.sources.files import dois_from_text, works_from_file
from nsf_coa.sources.openalex import OpenAlex
from nsf_coa.sources.orcid import Orcid

log = logging.getLogger(__name__)


@dataclass
class Collection:
    works: list[Work] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    openalex_author_ids: list[str] = field(default_factory=list)
    openalex: OpenAlex | None = None


def merge_work(into: Work, other: Work) -> None:
    """Fold ``other`` into ``into``, keeping the richest author list."""
    into.sources |= other.sources
    into.doi = into.doi or other.doi
    into.openalex_id = into.openalex_id or other.openalex_id
    into.venue = into.venue or other.venue
    into.type = into.type or other.type
    if into.date is None or (other.date and other.date.precision == "day" and into.date.precision != "day"):
        into.date = other.date or into.date
    if into.title.startswith("DOI ") and other.title:
        into.title = other.title
    better = len(other.authors) > len(into.authors) or (into.authors_truncated and not other.authors_truncated and other.authors)
    if better:
        into.authors = other.authors
        into.authors_truncated = other.authors_truncated
    elif other.authors and len(other.authors) == len(into.authors):
        # Same list from two sources: borrow missing identifiers/affiliations positionally.
        for mine, theirs in zip(into.authors, other.authors):
            mine.orcid = mine.orcid or theirs.orcid
            mine.openalex_id = mine.openalex_id or theirs.openalex_id
            mine.given = mine.given or theirs.given
            mine.family = mine.family or theirs.family
            if not mine.affiliations:
                mine.affiliations = theirs.affiliations


class WorkSet:
    """Works de-duplicated by DOI, then by normalized title + year."""

    def __init__(self):
        self.by_key: dict[str, Work] = {}
        self.title_index: dict[str, str] = {}

    def add(self, work: Work) -> Work:
        title_key = f"{normalize_text(work.title)}|{work.date.year if work.date else ''}"
        key = work.key
        existing_key = key if key in self.by_key else self.title_index.get(title_key)
        if existing_key and existing_key in self.by_key:
            merge_work(self.by_key[existing_key], work)
            if work.doi and f"doi:{work.doi}" not in self.by_key:
                self.by_key[f"doi:{work.doi}"] = self.by_key[existing_key]
            return self.by_key[existing_key]
        self.by_key[key] = work
        if not work.title.startswith("DOI ") and len(normalize_text(work.title)) >= 12:
            self.title_index.setdefault(title_key, key)
        return work

    def values(self) -> list[Work]:
        seen, out = set(), []
        for w in self.by_key.values():
            if id(w) not in seen:
                seen.add(id(w))
                out.append(w)
        return out


def collect(profile: Profile, http: HttpClient, progress=print) -> Collection:
    result = Collection()
    works = WorkSet()
    since = profile.coauthor_cutoff
    openalex = OpenAlex(http, profile.openalex_api_key) if profile.use_openalex else None
    crossref = Crossref(http) if profile.use_crossref else None
    datacite = DataCite(http) if profile.use_datacite else None
    orcid_api = Orcid(http) if profile.use_orcid else None
    result.openalex = openalex

    def attempt(label, fn, default=None):
        try:
            return fn()
        except SourceUnavailable as exc:
            msg = f"{label}: {exc}"
            if label.startswith("OpenAlex") and not profile.openalex_api_key and "HTTP 429" in str(exc):
                msg += " -- set OPENALEX_API_KEY (free: https://help.openalex.org/api/authentication/)"
            result.warnings.append(msg)
            progress(f"  ! {msg}")
            return default

    # 1. OpenAlex: the author's works in the window.
    if openalex:
        ids = list(profile.openalex_ids)
        if not ids and profile.orcid:
            rec = attempt("OpenAlex author lookup", lambda: openalex.author_by_orcid(profile.orcid))
            if rec:
                ids.append(rec["id"])
        result.openalex_author_ids = ids
        if ids or profile.orcid:
            found = attempt("OpenAlex works", lambda: openalex.works_for_author(ids, profile.orcid, since), [])
            progress(f"  OpenAlex: {len(found)} works since {since}")
            for w in found:
                works.add(w)
        else:
            progress("  OpenAlex: skipped (no ORCID or OpenAlex author ID in profile)")

    # 2. Crossref: works whose metadata carries the ORCID.
    if crossref and profile.orcid:
        found = attempt("Crossref ORCID search", lambda: crossref.works_for_orcid(profile.orcid, since), [])
        progress(f"  Crossref: {len(found)} works tagged with ORCID {profile.orcid}")
        for w in found:
            works.add(w)

    # 3. ORCID record: work stubs (DOIs) curated by the researcher.
    if orcid_api and profile.orcid:
        found = attempt("ORCID works", lambda: orcid_api.works(profile.orcid), [])
        found = [w for w in found if w.date is None or w.date.end >= since]
        progress(f"  ORCID: {len(found)} works in window")
        for w in found:
            works.add(w)

    # 4. Bibliography files (Google Scholar BibTeX export, Zotero, CV, ...).
    for path in profile.bibliography_files:
        found = [w for w in works_from_file(path) if w.date is None or w.date.end >= since]
        progress(f"  {path.name}: {len(found)} entries in window")
        for w in found:
            works.add(w)

    # 5. Web pages (lab site, faculty page, CV): harvest DOIs.
    for url in profile.urls:
        text = attempt(f"web page {url}", lambda u=url: http.get_text(u), "")
        dois = dois_from_text(text or "")
        progress(f"  {url}: {len(dois)} DOIs found")
        for d in dois:
            works.add(Work(title=f"DOI {d}", date=None, doi=d, sources={f"url:{url}"}))

    for d in profile.dois:
        works.add(Work(title=f"DOI {d}", date=None, doi=d.lower(), sources={"profile:dois"}))

    # 6. Resolve works that lack author lists (or have truncated ones).
    pending = [w for w in works.values() if w.doi and (not w.authors or w.authors_truncated)]
    if pending and openalex:
        resolved = attempt("OpenAlex DOI lookup", lambda: openalex.works_by_doi([w.doi for w in pending]), {})
        for w in pending:
            if w.doi in resolved:
                merge_work(w, resolved[w.doi])
    for w in [w for w in works.values() if w.doi and (not w.authors or w.authors_truncated)]:
        for label, api in (("Crossref", crossref), ("DataCite", datacite)):
            if api is None:
                continue
            other = attempt(f"{label} DOI {w.doi}", lambda a=api, d=w.doi: a.work(d))
            if other and other.authors:
                merge_work(w, other)
                break

    # 7. Citations without DOIs (typical of Google Scholar exports): match titles in Crossref.
    if crossref and profile.match_titles:
        for w in works.values():
            if w.doi or (w.authors and not w.authors_truncated):
                continue
            other = attempt(f"Crossref title match '{w.title[:40]}'",
                            lambda w=w: crossref.match_title(w.title, w.date.year if w.date else None))
            if other:
                merge_work(w, other)

    # Second pass: DOIs from step 7 / web pages may now collide with existing works.
    final = WorkSet()
    for w in works.values():
        final.add(w)
    result.works = final.values()
    return result
