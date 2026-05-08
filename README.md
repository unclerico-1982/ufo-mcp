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
data feed; new tranches drop every few weeks. AARO, NARA, and the FBI Vault
are wired up in v1. The PURSUE catalog endpoint will land once its JSON feed
serves real data.

Currently shipped tools:

| Tool | Description |
| --- | --- |
| `aaro_list_cases(category, limit?)` | List items from one of AARO's category pages: `imagery`, `resolution_reports`, `reporting_trends`, `uap_records`, `efoia_reading_room`, `congressional_press_products`. |
| `nara_search(query, limit?, ancestor_na_id?, record_group_number?)` | Full-text search the National Archives Catalog. Defaults to scoping under Record Group 615 (the UAP collection, naId 445887258); pass `ancestor_na_id=None` to search the whole catalog. |
| `nara_get_record(na_id, include_extracted_text?)` | Fetch one NARA record by integer naId. Returns title, scope-and-content summary, incident location, and digital-object (PDF/image) URLs. Pass `include_extracted_text=True` to also pull OCR'd document text. |
| `fbi_vault_search(query, collection?, limit?)` | Search the FBI Vault. Optional `collection`: `UFO`, `Roswell`, or `Hottel` to filter results to that collection's URL prefix. |
| `fbi_vault_list_collection(collection, limit?)` | Enumerate the parts of an FBI Vault collection (`UFO` has 16 parts, `Roswell` and `Hottel` have one each). |

Every tool returns `{records, meta}`. The `meta` block carries the upstream
URL, HTTP status, and parse warnings — that lets a caller distinguish "site
empty today" from "parser broken against new DOM."

### Transport notes

- AARO, NARA, and most public gov sites work fine through plain `httpx` (the
  `RateLimitedClient`).
- **FBI Vault sits behind Cloudflare's Managed Challenge.** A normal HTTP
  client gets `403 cf-mitigated: challenge` after a few requests. We use
  `curl_cffi` with Chrome TLS-handshake impersonation (`ImpersonatingClient`)
  for `vault.fbi.gov`. Same `FetchResult` contract, swappable per-source.
- Akamai-fronted DoD properties (AARO, war.gov, defense.gov, media.defense.gov)
  may IP-block clients in some egress ranges. If `aaro_list_cases` returns
  `meta.error == "non_200"`, run `pursue-mcp-verify aaro <category>` to
  capture the raw response — the issue is usually network-level, not parser.

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

## Verify parsers against live data

Each source has a live verify mode that prints a short report and dumps raw
HTML/JSON to `tests/fixtures/` if the parser comes back empty:

```bash
uv run pursue-mcp-verify aaro resolution_reports
uv run pursue-mcp-verify nara "ohio"
uv run pursue-mcp-verify nara "wright-patterson" --no-uap-scope
uv run pursue-mcp-verify nara-record 488808329           # Pilgrim Drone Spotting
uv run pursue-mcp-verify nara-record 488808329 --with-text
uv run pursue-mcp-verify fbi_vault search "Hottel"
uv run pursue-mcp-verify fbi_vault search "Ohio" UFO
uv run pursue-mcp-verify fbi_vault list UFO
```

On a non-200 response or a zero-record parse the raw response is saved to
`tests/fixtures/` so the parser can be tuned against the actual upstream
shape rather than a synthetic guess.

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
  server.py              FastMCP server; registers tools
  http_client.py         RateLimitedClient (httpx) + ImpersonatingClient (curl_cffi)
  db.py                  SQLite connect + migration runner + Record/Attachment dataclasses
  migrations/            Append-only SQL migrations
  sources/aaro.py        AARO listing parser
  sources/nara.py        NARA Catalog search (proxy/v3/records/search)
  sources/fbi_vault.py   FBI Vault search + collection-index parsers
  verify.py              Live smoke-test CLI (aaro | nara | fbi_vault)
tests/
  fixtures/              Real HTML/JSON fixtures captured from upstream
  test_aaro.py           AARO parser + HTTP-mocked end-to-end
  test_nara.py           NARA parser + respx-mocked search
  test_fbi_vault.py      Vault parser + stub-client end-to-end
  test_db.py             Migration + FTS round-trip
  test_verify.py         CLI argparse + happy path
```

### Conventions

- All upstream HTTP goes through one of two rate-limited clients sharing the
  same `FetchResult` contract: `RateLimitedClient` (httpx) for normal hosts,
  `ImpersonatingClient` (curl_cffi, Chrome TLS impersonation) for hosts that
  fingerprint TLS handshakes (FBI Vault).
- Tests mock at the HTTP layer with real fixtures captured from upstream —
  `respx` for httpx-backed sources (AARO, NARA), an in-process `_StubClient`
  for the curl_cffi-backed FBI Vault source. Either way schema drift surfaces
  as a test failure on real bytes, not synthetic shapes.
- The normalized record schema is documented in `CLAUDE.md`. New sources
  populate the same shape before being upserted.
- Records dedupe on attachment SHA-256, not URL — the same Apollo 17 image
  is expected to appear under multiple agencies.

## License

MIT.
