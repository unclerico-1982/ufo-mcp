# PURSUE MCP

An MCP server that aggregates declassified U.S. government UAP/UFO records from
official sources — primarily the new [PURSUE archive at war.gov](https://www.war.gov/UFO/),
with cross-source coverage of [AARO](https://www.aaro.mil/), the
[FBI Vault](https://vault.fbi.gov/UFO), and
[NARA's UAP collection](https://www.archives.gov/research/topics/uaps).

It exposes the records to any MCP-compatible client (Claude Desktop, IDE
integrations, custom agents) as searchable, normalized objects backed by a
local SQLite + FTS5 index.

## Status

PURSUE launched May 8, 2026 with a public catalog UI but a not-yet-populated
data feed; new tranches drop every few weeks. AARO is mature and queryable
today and is the only source wired up in v1. FBI Vault, NARA, and the PURSUE
catalog endpoint will land as they become useful.

Currently shipped tools:

| Tool | Description |
| --- | --- |
| `aaro_list_cases(category, limit?)` | List items from one of AARO's category pages: `imagery`, `resolution_reports`, `reporting_trends`, `uap_records`, `efoia_reading_room`, `congressional_press_products`. |

The response is `{records, meta}`. The `meta` block carries the upstream URL,
HTTP status, and parse warnings — that lets a caller distinguish "site empty
today" from "parser broken against new DOM."

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/unclerico-1982/ufo-mcp.git
cd ufo-mcp
uv venv
uv pip install -e ".[dev]"
```

## Run the MCP server

```bash
uv run pursue-mcp
```

This launches the FastMCP server over stdio. Wire it into Claude Desktop by
adding an entry to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "pursue": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/ufo-mcp", "run", "pursue-mcp"]
    }
  }
}
```

The server creates its SQLite index under the platform's user cache directory
(`~/.cache/pursue-mcp/index.sqlite` on Linux). Migrations run automatically on
first start.

## Verify the AARO parser against live data

The AARO listing parser is intentionally permissive (multiple selector
strategies, fallback to same-path links inside the main content area). Validate
it against the real site any time with:

```bash
uv run pursue-mcp-verify aaro resolution_reports
```

On a healthy parse it prints the record count and a few sample
title+date+URL lines. On a non-200 response or a zero-record parse it saves
the raw HTML to `tests/fixtures/` so the parser can be tuned against actual
DOM rather than synthetic shapes.

## Development

```bash
uv run ruff check src tests   # lint
uv run pytest -q              # 9 tests, HTTP layer mocked via respx
```

CI runs the same three commands on every push to `main` and every pull request
(`.github/workflows/ci.yml`).

### Project layout

```
src/pursue_mcp/
  server.py            FastMCP server; registers tools
  http_client.py       The single rate-limited httpx wrapper used for all upstream fetches
  db.py                SQLite connect + migration runner + Record/Attachment dataclasses
  migrations/          Append-only SQL migrations
  sources/aaro.py      AARO listing parser + aaro_list_cases tool
  verify.py            Live smoke-test CLI
tests/
  fixtures/            HTML fixtures for parser tests
  test_aaro.py         Parser + HTTP-mocked end-to-end
  test_db.py           Migration + FTS round-trip
  test_verify.py       CLI argparse + happy path
```

### Conventions

- All upstream HTTP goes through `http_client.RateLimitedClient` — per-host
  throttling, exponential backoff, polite User-Agent identifying the project,
  and raw-response dump on parse failure for forensic debugging.
- Tests mock at the HTTP layer (via `respx`), not the parser. Real HTML
  fixtures live next to the test that uses them so schema drift surfaces as
  a test failure on real bytes.
- The normalized record schema is documented in `CLAUDE.md`. New sources
  populate the same shape before being upserted.
- Records dedupe on attachment SHA-256, not URL — the same Apollo 17 image
  is expected to appear under multiple agencies.

## License

MIT.
