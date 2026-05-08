"""NARA Catalog source.

The National Archives' UAP topic landing page
(https://www.archives.gov/research/topics/uaps) is a navigation hub — actual
records live in the National Archives Catalog at https://catalog.archives.gov/.

The catalog's React SPA hits a server-side proxy at ``/proxy/v3/records/search``
which returns Elasticsearch-shaped JSON. The path was sniffed from the bundled
JS (``SEARCH_HOST = "https://catalog.archives.gov"``; ``J = SEARCH_HOST +
"/proxy"``; calls reference both ``/records/search`` and ``/v3/records/search``;
only ``/v3/...`` returns JSON in practice). Documented per CLAUDE.md so the
endpoint isn't re-discovered the next time the SPA is rebuilt.

NARA's UAP collection is Record Group 615 (naId ``445887258``) — see
https://catalog.archives.gov/id/445887258. Searches default to that ancestor
so callers get UAP records by default.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..db import IncidentLocation, Record
from ..http_client import RateLimitedClient

log = logging.getLogger(__name__)

CATALOG_BASE = "https://catalog.archives.gov"
SEARCH_PATH = "/proxy/v3/records/search"

UAP_COLLECTION_NAID = 445887258  # RG 615 — Unidentified Anomalous Phenomena Records Collection

DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": f"{CATALOG_BASE}/",
}

LEVEL_TO_TYPE = {
    "item": "report",
    "fileUnit": "report",
    "series": "report",
    "recordGroup": "report",
    "collection": "report",
}


def _location_from_subjects(subjects: list[dict[str, Any]] | None) -> IncidentLocation:
    if not subjects:
        return IncidentLocation()
    geo = [s.get("heading") for s in subjects if s.get("authorityType") == "geographicPlaceName"]
    geo = [g for g in geo if g]
    if not geo:
        return IncidentLocation()
    raw = "; ".join(geo[:3])
    return IncidentLocation(raw=raw, region=geo[0])


def _record_from_hit(hit: dict[str, Any]) -> Record | None:
    src = hit.get("_source", {}).get("record") or {}
    na_id = src.get("naId")
    title = src.get("title")
    if not na_id or not title:
        return None

    level = src.get("levelOfDescription")
    rec_type = LEVEL_TO_TYPE.get(level, "report")

    dates = src.get("productionDates") or src.get("inclusiveDates") or {}
    incident_date = None
    if isinstance(dates, dict):
        incident_date = dates.get("logicalDate") or dates.get("startDate")
    elif isinstance(dates, list) and dates:
        first = dates[0] if isinstance(dates[0], dict) else {}
        incident_date = first.get("logicalDate") or first.get("startDate")

    rg = src.get("recordGroupNumber")
    originating_agency = None
    creators = src.get("creators") or []
    if creators:
        first = creators[0]
        if isinstance(first, dict):
            originating_agency = first.get("heading")

    record = Record(
        id=f"nara:naId:{na_id}",
        source="nara",
        title=title,
        type=rec_type,
        originating_agency=originating_agency,
        originating_file_id=str(rg) if rg else None,
        incident_date=incident_date,
        incident_location=_location_from_subjects(src.get("subjects")),
        source_url=f"{CATALOG_BASE}/id/{na_id}",
    )
    return record


def parse_search_response(payload: dict[str, Any]) -> list[Record]:
    hits_root = payload.get("body") or payload
    hits = (hits_root.get("hits") or {}).get("hits") or []
    out: list[Record] = []
    for h in hits:
        rec = _record_from_hit(h)
        if rec is not None:
            out.append(rec)
    return out


def _total_from_payload(payload: dict[str, Any]) -> int | None:
    hits_root = payload.get("body") or payload
    total = (hits_root.get("hits") or {}).get("total")
    if isinstance(total, dict):
        return total.get("value")
    if isinstance(total, int):
        return total
    return None


async def search(
    client: RateLimitedClient,
    query: str,
    limit: int = 20,
    ancestor_na_id: int | None = UAP_COLLECTION_NAID,
    record_group_number: str | None = None,
) -> dict[str, Any]:
    """Search NARA's catalog.

    Defaults to scoping under RG 615 (the UAP records collection) so callers
    get UAP-relevant matches without having to know the naId. Pass
    ``ancestor_na_id=None`` to search the entire catalog.
    """
    if not query or not query.strip():
        return {
            "records": [],
            "meta": {"error": "empty_query", "message": "query is required"},
        }

    params: dict[str, Any] = {"q": query, "limit": min(max(limit, 1), 100)}
    if ancestor_na_id is not None:
        params["ancestorNaId"] = ancestor_na_id
    if record_group_number is not None:
        params["recordGroupNumber"] = record_group_number

    url = CATALOG_BASE + SEARCH_PATH
    result = await client.get(url, params=params, headers=DEFAULT_HEADERS)

    meta: dict[str, Any] = {
        "source_url": result.final_url,
        "http_status": result.status,
        "query": query,
        "ancestor_na_id": ancestor_na_id,
    }

    if result.status != 200:
        return {"records": [], "meta": {**meta, "error": "non_200"}}

    try:
        payload = json.loads(result.text)
    except json.JSONDecodeError as e:
        log.exception("nara search json decode failed")
        dump = client.dump_raw("nara", "search_json_error", result.text)
        return {
            "records": [],
            "meta": {**meta, "error": "json_decode", "detail": str(e), "raw_dump": str(dump)},
        }

    records = parse_search_response(payload)
    truncated = records[: params["limit"]]
    return {
        "records": [_record_for_response(r) for r in truncated],
        "meta": {
            **meta,
            "returned": len(truncated),
            "total_parsed": len(records),
            "total_matching": _total_from_payload(payload),
        },
    }


def _record_for_response(r: Record) -> dict[str, Any]:
    return {
        "id": r.id,
        "source": r.source,
        "title": r.title,
        "type": r.type,
        "originating_agency": r.originating_agency,
        "originating_file_id": r.originating_file_id,
        "incident_date": r.incident_date,
        "incident_location": {
            "raw": r.incident_location.raw,
            "region": r.incident_location.region,
        },
        "source_url": r.source_url,
        "fetched_at": r.fetched_at,
    }


__all__ = [
    "CATALOG_BASE",
    "SEARCH_PATH",
    "UAP_COLLECTION_NAID",
    "parse_search_response",
    "search",
]
