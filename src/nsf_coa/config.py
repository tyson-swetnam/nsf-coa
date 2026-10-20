"""Profile (YAML) loading.  See ``examples/profile.example.yaml`` for every option."""

from __future__ import annotations

import calendar
import datetime as dt
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from nsf_coa.models import PubDate, normalize_openalex_id, normalize_orcid

DEFAULT_EXCLUDED_TYPES = ["paratext", "erratum", "peer-review", "retraction"]


@dataclass
class ManualEntry:
    name: str
    code: str = ""
    affiliation: str = ""
    optional: str = ""
    relationship: str = ""
    journal: str = ""
    last_active: dt.date | None = None


@dataclass
class Profile:
    name: str = ""  # "Last, First M"
    orcid: str | None = None
    openalex_ids: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    mailto: str | None = None
    openalex_api_key: str | None = None
    as_of: dt.date = field(default_factory=dt.date.today)
    coauthor_months: int = 48
    editor_months: int = 24
    affiliation_months: int = 12

    use_openalex: bool = True
    use_orcid: bool = True
    use_crossref: bool = True
    use_datacite: bool = True
    match_titles: bool = True  # look up DOI-less citations (Google Scholar) in Crossref

    bibliography_files: list[Path] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    dois: list[str] = field(default_factory=list)
    previous_coa: Path | None = None

    affiliations: list[ManualEntry] = field(default_factory=list)  # Table 1
    relationships: list[ManualEntry] = field(default_factory=list)  # Table 2 (R)
    advisors: list[ManualEntry] = field(default_factory=list)  # Table 3 (G / T)
    collaborators: list[ManualEntry] = field(default_factory=list)  # Table 4 (C)
    editorial: list[ManualEntry] = field(default_factory=list)  # Table 5 (B / E)

    exclude: list[str] = field(default_factory=list)  # names, ORCIDs, OpenAlex IDs, or DOIs
    overrides: dict[str, dict] = field(default_factory=dict)  # name -> {name, affiliation, optional}
    same_person: list[list[str]] = field(default_factory=list)  # groups of names/ORCIDs/OpenAlex IDs
    exclude_types: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDED_TYPES))
    optional_column: str = "orcid"  # what to put in "Optional (email, Department)": orcid | none
    affiliation_mode: str = "current"  # current (OpenAlex last-known) | coauthorship
    large_author_threshold: int = 50

    base_dir: Path = field(default_factory=Path.cwd)

    @property
    def coauthor_cutoff(self) -> dt.date:
        return months_before(self.as_of, self.coauthor_months)

    @property
    def editor_cutoff(self) -> dt.date:
        return months_before(self.as_of, self.editor_months)

    @property
    def affiliation_cutoff(self) -> dt.date:
        return months_before(self.as_of, self.affiliation_months)


def months_before(day: dt.date, months: int) -> dt.date:
    year, month = divmod(day.year * 12 + (day.month - 1) - months, 12)
    month += 1
    return dt.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _date(value) -> dt.date | None:
    parsed = PubDate.parse(value)
    return parsed.end if parsed else None


def _entries(items, default_code: str = "") -> list[ManualEntry]:
    out = []
    for item in items or []:
        if isinstance(item, str):
            item = {"name": item}
        out.append(ManualEntry(
            name=str(item.get("name", "")).strip(),
            code=str(item.get("type") or item.get("code") or default_code).strip().rstrip(":").upper(),
            affiliation=str(item.get("affiliation") or "").strip(),
            optional=str(item.get("optional") or item.get("email") or "").strip(),
            relationship=str(item.get("relationship") or "").strip(),
            journal=str(item.get("journal") or "").strip(),
            last_active=_date(item.get("last_active")),
        ))
    return out


def load_profile(path: str | Path | None) -> Profile:
    data: dict = {}
    base = Path.cwd()
    if path:
        path = Path(path)
        data = yaml.safe_load(path.read_text()) or {}
        base = path.parent.resolve()
    return profile_from_dict(data, base)


def profile_from_dict(data: dict, base: Path | None = None) -> Profile:
    base = base or Path.cwd()
    person = data.get("person") or {}
    window = data.get("window") or {}
    sources = data.get("sources") or {}
    output = data.get("output") or {}

    def resolve(p) -> Path:
        p = Path(os.path.expanduser(str(p)))
        return p if p.is_absolute() else base / p

    files = []
    for key in ("bibtex", "ris", "csl_json", "files"):
        files.extend(resolve(p) for p in sources.get(key) or [])

    ids = person.get("openalex_ids") or person.get("openalex_id") or []
    if isinstance(ids, str):
        ids = [ids]

    profile = Profile(
        name=str(person.get("name") or "").strip(),
        orcid=normalize_orcid(person.get("orcid")),
        openalex_ids=[normalize_openalex_id(i) for i in ids if normalize_openalex_id(i)],
        aliases=[str(a) for a in person.get("aliases") or []],
        mailto=data.get("mailto") or person.get("email") or os.environ.get("NSF_COA_MAILTO"),
        openalex_api_key=data.get("openalex_api_key") or os.environ.get("OPENALEX_API_KEY"),
        as_of=_date(window.get("as_of")) or dt.date.today(),
        coauthor_months=int(window.get("coauthor_months", 48)),
        editor_months=int(window.get("editor_months", 24)),
        affiliation_months=int(window.get("affiliation_months", 12)),
        use_openalex=bool(sources.get("openalex", True)),
        use_orcid=bool(sources.get("orcid", True)),
        use_crossref=bool(sources.get("crossref", True)),
        use_datacite=bool(sources.get("datacite", True)),
        match_titles=bool(sources.get("match_titles", True)),
        bibliography_files=files,
        urls=list(sources.get("urls") or []),
        dois=list(sources.get("dois") or []),
        previous_coa=resolve(data["previous_coa"]) if data.get("previous_coa") else None,
        affiliations=_entries(person.get("affiliations") or data.get("affiliations"), "1"),
        relationships=_entries(data.get("relationships"), "R"),
        advisors=_entries(data.get("advisors"), "G"),
        collaborators=_entries(data.get("collaborators"), "C"),
        editorial=_entries(data.get("editorial"), "B"),
        exclude=[str(x) for x in data.get("exclude") or []],
        overrides={str(k): dict(v or {}) for k, v in (data.get("overrides") or {}).items()},
        same_person=[[str(x) for x in (g if isinstance(g, list) else [g])] for g in data.get("same_person") or []],
        exclude_types=list(data.get("exclude_types", DEFAULT_EXCLUDED_TYPES)),
        optional_column=str(output.get("optional_column", "orcid")),
        affiliation_mode=str(output.get("affiliation", "current")),
        large_author_threshold=int(output.get("large_author_threshold", 50)),
        base_dir=base,
    )
    return profile
