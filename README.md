# nsf-coa

Build your NSF **Collaborators and Other Affiliations (COA)** spreadsheet from open
scholarly data instead of retyping it every proposal season.

`nsf-coa` finds everyone you co-authored with in the last 48 months by combining
**OpenAlex**, **ORCID**, **Crossref**, **DataCite**, a **Google Scholar** BibTeX export, and
DOIs harvested from your **CV, lab, or faculty web pages**. It merges name variants
("B. Builder" = "Bob Builder"), drops you from your own list, applies the NSF time
windows, carries forward the hand-maintained parts of your previous COA, and writes the
result into **NSF's official COA Excel template**, ready to upload to Research.gov.
It also writes a review report showing the evidence behind every row and the rows that
need a human decision.

> **You remain responsible for your COA.** Open data is incomplete and sometimes
> wrong, especially for common names and for people who have changed institutions.
> Read the review report before you upload.

---

## Quick start

```bash
pip install git+https://github.com/tyson-swetnam/nsf-coa
# or, from a clone:  pip install -e .

# 1. Find your identifiers
nsf-coa find-author "Firstname Lastname"

# 2. Optional but recommended: a free OpenAlex API key (https://help.openalex.org/api/authentication/)
export OPENALEX_API_KEY=...

# 3. Build
nsf-coa build --orcid 0000-0000-0000-0000 --previous-coa my_last_COA.xlsx
```

This writes three files to `output/`:

| File | What it is |
|---|---|
| `COA_<Lastname>_<date>.xlsx` | The NSF COA template with Tables 1–5 filled in. Upload this. |
| `COA_<Lastname>_<date>_report.md` | Review report: flagged rows, dropped rows, unresolved publications, and the evidence for each co-author. |
| `COA_<Lastname>_<date>.csv` | Every row with provenance (ORCID, OpenAlex IDs, number of shared works, most recent shared work, flags). |

For repeat use, keep your settings in a profile:

```bash
nsf-coa init --orcid 0000-0000-0000-0000      # writes coa.yaml
nsf-coa import-coa my_last_COA.xlsx -o coa.yaml   # or start from your last COA
nsf-coa build --profile coa.yaml
```

Every option is documented in
[`src/nsf_coa/data/profile.example.yaml`](src/nsf_coa/data/profile.example.yaml).

## Where the data comes from

| Source | What it gives | Notes |
|---|---|---|
| [OpenAlex](https://openalex.org) | Your works with full author lists, author IDs, ORCIDs, and institutions; collaborators' **current** institutions | Main source. Needs a free API key for real use (the keyless budget is shared per IP address). OpenAlex sometimes splits one person into several author IDs, so list all of yours in `person.openalex_ids`. |
| [ORCID](https://orcid.org) | The works and employment you curate on your ORCID record | Work summaries carry no co-authors, so DOIs are resolved through OpenAlex, Crossref, or DataCite. Employment fills Table 1 when you give no affiliations. |
| [Crossref](https://www.crossref.org) | Works whose publisher metadata carries your ORCID; author lists for any DOI; title matching for citations without DOIs | No key needed. Set `mailto` to use the faster "polite pool". |
| [DataCite](https://datacite.org) | Author lists for dataset, software, and report DOIs (Zenodo, Figshare, Dryad, ...) | No key needed. |
| Google Scholar | Your profile, exported as BibTeX | Google Scholar has no API and forbids scraping. On your profile, select all articles, click **Export → BibTeX**, and pass the file with `--bibtex scholar.bib`. Citations without DOIs are matched to Crossref by title. Author lists truncated with "and others" are flagged. |
| Web pages | DOIs found on a CV, lab site, or faculty page (`--url`) | Only DOIs are harvested; papers listed without DOIs need a BibTeX/RIS file instead. |
| Files | BibTeX, RIS, CSL-JSON (Zotero, Mendeley, EndNote), or any text file of DOIs (`--file`) | |
| Previous COA | Tables 1, 2, 3, 5 and the `C:` rows of Table 4 | People and relationships no database knows about are carried forward. Co-authors (`A:`) are re-derived from publications. Any previous co-author not found again, but still inside the window, is kept and flagged. |

HTTP responses are cached in `~/.cache/nsf-coa` for 7 days. Use `--offline` to rebuild
from the cache alone.

## How NSF's rules are applied

Rules come from the [NSF COA FAQ](https://www.nsf.gov/funding/senior-personnel-documents/faq/coa)
and the instructions in the COA template (PAPPG Chapter II).

| Table | Code | Who | Window | How `nsf-coa` fills it |
|---|---|---|---|---|
| 1 | | Your name and organizational affiliations | last 12 months | `person.affiliations`, your previous COA, or ORCID employment |
| 2 | `R:` | Personal, family, or business relationships that would preclude reviewing | none | `relationships` in the profile and your previous COA. **Never inferred from data.** |
| 3 | `G:` / `T:` | Your Ph.D. advisors and all of your Ph.D. thesis advisees | none | `advisors` in the profile and your previous COA |
| 4 | `A:` | Co-authors on any book, article, report, abstract, or paper | last 48 months | Publications from all sources |
| 4 | `C:` | Collaborators on funded awards, projects, graduate research | last 48 months | `collaborators` in the profile and your previous COA |
| 5 | `B:` / `E:` | Editor-in-chief (as a board member) and co-editors you work with directly | last 24 months | `editorial` in the profile and your previous COA |

Details:

- **Large author lists.** NSF says to list all co-authors, whatever their position. Everyone
  is included. People who appear *only* on papers with more than 50 authors are flagged so
  you can talk to your Program Officer if the list is unreasonable
  (`output.large_author_threshold`).
- **A co-author who is also a collaborator** is listed once, under `C:` (NSF FAQ).
- **Last Active** is the date of the most recent shared publication, capped at today. A
  year-only date counts as December 31 of that year. Dates in Table 1 are blank for current
  affiliations.
- **Affiliation** is the co-author's *current* institution from OpenAlex, because the FAQ
  says to list people who moved under their current affiliation. If OpenAlex has none, the
  institution on the most recent shared paper is used instead.
- **Optional column.** Tables 2–4 have an "Optional (email, Department)" column for telling
  apart people with common names. `nsf-coa` puts the person's ORCID there when one is known.
  A curated email from your previous COA takes priority. To leave the column blank, set
  `output.optional_column: none`.
- **Errata, paratext, peer reviews, and retraction notices** are not co-authorship and are
  skipped (`exclude_types`).
- **Postdoctoral sponsors (old code `P:`)** are no longer a category. They are reported, not
  written, so you can decide whether they belong in Table 2.

## Fixing the output

The review report lists every row that needs a decision. Fix them in the profile, not in the
spreadsheet, so the next run keeps your corrections:

```yaml
exclude:                      # false positives: names, ORCIDs, OpenAlex IDs, or DOIs
  - "Smith, J"
  - 10.1234/not-my-paper
overrides:                    # correct a person, or merge two name variants
  "Builder, Bob": {affiliation: "New Employer", optional: "bob@new.edu"}
  "Smith, Bill": {name: "Smith, William J"}
person:
  aliases: ["Lastname, F", "Firstname Middle Lastname"]  # spellings of your own name
```

Rows are flagged when:

- their initials match several people;
- two rows share a name but carry different ORCIDs (one person with two ORCIDs, or two people);
- no affiliation was found;
- they appear only on large-team papers;
- they were carried forward from your previous COA without being found in current sources.

## Privacy

Your profile and COA can contain family and business relationships, emails, and student
names. The repository's `.gitignore` excludes `coa.yaml`, `profiles/`, `private/`,
`output/`, and all `.xlsx`/`.bib` files except the bundled template and test fixtures. Keep
it that way if you fork this repository. Personal data is sent only to the services
listed above: your ORCID, author IDs, and the DOIs and titles of your papers.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check src tests
```

The tests use canned API responses and never touch the network. The bundled
`src/nsf_coa/data/coa_template.xlsx` is NSF's official template
(<https://www.nsf.gov/bfa/dias/policy/coa/coa_template.xlsx>). When NSF revises it, replace
the file, or pass `--template new_template.xlsx`. Tables are located by their header rows,
so moderate layout changes still work.

Contributions are welcome, especially new sources (Semantic Scholar, DBLP, NIH RePORTER,
NSF award search for `C:` collaborators) and better name disambiguation.

## License

CC0 1.0 Universal. See [LICENSE](LICENSE).
