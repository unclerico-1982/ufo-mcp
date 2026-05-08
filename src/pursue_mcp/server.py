"""FastMCP server: registers the AARO, NARA, and FBI Vault tools."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from platformdirs import user_cache_path

from . import db
from .http_client import ImpersonatingClient, RateLimitedClient
from .sources import aaro, fbi_vault, nara

log = logging.getLogger(__name__)

mcp: FastMCP = FastMCP("pursue-mcp")

_state: dict[str, Any] = {"client": None, "impersonate_client": None, "conn": None}


def _dump_dir() -> Path:
    return user_cache_path("pursue-mcp", ensure_exists=True) / "raw_dumps"


def _ensure_started() -> tuple[RateLimitedClient, ImpersonatingClient, Any]:
    if _state["client"] is None:
        _state["client"] = RateLimitedClient(dump_dir=_dump_dir())
    if _state["impersonate_client"] is None:
        _state["impersonate_client"] = ImpersonatingClient(dump_dir=_dump_dir())
    if _state["conn"] is None:
        conn = db.connect()
        db.migrate(conn)
        _state["conn"] = conn
    return _state["client"], _state["impersonate_client"], _state["conn"]


@mcp.tool()
async def aaro_list_cases(category: str, limit: int = 50) -> dict[str, Any]:
    """List cases from an AARO category page.

    category: one of imagery | resolution_reports | reporting_trends |
              uap_records | efoia_reading_room | congressional_press_products
    limit: max records to return (default 50)

    Returns ``{records: [...], meta: {...}}``. ``meta`` carries the upstream
    URL, HTTP status, and any parse warnings so the caller can tell "site
    empty" apart from "parser stale".
    """
    client, _imp, _conn = _ensure_started()
    return await aaro.list_cases(client, category, limit=limit)


@mcp.tool()
async def nara_search(
    query: str,
    limit: int = 20,
    ancestor_na_id: int | None = nara.UAP_COLLECTION_NAID,
    record_group_number: str | None = None,
) -> dict[str, Any]:
    """Full-text search the National Archives Catalog.

    By default scopes to Record Group 615 (the UAP records collection,
    naId 445887258) so callers get UAP-relevant matches without needing the
    naId. Pass ``ancestor_na_id=None`` to search the whole catalog, or
    override with another naId to scope to a different ancestor.

    query: required search string (boolean operators, wildcards, exact phrases)
    limit: max records to return (1-100, default 20)
    ancestor_na_id: scope to descendants of this naId (default: UAP RG 615)
    record_group_number: optional, e.g. "615"

    Returns ``{records: [...], meta: {...}}``. ``meta.total_matching`` is the
    full-catalog count; ``meta.returned`` is what we sent back.
    """
    client, _imp, _conn = _ensure_started()
    return await nara.search(
        client,
        query,
        limit=limit,
        ancestor_na_id=ancestor_na_id,
        record_group_number=record_group_number,
    )


@mcp.tool()
async def fbi_vault_search(
    query: str,
    collection: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Search the FBI Vault, optionally restricted to one UFO collection.

    query: required search string
    collection: optional, one of UFO | Roswell | Hottel
                (filters results to the collection's URL prefix)
    limit: max records to return (default 50)

    Returns ``{records: [...], meta: {...}}``.
    """
    _client, imp, _conn = _ensure_started()
    return await fbi_vault.search(imp, query, collection=collection, limit=limit)


@mcp.tool()
async def fbi_vault_list_collection(collection: str, limit: int = 50) -> dict[str, Any]:
    """List the parts of an FBI Vault UFO-related collection.

    collection: UFO | Roswell | Hottel
    limit: max records to return (default 50)

    UFO has 16 parts (the 1947-onward FBI UFO collection); Roswell and Hottel
    each have one. Each record's ``source_url`` points at the part's /view
    page on Vault.
    """
    _client, imp, _conn = _ensure_started()
    return await fbi_vault.list_collection(imp, collection, limit=limit)


def serve() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _ensure_started()
    mcp.run()
