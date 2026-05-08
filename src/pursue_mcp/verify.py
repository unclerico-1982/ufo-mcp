"""Live smoke-test CLI: validates the AARO parser against the real site.

Usage::

    python -m pursue_mcp.verify aaro <category>

Categories: imagery, resolution_reports, reporting_trends, uap_records,
efoia_reading_room, congressional_press_products.

On a healthy parse the command prints a short report (record count, first
few titles, parsed dates). On a parse failure or empty result, it writes
the raw HTML to ``tests/fixtures/aaro_<category>_live_<timestamp>.html``
so the next iteration can tune selectors against real DOM.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

from .http_client import RateLimitedClient
from .sources import aaro

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pursue_mcp.verify")
    sub = parser.add_subparsers(dest="source", required=True)
    p_aaro = sub.add_parser("aaro", help="Verify the AARO listing parser against live AARO")
    p_aaro.add_argument("category", help="AARO category key (e.g. resolution_reports)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.source == "aaro":
        return asyncio.run(verify_aaro(args.category))
    return 2


if __name__ == "__main__":
    sys.exit(main())
