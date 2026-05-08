"""Live smoke-test CLI for source parsers.

Subcommands:

    pursue-mcp-verify aaro <category>
    pursue-mcp-verify nara <query>                   [--no-uap-scope]
    pursue-mcp-verify fbi_vault search <query> [collection]
    pursue-mcp-verify fbi_vault list <collection>

On a healthy run each command prints a summary (record count, first few
titles + URLs). On a non-200 response or an empty parse it dumps the raw
upstream body to ``tests/fixtures/`` so the parser can be tuned against
actual bytes.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

from .http_client import ImpersonatingClient, RateLimitedClient
from .sources import aaro, fbi_vault, nara

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures"


async def verify_aaro(category: str) -> int:
    if category not in aaro.CATEGORY_PATHS:
        print(f"unknown category: {category}", file=sys.stderr)
        print(f"valid: {sorted(aaro.CATEGORY_PATHS.keys())}", file=sys.stderr)
        return 2

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    client = RateLimitedClient(dump_dir=FIXTURE_DIR)
    try:
        url = aaro.AARO_BASE + aaro.CATEGORY_PATHS[category]
        print(f"GET {url}")
        result = await client.get(url)
        print(
            f"  status: {result.status}  final_url: {result.final_url}  "
            f"bytes: {len(result.text)}"
        )

        if result.status != 200:
            ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            dump = FIXTURE_DIR / f"aaro_{category}_live_{ts}_status{result.status}.html"
            dump.write_text(result.text, encoding="utf-8")
            print(f"  non-200 — raw saved to {dump.relative_to(REPO_ROOT)}")
            return 1

        records = aaro.parse_listing(result.text, category, result.final_url)
        print(f"  parsed: {len(records)} records")
        for r in records[:5]:
            print(f"    - [{r.release_date or '????-??-??'}] {r.title}")
            print(f"        {r.source_url}")

        if not records:
            ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            dump = FIXTURE_DIR / f"aaro_{category}_live_{ts}_empty.html"
            dump.write_text(result.text, encoding="utf-8")
            print(f"  zero records — raw saved to {dump.relative_to(REPO_ROOT)}")
            print("  next step: inspect the dump, add selectors to _extract_listing_anchors")
            return 1
        return 0
    finally:
        await client.aclose()


async def verify_nara(query: str, scope_uap: bool) -> int:
    client = RateLimitedClient()
    try:
        ancestor = nara.UAP_COLLECTION_NAID if scope_uap else None
        scope_label = (
            f"RG 615 / naId={nara.UAP_COLLECTION_NAID} (UAP collection)"
            if scope_uap
            else "entire catalog"
        )
        print(f"NARA search: q={query!r}  scope={scope_label}")
        result = await nara.search(client, query, limit=10, ancestor_na_id=ancestor)
        meta = result["meta"]
        print(
            f"  http_status: {meta.get('http_status')}  total_matching: "
            f"{meta.get('total_matching')}  returned: {meta.get('returned', 0)}"
        )
        for r in result["records"][:8]:
            print(f"    - naId={r['id'].split(':')[-1]}  {r['title'][:90]}")
            if r.get("incident_location", {}).get("raw"):
                print(f"        loc: {r['incident_location']['raw']}")
            print(f"        {r['source_url']}")
        if meta.get("error"):
            print(f"  error: {meta['error']}")
            return 1
        return 0
    finally:
        await client.aclose()


async def verify_nara_record(na_id: int, include_extracted_text: bool) -> int:
    client = RateLimitedClient()
    try:
        print(f"NARA get_record naId={na_id}  include_extracted_text={include_extracted_text}")
        result = await nara.get_record(
            client, na_id, include_extracted_text=include_extracted_text
        )
        meta = result["meta"]
        print(f"  http_status: {meta.get('http_status')}  error: {meta.get('error') or '-'}")
        rec = result.get("record")
        if not rec:
            return 1
        print(f"  title:    {rec['title']}")
        print(f"  type:     {rec['type']}")
        print(f"  agency:   {rec.get('originating_agency')}")
        print(f"  date:     {rec.get('incident_date')}")
        loc = rec.get("incident_location") or {}
        print(f"  location: {loc.get('raw') or '-'}")
        print(f"  url:      {rec.get('source_url')}")
        if rec.get("summary"):
            print(f"  summary:  {rec['summary'][:400]}")
            if len(rec["summary"]) > 400:
                print(f"            ... ({len(rec['summary'])} chars total)")
        for a in rec.get("attachments") or []:
            print(f"  attach:   [{a['kind']}] {a['url']}")
        if include_extracted_text and rec.get("extracted_text") is not None:
            txt = str(rec["extracted_text"])
            print(f"  extracted_text: {len(txt)} chars (first 200): {txt[:200]}")
        return 0
    finally:
        await client.aclose()


async def verify_fbi_vault_search(query: str, collection: str | None) -> int:
    client = ImpersonatingClient(dump_dir=FIXTURE_DIR)
    try:
        scope = collection or "all collections"
        print(f"Vault search: q={query!r}  scope={scope}")
        result = await fbi_vault.search(client, query, collection=collection, limit=20)
        meta = result["meta"]
        print(f"  http_status: {meta.get('http_status')}  returned: {meta.get('returned', 0)}")
        for r in result["records"][:10]:
            print(f"    - {r['title'][:90]}")
            print(f"        {r['source_url']}")
            if r.get("summary"):
                print(f"        {r['summary'][:120]}")
        if meta.get("error"):
            print(f"  error: {meta['error']}")
            return 1
        return 0
    finally:
        await client.aclose()


async def verify_fbi_vault_list(collection: str) -> int:
    if collection not in fbi_vault.COLLECTIONS:
        print(f"unknown collection: {collection}", file=sys.stderr)
        print(f"valid: {sorted(fbi_vault.COLLECTIONS.keys())}", file=sys.stderr)
        return 2
    client = ImpersonatingClient(dump_dir=FIXTURE_DIR)
    try:
        print(f"Vault list collection: {collection}")
        result = await fbi_vault.list_collection(client, collection, limit=50)
        meta = result["meta"]
        print(f"  http_status: {meta.get('http_status')}  returned: {meta.get('returned', 0)}")
        for r in result["records"][:20]:
            print(f"    - {r['title']}")
            print(f"        {r['source_url']}")
        if meta.get("error"):
            print(f"  error: {meta['error']}")
            return 1
        return 0
    finally:
        await client.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pursue-mcp-verify")
    sub = parser.add_subparsers(dest="source", required=True)

    p_aaro = sub.add_parser("aaro", help="Verify the AARO listing parser against live AARO")
    p_aaro.add_argument("category", help="AARO category key (e.g. resolution_reports)")

    p_nara = sub.add_parser("nara", help="Search NARA's catalog (defaults to UAP RG 615 scope)")
    p_nara.add_argument("query", help="search query")
    p_nara.add_argument(
        "--no-uap-scope",
        action="store_true",
        help="search the entire catalog rather than just RG 615",
    )

    p_nara_rec = sub.add_parser("nara-record", help="Fetch one NARA record by naId")
    p_nara_rec.add_argument("na_id", type=int, help="integer naId, e.g. 488808329")
    p_nara_rec.add_argument(
        "--with-text",
        action="store_true",
        help="also pull OCR'd extracted text from attachments",
    )

    p_vault = sub.add_parser("fbi_vault", help="Verify FBI Vault parsers against live Vault")
    vault_sub = p_vault.add_subparsers(dest="action", required=True)
    pv_search = vault_sub.add_parser("search", help="Full-text search Vault")
    pv_search.add_argument("query")
    pv_search.add_argument(
        "collection",
        nargs="?",
        default=None,
        help="optional: UFO | Roswell | Hottel",
    )
    pv_list = vault_sub.add_parser("list", help="List parts of a Vault collection")
    pv_list.add_argument("collection", help="UFO | Roswell | Hottel")

    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.source == "aaro":
        return asyncio.run(verify_aaro(args.category))
    if args.source == "nara":
        return asyncio.run(verify_nara(args.query, scope_uap=not args.no_uap_scope))
    if args.source == "nara-record":
        return asyncio.run(verify_nara_record(args.na_id, include_extracted_text=args.with_text))
    if args.source == "fbi_vault":
        if args.action == "search":
            return asyncio.run(verify_fbi_vault_search(args.query, args.collection))
        if args.action == "list":
            return asyncio.run(verify_fbi_vault_list(args.collection))
    return 2


if __name__ == "__main__":
    sys.exit(main())
