"""Tests for the NARA source — mocked at the HTTP layer per CLAUDE.md."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx

from pursue_mcp.http_client import RateLimitedClient
from pursue_mcp.sources import nara

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_parse_search_response_extracts_uap_records() -> None:
    payload = json.loads((FIXTURE_DIR / "nara_v3_search_uap_ohio.json").read_text(encoding="utf-8"))
    records = nara.parse_search_response(payload)
    assert len(records) > 0
    titles = [r.title for r in records]
    # Captured fixture is q=ohio scoped to RG 615 — every hit must be a real UAP record.
    assert any("UAP" in t or "UFO" in t for t in titles)
    for r in records:
        assert r.source == "nara"
        assert r.id.startswith("nara:naId:")
        assert r.source_url and r.source_url.startswith("https://catalog.archives.gov/id/")


def test_parse_record_populates_summary_attachments_and_location() -> None:
    """The Pilgrim Drone Spotting fixture is a single-record fetch — it has
    scope-and-content notes, a digital object (PDF), and a geographicPlaceName
    subject. Confirm we surface all three."""
    payload = json.loads(
        (FIXTURE_DIR / "nara_v3_record_pilgrim_488808329.json").read_text(encoding="utf-8")
    )
    records = nara.parse_search_response(payload)
    assert len(records) == 1
    r = records[0]
    assert r.id == "nara:naId:488808329"
    assert r.title == "Pilgrim Drone Spotting"
    assert r.summary and "Plymouth" in r.summary
    assert r.incident_location.raw == "Plymouth (Mass.)"
    assert len(r.attachments) == 1
    assert r.attachments[0].kind == "pdf"
    assert r.attachments[0].url.endswith("Klukan_Oct_2015_Emails_re_Pilgrim_Drone_Spotting.pdf")
    # ancestor-derived agency: NRC sub-collection (RG 615 ancestor is filtered out)
    assert r.originating_agency and "Nuclear Regulatory Commission" in r.originating_agency
    # coverage dates are dicts shaped {year, month, day, logicalDate} on this fixture;
    # we normalize to the ISO string.
    assert r.incident_date == "2015-10-01"


def test_parse_search_response_empty() -> None:
    assert nara.parse_search_response({"body": {"hits": {"hits": []}}}) == []
    assert nara.parse_search_response({}) == []


@respx.mock
async def test_search_against_mocked_catalog() -> None:
    payload = json.loads((FIXTURE_DIR / "nara_v3_search_uap_ohio.json").read_text(encoding="utf-8"))
    respx.get(nara.CATALOG_BASE + nara.SEARCH_PATH).mock(
        return_value=httpx.Response(200, json=payload)
    )

    client = RateLimitedClient(per_host_interval=0)
    try:
        result = await nara.search(client, "ohio", limit=10)
    finally:
        await client.aclose()

    assert result["meta"]["http_status"] == 200
    assert result["meta"]["query"] == "ohio"
    assert result["meta"]["ancestor_na_id"] == nara.UAP_COLLECTION_NAID
    assert result["meta"]["returned"] == len(result["records"]) <= 10
    # The fixture had 17 total matching records.
    assert result["meta"]["total_matching"] == 17
    assert all(r["source"] == "nara" for r in result["records"])


async def test_search_empty_query() -> None:
    client = RateLimitedClient(per_host_interval=0)
    try:
        result = await nara.search(client, "  ")
    finally:
        await client.aclose()
    assert result["records"] == []
    assert result["meta"]["error"] == "empty_query"


@respx.mock
async def test_get_record_returns_full_record_dict() -> None:
    payload = json.loads(
        (FIXTURE_DIR / "nara_v3_record_pilgrim_488808329.json").read_text(encoding="utf-8")
    )
    respx.get(nara.CATALOG_BASE + nara.SEARCH_PATH).mock(
        return_value=httpx.Response(200, json=payload)
    )

    client = RateLimitedClient(per_host_interval=0)
    try:
        result = await nara.get_record(client, 488808329)
    finally:
        await client.aclose()

    assert result["meta"]["http_status"] == 200
    assert result["meta"]["na_id"] == 488808329
    rec = result["record"]
    assert rec is not None
    assert rec["title"] == "Pilgrim Drone Spotting"
    assert rec["summary"] and "Plymouth" in rec["summary"]
    assert rec["incident_location"]["raw"] == "Plymouth (Mass.)"
    assert len(rec["attachments"]) == 1
    assert rec["attachments"][0]["kind"] == "pdf"
    # extracted_text key only appears when include_extracted_text=True
    assert "extracted_text" not in rec


@respx.mock
async def test_get_record_not_found() -> None:
    respx.get(nara.CATALOG_BASE + nara.SEARCH_PATH).mock(
        return_value=httpx.Response(200, json={"body": {"hits": {"hits": []}}})
    )
    client = RateLimitedClient(per_host_interval=0)
    try:
        result = await nara.get_record(client, 999999999)
    finally:
        await client.aclose()
    assert result["record"] is None
    assert result["meta"]["error"] == "not_found"


@respx.mock
async def test_search_dumps_raw_on_json_decode_error(tmp_path: Path) -> None:
    respx.get(nara.CATALOG_BASE + nara.SEARCH_PATH).mock(
        return_value=httpx.Response(200, text="<html>not json</html>")
    )
    client = RateLimitedClient(per_host_interval=0, dump_dir=tmp_path)
    try:
        result = await nara.search(client, "ohio")
    finally:
        await client.aclose()

    assert result["meta"]["error"] == "json_decode"
    dumps = list(tmp_path.glob("nara_*_search_json_error.html"))
    assert len(dumps) == 1
