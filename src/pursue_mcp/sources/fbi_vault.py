"""FBI Vault source (https://vault.fbi.gov).

The Vault sits behind Cloudflare's Managed Challenge — a TLS-fingerprint-based
bot defense. Plain ``httpx`` gets a 403 with ``cf-mitigated: challenge`` after
a few requests; ``curl_cffi`` impersonating Chrome passes through cleanly. All
calls in this module must go through :class:`ImpersonatingClient`, not the
default :class:`RateLimitedClient`.

Three UFO-relevant collections (paths confirmed via the Vault's own search):

* ``UFO``         — https://vault.fbi.gov/UFO  (16 parts)
* ``Roswell``     — https://vault.fbi.gov/Roswell%20UFO/
* ``Hottel``      — https://vault.fbi.gov/hottel_guy/

Vault search is at ``/search?SearchableText=<q>``. Results are rendered into
``ul#content-list.searchResults > li > a.policy-item``. Counterintuitively the
result *title* lives in ``div.policy-date`` and the *summary* in
``div.policy-title`` — that's just how Plone's class names landed here. Don't
"fix" it.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from ..db import IncidentLocation, Record
from ..http_client import ImpersonatingClient

log = logging.getLogger(__name__)

VAULT_BASE = "https://vault.fbi.gov"

COLLECTIONS: dict[str, dict[str, str]] = {
    "UFO": {
        "label": "UFO",
        "path": "/UFO",
        "url_prefix": "https://vault.fbi.gov/UFO/",
    },
    "Roswell": {
        "label": "Roswell UFO",
        "path": "/Roswell%20UFO/",
        "url_prefix": "https://vault.fbi.gov/Roswell%20UFO/",
    },
    "Hottel": {
        "label": "Guy Hottel",
        "path": "/hottel_guy/",
        "url_prefix": "https://vault.fbi.gov/hottel_guy/",
    },
}


def _slugify(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s[:80] or "untitled"


def _record_from_search_li(li: Any, fetched_at: str) -> Record | None:
    """Extract a Record from one Vault search result <li>.

    See the module docstring for the swapped-class-name caveat.
    """
    a = li.find("a", href=True)
    if not a:
        return None
    href = a["href"]
    if not href or href.startswith("#"):
        return None
    full_url = href if href.startswith("http") else urljoin(VAULT_BASE, href)

    title_el = a.select_one("div.policy-date")
    summary_el = a.select_one("div.policy-title")
    raw_title = title_el.get_text(" ", strip=True) if title_el else a.get_text(" ", strip=True)
    title = raw_title.strip()
    if not title:
        return None
    summary = summary_el.get_text(" ", strip=True) if summary_el else None

    is_view = full_url.rstrip("/").endswith("/view")
    rec_type = "report" if is_view else "memo"

    return Record(
        id=f"fbi_vault:{_slugify(title)}",
        source="fbi_vault",
        title=title,
        type=rec_type,
        incident_location=IncidentLocation(),
        summary=summary,
        source_url=full_url,
        fetched_at=fetched_at,
    )


def parse_search_results(html: str) -> list[Record]:
    """Parse a Vault ``/search`` page into Record objects."""
    soup = BeautifulSoup(html, "lxml")
    fetched_at = datetime.now(UTC).isoformat()
    out: list[Record] = []

    container = soup.select_one("ul#content-list") or soup.select_one("ul.searchResults")
    if container is None:
        return out

    for li in container.find_all("li", recursive=False):
        rec = _record_from_search_li(li, fetched_at)
        if rec is not None:
            out.append(rec)
    return out


def parse_collection_index(html: str, collection_url_prefix: str) -> list[Record]:
    """Parse a collection index page (e.g. /UFO) into Record objects.

    Each Vault collection's index lists "Part XX" links to per-part /view
    pages. Anchors not under the collection's URL prefix are nav chrome and
    discarded.
    """
    soup = BeautifulSoup(html, "lxml")
    fetched_at = datetime.now(UTC).isoformat()
    out: list[Record] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = href if href.startswith("http") else urljoin(VAULT_BASE, href)
        if not full.startswith(collection_url_prefix):
            continue
        # the index page itself, or the listing root, isn't a record
        if full.rstrip("/") == collection_url_prefix.rstrip("/"):
            continue
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 3:
            continue
        if full in seen:
            continue
        seen.add(full)

        is_view = full.rstrip("/").endswith("/view")
        rec_type = "report" if is_view else "memo"

        out.append(
            Record(
                id=f"fbi_vault:{_slugify(title)}",
                source="fbi_vault",
                title=title,
                type=rec_type,
                incident_location=IncidentLocation(),
                source_url=full,
                fetched_at=fetched_at,
            )
        )
    return out


async def search(
    client: ImpersonatingClient,
    query: str,
    collection: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Search the Vault, optionally restricting to one collection.

    Vault's search returns a single HTML page with all results inline. When
    ``collection`` is given, results are filtered to those whose URL is under
    that collection's path — a simple URL-prefix filter, not a server-side
    scope.
    """
    if not query or not query.strip():
        return {
            "records": [],
            "meta": {"error": "empty_query", "message": "query is required"},
        }
    if collection is not None and collection not in COLLECTIONS:
        return {
            "records": [],
            "meta": {
                "error": "unknown_collection",
                "collection": collection,
                "valid_collections": sorted(COLLECTIONS.keys()),
            },
        }

    url = f"{VAULT_BASE}/search?SearchableText={quote_plus(query)}"
    result = await client.get(url)

    meta: dict[str, Any] = {
        "source_url": result.final_url,
        "http_status": result.status,
        "query": query,
        "collection": collection,
    }

    if result.status != 200:
        return {"records": [], "meta": {**meta, "error": "non_200"}}

    try:
        records = parse_search_results(result.text)
    except Exception as e:
        log.exception("fbi_vault search parse failed for %r", query)
        dump = client.dump_raw("fbi_vault", f"search_{_slugify(query)}_parse_error", result.text)
        return {
            "records": [],
            "meta": {**meta, "error": "parse_exception", "detail": str(e), "raw_dump": str(dump)},
        }

    if collection is not None:
        prefix = COLLECTIONS[collection]["url_prefix"]
        records = [r for r in records if r.source_url and r.source_url.startswith(prefix)]

    if not records:
        meta["warning"] = "no_records_extracted"

    truncated = records[:limit]
    return {
        "records": [_record_for_response(r) for r in truncated],
        "meta": {**meta, "returned": len(truncated), "total_parsed": len(records)},
    }


async def list_collection(
    client: ImpersonatingClient,
    collection: str,
    limit: int = 50,
) -> dict[str, Any]:
    """List the parts of a Vault collection (UFO has 16, Roswell + Hottel have 1 each)."""
    if collection not in COLLECTIONS:
        return {
            "records": [],
            "meta": {
                "error": "unknown_collection",
                "collection": collection,
                "valid_collections": sorted(COLLECTIONS.keys()),
            },
        }
    info = COLLECTIONS[collection]
    url = urljoin(VAULT_BASE, info["path"])
    result = await client.get(url)

    meta: dict[str, Any] = {
        "source_url": result.final_url,
        "http_status": result.status,
        "collection": collection,
    }

    if result.status != 200:
        return {"records": [], "meta": {**meta, "error": "non_200"}}

    try:
        # The collection's URL prefix sometimes differs slightly from the path
        # (e.g. trailing slash). Use the explicit prefix from COLLECTIONS.
        records = parse_collection_index(result.text, info["url_prefix"])
    except Exception as e:
        log.exception("fbi_vault collection-index parse failed for %s", collection)
        dump = client.dump_raw("fbi_vault", f"collection_{collection}_parse_error", result.text)
        return {
            "records": [],
            "meta": {**meta, "error": "parse_exception", "detail": str(e), "raw_dump": str(dump)},
        }

    if not records:
        dump = client.dump_raw("fbi_vault", f"collection_{collection}_empty", result.text)
        meta["warning"] = "no_records_extracted"
        meta["raw_dump"] = str(dump) if dump else None

    truncated = records[:limit]
    return {
        "records": [_record_for_response(r) for r in truncated],
        "meta": {**meta, "returned": len(truncated), "total_parsed": len(records)},
    }


def _record_for_response(r: Record) -> dict[str, Any]:
    return {
        "id": r.id,
        "source": r.source,
        "title": r.title,
        "type": r.type,
        "summary": r.summary,
        "source_url": r.source_url,
        "fetched_at": r.fetched_at,
    }


__all__ = [
    "COLLECTIONS",
    "VAULT_BASE",
    "list_collection",
    "parse_collection_index",
    "parse_search_results",
    "search",
]
