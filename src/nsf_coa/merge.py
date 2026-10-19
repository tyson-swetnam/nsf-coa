"""Turn collected works + profile + previous COA into the five NSF COA tables."""

from __future__ import annotations

import datetime as dt
import difflib
import logging
import re
from collections import Counter
from dataclasses import dataclass, field

from nsf_coa.coa_reader import PreviousCOA
from nsf_coa.config import ManualEntry, Profile
from nsf_coa.models import (
    AuthorMention,
    Entry,
    Work,
    normalize_doi,
    normalize_openalex_id,
    normalize_orcid,
)
from nsf_coa.names import (
    family_key,
    format_nsf,
    given_compatible,
    given_key,
    given_specificity,
    has_full_given,
    normalize_text,
    parse_name,
    same_person_name,
    well_cased,
)

log = logging.getLogger(__name__)


@dataclass
class COAResult:
    self_name: str
    as_of: dt.date
    table1: list[Entry] = field(default_factory=list)
    table2: list[Entry] = field(default_factory=list)
    table3: list[Entry] = field(default_factory=list)
    table4: list[Entry] = field(default_factory=list)
    table5: list[Entry] = field(default_factory=list)
    dropped: list[Entry] = field(default_factory=list)
    works: list[Work] = field(default_factory=list)  # works used for Table 4
    works_without_authors: list[Work] = field(default_factory=list)
    works_self_not_found: list[Work] = field(default_factory=list)
    works_truncated: list[Work] = field(default_factory=list)  # author list ends in "and others"
    works_skipped: list[tuple[Work, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ self ---
class SelfMatcher:
    def __init__(self, names: list[str], orcid: str | None, openalex_ids: list[str]):
        self.names = [parse_name(n) for n in names if n]
        self.orcid = normalize_orcid(orcid)
        self.openalex_ids = {normalize_openalex_id(i) for i in openalex_ids if i}

    def strict(self, m: AuthorMention) -> bool:
        if self.orcid and m.orcid:
            return m.orcid == self.orcid
        if m.openalex_id and m.openalex_id in self.openalex_ids:
            return True
        given, family = parse_name(m.name, m.given, m.family)
        return any(same_person_name(given, family, g, f) for g, f in self.names)

    def loose(self, m: AuthorMention) -> bool:
        """Same family name and first initial; used only when strict matching finds nobody."""
        given, family = parse_name(m.name, m.given, m.family)
        gk = given_key(given)
        for g, f in self.names:
            fk = given_key(g)
            if family_key(family) == family_key(f) and (not gk or not fk or gk[0][0] == fk[0][0]):
                return True
        return False


# -------------------------------------------------------------- clusters ---
@dataclass
class Mention:
    given: str
    family: str
    orcid: str | None
    openalex_id: str | None
    affiliations: list[str]
    work: Work


class UnionFind:
    def __init__(self, mentions: list[Mention]):
        self.parent = list(range(len(mentions)))
        self.orcids = [{m.orcid} if m.orcid else set() for m in mentions]

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i: int, j: int) -> bool:
        ri, rj = self.find(i), self.find(j)
        if ri == rj:
            return True
        if self.orcids[ri] and self.orcids[rj] and not (self.orcids[ri] & self.orcids[rj]):
            return False  # two different ORCIDs: never the same person
        self.parent[rj] = ri
        self.orcids[ri] |= self.orcids[rj]
        return True

    def groups(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {}
        for i in range(len(self.parent)):
            out.setdefault(self.find(i), []).append(i)
        return out


def cluster_mentions(mentions: list[Mention]) -> tuple[list[list[Mention]], set[int]]:
    """Group mentions into people.  Returns (clusters, indexes of ambiguous clusters)."""
    uf = UnionFind(mentions)
    by_orcid: dict[str, int] = {}
    by_oa: dict[str, int] = {}
    for i, m in enumerate(mentions):
        if m.orcid:
            uf.union(by_orcid.setdefault(m.orcid, i), i)
        if m.openalex_id:
            uf.union(by_oa.setdefault(m.openalex_id, i), i)

    # Name pass 1: identical family + compatible full first names.
    buckets: dict[str, list[int]] = {}
    for i, m in enumerate(mentions):
        buckets.setdefault(family_key(m.family), []).append(i)
    for idxs in buckets.values():
        full = [i for i in idxs if has_full_given(mentions[i].given)]
        for a_pos, a in enumerate(full):
            for b in full[a_pos + 1:]:
                ga, gb = given_key(mentions[a].given), given_key(mentions[b].given)
                if ga[0] == gb[0] and given_compatible(mentions[a].given, mentions[b].given):
                    uf.union(a, b)

    # Name pass 2: initials-only mentions join the single compatible cluster, if unique.
    ambiguous_roots: set[int] = set()
    for idxs in buckets.values():
        initials = [i for i in idxs if not has_full_given(mentions[i].given)]
        for i in initials:
            roots = {
                uf.find(j) for j in idxs
                if j != i and uf.find(j) != uf.find(i)
                and given_compatible(mentions[i].given, mentions[j].given)
            }
            if len(roots) == 1:
                if not uf.union(next(iter(roots)), i):
                    ambiguous_roots.add(uf.find(i))
            elif len(roots) > 1:
                full_roots = {r for r in roots if any(
                    uf.find(j) == r and has_full_given(mentions[j].given) for j in idxs)}
                if len(full_roots) == 1:
                    uf.union(next(iter(full_roots)), i)
                else:
                    ambiguous_roots.add(uf.find(i))

    groups = uf.groups()
    clusters, ambiguous = [], set()
    for root, members in groups.items():
        if root in ambiguous_roots or any(uf.find(r) == root for r in ambiguous_roots):
            ambiguous.add(len(clusters))
        clusters.append([mentions[i] for i in members])
    return clusters, ambiguous


# --------------------------------------------------------------- helpers ---
def _last_active(works: list[Work], as_of: dt.date) -> dt.date | None:
    dates = [w.date.end for w in works if w.date]
    return min(max(dates), as_of) if dates else None


def _name_matches(name: str, other: str) -> bool:
    g1, f1 = parse_name(name)
    g2, f2 = parse_name(other)
    return same_person_name(g1, f1, g2, f2)


def _similar_name(name: str, other: str) -> bool:
    """Looser match for hand-typed names: a small family-name typo ("Stavaros" ~ "Stavros")
    or a dropped leading initial ("Nagy, Chelsea" ~ "Nagy, R Chelsea")."""
    g1, f1 = parse_name(name)
    g2, f2 = parse_name(other)
    full1 = [t for t in given_key(g1) if len(t) > 1]
    full2 = [t for t in given_key(g2) if len(t) > 1]
    if not full1 or not full2:
        return False
    k1, k2 = family_key(f1), family_key(f2)
    if k1 == k2:
        return bool(set(full1) & set(full2)) and (set(full1) <= set(full2) or set(full2) <= set(full1))
    if full1[0] != full2[0] or min(len(k1), len(k2)) < 5:
        return False
    return difflib.SequenceMatcher(None, k1, k2).ratio() >= 0.85


def _find(entries: list[Entry], name: str, fuzzy: bool = True) -> tuple[Entry | None, bool]:
    """Return (matching entry, matched exactly)."""
    for e in entries:
        if _name_matches(e.name, name):
            return e, True
    if fuzzy:
        for e in entries:
            if _similar_name(e.name, name):
                return e, False
    return None, False


def _nsf_name(name: str) -> str:
    """Normalize a hand-typed name ("Sean Hendryx", "Gillan, Jeffery K.") to "Last, First M"."""
    given, family = parse_name(name)
    return format_nsf(given, family) if family else name.strip()


def _manual_to_entry(m: ManualEntry, code: str, origin: str) -> Entry:
    given, family = parse_name(m.name)
    return Entry(
        code=(m.code or code).upper(),
        name=format_nsf(given, family) if family else m.name,
        affiliation=m.affiliation,
        optional=m.journal if code in {"B", "E"} else m.optional,
        relationship=m.relationship,
        last_active=m.last_active,
        origin=origin,
    )


def _prev_to_entry(row: dict) -> Entry:
    return Entry(
        code=row["code"],
        name=_nsf_name(row["name"]),
        affiliation=row.get("affiliation", ""),
        optional=row.get("journal") or row.get("optional") or "",
        relationship=row.get("relationship", ""),
        last_active=row.get("last_active"),
        origin="previous COA",
    )


def _merge_manual(primary: list[Entry], extra: list[Entry], by_code: bool = False) -> list[Entry]:
    out = list(primary)
    for e in extra:
        match = next((x for x in out if (not by_code or x.code == e.code) and _name_matches(x.name, e.name)), None)
        if match:
            match.affiliation = match.affiliation or e.affiliation
            match.optional = match.optional or e.optional
            match.relationship = match.relationship or e.relationship
            if e.last_active and (not match.last_active or e.last_active > match.last_active):
                match.last_active = e.last_active
        else:
            out.append(e)
    return out


def _sort(entries: list[Entry]) -> list[Entry]:
    return sorted(entries, key=lambda e: (e.code, normalize_text(e.name)))


# ------------------------------------------------------------------ main ---
def build_coa(
    profile: Profile,
    works: list[Work],
    previous: PreviousCOA | None = None,
    author_records: dict[str, dict] | None = None,
    employments: list | None = None,
    orcid_name: tuple[str, str] | None = None,
) -> COAResult:
    """Assemble all five tables.  ``author_records`` maps OpenAlex IDs to author records
    (for current affiliations); ``employments`` are ORCID employment records."""
    from nsf_coa.sources.openalex import institution_names

    self_name = _nsf_name(profile.name or (previous.self_name if previous else "") or (
        format_nsf(*orcid_name) if orcid_name else ""))
    names = [self_name, *profile.aliases]
    if previous and previous.self_name:
        names.append(previous.self_name)
    if orcid_name:
        names.append(format_nsf(*orcid_name))
    matcher = SelfMatcher(names, profile.orcid, profile.openalex_ids)
    result = COAResult(self_name=self_name, as_of=profile.as_of)

    excluded_dois = {normalize_doi(x) for x in profile.exclude if normalize_doi(x)}
    excluded_ids = {normalize_orcid(x) or normalize_openalex_id(x) for x in profile.exclude
                    if normalize_orcid(x) or normalize_openalex_id(x)}
    excluded_names = [x for x in profile.exclude if not normalize_doi(x) and not (normalize_orcid(x) or normalize_openalex_id(x))]
    override_names = {k: v for k, v in profile.overrides.items()}

    # ---- Table 4 (A): co-authors from publications ----------------------
    mentions: list[Mention] = []
    for w in works:
        if w.doi and w.doi in excluded_dois:
            result.works_skipped.append((w, "excluded in profile"))
            continue
        if w.type and w.type.lower() in {t.lower() for t in profile.exclude_types}:
            result.works_skipped.append((w, f"type '{w.type}' excluded"))
            continue
        if w.date and w.date.end < profile.coauthor_cutoff:
            result.works_skipped.append((w, f"published {w.date}, before {profile.coauthor_cutoff}"))
            continue
        if not w.authors:
            result.works_without_authors.append(w)
            continue
        flags = [matcher.strict(a) for a in w.authors]
        if not any(flags):
            flags = [matcher.loose(a) for a in w.authors]
            if not any(flags):
                result.works_self_not_found.append(w)
                continue
        result.works.append(w)
        if w.authors_truncated:
            result.works_truncated.append(w)
        for author, is_self in zip(w.authors, flags):
            if is_self:
                author.is_self = True
                continue
            given, family = parse_name(author.name, author.given, author.family)
            if not family:
                continue
            for key, ov in override_names.items():
                if ov.get("name") and _name_matches(format_nsf(given, family), key):
                    given, family = parse_name(ov["name"])
                    break
            mentions.append(Mention(given, family, author.orcid, author.openalex_id, author.affiliations, w))

    clusters, ambiguous = cluster_mentions(mentions)
    coauthors: list[Entry] = []
    for idx, cluster in enumerate(clusters):
        best = max(cluster, key=lambda m: (given_specificity(m.given), well_cased(m.family + m.given),
                                           len(m.given)))
        name = format_nsf(best.given, best.family)
        orcid = Counter(m.orcid for m in cluster if m.orcid).most_common(1)
        oa_ids = Counter(m.openalex_id for m in cluster if m.openalex_id)
        cluster_works = list({id(m.work): m.work for m in cluster}.values())
        entry = Entry(
            code="A",
            name=name,
            orcid=orcid[0][0] if orcid else None,
            openalex_ids=set(oa_ids),
            works=sorted(cluster_works, key=lambda w: w.date.end if w.date else dt.date.min, reverse=True),
            last_active=_last_active(cluster_works, profile.as_of),
            origin="publications",
        )
        # Affiliation: current (OpenAlex last-known) or from the most recent co-authored work.
        affiliation = []
        if profile.affiliation_mode == "current" and author_records:
            for oa_id, _ in oa_ids.most_common():
                rec = author_records.get(oa_id)
                if rec:
                    affiliation = institution_names(rec)
                    if affiliation:
                        break
        if not affiliation:
            for m in sorted(cluster, key=lambda m: m.work.date.end if m.work.date else dt.date.min, reverse=True):
                if m.affiliations:
                    affiliation = m.affiliations
                    break
        entry.affiliation = "; ".join(dict.fromkeys(affiliation[:2]))
        if not entry.affiliation:
            entry.flags.append("no affiliation found")
        if profile.optional_column == "orcid" and entry.orcid:
            entry.optional = f"ORCID {entry.orcid}"
        if idx in ambiguous:
            entry.flags.append("name ambiguous (initials match several people) - verify")
        if all(len(w.authors) > profile.large_author_threshold for w in cluster_works):
            entry.flags.append(f"only via large-team paper(s) (>{profile.large_author_threshold} authors)")
        if any(w.date is None for w in cluster_works):
            entry.flags.append("includes undated work")
        coauthors.append(entry)

    # Exclusions and overrides.
    kept = []
    for e in coauthors:
        if (e.orcid and e.orcid in excluded_ids) or (e.openalex_ids & excluded_ids) or any(
                _name_matches(e.name, x) for x in excluded_names):
            e.flags.append("excluded in profile")
            result.dropped.append(e)
            continue
        for key, ov in override_names.items():
            if _name_matches(e.name, key) or (ov.get("name") and _name_matches(e.name, ov["name"])):
                e.name = ov.get("name", e.name)
                e.affiliation = ov.get("affiliation", e.affiliation)
                e.optional = ov.get("optional", ov.get("email", e.optional))
                break
        kept.append(e)
    coauthors = kept
    by_name: dict[str, list[Entry]] = {}
    for e in coauthors:
        by_name.setdefault(normalize_text(e.name), []).append(e)
    for same in by_name.values():
        if len(same) > 1:
            for e in same:
                e.flags.append("same name as another row (different ORCID or unlinked records) - merge "
                               "with `overrides` or `exclude` if this is one person")

    # ---- previous COA reconciliation --------------------------------------
    cutoff = profile.coauthor_cutoff
    if previous:
        for row in previous.by_code("A"):
            prev = _prev_to_entry(row)
            match, exact = _find(coauthors, prev.name)
            if match:
                if not exact:
                    match.flags.append(f"matched previous-COA row '{prev.name}' by similar spelling - check")
                if prev.optional and ("@" in prev.optional or not match.optional):
                    match.optional = prev.optional  # keep a curated email over an ORCID
                if not match.affiliation and prev.affiliation:
                    match.affiliation = prev.affiliation
                    match.flags = [f for f in match.flags if f != "no affiliation found"]
                continue
            if any(_name_matches(prev.name, x) for x in excluded_names):
                continue
            if prev.last_active and prev.last_active < cutoff:
                prev.flags.append(f"last active {prev.last_active:%m/%d/%Y} is older than {profile.coauthor_months} months")
                result.dropped.append(prev)
            else:
                prev.flags.append("carried over from previous COA; not found in current sources - verify"
                                  if prev.last_active else
                                  "carried over from previous COA (undated) - verify still within 48 months")
                coauthors.append(prev)

    # ---- Table 4 (C): collaborators (grants, projects) ----------------------
    collaborators = [_manual_to_entry(m, "C", "profile") for m in profile.collaborators]
    if previous:
        collaborators = _merge_manual(collaborators, [_prev_to_entry(r) for r in previous.by_code("C")])
    kept_c = []
    for c in collaborators:
        c.code = "C"
        if c.last_active and c.last_active < cutoff:
            c.flags.append(f"last active {c.last_active:%m/%d/%Y} is older than {profile.coauthor_months} months")
            still, _ = _find(coauthors, c.name, fuzzy=False)
            if still:
                c.flags.append("still listed as a co-author (A) from recent publications")
            result.dropped.append(c)
            continue
        if c.origin == "previous COA" and not c.last_active:
            c.flags.append("from previous COA (undated) - confirm collaboration is ongoing")
        # NSF FAQ: co-authors who are also collaborators are listed once, under C.
        dup, exact = _find(coauthors, c.name)
        if dup:
            coauthors.remove(dup)
            if not exact:
                c.flags.append(f"'{c.name}' matched co-author '{dup.name}' by similar spelling; "
                               "using the publication spelling - check")
                c.name = dup.name
            c.affiliation = c.affiliation or dup.affiliation
            c.optional = c.optional or dup.optional
            c.orcid = dup.orcid
            c.works = dup.works
            if dup.last_active and (not c.last_active or dup.last_active > c.last_active):
                c.last_active = dup.last_active
            c.flags.append("also a co-author (listed once, under C)")
        kept_c.append(c)
    result.table4 = _sort(coauthors) + _sort(kept_c)
    result.table4 = sorted(result.table4, key=lambda e: (e.code, normalize_text(e.name)))

    # ---- Table 1: own affiliations, last 12 months -----------------------
    # Precedence: profile, then ORCID employment (usually the freshest), then the previous COA.
    t1 = [Entry(code="1", name="", affiliation=m.affiliation or m.name, last_active=m.last_active,
                origin="profile") for m in profile.affiliations]
    for emp in employments or []:
        end = emp.end.end if emp.end else None
        if end and end < profile.affiliation_cutoff:
            continue  # long-past positions on the ORCID record are not news
        t1.append(Entry(code="1", name="", affiliation=emp.organization, last_active=end,
                        origin="ORCID employment"))
    if previous:
        for a in previous.affiliations:
            t1.append(Entry(code="1", name="", affiliation=a["affiliation"], last_active=a["last_active"],
                            origin="previous COA"))
    seen, table1 = set(), []
    for e in t1:
        key = re.sub(r"^the ", "", normalize_text(e.affiliation))
        if not key or key in seen:
            continue
        seen.add(key)
        if e.last_active and e.last_active < profile.affiliation_cutoff:
            e.flags.append("ended more than 12 months ago")
            result.dropped.append(e)
            continue
        if e.last_active and e.last_active >= profile.as_of:
            e.last_active = None  # ongoing
        table1.append(e)
    result.table1 = table1

    # ---- Table 2: relationships ------------------------------------------
    t2 = [_manual_to_entry(m, "R", "profile") for m in profile.relationships]
    if previous:
        t2 = _merge_manual(t2, [_prev_to_entry(r) for r in previous.by_code("R")])
    for e in t2:
        e.code = "R"
    result.table2 = _sort(t2)

    # ---- Table 3: Ph.D. advisors (G) and advisees (T) --------------------
    t3 = [_manual_to_entry(m, "G", "profile") for m in profile.advisors]
    if previous:
        t3 = _merge_manual(t3, [_prev_to_entry(r) for r in previous.by_code("G", "T")], by_code=True)
        for r in previous.by_code("P"):
            p = _prev_to_entry(r)
            p.flags.append("category P (postdoc) no longer exists; list in Table 2 only if the relationship "
                           "would preclude review")
            result.dropped.append(p)
    result.table3 = _sort(t3)

    # ---- Table 5: editorial, last 24 months ------------------------------
    t5 = [_manual_to_entry(m, "B", "profile") for m in profile.editorial]
    if previous:
        t5 = _merge_manual(t5, [_prev_to_entry(r) for r in previous.by_code("B", "E")], by_code=True)
    table5 = []
    for e in t5:
        if e.last_active and e.last_active < profile.editor_cutoff:
            e.flags.append(f"last active older than {profile.editor_months} months")
            result.dropped.append(e)
        else:
            table5.append(e)
    result.table5 = _sort(table5)
    return result
