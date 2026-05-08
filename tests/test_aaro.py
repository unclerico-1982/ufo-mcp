"""Tests for the AARO source — mocked at the HTTP layer per CLAUDE.md."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

from pursue_mcp.http_client import RateLimitedClient
from pursue_mcp.sources import aaro

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_parse_listing_extracts_three_resolution_reports() -> None:
    html = (FIXTURE_DIR / "aaro_resolution_reports.html").read_text(encoding="utf-8")
    records = aaro.parse_listing(
        html,
        "resolution_reports",
        "https://www.aaro.mil/UAP-Cases/UAP-Case-Resolution-Reports/",
    )
    assert len(records) == 3
    titles = [r.title for r in records]
    assert any("Puerto Rico" in t for t in titles)
    assert any("GIMBAL" in t for t in titles)
    assert any("GO FAST" in t for t in titles)
    assert all(r.source == "aaro" for r in records)
    assert all(r.id.startswith("aaro:resolution_reports:") for r in records)
    assert all(r.source_url and r.source_url.startswith("https://www.aaro.mil/") for r in records)
    # Date parsing covers ISO, "March 14, 2024", and "15 September 2023".
    assert {r.release_date for r in records} >= {"2024-03-14", "2023-11-02", "2023-09-15"}


def test_parse_listing_empty_returns_empty_list() -> None:
    assert aaro.parse_listing("<html><body><main>nothing</main></body></html>",
                              "resolution_reports",
                              "https://www.aaro.mil/UAP-Cases/UAP-Case-Resolution-Reports/") == []


@respx.mock
async def test_list_cases_against_mocked_aaro(tmp_path: Path) -> None:
    html = (FIXTURE_DIR / "aaro_resolution_reports.html").read_text(encoding="utf-8")
    respx.get("https://www.aaro.mil/UAP-Cases/UAP-Case-Resolution-Reports/").mock(
        return_value=httpx.Response(200, text=html)
    )

    client = RateLimitedClient(per_host_interval=0, dump_dir=tmp_path)
    try:
        result = await aaro.list_cases(client, "resolution_reports", limit=10)
    finally:
        await client.aclose()

    assert result["meta"]["http_status"] == 200
    assert result["meta"]["total_parsed"] == 3
    assert result["meta"]["returned"] == 3
    assert "warning" not in result["meta"]
    assert result["records"][0]["source"] == "aaro"


async def test_list_cases_unknown_category() -> None:
    client = RateLimitedClient(per_host_interval=0)
    try:
        result = await aaro.list_cases(client, "not_a_real_category")
    finally:
        await client.aclose()
    assert result["records"] == []
    assert result["meta"]["error"] == "unknown_category"
    assert "imagery" in result["meta"]["valid_categories"]


@respx.mock
async def test_list_cases_dumps_raw_on_empty_parse(tmp_path: Path) -> None:
    respx.get("https://www.aaro.mil/UAP-Cases/Official-UAP-Imagery/").mock(
        return_value=httpx.Response(200, text="<html><body><main>nothing here</main></body></html>")
    )
    client = RateLimitedClient(per_host_interval=0, dump_dir=tmp_path)
    try:
        result = await aaro.list_cases(client, "imagery")
    finally:
        await client.aclose()

    assert result["meta"]["warning"] == "no_records_extracted"
    dumps = list(tmp_path.glob("aaro_*_imagery_empty.html"))
    assert len(dumps) == 1
