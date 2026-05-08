"""FastMCP server: exposes the (currently sole) aaro_list_cases tool."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from platformdirs import user_cache_path

from . import db
from .http_client import RateLimitedClient
from .sources import aaro

log = logging.getLogger(__name__)

mcp: FastMCP = FastMCP("pursue-mcp")

_state: dict[str, Any] = {"client": None, "conn": None}


def _dump_dir() -> Path:
    return user_cache_path("pursue-mcp", ensure_exists=True) / "raw_dumps"


def _ensure_started() -> tuple[RateLimitedClient, Any]:
    if _state["client"] is None:
        _state["client"] = RateLimitedClient(dump_dir=_dump_dir())
    if _state["conn"] is None:
        conn = db.connect()
        db.migrate(conn)
        _state["conn"] = conn
    return _state["client"], _state["conn"]


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
    client, _ = _ensure_started()
    return await aaro.list_cases(client, category, limit=limit)


def serve() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _ensure_started()
    mcp.run()
