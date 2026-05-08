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
