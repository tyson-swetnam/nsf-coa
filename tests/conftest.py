import datetime as dt
import json
from pathlib import Path

import pytest

from nsf_coa.config import profile_from_dict
from nsf_coa.http import HttpClient

FIXTURES = Path(__file__).parent / "fixtures"


class FakeHttp(HttpClient):
    """Serves canned JSON keyed by URL prefix; records every request."""

    def __init__(self, routes: dict[str, object]):
        super().__init__(cache_dir=None)
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []

    def get_json(self, url, params=None, headers=None, cache_key_exclude=()):
        self.calls.append((url, params or {}))
        for prefix in sorted(self.routes, key=len, reverse=True):
            if url.startswith(prefix):
                value = self.routes[prefix]
                return value(url, params or {}) if callable(value) else value
        return None

    def get_text(self, url):
        self.calls.append((url, {}))
        return self.routes.get(url, "")


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture
def load_json():
    def _load(name):
        return json.loads((FIXTURES / name).read_text())
    return _load


@pytest.fixture
def profile(tmp_path):
    def _make(**extra):
        data = {
            "person": {"name": "Researcher, Rita Q", "orcid": "0000-0001-0000-0001",
                       "affiliations": [{"name": "University of Example"}]},
            "window": {"as_of": "2026-10-19"},
        }
        for key, value in extra.items():
            data[key] = value
        return profile_from_dict(data, tmp_path)
    return _make


@pytest.fixture
def as_of():
    return dt.date(2026, 10, 19)
