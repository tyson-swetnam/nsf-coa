"""A small, polite HTTP client with retries and an on-disk JSON cache."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path

import requests

from nsf_coa import __version__

log = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path(os.environ.get("NSF_COA_CACHE", Path.home() / ".cache" / "nsf-coa"))


class SourceUnavailable(RuntimeError):
    """A data source refused or failed the request; other sources can continue."""


class HttpClient:
    def __init__(
        self,
        mailto: str | None = None,
        cache_dir: Path | None = DEFAULT_CACHE_DIR,
        cache_ttl_days: float = 7,
        offline: bool = False,
        max_retry_wait: float = 60,
        timeout: float = 30,
    ):
        self.mailto = mailto
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.cache_ttl = cache_ttl_days * 86400
        self.offline = offline
        self.max_retry_wait = max_retry_wait
        self.timeout = timeout
        self.session = requests.Session()
        agent = f"nsf-coa/{__version__} (+https://github.com/tyson-swetnam/nsf-coa"
        if mailto:
            agent += f"; mailto:{mailto}"
        self.session.headers["User-Agent"] = agent + ")"

    def _cache_path(self, key: str) -> Path | None:
        if not self.cache_dir:
            return None
        digest = hashlib.sha256(key.encode()).hexdigest()
        return self.cache_dir / digest[:2] / f"{digest}.json"

    def get_json(self, url: str, params: dict | None = None, headers: dict | None = None,
                 cache_key_exclude: tuple[str, ...] = ("api_key",)):
        """GET ``url`` and decode JSON, using the cache when fresh."""
        params = {k: v for k, v in (params or {}).items() if v is not None}
        key_params = {k: v for k, v in params.items() if k not in cache_key_exclude}
        key = url + "?" + json.dumps(key_params, sort_keys=True) + json.dumps(headers or {}, sort_keys=True)
        path = self._cache_path(key)
        if path and path.exists():
            age = time.time() - path.stat().st_mtime
            if self.offline or age < self.cache_ttl:
                return json.loads(path.read_text())
        if self.offline:
            raise SourceUnavailable(f"offline mode and no cached response for {url}")

        data = self._fetch(url, params, headers, as_json=True)
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data))
        return data

    def get_text(self, url: str) -> str:
        path = self._cache_path("text:" + url)
        if path and path.exists() and (self.offline or time.time() - path.stat().st_mtime < self.cache_ttl):
            return path.read_text()
        if self.offline:
            raise SourceUnavailable(f"offline mode and no cached page for {url}")
        text = self._fetch(url, None, None, as_json=False)
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return text

    def _fetch(self, url, params, headers, as_json: bool):
        last_error = None
        for attempt in range(5):
            try:
                resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(min(2 ** attempt, self.max_retry_wait))
                continue
            if resp.status_code == 404:
                return None
            if resp.status_code in (429, 500, 502, 503, 504):
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
                if wait > self.max_retry_wait:
                    raise SourceUnavailable(_describe_refusal(url, resp))
                log.debug("HTTP %s from %s; retrying in %.0fs", resp.status_code, url, wait)
                time.sleep(wait)
                last_error = _describe_refusal(url, resp)
                continue
            if resp.status_code >= 400:
                raise SourceUnavailable(_describe_refusal(url, resp))
            return resp.json() if as_json else resp.text
        raise SourceUnavailable(f"giving up on {url}: {last_error}")


def _describe_refusal(url: str, resp: requests.Response) -> str:
    detail = ""
    try:
        body = resp.json()
        detail = body.get("message") or body.get("error") or ""
    except ValueError:
        detail = resp.text[:200]
    if len(detail) > 160:
        detail = detail[:157].rstrip() + "..."
    return f"HTTP {resp.status_code} from {url.split('?')[0]}: {detail}".strip()
