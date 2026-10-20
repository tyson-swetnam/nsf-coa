"""Bibliography files: BibTeX (Google Scholar, Zotero, personal sites), RIS, CSL-JSON, DOI lists.

Google Scholar has no public API and forbids automated scraping, but every
profile can export its articles: on your profile select all articles, click
"Export" -> "BibTeX", and pass the file with ``--bibtex``.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from nsf_coa.models import DOI_RE, AuthorMention, PubDate, Work, normalize_doi
from nsf_coa.names import parse_name

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


# ---------------------------------------------------------------- BibTeX ---
ACCENTS = {'"': "\u0308", "'": "\u0301", "`": "\u0300", "^": "\u0302", "~": "\u0303",
           "=": "\u0304", ".": "\u0307", "c": "\u0327", "v": "\u030c", "u": "\u0306",
           "H": "\u030b", "r": "\u030a", "k": "\u0328"}
LATEX_SYMBOLS = {"ss": "ß", "o": "ø", "O": "Ø", "aa": "å", "AA": "Å", "ae": "æ", "AE": "Æ",
                 "oe": "œ", "OE": "Œ", "l": "ł", "L": "Ł", "i": "ı", "&": "&", "%": "%", "_": "_"}


def latex_to_unicode(text: str) -> str:
    """Convert common LaTeX accent macros (``M{\\"u}ller``, ``\\'{e}``) to Unicode."""
    def accent(m):
        return unicodedata.normalize("NFC", m.group(2) + ACCENTS[m.group(1)])

    text = re.sub(r"\\([\"'`^~=.])\s*\{?\s*([A-Za-z])\s*\}?", accent, text)
    text = re.sub(r"\\([cvuHrk])(?:\s*\{\s*([A-Za-z])\s*\}|\s+([A-Za-z]))",
                  lambda m: unicodedata.normalize("NFC", (m.group(2) or m.group(3)) + ACCENTS[m.group(1)]), text)
    text = re.sub(r"\\(ss|aa|AA|ae|AE|oe|OE|o|O|l|L|i)\b\s*", lambda m: LATEX_SYMBOLS[m.group(1)], text)
    text = re.sub(r"\\([&%_])", lambda m: m.group(1), text)
    return text.replace("{", "").replace("}", "")


def _read_value(text: str, i: int) -> tuple[str, int]:
    """Read a BibTeX field value starting at ``text[i]``; returns (value, next index)."""
    parts = []
    while i < len(text):
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c == "{":
            depth, j = 1, i + 1
            while j < len(text) and depth:
                depth += {"{": 1, "}": -1}.get(text[j], 0)
                j += 1
            parts.append(text[i + 1:j - 1])
            i = j
        elif c == '"':
            j, depth = i + 1, 0
            while j < len(text) and not (text[j] == '"' and depth == 0):
                depth += {"{": 1, "}": -1}.get(text[j], 0)
                j += 1
            parts.append(text[i + 1:j])
            i = j + 1
        else:
            m = re.match(r"[^,#}\s]+", text[i:])
            token = m.group(0) if m else ""
            parts.append(token)
            i += max(len(token), 1)
        # skip whitespace and handle "#" concatenation
        while i < len(text) and text[i].isspace():
            i += 1
        if i < len(text) and text[i] == "#":
            i += 1
            continue
        break
    return "".join(parts), i


def parse_bibtex(text: str) -> list[dict]:
    """Minimal, dependency-free BibTeX parser returning dicts of lower-case fields."""
    entries = []
    for m in re.finditer(r"@(\w+)\s*[{(]", text):
        etype = m.group(1).lower()
        if etype in {"comment", "string", "preamble"}:
            continue
        i = m.end()
        key_end = text.find(",", i)
        if key_end < 0:
            continue
        entry = {"ENTRYTYPE": etype, "ID": text[i:key_end].strip()}
        i = key_end + 1
        while i < len(text):
            fm = re.compile(r"\s*([\w\-:.]+)\s*=\s*").match(text, i)
            if not fm:
                break
            value, i = _read_value(text, fm.end())
            field = fm.group(1).lower()
            if field in {"author", "editor", "title", "journal", "booktitle"}:
                value = latex_to_unicode(value) if field != "author" else value
            entry[field] = re.sub(r"\s+", " ", value).strip()
            while i < len(text) and text[i] in ", \t\r\n":
                i += 1
            if i < len(text) and text[i] in "})":
                break
        entries.append(entry)
    return entries


def _split_authors(field: str) -> list[str]:
    out, depth, current = [], 0, []
    tokens = re.split(r"(\s+and\s+|[{}])", field, flags=re.IGNORECASE)
    for tok in tokens:
        if tok == "{":
            depth += 1
        elif tok == "}":
            depth -= 1
        if depth == 0 and re.fullmatch(r"\s+and\s+", tok or "", flags=re.IGNORECASE):
            out.append("".join(current))
            current = []
        else:
            current.append(tok or "")
    out.append("".join(current))
    return [a.strip() for a in out if a.strip()]


def _bib_date(entry: dict) -> PubDate | None:
    if entry.get("date"):
        return PubDate.parse(entry["date"])
    year = entry.get("year")
    if not year or not re.search(r"\d{4}", year):
        return None
    year_int = int(re.search(r"\d{4}", year).group(0))
    month = (entry.get("month") or "").strip().lower()[:3]
    if month.isdigit():
        return PubDate(year_int, int(month), 1, "month")
    if month in MONTHS:
        return PubDate(year_int, MONTHS[month], 1, "month")
    return PubDate(year_int, 1, 1, "year")


def works_from_bibtex(path: str | Path) -> list[Work]:
    works = []
    for entry in parse_bibtex(Path(path).read_text(encoding="utf-8", errors="replace")):
        names = [latex_to_unicode(n) for n in _split_authors(entry.get("author", ""))]
        truncated = any(n.lower() in {"others", "et al", "et al."} for n in names)
        authors = []
        for n in names:
            if n.lower() in {"others", "et al", "et al."}:
                continue
            given, family = parse_name(n)
            authors.append(AuthorMention(name=n, given=given, family=family))
        doi = normalize_doi(entry.get("doi")) or normalize_doi(entry.get("url"))
        works.append(Work(
            title=entry.get("title", "(untitled)"),
            date=_bib_date(entry),
            authors=authors,
            doi=doi,
            type=entry["ENTRYTYPE"],
            venue=entry.get("journal") or entry.get("booktitle") or entry.get("publisher"),
            sources={f"bibtex:{Path(path).name}"},
            authors_truncated=truncated,
        ))
    return works


# ------------------------------------------------------------------- RIS ---
def works_from_ris(path: str | Path) -> list[Work]:
    works, current = [], None
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^([A-Z][A-Z0-9])  -\s?(.*)$", line)
        if not m:
            continue
        tag, value = m.group(1), m.group(2).strip()
        if tag == "TY":
            current = {"authors": [], "TY": value}
        elif current is None:
            continue
        elif tag in {"AU", "A1"}:
            current["authors"].append(value)
        elif tag == "ER":
            authors = [AuthorMention(name=a, given=parse_name(a)[0], family=parse_name(a)[1])
                       for a in current["authors"]]
            date = PubDate.parse(current.get("DA", "").replace("/", "-").strip("-")) or PubDate.parse(current.get("PY") or current.get("Y1"))
            works.append(Work(
                title=current.get("TI") or current.get("T1") or "(untitled)",
                date=date,
                authors=authors,
                doi=normalize_doi(current.get("DO")),
                type=current.get("TY", "").lower() or None,
                venue=current.get("JO") or current.get("T2") or current.get("JF"),
                sources={f"ris:{Path(path).name}"},
            ))
            current = None
        else:
            current.setdefault(tag, value)
    return works


# -------------------------------------------------------------- CSL-JSON ---
def works_from_csl_json(path: str | Path) -> list[Work]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("items", [data])
    works = []
    for item in data:
        authors = []
        for a in item.get("author") or []:
            if a.get("family"):
                authors.append(AuthorMention(name=f"{a.get('given', '')} {a['family']}".strip(),
                                             given=a.get("given"), family=a["family"]))
            elif a.get("literal"):
                given, family = parse_name(a["literal"])
                authors.append(AuthorMention(name=a["literal"], given=given, family=family))
        parts = ((item.get("issued") or {}).get("date-parts") or [[None]])[0]
        works.append(Work(
            title=item.get("title") or "(untitled)",
            date=PubDate.parse(parts),
            authors=authors,
            doi=normalize_doi(item.get("DOI")),
            type=item.get("type"),
            venue=item.get("container-title"),
            sources={f"csl:{Path(path).name}"},
        ))
    return works


# ------------------------------------------------------------------ DOIs ---
def dois_from_text(text: str) -> list[str]:
    """Every distinct DOI mentioned in free text or HTML (doi.org links included)."""
    seen: dict[str, None] = {}
    text = text.replace("%2F", "/").replace("%2f", "/")
    for m in DOI_RE.finditer(text):
        doi = normalize_doi(m.group(0))
        if doi:
            seen.setdefault(doi, None)
    return list(seen)


def works_from_file(path: str | Path) -> list[Work]:
    """Dispatch on file extension."""
    suffix = Path(path).suffix.lower()
    if suffix in {".bib", ".bibtex"}:
        return works_from_bibtex(path)
    if suffix in {".ris", ".txt"} and Path(path).read_text(errors="replace").lstrip().startswith("TY  -"):
        return works_from_ris(path)
    if suffix == ".ris":
        return works_from_ris(path)
    if suffix in {".json", ".csljson"}:
        return works_from_csl_json(path)
    # Any other text file: harvest DOIs and let the resolver fill in authors.
    return [Work(title=f"DOI {d}", date=None, doi=d, sources={f"doi-list:{Path(path).name}"})
            for d in dois_from_text(Path(path).read_text(errors="replace"))]
