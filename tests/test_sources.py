import datetime as dt

from conftest import FakeHttp

from nsf_coa.models import PubDate, normalize_doi, normalize_orcid
from nsf_coa.sources.crossref import Crossref
from nsf_coa.sources.datacite import DataCite
from nsf_coa.sources.files import dois_from_text, works_from_bibtex, works_from_ris
from nsf_coa.sources.openalex import OpenAlex
from nsf_coa.sources.orcid import Orcid


def test_identifier_normalization():
    assert normalize_doi("https://doi.org/10.1000/ABC.def).") == "10.1000/abc.def"
    assert normalize_doi("doi:10.1000/x(1)") == "10.1000/x(1)"
    assert normalize_orcid("https://orcid.org/0000-0002-6639-7181") == "0000-0002-6639-7181"


def test_pubdate_precision():
    assert PubDate.parse("2024").end == dt.date(2024, 12, 31)
    assert PubDate.parse([2024, 2]).end == dt.date(2024, 2, 29)
    assert PubDate.parse("3/7/2025").start == dt.date(2025, 3, 7)


def test_openalex_parse(load_json):
    works = [OpenAlex.parse_work(r) for r in load_json("openalex_works.json")["results"]]
    first = works[0]
    assert first.doi == "10.1234/alpha.2025"
    assert first.title == "Fire regimes & forests"
    assert first.authors[1].orcid == "0000-0002-0000-0002"
    assert first.authors[2].affiliations == ["Instituto Ejemplo, Lima, Peru"]  # raw string fallback


def test_openalex_works_query_uses_window(load_json):
    http = FakeHttp({"https://api.openalex.org/works": load_json("openalex_works.json")})
    works = OpenAlex(http, api_key="k").works_for_author(["A1"], None, dt.date(2022, 10, 19))
    assert len(works) == 4
    url, params = http.calls[0]
    assert "author.id:A1" in params["filter"] and "from_publication_date:2022-10-19" in params["filter"]
    assert params["api_key"] == "k"


def test_bibtex_google_scholar_export(fixtures):
    works = works_from_bibtex(fixtures / "scholar.bib")
    assert len(works) == 3
    fire, workshop, _ = works
    assert fire.title == "Fire regimes & forests"
    assert [a.family for a in fire.authors] == ["Researcher", "Builder", "de la Cruz"]
    assert workshop.authors_truncated  # "and others"
    assert workshop.authors[2].family == "Müller" and workshop.authors[2].given == "Jörg"
    assert workshop.date == PubDate(2026, 3, 1, "month")
    assert workshop.title == "Open science workshops in practice"


def test_ris(tmp_path):
    path = tmp_path / "x.ris"
    path.write_text("TY  - JOUR\nAU  - Doe, Jane\nAU  - Roe, Rick\nTI  - A title\nPY  - 2024\n"
                    "DO  - 10.5555/xyz\nER  - \n")
    (work,) = works_from_ris(path)
    assert work.doi == "10.5555/xyz" and [a.family for a in work.authors] == ["Doe", "Roe"]


def test_dois_from_html():
    html = '<a href="https://doi.org/10.1000/abc">x</a> see doi:10.2000/DEF.1, and https://doi.org/10.1000%2Fghi'
    assert dois_from_text(html) == ["10.1000/abc", "10.2000/def.1", "10.1000/ghi"]


def test_crossref_parse():
    item = {"DOI": "10.1111/A", "title": ["T &amp; U"], "type": "journal-article",
            "author": [{"given": "Ann", "family": "Lee", "ORCID": "http://orcid.org/0000-0003-0000-0003",
                        "affiliation": [{"name": "Uni A"}]}],
            "published": {"date-parts": [[2025, 4]]}}
    work = Crossref.parse_work(item)
    assert work.title == "T & U" and work.date == PubDate(2025, 4, 1, "month")
    assert work.authors[0].orcid == "0000-0003-0000-0003" and work.authors[0].affiliations == ["Uni A"]


def test_crossref_title_match_threshold():
    items = {"message": {"items": [{"DOI": "10.1111/b", "title": ["Something else entirely"]},
                                   {"DOI": "10.1111/a", "title": ["Open science workshops in practice."],
                                    "author": [{"given": "W", "family": "Zhang"}]}]}}
    cr = Crossref(FakeHttp({"https://api.crossref.org/works": items}))
    assert cr.match_title("Open science workshops in practice", 2026).doi == "10.1111/a"
    assert cr.match_title("Totally unrelated", 2026) is None


def test_datacite_parse():
    attrs = {"doi": "10.5281/ZENODO.1", "titles": [{"title": "Data"}], "publicationYear": 2024,
             "types": {"resourceTypeGeneral": "Dataset"},
             "creators": [{"name": "Lab Org", "nameType": "Organizational"},
                          {"name": "Kim, Sun", "givenName": "Sun", "familyName": "Kim",
                           "nameIdentifiers": [{"nameIdentifier": "https://orcid.org/0000-0004-0000-0004",
                                                "nameIdentifierScheme": "ORCID"}],
                           "affiliation": ["Uni K"]}]}
    work = DataCite.parse_work(attrs)
    assert work.type == "dataset" and len(work.authors) == 1
    assert work.authors[0].orcid == "0000-0004-0000-0004"


def test_orcid_works_and_employment():
    works = {"group": [{
        "external-ids": {"external-id": [
            {"external-id-type": "doi", "external-id-relationship": "version-of", "external-id-value": "10.9999/v"},
            {"external-id-type": "doi", "external-id-relationship": "self", "external-id-value": "10.9999/self"}]},
        "work-summary": [{"title": {"title": {"value": "Paper"}}, "type": "journal-article",
                          "publication-date": {"year": {"value": "2025"}, "month": {"value": "02"}}}]}]}
    employments = {"affiliation-group": [{"summaries": [{"employment-summary": {
        "organization": {"name": "Uni Z"}, "start-date": {"year": {"value": "2020"}}, "end-date": None}}]}]}
    http = FakeHttp({"https://pub.orcid.org/v3.0/0000-0001-0000-0001/works": works,
                     "https://pub.orcid.org/v3.0/0000-0001-0000-0001/employments": employments})
    api = Orcid(http)
    (work,) = api.works("0000-0001-0000-0001")
    assert work.doi == "10.9999/self" and work.date == PubDate(2025, 2, 1, "month")
    (emp,) = api.employments("0000-0001-0000-0001")
    assert emp.organization == "Uni Z" and emp.end is None
