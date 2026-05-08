"""Tests for the FBI Vault source.

Vault is fetched via :class:`ImpersonatingClient` (curl_cffi-backed). respx
mocks httpx, not curl_cffi, so HTTP-layer mocking here uses a small in-process
fake that satisfies the same ``async get -> FetchResult`` contract — equivalent
in spirit to the respx layer used elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pursue_mcp.http_client import FetchResult
from pursue_mcp.sources import fbi_vault

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@dataclass
class _StubClient:
    """Fake HTTP client matching the FetchResult contract used by sources."""

    response_text: str = ""
    response_status: int = 200
    last_url: str | None = None
    dumps: list[Path] = field(default_factory=list)

    async def get(self, url: str, **_kwargs: object) -> FetchResult:
        self.last_url = url
        return FetchResult(
            url=url,
            status=self.response_status,
            text=self.response_text,
            headers={},
            final_url=url,
        )

    def dump_raw(self, source: str, label: str, body: str) -> Path | None:  # pragma: no cover
        return None

    async def aclose(self) -> None:  # pragma: no cover
        return None


def test_parse_search_results_extracts_hottel_collection() -> None:
    html = (FIXTURE_DIR / "fbi_vault_search_hottel.html").read_text(encoding="utf-8")
    records = fbi_vault.parse_search_results(html)
    assert len(records) > 0
    # Fixture is the live search for "Hottel" — top result must be the Hottel collection root.
    titles = [r.title for r in records]
    assert any("Hottel" in t for t in titles)
    assert any(r.source_url == "https://vault.fbi.gov/hottel_guy/" for r in records)
    for r in records:
        assert r.source == "fbi_vault"
        assert r.id.startswith("fbi_vault:")


def test_parse_collection_index_extracts_16_ufo_parts() -> None:
    html = (FIXTURE_DIR / "fbi_vault_ufo_index.html").read_text(encoding="utf-8")
    records = fbi_vault.parse_collection_index(html, fbi_vault.COLLECTIONS["UFO"]["url_prefix"])
    # The /UFO collection has 16 parts (Part 01 through Part 16 (Final)).
    assert len(records) == 16
    titles = [r.title for r in records]
    assert "UFO Part 01" in titles
    assert "UFO Part 16 (Final)" in titles
    for r in records:
        assert r.source == "fbi_vault"
        assert r.source_url and r.source_url.startswith("https://vault.fbi.gov/UFO/")
        assert r.source_url.endswith("/view")


async def test_search_against_stubbed_vault() -> None:
    html = (FIXTURE_DIR / "fbi_vault_search_hottel.html").read_text(encoding="utf-8")
    client = _StubClient(response_text=html)
    result = await fbi_vault.search(client, "Hottel", limit=10)
    assert result["meta"]["http_status"] == 200
    assert result["meta"]["query"] == "Hottel"
    assert result["meta"]["returned"] == len(result["records"])
    assert client.last_url == "https://vault.fbi.gov/search?SearchableText=Hottel"


async def test_search_url_encodes_special_characters_in_query() -> None:
    """Spaces, &, # etc. must be percent-encoded — otherwise Plone silently
    truncates the query at the first unencoded special char."""
    client = _StubClient(response_text="")
    await fbi_vault.search(client, "project blue book")
    assert client.last_url == "https://vault.fbi.gov/search?SearchableText=project+blue+book"

    client = _StubClient(response_text="")
    await fbi_vault.search(client, "Bigelow & Lazar")
    assert client.last_url == "https://vault.fbi.gov/search?SearchableText=Bigelow+%26+Lazar"


async def test_search_filters_to_collection_url_prefix() -> None:
    html = (FIXTURE_DIR / "fbi_vault_search_hottel.html").read_text(encoding="utf-8")
    client = _StubClient(response_text=html)
    result = await fbi_vault.search(client, "Hottel", collection="Hottel", limit=10)
    assert all(
        r["source_url"].startswith("https://vault.fbi.gov/hottel_guy/")
        for r in result["records"]
    )
    assert result["meta"]["collection"] == "Hottel"


async def test_search_unknown_collection() -> None:
    client = _StubClient(response_text="")
    result = await fbi_vault.search(client, "anything", collection="Atlantis")
    assert result["records"] == []
    assert result["meta"]["error"] == "unknown_collection"


async def test_search_empty_query() -> None:
    client = _StubClient(response_text="")
    result = await fbi_vault.search(client, "  ")
    assert result["records"] == []
    assert result["meta"]["error"] == "empty_query"


async def test_list_collection_against_stubbed_vault() -> None:
    html = (FIXTURE_DIR / "fbi_vault_ufo_index.html").read_text(encoding="utf-8")
    client = _StubClient(response_text=html)
    result = await fbi_vault.list_collection(client, "UFO")
    assert result["meta"]["http_status"] == 200
    assert result["meta"]["collection"] == "UFO"
    assert result["meta"]["returned"] == 16
    assert client.last_url == "https://vault.fbi.gov/UFO"


async def test_list_collection_unknown() -> None:
    client = _StubClient(response_text="")
    result = await fbi_vault.list_collection(client, "Atlantis")
    assert result["meta"]["error"] == "unknown_collection"
