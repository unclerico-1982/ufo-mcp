"""Rate-limited HTTP client wrappers. All upstream fetches go through here.

Two backends share the ``FetchResult`` contract:

* :class:`RateLimitedClient` — httpx-based, the default. Used for hosts that
  respond to a normal HTTP client (AARO, NARA's catalog API, NARA topic pages).
* :class:`ImpersonatingClient` — curl_cffi-based with TLS fingerprint
  impersonation. Required for hosts behind Cloudflare's Managed Challenge
  (vault.fbi.gov), which fingerprints the TLS handshake and rejects anything
  that doesn't look like a real Chrome.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from . import __version__

log = logging.getLogger(__name__)

USER_AGENT = (
    f"pursue-mcp/{__version__} "
    "(+https://github.com/unclerico-1982/ufo-mcp; declassified-UAP-aggregator)"
)


@dataclass
class FetchResult:
    url: str
    status: int
    text: str
    headers: dict[str, str]
    final_url: str


class RateLimitedClient:
    """Async httpx wrapper with per-host rate limiting and exponential backoff.

    Per-host minimum interval between requests guards against tripping gov-site
    rate limiters. Failed responses retry with exponential backoff. Bodies of
    failed/parse-error responses can be archived to disk via dump_path.
    """

    def __init__(
        self,
        per_host_interval: float = 1.0,
        max_retries: int = 4,
        timeout: float = 30.0,
        dump_dir: Path | None = None,
    ) -> None:
        self._per_host_interval = per_host_interval
        self._max_retries = max_retries
        self._dump_dir = dump_dir
        self._last_hit: dict[str, float] = defaultdict(float)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            timeout=timeout,
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> RateLimitedClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def get(self, url: str, **kwargs: Any) -> FetchResult:
        host = httpx.URL(url).host
        async with self._locks[host]:
            wait = self._per_host_interval - (time.monotonic() - self._last_hit[host])
            if wait > 0:
                await asyncio.sleep(wait)

            attempt = 0
            last_exc: Exception | None = None
            while attempt <= self._max_retries:
                try:
                    resp = await self._client.get(url, **kwargs)
                    self._last_hit[host] = time.monotonic()
                    if resp.status_code >= 500 or resp.status_code == 429:
                        log.warning(
                            "upstream %s on %s (attempt %d)", resp.status_code, url, attempt + 1
                        )
                        await asyncio.sleep(2**attempt)
                        attempt += 1
                        continue
                    return FetchResult(
                        url=url,
                        status=resp.status_code,
                        text=resp.text,
                        headers=dict(resp.headers),
                        final_url=str(resp.url),
                    )
                except httpx.HTTPError as e:
                    last_exc = e
                    log.warning("network error on %s: %s (attempt %d)", url, e, attempt + 1)
                    await asyncio.sleep(2**attempt)
                    attempt += 1

            assert last_exc is not None
            raise last_exc

    def dump_raw(self, source: str, label: str, body: str) -> Path | None:
        """Persist a raw upstream body for forensic debugging on parse failure."""
        if self._dump_dir is None:
            return None
        self._dump_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)[:80]
        path = self._dump_dir / f"{source}_{ts}_{safe}.html"
        path.write_text(body, encoding="utf-8")
        return path


class ImpersonatingClient:
    """Browser-impersonating async client for hosts behind Cloudflare-style WAFs.

    vault.fbi.gov gates every request with a Cloudflare "Managed Challenge"
    that fingerprints the TLS handshake (JA3) and rejects clients whose
    handshake doesn't match a real browser. ``curl_cffi`` solves this by
    linking against curl-impersonate, which produces byte-identical TLS
    handshakes for Chrome/Firefox/Safari.

    Same FetchResult contract as :class:`RateLimitedClient` so source modules
    are transport-agnostic.
    """

    def __init__(
        self,
        impersonate: str = "chrome124",
        per_host_interval: float = 1.5,
        max_retries: int = 3,
        timeout: float = 30.0,
        dump_dir: Path | None = None,
    ) -> None:
        from curl_cffi.requests import AsyncSession

        self._per_host_interval = per_host_interval
        self._max_retries = max_retries
        self._dump_dir = dump_dir
        self._last_hit: dict[str, float] = defaultdict(float)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._session = AsyncSession(impersonate=impersonate, timeout=timeout)

    async def aclose(self) -> None:
        await self._session.close()

    async def __aenter__(self) -> ImpersonatingClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def get(self, url: str, **kwargs: Any) -> FetchResult:
        host = httpx.URL(url).host
        async with self._locks[host]:
            wait = self._per_host_interval - (time.monotonic() - self._last_hit[host])
            if wait > 0:
                await asyncio.sleep(wait)

            attempt = 0
            last_exc: Exception | None = None
            while attempt <= self._max_retries:
                try:
                    resp = await self._session.get(url, **kwargs)
                    self._last_hit[host] = time.monotonic()
                    if resp.status_code >= 500 or resp.status_code == 429:
                        log.warning(
                            "upstream %s on %s (attempt %d)",
                            resp.status_code,
                            url,
                            attempt + 1,
                        )
                        await asyncio.sleep(2**attempt)
                        attempt += 1
                        continue
                    return FetchResult(
                        url=url,
                        status=resp.status_code,
                        text=resp.text,
                        headers=dict(resp.headers),
                        final_url=str(resp.url),
                    )
                except Exception as e:
                    last_exc = e
                    log.warning("network error on %s: %s (attempt %d)", url, e, attempt + 1)
                    await asyncio.sleep(2**attempt)
                    attempt += 1

            assert last_exc is not None
            raise last_exc

    def dump_raw(self, source: str, label: str, body: str) -> Path | None:
        if self._dump_dir is None:
            return None
        self._dump_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)[:80]
        path = self._dump_dir / f"{source}_{ts}_{safe}.html"
        path.write_text(body, encoding="utf-8")
        return path
