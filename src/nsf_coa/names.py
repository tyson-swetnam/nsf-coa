"""Personal-name parsing, normalization, matching, and NSF formatting.

NSF asks for names as "Last Name, First Name, Middle Initial", e.g.
``Alphaman, Alan K``.  Sources give names in many shapes ("Alan K. Alphaman",
"Alphaman, A. K.", "A.K. Alphaman", structured given/family), so everything is
parsed into ``(given, family)`` first.
"""

from __future__ import annotations

import re
import unicodedata

PARTICLES = {
    "van", "von", "der", "den", "de", "del", "della", "da", "das", "do", "dos",
    "di", "du", "la", "le", "st", "st.", "ter", "ten", "bin", "ibn", "al", "el", "y",
}
SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v", "phd", "ph.d.", "md", "m.d."}


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize_text(text: str | None) -> str:
    """Lower-case, accent-free, punctuation-free text for comparisons."""
    if not text:
        return ""
    text = strip_accents(text).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _clean(text: str) -> str:
    text = re.sub(r"[{}\\]", "", text)  # BibTeX braces / escapes
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,;")


def _strip_suffix(tokens: list[str]) -> list[str]:
    while len(tokens) > 1 and tokens[-1].lower().strip(",") in SUFFIXES:
        tokens = tokens[:-1]
    return tokens


def parse_name(name: str, given: str | None = None, family: str | None = None) -> tuple[str, str]:
    """Split a personal name into ``(given, family)``.

    Structured ``given``/``family`` values win when provided.
    """
    name = _clean(name or "")
    if family:
        family = _clean(family)
        # Some registries put the whole name in familyName ("Mary Rose Posa").
        whole_name_in_family = not given and " " in family and "," not in name and family == name
        if not whole_name_in_family:
            return _clean(given or ""), family
    if not name:
        return "", ""
    if "," in name:
        # "Smith, John", "Smith, Jr., John", "Smith, John, Jr."
        parts = [p.strip() for p in name.split(",") if p.strip()]
        parts = [parts[0]] + [p for p in parts[1:] if p.lower() not in SUFFIXES]
        given = " ".join(_strip_suffix(" ".join(parts[1:]).split()))
        return given, parts[0]
    tokens = _strip_suffix(name.split())
    if len(tokens) == 1:
        return "", tokens[0]
    # Family name starts at the first lower-case particle that is not the first token.
    start = len(tokens) - 1
    for i in range(1, len(tokens) - 1):
        if tokens[i].lower() in PARTICLES:
            start = i
            break
    return " ".join(tokens[:start]), " ".join(tokens[start:])


def given_tokens(given: str) -> list[str]:
    """Split given names into tokens, expanding run-together initials (``J.R.`` / ``JR``)."""
    out: list[str] = []
    for raw in given.split():
        if "." in raw:
            out.extend(p.strip("-") for p in raw.split(".") if p.strip("-"))
        elif 2 <= len(raw) <= 3 and raw.isalpha() and raw.isupper():
            out.extend(raw)  # "TL" -> ["T", "L"]
        else:
            out.append(raw)
    return out


def _fix_case(token: str) -> str:
    """Title-case tokens written entirely in upper or lower case ("BALCH" -> "Balch")."""
    if len(token) > 1 and (token.isupper() or token.islower()):
        return token.title()
    return token


def format_nsf(given: str, family: str) -> str:
    """Format as ``Family, First M``.

    The first given name is written in full and the rest as initials.  A leading
    initial followed by the name someone goes by is kept ("Nagy, R Chelsea").
    """
    family = " ".join(
        t if t.lower() in PARTICLES and t.islower() else _fix_case(t) for t in _clean(family).split()
    )
    tokens = [t if len(t) == 1 else _fix_case(t) for t in given_tokens(given or "")]
    if not tokens:
        return family
    first_full = next((i for i, t in enumerate(tokens) if len(t) > 1), 0)
    head = [t.upper() if len(t) == 1 else t for t in tokens[:first_full + 1]]
    initials = [t[0].upper() for t in tokens[first_full + 1:]]
    return f"{family}, {' '.join(head + initials)}".strip()


def family_key(family: str) -> str:
    return normalize_text(family).replace(" ", "")


def given_key(given: str) -> list[str]:
    return [normalize_text(t).replace(" ", "") for t in given_tokens(given or "") if normalize_text(t)]


def is_initial(token: str) -> bool:
    return len(token) == 1


def given_compatible(a: str, b: str) -> bool:
    """True when two given-name strings could belong to the same person.

    ``John`` ~ ``J`` ~ ``John R`` ~ ``J R``; ``John`` !~ ``Jane``; ``J R`` !~ ``J K``.
    """
    ta, tb = given_key(a), given_key(b)
    if not ta or not tb:
        return True
    for x, y in zip(ta, tb):
        if is_initial(x) or is_initial(y):
            if x[0] != y[0]:
                return False
        elif x != y:
            # allow hyphenated / compound first names written without hyphen
            if not (x.startswith(y) or y.startswith(x)):
                return False
    return True


def given_specificity(given: str) -> tuple[int, int]:
    """Rank given-name strings: more full (non-initial) tokens, then more tokens."""
    toks = given_key(given)
    return (sum(1 for t in toks if not is_initial(t)), len(toks))


def has_full_given(given: str) -> bool:
    """True when at least one given-name token is more than an initial."""
    return any(not is_initial(t) for t in given_key(given))


def well_cased(text: str) -> bool:
    """True for "Mixed Case" text (not ALL CAPS or all lower case)."""
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and not (text.isupper() or text.islower())


def same_person_name(given_a: str, family_a: str, given_b: str, family_b: str) -> bool:
    return family_key(family_a) == family_key(family_b) and given_compatible(given_a, given_b)
