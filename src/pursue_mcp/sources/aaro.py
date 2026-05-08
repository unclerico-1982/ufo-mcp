"""AARO (https://www.aaro.mil) source.

AARO categories of interest (per CLAUDE.md):
  - Official-UAP-Imagery
  - UAP-Case-Resolution-Reports
  - UAP-Reporting-Trends

The DOM structure of these listings has not been verified from this dev sandbox
(blocked by network policy). The parser is intentionally permissive: it walks
common AFPIMS / DotNetNuke listing patterns used by .mil sites and falls back
to a generic "links inside the main content area whose href stays under the
category path" strategy. On total parse failure the raw HTML is dumped via
the http client so we can iterate on real fixtures.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from ..db import IncidentLocation, Record
from ..http_client import RateLimitedClient

log = logging.getLogger(__name__)

AARO_BASE = "https://www.aaro.mil"

CATEGORY_PATHS: dict[str, str] = {
    "imagery": "/UAP-Cases/Official-UAP-Imagery/",
    "resolution_reports": "/UAP-Cases/UAP-Case-Resolution-Reports/",
    "reporting_trends": "/UAP-Cases/UAP-Reporting-Trends/",
    "uap_records": "/UAP-Records/",
    "efoia_reading_room": "/EFOIA-Reading-Room/",
    "congressional_press_products": "/Congressional-Press-Products/",
}

CATEGORY_TYPE: dict[str, str] = {
    "imagery": "image",
    "resolution_reports": "report",
    "reporting_trends": "report",
    "uap_records": "report",
    "efoia_reading_room": "memo",
    "congressional_press_products": "memo",
}

DATE_PATTERNS = [
    re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
    re.compile(r"\b([A-Z][a-z]+ \d{1,2},? \d{4})\b"),
    re.compile(r"\b(\d{1,2} [A-Z][a-z]+ \d{4})\b"),
]


def _parse_date(text: str) -> str | None:
    for pat in DATE_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        raw = m.group(1)
        for fmt in ("%Y-%m-%d", "%B %d, %Y", "%B %d %Y", "%d %B %Y"):
            try:
                return datetime.strptime(raw, fmt).date().isoformat()
            except ValueError:
                continue
    return None


def _slugify(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s[:80] or "untitled"


def _extract_listing_anchors(soup: BeautifulSoup, category_path: str) -> list[Tag]:
    """Find links to individual cases.

    Strategy 1: AFPIMS uses ``article.article-list`` containers with an h-tag
    anchor inside.
    Strategy 2: DNN listings often use ``div.dnn_*`` with links.
    Strategy 3 (fallback): any ``<a>`` inside ``<main>`` / ``#content`` whose
    href stays under the same category path and whose text is non-trivial.
    """
    anchors: list[Tag] = []

    for sel in ("article.article-list a", "article a.title", "article h2 a", "article h3 a"):
        anchors = list(soup.select(sel))
        if anchors:
            return anchors

    for sel in ("div.dnn-listing a", "div.list-item a", "li.case a"):
        anchors = list(soup.select(sel))
        if anchors:
            return anchors

    main = soup.find("main") or soup.find(id="content") or soup.find(id="dnn_content") or soup
    out: list[Tag] = []
    for a in main.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if not text or len(text) < 4:
            continue
        if href.startswith("#") or href.startswith("mailto:"):
            continue
        path = urlparse(urljoin(AARO_BASE, href)).path
        if path == category_path or not path.startswith(category_path):
            continue
        out.append(a)
    return out


def parse_listing(
    html: str,
    category_key: str,
    page_url: str,
) -> list[Record]:
    """Parse an AARO category listing page into Record objects.

    Returns [] on a structurally empty page; caller is responsible for dumping
    raw HTML if zero records were extracted from a page that should not be empty.
    """
    soup = BeautifulSoup(html, "lxml")
    category_path = CATEGORY_PATHS[category_key]

    anchors = _extract_listing_anchors(soup, category_path)

    seen_hrefs: set[str] = set()
    records: list[Record] = []
    fetched_at = datetime.now(UTC).isoformat()

    for a in anchors:
        href = a.get("href")
        if not href:
            continue
        full_url = urljoin(AARO_BASE, href)
        if full_url in seen_hrefs:
            continue
        seen_hrefs.add(full_url)

        title = a.get_text(" ", strip=True)
        if not title:
            continue

        container = a.find_parent(["article", "li", "div", "tr"]) or a
        ctx = container.get_text(" ", strip=True) if isinstance(container, Tag) else title
        release_date = _parse_date(ctx)

        case_id = f"aaro:{category_key}:{_slugify(title)}"
        records.append(
            Record(
                id=case_id,
                source="aaro",
                title=title,
                type=CATEGORY_TYPE.get(category_key),
                release_date=release_date,
                incident_location=IncidentLocation(),
                source_url=full_url,
                fetched_at=fetched_at,
            )
        )

    return records


async def list_cases(
    client: RateLimitedClient,
    category: str,
    limit: int = 50,
) -> dict[str, Any]:
    """Fetch and parse one AARO category listing.

    Returns a dict with ``records`` and a ``meta`` block — meta carries the
    upstream URL, HTTP status, and any parse warnings so the MCP caller can
    distinguish "site empty today" from "parser broken."
    """
    if category not in CATEGORY_PATHS:
        return {
            "records": [],
            "meta": {
                "error": "unknown_category",
                "category": category,
                "valid_categories": sorted(CATEGORY_PATHS.keys()),
            },
        }

    url = AARO_BASE + CATEGORY_PATHS[category]
    result = await client.get(url)

    meta: dict[str, Any] = {
        "category": category,
        "source_url": result.final_url,
        "http_status": result.status,
    }

    if result.status != 200:
        return {"records": [], "meta": {**meta, "error": "non_200"}}

    try:
        records = parse_listing(result.text, category, result.final_url)
    except Exception as e:
        log.exception("aaro listing parse failed for %s", category)
        dump = client.dump_raw("aaro", f"{category}_parse_error", result.text)
        return {
            "records": [],
            "meta": {**meta, "error": "parse_exception", "detail": str(e), "raw_dump": str(dump)},
        }

    if not records:
        dump = client.dump_raw("aaro", f"{category}_empty", result.text)
        meta["warning"] = "no_records_extracted"
        meta["raw_dump"] = str(dump) if dump else None

    truncated = records[:limit]
    return {
        "records": [_record_for_response(r) for r in truncated],
        "meta": {**meta, "returned": len(truncated), "total_parsed": len(records)},
    }


def _record_for_response(r: Record) -> dict[str, Any]:
    """Trim Record to the fields useful to an MCP caller."""
    return {
        "id": r.id,
        "source": r.source,
        "title": r.title,
        "type": r.type,
        "release_date": r.release_date,
        "source_url": r.source_url,
        "fetched_at": r.fetched_at,
    }


__all__ = ["CATEGORY_PATHS", "list_cases", "parse_listing"]
