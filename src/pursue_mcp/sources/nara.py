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

from ..db import Attachment, IncidentLocation, Record
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

# NARA's digitalObjects.objectType strings → our open-ended Attachment.kind.
_OBJECT_TYPE_TO_KIND = (
    ("portable document", "pdf"),
    ("pdf", "pdf"),
    ("image", "image"),
    ("photograph", "image"),
    ("audio", "audio"),
    ("sound", "audio"),
    ("video", "video"),
    ("moving", "video"),
)


def _attachment_kind(object_type: str | None) -> str:
    if not object_type:
        return "other"
    lower = object_type.lower()
    for needle, kind in _OBJECT_TYPE_TO_KIND:
        if needle in lower:
            return kind
    return "other"


def _attachments_from_digital_objects(
    digital_objects: list[dict[str, Any]] | None,
) -> list[Attachment]:
    if not digital_objects:
        return []
    out: list[Attachment] = []
    for o in digital_objects:
        url = o.get("objectUrl")
        if not url:
            continue
        out.append(Attachment(kind=_attachment_kind(o.get("objectType")), url=url))
    return out


def _location_from_subjects(subjects: list[dict[str, Any]] | None) -> IncidentLocation:
    if not subjects:
        return IncidentLocation()
    geo = [s.get("heading") for s in subjects if s.get("authorityType") == "geographicPlaceName"]
    geo = [g for g in geo if g]
    if not geo:
        return IncidentLocation()
    raw = "; ".join(geo[:3])
    return IncidentLocation(raw=raw, region=geo[0])


def _coerce_date(value: Any) -> str | None:
    """NARA dates are sometimes plain ISO strings, sometimes
    ``{"year": Y, "month": M, "day": D, "logicalDate": "YYYY-MM-DD"}``.
    Normalize to the ISO string when possible."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("logicalDate") or value.get("startDate")
    return None


def _date_from_record(src: dict[str, Any]) -> str | None:
    """Pull the most useful incident-ish date out of a NARA record.

    Tries production/inclusive dates first; falls back to coverage dates,
    which are present on most RG 615 items even when production dates are
    missing.
    """
    for key in ("productionDates", "inclusiveDates"):
        dates = src.get(key)
        if isinstance(dates, list) and dates:
            d = _coerce_date(dates[0])
            if d:
                return d
        else:
            d = _coerce_date(dates)
            if d:
                return d
    return _coerce_date(src.get("coverageStartDate")) or _coerce_date(src.get("coverageEndDate"))


def _originating_agency(src: dict[str, Any]) -> str | None:
    """Best-effort agency: explicit creators field, falling back to the most
    specific (non-RG-615) ancestor — RG 615 items are organized by source
    agency in sub-collections like "Records from the FAA Relating to UAP" /
    "Records from the NRC ...". That sub-collection name is the most
    informative agency label most of the time."""
    creators = src.get("creators") or []
    if creators and isinstance(creators[0], dict):
        heading = creators[0].get("heading")
        if heading:
            return heading
    ancestors = src.get("ancestors") or []
    for a in ancestors:
        if not isinstance(a, dict):
            continue
        if a.get("naId") == UAP_COLLECTION_NAID:
            continue
        title = a.get("title")
        if title:
            return title
    return None


def _record_from_hit(hit: dict[str, Any]) -> Record | None:
    src = hit.get("_source", {}).get("record") or {}
    na_id = src.get("naId")
    title = src.get("title")
    if not na_id or not title:
        return None

    level = src.get("levelOfDescription")
    rec_type = LEVEL_TO_TYPE.get(level, "report")
    rg = src.get("recordGroupNumber")
    summary = src.get("scopeAndContentNote")

    record = Record(
        id=f"nara:naId:{na_id}",
        source="nara",
        title=title,
        type=rec_type,
        originating_agency=_originating_agency(src),
        originating_file_id=str(rg) if rg else None,
        incident_date=_date_from_record(src),
        incident_location=_location_from_subjects(src.get("subjects")),
        attachments=_attachments_from_digital_objects(src.get("digitalObjects")),
        summary=summary,
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


async def get_record(
    client: RateLimitedClient,
    na_id: int,
    include_extracted_text: bool = False,
) -> dict[str, Any]:
    """Fetch a single NARA record by naId.

    NARA has no dedicated single-record endpoint — the SPA itself fetches
    via ``/proxy/v3/records/search?naId=<id>`` (filter, returns a hit list of
    length 0 or 1). Pass ``include_extracted_text=True`` to also pull OCR'd
    text for the record's digital objects (much larger payload).
    """
    params: dict[str, Any] = {"naId": int(na_id)}
    if include_extracted_text:
        params["includeExtractedText"] = "true"

    url = CATALOG_BASE + SEARCH_PATH
    result = await client.get(url, params=params, headers=DEFAULT_HEADERS)

    meta: dict[str, Any] = {
        "source_url": result.final_url,
        "http_status": result.status,
        "na_id": int(na_id),
    }

    if result.status != 200:
        return {"record": None, "meta": {**meta, "error": "non_200"}}

    try:
        payload = json.loads(result.text)
    except json.JSONDecodeError as e:
        log.exception("nara get_record json decode failed for naId=%s", na_id)
        dump = client.dump_raw("nara", f"get_record_{na_id}_json_error", result.text)
        return {
            "record": None,
            "meta": {**meta, "error": "json_decode", "detail": str(e), "raw_dump": str(dump)},
        }

    records = parse_search_response(payload)
    if not records:
        return {"record": None, "meta": {**meta, "error": "not_found"}}
    record = records[0]
    extracted_text = None
    if include_extracted_text:
        hits = (payload.get("body") or payload).get("hits", {}).get("hits", []) or []
        if hits:
            src = hits[0].get("_source", {})
            extracted_text = src.get("extractedText") or src.get("record", {}).get("extractedText")

    return {
        "record": _full_record_for_response(record, extracted_text=extracted_text),
        "meta": meta,
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


def _full_record_for_response(r: Record, extracted_text: Any = None) -> dict[str, Any]:
    """Like _record_for_response but with summary, attachments, and (optionally)
    extracted text — used by get_record where the payload is detail-rich."""
    out = _record_for_response(r)
    out["summary"] = r.summary
    out["attachments"] = [
        {"kind": a.kind, "url": a.url, "sha256": a.sha256, "ocr_done": a.ocr_done}
        for a in r.attachments
    ]
    if extracted_text is not None:
        out["extracted_text"] = extracted_text
    return out


__all__ = [
    "CATALOG_BASE",
    "SEARCH_PATH",
    "UAP_COLLECTION_NAID",
    "get_record",
    "parse_search_response",
    "search",
]
