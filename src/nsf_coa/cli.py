"""Command-line interface: ``nsf-coa build | find-author | import-coa | init``."""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from importlib import resources
from pathlib import Path

import yaml

from nsf_coa import __version__
from nsf_coa.coa_reader import previous_coa_to_profile_dict, read_previous_coa
from nsf_coa.collect import collect
from nsf_coa.config import load_profile
from nsf_coa.http import HttpClient, SourceUnavailable
from nsf_coa.merge import build_coa
from nsf_coa.models import PubDate, normalize_openalex_id, normalize_orcid
from nsf_coa.names import format_nsf, parse_name, strip_accents
from nsf_coa.sources.openalex import OpenAlex, institution_names
from nsf_coa.sources.orcid import Orcid
from nsf_coa.writers import write_csv, write_report, write_xlsx


def _http(args, mailto=None) -> HttpClient:
    return HttpClient(
        mailto=mailto,
        cache_dir=None if getattr(args, "no_cache", False) else Path(args.cache_dir).expanduser(),
        offline=getattr(args, "offline", False),
    )


def cmd_build(args) -> int:
    profile = load_profile(args.profile)
    if args.name:
        profile.name = args.name
    if args.orcid:
        profile.orcid = normalize_orcid(args.orcid)
    if args.openalex_id:
        profile.openalex_ids = [normalize_openalex_id(i) for i in args.openalex_id]
    for f in args.file or []:
        profile.bibliography_files.append(Path(f))
    profile.urls += args.url or []
    profile.dois += args.doi or []
    if args.previous_coa:
        profile.previous_coa = Path(args.previous_coa)
    if args.as_of:
        profile.as_of = PubDate.parse(args.as_of).start
    if args.openalex_api_key:
        profile.openalex_api_key = args.openalex_api_key
    if args.mailto:
        profile.mailto = args.mailto
    for flag, attr in (("no_openalex", "use_openalex"), ("no_crossref", "use_crossref"),
                       ("no_orcid", "use_orcid"), ("no_datacite", "use_datacite")):
        if getattr(args, flag):
            setattr(profile, attr, False)

    if not (profile.orcid or profile.openalex_ids or profile.bibliography_files or profile.urls or profile.dois):
        print("Nothing to search: give an ORCID (--orcid), OpenAlex author ID, bibliography file, or URL.\n"
              "Run `nsf-coa find-author \"Your Name\"` to look up your identifiers.", file=sys.stderr)
        return 2

    http = _http(args, profile.mailto)
    print(f"Collecting works since {profile.coauthor_cutoff:%m/%d/%Y} "
          f"({profile.coauthor_months} months before {profile.as_of:%m/%d/%Y})")

    orcid_name, employments, warnings = None, [], []
    if profile.orcid and profile.use_orcid:
        orcid_api = Orcid(http)
        try:
            orcid_name = orcid_api.person_name(profile.orcid)
            employments = orcid_api.employments(profile.orcid)
        except SourceUnavailable as exc:
            warnings.append(f"ORCID profile: {exc}")

    collection = collect(profile, http)
    warnings += collection.warnings

    previous = read_previous_coa(profile.previous_coa) if profile.previous_coa else None
    if previous:
        print(f"  Previous COA {profile.previous_coa.name}: {len(previous.rows)} coded rows")
    if profile.affiliations or (previous and previous.affiliations):
        employments = []  # curated Table 1 wins over ORCID employment history

    author_records = {}
    if collection.openalex and profile.affiliation_mode == "current":
        ids = {a.openalex_id for w in collection.works for a in w.authors if a.openalex_id}
        ids -= set(collection.openalex_author_ids)
        if ids:
            try:
                author_records = collection.openalex.authors_by_ids(sorted(ids))
            except SourceUnavailable as exc:
                warnings.append(f"OpenAlex author affiliations: {exc}")

    result = build_coa(profile, collection.works, previous, author_records, employments, orcid_name)
    result.warnings = warnings + result.warnings
    if not result.self_name:
        result.warnings.append("Your name is unknown; set person.name in the profile.")

    out = Path(args.out)
    family = re.sub(r"[^A-Za-z0-9]+", "", strip_accents(parse_name(result.self_name)[1])) or "researcher"
    stem = f"COA_{family}_{profile.as_of:%Y%m%d}"
    xlsx = write_xlsx(result, out / f"{stem}.xlsx", args.template)
    csv_path = write_csv(result, out / f"{stem}.csv")
    report = write_report(result, out / f"{stem}_report.md")

    a = sum(1 for e in result.table4 if e.code == "A")
    c = sum(1 for e in result.table4 if e.code == "C")
    flagged = sum(1 for e in result.table4 + result.table5 if e.flags)
    print(f"\n{len(result.works)} publications -> {a} co-authors (A), {c} collaborators (C); "
          f"{len(result.table2)} R, {len(result.table3)} G/T, {len(result.table5)} B/E rows")
    if flagged:
        print(f"{flagged} rows are flagged for review")
    for w in result.warnings:
        print(f"warning: {w}")
    print(f"\nWrote:\n  {xlsx}\n  {csv_path}\n  {report}")
    return 0


def cmd_find_author(args) -> int:
    http = _http(args, args.mailto)
    found = False
    oa = OpenAlex(http, args.openalex_api_key)
    try:
        for rec in oa.search_authors(args.name, limit=args.limit):
            found = True
            inst = "; ".join(institution_names(rec)) or "?"
            print(f"OpenAlex {normalize_openalex_id(rec['id'])}  {rec.get('display_name')}  "
                  f"works={rec.get('works_count')}  ORCID={normalize_orcid(rec.get('orcid')) or '-'}  {inst}")
    except SourceUnavailable as exc:
        print(f"OpenAlex unavailable: {exc}", file=sys.stderr)
    given, family = parse_name(args.name)
    try:
        query = f"family-name:{family}" + (f" AND given-names:{given.split()[0]}" if given else "")
        data = http.get_json("https://pub.orcid.org/v3.0/expanded-search/",
                             params={"q": query, "rows": args.limit}, headers={"Accept": "application/json"})
        for rec in (data or {}).get("expanded-result") or []:
            found = True
            inst = "; ".join((rec.get("institution-name") or [])[:3])
            print(f"ORCID    {rec['orcid-id']}  {rec.get('given-names')} {rec.get('family-names')}  {inst}")
    except SourceUnavailable as exc:
        print(f"ORCID unavailable: {exc}", file=sys.stderr)
    if found:
        print("\nOpenAlex often splits one person into several author IDs: list every ID that is you "
              "under person.openalex_ids.")
    return 0 if found else 1


def cmd_import_coa(args) -> int:
    prev = read_previous_coa(args.coa)
    data = previous_coa_to_profile_dict(prev)
    data["previous_coa"] = str(Path(args.coa))
    text = (f"# Manual COA sections imported from {Path(args.coa).name}.\n"
            "# Add person.orcid (and sources) and run: nsf-coa build --profile <this file>\n"
            + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    if args.output:
        Path(args.output).write_text(text)
        print(f"Wrote {args.output}: {len(prev.rows)} coded rows, {len(prev.affiliations)} affiliations")
    else:
        print(text)
    return 0


def cmd_init(args) -> int:
    target = Path(args.output)
    if target.exists() and not args.force:
        print(f"{target} exists; use --force to overwrite", file=sys.stderr)
        return 1
    text = (resources.files("nsf_coa") / "data" / "profile.example.yaml").read_text()
    if args.orcid:
        orcid = normalize_orcid(args.orcid)
        text = text.replace("0000-0000-0000-0000", orcid)
        try:
            name = Orcid(_http(args)).person_name(orcid)
            if name:
                text = text.replace('"Lastname, Firstname M"', f'"{format_nsf(*name)}"')
        except SourceUnavailable:
            pass
    target.write_text(text)
    print(f"Wrote {target}. Edit it, then run: nsf-coa build --profile {target}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nsf-coa", description=__doc__)
    p.add_argument("--version", action="version", version=f"nsf-coa {__version__}")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--cache-dir", default="~/.cache/nsf-coa", help="HTTP response cache (default: %(default)s)")
    p.add_argument("--no-cache", action="store_true", help="do not read or write the HTTP cache")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="collect collaborators and write the COA spreadsheet + review report")
    b.add_argument("--profile", "-p", help="profile YAML (see `nsf-coa init`)")
    b.add_argument("--name", help='your name as "Last, First M"')
    b.add_argument("--orcid", help="your ORCID iD")
    b.add_argument("--openalex-id", action="append", help="your OpenAlex author ID (repeatable)")
    b.add_argument("--file", "--bibtex", action="append",
                   help="BibTeX / RIS / CSL-JSON / DOI-list file, e.g. a Google Scholar export (repeatable)")
    b.add_argument("--url", action="append", help="web page (CV, lab or faculty page) to scan for DOIs (repeatable)")
    b.add_argument("--doi", action="append", help="extra DOI to include (repeatable)")
    b.add_argument("--previous-coa", help="your last COA .xlsx; manual tables are carried forward")
    b.add_argument("--as-of", help="reference date (default today), e.g. 2026-10-19")
    b.add_argument("--out", "-o", default="output", help="output directory (default: %(default)s)")
    b.add_argument("--template", help="alternative NSF COA template .xlsx")
    b.add_argument("--openalex-api-key", help="OpenAlex API key (or set OPENALEX_API_KEY)")
    b.add_argument("--mailto", help="contact email sent to Crossref/OpenAlex polite pools")
    b.add_argument("--offline", action="store_true", help="use only cached HTTP responses")
    for name in ("openalex", "crossref", "orcid", "datacite"):
        b.add_argument(f"--no-{name}", action="store_true", help=f"skip {name}")
    b.set_defaults(func=cmd_build)

    f = sub.add_parser("find-author", help="look up your OpenAlex author IDs and ORCID by name")
    f.add_argument("name")
    f.add_argument("--limit", type=int, default=10)
    f.add_argument("--openalex-api-key")
    f.add_argument("--mailto")
    f.set_defaults(func=cmd_find_author)

    i = sub.add_parser("import-coa", help="convert an existing COA .xlsx into profile YAML")
    i.add_argument("coa")
    i.add_argument("--output", "-o", help="write YAML here instead of stdout")
    i.set_defaults(func=cmd_import_coa)

    n = sub.add_parser("init", help="write a starter profile YAML")
    n.add_argument("--output", "-o", default="coa.yaml")
    n.add_argument("--orcid", help="prefill your ORCID and name")
    n.add_argument("--force", action="store_true")
    n.set_defaults(func=cmd_init)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(message)s")
    if hasattr(args, "openalex_api_key") and not args.openalex_api_key:
        args.openalex_api_key = os.environ.get("OPENALEX_API_KEY")
    if hasattr(args, "mailto") and not args.mailto:
        args.mailto = os.environ.get("NSF_COA_MAILTO")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
