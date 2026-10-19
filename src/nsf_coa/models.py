"""Plain data structures shared by sources, merging, and writers."""

from __future__ import annotations

import calendar
import datetime as dt
import html
import re
from dataclasses import dataclass, field

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+", re.IGNORECASE)


def clean_text(value) -> str:
    """Unescape HTML entities and strip tags/extra whitespace from source metadata."""
    if value is None:
        return ""
    text = html.unescape(html.unescape(str(value)))
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_doi(value: str | None) -> str | None:
    """Return a bare, lower-case DOI (``10.xxxx/yyy``) or None."""
    if not value:
        return None
    match = DOI_RE.search(str(value))
    if not match:
        return None
    doi = match.group(0)
    # Trailing punctuation (and unbalanced closing brackets) belong to the sentence, not the DOI.
    while doi and (doi[-1] in ".,;:]}" or (doi[-1] == ")" and doi.count("(") < doi.count(")"))):
        doi = doi[:-1]
    return doi.lower()


def normalize_orcid(value: str | None) -> str | None:
    """Return a bare ORCID iD (``0000-0000-0000-000X``) or None."""
    if not value:
        return None
    match = re.search(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", str(value).upper())
    return match.group(1) if match else None


def normalize_openalex_id(value: str | None) -> str | None:
    """Return a bare OpenAlex ID (``A123...`` / ``W123...``) or None."""
    if not value:
        return None
    match = re.search(r"(?:^|[/:])([AWISCFP]\d+)$", str(value).strip().upper())
    return match.group(1) if match else None


@dataclass(frozen=True)
class PubDate:
    """A publication date with explicit precision ("day", "month", or "year")."""

    year: int
    month: int = 1
    day: int = 1
    precision: str = "day"

    @classmethod
    def parse(cls, value) -> PubDate | None:
        if value is None or value == "":
            return None
        if isinstance(value, PubDate):
            return value
        if isinstance(value, dt.datetime):
            return cls(value.year, value.month, value.day, "day")
        if isinstance(value, dt.date):
            return cls(value.year, value.month, value.day, "day")
        if isinstance(value, int):
            return cls(value, 1, 1, "year")
        if isinstance(value, (list, tuple)):
            parts = [int(p) for p in value if p not in (None, "")]
            if not parts:
                return None
            if len(parts) == 1:
                return cls(parts[0], 1, 1, "year")
            if len(parts) == 2:
                return cls(parts[0], parts[1], 1, "month")
            return cls(parts[0], parts[1], parts[2], "day")
        text = str(value).strip()
        m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].*)?", text)
        if m:
            return cls(int(m[1]), int(m[2]), int(m[3]), "day")
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
        if m:
            return cls(int(m[3]), int(m[1]), int(m[2]), "day")
        m = re.fullmatch(r"(\d{4})-(\d{1,2})", text)
        if m:
            return cls(int(m[1]), int(m[2]), 1, "month")
        m = re.search(r"(19|20)\d{2}", text)
        if m:
            return cls(int(m[0]), 1, 1, "year")
        return None

    @property
    def start(self) -> dt.date:
        return dt.date(self.year, self.month, self.day)

    @property
    def end(self) -> dt.date:
        """Latest calendar day this (possibly imprecise) date could refer to."""
        if self.precision == "year":
            return dt.date(self.year, 12, 31)
        if self.precision == "month":
            return dt.date(self.year, self.month, calendar.monthrange(self.year, self.month)[1])
        return self.start

    def __str__(self) -> str:
        if self.precision == "year":
            return f"{self.year}"
        if self.precision == "month":
            return f"{self.year}-{self.month:02d}"
        return self.start.isoformat()


@dataclass
class AuthorMention:
    """One author as listed on one work."""

    name: str
    given: str | None = None
    family: str | None = None
    orcid: str | None = None
    openalex_id: str | None = None
    affiliations: list[str] = field(default_factory=list)
    email: str | None = None
    is_self: bool = False


@dataclass
class Work:
    """A publication or other research product with its author list."""

    title: str
    date: PubDate | None
    authors: list[AuthorMention] = field(default_factory=list)
    doi: str | None = None
    openalex_id: str | None = None
    type: str | None = None
    venue: str | None = None
    sources: set[str] = field(default_factory=set)
    authors_truncated: bool = False

    @property
    def key(self) -> str:
        if self.doi:
            return f"doi:{self.doi}"
        if self.openalex_id:
            return f"openalex:{self.openalex_id}"
        from nsf_coa.names import normalize_text

        year = self.date.year if self.date else ""
        return f"title:{normalize_text(self.title)}:{year}"


@dataclass
class Entry:
    """One row in an NSF COA table."""

    code: str  # A, C, R, G, T, B, E, or "1" for Table 1
    name: str  # formatted "Last, First M"
    affiliation: str = ""
    optional: str = ""  # email/department (Tables 2-4) or journal (Table 5)
    last_active: dt.date | None = None
    relationship: str = ""  # Table 2 only
    # Provenance / review metadata (not written to the NSF table)
    orcid: str | None = None
    openalex_ids: set[str] = field(default_factory=set)
    works: list[Work] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    origin: str = ""  # "publications", "profile", "previous COA", ...
